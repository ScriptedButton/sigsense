/**
 * Offline smoke test: encode Baudot → perfect discriminator PCM → decode.
 * Run: pnpm exec tsx scripts/fsk-smoke.ts
 */
import { encodeBaudotBits } from "../src/lib/sdr/fsk/baudot";
import { FskDecoder } from "../src/lib/sdr/fsk/decoder";
import { DEFAULT_FSK_CONFIG } from "../src/lib/sdr/fsk/types";

const MESSAGE = "CQ DE SDRSENSE 123\n";
const BAUD = 45.45;
const RATE = 48_000;
const BITS = encodeBaudotBits(MESSAGE, 1.5);

const samplesPerBit = RATE / BAUD;
const totalSamples = Math.ceil(BITS.length * samplesPerBit);
const pcm = new Float32Array(totalSamples);
for (let i = 0; i < totalSamples; i++) {
  const idx = Math.floor(i / samplesPerBit) % BITS.length;
  pcm[i] = BITS[idx]! ? 0.017 : -0.017;
}

const decoder = new FskDecoder({ ...DEFAULT_FSK_CONFIG, baud: BAUD });
const { chars, lock } = decoder.push(pcm, RATE);

const normalized = (s: string) => s.replace(/\r/g, "").replace(/\n+/g, "\n").trim();
const got = normalized(chars);
const want = normalized(MESSAGE);

console.log("want:", JSON.stringify(want));
console.log("got: ", JSON.stringify(got));
console.log("lock:", lock.toFixed(3));

if (!got.includes("CQ") || !got.includes("SDRSENSE")) {
  console.error("FAIL: expected message fragments missing");
  process.exit(1);
}
console.log("OK");
