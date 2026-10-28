/**
 * Builds the on-device SigID catalog from the Artemis DB
 * (https://github.com/AresValley/Artemis-DB), a crawl of sigidwiki.com that
 * is complete and reachable, unlike the wiki API which rate-limits.
 *
 * Usage:  pnpm fetch:artemis [--ref main] [--cache <dir>]
 *
 * Reads every static/<id>/signal.json (≈580 records, ≈1 MB; the ≈300 MB of
 * spectrum images and audio are not downloaded here) and writes
 * src/lib/sdr/signal-id/catalog/signals.json. The hand-curated entries in
 * builtin.ts that Artemis does not already cover are kept.
 *
 * Artemis and the wiki content are GPL-3 / CC BY-SA; the catalog's `source`
 * and `attribution` fields keep that visible.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { BUILTIN_CATALOG } from "../../src/lib/sdr/signal-id/catalog/builtin";
import type {
  CatalogEntry,
  SignalCatalog,
} from "../../src/lib/sdr/signal-id/catalog/types";
import type { DisplayClass } from "../../src/lib/sdr/signal-id/types";
import { mapClasses, slugify } from "./catalog-common";

const REPO = "AresValley/Artemis-DB";
const OUT_PATH = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../src/lib/sdr/signal-id/catalog/signals.json",
);
const VERSION = 2;
const CONCURRENCY = 12;
/** Two listed values are read as a range only when they span at most this ratio. */
const MAX_RANGE_RATIO = 20;

type ArtemisValue<T> = { value: T; description?: string };
type ArtemisSignal = {
  pageid: string;
  title: string;
  category?: string[];
  frequency?: ArtemisValue<number>[];
  bandwidth?: ArtemisValue<number>[];
  modulation?: ArtemisValue<string>[];
  mode?: ArtemisValue<string>[];
  location?: ArtemisValue<string>[];
  "short description"?: string;
  description?: string;
};

const args = process.argv.slice(2);
const flag = (name: string): string | undefined => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
};
const REF = flag("--ref") ?? "main";
const CACHE = flag("--cache");

/** Modulations that describe the signal itself, as opposed to how to receive it. */
const DIGITAL_MODULATION =
  /fsk|psk|qam|ofdm|msk|ask|ook|fmcw|pulse|ppm|dsss|fhss|ifk|pcm|pam|pwm|cdma|tdma|lora|chirp/i;

async function getJson<T>(url: string): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try {
      const res = await fetch(url, {
        headers: { "User-Agent": "SigSenseCatalogImport/1.0", Accept: "application/json" },
      });
      if (!res.ok) throw new Error(`${res.status} ${url}`);
      return (await res.json()) as T;
    } catch (err) {
      if (attempt >= 3) throw err;
      await new Promise((r) => setTimeout(r, 1000 * 2 ** attempt));
    }
  }
}

async function listSignalIds(): Promise<string[]> {
  const tree = await getJson<{
    truncated?: boolean;
    tree: { path: string; type: string }[];
  }>(`https://api.github.com/repos/${REPO}/git/trees/${REF}?recursive=1`);
  if (tree.truncated) throw new Error("GitHub tree listing was truncated");
  const ids = new Set<string>();
  for (const e of tree.tree) {
    const m = e.path.match(/^static\/([^/]+)\/signal\.json$/);
    if (m) ids.add(m[1]);
  }
  return [...ids].sort((a, b) => Number(a) - Number(b));
}

async function loadSignal(id: string): Promise<ArtemisSignal> {
  const cached = CACHE ? join(CACHE, `${id}.json`) : null;
  if (cached && existsSync(cached)) {
    return JSON.parse(readFileSync(cached, "utf8")) as ArtemisSignal;
  }
  const signal = await getJson<ArtemisSignal>(
    `https://raw.githubusercontent.com/${REPO}/${REF}/static/${id}/signal.json`,
  );
  if (cached) {
    mkdirSync(dirname(cached), { recursive: true });
    writeFileSync(cached, JSON.stringify(signal));
  }
  return signal;
}

async function loadAll(ids: string[]): Promise<ArtemisSignal[]> {
  const out: ArtemisSignal[] = new Array(ids.length);
  let next = 0;
  let done = 0;
  const worker = async () => {
    while (next < ids.length) {
      const i = next++;
      out[i] = await loadSignal(ids[i]);
      if (++done % 100 === 0) console.log(`  fetched ${done}/${ids.length}`);
    }
  };
  await Promise.all(Array.from({ length: CONCURRENCY }, worker));
  return out;
}

/**
 * Artemis stores frequencies as a flat list of Hz values; the wiki's ranges
 * ("3-30 MHz") became consecutive pairs. A lone value is a point frequency.
 * An even, ascending list is read as ranges when each pair is plausible,
 * otherwise every value is a point — a wrong range would claim a whole band.
 */
function frequencyRanges(values: number[]): { lo: number; hi: number }[] {
  const v = values.filter((x) => Number.isFinite(x) && x > 0);
  const point = (hz: number) => {
    const pad = Math.max(hz * 0.0005, 5_000);
    return { lo: hz - pad, hi: hz + pad };
  };
  if (v.length === 0) return [];
  const pairs = v.length % 2 === 0;
  const ascending = v.every((x, i) => i % 2 === 0 || x >= v[i - 1]);
  if (!pairs || !ascending) return v.map(point);

  const out: { lo: number; hi: number }[] = [];
  for (let i = 0; i < v.length; i += 2) {
    const lo = v[i];
    const hi = v[i + 1];
    if (hi / lo <= MAX_RANGE_RATIO) out.push({ lo, hi: Math.max(hi, lo) });
    else out.push(point(lo), point(hi));
  }
  return out;
}

