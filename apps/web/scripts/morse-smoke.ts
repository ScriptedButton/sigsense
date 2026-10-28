/**
 * Offline smoke test: encode Morse → keyed 600 Hz PCM → decode + keywords.
 * Run: pnpm exec tsx scripts/morse-smoke.ts
 */
import { encodeMorseUnits } from "../src/lib/sdr/morse/alphabet";
import { MorseDecoder } from "../src/lib/sdr/morse/decoder";
import {
  findMorseKeywords,
  uniqueMorseKeywords,
} from "../src/lib/sdr/morse/keywords";
import { DEFAULT_MORSE_CONFIG, ditSeconds } from "../src/lib/sdr/morse/types";

const MESSAGE = "CQ DE W1AW K";
const WPM = 20;
const RATE = 48_000;
const TONE_HZ = 600;
const UNITS = encodeMorseUnits(MESSAGE);
const DIT = ditSeconds(WPM);
const SAMPLES_PER_DIT = Math.round(RATE * DIT);
const TOTAL = UNITS.length * SAMPLES_PER_DIT;

const pcm = new Float32Array(TOTAL);
for (let i = 0; i < TOTAL; i++) {
  const idx = Math.floor(i / SAMPLES_PER_DIT);
  if (!UNITS[idx]) continue;
  pcm[i] = 0.4 * Math.sin((2 * Math.PI * TONE_HZ * i) / RATE);
}

const decoder = new MorseDecoder({
  ...DEFAULT_MORSE_CONFIG,
  wpm: WPM,
  toneHz: TONE_HZ,
  autoSpeed: false,
});
const { chars, lock, wpmEstimate } = decoder.push(pcm, RATE);

const normalized = (s: string) => s.replace(/\s+/g, " ").trim();
const got = normalized(chars);
const want = normalized(MESSAGE);

console.log("want:", JSON.stringify(want));
console.log("got: ", JSON.stringify(got));
console.log("lock:", lock.toFixed(3), "wpm:", wpmEstimate.toFixed(1));

if (!got.includes("CQ") || !got.includes("W1AW")) {
  console.error("FAIL: expected message fragments missing");
  process.exit(1);
}

const hits = findMorseKeywords(got);
const tokens = hits.map((h) => h.token);
console.log("keywords:", tokens.join(", "));

for (const need of ["CQ", "DE", "W1AW", "K"] as const) {
  if (!tokens.includes(need)) {
    console.error(`FAIL: expected keyword ${need}`);
    process.exit(1);
  }
}

const call = hits.find((h) => h.token === "W1AW");
if (!call || call.kind !== "callsign") {
  console.error("FAIL: W1AW should be identified as callsign");
  process.exit(1);
}

const unique = uniqueMorseKeywords("CQ CQ DE W1AW K");
if (unique.filter((h) => h.token === "CQ").length !== 1) {
  console.error("FAIL: unique keywords should dedupe CQ");
  process.exit(1);
}

console.log("OK");
