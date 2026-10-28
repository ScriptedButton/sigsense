/**
 * Offline FT8 encode → decode smoke test + keyword enrichment.
 * Run: pnpm exec tsx scripts/ft8-smoke.ts
 */
import { decodeFT8, encodeFT8, HashCallBook } from "@e04/ft8ts";

import {
  findFt8Keywords,
  uniqueFt8Keywords,
} from "../src/lib/sdr/ft8/keywords";
import { extractFt8Symbols } from "../src/lib/sdr/ft8/symbols";
import { FT8_DECODE_RATE } from "../src/lib/sdr/ft8/types";

const MSG = "CQ K1ABC FN42";
const BASE = 1500;
const SR = FT8_DECODE_RATE;

const audio = encodeFT8(MSG, { sampleRate: SR, baseFrequency: BASE });
const book = new HashCallBook();
const direct = decodeFT8(audio, {
  sampleRate: SR,
  depth: 2,
  hashCallBook: book,
});

console.log("direct encode→decode:");
for (const d of direct) {
  console.log(`  ${d.msg}  SNR ${d.snr.toFixed(1)}  ${d.freq.toFixed(0)} Hz`);
}

const symbols = extractFt8Symbols(MSG, BASE);
const rebuilt = new Float32Array(Math.floor(15 * SR));
let phase = 0;
const spSym = Math.floor(SR * 0.16);
for (let i = 0; i < Math.floor(12.64 * SR); i++) {
  const sym = symbols[Math.min(Math.floor(i / spSym), symbols.length - 1)]!;
  const f = BASE + sym * 6.25;
  phase += (2 * Math.PI * f) / SR;
  rebuilt[i] = 0.5 * Math.sin(phase);
}

const fromSymbols = decodeFT8(rebuilt, {
  sampleRate: SR,
  depth: 2,
  hashCallBook: new HashCallBook(),
});

console.log("symbol-rebuild→decode:");
for (const d of fromSymbols) {
  console.log(`  ${d.msg}  SNR ${d.snr.toFixed(1)}  ${d.freq.toFixed(0)} Hz`);
}

const ok =
  direct.some((d) => d.msg.includes("K1ABC")) &&
  fromSymbols.some((d) => d.msg.includes("K1ABC"));

if (!ok) {
  console.error("FAIL: expected CQ K1ABC in both paths");
  process.exit(1);
}

const sampleMsgs = [
  "CQ K1ABC FN42",
  "K1ABC W1AW -12",
  "W1AW K1ABC R-10",
  "K1ABC W1AW RR73",
  "CQ POTA W1AW FN31",
];

for (const msg of sampleMsgs) {
  const kinds = Object.fromEntries(
    findFt8Keywords(msg).map((h) => [h.token, h.kind]),
  );
  console.log(`keywords ${JSON.stringify(msg)}:`, kinds);
}

const cqHits = findFt8Keywords("CQ K1ABC FN42");
for (const [token, kind] of [
  ["CQ", "procedure"],
  ["K1ABC", "callsign"],
  ["FN42", "grid"],
] as const) {
  const hit = cqHits.find((h) => h.token === token);
  if (!hit || hit.kind !== kind) {
    console.error(`FAIL: expected ${token} as ${kind}`);
    process.exit(1);
  }
}

const report = findFt8Keywords("K1ABC W1AW R+05").find(
  (h) => h.token === "R+05",
);
if (!report || report.kind !== "report") {
  console.error("FAIL: expected R+05 as report");
  process.exit(1);
}

const portable = findFt8Keywords("CQ W1AW/QRP FN31").find(
  (h) => h.kind === "callsign",
);
if (!portable || portable.token !== "W1AW") {
  console.error("FAIL: expected portable W1AW/QRP → callsign W1AW");
  process.exit(1);
}

const unique = uniqueFt8Keywords([
  { msg: "CQ K1ABC FN42" },
  { msg: "CQ K1ABC FN42" },
  { msg: "K1ABC W1AW RR73" },
]);
if (unique.filter((h) => h.token === "CQ").length !== 1) {
  console.error("FAIL: unique keywords should dedupe CQ");
  process.exit(1);
}
if (!unique.some((h) => h.token === "W1AW" && h.kind === "callsign")) {
  console.error("FAIL: expected W1AW in unique keywords");
  process.exit(1);
}

console.log("OK");
