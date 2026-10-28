import type { Detection } from "./types";

/** Bin width the bundled model was trained on (1.024 MS/s over 2048 bins). */
export const TRAIN_BIN_HZ = 1_024_000 / 2048;

/**
 * Spectra whose bins fall between these widths are fed to the model as-is.
 * Coarser spectra (HackRF at several MS/s) are interpolated down to the
 * training grid; finer ones are averaged up to it.
 */
const NATIVE_MIN_BIN_HZ = TRAIN_BIN_HZ / 2;
const NATIVE_MAX_BIN_HZ = 1500;

/** A detection narrower than this many source bins carries little shape. */
const LOW_RES_BINS = 4;

export type FeatureInput = {
  frames: Float32Array[];
  detection: Detection;
  binHz: number;
  /** True when the shape was inferred from fewer than LOW_RES_BINS source bins. */
  lowResolution: boolean;
};

/** Whether `binHz` is far enough from the training grid to need resampling. */
export function needsResample(binHz: number): boolean {
  return binHz < NATIVE_MIN_BIN_HZ || binHz > NATIVE_MAX_BIN_HZ;
}

/** Spectra this coarse cannot resolve a narrowband emission's shape. */
export function isCoarse(binHz: number): boolean {
  return binHz > NATIVE_MAX_BIN_HZ;
}

/**
 * Re-bins a window around `detection` onto the training grid so the model sees
 * the bin width it learned. Features themselves are unchanged.
 *
 * Coarse source: linear interpolation in dB between bin centres. That can only
 * reproduce the shape the source resolved, so a narrow detection is flagged
 * `lowResolution` and the caller should discount its confidence.
 * Fine source: power is averaged over each training bin's footprint.
 */
export function toTrainingGrid(
  frames: Float32Array[],
  detection: Detection,
  binHz: number,
): FeatureInput {
  const sourceWidth = detection.hiBin - detection.loBin + 1;
  if (!needsResample(binHz)) {
    return { frames, detection, binHz, lowResolution: false };
  }

  const n = frames[0].length;
  const centerBin = n / 2;
  // Edges of the occupied band, in Hz relative to the spectrum centre.
  const loHz = (detection.loBin - 0.5 - centerBin) * binHz;
  const hiHz = (detection.hiBin + 0.5 - centerBin) * binHz;
  const midHz = (loHz + hiHz) / 2;
  const widthBins = Math.max(1, Math.ceil((hiHz - loHz) / TRAIN_BIN_HZ));

  // Context on both sides: the features look a half-width beyond each edge.
  const size = Math.min(2048, Math.max(128, 2 * Math.ceil(widthBins * 2.5)));
  const half = size / 2;
  const windowFrames = frames.map((frame) => {
    const out = new Float32Array(size);
    for (let j = 0; j < size; j++) {
      const hz = midHz + (j - half + 0.5) * TRAIN_BIN_HZ;
      out[j] = sampleDb(frame, hz / binHz + centerBin, binHz);
    }
    return out;
  });

  // Training bin j is centred at (j - half + 0.5) bins from the window middle.
  const lo = clampInt(Math.ceil((loHz - midHz) / TRAIN_BIN_HZ + half - 0.5), 0, size - 1);
  const hi = clampInt(Math.floor((hiHz - midHz) / TRAIN_BIN_HZ + half - 0.5), lo, size - 1);

  const latest = windowFrames[windowFrames.length - 1];
  let peakBin = lo;
  for (let i = lo; i <= hi; i++) if (latest[i] > latest[peakBin]) peakBin = i;
  const peakDb = latest[peakBin];

  return {
    frames: windowFrames,
    detection: { loBin: lo, hiBin: hi, peakBin, peakDb },
    binHz: TRAIN_BIN_HZ,
    lowResolution: binHz > NATIVE_MAX_BIN_HZ && sourceWidth < LOW_RES_BINS,
  };
}

/** Level at fractional source index `pos` (bin centres at integer positions). */
function sampleDb(frame: Float32Array, pos: number, srcBinHz: number): number {
  const n = frame.length;
  if (srcBinHz > TRAIN_BIN_HZ) {
    const clamped = Math.min(Math.max(pos, 0), n - 1);
    const i0 = Math.floor(clamped);
    const i1 = Math.min(i0 + 1, n - 1);
    const t = clamped - i0;
    return frame[i0] * (1 - t) + frame[i1] * t;
  }
  // Finer than the training grid: average linear power across the footprint.
  const span = TRAIN_BIN_HZ / srcBinHz;
  const a = Math.min(n - 1, Math.max(0, Math.round(pos - span / 2)));
  const b = Math.min(n - 1, Math.max(a, a + Math.round(span) - 1));
  let sum = 0;
  for (let i = a; i <= b; i++) sum += Math.pow(10, frame[i] / 10);
  return 10 * Math.log10(sum / (b - a + 1) + 1e-30);
}

function clampInt(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}
