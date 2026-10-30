/** Browser-safe receiver control wire contract. No arbitrary JavaScript execution. */
export const RECEIVER_CONTROL_VERSION = 1;
export const RECEIVER_CONTROL_PORT = 8787;
export const RECEIVER_ACTIONS = [
  "get_state", "configure", "tune", "connect", "stop", "get_spectrum",
  "capture_audio", "capture_iq", "get_signals", "find_signal", "get_schedule",
  "set_display", "get_display", "get_diagnostics",
  "autofinder_state", "autofinder_start", "autofinder_stop", "autofinder_skip",
] as const;
export type ReceiverAction = typeof RECEIVER_ACTIONS[number];
export type ReceiverCommand = {
  type: "command";
  id: string;
  action: ReceiverAction;
  args: Record<string, unknown>;
};
export type ReceiverResponse = {
  type: "result";
  id: string;
  result?: unknown;
  error?: string;
};
export type ReceiverBootstrap = { token: string; port: number; version: number };
export type ReceiverSpectrum = {
  values: number[];
  centerHz: number;
  sampleRate: number;
  centerOffsetHz: number;
  binHz: number;
  startHz?: number;
  binOrder?: "ascending-frequency";
  capturedAt: string;
};
