import type { FeatureWindow } from "./types";

/** Points in the resampled spectral silhouette handed to the model. */
export const SHAPE_BINS = 24;
/** Hand-designed scalars preceding the silhouette. */
export const SCALAR_COUNT = 20;
export const FEATURE_COUNT = SCALAR_COUNT + SHAPE_BINS;

/**
 * Names line up with the feature vector, purely so the training report can
 * print which inputs matter.
 */
export const FEATURE_NAMES: string[] = [
  "bw3Log",
  "bw20Log",
  "shapeFactor",
  "snr",
  "carrierProminence",
  "flatness",
  "asymmetry",
  "energySkew",
  "mirrorCorr",
  "kurtosis",
  "edgeSteepness",
  "peakCount",
  "secondPeakRatio",
  "troughDepth",
  "powerStd",
  "dutyCycle",
  "bwStdRatio",
  "flux",
  "peakWander",
  "onOffContrast",
  ...Array.from({ length: SHAPE_BINS }, (_, i) => `shape${i}`),
];

function clamp(v: number, lo: number, hi: number): number {
  if (!Number.isFinite(v)) return lo;
  return v < lo ? lo : v > hi ? hi : v;
}

/**
 * Converts a dB bin to linear power with the noise floor removed, so every
 * downstream statistic is invariant to the receiver's absolute gain.
 */
function excessPower(db: number, noiseFloorDb: number): number {
  if (!Number.isFinite(db)) return 0;
  const linear = Math.pow(10, (db - noiseFloorDb) / 10);
  return linear > 1 ? linear - 1 : 0;
}

/** Walks out from the peak while the level stays above `threshDb`. */
function widthBins(
  frame: Float32Array,
  peakBin: number,
  threshDb: number,
  lo: number,
  hi: number,
): number {
  let left = peakBin;
  while (left > lo && frame[left - 1] >= threshDb) left--;
  let right = peakBin;
  while (right < hi && frame[right + 1] >= threshDb) right++;
  return right - left + 1;
}

function peakOf(frame: Float32Array, lo: number, hi: number): { bin: number; db: number } {
  let bin = lo;
  let db = -Infinity;
  for (let i = lo; i <= hi; i++) {
    if (frame[i] > db) {
      db = frame[i];
      bin = i;
    }
  }
  return { bin, db };
}

/** Linearly resamples a slice into a fixed-length silhouette in 0..1. */
function resampleShape(
  frame: Float32Array,
  lo: number,
  hi: number,
  noiseFloorDb: number,
  peakDb: number,
  out: Float32Array,
): void {
  const span = hi - lo;
  const scale = Math.max(peakDb - noiseFloorDb, 1);
  for (let i = 0; i < SHAPE_BINS; i++) {
    const pos = span === 0 ? lo : lo + (i / (SHAPE_BINS - 1)) * span;
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, hi);
    const t = pos - i0;
    const db = frame[i0] * (1 - t) + frame[i1] * t;
    out[i] = clamp((db - noiseFloorDb) / scale, 0, 1);
  }
}

function totalExcessDb(
  frame: Float32Array,
  lo: number,
  hi: number,
  noiseFloorDb: number,
): number {
  let sum = 0;
  for (let i = lo; i <= hi; i++) sum += excessPower(frame[i], noiseFloorDb);
  return 10 * Math.log10(sum + 1e-9);
}

/**
 * Turns one analysis window into the model's input vector.
 *
 * Frames are frequency-ordered with DC centred. The returned vector is stable
 * under changes in receiver gain: features read either dB *relative to the
 * noise floor* or ratios, never an absolute level.
 */
