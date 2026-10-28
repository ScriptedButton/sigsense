import type { DemodScheme } from "../types";
import type { DisplayClass } from "./types";

export type SignalClassInfo = {
  /** Short label for badges and the spectrum overlay. */
  short: string;
  /** Expanded name for tooltips and screen readers. */
  name: string;
  /** One-line description of the spectral signature. */
  hint: string;
  /** Demodulator that best receives this class, if any. */
  scheme: DemodScheme | null;
  /** Badge colour, chosen for AA contrast on the dark canvas. */
  color: string;
};

export const SIGNAL_CLASS_INFO: Record<DisplayClass, SignalClassInfo> = {
  CW: {
    short: "CW",
    name: "Morse / on-off keying",
    hint: "Very narrow carrier switching on and off",
    scheme: "CW",
    color: "oklch(0.80 0.14 75)",
  },
  CARRIER: {
    short: "CARRIER",
    name: "Unmodulated carrier",
    hint: "Narrow steady tone carrying no modulation",
    scheme: "AM",
    color: "oklch(0.72 0.03 355)",
  },
  AM: {
    short: "AM",
    name: "Amplitude modulation",
    hint: "Strong carrier with symmetric sidebands",
    scheme: "AM",
    color: "oklch(0.78 0.12 195)",
  },
  USB: {
    short: "USB",
    name: "Upper sideband",
    hint: "Suppressed carrier, energy above centre only",
    scheme: "USB",
    color: "oklch(0.78 0.15 150)",
  },
  LSB: {
    short: "LSB",
    name: "Lower sideband",
    hint: "Suppressed carrier, energy below centre only",
    scheme: "LSB",
    color: "oklch(0.74 0.13 170)",
  },
  SSB: {
    short: "SSB",
    name: "Single sideband, side unclear",
    hint: "Sideband voice — too narrow here to tell upper from lower",
    scheme: "USB",
    color: "oklch(0.76 0.14 160)",
  },
  NBFM: {
    short: "NBFM",
    name: "Narrowband FM",
    hint: "Roughly 12–16 kHz wide, no carrier spike",
    scheme: "NBFM",
    color: "oklch(0.70 0.18 355)",
  },
  WBFM: {
    short: "WBFM",
    name: "Wideband FM",
    hint: "Broadcast-width FM, 150 kHz or more",
    scheme: "WBFM",
    color: "oklch(0.71 0.16 300)",
  },
  FSK: {
    short: "FSK",
    name: "Frequency-shift keying",
    hint: "Two or more discrete tones alternating",
    scheme: "NBFM",
    color: "oklch(0.76 0.16 50)",
  },
  DIGITAL: {
    short: "DIGITAL",
    name: "Digital / data burst",
    hint: "Flat top with steep edges, noise-like inside",
    scheme: "NBFM",
    color: "oklch(0.73 0.15 250)",
  },
  UNKNOWN: {
    short: "UNKNOWN",
    name: "Unknown signal",
    hint: "Detected, but the classifier is not confident enough to name it",
    scheme: null,
    color: "oklch(0.66 0.04 90)",
  },
  NOISE: {
    short: "NOISE",
    name: "Noise or spur",
    hint: "No consistent structure — likely interference",
    scheme: null,
    color: "oklch(0.58 0.015 355)",
  },
};

export function classInfo(label: DisplayClass): SignalClassInfo {
  return SIGNAL_CLASS_INFO[label];
}
