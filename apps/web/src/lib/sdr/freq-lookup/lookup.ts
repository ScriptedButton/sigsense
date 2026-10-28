import { formatGmrsMhz, GMRS_CHANNELS } from "@/lib/sdr/gmrs/channels";
import { lookupBandPlan } from "@/lib/sdr/freq-lookup/band-plan";
import { matchCatalog } from "@/lib/sdr/signal-id/catalog";
import type { DisplayClass } from "@/lib/sdr/signal-id/types";
import { lookupAtFrequency } from "@/lib/sdr/shortwave/schedule";
import type { ShortwaveEntry, ShortwaveLive } from "@/lib/sdr/shortwave/types";
import type { DemodScheme } from "@/lib/sdr/types";

export type FreqLookupSource =
  | "gmrs"
  | "band-plan"
  | "sigid"
  | "shortwave";

export type FreqLookupHit = {
  id: string;
  source: FreqLookupSource;
  name: string;
  detail?: string;
  /** Exact or nominal frequency when known. */
  frequencyHz?: number;
  scheme?: DemodScheme;
  url?: string;
  /** 0…1 relevance. */
  score: number;
  onAir?: boolean;
  meta?: string;
};

export type FreqLookupOptions = {
  scheme?: DemodScheme;
  bandwidthHz?: number;
  /** EiBi entries when HF shortwave lookup is desired. */
  shortwaveEntries?: readonly ShortwaveEntry[];
  now?: Date;
  limit?: number;
};

const GMRS_TOLERANCE_HZ = 2_500;

function schemeToDisplayClass(scheme: DemodScheme | undefined): DisplayClass {
  if (!scheme) return "NBFM";
  return scheme;
}

function gmrsHits(frequencyHz: number): FreqLookupHit[] {
  const hits: FreqLookupHit[] = [];
  for (const ch of GMRS_CHANNELS) {
    const delta = Math.abs(ch.frequencyHz - frequencyHz);
    if (delta > GMRS_TOLERANCE_HZ) continue;
    const kind =
      ch.kind === "repeater-in" ? "repeater input" : "simplex / output";
    hits.push({
      id: `gmrs-${ch.id}`,
      source: "gmrs",
      name: `GMRS ${ch.channel}`,
      detail: `${formatGmrsMhz(ch.frequencyHz)} MHz · ${kind}`,
      frequencyHz: ch.frequencyHz,
      scheme: "NBFM",
      score: 1 - delta / GMRS_TOLERANCE_HZ,
      meta: kind,
    });
  }
  return hits;
}

function bandPlanHits(frequencyHz: number): FreqLookupHit[] {
  return lookupBandPlan(frequencyHz, 4).map((e, i) => {
    const span = Math.max(e.hiHz - e.loHz, 1);
    const specificity = Math.min(1, 1e6 / span + 0.35);
    return {
      id: `band-${e.id}`,
      source: "band-plan" as const,
      name: e.name,
      detail: e.detail,
      scheme: e.scheme,
      score: specificity * (1 - i * 0.05),
      meta: e.region,
    };
  });
}

function sigidHits(
  frequencyHz: number,
  scheme: DemodScheme | undefined,
  bandwidthHz: number | undefined,
): FreqLookupHit[] {
  const matches = matchCatalog(
    {
      frequencyHz,
      bandwidthHz: bandwidthHz ?? 12_500,
      label: schemeToDisplayClass(scheme),
    },
    5,
  );
  return matches.map((m) => ({
    id: `sigid-${m.id}`,
    source: "sigid" as const,
    name: m.name,
    detail: m.description ?? m.modulation ?? m.mode,
    url: m.url,
    score: m.score,
    meta: m.categories.slice(0, 2).join(" · ") || undefined,
  }));
}

function shortwaveHits(
  frequencyHz: number,
  entries: readonly ShortwaveEntry[] | undefined,
  now: Date,
): FreqLookupHit[] {
  if (!entries || entries.length === 0) return [];
  // Only bother in HF broadcast territory.
  if (frequencyHz < 2e6 || frequencyHz > 30e6) return [];

  const live: ShortwaveLive[] = lookupAtFrequency(entries, frequencyHz, now);
  return live.slice(0, 6).map((row) => ({
    id: `sw-${row.entry.f}-${row.entry.n}-${row.entry.a}`,
    source: "shortwave" as const,
    name: row.entry.n || "Shortwave",
    detail: [
      row.entry.l ? `Lang ${row.entry.l}` : null,
      row.entry.t || null,
      row.onAir
        ? `On air · ends in ${row.endsInMin ?? "?"}m`
        : `Starts in ${row.startsInMin}m`,
    ]
      .filter(Boolean)
      .join(" · "),
    frequencyHz: row.frequencyHz,
    scheme: "AM" as const,
    score: row.onAir ? 0.95 : 0.7,
    onAir: row.onAir,
    meta: row.onAir ? "On air" : "Scheduled",
  }));
}

/**
 * Reverse lookup: what services / allocations / schedules match this Hz.
 * Composes GMRS channels, band plan, SigID catalog, and optional EiBi.
 */
export function lookupFrequency(
  frequencyHz: number,
  opts: FreqLookupOptions = {},
): FreqLookupHit[] {
  if (!Number.isFinite(frequencyHz) || frequencyHz <= 0) return [];

  const limit = opts.limit ?? 12;
  const now = opts.now ?? new Date();

  const pooled: FreqLookupHit[] = [
    ...gmrsHits(frequencyHz),
    ...bandPlanHits(frequencyHz),
    ...sigidHits(frequencyHz, opts.scheme, opts.bandwidthHz),
    ...shortwaveHits(frequencyHz, opts.shortwaveEntries, now),
  ];

  // Deduplicate by name+source, keep highest score.
  const best = new Map<string, FreqLookupHit>();
  for (const hit of pooled) {
    const key = `${hit.source}:${hit.name.toLowerCase()}`;
    const prev = best.get(key);
    if (!prev || hit.score > prev.score) best.set(key, hit);
  }

  return [...best.values()]
    .sort((a, b) => b.score - a.score || a.name.localeCompare(b.name))
    .slice(0, limit);
}

export { lookupBandPlan, BAND_PLAN } from "./band-plan";