export function extractFeatures(window: FeatureWindow): Float32Array {
  const { frames, detection, noiseFloorDb, binHz } = window;
  const out = new Float32Array(FEATURE_COUNT);
  if (frames.length === 0) return out;

  const latest = frames[frames.length - 1];
  const n = latest.length;

  const coreWidth = detection.hiBin - detection.loBin + 1;
  const guard = Math.max(2, Math.round(coreWidth * 0.5));
  const lo = clamp(detection.loBin - guard, 0, n - 1);
  const hi = clamp(detection.hiBin + guard, 0, n - 1);

  const { bin: peakBin, db: peakDb } = peakOf(latest, detection.loBin, detection.hiBin);
  const peakRel = Math.max(peakDb - noiseFloorDb, 0.5);

  // --- Bandwidth ---------------------------------------------------------
  const floorGuardDb = noiseFloorDb + 3;
  const bw3Bins = widthBins(latest, peakBin, Math.max(peakDb - 3, floorGuardDb), lo, hi);
  const bw20Bins = widthBins(latest, peakBin, Math.max(peakDb - 20, floorGuardDb), lo, hi);
  const bw3Hz = bw3Bins * binHz;
  const bw20Hz = bw20Bins * binHz;

  out[0] = clamp(Math.log10(Math.max(bw3Hz, 1)) / 6, 0, 1);
  out[1] = clamp(Math.log10(Math.max(bw20Hz, 1)) / 6, 0, 1);
  out[2] = clamp(bw3Hz / Math.max(bw20Hz, 1), 0, 1);
  out[3] = clamp(peakRel / 60, 0, 1);

  // --- In-band level statistics -----------------------------------------
  const coreLevels: number[] = [];
  for (let i = detection.loBin; i <= detection.hiBin; i++) coreLevels.push(latest[i]);
  coreLevels.sort((a, b) => a - b);
  const medianDb = coreLevels[coreLevels.length >> 1] ?? noiseFloorDb;
  out[4] = clamp((peakDb - medianDb) / 40, 0, 1);

  // Spectral flatness: geometric over arithmetic mean of excess power.
  let logSum = 0;
  let linSum = 0;
  let count = 0;
  for (let i = detection.loBin; i <= detection.hiBin; i++) {
    const p = excessPower(latest[i], noiseFloorDb) + 1e-6;
    logSum += Math.log(p);
    linSum += p;
    count++;
  }
  out[5] =
    count > 0 ? clamp(Math.exp(logSum / count) / (linSum / count + 1e-12), 0, 1) : 0;

  // --- Distribution moments over the band -------------------------------
  let w = 0;
  let m1 = 0;
  for (let i = lo; i <= hi; i++) {
    const p = excessPower(latest[i], noiseFloorDb);
    w += p;
    m1 += p * i;
  }
  const centroid = w > 0 ? m1 / w : peakBin;
  let m2 = 0;
  let m3 = 0;
  let m4 = 0;
  for (let i = lo; i <= hi; i++) {
    const p = excessPower(latest[i], noiseFloorDb);
    const d = i - centroid;
    m2 += p * d * d;
    m3 += p * d * d * d;
    m4 += p * d * d * d * d;
  }
  const variance = w > 0 ? m2 / w : 0;
  const sigma = Math.sqrt(Math.max(variance, 1e-9));
  const halfWidth = Math.max(bw3Bins / 2, 1);

  out[6] = clamp((centroid - peakBin) / halfWidth, -1, 1);
  out[7] = clamp(w > 0 ? m3 / w / Math.pow(sigma, 3) / 3 : 0, -1, 1);
  out[8] = mirrorCorrelation(latest, peakBin, lo, hi, noiseFloorDb);
  out[9] = clamp(w > 0 ? (m4 / w / Math.pow(sigma, 4) - 3) / 20 : 0, -1, 1);
  out[10] = edgeSteepness(latest, detection.loBin, detection.hiBin, noiseFloorDb, peakRel);

  // --- Multi-tone structure ---------------------------------------------
  const tones = findTones(latest, lo, hi, noiseFloorDb, peakRel);
  out[11] = clamp(tones.count / 6, 0, 1);
  out[12] = clamp(tones.secondRatio, 0, 1);
  out[13] = clamp(tones.troughDepth, 0, 1);

  // --- Temporal behaviour ------------------------------------------------
  const framePower: number[] = [];
  const frameBw: number[] = [];
  const framePeak: number[] = [];
  const shapes: Float32Array[] = [];

  for (const frame of frames) {
    framePower.push(totalExcessDb(frame, detection.loBin, detection.hiBin, noiseFloorDb));
    const fp = peakOf(frame, detection.loBin, detection.hiBin);
    framePeak.push(fp.bin);
    frameBw.push(
      widthBins(frame, fp.bin, Math.max(fp.db - 3, noiseFloorDb + 3), lo, hi),
    );
    const shape = new Float32Array(SHAPE_BINS);
    resampleShape(frame, lo, hi, noiseFloorDb, Math.max(fp.db, noiseFloorDb + 1), shape);
    shapes.push(shape);
  }

  out[14] = clamp(std(framePower) / 15, 0, 1);
  out[15] = dutyCycle(framePower);
  const bwMean = mean(frameBw);
  out[16] = clamp(bwMean > 0 ? std(frameBw) / bwMean : 0, 0, 1);
  out[17] = clamp(meanFlux(shapes) * 3, 0, 1);
  out[18] = clamp(std(framePeak) / halfWidth, 0, 1);
  const pMin = Math.min(...framePower);
  const pMax = Math.max(...framePower);
  out[19] = clamp((pMax - pMin) / 40, 0, 1);

  // --- Averaged silhouette ------------------------------------------------
  for (let i = 0; i < SHAPE_BINS; i++) {
    let sum = 0;
    for (const shape of shapes) sum += shape[i];
    out[SCALAR_COUNT + i] = shapes.length > 0 ? sum / shapes.length : 0;
  }

  for (let i = 0; i < out.length; i++) {
    if (!Number.isFinite(out[i])) out[i] = 0;
  }
  return out;
}

