/** P25 Phase 1 operating mode. */
export type P25Mode = "conventional" | "trunk";

export type P25Config = {
  mode: P25Mode;
  /** NAC filter — null / -1 accepts any. */
  nac: number | null;
  /** Control-channel frequencies (Hz) for trunk mode. */
  controlChannelsHz: number[];
  /** Hangtime after last voice activity before returning to CC (ms). */
  hangtimeMs: number;
  /** NBFM-equivalent RF bandwidth for C4FM (Hz). */
  bandwidthHz: number;
  /** Play clear IMBE voice when available. */
  playVoice: boolean;
  /** Follow voice grants in trunk mode (retune dongle). */
  followGrants: boolean;
};

export type P25Status =
  | "idle"
  | "listening"
  | "synced"
  | "trunking"
  | "voice"
  | "error";

export type P25Duid =
  | "HDU"
  | "TDU"
  | "LDU1"
  | "TSBK"
  | "LDU2"
  | "PDU"
  | "TDULC"
  | "UNKNOWN";

export type P25SystemInfo = {
  nac: number | null;
  wacn: number | null;
  systemId: number | null;
  rfssId: number | null;
  siteId: number | null;
};

export type P25Call = {
  id: string;
  tgid: number;
  sourceId: number | null;
  frequencyHz: number;
  channel: number | null;
  encrypted: boolean;
  emergency: boolean;
  startMs: number;
  lastActivityMs: number;
  active: boolean;
};

export type P25Message = {
  id: number;
  timestampMs: number;
  opcode: number | null;
  opcodeName: string;
  nac: number | null;
  summary: string;
};

export type P25State = {
  enabled: boolean;
  status: P25Status;
  config: P25Config;
  system: P25SystemInfo;
  /** Sync / symbol lock quality 0…1. */
  lock: number;
  /** Approximate frames / second. */
  decodeRate: number;
  /** Control channel currently parked on (trunk). */
  controlChannelHz: number | null;
  /** Active voice frequency when following a grant. */
  voiceFrequencyHz: number | null;
  activeCalls: P25Call[];
  recentCalls: P25Call[];
  messages: P25Message[];
  error: string | null;
  /** Whether IMBE WASM loaded successfully. */
  imbeReady: boolean;
};

export type P25Listener = (state: P25State) => void;

/** Callbacks the engine uses to retune / mute the radio. */
export type P25RadioHooks = {
  setFrequency: (hz: number) => void | Promise<void>;
  setOffset: (hz: number) => void;
  setSchemeNbfm: () => void;
  setDigitalMute: (muted: boolean) => void;
  getFrequency: () => number;
  getOffset: () => number;
};

export const DEFAULT_P25_CONFIG: P25Config = {
  mode: "conventional",
  nac: null,
  controlChannelsHz: [],
  hangtimeMs: 2_000,
  bandwidthHz: 12_500,
  playVoice: true,
  followGrants: true,
};

export const DEFAULT_P25_SYSTEM: P25SystemInfo = {
  nac: null,
  wacn: null,
  systemId: null,
  rfssId: null,
  siteId: null,
};

/** Demo C4FM carrier used by FakeRtl. */
export const DEMO_P25_FREQUENCY_HZ = 851_012_500;

export const P25_SYMBOL_RATE = 4_800;
export const P25_BASEBAND_RATE = 48_000;

export function duidLabel(duid: number): P25Duid {
  switch (duid & 0xf) {
    case 0x0:
      return "HDU";
    case 0x3:
      return "TDU";
    case 0x5:
      return "LDU1";
    case 0x7:
      return "TSBK";
    case 0xa:
      return "LDU2";
    case 0xc:
      return "PDU";
    case 0xf:
      return "TDULC";
    default:
      return "UNKNOWN";
  }
}

export function nacHex(nac: number | null): string {
  if (nac == null) return "—";
  return nac.toString(16).toUpperCase().padStart(3, "0");
}
