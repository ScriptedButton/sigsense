/**
 * Signal-id on HackRF-style spectra: coarse bins, a narrow GMRS channel,
 * band-plan fallback, and burst hold. Run: pnpm tsx scripts/signal-id-smoke.ts
 */
import { SignalIdEngine } from "../src/lib/sdr/signal-id/engine";
import { toTrainingGrid, TRAIN_BIN_HZ } from "../src/lib/sdr/signal-id/resample";

let failures = 0;
function check(name: string, ok: boolean, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures++;
}

let clock = 1_000_000;
Date.now = () => clock;

const N = 1024;
const RATE = 4e6;
const CENTER = 462.6e6;
const binHz = RATE / N;
let seed = 7;
const rnd = () => (seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296;

/** FFT-order frame (DC at index 0), -80 dB floor, optional carrier at offsetHz. */
function frame(offsetHz: number | null, widthBins = 3, levelDb = -50): Float32Array {
  const shifted = new Float32Array(N);
  for (let i = 0; i < N; i++) shifted[i] = -80 + (rnd() - 0.5) * 3;
  if (offsetHz !== null) {
    const peak = Math.round(offsetHz / binHz) + N / 2;
    for (let k = -(widthBins >> 1); k <= widthBins >> 1; k++) {
      shifted[peak + k] = levelDb - Math.abs(k) * 4 + (rnd() - 0.5) * 3;
    }
  }
  const raw = new Float32Array(N);
  for (let i = 0; i < N; i++) raw[(i + N / 2) % N] = shifted[i];
  return raw;
}

function run(engine: SignalIdEngine, frames: number, offsetHz: number | null) {
  for (let i = 0; i < frames; i++) {
    clock += 50;
    engine.pushSpectrum(frame(offsetHz), CENTER, RATE, CENTER);
  }
}

// --- resampling -------------------------------------------------------------
{
  const shifted = new Float32Array(N).fill(-80);
  shifted.fill(-50, N / 2 + 20, N / 2 + 23);
  const out = toTrainingGrid([shifted], { loBin: N / 2 + 20, hiBin: N / 2 + 22, peakBin: N / 2 + 21, peakDb: -50 }, binHz);
  check("coarse bins re-binned to training grid", out.binHz === TRAIN_BIN_HZ);
  const width = out.detection.hiBin - out.detection.loBin + 1;
  check("3 coarse bins span ~23 training bins", width > 18 && width < 28, `width=${width}`);
  const one = toTrainingGrid([shifted], { loBin: N / 2 + 20, hiBin: N / 2 + 20, peakBin: N / 2 + 20, peakDb: -50 }, binHz);
  check("1 coarse bin flagged lowResolution", one.lowResolution);
}

// --- GMRS channel 3 (462.6125 MHz) → +12.5 kHz from 462.6 MHz --------------
{
  const engine = new SignalIdEngine();
  engine.setEnabled(true);
  run(engine, 40, 12_500);
  const signals = engine.getState().signals;
  check("detects narrow carrier on coarse bins", signals.length >= 1, `n=${signals.length}`);
  const s = signals[0];
  if (s) {
    check("identity is FRS/GMRS", s.identities[0]?.id === "frs-gmrs", s.identities[0]?.name);
    console.log("     label", s.label, s.confidence.toFixed(2), s.basis, s.bandPlanNote ?? "");
    check("frequency near 462.6125 MHz", Math.abs(s.frequencyHz - 462.6125e6) < 8e3, `${(s.frequencyHz / 1e6).toFixed(4)}`);

    // --- burst hold ---------------------------------------------------------
    clock += 100;
    run(engine, 20, null); // transmission ends (1 s of silence)
    check("track held after burst ends", engine.getState().signals.some((x) => x.id === s.id));
    clock += 4000;
    run(engine, 12, null);
    const held = engine.getState().signals.find((x) => x.id === s.id);
    check("track flagged stale and faded", !!held?.stale && held.confidence < s.confidence, held ? `${held.confidence.toFixed(2)} < ${s.confidence.toFixed(2)}` : "missing");
    clock += 9000;
    run(engine, 12, null);
    check("track dropped after timeout", !engine.getState().signals.some((x) => x.id === s.id));
  }
}

// --- retune tolerance -------------------------------------------------------
{
  const engine = new SignalIdEngine();
  engine.setEnabled(true);
  run(engine, 40, 12_500);
  const before = engine.getState().signals.length;
  clock += 50;
  engine.pushSpectrum(frame(12_500), CENTER + 0.4, RATE, CENTER);
  clock += 450;
  engine.pushSpectrum(frame(12_500), CENTER + 0.4, RATE, CENTER);
  check("sub-Hz centre jitter keeps history", engine.getState().signals.length >= before && before > 0);
}

if (failures) {
  console.error(`${failures} failed`);
  process.exit(1);
}
console.log("signal-id smoke passed");
