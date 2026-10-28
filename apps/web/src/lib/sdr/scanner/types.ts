import type { DemodScheme } from "@/lib/sdr/types";

/** One memory channel the scanner can hop to. */
export type ScanChannel = {
  id: string;
  label: string;
  frequencyHz: number;
  scheme: DemodScheme;
  /** Optional override; otherwise scheme defaults apply. */
  bandwidthHz?: number;
  /** Skip while scanning (still listed; tap to tune manually). */
  lockedOut: boolean;
};

/** Named list of channels (GMRS, WX, custom, …). */
export type ScanBank = {
  id: string;
  name: string;
  channels: ScanChannel[];
  /** Seed banks; user may lock out / add but not delete the bank. */
  builtin: boolean;
};

export type ScannerStatus = "idle" | "scanning" | "held" | "paused";

export type ScannerHoldReason = "activity" | "manual" | null;

export type ScannerConfig = {
  activeBankId: string;
  /** Quiet dwell before advancing (ms). */
  dwellMs: number;
  /**
   * After activity, wait this long with squelch closed before resuming.
   * 0 = stay held until Skip / Resume.
   */
  holdMs: number;
  /** Hold when squelch opens (needs squelch enabled on the radio). */
  stopOnActivity: boolean;
  /** Auto-resume after holdMs of quiet when stopOnActivity held us. */
  resumeOnQuiet: boolean;
};

export type ScannerState = {
  status: ScannerStatus;
  config: ScannerConfig;
  banks: ScanBank[];
  /** Index into the active bank's scanable (non-locked) list. */
  scanIndex: number;
  currentChannelId: string | null;
  holdReason: ScannerHoldReason;
  /** Channels visited this scan cycle (for UI progress). */
  hopCount: number;
  error: string | null;
};

export type ScannerListener = (state: ScannerState) => void;

/** Radio hooks the engine needs to hop the LO / mode. */
export type ScannerRadioHooks = {
  setFrequency: (hz: number) => Promise<void>;
  setScheme: (scheme: DemodScheme) => void;
  setBandwidth: (hz: number) => void;
  setOffset: (hz: number) => void;
  getSquelchOpen: () => boolean;
  getSquelchActive: () => boolean;
  isConnected: () => boolean;
};

export const DWELL_OPTIONS_MS = [
  { id: "250", ms: 250, label: "0.25 s" },
  { id: "500", ms: 500, label: "0.5 s" },
  { id: "1000", ms: 1_000, label: "1 s" },
  { id: "2000", ms: 2_000, label: "2 s" },
  { id: "3000", ms: 3_000, label: "3 s" },
] as const;

export const HOLD_OPTIONS_MS = [
  { id: "0", ms: 0, label: "Forever" },
  { id: "2000", ms: 2_000, label: "2 s" },
  { id: "5000", ms: 5_000, label: "5 s" },
  { id: "10000", ms: 10_000, label: "10 s" },
] as const;

export const DEFAULT_SCANNER_CONFIG: ScannerConfig = {
  activeBankId: "gmrs",
  dwellMs: 500,
  holdMs: 5_000,
  stopOnActivity: true,
  resumeOnQuiet: true,
};

export const SCANNER_STORAGE_KEY = "sdrsense.scanner";
