import { estimateNoiseFloor, shiftSpectrum } from "./signal-id/detector";
import type { DemodScheme } from "./types";

/** Modes where a spectrum peak / occupied-band centroid is a reliable centre. */
export type AfcScheme = "AM" | "NBFM" | "WBFM";

export function isAfcScheme(scheme: DemodScheme): scheme is AfcScheme {
  return scheme === "AM" || scheme === "NBFM" || scheme === "WBFM";
}

/**
 * Half-width of the search window around the current tune point.
 * Wide enough to catch a near-miss click, narrow enough to ignore neighbours.
 */
const SEARCH_HALF_HZ: Record<AfcScheme, number> = {
  AM: 25_000,
  NBFM: 20_000,
  WBFM: 150_000,
};

/** Peak must clear the noise floor by this much to count as a lock. */
const ENTER_DB = 8;
/** Walk band edges out until the level drops below noise + this. */
const EXIT_DB = 3;
/** Ignore residual offsets smaller than this (Hz) so the tune doesn't chatter. */
const DEADBAND_HZ = 40;
/** Blend factor for continuous tracking (higher = snappier). */
const TRACK_ALPHA = 0.35;
/** Cap how far one update may move the offset (Hz). */
const MAX_STEP_HZ = 8_000;

export type AfcEstimate = {
  /** LO-relative frequency of the signal centre. */
  offsetHz: number;
  /** Peak level inside the occupied band (dBFS). */
  peakDb: number;
  /** Noise floor used for the decision (dBFS). */
  noiseDb: number;
  /** Absolute error from the current tune point. */
  errorHz: number;
};

export type AfcUpdateResult = {
  /** Suggested new offset, or null when nothing to apply this frame. */
  offsetHz: number | null;
  locked: boolean;
  errorHz: number;
  peakDb: number;
};

/**
 * Locate the centre of an AM / NBFM / WBFM emission near `offsetHz`.
 *
 * AM uses the carrier peak. FM / WBFM use a power-weighted centroid of the
 * contiguous occupied band around that peak, which better matches the
 * occupied width of a deviation-spread signal.
 */
export function estimateSignalCenter(
  rawSpectrum: Float32Array,
  sampleRate: number,
  offsetHz: number,
  scheme: AfcScheme,
  shiftedScratch?: Float32Array,
): AfcEstimate | null {
  const n = rawSpectrum.length;
  if (n < 8 || sampleRate <= 0) return null;

  const binHz = sampleRate / n;
  const searchHalf = SEARCH_HALF_HZ[scheme];
  const loHz = offsetHz - searchHalf;
  const hiHz = offsetHz + searchHalf;

  let peakDb = -Infinity;
  let peakHz = offsetHz;
  for (let i = 0; i < n; i++) {
    const hz = i <= n / 2 ? i * binHz : (i - n) * binHz;
    if (hz < loHz || hz > hiHz) continue;
    const v = rawSpectrum[i];
    if (!Number.isFinite(v)) continue;
    if (v > peakDb) {
      peakDb = v;
      peakHz = hz;
    }
  }
  if (!Number.isFinite(peakDb)) return null;

  const shifted = shiftSpectrum(rawSpectrum, shiftedScratch);
  const noiseDb = estimateNoiseFloor(shifted);
  if (peakDb < noiseDb + ENTER_DB) return null;

  const exit = noiseDb + EXIT_DB;
  // Walk edges in bin space from the peak so we measure one emission only.
  const peakBin = hzToRawBin(peakHz, n, binHz);
  let loBin = peakBin;
  let hiBin = peakBin;
  while (loBin > 0) {
    const hz = rawBinToHz(loBin - 1, n, binHz);
    if (hz < loHz) break;
    const v = rawSpectrum[loBin - 1];
    if (!Number.isFinite(v) || v < exit) break;
    loBin--;
  }
  while (hiBin < n - 1) {
    const hz = rawBinToHz(hiBin + 1, n, binHz);
    if (hz > hiHz) break;
    const v = rawSpectrum[hiBin + 1];
    if (!Number.isFinite(v) || v < exit) break;
    hiBin++;
  }

  let centerHz: number;
  if (scheme === "AM") {
    centerHz = peakHz;
  } else {
    // Power-weighted centroid across the occupied run.
    let powerSum = 0;
    let weighted = 0;
    for (let i = loBin; i <= hiBin; i++) {
      const v = rawSpectrum[i];
      if (!Number.isFinite(v)) continue;
      const p = Math.pow(10, v / 10);
      powerSum += p;
      weighted += p * rawBinToHz(i, n, binHz);
    }
    centerHz = powerSum > 0 ? weighted / powerSum : peakHz;
  }

  return {
    offsetHz: centerHz,
    peakDb,
    noiseDb,
    errorHz: centerHz - offsetHz,
  };
}

