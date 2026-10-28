import { shiftSpectrum, estimateNoiseFloor } from "../../src/lib/sdr/signal-id/detector";
import { Rng } from "./synth";

/**
 * Receiver impairments applied to synthetic spectra, so evaluation and
 * training see the things real RTL-SDR/HackRF captures have that the clean
 * generator does not. All operate on raw FFT-order dB frames.
 */

/** Averages power over `k` adjacent bins: the HackRF's coarser FFT resolution. */
export function coarsen(frames: Float32Array[], k: number): Float32Array[] {
  if (k <= 1) return frames;
  return frames.map((raw) => {
    const shifted = shiftSpectrum(raw);
    const n = Math.floor(shifted.length / k);
    const out = new Float32Array(n);
    for (let j = 0; j < n; j++) {
      let sum = 0;
      for (let i = 0; i < k; i++) sum += Math.pow(10, shifted[j * k + i] / 10);
      out[j] = 10 * Math.log10(sum / k + 1e-30);
    }
    // Back to raw FFT order (fftshift is its own inverse for even lengths).
    return shiftSpectrum(out);
  });
}

/** The permanent centre-bin spike of a zero-IF receiver. */
export function addDcSpike(
  frames: Float32Array[],
  rng: Rng,
  spikeDb = rng.range(15, 35),
): Float32Array[] {
  return frames.map((raw) => {
    const out = raw.slice();
    const floor = estimateNoiseFloor(shiftSpectrum(raw));
    const n = out.length;
    out[0] = Math.max(out[0], floor + spikeDb);
    const skirt = Math.max(out[1], floor + spikeDb - 12);
    out[1] = Math.max(out[1], skirt);
    out[n - 1] = Math.max(out[n - 1], skirt);
    return out;
  });
}

/** A different gain setting: every bin moves together, SNR unchanged. */
export function addGain(frames: Float32Array[], offsetDb: number): Float32Array[] {
  return frames.map((raw) => raw.map((v) => v + offsetDb));
}

/**
 * Push-to-talk / bursty emitters: the signal is absent from some frames. Off
 * frames are receiver noise at the frame's own floor.
 */
export function applyDuty(
  frames: Float32Array[],
  rng: Rng,
  duty = rng.range(0.35, 0.75),
): Float32Array[] {
  return frames.map((raw) => {
    if (rng.next() < duty) return raw;
    // Off: bins that stand out are replaced by draws from the frame's own
    // noise bins, so the noise statistics stay those of a real periodogram.
    const floor = estimateNoiseFloor(shiftSpectrum(raw));
    const noise: number[] = [];
    for (const v of raw) if (v <= floor + 7) noise.push(v);
    if (noise.length === 0) return raw;
    return raw.map((v) =>
      v > floor + 7 ? noise[Math.floor(rng.next() * noise.length)] : v,
    );
  });
}
