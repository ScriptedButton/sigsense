/**
 * Compact EiBi broadcast entry.
 *
 * Keys are short to keep the bundled schedule small (~0.8 MB).
 */
export type ShortwaveEntry = {
  /** Frequency in kHz. */
  f: number;
  /** Start minute of day UTC (0…1440). */
  a: number;
  /** End minute of day UTC (0…1440); may be ≤ start when wrapping midnight. */
  b: number;
  /** Days code (empty = daily). See EiBi README. */
  d: string;
  /** ITU country of the broadcaster. */
  i: string;
  /** Station / programme name. */
  n: string;
  /** Language code. */
  l: string;
  /** Target-area code. */
  t: string;
  /** Transmitter-site code. */
  s: string;
};

export type ShortwaveSchedule = {
  season: string;
  source: string;
  url: string;
  fetchedAt: string;
  entries: ShortwaveEntry[];
};

export type ShortwaveBandId =
  | "120m"
  | "90m"
  | "75m"
  | "60m"
  | "49m"
  | "41m"
  | "31m"
  | "25m"
  | "22m"
  | "19m"
  | "16m"
  | "15m"
  | "13m"
  | "11m";

export type ShortwaveBand = {
  id: ShortwaveBandId;
  label: string;
  /** Inclusive kHz range. */
  loKHz: number;
  hiKHz: number;
};

export type ShortwaveWhen = "now" | "soon" | "all";

export type ShortwaveFilters = {
  when: ShortwaveWhen;
  /** Empty = any band. */
  bandId: ShortwaveBandId | null;
  /** Empty = any language. */
  language: string | null;
  query: string;
};

export type ShortwaveLive = {
  entry: ShortwaveEntry;
  frequencyHz: number;
  onAir: boolean;
  /** Minutes until start (0 if on air). */
  startsInMin: number;
  /** Minutes until end (null if not on air). */
  endsInMin: number | null;
};
