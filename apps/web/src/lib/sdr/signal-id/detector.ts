import type { Detection } from "./types";

export type DetectorOptions = {
  /** A run must peak at least this far above the noise floor to count. */
  enterDb?: number;
  /** Band edges are walked out until the level drops below this. */
  exitDb?: number;
  /** Runs separated by fewer than this many bins are merged. */
  bridgeBins?: number;
  /** Reject anything occupying more than this fraction of the span. */
  maxWidthFraction?: number;
  /** Keep at most this many detections, strongest first. */
  maxDetections?: number;
  /**
   * Three-bin smoothing steadies edges at fine resolution but divides a
   * one-bin carrier's level; turn it off when bins are wider than the signal.
   */
  smooth?: boolean;
  /** Narrow spikes within this many bins of centre are treated as DC offset (0 = off). */
  dcGuardBins?: number;
  /**
   * Bin width in Hz. When set, fragments of one wideband emission (broadcast
   * FM, TV, DAB) are merged into a single detection; 0 turns that off.
   */
  binHz?: number;
};

/** Coarse occupancy is judged over ~20 kHz, so ripple inside a signal is bridged. */
const COARSE_SMOOTH_HZ = 20_000;
const WIDEBAND_MIN_HZ = 75_000;
/** Fragments must fill this much of the region; a row of separate carriers does not. */
const WIDEBAND_MIN_OCCUPANCY = 0.8;

const DEFAULTS: Required<DetectorOptions> = {
  binHz: 0,
  enterDb: 10,
  exitDb: 3.5,
  bridgeBins: 4,
  maxWidthFraction: 0.45,
  maxDetections: 28,
  smooth: true,
  dcGuardBins: 2,
};

/**
 * Reorders raw FFT output (DC at index 0) into frequency order with DC centred,
 * matching how the scope and waterfall plot the same data.
 */
export function shiftSpectrum(raw: Float32Array, out?: Float32Array): Float32Array {
  const n = raw.length;
  const half = n >> 1;
  const dest = out && out.length === n ? out : new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const value = raw[(i + half) % n];
    dest[i] = Number.isFinite(value) ? value : -200;
  }
  return dest;
}

/**
 * Robust noise floor estimate. Most bins in a 1 MHz window are noise, so a low
 * percentile tracks the floor without being dragged up by strong signals.
 */
export function estimateNoiseFloor(shifted: Float32Array): number {
  const n = shifted.length;
  const sorted = new Float32Array(n);
  let count = 0;
  for (let i = 0; i < n; i++) {
    const v = shifted[i];
    if (Number.isFinite(v)) sorted[count++] = v;
  }
  if (count === 0) return -100;
  const view = sorted.subarray(0, count);
  view.sort();
  // 25th percentile stays below most weak carriers while resisting spurs.
  return view[Math.floor(count * 0.25)];
}

/** Three-bin moving average, used only to stabilise edge finding. */
function smooth(shifted: Float32Array, out: Float32Array): Float32Array {
  const n = shifted.length;
  out[0] = (shifted[0] + shifted[1]) / 2;
  out[n - 1] = (shifted[n - 2] + shifted[n - 1]) / 2;
  for (let i = 1; i < n - 1; i++) {
    out[i] = (shifted[i - 1] + shifted[i] + shifted[i + 1]) / 3;
  }
  return out;
}

let smoothScratch: Float32Array | null = null;

/**
 * Segments a frequency-ordered spectrum into candidate signal bands using
 * hysteresis: a run must peak above `enterDb` to qualify, but its edges are
 * walked out to the lower `exitDb` so the measured bandwidth is realistic.
 */
