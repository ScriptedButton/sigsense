import assert from "node:assert/strict";
import test from "node:test";

import { DemodAM } from "@jtarrio/signals/demod/demod-am.js";
import { HfAmDemod } from "./hf-am-demod";

const INPUT_RATE = 1_024_000;
const OUTPUT_RATE = 48_000;
const BLOCK = 16_384;

function iqBlock(start: number, wanted = true, adjacent = false, inputRate = INPUT_RATE, scale = 0.25, adjacentHz = 9_000) {
  const I = new Float32Array(BLOCK);
  const Q = new Float32Array(BLOCK);
  for (let index = 0; index < BLOCK; index += 1) {
    const t = (start + index) / inputRate;
    let real = 0;
    let imag = 0;
    if (wanted) {
      const envelope = 1 + 0.55 * Math.sin(2 * Math.PI * 1_000 * t);
      real += envelope;
    }
    if (adjacent) {
      const envelope = 1 + 0.9 * Math.sin(2 * Math.PI * 2_100 * t);
      const phase = 2 * Math.PI * adjacentHz * t;
      real += envelope * Math.cos(phase);
      imag += envelope * Math.sin(phase);
    }
    I[index] = real * scale;
    Q[index] = imag * scale;
  }
  return [I, Q] as const;
}

function render(wanted: boolean, adjacent: boolean, inputRate = INPUT_RATE, scale = 0.25, adjacentHz = 9_000) {
  const demod = new HfAmDemod(inputRate, OUTPUT_RATE, {
    scheme: "AM",
    bandwidth: 6_000,
    squelch: 0,
  });
  const output: number[] = [];
  for (let start = 0; start < inputRate / 2; start += BLOCK) {
    const [I, Q] = iqBlock(start, wanted, adjacent, inputRate, scale, adjacentHz);
    output.push(...demod.demodulate(I, Q, 0).left);
  }
  return output.slice(Math.round(OUTPUT_RATE * 0.15));
}

function toneMagnitude(samples: readonly number[], frequency: number) {
  let real = 0;
  let imag = 0;
  for (let index = 0; index < samples.length; index += 1) {
    const phase = 2 * Math.PI * frequency * index / OUTPUT_RATE;
    real += samples[index] * Math.cos(phase);
    imag -= samples[index] * Math.sin(phase);
  }
  return Math.hypot(real, imag) / samples.length;
}

test("HF AM starts bounded and preserves streaming state", () => {
  const demod = new HfAmDemod(INPUT_RATE, OUTPUT_RATE, {
    scheme: "AM", bandwidth: 6_000, squelch: 0,
  });
  const [I, Q] = iqBlock(0, true, false);
  const audio = demod.demodulate(I, Q, 0).left;
  assert.ok(audio.every(Number.isFinite));
  assert.ok(Math.max(...audio.map(Math.abs)) <= 1);
  assert.ok(Math.abs(audio[0]) < 0.01, `startup sample ${audio[0]}`);
});

test("HF AM recovers usable speech within 100 ms and improves stock startup", () => {
  const mode = { scheme: "AM" as const, bandwidth: 6_000, squelch: 0 };
  const ours = new HfAmDemod(INPUT_RATE, OUTPUT_RATE, mode);
  const stock = new DemodAM(INPUT_RATE, OUTPUT_RATE, mode);
  const oursAudio: number[] = [];
  const stockAudio: number[] = [];
  for (let start = 0; start < INPUT_RATE * 0.2; start += BLOCK) {
    const [oursI, oursQ] = iqBlock(start, true, false);
    const [stockI, stockQ] = iqBlock(start, true, false);
    oursAudio.push(...ours.demodulate(oursI, oursQ, 0).left);
    stockAudio.push(...stock.demodulate(stockI, stockQ, 0).left);
  }
  const after100ms = oursAudio.slice(Math.round(OUTPUT_RATE * 0.1));
  const wanted = toneMagnitude(after100ms, 1_000);
  const stockStartupPeak = Math.max(...stockAudio.slice(0, 4_800).map(Math.abs));
  const oursStartupPeak = Math.max(...oursAudio.slice(0, 4_800).map(Math.abs));
  console.log(`100ms wanted magnitude: ${wanted.toFixed(3)}; startup peak ours ${oursStartupPeak.toFixed(2)}, stock ${stockStartupPeak.toFixed(2)}`);
  assert.ok(wanted > 0.05, `wanted tone after 100ms only ${wanted.toFixed(3)}`);
  assert.ok(Math.max(...after100ms.map(Math.abs)) > 0.15, "audio has useful headroom after 100ms");
  assert.ok(oursStartupPeak <= 1 && oursStartupPeak < stockStartupPeak);
});

test("HF AM rejects a full-strength adjacent 9 kHz channel", () => {
  const mixed = render(true, true);
  const wanted = toneMagnitude(mixed, 1_000);
  const adjacent = toneMagnitude(mixed, 2_100);
  const rejectionDb = 20 * Math.log10(wanted / adjacent);
  console.log(`adjacent-channel rejection: ${rejectionDb.toFixed(1)} dB`);
  assert.ok(rejectionDb >= 24, `adjacent rejection ${rejectionDb.toFixed(1)} dB`);
});

test("HF AM anti-alias filter rejects a carrier that would fold at 2.4 MS/s", () => {
  const mixed = render(true, true, 2_400_000, 0.25, 48_000 + 2_100);
  const rejectionDb = 20 * Math.log10(
    toneMagnitude(mixed, 1_000) / toneMagnitude(mixed, 2_100),
  );
  console.log(`2.4 MS/s folded-alias rejection: ${rejectionDb.toFixed(1)} dB`);
  assert.ok(rejectionDb >= 30, `2.4 MS/s alias rejection ${rejectionDb.toFixed(1)} dB`);
});

test("carrier normalization keeps weak and strong AM speech at similar level", () => {
  const weak = toneMagnitude(render(true, false, INPUT_RATE, 0.03), 1_000);
  const strong = toneMagnitude(render(true, false, INPUT_RATE, 0.6), 1_000);
  const deltaDb = Math.abs(20 * Math.log10(strong / weak));
  console.log(`weak/strong normalised level delta: ${deltaDb.toFixed(1)} dB`);
  assert.ok(deltaDb < 3, `normalised level delta ${deltaDb.toFixed(1)} dB`);
});
