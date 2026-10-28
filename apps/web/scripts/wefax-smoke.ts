/**
 * Offline smoke test: synthesize AFSK fax lines → decode → check gradient.
 * Verifies ITU-style APT start (B/W FSK @ 300 Hz) and stop (@ 450 Hz).
 * Run: pnpm exec tsx scripts/wefax-smoke.ts
 */
import { WefaxDecoder } from "../src/lib/sdr/wefax/decoder";
import {
  DEFAULT_WEFAX_CONFIG,
  WEFAX_APT_START_IOC288_HZ,
  WEFAX_APT_START_IOC576_HZ,
  WEFAX_APT_STOP_HZ,
  wefaxLineSeconds,
} from "../src/lib/sdr/wefax/types";

const RATE = 48_000;
const LINE_SEC = wefaxLineSeconds(120);
const SAMPLES_PER_LINE = Math.round(RATE * LINE_SEC);
const LINES = 8;
const BLACK_HZ = 1500;
const WHITE_HZ = 2300;

// --- Manual imaging gradient ---
{
  const pcm = new Float32Array(SAMPLES_PER_LINE * LINES);
  let phase = 0;
  for (let line = 0; line < LINES; line++) {
    for (let s = 0; s < SAMPLES_PER_LINE; s++) {
      const x = s / SAMPLES_PER_LINE;
      const level = x;
      const freq = BLACK_HZ + level * (WHITE_HZ - BLACK_HZ);
      phase += (2 * Math.PI * freq) / RATE;
      if (phase > Math.PI * 2) phase -= Math.PI * 2;
      pcm[line * SAMPLES_PER_LINE + s] = 0.4 * Math.sin(phase);
    }
  }

  const decoder = new WefaxDecoder({
    ...DEFAULT_WEFAX_CONFIG,
    sync: "manual",
    slantPct: 0,
  });
  decoder.forceStart();
  const result = decoder.push(pcm, RATE);

  console.log("imaging status:", result.status);
  console.log("lines:", result.lines);
  console.log("toneHz:", result.toneHz.toFixed(1));
  console.log("lock:", result.lock.toFixed(3));

  if (result.lines < LINES - 1) {
    console.error(`FAIL: expected ~${LINES} lines, got ${result.lines}`);
    process.exit(1);
  }

  const pixels = decoder.getPixels();
  const width = decoder.getWidth();
  const mid = Math.floor(result.lines / 2);
  const left = avg(pixels, mid * width, 40);
  const right = avg(pixels, mid * width + width - 40, 40);

  console.log("left gray:", left.toFixed(1), "right gray:", right.toFixed(1));

  if (left > 80) {
    console.error("FAIL: left side should be dark (near black)");
    process.exit(1);
  }
  if (right < 170) {
    console.error("FAIL: right side should be bright (near white)");
    process.exit(1);
  }
  if (right - left < 80) {
    console.error("FAIL: gradient contrast too low");
    process.exit(1);
  }
}

// --- APT start: IOC 576 via B/W FSK @ 300 Hz ---
{
  const status = pushApt(WEFAX_APT_START_IOC576_HZ, 3.2);
  console.log("apt576 status:", status.status, "ioc:", status.ioc);
  if (status.status !== "phasing" && status.status !== "imaging") {
    console.error("FAIL: APT B/W @ 300 Hz should enter phasing/imaging");
    process.exit(1);
  }
  if (status.ioc !== 576) {
    console.error("FAIL: APT B/W @ 300 Hz should select IOC 576");
    process.exit(1);
  }
}

// --- APT start: IOC 288 via B/W FSK @ 675 Hz ---
{
  const status = pushApt(WEFAX_APT_START_IOC288_HZ, 3.2);
  console.log("apt288 status:", status.status, "ioc:", status.ioc);
  if (status.status !== "phasing" && status.status !== "imaging") {
    console.error("FAIL: APT B/W @ 675 Hz should enter phasing/imaging");
    process.exit(1);
  }
  if (status.ioc !== 288) {
    console.error("FAIL: APT B/W @ 675 Hz should select IOC 288");
    process.exit(1);
  }
}

// --- APT stop after force-start imaging ---
{
  const decoder = new WefaxDecoder({
    ...DEFAULT_WEFAX_CONFIG,
    sync: "auto",
  });
  decoder.forceStart();
  const pcm = synthesizeBwFsk(WEFAX_APT_STOP_HZ, 2.5);
  let status = decoder.getStatus();
  const chunk = 4096;
  for (let i = 0; i < pcm.length; i += chunk) {
    status = decoder.push(pcm.subarray(i, i + chunk), RATE).status;
  }
  console.log("apt stop status:", status);
  if (status !== "stopped" && status !== "listening") {
    console.error("FAIL: APT B/W @ 450 Hz should stop (or re-arm listening)");
    process.exit(1);
  }
}

// --- Pure 300 Hz sine must NOT look like APT (wrong signal model) ---
{
  const secs = 3.2;
  const n = Math.round(RATE * secs);
  const pcm = new Float32Array(n);
  let phase = 0;
  for (let i = 0; i < n; i++) {
    phase += (2 * Math.PI * WEFAX_APT_START_IOC576_HZ) / RATE;
    if (phase > Math.PI * 2) phase -= Math.PI * 2;
    pcm[i] = 0.35 * Math.sin(phase);
  }
  const decoder = new WefaxDecoder({
    ...DEFAULT_WEFAX_CONFIG,
    sync: "auto",
  });
  let status = decoder.getStatus();
  const chunk = 4096;
  for (let i = 0; i < pcm.length; i += chunk) {
    status = decoder.push(pcm.subarray(i, i + chunk), RATE).status;
  }
  console.log("pure-tone status:", status);
  if (status === "phasing" || status === "imaging" || status === "starting") {
    console.error("FAIL: pure 300 Hz sine should not trigger APT");
    process.exit(1);
  }
}

console.log("OK");

function pushApt(
  rateHz: number,
  secs: number,
): { status: string; ioc: number } {
  const decoder = new WefaxDecoder({
    ...DEFAULT_WEFAX_CONFIG,
    sync: "auto",
  });
  const pcm = synthesizeBwFsk(rateHz, secs);
  let status = decoder.getStatus();
  const chunk = 4096;
  for (let i = 0; i < pcm.length; i += chunk) {
    const result = decoder.push(pcm.subarray(i, i + chunk), RATE);
    status = result.status;
  }
  return { status, ioc: decoder.getIoc() };
}

/** USB AFSK: alternate black/white at `rateHz` (ITU APT). */
function synthesizeBwFsk(rateHz: number, secs: number): Float32Array {
  const n = Math.round(RATE * secs);
  const pcm = new Float32Array(n);
  let phase = 0;
  for (let i = 0; i < n; i++) {
    const t = i / RATE;
    const freq = (t * rateHz) % 1 < 0.5 ? BLACK_HZ : WHITE_HZ;
    phase += (2 * Math.PI * freq) / RATE;
    if (phase > Math.PI * 2) phase -= Math.PI * 2;
    pcm[i] = 0.4 * Math.sin(phase);
  }
  return pcm;
}

function avg(buf: Uint8Array, start: number, n: number): number {
  let sum = 0;
  for (let i = 0; i < n; i++) sum += buf[start + i]!;
  return sum / n;
}
