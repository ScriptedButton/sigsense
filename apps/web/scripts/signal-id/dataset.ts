import {
  detectSignals,
  estimateNoiseFloor,
  shiftSpectrum,
} from "../../src/lib/sdr/signal-id/detector";
import { FEATURE_COUNT } from "../../src/lib/sdr/signal-id/features";
import { detectInFrames, featuresFor } from "../../src/lib/sdr/signal-id/pipeline";
import { addDcSpike, addGain, applyDuty, coarsen } from "./impair";
import { SIGNAL_CLASSES, type Detection, type SignalClass } from "../../src/lib/sdr/signal-id/types";
import { BIN_HZ, Rng, SAMPLE_RATE, synthesize, synthesizeNoiseFloor } from "./synth";

export type Dataset = {
  x: Float32Array[];
  y: number[];
};

export type BuildOptions = {
  perClass: number;
  frames: number;
  seed: number;
  /**
   * Extra impaired copies of each emitter (coarser bins, DC spike, gain,
   * push-to-talk duty), run through the same detect → re-bin → features path
   * as the live engine. 0 reproduces the original clean-only dataset.
   */
  impairedVariants?: number;
  onProgress?: (done: number, total: number) => void;
};

/** Picks the detection that actually corresponds to the synthesised emitter. */
function matchDetection(
  detections: Detection[],
  trueBin: number,
): Detection | null {
  let best: Detection | null = null;
  let bestDistance = Infinity;
  for (const d of detections) {
    const width = d.hiBin - d.loBin + 1;
    const tolerance = Math.max(4, width);
    const inside = trueBin >= d.loBin - tolerance && trueBin <= d.hiBin + tolerance;
    if (!inside) continue;
    const distance = Math.abs(d.peakBin - trueBin);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = d;
    }
  }
  return best;
}

/** Features for one emitter exactly as the engine would compute them. */
function extractLikeEngine(
  raw: Float32Array[],
  offsetHz: number,
  bare: boolean,
  rng: Rng,
): Float32Array | null {
  const shifted = raw.map((f) => shiftSpectrum(f));
  const latest = shifted[shifted.length - 1];
  const binHz = SAMPLE_RATE / latest.length;
  const { detections, noiseFloorDb } = detectInFrames(shifted, binHz, 10);
  if (detections.length === 0) return null;
  const detection = bare
    ? (detections[Math.floor(rng.next() * detections.length)] ?? null)
    : matchDetection(detections, Math.round(offsetHz / binHz) + latest.length / 2);
  if (!detection) return null;
  return featuresFor(shifted, detection, noiseFloorDb, binHz).features;
}

/** A random receiver-impairment recipe, mirroring the evaluation scenarios. */
function impair(frames: Float32Array[], rng: Rng, bare: boolean): Float32Array[] {
  const ks = [2, 4, 8, 16, 32];
  const r = rng.next();
  if (r < 0.3) return coarsen(frames, rng.pick(ks));
  if (r < 0.45) return addGain(addDcSpike(frames, rng), rng.range(-20, 15));
  if (r < 0.65 && !bare) return applyDuty(frames, rng);
  let f = coarsen(frames, rng.pick([2, 4, 8, 16]));
  f = addDcSpike(f, rng);
  if (!bare) f = applyDuty(f, rng);
  return addGain(f, rng.range(-15, 10));
}

export function buildDataset(options: BuildOptions): Dataset {
  const { perClass, frames, seed } = options;
  const rng = new Rng(seed);
  const x: Float32Array[] = [];
  const y: number[] = [];

  const missed = new Map<SignalClass, number>();
  const total = SIGNAL_CLASSES.length * perClass;
  let done = 0;

  for (let classIndex = 0; classIndex < SIGNAL_CLASSES.length; classIndex++) {
    const cls = SIGNAL_CLASSES[classIndex];
    let produced = 0;
    let attempts = 0;
    const maxAttempts = perClass * 4;

    while (produced < perClass && attempts < maxAttempts) {
      attempts++;

      // A quarter of NOISE examples come from bare receiver noise, so the model
      // learns to dismiss the detector's own false positives.
      const bareNoise = cls === "NOISE" && rng.next() < 0.25;
      const sample = bareNoise
        ? synthesizeNoiseFloor(rng, frames)
        : synthesize(cls, rng, frames);

      const features = extractLikeEngine(sample.frames, sample.offsetHz, bareNoise, rng);
      if (!features) continue;

      x.push(features);
      y.push(classIndex);
      for (let v = 0; v < (options.impairedVariants ?? 0); v++) {
        const variant = extractLikeEngine(
          impair(sample.frames, rng, bareNoise),
          sample.offsetHz,
          bareNoise,
          rng,
        );
        if (variant) {
          x.push(variant);
          y.push(classIndex);
        }
      }
      produced++;
      done++;
      options.onProgress?.(done, total);
    }

    if (produced < perClass) {
      missed.set(cls, perClass - produced);
    }
  }

  if (missed.size > 0) {
    const summary = [...missed.entries()].map(([c, n]) => `${c}:${n}`).join(" ");
    console.warn(`  under-filled classes (signal below detection threshold): ${summary}`);
  }

  return { x, y };
}

/** Shuffles in place with the supplied RNG so splits are reproducible. */
export function shuffle(data: Dataset, rng: Rng): void {
  for (let i = data.x.length - 1; i > 0; i--) {
    const j = Math.floor(rng.next() * (i + 1));
    [data.x[i], data.x[j]] = [data.x[j], data.x[i]];
    [data.y[i], data.y[j]] = [data.y[j], data.y[i]];
  }
}

export function split(data: Dataset, testFraction: number): [Dataset, Dataset] {
  const testCount = Math.floor(data.x.length * testFraction);
  const test: Dataset = {
    x: data.x.slice(0, testCount),
    y: data.y.slice(0, testCount),
  };
  const train: Dataset = {
    x: data.x.slice(testCount),
    y: data.y.slice(testCount),
  };
  return [train, test];
}

/** Per-feature mean and standard deviation used to standardise model inputs. */
export function standardisation(data: Dataset): { mean: number[]; std: number[] } {
  const mean = new Array<number>(FEATURE_COUNT).fill(0);
  const std = new Array<number>(FEATURE_COUNT).fill(0);
  const n = data.x.length;
  if (n === 0) return { mean, std: std.map(() => 1) };

  for (const row of data.x) {
    for (let i = 0; i < FEATURE_COUNT; i++) mean[i] += row[i];
  }
  for (let i = 0; i < FEATURE_COUNT; i++) mean[i] /= n;

  for (const row of data.x) {
    for (let i = 0; i < FEATURE_COUNT; i++) {
      const d = row[i] - mean[i];
      std[i] += d * d;
    }
  }
  for (let i = 0; i < FEATURE_COUNT; i++) {
    std[i] = Math.sqrt(std[i] / n) || 1;
  }

  return { mean, std };
}