function classesFor(
  mode: string,
  modulation: string,
  bwHi: number | null,
): DisplayClass[] {
  // "Mode" is how the wiki says to receive the signal. USB is listed for most
  // data signals, so it only says something about the signal when the
  // modulation is not clearly digital.
  const receiveOnly = DIGITAL_MODULATION.test(modulation);
  const effectiveMode = receiveOnly && /^(usb|lsb|ssb|raw|iq)/i.test(mode) ? "" : mode;
  return mapClasses(effectiveMode, modulation, bwHi);
}

function toEntry(sig: ArtemisSignal): CatalogEntry | null {
  const ranges = frequencyRanges((sig.frequency ?? []).map((f) => Number(f.value)));
  if (ranges.length === 0) return null;

  const bws = (sig.bandwidth ?? [])
    .map((b) => Number(b.value))
    .filter((x) => Number.isFinite(x) && x > 0);
  const bandwidthHz = bws.length
    ? { lo: Math.min(...bws) * 0.6, hi: Math.max(...bws) * 1.5 }
    : undefined;

  const join = (list?: ArtemisValue<string>[]) =>
    [...new Set((list ?? []).map((x) => String(x.value).trim()).filter(Boolean))].join(" / ");
  const mode = join(sig.mode);
  const modulation = join(sig.modulation);
  const title = sig.title.replace(/\s+/g, " ").trim();
  const text = (sig["short description"] || sig.description || "")
    .replace(/\*\*?/g, "")
    .replace(/\s+/g, " ")
    .trim();

  return {
    id: slugify(title) || `artemis-${sig.pageid}`,
    name: title,
    description: text ? text.slice(0, 280) : undefined,
    url: `https://www.sigidwiki.com/wiki/${encodeURIComponent(title.replace(/ /g, "_"))}`,
    location: join(sig.location) || undefined,
    categories: (sig.category ?? []).slice(0, 8),
    mode: mode || undefined,
    modulation: modulation || undefined,
    classes: classesFor(mode, modulation, bandwidthHz?.hi ?? null),
    frequencies: ranges,
    bandwidthHz,
  };
}

const normName = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "");

/**
 * Names a signal is also known by: the full title, the title without its
 * parenthetical, and the parenthetical itself — so the curated "ACARS" matches
 * Artemis's "Aircraft Communications Addressing and Reporting System (ACARS)".
 */
function aliases(name: string): Set<string> {
  const out = new Set([normName(name)]);
  const paren = name.match(/\(([^)]+)\)/);
  if (paren) {
    out.add(normName(paren[1]));
    out.add(normName(name.replace(/\([^)]*\)/g, "")));
  }
  out.delete("");
  return out;
}

async function main(): Promise<void> {
  console.log(`SigSense — import Artemis DB (${REPO}@${REF})`);
  const ids = await listSignalIds();
  console.log(`  ${ids.length} signal records`);
  const records = await loadAll(ids);

  const entries: CatalogEntry[] = [];
  const used = new Set<string>();
  let skipped = 0;
  for (const rec of records) {
    const entry = toEntry(rec);
    if (entry) entry.origin = "artemis";
    if (!entry) {
      skipped++;
      continue;
    }
    // Wiki titles can collide after slugging; keep both, distinguished by page id.
    if (used.has(entry.id)) entry.id = `${entry.id}-${rec.pageid}`;
    used.add(entry.id);
    entries.push(entry);
  }

  // Keep curated entries Artemis does not cover (they carry hand-tuned classes).
  const known = new Set(entries.flatMap((e) => [...aliases(e.name)]));
  const curated = BUILTIN_CATALOG.signals.filter(
    (e) => ![...aliases(e.name)].some((alias) => known.has(alias)),
  );
  entries.push(...curated.map((e) => ({ ...e, origin: "curated" as const })));
  entries.sort((a, b) => a.name.localeCompare(b.name));

  const catalog: SignalCatalog & { attribution: string } = {
    version: VERSION,
    source: curated.length ? "artemis-db+curated" : "artemis-db",
    fetchedAt: new Date().toISOString(),
    count: entries.length,
    attribution: `Artemis-DB entries: https://github.com/${REPO} (GPL-3, sigidwiki.com content). "curated" entries are hand-written.`,
    signals: entries,
  };
  mkdirSync(dirname(OUT_PATH), { recursive: true });
  writeFileSync(OUT_PATH, JSON.stringify(catalog), "utf8");

  const byClass = new Map<string, number>();
  for (const e of entries) for (const c of e.classes) byClass.set(c, (byClass.get(c) ?? 0) + 1);
  console.log(
    `\n  kept ${entries.length - curated.length} from Artemis + ${curated.length} curated` +
      `  (skipped ${skipped} without a frequency)`,
  );
  console.log(
    "  classes: " +
      [...byClass].sort((a, b) => b[1] - a[1]).map(([c, n]) => `${c} ${n}`).join(", "),
  );
  console.log(`  wrote ${OUT_PATH}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
