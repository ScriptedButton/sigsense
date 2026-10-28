import { ImaAdpcmDecoder } from "@/lib/sdr/openwebrx/adpcm";
import { KIWI_WF_ADPCM_TAIL, KIWI_WF_BINS } from "./protocol";

/**
 * Decode one Kiwi W/F waterfall payload into dB-ish bins (low frequency first).
 * Uncompressed rows are uint8 intensities; compressed rows are IMA ADPCM.
 */
export function decodeKiwiWaterfall(
  payload: Uint8Array,
  compressed: boolean,
  decoder: ImaAdpcmDecoder,
): Float32Array | null {
  if (payload.length === 0) return null;

  if (!compressed) {
    const n = Math.min(payload.length, KIWI_WF_BINS);
    if (n <= 0) return null;
    const bins = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      // Map 0..255 intensity toward a noise-floor-ish dB range for the scope.
      bins[i] = (payload[i] ?? 0) - 200;
    }
    return bins;
  }

  decoder.reset();
  const pcm = decoder.decode(payload);
  const available = Math.max(0, pcm.length - KIWI_WF_ADPCM_TAIL);
  const n = Math.min(available, KIWI_WF_BINS);
  if (n <= 0) return null;
  const bins = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    bins[i] = (pcm[i] ?? 0) - 200;
  }
  return bins;
}

/**
 * Kiwi waterfall is already low-frequency-first (like OpenWebRX shifted order).
 * SDRSense stores DC-at-zero FFT order; reuse OpenWebRX unshift at the controller.
 */
