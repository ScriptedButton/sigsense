/** Index of cooperation — sets pixels per scan line (IOC × π). */
export type WefaxIoc = 576 | 288;

/** Lines per minute — sets scan-line duration (60 / LPM seconds). */
export type WefaxLpm = 120 | 60;

export type WefaxSyncMode = "auto" | "manual";

export type WefaxPresetId =
  | "ioc576-120"
  | "ioc576-60"
  | "ioc288-120"
  | "custom";

export type WefaxConfig = {
  ioc: WefaxIoc;
  lpm: WefaxLpm;
  /** Audio frequency for black (typical 1500 Hz on USB). */
  blackHz: number;
  /** Audio frequency for white (typical 2300 Hz on USB). */
  whiteHz: number;
  /** Invert black/white polarity. */
  inverted: boolean;
  /** Auto APT start/stop + phasing, or free-run imaging. */
  sync: WefaxSyncMode;
  /** Fine line-timing trim in percent (−5…+5). */
  slantPct: number;
  preset: WefaxPresetId;
};

export type WefaxStatus =
  | "idle"
  | "listening"
  | "starting"
  | "phasing"
  | "imaging"
  | "stopped";

export type WefaxState = {
  enabled: boolean;
  status: WefaxStatus;
  config: WefaxConfig;
  /** Pixels per scan line from IOC. */
  width: number;
  /** Completed scan lines in the current image. */
  lines: number;
  /** Bumps when the pixel buffer advances (canvas refresh). */
  frameId: number;
  /** Smoothed instantaneous audio tone Hz. */
  toneHz: number;
  /** Soft lock quality 0…1 from tone / sync. */
  lock: number;
  error: string | null;
};

export type WefaxListener = (state: WefaxState) => void;

export const WEFAX_PRESETS: Record<
  Exclude<WefaxPresetId, "custom">,
  { label: string; config: Omit<WefaxConfig, "preset"> }
> = {
  "ioc576-120": {
    label: "IOC 576 · 120",
    config: {
      ioc: 576,
      lpm: 120,
      blackHz: 1500,
      whiteHz: 2300,
      inverted: false,
      sync: "auto",
      slantPct: 0,
    },
  },
  "ioc576-60": {
    label: "IOC 576 · 60",
    config: {
      ioc: 576,
      lpm: 60,
      blackHz: 1500,
      whiteHz: 2300,
      inverted: false,
      sync: "auto",
      slantPct: 0,
    },
  },
  "ioc288-120": {
    label: "IOC 288 · 120",
    config: {
      ioc: 288,
      lpm: 120,
      blackHz: 1500,
      whiteHz: 2300,
      inverted: false,
      sync: "auto",
      slantPct: 0,
    },
  },
};

export const DEFAULT_WEFAX_CONFIG: WefaxConfig = {
  ...WEFAX_PRESETS["ioc576-120"].config,
  preset: "ioc576-120",
};

/** ITU-R pixels-per-line ≈ IOC × π. */
export function wefaxLineWidth(ioc: WefaxIoc): number {
  return Math.round(ioc * Math.PI);
}

/** Scan-line duration in seconds. */
export function wefaxLineSeconds(lpm: WefaxLpm): number {
  return 60 / lpm;
}

/** Demo USB dial used by FakeRtl so the decoder can be tried without hardware. */
export const DEMO_WEFAX_FREQUENCY_HZ = 12_625_000;

/** Cap image height so the rail canvas stays bounded. */
export const WEFAX_MAX_LINES = 2_400;

/** APT start B/W FSK rate → IOC 576 (ITU). */
export const WEFAX_APT_START_IOC576_HZ = 300;
/** APT start B/W FSK rate → IOC 288. */
export const WEFAX_APT_START_IOC288_HZ = 675;
/** APT stop B/W FSK rate. */
export const WEFAX_APT_STOP_HZ = 450;
