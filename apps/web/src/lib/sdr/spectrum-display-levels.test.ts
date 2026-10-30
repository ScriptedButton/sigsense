import assert from "node:assert/strict";
import { test } from "node:test";
import { SpectrumDisplayLevels, SpectrumRowAverage } from "./spectrum-display-levels";

test("auto contrast follows gain and preserves weak signals without chasing strong outliers", () => {
  const spectrum = new Float32Array(2048).fill(-105);
  spectrum[300] = -99; // a weak signal, below all previous fixed display ranges
  spectrum[1000] = -20; // a single strong neighbor must not set the display gain
  const levels = new SpectrumDisplayLevels().update(spectrum);
  assert.ok(levels.minDb < -99);
  assert.ok(levels.maxDb - levels.minDb <= 52);
  const shifted = Float32Array.from(spectrum, (v) => v + 30);
  const gainLevels = new SpectrumDisplayLevels().update(shifted);
  assert.equal(gainLevels.minDb - levels.minDb, 30);
  assert.equal(gainLevels.maxDb - levels.maxDb, 30);
  const index = (spectrum[300] - levels.minDb) / (levels.maxDb - levels.minDb);
  assert.ok(index > .1, "weak signals must have visible contrast rather than clipping to black");
});

test("levels resist small noise fluctuations and recover immediately after receiver changes", () => {
  const levels = new SpectrumDisplayLevels();
  const initial = levels.update(new Float32Array(2048).fill(-100));
  const small = levels.update(new Float32Array(2048).fill(-98));
  assert.ok(small.minDb - initial.minDb < .2);
  const changed = levels.update(new Float32Array(2048).fill(-55));
  assert.equal(changed.minDb, -53);
  assert.ok(Number.isFinite(levels.update(new Float32Array([NaN, -Infinity])).minDb));
});

test("waterfall averaging reduces noise variation while preserving a steady weak carrier", () => {
  let seed = 73;
  const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return (seed + .5) / 2 ** 32; };
  const average = new SpectrumRowAverage();
  let first: Float32Array | undefined;
  for (let f = 0; f < 4; f++) {
    const frame = Float32Array.from({ length: 2048 }, () => -100 + 10 * Math.log10(-Math.log(random())));
    frame[300] = -94;
    if (!first) first = frame;
    average.add(frame);
  }
  const output = average.take();
  const variance = (frame: Float32Array) => {
    const noise = Array.from(frame).filter((_, i) => i !== 300);
    const mean = noise.reduce((a, b) => a + b) / noise.length;
    return noise.reduce((a, b) => a + (b - mean) ** 2, 0) / noise.length;
  };
  assert.ok(variance(output) < variance(first!) * .4);
  assert.ok(Math.abs(output[300] + 94) < .001);
  average.add(new Float32Array([0, -10]));
  average.add(new Float32Array([-10, 0]));
  const pooled = average.take();
  assert.ok(Math.abs(pooled[0] - 10 * Math.log10(.55)) < .001, "average linear power, not dB");
});
