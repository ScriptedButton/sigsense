import { GMRS_SIMPLEX_CHANNELS } from "@/lib/sdr/gmrs/channels";
import type { ScanBank, ScanChannel } from "@/lib/sdr/scanner/types";

function ch(
  id: string,
  label: string,
  frequencyHz: number,
  scheme: ScanChannel["scheme"],
  bandwidthHz?: number,
): ScanChannel {
  return {
    id,
    label,
    frequencyHz,
    scheme,
    bandwidthHz,
    lockedOut: false,
  };
}

/** NOAA Weather Radio (NWR) VHF channels — NBFM. */
const NOAA_WX: ScanChannel[] = [
  ch("wx-1", "WX 1", 162_550_000, "NBFM"),
  ch("wx-2", "WX 2", 162_400_000, "NBFM"),
  ch("wx-3", "WX 3", 162_475_000, "NBFM"),
  ch("wx-4", "WX 4", 162_425_000, "NBFM"),
  ch("wx-5", "WX 5", 162_450_000, "NBFM"),
  ch("wx-6", "WX 6", 162_500_000, "NBFM"),
  ch("wx-7", "WX 7", 162_525_000, "NBFM"),
];

/** Common VHF / UHF monitoring starters. */
const VHF_UHF: ScanChannel[] = [
  ch("air-118", "Air 118.0", 118_000_000, "AM"),
  ch("air-1215", "Air 121.5", 121_500_000, "AM"),
  ch("2m-call", "2m 146.52", 146_520_000, "NBFM"),
  ch("murs-1", "MURS 1", 151_820_000, "NBFM"),
  ch("murs-2", "MURS 2", 151_880_000, "NBFM"),
  ch("murs-3", "MURS 3", 151_940_000, "NBFM"),
  ch("murs-4", "MURS 4", 154_570_000, "NBFM"),
  ch("murs-5", "MURS 5", 154_600_000, "NBFM"),
  ch("70cm-call", "70cm 446.0", 446_000_000, "NBFM"),
];

/** A few FM broadcast samples for demo / wideband scan. */
const FM_BROADCAST: ScanChannel[] = [
  ch("fm-885", "88.5", 88_500_000, "WBFM"),
  ch("fm-939", "93.9", 93_900_000, "WBFM"),
  ch("fm-1011", "101.1", 101_100_000, "WBFM"),
  ch("fm-1045", "104.5", 104_500_000, "WBFM"),
  ch("fm-1079", "107.9", 107_900_000, "WBFM"),
];

function gmrsBank(): ScanBank {
  return {
    id: "gmrs",
    name: "GMRS 1–22",
    builtin: true,
    channels: GMRS_SIMPLEX_CHANNELS.map((c) =>
      ch(`scan-${c.id}`, `GMRS ${c.channel}`, c.frequencyHz, "NBFM"),
    ),
  };
}

/** Fresh builtin + empty custom bank. */
export function createDefaultBanks(): ScanBank[] {
  return [
    gmrsBank(),
    { id: "wx", name: "NOAA Weather", builtin: true, channels: [...NOAA_WX] },
    { id: "vhf-uhf", name: "VHF / UHF", builtin: true, channels: [...VHF_UHF] },
    {
      id: "fm",
      name: "FM Broadcast",
      builtin: true,
      channels: [...FM_BROADCAST],
    },
    { id: "custom", name: "Custom", builtin: false, channels: [] },
  ];
}

export function newChannelId(): string {
  return `ch-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

export function newBankId(): string {
  return `bank-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

/** Channels that participate in a scan (not locked out). */
export function scanableChannels(bank: ScanBank): ScanChannel[] {
  return bank.channels.filter((c) => !c.lockedOut);
}

export function findBank(
  banks: readonly ScanBank[],
  id: string,
): ScanBank | undefined {
  return banks.find((b) => b.id === id);
}

export function findChannel(
  bank: ScanBank,
  id: string,
): ScanChannel | undefined {
  return bank.channels.find((c) => c.id === id);
}
