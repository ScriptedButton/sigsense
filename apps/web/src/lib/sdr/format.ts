import { DEMO_FSK_FREQUENCY_HZ } from "./fsk/types";
import { DEMO_FT8_FREQUENCY_HZ } from "./ft8/types";
import { GMRS_CALLING_CHANNEL } from "./gmrs/channels";
import { DEMO_MORSE_FREQUENCY_HZ } from "./morse/types";
import { DEMO_P25_FREQUENCY_HZ } from "./p25/types";
import type { BandPreset } from "./types";
import { DEMO_WEFAX_FREQUENCY_HZ } from "./wefax/types";

export const BAND_PRESETS: BandPreset[] = [
  { id: "fm-885", label: "FM 88.5", frequencyHz: 88_500_000, scheme: "WBFM" },
  { id: "fm-939", label: "FM 93.9", frequencyHz: 93_900_000, scheme: "WBFM" },
  { id: "fm-1011", label: "FM 101.1", frequencyHz: 101_100_000, scheme: "WBFM" },
  { id: "air", label: "Air 118.0", frequencyHz: 118_000_000, scheme: "AM" },
  { id: "2m", label: "2m 146.52", frequencyHz: 146_520_000, scheme: "NBFM" },
  { id: "wx", label: "WX 162.55", frequencyHz: 162_550_000, scheme: "NBFM" },
  { id: "70cm", label: "70cm 446.0", frequencyHz: 446_000_000, scheme: "NBFM" },
  {
    id: "gmrs-20",
    label: `GMRS ${GMRS_CALLING_CHANNEL.channel}`,
    frequencyHz: GMRS_CALLING_CHANNEL.frequencyHz,
    scheme: "NBFM",
  },
  {
    id: "gmrs-15",
    label: "GMRS 15",
    frequencyHz: 462_550_000,
    scheme: "NBFM",
  },
  { id: "am-810", label: "AM 810", frequencyHz: 810_000, scheme: "AM" },
  { id: "20m", label: "20m 14.225", frequencyHz: 14_225_000, scheme: "USB" },
  {
    id: "cw-demo",
    label: "CW 14.050",
    frequencyHz: DEMO_MORSE_FREQUENCY_HZ,
    scheme: "CW",
  },
  {
    id: "rtty-demo",
    label: "RTTY 14.080",
    frequencyHz: DEMO_FSK_FREQUENCY_HZ,
    scheme: "NBFM",
  },
  {
    id: "ft8-demo",
    label: "FT8 14.074",
    frequencyHz: DEMO_FT8_FREQUENCY_HZ,
    scheme: "USB",
  },
  {
    id: "wefax-demo",
    label: "Fax 12.625",
    frequencyHz: DEMO_WEFAX_FREQUENCY_HZ,
    scheme: "USB",
  },
  {
    id: "p25-demo",
    label: "P25 851.0125",
    frequencyHz: DEMO_P25_FREQUENCY_HZ,
    scheme: "NBFM",
  },
];

export function formatFrequency(hz: number): string {
  const abs = Math.abs(hz);
  if (abs >= 1e9) return `${(hz / 1e9).toFixed(6)} GHz`;
  if (abs >= 1e6) return `${(hz / 1e6).toFixed(3)} MHz`;
  if (abs >= 1e3) return `${(hz / 1e3).toFixed(2)} kHz`;
  return `${Math.round(hz)} Hz`;
}

/** Format with an explicit unit preference (auto picks by magnitude). */
export function formatFrequencyWith(
  hz: number,
  format: "auto" | "mhz" | "khz" | "hz",
): string {
  if (format === "mhz") return `${(hz / 1e6).toFixed(6)} MHz`;
  if (format === "khz") return `${(hz / 1e3).toFixed(3)} kHz`;
  if (format === "hz") return `${Math.round(hz)} Hz`;
  return formatFrequency(hz);
}

export function formatFrequencyCompact(hz: number): string {
  if (Math.abs(hz) >= 1e6) return (hz / 1e6).toFixed(3);
  if (Math.abs(hz) >= 1e3) return (hz / 1e3).toFixed(2);
  return String(Math.round(hz));
}

/** Compact width readout — bandwidths do not warrant sub-hertz precision. */
export function formatBandwidth(hz: number): string {
  if (hz >= 1e6) return `${(hz / 1e6).toFixed(2)} MHz`;
  if (hz >= 1e3) return `${(hz / 1e3).toFixed(1)} kHz`;
  return `${Math.round(hz)} Hz`;
}

export function parseFrequencyInput(raw: string, unit: "MHz" | "kHz" | "Hz"): number | null {
  const cleaned = raw.trim().replace(/,/g, "");
  if (!cleaned) return null;
  const value = Number(cleaned);
  if (!Number.isFinite(value) || value < 0) return null;
  if (unit === "MHz") return value * 1e6;
  if (unit === "kHz") return value * 1e3;
  return value;
}

export function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n));
}
