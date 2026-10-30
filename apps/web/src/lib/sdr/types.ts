export type ConnectionMode =
  | "disconnected"
  | "device"
  | "demo"
  | "openwebrx"
  | "kiwi"
  | "rtltcp"
  | "hackrf";

export type RadioStatus =
  | "idle"
  | "starting"
  | "playing"
  | "stopping"
  | "error";

export type DemodScheme = "WBFM" | "NBFM" | "AM" | "USB" | "LSB" | "CW";

export type SdrState = {
  connection: ConnectionMode;
  status: RadioStatus;
  frequencyHz: number;
  offsetHz: number;
  sampleRate: number;
  scheme: DemodScheme;
  volume: number;
  audioNoiseReduction: "off" | "low" | "medium" | "high";
  voiceIsolation: boolean;
  audioSpeechFilter?: boolean;
  voiceIsolationLoading: boolean;
  audioEnhancementError: string | null;
  autoGain: boolean;
  gainDb: number;
  rfClipFraction?: number;
  bandwidthHz: number;
  /**
   * Manual: dB above noise floor required to open (0 = off).
   * Auto: margin above tracked noise (0 uses the default margin).
   */
  squelch: number;
  /** When true, threshold rides above a tracked noise floor. */
  autoSquelch: boolean;
  /** Whether the squelch gate is currently letting audio through. */
  squelchOpen: boolean;
  /**
   * Automatic frequency centering for AM / NBFM / WBFM: tracks the spectrum
   * peak (AM) or occupied-band centroid (FM) and nudges the tune offset.
   */
  afc: boolean;
  /** True while AFC has a lock on a signal near the current tune point. */
  afcLocked: boolean;
  stereo: boolean;
  stereoActive: boolean;
  ppm: number;
  biasTee: boolean;
  error: string | null;
  webUsbSupported: boolean;
};

export type BandPreset = {
  id: string;
  label: string;
  frequencyHz: number;
  scheme: DemodScheme;
};

export const DEFAULT_SAMPLE_RATE = 1_024_000;
export const FFT_SIZE = 2048;
export const MIN_DB = -90;
export const MAX_DB = -20;

/**
 * RTL-SDR capture rates that are known to land cleanly on the 28.8 MHz crystal.
 * The stick can approximate other values, but these are the usual presets.
 */
export const SAMPLE_RATES = [
  { hz: 250_000, label: "0.25 MHz", hint: "Narrow" },
  { hz: 1_024_000, label: "1.024 MHz", hint: "Default" },
  { hz: 1_536_000, label: "1.536 MHz", hint: null },
  { hz: 2_048_000, label: "2.048 MHz", hint: "Wide" },
  { hz: 2_400_000, label: "2.4 MHz", hint: "FM band" },
  { hz: 2_880_000, label: "2.88 MHz", hint: "Max" },
] as const;

/** HackRF capture rates (it cannot run below 2 MS/s). */
export const HACKRF_SAMPLE_RATES = [
  { hz: 2_000_000, label: "2 MHz", hint: "Narrow" },
  { hz: 4_000_000, label: "4 MHz", hint: "BLE" },
  { hz: 8_000_000, label: "8 MHz", hint: null },
  { hz: 10_000_000, label: "10 MHz", hint: null },
  { hz: 16_000_000, label: "16 MHz", hint: null },
  { hz: 20_000_000, label: "20 MHz", hint: "Max" },
] as const;

export type SampleRateOption = (typeof SAMPLE_RATES)[number];

export function nearestSampleRate(hz: number): number {
  let best: number = SAMPLE_RATES[0].hz;
  let bestDist = Math.abs(hz - best);
  for (const option of SAMPLE_RATES) {
    const dist = Math.abs(hz - option.hz);
    if (dist < bestDist) {
      best = option.hz;
      bestDist = dist;
    }
  }
  return best;
}