/**
 * Continuous AFC: smooths estimates and only proposes a retune when the
 * residual exceeds the deadband.
 */
export class AfcTracker {
  private smoothedOffsetHz: number | null = null;
  private locked = false;
  private shiftedScratch: Float32Array | null = null;

  reset(): void {
    this.smoothedOffsetHz = null;
    this.locked = false;
  }

  isLocked(): boolean {
    return this.locked;
  }

  /**
   * One-shot snap: returns the raw estimate's centre with no smoothing.
   * Useful for a "Center now" button.
   */
  snap(
    rawSpectrum: Float32Array,
    sampleRate: number,
    offsetHz: number,
    scheme: AfcScheme,
  ): AfcEstimate | null {
    this.ensureScratch(rawSpectrum.length);
    return estimateSignalCenter(
      rawSpectrum,
      sampleRate,
      offsetHz,
      scheme,
      this.shiftedScratch!,
    );
  }

  /**
   * Feed a spectrum frame. Returns a new offset to apply, or null when the
   * tune should stay put (no signal, still within deadband, or tiny step).
   */
  update(
    rawSpectrum: Float32Array,
    sampleRate: number,
    offsetHz: number,
    scheme: AfcScheme,
  ): AfcUpdateResult {
    this.ensureScratch(rawSpectrum.length);
    const estimate = estimateSignalCenter(
      rawSpectrum,
      sampleRate,
      offsetHz,
      scheme,
      this.shiftedScratch!,
    );

    if (!estimate) {
      this.locked = false;
      // Decay the smoother so a reappearing signal isn't stuck on an old centre.
      if (this.smoothedOffsetHz != null) {
        this.smoothedOffsetHz =
          this.smoothedOffsetHz * 0.9 + offsetHz * 0.1;
      }
      return {
        offsetHz: null,
        locked: false,
        errorHz: 0,
        peakDb: -200,
      };
    }

    if (this.smoothedOffsetHz == null) {
      this.smoothedOffsetHz = estimate.offsetHz;
    } else {
      this.smoothedOffsetHz =
        this.smoothedOffsetHz * (1 - TRACK_ALPHA) +
        estimate.offsetHz * TRACK_ALPHA;
    }

    const target = this.smoothedOffsetHz;
    const error = target - offsetHz;
    this.locked = Math.abs(error) < DEADBAND_HZ * 2;

    if (Math.abs(error) < DEADBAND_HZ) {
      return {
        offsetHz: null,
        locked: true,
        errorHz: error,
        peakDb: estimate.peakDb,
      };
    }

    const step = clamp(error, -MAX_STEP_HZ, MAX_STEP_HZ);
    const next = Math.round(offsetHz + step);
    if (next === Math.round(offsetHz)) {
      return {
        offsetHz: null,
        locked: this.locked,
        errorHz: error,
        peakDb: estimate.peakDb,
      };
    }

    return {
      offsetHz: next,
      locked: Math.abs(target - next) < DEADBAND_HZ * 2,
      errorHz: error,
      peakDb: estimate.peakDb,
    };
  }

  private ensureScratch(n: number): void {
    if (!this.shiftedScratch || this.shiftedScratch.length !== n) {
      this.shiftedScratch = new Float32Array(n);
    }
  }
}

function rawBinToHz(bin: number, n: number, binHz: number): number {
  return bin <= n / 2 ? bin * binHz : (bin - n) * binHz;
}

/** Nearest FFT bin for an LO-relative frequency (DC at index 0). */
function hzToRawBin(hz: number, n: number, binHz: number): number {
  let bin = Math.round(hz / binHz);
  if (bin < 0) bin += n;
  if (bin < 0) bin = 0;
  if (bin >= n) bin = n - 1;
  return bin;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
