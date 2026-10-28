import { catalogEntries } from "./catalog/match";
import { CLASS_COUNT, SIGNAL_CLASSES } from "./types";

/**
 * Frequency/bandwidth priors from the signal catalog.
 *
 * The model sees only spectrum shape; where a signal sits and how wide it is
 * says a lot about what it can be (a 12.5 kHz carrier on 462.5625 MHz is not
 * wideband FM). For a detection, every catalog entry whose allocation contains
 * the frequency votes for the classes it lists, weighted by how specific the
 * allocation is. The result multiplies the model's probabilities (a naive-Bayes
 * style fusion), bounded below so the prior can reorder close calls but never
 * rule a class out: the catalog is incomplete, and a wrong prior must not be
 * able to silence a strong model decision.
 */

/** Lowest multiplier a class can receive from the prior. */
export const PRIOR_FLOOR = 0.35;

/** Classes the catalog never describes; the prior leaves them alone. */
const NEUTRAL = new Set(["NOISE"]);

/** Allocations wider than this say nothing about a single signal. */
const MAX_SPAN_HZ = 300e6;

const INDEX = new Map<string, number>(SIGNAL_CLASSES.map((c, i) => [c, i]));

/**
 * Per-class multipliers in [PRIOR_FLOOR, 1], or null when no catalog entry
 * covers this frequency (no information, so the model is left untouched).
 */
export function classPriorFactors(
  frequencyHz: number,
  bandwidthHz: number,
): Float32Array | null {
  if (!Number.isFinite(frequencyHz) || frequencyHz <= 0) return null;

  const evidence = new Float32Array(CLASS_COUNT);
  let any = false;

  for (const entry of catalogEntries()) {
    let weight = 0;
    for (const range of entry.frequencies) {
      if (frequencyHz < range.lo || frequencyHz > range.hi) continue;
      const span = Math.max(range.hi - range.lo, 1);
      if (span > MAX_SPAN_HZ) continue;
      // Specific allocations are stronger evidence than broad bands.
      weight = Math.max(weight, clamp(1 - Math.log10(span / 1e3) / 6, 0.25, 1));
    }
    if (weight === 0) continue;

    if (entry.bandwidthHz && bandwidthHz > 0) {
      const { lo, hi } = entry.bandwidthHz;
      if (bandwidthHz < lo * 0.5 || bandwidthHz > hi * 2) weight *= 0.4;
    }

    any = true;
    for (const cls of entry.classes) {
      const i = INDEX.get(cls);
      if (i !== undefined && weight > evidence[i]) evidence[i] = weight;
    }
  }
  if (!any) return null;

  let peak = 0;
  for (let i = 0; i < CLASS_COUNT; i++) peak = Math.max(peak, evidence[i]);
  const factors = new Float32Array(CLASS_COUNT);
  for (let i = 0; i < CLASS_COUNT; i++) {
    if (NEUTRAL.has(SIGNAL_CLASSES[i])) {
      factors[i] = 1;
      continue;
    }
    // Entries that cover this frequency but list no known class carry no
    // class information.
    factors[i] =
      peak > 0 ? PRIOR_FLOOR + (1 - PRIOR_FLOOR) * (evidence[i] / peak) : 1;
  }
  return factors;
}

/** posterior ∝ p · factor^beta, renormalised. Returns `p` when there is no prior. */
export function fusePrior(
  probabilities: Float32Array,
  factors: Float32Array | null,
  beta = 1,
): Float32Array {
  if (!factors) return probabilities;
  const out = new Float32Array(probabilities.length);
  let sum = 0;
  for (let i = 0; i < out.length; i++) {
    out[i] = probabilities[i] * Math.pow(factors[i], beta);
    sum += out[i];
  }
  if (sum <= 0) return probabilities;
  for (let i = 0; i < out.length; i++) out[i] /= sum;
  return out;
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}
