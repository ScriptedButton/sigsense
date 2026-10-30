import assert from "node:assert/strict";
import { test } from "node:test";
import { scheduleAudioBlock, TappingPlayer } from "./tapping-player";

test("audio scheduling stays contiguous when resampler block lengths alternate", () => {
  const lengths = [1024, 1025, 1024, 1025, 1024, 1025];
  let queuedUntil = 0;
  let previousEnd = 0;
  for (let i = 0; i < lengths.length; i++) {
    const scheduled = scheduleAudioBlock(
      queuedUntil,
      1,
      lengths[i] / 48_000,
    );
    if (i > 0) assert.equal(scheduled.start, previousEnd);
    previousEnd = scheduled.queuedUntil;
    queuedUntil = scheduled.queuedUntil;
  }
});

test("audio scheduling drops stale or excessive queue lead", () => {
  assert.equal(scheduleAudioBlock(0.5, 1, 0.02).start, 1.05);
  assert.equal(scheduleAudioBlock(2, 1, 0.02).start, 1.05);
});

test("playback cancels obsolete queued sources, releases its context, and preserves input", () => {
  const sources: { stopped: boolean; startTime: number }[] = [];
  let closed = false;
  class TestAudioContext {
    sampleRate = 48_000;
    currentTime = 1;
    state = "running";
    destination = {};
    createGain() { return { gain: { value: 0 }, connect() {} }; }
    createBuffer(_channels: number, length: number, rate: number) {
      const data = [new Float32Array(length), new Float32Array(length)];
      return { duration: length / rate, getChannelData: (channel: number) => data[channel] };
    }
    createBufferSource() {
      const source = {
        buffer: null,
        onended: null,
        stopped: false,
        startTime: 0,
        connect() {},
        disconnect() {},
        start(time: number) { this.startTime = time; },
        stop() { this.stopped = true; },
      };
      sources.push(source);
      return source;
    }
    close() { closed = true; return Promise.resolve(); }
  }
  const original = Object.getOwnPropertyDescriptor(globalThis, "AudioContext");
  Object.defineProperty(globalThis, "AudioContext", { configurable: true, value: TestAudioContext });
  try {
    const player = new TappingPlayer();
    const input = Float32Array.of(.2, -.2, .1, -.1);
    player.setSquelched(true);
    player.play(input, input);
    assert.deepEqual(input, Float32Array.of(.2, -.2, .1, -.1));
    player.setSquelched(false);
    for (let i = 0; i < 40; i++) player.play(new Float32Array(1_024), new Float32Array(1_024));
    assert.ok(sources.some((source) => source.stopped), "overflowed queue must be cancelled before rescheduling");
    player.close();
    assert.ok(sources.every((source) => source.stopped));
    assert.ok(closed);
  } finally {
    if (original) Object.defineProperty(globalThis, "AudioContext", original);
    else Reflect.deleteProperty(globalThis, "AudioContext");
  }
});
