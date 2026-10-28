/** Wideband fragments merge into one detection; separate narrow carriers do not. */
import { detectInFrames } from "../src/lib/sdr/signal-id/pipeline";

function rng(seed: number) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
}

function frames(build: (i: number, r: () => number) => number, bins: number, count: number) {
  const r = rng(7);
  const out: Float32Array[] = [];
  for (let f = 0; f < count; f++) {
    const x = new Float32Array(bins);
    for (let i = 0; i < bins; i++) x[i] = build(i, r);
    out.push(x);
  }
  return out;
}

let failed = 0;
const check = (ok: boolean, msg: string) => {
  console.log(`${ok ? "ok  " : "FAIL"} ${msg}`);
  if (!ok) failed++;
};

// Broadcast-FM-like: 200 kHz hump, deep ripple dips, 500 Hz bins.
const binHz = 500;
const bins = 2048;
const wfm = frames((i, r) => {
  const off = (i - bins / 2) * binHz;
  let v = -90 + (r() - 0.5) * 3;
  if (Math.abs(off) < 100_000) {
    const ripple = 12 * Math.sin(off / 9_000) ** 2; // dips toward the floor
    v = -55 - ripple + (r() - 0.5) * 6;
  }
  return v;
}, bins, 8);
const w = detectInFrames(wfm, binHz, 10);
check(w.detections.length === 1, `broadcast FM is 1 detection (got ${w.detections.length})`);
const width = (w.detections[0].hiBin - w.detections[0].loBin + 1) * binHz;
check(width > 150_000, `merged width ${Math.round(width / 1000)} kHz > 150 kHz`);

// Five 12.5 kHz carriers 25 kHz apart must stay separate.
const carriers = frames((i, r) => {
  const off = (i - bins / 2) * binHz;
  let v = -90 + (r() - 0.5) * 3;
  for (const c of [-100_000, -75_000, -50_000, -25_000, 0]) {
    if (Math.abs(off - c) < 6_000) v = -55 + (r() - 0.5) * 3;
  }
  return v;
}, bins, 8);
const c = detectInFrames(carriers, binHz, 10);
check(c.detections.length === 5, `5 separate carriers stay 5 (got ${c.detections.length})`);

if (failed) process.exit(1);
console.log("wideband smoke passed");
