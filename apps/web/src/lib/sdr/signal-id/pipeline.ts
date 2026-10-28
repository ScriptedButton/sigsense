import { detectSignals, estimateNoiseFloor, type DetectorOptions } from "./detector";
import { extractFeatures } from "./features";
import { isCoarse, toTrainingGrid } from "./resample";
import type { Detection } from "./types";

/**
 * Detector settings for a given bin width. Shared by the live engine, the
 * trainer and the evaluator so all three see detections made the same way.
 *
 * Bins wider than the signal: smoothing would shave a one-bin carrier below
 * threshold, bridging would merge neighbouring channels, and the RTL-style
 * DC-spike rule would discard a real signal tuned to centre.
 */
export function detectionOptions(binHz: number, enterDb: number): DetectorOptions {
  return isCoarse(binHz)
    ? { enterDb, smooth: false, bridgeBins: 1, dcGuardBins: 0, binHz }
    : { enterDb, binHz };
}

/** Frames power-averaged for detection; features still read each raw frame. */
export const DETECTION_FRAMES = 4;

/**
 * Detects emitters in a stack of frequency-ordered frames.
 *
 * A single periodogram has ~5.6 dB of scatter per bin, so thresholding it
 * 10 dB over the floor yields dozens of random "signals" across a fine
 * spectrum. Averaging the newest few frames in power halves that scatter
 * (so noise stops clearing the threshold) while a steady or even a
 * half-duty-cycle emitter still stands well clear of the floor.
 */
export function detectInFrames(
  frames: Float32Array[],
  binHz: number,
  enterDb: number,
  averageFrames = DETECTION_FRAMES,
): { detections: Detection[]; noiseFloorDb: number } {
  const latest = frames[frames.length - 1];
  // The model's features are relative to the newest frame's own floor.
  const noiseFloorDb = estimateNoiseFloor(latest);
  const count = Math.max(1, Math.min(averageFrames, frames.length));
  let spectrum = latest;
  let floor = noiseFloorDb;
  if (count > 1) {
    spectrum = new Float32Array(latest.length);
    for (let f = frames.length - count; f < frames.length; f++) {
      const frame = frames[f];
      for (let i = 0; i < spectrum.length; i++) spectrum[i] += Math.pow(10, frame[i] / 10);
    }
    for (let i = 0; i < spectrum.length; i++) {
      spectrum[i] = 10 * Math.log10(spectrum[i] / count + 1e-30);
    }
    floor = estimateNoiseFloor(spectrum);
  }
  return {
    detections: detectSignals(spectrum, floor, detectionOptions(binHz, enterDb)),
    noiseFloorDb,
  };
}

/** Feature vector for one detection, re-binned onto the training grid first. */
export function featuresFor(
  frames: Float32Array[],
  detection: Detection,
  noiseFloorDb: number,
  binHz: number,
): { features: Float32Array; lowResolution: boolean } {
  const input = toTrainingGrid(frames, detection, binHz);
  const features = extractFeatures({
    frames: input.frames,
    detection: input.detection,
    noiseFloorDb,
    binHz: input.binHz,
  });
  return { features, lowResolution: input.lowResolution };
}
