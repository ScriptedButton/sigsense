/**
 * Minimal radix-2 FFT used only by the training pipeline.
 *
 * This deliberately mirrors what `@jtarrio/signals` does at runtime — Blackman
 * window, unnormalised forward transform, `20*log10(magnitude)` — so the model
 * trains on spectra numerically indistinguishable from live ones.
 */

export function makeBlackmanWindow(length: number): Float32Array {
  const window = new Float32Array(length);
  for (let n = 0; n < length; n++) {
    window[n] =
      0.42 -
      0.5 * Math.cos((2 * Math.PI * n) / (length - 1)) +
      0.08 * Math.cos((4 * Math.PI * n) / (length - 1));
  }
  return window;
}

/** In-place iterative Cooley-Tukey transform. `re`/`im` must be a power of two. */
export function fftInPlace(re: Float32Array, im: Float32Array): void {
  const n = re.length;
  if ((n & (n - 1)) !== 0) {
    throw new Error(`FFT length must be a power of two, got ${n}`);
  }

  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }

  for (let len = 2; len <= n; len <<= 1) {
    const angle = (-2 * Math.PI) / len;
    const wRe = Math.cos(angle);
    const wIm = Math.sin(angle);
    for (let i = 0; i < n; i += len) {
      let curRe = 1;
      let curIm = 0;
      const half = len >> 1;
      for (let k = 0; k < half; k++) {
        const aRe = re[i + k];
        const aIm = im[i + k];
        const bRe = re[i + k + half] * curRe - im[i + k + half] * curIm;
        const bIm = re[i + k + half] * curIm + im[i + k + half] * curRe;
        re[i + k] = aRe + bRe;
        im[i + k] = aIm + bIm;
        re[i + k + half] = aRe - bRe;
        im[i + k + half] = aIm - bIm;
        const nextRe = curRe * wRe - curIm * wIm;
        curIm = curRe * wIm + curIm * wRe;
        curRe = nextRe;
      }
    }
  }
}

/**
 * Windows an IQ block and returns its magnitude spectrum in dB, in raw FFT
 * order (DC at index 0) exactly as `Spectrum.getSpectrum` produces it.
 */
export function spectrumDb(
  iRe: Float32Array,
  qIm: Float32Array,
  window: Float32Array,
  out: Float32Array,
): Float32Array {
  const n = window.length;
  const re = new Float32Array(n);
  const im = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    re[i] = iRe[i] * window[i];
    im[i] = qIm[i] * window[i];
  }
  fftInPlace(re, im);
  for (let i = 0; i < n; i++) {
    out[i] = 20 * Math.log10(Math.hypot(re[i], im[i]) + 1e-12);
  }
  return out;
}
