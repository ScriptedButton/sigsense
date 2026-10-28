import weightsJson from "./model-weights.json";
import { argmax, forward, type ModelWeights } from "./mlp";
import { SIGNAL_CLASSES, type DisplayClass, type SignalClass } from "./types";

const MODEL = weightsJson as ModelWeights;

/** Held-out accuracy of the bundled model, surfaced in the UI. */
export const MODEL_ACCURACY = MODEL.accuracy;
export const MODEL_TRAINED_AT = MODEL.trainedAt;

export type Classification = {
  label: DisplayClass;
  confidence: number;
};

export function classProbabilities(features: Float32Array): Float32Array {
  return forward(MODEL, features);
}

/** Same as classProbabilities for an arbitrary weight set (evaluation, A/B). */
export function classProbabilitiesFor(
  model: ModelWeights,
  features: Float32Array,
  temperature?: number,
): Float32Array {
  return forward(model, features, temperature);
}

export const BUNDLED_MODEL: ModelWeights = MODEL;

/**
 * Collapses the model's raw distribution into a label to show.
 *
 * Upper and lower sideband differ only by which way the voice energy tapers,
 * which at 500 Hz per bin is often below what the FFT can resolve. When the
 * model splits its confidence between the two, reporting the pair as "SSB" is
 * more honest than picking a coin-flip winner.
 */
export function labelFor(probabilities: Float32Array): Classification {
  const top = argmax(probabilities);
  const label = SIGNAL_CLASSES[top] as SignalClass;
  const confidence = probabilities[top];

  if (label === "USB" || label === "LSB") {
    const usb = probabilities[SIGNAL_CLASSES.indexOf("USB")];
    const lsb = probabilities[SIGNAL_CLASSES.indexOf("LSB")];
    const combined = usb + lsb;
    if (combined > 0 && Math.max(usb, lsb) / combined < 0.65) {
      return { label: "SSB", confidence: combined };
    }
  }

  return { label, confidence };
}

export function classify(features: Float32Array): Classification {
  return labelFor(classProbabilities(features));
}
