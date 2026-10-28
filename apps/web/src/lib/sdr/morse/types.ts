import type { MorseKeywordHit } from "./keywords";

/** How keyed audio is recovered from the demod tap. */
export type MorseDetector =
  /** Goertzel at toneHz (CW sidetone / USB pitch). */
  | "tone"
  /** Full-wave envelope (any-pitch keyed audio). */
  | "envelope";

export type MorsePresetId = "cw-15" | "cw-20" | "cw-25" | "auto" | "custom";

export type MorseConfig = {
  detector: MorseDetector;
  /** Nominal WPM — seed for timing; also used when autoSpeed is off. */
  wpm: number;
  /** Adapt dit length from observed short marks. */
  autoSpeed: boolean;
  /** Goertzel center Hz (CW demod default sidetone is 600). */
  toneHz: number;
  /**
   * Detection aggressiveness 0…1.
   * Higher = lower threshold (more sensitive, more false dits).
   */
  sensitivity: number;
  preset: MorsePresetId;
};

export type MorseStatus = "idle" | "listening" | "decoding";

export type { MorseKeywordHit, MorseKeywordKind } from "./keywords";

export type MorseState = {
  enabled: boolean;
  status: MorseStatus;
  config: MorseConfig;
  /** Decoded text (rolling buffer). */
  text: string;
  /** Identified CW keywords / Q-codes / callsigns in `text` (unique). */
  keywords: MorseKeywordHit[];
  /** Most recent character for status UI. */
  lastChar: string | null;
  /** Estimated WPM from element timing. */
  wpmEstimate: number;
  /** Tone / envelope lock quality 0…1. */
  lock: number;
  error: string | null;
};

export type MorseListener = (state: MorseState) => void;

export const MORSE_PRESETS: Record<
  Exclude<MorsePresetId, "custom">,
  { label: string; config: Omit<MorseConfig, "preset"> }
> = {
  "cw-15": {
    label: "15 WPM",
    config: {
      detector: "tone",
      wpm: 15,
      autoSpeed: false,
      toneHz: 600,
      sensitivity: 0.55,
    },
  },
  "cw-20": {
    label: "20 WPM",
    config: {
      detector: "tone",
      wpm: 20,
      autoSpeed: false,
      toneHz: 600,
      sensitivity: 0.55,
    },
  },
  "cw-25": {
    label: "25 WPM",
    config: {
      detector: "tone",
      wpm: 25,
      autoSpeed: false,
      toneHz: 600,
      sensitivity: 0.55,
    },
  },
  auto: {
    label: "Auto WPM",
    config: {
      detector: "tone",
      wpm: 20,
      autoSpeed: true,
      toneHz: 600,
      sensitivity: 0.55,
    },
  },
};

export const DEFAULT_MORSE_CONFIG: MorseConfig = {
  ...MORSE_PRESETS["cw-20"].config,
  preset: "cw-20",
};

/** Demo carrier used by FakeRtl — typical 20 m CW segment. */
export const DEMO_MORSE_FREQUENCY_HZ = 14_050_000;

/** Dit duration in seconds for a given WPM (Paris standard: 50 units / word). */
export function ditSeconds(wpm: number): number {
  return 1.2 / Math.max(5, wpm);
}
