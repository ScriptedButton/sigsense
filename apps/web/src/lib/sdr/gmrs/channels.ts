/**
 * US GMRS / FRS shared channels (47 CFR 95.1763) plus GMRS repeater inputs.
 */

export type GmrsChannelKind = "shared" | "repeater-in";

export type GmrsChannel = {
  id: string;
  /** Display channel number (1–22 or 15R–22R). */
  channel: string;
  frequencyHz: number;
  kind: GmrsChannelKind;
  /** Short chip label. */
  label: string;
};

/** Channels 1–22 (FRS/GMRS shared simplex / repeater outputs). */
export const GMRS_SIMPLEX_CHANNELS: readonly GmrsChannel[] = [
  { id: "gmrs-1", channel: "1", frequencyHz: 462_562_500, kind: "shared", label: "1" },
  { id: "gmrs-2", channel: "2", frequencyHz: 462_587_500, kind: "shared", label: "2" },
  { id: "gmrs-3", channel: "3", frequencyHz: 462_612_500, kind: "shared", label: "3" },
  { id: "gmrs-4", channel: "4", frequencyHz: 462_637_500, kind: "shared", label: "4" },
  { id: "gmrs-5", channel: "5", frequencyHz: 462_662_500, kind: "shared", label: "5" },
  { id: "gmrs-6", channel: "6", frequencyHz: 462_687_500, kind: "shared", label: "6" },
  { id: "gmrs-7", channel: "7", frequencyHz: 462_712_500, kind: "shared", label: "7" },
  { id: "gmrs-8", channel: "8", frequencyHz: 467_562_500, kind: "shared", label: "8" },
  { id: "gmrs-9", channel: "9", frequencyHz: 467_587_500, kind: "shared", label: "9" },
  { id: "gmrs-10", channel: "10", frequencyHz: 467_612_500, kind: "shared", label: "10" },
  { id: "gmrs-11", channel: "11", frequencyHz: 467_637_500, kind: "shared", label: "11" },
  { id: "gmrs-12", channel: "12", frequencyHz: 467_662_500, kind: "shared", label: "12" },
  { id: "gmrs-13", channel: "13", frequencyHz: 467_687_500, kind: "shared", label: "13" },
  { id: "gmrs-14", channel: "14", frequencyHz: 467_712_500, kind: "shared", label: "14" },
  { id: "gmrs-15", channel: "15", frequencyHz: 462_550_000, kind: "shared", label: "15" },
  { id: "gmrs-16", channel: "16", frequencyHz: 462_575_000, kind: "shared", label: "16" },
  { id: "gmrs-17", channel: "17", frequencyHz: 462_600_000, kind: "shared", label: "17" },
  { id: "gmrs-18", channel: "18", frequencyHz: 462_625_000, kind: "shared", label: "18" },
  { id: "gmrs-19", channel: "19", frequencyHz: 462_650_000, kind: "shared", label: "19" },
  { id: "gmrs-20", channel: "20", frequencyHz: 462_675_000, kind: "shared", label: "20" },
  { id: "gmrs-21", channel: "21", frequencyHz: 462_700_000, kind: "shared", label: "21" },
  { id: "gmrs-22", channel: "22", frequencyHz: 462_725_000, kind: "shared", label: "22" },
];

/** Repeater inputs (+5 MHz from 15–22 outputs). GMRS-only. */
export const GMRS_REPEATER_INPUTS: readonly GmrsChannel[] = [
  { id: "gmrs-15r", channel: "15R", frequencyHz: 467_550_000, kind: "repeater-in", label: "15R" },
  { id: "gmrs-16r", channel: "16R", frequencyHz: 467_575_000, kind: "repeater-in", label: "16R" },
  { id: "gmrs-17r", channel: "17R", frequencyHz: 467_600_000, kind: "repeater-in", label: "17R" },
  { id: "gmrs-18r", channel: "18R", frequencyHz: 467_625_000, kind: "repeater-in", label: "18R" },
  { id: "gmrs-19r", channel: "19R", frequencyHz: 467_650_000, kind: "repeater-in", label: "19R" },
  { id: "gmrs-20r", channel: "20R", frequencyHz: 467_675_000, kind: "repeater-in", label: "20R" },
  { id: "gmrs-21r", channel: "21R", frequencyHz: 467_700_000, kind: "repeater-in", label: "21R" },
  { id: "gmrs-22r", channel: "22R", frequencyHz: 467_725_000, kind: "repeater-in", label: "22R" },
];

export const GMRS_CHANNELS: readonly GmrsChannel[] = [
  ...GMRS_SIMPLEX_CHANNELS,
  ...GMRS_REPEATER_INPUTS,
];

/** Popular calling / travel channel (462.675 MHz). */
export const GMRS_CALLING_CHANNEL = GMRS_SIMPLEX_CHANNELS.find(
  (c) => c.channel === "20",
)!;

export function formatGmrsMhz(hz: number): string {
  return (hz / 1e6).toFixed(4);
}
