import type { DisplayClass } from "../types";

/**
 * Where a catalog entry came from.
 * - "artemis": imported from Artemis-DB (sigidwiki.com content, GPL-3).
 * - "curated": hand-written entry in builtin.ts, kept where Artemis has none.
 */
export type CatalogOrigin = "artemis" | "curated";

/** One known emitter / protocol from the SigID-derived catalog. */
export type CatalogEntry = {
  id: string;
  origin?: CatalogOrigin;
  name: string;
  description?: string;
  /** Canonical SigID wiki page. */
  url: string;
  location?: string;
  categories: string[];
  /** Free-form Mode string from the wiki (NFM, USB, …). */
  mode?: string;
  /** Free-form Modulation string from the wiki (FSK, PSK, …). */
  modulation?: string;
  /** Our DisplayClass values this entry is compatible with. */
  classes: DisplayClass[];
  /** Inclusive absolute frequency ranges where this signal appears. */
  frequencies: { lo: number; hi: number }[];
  /** Expected occupied bandwidth, when known. */
  bandwidthHz?: { lo: number; hi: number };
};

export type SignalCatalog = {
  version: number;
  source: string;
  fetchedAt: string;
  count: number;
  /** Upstream project and licence note for imported entries. */
  attribution?: string;
  signals: CatalogEntry[];
};

/** A scored suggestion that a live detection is a known catalog entry. */
export type CatalogMatch = {
  id: string;
  origin: CatalogOrigin;
  name: string;
  url: string;
  score: number;
  description?: string;
  categories: string[];
  mode?: string;
  modulation?: string;
};
