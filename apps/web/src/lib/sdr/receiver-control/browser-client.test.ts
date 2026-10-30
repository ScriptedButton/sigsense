import assert from "node:assert/strict";
import test from "node:test";

import type { RadioController } from "../radio-controller";
import type { ReceiverAction, ReceiverCommand } from "./protocol";
import { createReceiverCommandExecutor } from "./browser-client";

function command(action: ReceiverAction, args: Record<string, unknown> = {}): ReceiverCommand {
  return { type: "command", id: "test", action, args };
}

function fakeController() {
  const calls: Array<[string, ...unknown[]]> = [];
  const state = { frequencyHz: 1_000_000, offsetHz: 0, sampleRate: 1_024_000, scheme: "AM" };
  const controller = {
    getState: () => state,
    setScheme: (value: unknown) => calls.push(["setScheme", value]),
    setBandwidth: (value: unknown) => calls.push(["setBandwidth", value]),
    setFrequency: async (value: number) => { calls.push(["setFrequency", value]); state.frequencyHz = value; },
    setOffset: (value: number) => { calls.push(["setOffset", value]); state.offsetHz = value; },
    captureDiagnosticAudio: async (...args: unknown[]) => {
      calls.push(["captureDiagnosticAudio", ...args]);
      return { format: "wav", data: "AQID", mimeType: "audio/wav", sampleRate: 48_000, samples: 3, durationMs: 1, metrics: {}, receiver: {}, stage: args[2] };
    },
    captureDiagnosticIq: async (...args: unknown[]) => {
      calls.push(["captureDiagnosticIq", ...args]);
      return { format: "complex-f32-le", data: "BAUG", sampleRate: 1_024_000, samples: 2 };
    },
    getDiagnosticSnapshot: () => ({ ok: true }),
  };
  return { controller: controller as unknown as RadioController, calls };
}

test("configure validates the entire command before changing receiver state", async () => {
  const { controller, calls } = fakeController();
  const execute = createReceiverCommandExecutor(controller, () => null);
  await assert.rejects(execute(command("configure", { scheme: "AM", bandwidthHz: Number.NaN })));
  assert.deepEqual(calls, []);
});

test("tune awaits center frequency and returns readback", async () => {
  const { controller, calls } = fakeController();
  const result = await createReceiverCommandExecutor(controller, () => null)(command("tune", { frequencyHz: 2_000_000, center: true }));
  assert.deepEqual(calls, [["setFrequency", 2_000_000], ["setOffset", 0]]);
  assert.equal((result as { frequencyHz: number }).frequencyHz, 2_000_000);
});

test("audio capture forwards stage and preserves existing base64 capture", async () => {
  const { controller, calls } = fakeController();
  const result = await createReceiverCommandExecutor(controller, () => null)(command("capture_audio", { durationMs: 500, maxSamples: 24_000, stage: "raw" }));
  assert.deepEqual(calls[0], ["captureDiagnosticAudio", 500, 24_000, "raw"]);
  assert.deepEqual(result, { format: "wav", data: "AQID", mimeType: "audio/wav", sampleRate: 48_000, samples: 3, durationMs: 1, metrics: {}, receiver: {}, stage: "raw" });
});

test("IQ capture preserves encoded data and applies IQ-specific bounds", async () => {
  const { controller } = fakeController();
  const execute = createReceiverCommandExecutor(controller, () => null);
  const result = await execute(command("capture_iq", { durationMs: 100, maxSamples: 4096 }));
  assert.deepEqual(result, { format: "complex-f32-le", data: "BAUG", sampleRate: 1_024_000, samples: 2 });
  await assert.rejects(execute(command("capture_iq", { durationMs: 2001, maxSamples: 4096 })));
  await assert.rejects(execute(command("capture_audio", { durationMs: 49, maxSamples: 4096 })));
  await assert.rejects(execute(command("capture_audio", { durationMs: 100, maxSamples: 4096, stage: "speaker" })));
});
