/** WSJT-X-style decode depth for @e04/ft8ts. */
export type Ft8Depth = 1 | 2 | 3 | 4;

export type Ft8Config = {
  /** 1=fast … 4=deepest (slower, more weak-signal finds). */
  depth: Ft8Depth;
  /** Audio search band low edge (Hz). */
  freqLow: number;
  /** Audio search band high edge (Hz). */
  freqHigh: number;
};

export type Ft8Message = {
  id: string;
  /** UTC period start (ms). */
  at: number;
  msg: string;
  snr: number;
  /** Time offset within the period (s). */
  dt: number;
  /** Audio tone frequency (Hz). */
  freq: number;
  sync: number;
};

export type Ft8Status = "idle" | "listening" | "decoding";

export type Ft8State = {
  enabled: boolean;
  status: Ft8Status;
  config: Ft8Config;
  messages: Ft8Message[];
  /** Position within the current 15 s UTC cycle (0…15). */
  cycleSec: number;
  error: string | null;
};

export type Ft8Listener = (state: Ft8State) => void;

export const DEFAULT_FT8_CONFIG: Ft8Config = {
  depth: 2,
  freqLow: 200,
  freqHigh: 3000,
};

/** 20 m FT8 dial (USB). Demo FakeRtl puts a CQ there. */
export const DEMO_FT8_FREQUENCY_HZ = 14_074_000;

export const FT8_CYCLE_SEC = 15;
export const FT8_TX_SEC = 12.64;
export const FT8_DECODE_RATE = 12_000;