/**
 * How well the band matches its own mirror image about the peak. Symmetric
 * modes (AM, FM, most digital) score high; single-sideband scores low.
 */
function mirrorCorrelation(
  frame: Float32Array,
  peakBin: number,
  lo: number,
  hi: number,
  noiseFloorDb: number,
): number {
  const radius = Math.min(peakBin - lo, hi - peakBin);
  if (radius < 2) return 1;
  const left: number[] = [];
  const right: number[] = [];
  for (let k = 1; k <= radius; k++) {
    left.push(excessPower(frame[peakBin - k], noiseFloorDb));
    right.push(excessPower(frame[peakBin + k], noiseFloorDb));
  }
  return clamp(pearson(left, right), -1, 1);
}

function pearson(a: number[], b: number[]): number {
  const n = a.length;
  if (n === 0) return 0;
  const ma = mean(a);
  const mb = mean(b);
  let num = 0;
  let da = 0;
  let db = 0;
  for (let i = 0; i < n; i++) {
    const x = a[i] - ma;
    const y = b[i] - mb;
    num += x * y;
    da += x * x;
    db += y * y;
  }
  const den = Math.sqrt(da * db);
  return den < 1e-12 ? 0 : num / den;
}

/** Mean absolute dB slope just outside the band edges, scaled by depth. */
function edgeSteepness(
  frame: Float32Array,
  loBin: number,
  hiBin: number,
  noiseFloorDb: number,
  peakRel: number,
): number {
  const width = hiBin - loBin + 1;
  const reach = Math.max(1, Math.round(width * 0.15));
  const n = frame.length;
  const leftOuter = Math.max(0, loBin - reach);
  const rightOuter = Math.min(n - 1, hiBin + reach);
  const leftSlope = Math.abs(frame[loBin] - frame[leftOuter]) / reach;
  const rightSlope = Math.abs(frame[hiBin] - frame[rightOuter]) / reach;
  const avg = (leftSlope + rightSlope) / 2;
  return clamp(avg / (peakRel / 4), 0, 1);
}

/**
 * Counts distinct tones in the band and measures the dip between the two
 * strongest, which is what separates FSK from a single modulated carrier.
 */
function findTones(
  frame: Float32Array,
  lo: number,
  hi: number,
  noiseFloorDb: number,
  peakRel: number,
): { count: number; secondRatio: number; troughDepth: number } {
  const threshold = noiseFloorDb + peakRel * 0.5;
  const peaks: { bin: number; db: number }[] = [];
  for (let i = lo + 1; i < hi; i++) {
    if (
      frame[i] >= threshold &&
      frame[i] >= frame[i - 1] &&
      frame[i] > frame[i + 1]
    ) {
      peaks.push({ bin: i, db: frame[i] });
    }
  }
  if (peaks.length === 0) return { count: 0, secondRatio: 0, troughDepth: 0 };

  peaks.sort((a, b) => b.db - a.db);
  const first = peaks[0];
  const second = peaks[1];
  if (!second) return { count: 1, secondRatio: 0, troughDepth: 0 };

  const secondRatio =
    excessPower(second.db, noiseFloorDb) / (excessPower(first.db, noiseFloorDb) + 1e-9);

  let trough = Infinity;
  const a = Math.min(first.bin, second.bin);
  const b = Math.max(first.bin, second.bin);
  for (let i = a; i <= b; i++) trough = Math.min(trough, frame[i]);
  const weaker = Math.min(first.db, second.db);
  const troughDepth = clamp((weaker - trough) / Math.max(peakRel, 1), 0, 1);

  return { count: peaks.length, secondRatio, troughDepth };
}

function mean(values: ArrayLike<number>): number {
  if (values.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < values.length; i++) sum += values[i];
  return sum / values.length;
}

function std(values: ArrayLike<number>): number {
  if (values.length < 2) return 0;
  const m = mean(values);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    const d = values[i] - m;
    sum += d * d;
  }
  return Math.sqrt(sum / values.length);
}

/** Fraction of frames sitting in the upper half of the observed power range. */
function dutyCycle(powerDb: number[]): number {
  if (powerDb.length === 0) return 0;
  const lo = Math.min(...powerDb);
  const hi = Math.max(...powerDb);
  if (hi - lo < 3) return 1;
  const mid = (lo + hi) / 2;
  let on = 0;
  for (const p of powerDb) if (p >= mid) on++;
  return on / powerDb.length;
}

/** Average frame-to-frame change in silhouette. */
function meanFlux(shapes: Float32Array[]): number {
  if (shapes.length < 2) return 0;
  let total = 0;
  for (let i = 1; i < shapes.length; i++) {
    let d = 0;
    for (let k = 0; k < SHAPE_BINS; k++) {
      d += Math.abs(shapes[i][k] - shapes[i - 1][k]);
    }
    total += d / SHAPE_BINS;
  }
  return total / (shapes.length - 1);
}
