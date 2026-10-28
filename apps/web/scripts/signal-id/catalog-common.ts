/**
 * Helpers shared by the catalog builders (sigidwiki live fetch, Artemis DB).
 */

import type { DisplayClass } from "../../src/lib/sdr/signal-id/types";

/**
 * Maps SigID Mode + Modulation + bandwidth into our DisplayClass set.
 * Multiple classes are allowed when the wiki entry is ambiguous.
 */
export function mapClasses(mode: string, modulation: string, bwHi: number | null): DisplayClass[] {
  const classes = new Set<DisplayClass>();
  const modeL = mode.toLowerCase();
  const modL = modulation.toLowerCase();

  if (/\bcw\b|morse|ook/.test(modeL) || /\bcw\b|morse|ook/.test(modL)) {
    classes.add("CW");
  }
  if (/\busb\b/.test(modeL)) classes.add("USB");
  if (/\blsb\b/.test(modeL)) classes.add("LSB");
  if (/\bssb\b/.test(modeL) && !classes.has("USB") && !classes.has("LSB")) {
    classes.add("USB");
    classes.add("LSB");
  }
  if (/\bam\b|dsb/.test(modeL) || /\bam\b/.test(modL)) classes.add("AM");
  if (/\bwfm\b|wbfm|broadcast/.test(modeL)) classes.add("WBFM");
  if (/\bnfm\b|nbfm|fm\b/.test(modeL) && !classes.has("WBFM")) {
    if (bwHi != null && bwHi >= 80_000) classes.add("WBFM");
    else classes.add("NBFM");
  }

  if (/\bfsk\b|afsk|gfsk|msk|gmsk|c4fm|mfsk/.test(modL)) classes.add("FSK");
  if (
    /\bpsk\b|qpsk|qam|ofdm|gfsk|gmsk|cdma|tdma|dsss|fhss|lora|chirp|pam|ppm|pwm|pulse|ask\b|digital/.test(
      modL,
    )
  ) {
    classes.add("DIGITAL");
  }
  if (/\bfm\b/.test(modL) && !classes.has("NBFM") && !classes.has("WBFM")) {
    if (bwHi != null && bwHi >= 80_000) classes.add("WBFM");
    else classes.add("NBFM");
  }

  // RAW/IQ pages with no usable mode still help via frequency + bandwidth.
  if (classes.size === 0) {
    if (bwHi != null && bwHi < 200) classes.add("CW");
    else if (bwHi != null && bwHi < 5_000) classes.add("CARRIER");
    else classes.add("DIGITAL");
  }

  return [...classes];
}

export function slugify(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 80);
}
