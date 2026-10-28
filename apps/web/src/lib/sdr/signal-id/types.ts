import type { DemodScheme } from "../types";

/**
 * Modulation classes the model can identify. These are RF-native labels derived
 * from spectral shape and how it moves over time, not audio content.
 */
export const SIGNAL_CLASSES = [
  "CW",
  "CARRIER",
  "AM",
  "USB",
  "LSB",
  "NBFM",
  "WBFM",
  "FSK",
  "DIGITAL",
  "NOISE",
] as const;

export type SignalClass = (typeof SIGNAL_CLASSES)[number];

export const CLASS_COUNT = SIGNAL_CLASSES.length;

/**
 * What the UI shows. "SSB" is never predicted directly — it is reported when
 * the model cannot separate upper from lower sideband, which the FFT's 500 Hz
 * resolution frequently makes impossible.
 */
export type DisplayClass = SignalClass | "SSB" | "UNKNOWN";

/** A contiguous run of bins that rose above the detection threshold. */
export type Detection = {
  /** Inclusive bin index of the lower band edge, in frequency-ordered bins. */
  loBin: number;
  /** Inclusive bin index of the upper band edge. */
  hiBin: number;
  peakBin: number;
  peakDb: number;
};

/**
 * One analysis window for a tracked signal: the detected band plus the recent
 * frame history that temporal features are computed from.
 */
export type FeatureWindow = {
  /** Most recent frames, frequency-ordered (DC centered), oldest first. */
  frames: Float32Array[];
  detection: Detection;
  noiseFloorDb: number;
  binHz: number;
};

export type SignalIdStatus = "idle" | "scanning" | "error";

/** Best SigID-catalog guess for what this emission actually is. */
export type SignalIdentity = {
  id: string;
  /** "artemis" = imported Artemis-DB entry; "curated" = hand-written. */
  origin: "artemis" | "curated";
  name: string;
  url: string;
  score: number;
  description?: string;
  categories: string[];
  mode?: string;
  modulation?: string;
};

export type IdentifiedSignal = {
  /** Stable across frames while the signal persists, so React rows do not remount. */
  id: string;
  /** Absolute tuned frequency of the signal's peak. */
  frequencyHz: number;
  /** Offset from the receiver's center frequency. */
  offsetHz: number;
  bandwidthHz: number;
  snrDb: number;
  label: DisplayClass;
  confidence: number;
  /** Demodulator to switch to when the user tunes this signal, if one applies. */
  scheme: DemodScheme | null;
  /** Top SigID wiki matches for frequency + bandwidth + modulation. */
  identities: SignalIdentity[];
  firstSeen: number;
  lastSeen: number;
  /** Where the label came from. "band-plan" means the frequency, not the model. */
  basis?: "model" | "band-plan" | "backend-cnn";
  /** The backend CNN's own class name (49-class set), before display mapping. */
  backendLabel?: string;
  /** Backend open-set verdict: confidence was below the acceptance threshold. */
  unknown?: boolean;
  /** How the backend arrived at this track, e.g. "CNN on spectrum rows". */
  evidence?: string;
  /** Next-best backend classes with their confidence. */
  alternatives?: { label: string; confidence: number }[];
  /** Saved or discovered name (e.g. a BLE device name). */
  title?: string;
  /** Why a band-plan label was applied, e.g. "FRS/GMRS CH 3 (462.6125 MHz)". */
  bandPlanNote?: string;
  /** The backend's id for this track; what a correction refers to. */
  trackId?: string;
  /** Rows-path session the track lives in (browser sources only). */
  session?: string;
  /** The user's own corrections moved this label; how many back it. */
  learned?: { label: string; examples: number; similarity: number; weight: number } | null;
  /** The user set this label by hand for this very signal. */
  userCorrected?: boolean;
  /** A label the user invented, outside the model's built-in classes. */
  customLabel?: boolean;
  /** Shape was inferred from very few bins, so the model's output is weaker evidence. */
  lowResolution?: boolean;
  /** Not seen recently; held briefly so bursty signals do not flicker away. */
  stale?: boolean;
};

export type SignalIdState = {
  enabled: boolean;
  status: SignalIdStatus;
  signals: IdentifiedSignal[];
  noiseFloorDb: number;
  error: string | null;
  /** Which pipeline produced `signals`; absent for the local model. */
  source?: "backend-cnn" | "offline-fallback";
  /** One line on the data behind `signals`, e.g. a fallback reason. */
  detail?: string;
};

export type SignalIdListener = (state: SignalIdState) => void;
