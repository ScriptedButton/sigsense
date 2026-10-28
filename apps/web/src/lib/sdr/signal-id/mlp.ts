/** Dense layer, weights stored row-major as `[outIndex * inSize + inIndex]`. */
export type LayerWeights = {
  inSize: number;
  outSize: number;
  w: number[];
  b: number[];
};

export type ModelWeights = {
  version: number;
  featureCount: number;
  classes: string[];
  /** Per-feature standardisation captured from the training set. */
  mean: number[];
  std: number[];
  layers: LayerWeights[];
  trainedAt: string;
  /** Held-out accuracy at export time, reported in the UI. */
  accuracy: number;
  /**
   * Post-hoc calibration: logits are divided by this before softmax (Guo et
   * al. 2017). Absent in older weight files, which means 1 (uncalibrated).
   */
  temperature?: number;
};

export function forward(
  model: ModelWeights,
  features: Float32Array,
  temperature = model.temperature ?? 1,
): Float32Array {
  const { mean, std, layers } = model;
  let activations = new Float32Array(features.length);
  for (let i = 0; i < features.length; i++) {
    const s = std[i] || 1;
    activations[i] = (features[i] - mean[i]) / s;
  }

  for (let l = 0; l < layers.length; l++) {
    const layer = layers[l];
    const next = new Float32Array(layer.outSize);
    const isLast = l === layers.length - 1;
    for (let o = 0; o < layer.outSize; o++) {
      let sum = layer.b[o];
      const base = o * layer.inSize;
      for (let i = 0; i < layer.inSize; i++) {
        sum += layer.w[base + i] * activations[i];
      }
      next[o] = isLast ? sum : Math.max(0, sum);
    }
    activations = next;
  }

  if (temperature !== 1) {
    for (let i = 0; i < activations.length; i++) activations[i] /= temperature;
  }
  return softmax(activations);
}

export function softmax(logits: Float32Array): Float32Array {
  let max = -Infinity;
  for (let i = 0; i < logits.length; i++) {
    if (logits[i] > max) max = logits[i];
  }
  const out = new Float32Array(logits.length);
  let sum = 0;
  for (let i = 0; i < logits.length; i++) {
    const e = Math.exp(logits[i] - max);
    out[i] = e;
    sum += e;
  }
  for (let i = 0; i < out.length; i++) out[i] /= sum || 1;
  return out;
}

export function argmax(values: ArrayLike<number>): number {
  let best = 0;
  for (let i = 1; i < values.length; i++) {
    if (values[i] > values[best]) best = i;
  }
  return best;
}
