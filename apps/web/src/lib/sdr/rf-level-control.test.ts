import assert from "node:assert/strict";
import { test } from "node:test";

import { analyzeUnsignedIq, RfLevelController } from "./rf-level-control";

function iq(value: number, samples = 10_000): Uint8Array {
  return new Uint8Array(samples * 2).fill(value);
}

test("measures exact and near ADC rails", () => {
  const bytes = Uint8Array.from([0, 255, 1, 254, 127, 128, 64, 192]);
  const metrics = analyzeUnsignedIq(bytes);
  assert.equal(metrics.railFraction, 0.25);
  assert.equal(metrics.nearRailFraction, 0.5);
  assert.equal(metrics.peak, 1);
});

test("cuts severe overload immediately and disables tuner AGC", () => {
  const bytes = iq(128);
  bytes.fill(0, 0, 800); // 4% of scalar ADC samples
  const control = new RfLevelController();
  const recommendation = control.observe(bytes, 20, true, 0);
  assert.equal(recommendation?.reason, "overload");
  assert.equal(recommendation?.gainDb, 8);
  assert.equal(recommendation?.disableAutoGain, true);
});

test("does not chase gain upward until a sustained clean interval", () => {
  const control = new RfLevelController({ gainUpIntervalMs: 30_000 });
  const clean = iq(140);
  assert.equal(control.observe(clean, 0, false, 0), null);
  assert.equal(control.observe(clean, 0, false, 29_999), null);
  assert.equal(control.observe(clean, 0, false, 30_000)?.gainDb, 1);
  assert.equal(control.observe(clean, 1, false, 59_999), null);
  assert.equal(control.observe(clean, 1, false, 60_000)?.gainDb, 2);
});

test("holds gain when peaks are high even without exact rail hits", () => {
  const control = new RfLevelController({ gainUpIntervalMs: 1_000 });
  const high = iq(128);
  for (let index = 0; index < high.length; index += 20) high[index] = 240;
  assert.equal(control.observe(high, 0, false, 0), null);
  assert.equal(control.observe(high, 0, false, 2_000), null);
});

test("empty and odd buffers remain finite", () => {
  assert.deepEqual(analyzeUnsignedIq(new Uint8Array()), {
    railFraction: 0,
    nearRailFraction: 0,
    rms: 0,
    peak: 0,
  });
  const metrics = analyzeUnsignedIq(Uint8Array.from([128, 128, 255]));
  assert.ok(Object.values(metrics).every(Number.isFinite));
  assert.equal(metrics.railFraction, 0);
});
