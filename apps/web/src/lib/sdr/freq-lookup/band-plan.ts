import type { DemodScheme } from "@/lib/sdr/types";

/** Service / amateur allocation for reverse frequency lookup. */
export type BandPlanEntry = {
  id: string;
  name: string;
  /** Inclusive Hz range. */
  loHz: number;
  hiHz: number;
  scheme?: DemodScheme;
  detail?: string;
  region?: string;
};

/**
 * Curated HF–UHF allocations useful for RTL-SDR users.
 * Not a full FCC table — focused ranges for “what’s on this frequency?”.
 */
export const BAND_PLAN: readonly BandPlanEntry[] = [
  // MF / HF broadcast & amateur
  {
    id: "mw-am",
    name: "AM Broadcast",
    loHz: 530_000,
    hiHz: 1_700_000,
    scheme: "AM",
    detail: "Medium-wave AM broadcast",
  },
  {
    id: "160m",
    name: "160 m Amateur",
    loHz: 1_800_000,
    hiHz: 2_000_000,
    scheme: "LSB",
    detail: "160-meter ham band",
  },
  {
    id: "120m-sw",
    name: "120 m Shortwave",
    loHz: 2_300_000,
    hiHz: 2_495_000,
    scheme: "AM",
    detail: "Tropical broadcast",
  },
  {
    id: "80m",
    name: "80 m Amateur",
    loHz: 3_500_000,
    hiHz: 4_000_000,
    scheme: "LSB",
    detail: "80-meter ham band",
  },
  {
    id: "75m-sw",
    name: "75 / 90 m Shortwave",
    loHz: 3_200_000,
    hiHz: 3_400_000,
    scheme: "AM",
  },
  {
    id: "60m",
    name: "60 m Amateur",
    loHz: 5_330_500,
    hiHz: 5_406_500,
    scheme: "USB",
    detail: "60-meter channels (region-dependent)",
  },
  {
    id: "49m-sw",
    name: "49 m Shortwave",
    loHz: 5_900_000,
    hiHz: 6_200_000,
    scheme: "AM",
  },
  {
    id: "40m",
    name: "40 m Amateur",
    loHz: 7_000_000,
    hiHz: 7_300_000,
    scheme: "LSB",
    detail: "40-meter ham band",
  },
  {
    id: "41m-sw",
    name: "41 m Shortwave",
    loHz: 7_200_000,
    hiHz: 7_450_000,
    scheme: "AM",
  },
  {
    id: "31m-sw",
    name: "31 m Shortwave",
    loHz: 9_400_000,
    hiHz: 9_900_000,
    scheme: "AM",
  },
  {
    id: "30m",
    name: "30 m Amateur",
    loHz: 10_100_000,
    hiHz: 10_150_000,
    scheme: "CW",
    detail: "30-meter WARC band",
  },
  {
    id: "25m-sw",
    name: "25 m Shortwave",
    loHz: 11_600_000,
    hiHz: 12_100_000,
    scheme: "AM",
  },
  {
    id: "22m-sw",
    name: "22 m Shortwave",
    loHz: 13_570_000,
    hiHz: 13_870_000,
    scheme: "AM",
  },
  {
    id: "20m",
    name: "20 m Amateur",
    loHz: 14_000_000,
    hiHz: 14_350_000,
    scheme: "USB",
    detail: "20-meter ham band",
  },
  {
    id: "19m-sw",
    name: "19 m Shortwave",
    loHz: 15_100_000,
    hiHz: 15_800_000,
    scheme: "AM",
  },
  {
    id: "17m",
    name: "17 m Amateur",
    loHz: 18_068_000,
    hiHz: 18_168_000,
    scheme: "USB",
  },
  {
    id: "16m-sw",
    name: "16 m Shortwave",
    loHz: 17_480_000,
    hiHz: 17_900_000,
    scheme: "AM",
  },
  {
    id: "15m",
    name: "15 m Amateur",
    loHz: 21_000_000,
    hiHz: 21_450_000,
    scheme: "USB",
  },
  {
    id: "13m-sw",
    name: "13 m Shortwave",
    loHz: 21_450_000,
    hiHz: 21_850_000,
    scheme: "AM",
  },
  {
    id: "12m",
    name: "12 m Amateur",
    loHz: 24_890_000,
    hiHz: 24_990_000,
    scheme: "USB",
  },
  {
    id: "11m-sw",
    name: "11 m Shortwave",
    loHz: 25_670_000,
    hiHz: 26_100_000,
    scheme: "AM",
  },
  {
    id: "cb",
    name: "CB / 11 m",
    loHz: 26_965_000,
    hiHz: 27_405_000,
    scheme: "AM",
    detail: "Citizens Band (region-dependent)",
  },
  {
    id: "10m",
    name: "10 m Amateur",
    loHz: 28_000_000,
    hiHz: 29_700_000,
    scheme: "USB",
  },
  // VHF
  {
    id: "fm-bcast",
    name: "FM Broadcast",
    loHz: 87_500_000,
    hiHz: 108_000_000,
    scheme: "WBFM",
    detail: "88–108 MHz commercial / public FM",
  },
  {
    id: "air-band",
    name: "Aircraft Band",
    loHz: 118_000_000,
    hiHz: 137_000_000,
    scheme: "AM",
    detail: "VHF civil aviation",
  },
  {
    id: "2m",
    name: "2 m Amateur",
    loHz: 144_000_000,
    hiHz: 148_000_000,
    scheme: "NBFM",
    detail: "2-meter ham band",
    region: "Region 2",
  },
  {
    id: "murs",
    name: "MURS",
    loHz: 151_820_000,
    hiHz: 154_600_000,
    scheme: "NBFM",
    detail: "Multi-Use Radio Service (US)",
    region: "US",
  },
  {
    id: "vhf-marine",
    name: "VHF Marine",
    loHz: 156_000_000,
    hiHz: 162_025_000,
    scheme: "NBFM",
    detail: "Marine VHF channels",
  },
  {
    id: "noaa-nwr",
    name: "NOAA Weather Radio",
    loHz: 162_400_000,
    hiHz: 162_550_000,
    scheme: "NBFM",
    detail: "NWR VHF weather",
    region: "US",
  },
  {
    id: "1-25m",
    name: "1.25 m Amateur",
    loHz: 222_000_000,
    hiHz: 225_000_000,
    scheme: "NBFM",
    region: "US",
  },
  // UHF
  {
    id: "70cm",
    name: "70 cm Amateur",
    loHz: 420_000_000,
    hiHz: 450_000_000,
    scheme: "NBFM",
    detail: "70-centimeter ham band",
  },
  {
    id: "gmrs-frs",
    name: "GMRS / FRS",
    loHz: 462_550_000,
    hiHz: 467_725_000,
    scheme: "NBFM",
    detail: "GMRS and FRS shared / repeater",
    region: "US",
  },
  {
    id: "70cm-ism",
    name: "ISM / Part 15",
    loHz: 902_000_000,
    hiHz: 928_000_000,
    scheme: "NBFM",
    detail: "900 MHz ISM",
    region: "US",
  },
];

export function lookupBandPlan(
  frequencyHz: number,
  limit = 5,
): BandPlanEntry[] {
  if (!Number.isFinite(frequencyHz) || frequencyHz <= 0) return [];

  const hits = BAND_PLAN.filter(
    (e) => frequencyHz >= e.loHz && frequencyHz <= e.hiHz,
  );

  // Prefer narrower (more specific) allocations.
  hits.sort((a, b) => {
    const sa = a.hiHz - a.loHz;
    const sb = b.hiHz - b.loHz;
    if (sa !== sb) return sa - sb;
    return a.name.localeCompare(b.name);
  });

  return hits.slice(0, limit);
}
