import assert from "node:assert/strict";
import test from "node:test";
import type { RadioController } from "../radio-controller";
import type { SdrState } from "../types";
import { KiwiClient, type KiwiClientHandlers } from "./client";
import { KiwiDiversity, projectSpectrum, type DiversityStatus } from "./diversity";

type TestContext = Parameters<NonNullable<Parameters<typeof test>[0]>>[0];

class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 0;
  bufferedAmount = 0;
  binaryType = "";
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string | ArrayBuffer }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  sent: (string | ArrayBuffer)[] = [];
  closed = false;
  constructor(public url: string) { Socket.instances.push(this); }
  send(value: string | ArrayBuffer) { this.sent.push(value); }
  open() { this.readyState = 1; this.onopen?.(); }
  close() { this.closed = true; this.readyState = 3; this.onclose?.(); }
}

class Context {
  static instances: Context[] = [];
  currentTime = 1;
  state = "running";
  destination = {};
  buffers: { samples: Float32Array; copyToChannel: (samples: Float32Array) => void; duration: number }[] = [];
  constructor() { Context.instances.push(this); }
  resume() { return Promise.resolve(); }
  close() { this.state = "closed"; return Promise.resolve(); }
  createGain() { return { gain: { value: 1, setValueAtTime() {} }, connect() {}, disconnect() {} }; }
  createBuffer(_channels: number, count: number, rate: number) {
    const buffer = { samples: new Float32Array(count), duration: count / rate,
      copyToChannel(samples: Float32Array) { this.samples.set(samples); } };
    this.buffers.push(buffer);
    return buffer;
  }
  createBufferSource() { return { buffer: null, onended: null, connect() {}, disconnect() {}, start() {}, stop() {} }; }
}

function harness(t: TestContext) {
  const old = { WebSocket: globalThis.WebSocket, window: globalThis.window, AudioContext: globalThis.AudioContext };
  Object.assign(globalThis, { WebSocket: Socket, window: { location: { origin: "http://receiver:3000" } }, AudioContext: Context });
  t.after(() => Object.assign(globalThis, old));
  let clock = 1000;
  t.mock.method(performance, "now", () => clock);
  let handlers: KiwiClientHandlers;
  let closed = 0;
  const tuning: unknown[] = [];
  t.mock.method(KiwiClient.prototype, "connect", function(this: KiwiClient) {
    handlers = (this as unknown as { handlers: KiwiClientHandlers }).handlers;
    handlers.onReady(); return Promise.resolve();
  });
  t.mock.method(KiwiClient.prototype, "close", () => { closed++; });
  t.mock.method(KiwiClient.prototype, "applyTuning", (value: unknown) => tuning.push(value));
  t.mock.method(KiwiClient.prototype, "setWaterfallCenter", () => {});
  t.mock.method(KiwiClient.prototype, "setZoom", () => {});
  let state = { status: "playing", connection: "rtltcp", frequencyHz: 5950000, offsetHz: 0,
    sampleRate: 2400000, scheme: "AM", bandwidthHz: 6000, volume: .8 } as SdrState;
  let raw: ((x: Float32Array, rate: number) => void) | null = null;
  let update: (() => void) | null = null;
  const mutes: [boolean, object?][] = [];
  const controller = {
    getState: () => state,
    onRawAudio: (fn: typeof raw) => { raw = fn; return () => { raw = null; }; },
    onSpectrum: () => () => {},
    subscribe: (fn: typeof update) => { update = fn; return () => { update = null; }; },
    setBackendMonitorMuted: (muted: boolean, owner?: object) => mutes.push([muted, owner]),
  } as unknown as RadioController;
  const statuses: DiversityStatus[] = [];
  const client = new KiwiDiversity(controller, (status) => statuses.push(status), () => {});
  t.after(() => client.disconnect());
  return { client, statuses, tuning, mutes,
    handlers: () => handlers!, closed: () => closed,
    advance: () => { clock += 1000; },
    local: (rate = 48000) => raw?.(new Float32Array(4800).fill(.1), rate),
    reference: () => handlers!.onAudio(new Int16Array(960).fill(3200), 12000),
    retune: (next: Partial<SdrState>) => { state = { ...state, ...next }; update?.(); },
    hasTap: () => raw !== null,
  };
}

