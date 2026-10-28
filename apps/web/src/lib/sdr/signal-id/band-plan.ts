import { BUILTIN_CATALOG } from "./catalog/builtin";
import type { CatalogEntry } from "./catalog/types";
import type { SignalIdentity } from "./types";

/**
 * Narrowband FM channels where the frequency alone is strong evidence of what
 * a signal is. Used when the spectral model is unsure — typically because the
 * receiver's bins are too coarse to resolve a 12.5 kHz emission.
 */

const GMRS_ID = "frs-gmrs";
const GMRS_BAND = { lo: 462.55e6, hi: 467.725e6 };
/** Anything wider is not an FRS/GMRS voice channel. */
const GMRS_MAX_BW_HZ = 30e3;

/** FRS/GMRS channel centres: 1-7, 8-14, 15-22 and the 467.55 MHz repeater inputs. */
const GMRS_CHANNELS: { name: string; hz: number }[] = [
  ...Array.from({ length: 7 }, (_, i) => ({
    name: `CH ${i + 1}`,
    hz: 462.5625e6 + i * 25e3,
  })),
  ...Array.from({ length: 7 }, (_, i) => ({
    name: `CH ${i + 8}`,
    hz: 467.5625e6 + i * 25e3,
  })),
  ...Array.from({ length: 8 }, (_, i) => ({
    name: `CH ${i + 15}`,
    hz: 462.55e6 + i * 25e3,
  })),
  ...Array.from({ length: 8 }, (_, i) => ({
    name: `repeater input ${i + 1}`,
    hz: 467.55e6 + i * 25e3,
  })),
];

export type BandPlanMatch = {
  identity: SignalIdentity;
  /** 0..1 — how much the frequency alone supports the identification. */
  score: number;
  /** Human-readable reason, e.g. "FRS/GMRS CH 3 (462.6125 MHz)". */
  note: string;
};

type Query = { frequencyHz: number; bandwidthHz: number; binHz: number };

export function bandPlanMatch({
  frequencyHz,
  bandwidthHz,
  binHz,
}: Query): BandPlanMatch | null {
  if (frequencyHz < GMRS_BAND.lo - binHz || frequencyHz > GMRS_BAND.hi + binHz) {
    return null;
  }
  // A coarse bin inflates measured width to at least one bin.
  if (bandwidthHz > Math.max(GMRS_MAX_BW_HZ, 2 * binHz)) return null;

  let nearest = GMRS_CHANNELS[0];
  for (const channel of GMRS_CHANNELS) {
    if (Math.abs(channel.hz - frequencyHz) < Math.abs(nearest.hz - frequencyHz)) {
      nearest = channel;
    }
  }
  // Channels sit 25 kHz apart; allow a bin of slop before calling it off-channel.
  const tolerance = Math.max(7.5e3, binHz * 0.75);
  const onChannel = Math.abs(nearest.hz - frequencyHz) <= tolerance;

  const entry = BUILTIN_CATALOG.signals.find((s) => s.id === GMRS_ID);
  if (!entry) return null;

  return {
    identity: toIdentity(entry, onChannel ? 0.9 : 0.7),
    score: onChannel ? 0.9 : 0.7,
    note: onChannel
      ? `FRS/GMRS ${nearest.name} (${(nearest.hz / 1e6).toFixed(4)} MHz)`
      : "inside the FRS/GMRS band",
  };
}

function toIdentity(entry: CatalogEntry, score: number): SignalIdentity {
  return {
    id: entry.id,
    origin: entry.origin ?? "curated",
    name: entry.name,
    url: entry.url,
    score,
    description: entry.description,
    categories: entry.categories,
    mode: entry.mode,
    modulation: entry.modulation,
  };
}
