/**
 * Builds the on-device SigID catalog from sigidwiki.com Signal templates.
 *
 * Usage:  pnpm exec tsx scripts/signal-id/fetch-catalog.ts
 *
 * Fetches every page in Category:Signal, parses the {{Signal}} infobox, and
 * writes a compact JSON catalog the browser matcher can score against.
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { mapClasses, slugify } from "./catalog-common";
import type {
  CatalogEntry,
  SignalCatalog,
} from "../../src/lib/sdr/signal-id/catalog/types";

const API = "https://www.sigidwiki.com/api.php";
const OUT_PATH = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../src/lib/sdr/signal-id/catalog/signals.json",
);
const SOURCE = "sigidwiki";
const VERSION = 1;
const USER_AGENT = "SDRSenseCatalogFetcher/1.0 (local; educational)";

type WikiPage = { title: string; pageid?: number; missing?: string };

async function api(params: Record<string, string>): Promise<unknown> {
  const url = new URL(API);
  url.searchParams.set("format", "json");
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);

  const res = await fetch(url, {
    headers: { "User-Agent": USER_AGENT, Accept: "application/json" },
  });
  if (!res.ok) throw new Error(`SigID API ${res.status}: ${await res.text()}`);
  return res.json();
}

async function listSignalTitles(): Promise<string[]> {
  const titles: string[] = [];
  let continueToken: string | undefined;
  do {
    const params: Record<string, string> = {
      action: "query",
      list: "categorymembers",
      cmtitle: "Category:Signal",
      cmlimit: "500",
      cmtype: "page",
    };
    if (continueToken) params.cmcontinue = continueToken;
    const data = (await api(params)) as {
      query?: { categorymembers?: { title: string }[] };
      continue?: { cmcontinue?: string };
    };
    for (const m of data.query?.categorymembers ?? []) titles.push(m.title);
    continueToken = data.continue?.cmcontinue;
    process.stdout.write(`  listed ${titles.length} pages…\n`);
  } while (continueToken);
  return titles;
}

async function fetchWikitext(titles: string[]): Promise<Map<string, string>> {
  const out = new Map<string, string>();
  const chunkSize = 10;
  for (let i = 0; i < titles.length; i += chunkSize) {
    const chunk = titles.slice(i, i + chunkSize);
    const data = await apiWithRetry({
      action: "query",
      prop: "revisions",
      rvprop: "content",
      rvslots: "main",
      titles: chunk.join("|"),
    });
    const pages = (
      data as {
        query?: {
          pages?: Record<
            string,
            WikiPage & { revisions?: { slots?: { main?: { "*"?: string } } }[] }
          >;
        };
      }
    ).query?.pages;
    for (const page of Object.values(pages ?? {})) {
      if (page.missing != null || !page.title) continue;
      const text = page.revisions?.[0]?.slots?.main?.["*"];
      if (typeof text === "string") out.set(page.title, text);
    }
    process.stdout.write(
      `  fetched ${Math.min(i + chunkSize, titles.length)}/${titles.length}…\n`,
    );
    await sleep(900);
  }
  return out;
}

async function apiWithRetry(
  params: Record<string, string>,
  attempts = 5,
): Promise<unknown> {
  let lastError: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      return await api(params);
    } catch (err) {
      lastError = err;
      const message = err instanceof Error ? err.message : String(err);
      const retryable = /429|503|502|Cloudflare|Just a moment/i.test(message);
      if (!retryable || i === attempts - 1) throw err;
      const wait = 2000 * 2 ** i;
      process.stdout.write(`  rate-limited, waiting ${wait}ms…\n`);
      await sleep(wait);
    }
  }
  throw lastError;
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/** Pulls `|Key=value` pairs from the first {{Signal … }} template. */
function parseSignalTemplate(wikitext: string): Record<string, string> | null {
  const start = wikitext.search(/\{\{\s*Signal\b/i);
  if (start < 0) return null;

  let depth = 0;
  let end = -1;
  for (let i = start; i < wikitext.length - 1; i++) {
    if (wikitext[i] === "{" && wikitext[i + 1] === "{") {
      depth++;
      i++;
      continue;
    }
    if (wikitext[i] === "}" && wikitext[i + 1] === "}") {
      depth--;
      i++;
      if (depth === 0) {
        end = i + 1;
        break;
      }
    }
  }
  if (end < 0) return null;

  const body = wikitext.slice(start, end);
  // Drop the opening {{Signal and closing }}
  const inner = body.replace(/^\{\{\s*Signal\s*/i, "").replace(/\}\}$/, "");
  const fields: Record<string, string> = {};
  for (const part of inner.split(/\n\|/)) {
    const trimmed = part.replace(/^\|/, "").trim();
    const eq = trimmed.indexOf("=");
    if (eq <= 0) continue;
    const key = trimmed.slice(0, eq).trim();
    const value = trimmed
      .slice(eq + 1)
      .replace(/\{\{[^}]*\}\}/g, "")
      .replace(/\[\[([^|\]]*\|)?([^\]]*)\]\]/g, "$2")
      .replace(/'''?/g, "")
      .replace(/<[^>]+>/g, "")
      .replace(/\s+/g, " ")
      .trim();
    if (key && value) fields[key] = value;
  }
  return fields;
}

function parseHzToken(raw: string): number | null {
  const cleaned = raw
    .replace(/,/g, "")
    .replace(/∼|~|≈/g, "")
    .replace(/\+/g, "")
    .trim();
  const m = cleaned.match(
    /^([<>]?\s*)?(-?\d+(?:\.\d+)?)\s*(Hz|kHz|MHz|GHz)?$/i,
  );
  if (!m) return null;
  const value = Number(m[2]);
  if (!Number.isFinite(value)) return null;
  const unit = (m[3] ?? "Hz").toLowerCase();
  if (unit === "ghz") return value * 1e9;
  if (unit === "mhz") return value * 1e6;
  if (unit === "khz") return value * 1e3;
  return value;
}

/**
 * Accepts the wiki's free-form frequency strings and returns inclusive Hz ranges.
 * Examples: "88-108 MHz", "162.400 MHz, 162.550 MHz", "25 MHz, 932 MHz".
 */
function parseFrequencyRanges(raw: string): { lo: number; hi: number }[] {
  if (!raw || /unknown|various|n\/a|none/i.test(raw)) return [];

  const ranges: { lo: number; hi: number }[] = [];
  // Split on commas / semicolons / "and" but keep dash ranges intact.
  const parts = raw.split(/\s*(?:,|;|\band\b)\s*/i);

  for (const part of parts) {
    const rangeMatch = part.match(
      /(-?\d+(?:\.\d+)?)\s*(Hz|kHz|MHz|GHz)?\s*(?:-|–|—|to|~)\s*(-?\d+(?:\.\d+)?)\s*(Hz|kHz|MHz|GHz)?/i,
    );
    if (rangeMatch) {
      const unitHi = rangeMatch[4] ?? rangeMatch[2] ?? "MHz";
      const unitLo = rangeMatch[2] ?? unitHi;
      const lo = parseHzToken(`${rangeMatch[1]} ${unitLo}`);
      const hi = parseHzToken(`${rangeMatch[3]} ${unitHi}`);
      if (lo != null && hi != null && hi >= lo) ranges.push({ lo, hi });
      continue;
    }
    const single = parseHzToken(part);
    if (single != null && single > 0) {
      // Point frequencies get a small tolerance so matching is usable.
      const pad = Math.max(single * 0.0005, 5_000);
      ranges.push({ lo: single - pad, hi: single + pad });
    }
  }

  return ranges;
}

function parseBandwidth(raw: string): { lo: number; hi: number } | null {
  if (!raw || /unknown|various|n\/a|none/i.test(raw)) return null;
  const rangeMatch = raw.match(
    /(-?\d+(?:\.\d+)?)\s*(Hz|kHz|MHz)?\s*(?:-|–|—|to|~)\s*(-?\d+(?:\.\d+)?)\s*(Hz|kHz|MHz)?/i,
  );
  if (rangeMatch) {
    const unitHi = rangeMatch[4] ?? rangeMatch[2] ?? "Hz";
    const unitLo = rangeMatch[2] ?? unitHi;
    const lo = parseHzToken(`${rangeMatch[1]} ${unitLo}`);
    const hi = parseHzToken(`${rangeMatch[3]} ${unitHi}`);
    if (lo != null && hi != null && hi >= lo) return { lo, hi };
  }
  const single = parseHzToken(raw.split(/[,;]/)[0] ?? "");
  if (single == null || single <= 0) return null;
  // Allow ±40% around a stated bandwidth when matching detections.
  return { lo: single * 0.6, hi: single * 1.5 };
}

function wikiUrl(title: string): string {
  return `https://www.sigidwiki.com/wiki/${encodeURIComponent(title.replace(/ /g, "_"))}`;
}

function categoriesFrom(raw: string | undefined): string[] {
  if (!raw) return [];
  return raw
    .split(/[,;]/)
    .map((c) => c.trim())
    .filter(Boolean)
    .slice(0, 8);
}

function toEntry(title: string, fields: Record<string, string>): CatalogEntry | null {
  const ranges = parseFrequencyRanges(fields.Frequencies ?? "");
  if (ranges.length === 0) return null;

  const bw = parseBandwidth(fields.Bandwidth ?? "");
  const mode = fields.Mode ?? "";
  const modulation = fields.Modulation ?? "";
  const classes = mapClasses(mode, modulation, bw?.hi ?? null);
  const name = (fields.Title ?? title).replace(/\s+/g, " ").trim();
  if (!name) return null;

  const description = (fields["Signal description"] ?? "").slice(0, 280);

  return {
    id: slugify(title),
    name,
    description: description || undefined,
    url: wikiUrl(title),
    location: fields.Location || undefined,
    categories: categoriesFrom(fields["Additional categories"]),
    mode: mode || undefined,
    modulation: modulation || undefined,
    classes,
    frequencies: ranges,
    bandwidthHz: bw ?? undefined,
  };
}

async function main(): Promise<void> {
  console.log("SDRSense — fetch SigID wiki catalog");
  const titles = await listSignalTitles();
  console.log(`  ${titles.length} signal pages`);

  const wikitext = await fetchWikitext(titles);
  const signals: CatalogEntry[] = [];
  let skipped = 0;

  for (const [title, text] of wikitext) {
    const fields = parseSignalTemplate(text);
    if (!fields) {
      skipped++;
      continue;
    }
    const entry = toEntry(title, fields);
    if (!entry) {
      skipped++;
      continue;
    }
    signals.push(entry);
  }

  signals.sort((a, b) => a.name.localeCompare(b.name));

  const catalog: SignalCatalog = {
    version: VERSION,
    source: SOURCE,
    fetchedAt: new Date().toISOString(),
    count: signals.length,
    signals,
  };

  mkdirSync(dirname(OUT_PATH), { recursive: true });
  writeFileSync(OUT_PATH, JSON.stringify(catalog), "utf8");

  console.log(`\n  kept ${signals.length}  skipped ${skipped}`);
  console.log(`  wrote ${OUT_PATH}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