export function detectSignals(
  shifted: Float32Array,
  noiseFloorDb: number,
  options: DetectorOptions = {},
): Detection[] {
  const opts = { ...DEFAULTS, ...options };
  const n = shifted.length;
  if (!smoothScratch || smoothScratch.length !== n) {
    smoothScratch = new Float32Array(n);
  }
  const level = opts.smooth ? smooth(shifted, smoothScratch) : shifted;

  const enter = noiseFloorDb + opts.enterDb;
  const exit = noiseFloorDb + opts.exitDb;

  const runs: Detection[] = [];
  let start = -1;

  for (let i = 0; i <= n; i++) {
    const above = i < n && level[i] >= exit;
    if (above && start < 0) {
      start = i;
    } else if (!above && start >= 0) {
      runs.push(makeDetection(shifted, start, i - 1));
      start = -1;
    }
  }

  const merged = mergeWideband(
    shifted,
    mergeRuns(shifted, runs, opts.bridgeBins),
    level,
    exit,
    opts.binHz,
  );
  const maxWidth = Math.floor(n * opts.maxWidthFraction);
  const centerBin = n >> 1;

  const kept = merged.filter((d) => {
    if (d.peakDb < enter) return false;
    const width = d.hiBin - d.loBin + 1;
    if (width > maxWidth) return false;
    // The RTL-SDR's DC offset shows up as a permanent one-bin spike at centre.
    // Real signals tuned to centre are far wider, so width discriminates them.
    if (
      opts.dcGuardBins > 0 &&
      width <= 3 &&
      Math.abs(d.peakBin - centerBin) <= opts.dcGuardBins
    ) {
      return false;
    }
    return true;
  });

  if (kept.length <= opts.maxDetections) {
    return kept.sort((a, b) => a.peakBin - b.peakBin);
  }
  return kept
    .sort((a, b) => b.peakDb - a.peakDb)
    .slice(0, opts.maxDetections)
    .sort((a, b) => a.peakBin - b.peakBin);
}

/**
 * Joins fragments of one wideband emission into a single detection. A power
 * average over ~20 kHz finds regions at least 75 kHz wide; runs centred inside
 * one merge when they fill it, so adjacent separate carriers stay separate.
 */
function mergeWideband(
  shifted: Float32Array,
  runs: Detection[],
  level: Float32Array,
  exitDb: number,
  binHz: number,
): Detection[] {
  if (binHz <= 0 || runs.length < 2) return runs;
  const win = Math.round(COARSE_SMOOTH_HZ / binHz);
  if (win < 3) return runs;
  const n = level.length;

  // Moving average in the power domain via a prefix sum.
  const prefix = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) prefix[i + 1] = prefix[i] + 10 ** (level[i] / 10);
  const half = win >> 1;
  const coarseOn = new Uint8Array(n);
  // Occupied against the coarse level, offset a little below the fine exit
  // threshold because averaging over dips lowers the level.
  const threshold = exitDb - 1.5;
  for (let i = 0; i < n; i++) {
    const lo = Math.max(0, i - half);
    const hi = Math.min(n, i + half + 1);
    const mean = (prefix[hi] - prefix[lo]) / (hi - lo);
    coarseOn[i] = 10 * Math.log10(mean + 1e-30) > threshold ? 1 : 0;
  }

  const out: Detection[] = [];
  const used = new Set<Detection>();
  let start = -1;
  for (let i = 0; i <= n; i++) {
    const on = i < n && coarseOn[i] === 1;
    if (on && start < 0) start = i;
    if (!on && start >= 0) {
      const a = start;
      const b = i;
      start = -1;
      if ((b - a) * binHz < WIDEBAND_MIN_HZ) continue;
      const inside = runs.filter((r) => {
        const centre = (r.loBin + r.hiBin) >> 1;
        return centre >= a && centre < b;
      });
      if (inside.length < 2) continue;
      const lo = Math.min(...inside.map((r) => r.loBin));
      const hi = Math.max(...inside.map((r) => r.hiBin));
      let active = 0;
      for (let k = lo; k <= hi; k++) if (level[k] >= exitDb) active++;
      if ((hi - lo + 1) * binHz < WIDEBAND_MIN_HZ) continue;
      if (active / (hi - lo + 1) < WIDEBAND_MIN_OCCUPANCY) continue;
      inside.forEach((r) => used.add(r));
      out.push(makeDetection(shifted, lo, hi));
    }
  }
  if (out.length === 0) return runs;
  return [...runs.filter((r) => !used.has(r)), ...out].sort(
    (x, y) => x.loBin - y.loBin,
  );
}

function makeDetection(shifted: Float32Array, loBin: number, hiBin: number): Detection {
  let peakBin = loBin;
  let peakDb = -Infinity;
  for (let i = loBin; i <= hiBin; i++) {
    if (shifted[i] > peakDb) {
      peakDb = shifted[i];
      peakBin = i;
    }
  }
  return { loBin, hiBin, peakBin, peakDb };
}

function mergeRuns(
  shifted: Float32Array,
  runs: Detection[],
  bridgeBins: number,
): Detection[] {
  if (runs.length === 0) return runs;
  const merged: Detection[] = [];
  let current = runs[0];
  for (let i = 1; i < runs.length; i++) {
    const next = runs[i];
    if (next.loBin - current.hiBin - 1 <= bridgeBins) {
      current = makeDetection(shifted, current.loBin, next.hiBin);
    } else {
      merged.push(current);
      current = next;
    }
  }
  merged.push(current);
  return merged;
}
