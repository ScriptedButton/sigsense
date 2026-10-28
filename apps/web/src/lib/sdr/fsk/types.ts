/** How mark/space are recovered from demodulated audio. */
export type FskDetector =
  /** Post-FM discriminator level (RF FSK via NBFM). */
  | "discriminator"
  /** Dual-tone Goertzel (AFSK on SSB / AM). */
  | "tones";

/** Character encoding of the serial bit stream. */
export type FskEncoding = "baudot" | "ascii";

export type FskPresetId =
  | "rtty-45"
  | "rtty-50"
  | "ascii-300"
  | "afsk-1200"
  | "custom";

export type FskConfig = {
  detector: FskDetector;
  encoding: FskEncoding;
  /** Symbol rate in baud (e.g. 45.45 for amateur RTTY). */
  baud: number;
  /** Mark−space separation in Hz (discriminator mode uses this only for UI). */
  shiftHz: number;
  /** Mark tone Hz (tones mode). */
  markHz: number;
  /** Space tone Hz (tones mode). */
  spaceHz: number;
  /** Invert mark/space polarity. */
  inverted: boolean;
  /** Stop bits (1, 1.5, or 2). */
  stopBits: number;
  /** Active preset chip, or custom when knobs diverge. */
  preset: FskPresetId;
};

export type FskStatus = "idle" | "listening" | "decoding";

export type FskState = {
  enabled: boolean;
  status: FskStatus;
  config: FskConfig;
  /** Decoded text (rolling buffer). */
  text: string;
  /** Most recent raw character for status UI. */
  lastChar: string | null;
  /** Estimated mark−space confidence 0…1 from recent soft bits. */
  lock: number;
  error: string | null;
};

export type FskListener = (state: FskState) => void;

export const FSK_PRESETS: Record<
  Exclude<FskPresetId, "custom">,
  { label: string; config: Omit<FskConfig, "preset"> }
> = {
  "rtty-45": {
    label: "RTTY 45",
    config: {
      detector: "discriminator",
      encoding: "baudot",
      baud: 45.45,
      shiftHz: 170,
      markHz: 2125,
      spaceHz: 2295,
      inverted: false,
      stopBits: 1.5,
    },
  },
  "rtty-50": {
    label: "RTTY 50",
    config: {
      detector: "discriminator",
      encoding: "baudot",
      baud: 50,
      shiftHz: 170,
      markHz: 2125,
      spaceHz: 2295,
      inverted: false,
      stopBits: 1.5,
    },
  },
  "ascii-300": {
    label: "ASCII 300",
    config: {
      detector: "discriminator",
      encoding: "ascii",
      baud: 300,
      shiftHz: 850,
      markHz: 2125,
      spaceHz: 2975,
      inverted: false,
      stopBits: 2,
    },
  },
  "afsk-1200": {
    label: "AFSK 1200",
    config: {
      detector: "tones",
      encoding: "ascii",
      baud: 1200,
      shiftHz: 1000,
      markHz: 1200,
      spaceHz: 2200,
      inverted: false,
      stopBits: 1,
    },
  },
};

export const DEFAULT_FSK_CONFIG: FskConfig = {
  ...FSK_PRESETS["rtty-45"].config,
  preset: "rtty-45",
};

/** Demo carrier used by FakeRtl so the decoder can be tried without hardware. */
export const DEMO_FSK_FREQUENCY_HZ = 14_080_000;
