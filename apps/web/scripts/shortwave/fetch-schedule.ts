/**
 * Downloads the EiBi shortwave broadcast schedule and writes a compact JSON
 * catalog for the Shortwave Explorer.
 *
 * Usage:  pnpm fetch:shortwave
 *
 * Source: http://www.eibispace.de/ (free to copy / redistribute)
 */

import { writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import type {
  ShortwaveEntry,
  ShortwaveSchedule,
} from "../../src/lib/sdr/shortwave/types";

const SEASON = "A26";
const CSV_URL = `http://www.eibispace.de/dx/sked-${SEASON.toLowerCase()}.csv`;
const OUT_PATH = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../src/lib/sdr/shortwave/schedule.json",
);

/** HF broadcast window useful for RTL-SDR SWL (excludes VLF / MW utility). */
const FREQ_KHZ_MIN = 2_300;
const FREQ_KHZ_MAX = 26_100;

function parseTimePair(raw: string): { startMin: number; endMin: number } | null {
  const m = /^(\d{4})-(\d{4})$/.exec(raw.trim());
  if (!m) return null;
  const toMin = (hhmm: string) =>
    Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(2));
  return { startMin: toMin(m[1]!), endMin: toMin(m[2]!) };
}

function parseCsvLine(line: string): string[] {
  return line.split(";");
}

async function main() {
  process.stdout.write(`Fetching ${CSV_URL}…\n`);
  const res = await fetch(CSV_URL, {
    headers: { "User-Agent": "SDRSenseShortwaveFetcher/1.0" },
  });
  if (!res.ok) throw new Error(`EiBi fetch ${res.status}`);
  const text = await res.text();
  const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (lines.length < 2) throw new Error("Empty EiBi CSV");

  const entries: ShortwaveEntry[] = [];
  for (const line of lines.slice(1)) {
    const parts = parseCsvLine(line);
    if (parts.length < 9) continue;
    const freq = Number(parts[0]);
    if (!Number.isFinite(freq) || freq < FREQ_KHZ_MIN || freq > FREQ_KHZ_MAX) {
      continue;
    }
    const persist = Number(parts[8] || "0");
    if (!Number.isFinite(persist) || persist >= 90 || persist === 8) continue;

    const times = parseTimePair(parts[1] ?? "");
    if (!times) continue;

    const freqKHz = Number.isInteger(freq) ? freq : Math.round(freq * 100) / 100;
    entries.push({
      f: freqKHz,
      a: times.startMin,
      b: times.endMin,
      d: (parts[2] ?? "").trim(),
      i: (parts[3] ?? "").trim(),
      n: (parts[4] ?? "").trim(),
      l: (parts[5] ?? "").trim(),
      t: (parts[6] ?? "").trim(),
      s: (parts[7] ?? "").trim(),
    });
  }

  const schedule: ShortwaveSchedule = {
    season: SEASON,
    source: "eibi",
    url: "http://www.eibispace.de/",
    fetchedAt: new Date().toISOString().slice(0, 10),
    entries,
  };

  writeFileSync(OUT_PATH, `${JSON.stringify(schedule)}\n`);
  process.stdout.write(
    `Wrote ${entries.length} entries → ${OUT_PATH}\n`,
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
