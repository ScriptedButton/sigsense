import { ImaAdpcmDecoder } from "./adpcm";
import { OWRX_FFT_ADPCM_PAD, type OpenWebRxCompression } from "./protocol";

/**
 * OpenWebRX sends each waterfall row already fftshifted (low frequency at
 * index 0). SDRSense stores raw FFT order (DC at index 0) and shifts at draw
 * time. fftshift is its own inverse for even lengths.
 */
export function unshiftSpectrum(
  shifted: Float32Array,
  out?: Float32Array,
): Float32Array {
  const n = shifted.length;
  const dest = out && out.length === n ? out : new Float32Array(n);
  const half = n >> 1;
  for (let i = 0; i < n; i++) {
    dest[i] = shifted[(i + half) % n] ?? -200;
  }
  return dest;
}

/**
 * Decode one spectrum payload into dB bins in server order (low frequency
 * first). ADPCM rows are scaled by 100 on the wire and padded.
 */
export function decodeSpectrumPayload(
  payload: Uint8Array,
  compression: OpenWebRxCompression,
  decoder: ImaAdpcmDecoder,
  fftSize: number | null,
): Float32Array | null {
  if (payload.length === 0) return null;

  if (compression === "none") {
    const byteLength = payload.byteLength - (payload.byteLength % 4);
    if (byteLength < 4) return null;
    const copy = payload.buffer.slice(
      payload.byteOffset,
      payload.byteOffset + byteLength,
    );
    const bins = new Float32Array(copy);
    return cropToFftSize(bins, fftSize);
  }

  decoder.reset();
  const pcm = decoder.decode(payload);
  const start = Math.min(OWRX_FFT_ADPCM_PAD, pcm.length);
  const available = pcm.length - start;
  if (available <= 0) return null;
  const length =
    fftSize != null && fftSize > 0 && fftSize < available ? fftSize : available;
  const bins = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    bins[i] = (pcm[start + i] ?? 0) / 100;
  }
  return bins;
}

function cropToFftSize(
  bins: Float32Array,
  fftSize: number | null,
): Float32Array {
  if (fftSize == null || fftSize <= 0 || fftSize >= bins.length) return bins;
  return bins.subarray(0, fftSize);
}
