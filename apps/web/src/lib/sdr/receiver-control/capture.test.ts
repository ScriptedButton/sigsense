import assert from "node:assert/strict";
import { test } from "node:test";
import { captureAudioStream, captureIqStream, encodeWav, type PcmListener, type IqListener } from "./capture";

test("WAV has correct signed PCM, rate, size and invalid-sample containment", () => {
  const bytes = Buffer.from(encodeWav(Float32Array.of(-1, 0, 1, NaN), 12_000), "base64");
  assert.equal(bytes.toString("ascii", 0, 4), "RIFF");
  assert.equal(bytes.readUInt32LE(24), 12_000); assert.equal(bytes.readUInt32LE(40), 8);
  assert.deepEqual([0, 1, 2, 3].map((i) => bytes.readInt16LE(44 + i * 2)), [-32768, 0, 32767, 0]);
});
test("audio copies consecutive pooled PCM and releases its tap at the exact length", async () => {
  const listeners = new Set<PcmListener>();
  const pending = captureAudioStream((listener) => { listeners.add(listener); return () => { listeners.delete(listener); }; }, 50);
  const first = new Float32Array(1_500).fill(.25), second = new Float32Array(1_500).fill(.5);
  for (const listener of listeners) listener(first, 48_000);
  first.fill(0);
  for (const listener of listeners) listener(second, 48_000);
  const result = await pending, bytes = Buffer.from(result.data, "base64");
  assert.equal(result.samples, 2400); assert.equal(listeners.size, 0);
  assert.equal(bytes.readInt16LE(44), 8192); assert.equal(bytes.readInt16LE(44 + 1500 * 2), 16384);
});
test("IQ records native interleaved float32 LE without mutating input", async () => {
  const listeners = new Set<IqListener>();
  const pending = captureIqStream((listener) => { listeners.add(listener); return () => { listeners.delete(listener); }; }, 10, 256);
  const I = new Float32Array(256).fill(.125), Q = new Float32Array(256).fill(-.25);
  for (const listener of listeners) listener(I, Q, 48_000, 6_100_000);
  const result = await pending, bytes = Buffer.from(result.data, "base64");
  assert.equal(bytes.length, 2048); assert.equal(bytes.readFloatLE(0), .125); assert.equal(bytes.readFloatLE(4), -.25);
  assert.equal(result.centerHz, 6_100_000); assert.equal(result.samples, 256); assert.equal(listeners.size, 0);
});
test("retuning aborts IQ capture instead of mixing unrelated channels", async () => {
  const listeners = new Set<IqListener>();
  const pending = captureIqStream((listener) => { listeners.add(listener); return () => { listeners.delete(listener); }; }, 100, 10000);
  const rejected = assert.rejects(pending, /retuned/);
  for (const listener of listeners) listener(new Float32Array(128), new Float32Array(128), 48000, 1_000_000);
  for (const listener of listeners) listener(new Float32Array(128), new Float32Array(128), 48000, 2_000_000);
  await rejected; assert.equal(listeners.size, 0);
});
