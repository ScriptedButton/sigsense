import type { DemodScheme } from "@/lib/sdr/types";

const TO_OWRX: Record<DemodScheme, string> = {
  WBFM: "wfm",
  NBFM: "nfm",
  AM: "am",
  USB: "usb",
  LSB: "lsb",
  CW: "cw",
};

const FROM_OWRX: Record<string, DemodScheme> = {
  wfm: "WBFM",
  nfm: "NBFM",
  am: "AM",
  sam: "AM",
  usb: "USB",
  lsb: "LSB",
  cw: "CW",
};

export function owrxModulation(scheme: DemodScheme): string {
  return TO_OWRX[scheme];
}

export function schemeFromOwrx(mod: string | null | undefined): DemodScheme | null {
  if (!mod) return null;
  return FROM_OWRX[mod.toLowerCase()] ?? null;
}

/** Passband edges the OpenWebRX+ demodulator expects, in Hz. */
export function owrxBandpass(
  scheme: DemodScheme,
  bandwidthHz: number,
): { low_cut: number; high_cut: number } {
  const bw = Math.max(50, Math.round(Math.abs(bandwidthHz)));
  if (scheme === "USB") return { low_cut: 0, high_cut: bw };
  if (scheme === "LSB") return { low_cut: -bw, high_cut: 0 };
  const half = Math.max(25, Math.round(bw / 2));
  return { low_cut: -half, high_cut: half };
}
