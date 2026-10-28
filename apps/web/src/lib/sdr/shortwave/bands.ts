import type { ShortwaveBand, ShortwaveBandId } from "@/lib/sdr/shortwave/types";

/** ITU HF broadcast meter bands (approximate allocations). */
export const SHORTWAVE_BANDS: readonly ShortwaveBand[] = [
  { id: "120m", label: "120 m", loKHz: 2_300, hiKHz: 2_495 },
  { id: "90m", label: "90 m", loKHz: 3_200, hiKHz: 3_400 },
  { id: "75m", label: "75 m", loKHz: 3_900, hiKHz: 4_000 },
  { id: "60m", label: "60 m", loKHz: 4_750, hiKHz: 5_060 },
  { id: "49m", label: "49 m", loKHz: 5_900, hiKHz: 6_200 },
  { id: "41m", label: "41 m", loKHz: 7_200, hiKHz: 7_450 },
  { id: "31m", label: "31 m", loKHz: 9_400, hiKHz: 9_900 },
  { id: "25m", label: "25 m", loKHz: 11_600, hiKHz: 12_100 },
  { id: "22m", label: "22 m", loKHz: 13_570, hiKHz: 13_870 },
  { id: "19m", label: "19 m", loKHz: 15_100, hiKHz: 15_800 },
  { id: "16m", label: "16 m", loKHz: 17_480, hiKHz: 17_900 },
  { id: "15m", label: "15 m", loKHz: 18_900, hiKHz: 19_020 },
  { id: "13m", label: "13 m", loKHz: 21_450, hiKHz: 21_850 },
  { id: "11m", label: "11 m", loKHz: 25_670, hiKHz: 26_100 },
];

export function bandForKHz(kHz: number): ShortwaveBand | null {
  for (const band of SHORTWAVE_BANDS) {
    if (kHz >= band.loKHz && kHz <= band.hiKHz) return band;
  }
  return null;
}

export function bandById(id: ShortwaveBandId): ShortwaveBand | undefined {
  return SHORTWAVE_BANDS.find((b) => b.id === id);
}
