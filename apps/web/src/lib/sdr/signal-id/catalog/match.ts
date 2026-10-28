import { BUILTIN_CATALOG } from "./builtin";
import fetchedJson from "./signals.json";
import type { DisplayClass } from "../types";
import type { CatalogEntry, CatalogMatch, SignalCatalog } from "./types";

const FETCHED = fetchedJson as SignalCatalog;

/** Prefer a freshly fetched wiki dump when present; otherwise the curated seed. */
const CATALOG: SignalCatalog =
  FETCHED.count > BUILTIN_CATALOG.count ? FETCHED : BUILTIN_CATALOG;

export const CATALOG_COUNT = CATALOG.count;

/** Entries in use, for priors that need to scan the whole catalog. */
export function catalogEntries(): readonly CatalogEntry[] {
  return CATALOG.signals;
}
export const CATALOG_FETCHED_AT = CATALOG.fetchedAt;
export const CATALOG_SOURCE = CATALOG.source;

export type MatchQuery = {
  frequencyHz: number;
  bandwidthHz: number;
  label: DisplayClass;
};

const MIN_SCORE = 0.42;
const MAX_RESULTS = 3;

/**
 * Scores how well a live detection matches a known SigID catalog entry.
 *
 * Frequency is required (hard gate). Bandwidth and modulation class refine the
 * rank — a tight band + matching class beats a loose worldwide allocation.
 */
export function matchCatalog(query: MatchQuery, limit = MAX_RESULTS): CatalogMatch[] {
  if (!Number.isFinite(query.frequencyHz) || query.frequencyHz <= 0) return [];

  const scored: CatalogMatch[] = [];

  for (const entry of CATALOG.signals) {
    const freqScore = frequencyScore(entry, query.frequencyHz);
    if (freqScore <= 0) continue;

    const bwScore = bandwidthScore(entry, query.bandwidthHz);
    const classScore = classAgreement(entry, query.label);

    // Prefer entries whose declared classes centre on the observed label.
    const focus =
      entry.classes.length > 0 && entry.classes[0] === query.label
        ? 0.04
        : 0;
    const score = freqScore * 0.55 + classScore * 0.3 + bwScore * 0.15 + focus;
    if (score < MIN_SCORE) continue;

    scored.push({
      id: entry.id,
      name: entry.name,
      url: entry.url,
      score,
      origin: entry.origin ?? "curated",
      description: entry.description,
      categories: entry.categories,
      mode: entry.mode,
      modulation: entry.modulation,
    });
  }

  scored.sort((a, b) => b.score - a.score || a.name.localeCompare(b.name));
  return scored.slice(0, limit);
}

function frequencyScore(entry: CatalogEntry, hz: number): number {
  let best = 0;
  for (const range of entry.frequencies) {
    if (hz < range.lo || hz > range.hi) continue;
    const span = Math.max(range.hi - range.lo, 1);
    // Prefer entries whose allocation is specific rather than "entire HF".
    const specificity = clamp(1 - Math.log10(span / 1e3) / 6, 0.25, 1);
    // Slight boost when sitting near the centre of the allocation.
    const mid = (range.lo + range.hi) / 2;
    const centrality = 1 - Math.min(Math.abs(hz - mid) / (span / 2), 1) * 0.15;
    best = Math.max(best, specificity * centrality);
  }
  return best;
}

function bandwidthScore(entry: CatalogEntry, bwHz: number): number {
  if (!entry.bandwidthHz || !Number.isFinite(bwHz) || bwHz <= 0) return 0.5;
  const { lo, hi } = entry.bandwidthHz;
  if (bwHz >= lo && bwHz <= hi) return 1;
  // Soft falloff outside the expected band.
  if (bwHz < lo) return clamp(bwHz / lo, 0, 0.7);
  return clamp(hi / bwHz, 0, 0.7);
}

function classAgreement(entry: CatalogEntry, label: DisplayClass): number {
  if (entry.classes.length === 0) return 0.4;
  if (label === "NOISE") return 0.1;
  if (entry.classes.includes(label)) return 1;
  // SSB umbrella: USB/LSB catalog entries still match an SSB display label.
  if (label === "SSB" && (entry.classes.includes("USB") || entry.classes.includes("LSB"))) {
    return 0.9;
  }
  // Near-neighbours that the MLP often confuses.
  if (nearClass(label, entry.classes)) return 0.55;
  return 0.15;
}

function nearClass(label: DisplayClass, classes: DisplayClass[]): boolean {
  const neighbours: Partial<Record<DisplayClass, DisplayClass[]>> = {
    CW: ["CARRIER"],
    CARRIER: ["CW"],
    USB: ["LSB"],
    LSB: ["USB"],
    SSB: ["USB", "LSB"],
    FSK: ["NBFM", "DIGITAL"],
    DIGITAL: ["FSK", "NBFM"],
    NBFM: ["FSK", "DIGITAL", "WBFM"],
    WBFM: ["NBFM"],
  };
  const near = neighbours[label] ?? [];
  return near.some((n) => classes.includes(n));
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

/** Empty stub used until the fetch script has produced signals.json. */
export function emptyCatalogMatches(): CatalogMatch[] {
  return [];
}
