/**
 * Smoke: frequency lookup composition.
 * Run: pnpm exec tsx scripts/freq-lookup-smoke.ts
 */

import { lookupBandPlan } from "../src/lib/sdr/freq-lookup/band-plan";
import { lookupFrequency } from "../src/lib/sdr/freq-lookup/lookup";
import { GMRS_CALLING_CHANNEL } from "../src/lib/sdr/gmrs/channels";
import type { ShortwaveEntry } from "../src/lib/sdr/shortwave/types";

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(msg);
}

const fm = lookupBandPlan(93_900_000);
assert(fm.some((e) => e.id === "fm-bcast"), "FM broadcast plan");

const air = lookupBandPlan(118_500_000);
assert(air.some((e) => e.id === "air-band"), "air band plan");

const gmrsHits = lookupFrequency(GMRS_CALLING_CHANNEL.frequencyHz, {
  scheme: "NBFM",
});
assert(
  gmrsHits.some((h) => h.source === "gmrs" && /20/.test(h.name)),
  "GMRS channel 20",
);
assert(
  gmrsHits.some((h) => h.source === "band-plan"),
  "GMRS band plan hit",
);

const wx = lookupFrequency(162_550_000, { scheme: "NBFM" });
assert(
  wx.some((h) => /weather|noaa/i.test(h.name)),
  "NOAA / weather identity",
);

const sample: ShortwaveEntry = {
  f: 9420,
  a: 600,
  b: 800,
  d: "",
  i: "G",
  n: "BBC",
  l: "E",
  t: "Eu",
  s: "wo",
};

const sw = lookupFrequency(9_420_000, {
  scheme: "AM",
  shortwaveEntries: [sample],
  now: new Date("2026-08-05T10:30:00Z"), // 10:30 UTC = minute 630 — in window
});
assert(
  sw.some((h) => h.source === "shortwave" && /bbc/i.test(h.name)),
  "shortwave schedule hit",
);

const empty = lookupFrequency(0);
assert(empty.length === 0, "invalid freq empty");

console.log("freq-lookup-smoke: ok");
