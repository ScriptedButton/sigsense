import assert from "node:assert/strict";
import { test } from "node:test";
import { RemoteSignalIdEngine } from "./remote-engine";

test("remote identification preserves narrow signals and isolates retunes", async () => {
  const originalFetch = globalThis.fetch;
  const originalNow = Date.now;
  let now = 100_000;
  Date.now = () => now;
  const requests: { body: Record<string, unknown>; resolve: (response: Response) => void }[] = [];
  globalThis.fetch = ((_input: unknown, init: RequestInit) => new Promise<Response>((resolve) => {
    requests.push({ body: JSON.parse(String(init.body)), resolve });
  })) as typeof fetch;
  const response = (tracks: unknown[] = []) => new Response(JSON.stringify({ tracks, noise_floor_db: -100 }));
  const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
  const engine = new RemoteSignalIdEngine(() => {});
  engine.setEnabled(true);
  const feed = (raw: Float32Array, center = 100e6, count = 9) => {
    for (let i = 0; i < count; i++) {
      now += 65;
      engine.pushSpectrum(raw, center, 2_048_000, center);
    }
  };
  try {
    const raw = new Float32Array(8192).fill(-100);
    raw[123] = -40;
    feed(raw);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].body.bins, 8192);
    const bytes = Buffer.from(String(requests[0].body.data), "base64");
    assert.equal(bytes[(123 + 4096) % 8192], 255, "a one-bin emitter survives transport");
    requests[0].resolve(response([{ id: "old", label: "NFM", confidence: .8, center_freq_hz: 100e6, bandwidth_hz: 12500 }]));
    await flush();
    assert.equal(engine.getState().signals.length, 1);
    feed(raw, 101e6, 1);
    assert.equal(engine.getState().signals.length, 0, "retune clears old labels immediately");
    feed(raw, 101e6);
    assert.equal(requests.length, 2);
    feed(raw, 102e6);
    assert.equal(requests.length, 3);
    requests[1].resolve(response());
    await flush();
    feed(raw, 102e6, 20);
    assert.equal(requests.length, 3, "an old request cannot unlock the current request");
    requests[2].resolve(response());
    await flush();

    // Above the server limit, a 60 dB one-bin signal loses only 3 dB when
    // pooled with one quiet bin, instead of the old 30 dB logarithmic average.
    const large = new Float32Array(32768).fill(-100);
    large[0] = -40;
    feed(large, 103e6);
    const pooled = requests.at(-1)!.body;
    assert.equal(pooled.bins, 16384);
    assert.ok(Math.abs(Number(pooled.db_max) - (-43.0103)) < .01);
  } finally {
    globalThis.fetch = originalFetch;
    Date.now = originalNow;
  }
});

test("IQ capture sends consecutive samples before display averaging and resets on retune", async () => {
  const originalFetch = globalThis.fetch;
  const originalNow = Date.now;
  let now = 100_000;
  Date.now = () => now;
  const requests: { path: string; body: Record<string, unknown> }[] = [];
  globalThis.fetch = (async (input: unknown, init: RequestInit) => {
    requests.push({ path: String(input), body: JSON.parse(String(init.body)) });
    return new Response(JSON.stringify({ tracks: [] }));
  }) as typeof fetch;
  try {
    const engine = new RemoteSignalIdEngine(() => {});
    engine.setEnabled(true);
    engine.setIqMode(true);
    const I = new Float32Array(32768).fill(.5);
    const Q = new Float32Array(32768).fill(-.25);
    engine.pushIq(I, Q, 100e6, 1_024_000);
    assert.equal(requests.length, 0);
    engine.pushIq(I, Q, 101e6, 1_024_000);
    assert.equal(requests.length, 0, "retune must discard the partial old capture");
    engine.pushIq(I, Q, 101e6, 1_024_000);
    assert.equal(requests.length, 1);
    assert.ok(requests[0].path.endsWith("/analyze/iq-bands"));
    const payload = requests[0].body;
    assert.equal(payload.center_freq_hz, 101e6);
    const bytes = Buffer.from(String(payload.data), "base64");
    assert.equal(bytes.length, 65536 * 4);
    assert.equal(bytes.readInt16LE(0), 16384);
    assert.equal(bytes.readInt16LE(2), -8192);
    assert.equal(bytes.readInt16LE(bytes.length - 4), 16384);
    for (let i = 0; i < 20; i++) {
      now += 65;
      engine.pushSpectrum(new Float32Array(2048).fill(-90), 101e6, 1_024_000, 101e6);
    }
    assert.equal(requests.length, 1, "IQ sources must not also post display rows");
  } finally {
    globalThis.fetch = originalFetch;
    Date.now = originalNow;
  }
});
