import assert from "node:assert/strict";
import test from "node:test";
import { FinderCleaner, type CleanerState } from "./cleaner";

class FakeSocket {
  static OPEN = 1;
  static instances: FakeSocket[] = [];
  readyState = 1;
  bufferedAmount = 0;
  binaryType = "";
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string | ArrayBuffer }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  sent: unknown[] = [];
  closed = false;
  constructor(public url: string) { FakeSocket.instances.push(this); }
  send(data: unknown) { this.sent.push(data); }
  close() { this.closed = true; }
}

test("cleaner sends raw mono PCM, receives processed playback, and invalidates stale RF/audio after stop", () => {
  const previous = { socket: globalThis.WebSocket, window: globalThis.window };
  Object.assign(globalThis, { WebSocket: FakeSocket, window: { location: { origin: "http://receiver:3000" } } });
  try {
    const states: CleanerState[] = [], rf: unknown[] = [], audio: Int16Array[] = [];
    const cleaner = new FinderCleaner(() => ({ frequencyHz: 5950000, source: "rtltcp" }), (state) => states.push(state), (advice) => rf.push(advice), (pcm) => audio.push(pcm));
    cleaner.setTarget(5950000);
    cleaner.push(new Float32Array(4800).fill(.125), 48000);
    const socket = FakeSocket.instances.at(-1)!;
    socket.onopen!();
    assert.ok(socket.url.includes("/autofinder/cleaner/source/"));
    const payload = socket.sent.find((value) => value instanceof ArrayBuffer) as ArrayBuffer;
    assert.equal(payload.byteLength, 9600);
    assert.equal(new DataView(payload).getInt16(0, true), 4096);
    socket.onmessage!({ data: JSON.stringify({ type: "rf", frequencyHz: 5950000, gainDb: 10 }) });
    socket.onmessage!({ data: Int16Array.of(100, -100).buffer });
    assert.equal(rf.length, 1); assert.equal(audio.length, 1);
    cleaner.stop();
    assert.equal(socket.closed, true);
    socket.onmessage!({ data: JSON.stringify({ type: "rf", frequencyHz: 5950000, gainDb: 0 }) });
    socket.onmessage!({ data: Int16Array.of(999).buffer });
    assert.equal(rf.length, 1); assert.equal(audio.length, 1);
    assert.equal(states.at(-1)?.status, "stopped");
  } finally { Object.assign(globalThis, { WebSocket: previous.socket, window: previous.window }); }
});

test("a stalled upload closes and reports an error instead of growing its audio queue", () => {
  const previous = { socket: globalThis.WebSocket, window: globalThis.window };
  Object.assign(globalThis, { WebSocket: FakeSocket, window: { location: { origin: "http://receiver:3000" } } });
  try {
    const states: CleanerState[] = [];
    const cleaner = new FinderCleaner(() => ({ frequencyHz: 5950000, source: "rtltcp" }), (state) => states.push(state), () => {});
    cleaner.setTarget(5950000); cleaner.push(new Float32Array(4800), 48000);
    const socket = FakeSocket.instances.at(-1)!; socket.onopen!(); socket.bufferedAmount = 1_000_000;
    cleaner.push(new Float32Array(4800), 48000);
    assert.equal(socket.closed, true); assert.equal(states.at(-1)?.status, "error");
    cleaner.stop();
  } finally { Object.assign(globalThis, { WebSocket: previous.socket, window: previous.window }); }
});
