import type { SignalIdListener, SignalIdState } from "./types";

export type SignalIdSensitivity = "low" | "medium" | "high";

/**
 * What the radio controller needs from a signal identifier. The local MLP
 * (engine.ts), the backend client (remote-engine.ts) and the router over both
 * (hybrid.ts) all satisfy it.
 */
export interface SignalIdSource {
  getState(): SignalIdState;
  subscribe(listener: SignalIdListener): () => void;
  getSensitivity(): SignalIdSensitivity;
  setSensitivity(sensitivity: SignalIdSensitivity): void;
  setEnabled(enabled: boolean): void;
  /** Drops history, e.g. after retuning or disconnecting. */
  reset(): void;
  /** Raw FFT-order (DC at index 0) dB frame from the radio. */
  pushSpectrum(
    raw: Float32Array,
    centerHz: number | undefined,
    sampleRate: number,
    fallbackCenterHz: number,
  ): void;
}