test("frequency overlay aligns shifted/raw FFTs and preserves narrow peaks", () => {
  const shifted = new Float32Array(1024).fill(-100);
  shifted[512] = -60;
  const raw = new Float32Array(1024);
  for (let i = 0; i < 1024; i++) raw[i] = shifted[(i + 512) % 1024];
  const a = projectSpectrum(shifted, 7000000, 16000, 7000000, 16000, true);
  const b = projectSpectrum(raw, 7000000, 16000, 7000000, 16000, false);
  assert.deepEqual(a, b);
  assert.equal(Math.max(...a.filter((x): x is number => x !== null)), 40);
  const outside = projectSpectrum(shifted, 7000000, 16000, 7100000, 16000, true);
  assert.ok(outside.every((x) => x === null));
});

test("independent Kiwi follows tuning, uploads two sources, and clears old audio on retune", async (t) => {
  const h = harness(t);
  await h.client.connect("http://nearby:8073");
  assert.equal(h.tuning.length, 1);
  h.advance(); h.local(); h.reference();
  const socket = Socket.instances.at(-1)!;
  assert.match(socket.url, /\/api\/kiwi-diversity$/);
  socket.open(); h.local(); h.reference();
  assert.deepEqual(JSON.parse(socket.sent[0] as string), { type: "configure", localRate: 48000, referenceRate: 12000 });
  const pcm = socket.sent.filter((packet): packet is ArrayBuffer => packet instanceof ArrayBuffer);
  assert.equal(new DataView(pcm[0]).getUint8(0), 0);
  assert.equal(new DataView(pcm[1]).getUint8(0), 1);
  h.retune({ frequencyHz: 6070000, scheme: "USB" });
  assert.equal(socket.closed, true);
  assert.equal(h.tuning.length, 2);
  const count = h.statuses.length;
  socket.onmessage?.({ data: JSON.stringify({ type: "status", state: "matched" }) });
  assert.equal(h.statuses.length, count, "stale session must not revive a match");
  h.client.disconnect();
  assert.equal(h.closed(), 1);
  assert.equal(h.hasTap(), false);
  h.handlers().onReady();
  assert.equal(h.tuning.length, 2, "late Kiwi callback must not revive a disconnected session");
});

test("sample-aligned A/B is opt-in and failure releases only its own mute", async (t) => {
  const h = harness(t);
  await h.client.connect("http://nearby:8073");
  h.advance(); h.local(); h.reference();
  const socket = Socket.instances.at(-1)!; socket.open();
  const bytes = new ArrayBuffer(512 * 4), view = new DataView(bytes);
  for (let i = 0; i < 512; i++) { view.setInt16(i * 4, 1000, true); view.setInt16(i * 4 + 2, 2000, true); }
  socket.onmessage?.({ data: bytes });
  assert.equal(h.mutes.length, 0, "connecting must not silence or replace local audio");
  h.client.setMonitor("clean"); socket.onmessage?.({ data: bytes });
  assert.equal(Context.instances.at(-1)!.buffers.at(-1)!.samples[0], 1000 / 32768);
  assert.deepEqual(h.mutes.at(-1), [true, h.client]);
  h.client.setMonitor("raw"); socket.onmessage?.({ data: bytes });
  assert.equal(Context.instances.at(-1)!.buffers.at(-1)!.samples[0], 2000 / 32768);
  socket.onerror?.();
  assert.deepEqual(h.mutes.at(-1), [false, h.client]);
  assert.equal(h.statuses.at(-1)?.state, "error");
  assert.equal(h.hasTap(), false);
});

test("stalled upload fails closed and releases public Kiwi channel", async (t) => {
  const h = harness(t);
  await h.client.connect("http://nearby:8073");
  h.advance(); h.local(); h.reference();
  const socket = Socket.instances.at(-1)!; socket.open(); socket.bufferedAmount = 1000000;
  h.local();
  assert.equal(socket.closed, true);
  assert.equal(h.closed(), 1);
  assert.equal(h.statuses.at(-1)?.state, "error");
});

test("stopping local reception disconnects reference instead of playing unrelated audio", async (t) => {
  const h = harness(t);
  await h.client.connect("http://nearby:8073");
  h.retune({ status: "idle" });
  assert.equal(h.closed(), 1);
  assert.equal(h.hasTap(), false);
  assert.equal(h.statuses.at(-1)?.state, "error");
});
