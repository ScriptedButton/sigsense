import assert from "node:assert/strict";
import { test } from "node:test";
import { findReceivedCarriers } from "./received-signals";

test("received candidates use absolute frequency, rank local contrast, and exclude DC", () => {
  const spectrum = new Float32Array(1024).fill(-90);
  spectrum[0] = -10;
  spectrum[100] = -60;
  spectrum[924] = -70;
  const copy = spectrum.slice();
  const found = findReceivedCarriers(spectrum, 6_000_000, 102_400);
  assert.deepEqual(found.map((c) => c.frequencyHz), [6_010_000, 5_990_000]);
  assert.equal(found[0].aboveFloorDb, 30);
  assert.deepEqual(spectrum, copy);
});

test("flat noise, invalid data and unresolved wide captures do not invent stations", () => {
  assert.deepEqual(findReceivedCarriers(new Float32Array(1024).fill(-90), 6_000_000, 102_400), []);
  assert.deepEqual(findReceivedCarriers(new Float32Array(1024).fill(NaN), 6_000_000, 102_400), []);
  assert.deepEqual(findReceivedCarriers(new Float32Array(1024), 6_000_000, 10_000_000), []);
});
