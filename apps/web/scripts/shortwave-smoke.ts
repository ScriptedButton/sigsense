/**
 * Smoke: shortwave schedule filters.
 * Run: pnpm exec tsx scripts/shortwave-smoke.ts
 */

import {
  daysMatch,
  eibiWeekday,
  filterSchedule,
  lookupAtFrequency,
  timeInWindow,
  utcMinuteNow,
} from "../src/lib/sdr/shortwave/schedule";
import type { ShortwaveEntry } from "../src/lib/sdr/shortwave/types";
import schedule from "../src/lib/sdr/shortwave/schedule.json";

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(msg);
}

const sample: ShortwaveEntry = {
  f: 9420,
  a: 600,
  b: 800,
  d: "12345",
  i: "G",
  n: "BBC",
  l: "E",
  t: "Eu",
  s: "wo",
};

assert(timeInWindow(700, 600, 800), "mid window");
assert(!timeInWindow(500, 600, 800), "before window");
assert(timeInWindow(100, 1200, 200), "wrap window");
assert(daysMatch("", 3), "daily");
assert(daysMatch("12345", 1), "numeric mon");
assert(!daysMatch("12345", 6), "numeric sat off");
assert(daysMatch("MoWeFr", 3), "token we");

assert(schedule.entries.length > 1000, "schedule loaded");

const now = new Date("2026-08-05T14:30:00Z");
const onAir = filterSchedule(
  schedule.entries as ShortwaveEntry[],
  { when: "now", bandId: null, language: null, query: "" },
  now,
);
assert(onAir.length > 0, "some on air at sample UTC");
assert(onAir.every((x) => x.onAir), "now filter only on-air");

const english31 = filterSchedule(
  schedule.entries as ShortwaveEntry[],
  { when: "all", bandId: "31m", language: "E", query: "" },
  now,
);
assert(english31.every((x) => x.entry.l === "E"), "language filter");
assert(
  english31.every((x) => x.entry.f >= 9400 && x.entry.f <= 9900),
  "31m band",
);

const bbc = filterSchedule(
  schedule.entries as ShortwaveEntry[],
  { when: "all", bandId: null, language: null, query: "bbc" },
  now,
);
assert(bbc.some((x) => /bbc/i.test(x.entry.n)), "search bbc");

const atFreq = lookupAtFrequency(
  schedule.entries as ShortwaveEntry[],
  Math.round(onAir[0].entry.f * 1_000),
  now,
);
assert(atFreq.length > 0, "lookup at on-air frequency");
assert(
  atFreq.every((x) => Math.abs(x.frequencyHz - onAir[0].frequencyHz) <= 1_500),
  "lookup within tolerance",
);
assert(atFreq[0].onAir, "lookup prefers on-air");

const empty = lookupAtFrequency(
  schedule.entries as ShortwaveEntry[],
  88_500_000,
  now,
);
assert(empty.length === 0, "no SW at FM broadcast");

console.log("shortwave-smoke ok", {
  entries: schedule.entries.length,
  onAir: onAir.length,
  english31: english31.length,
  bbc: bbc.length,
  atFreq: atFreq.length,
  utcMin: utcMinuteNow(now),
  weekday: eibiWeekday(now),
  sampleOk: Boolean(sample),
});
