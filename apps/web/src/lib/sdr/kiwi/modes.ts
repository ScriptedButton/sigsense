import type { DemodScheme } from "@/lib/sdr/types";

const TO_KIWI: Record<DemodScheme, string> = {
  WBFM: "nbfm",
  NBFM: "nbfm",
  AM: "am",
  USB: "usb",
  LSB: "lsb",
  CW: "cw",
};

const FROM_KIWI: Record<string, DemodScheme> = {
  am: "AM",
  amn: "AM",
  amw: "AM",
  sam: "AM",
  usb: "USB",
  usn: "USB",
  lsb: "LSB",
  lsn: "LSB",
  cw: "CW",
  cwn: "CW",
  nbfm: "NBFM",
  nnfm: "NBFM",
};

export function kiwiModulation(scheme: DemodScheme): string {
  return TO_KIWI[scheme];
}

export function schemeFromKiwi(mod: string | null | undefined): DemodScheme | null {
  if (!mod) return null;
  return FROM_KIWI[mod.toLowerCase()] ?? null;
}

/** Passband edges the Kiwi demodulator expects, in Hz. */
export function kiwiBandpass(
  scheme: DemodScheme,
  bandwidthHz: number,
): { low_cut: number; high_cut: number } {
  const bw = Math.max(50, Math.round(Math.abs(bandwidthHz)));
  if (scheme === "USB") return { low_cut: 300, high_cut: Math.max(400, bw) };
  if (scheme === "LSB") return { low_cut: -Math.max(400, bw), high_cut: -300 };
  if (scheme === "CW") {
    const half = Math.max(25, Math.round(bw / 2));
    const center = 500;
    return { low_cut: center - half, high_cut: center + half };
  }
  const half = Math.max(25, Math.round(bw / 2));
  return { low_cut: -half, high_cut: half };
}
