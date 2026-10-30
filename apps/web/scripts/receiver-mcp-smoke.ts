import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Client } from "@modelcontextprotocol/client";
import { StdioClientTransport } from "@modelcontextprotocol/client/stdio";
import { WebSocket } from "ws";
import { createReceiverBroker } from "../server/receiver-control/broker";
import { createReceiverCommandExecutor } from "../src/lib/sdr/receiver-control/browser-client";
import { encodeWav } from "../src/lib/sdr/receiver-control/capture";
import type { RadioController } from "../src/lib/sdr/radio-controller";
import type { SdrState } from "../src/lib/sdr/types";
import type { ReceiverCommand } from "../src/lib/sdr/receiver-control/protocol";

async function main() {
 const temporary = await mkdtemp(join(tmpdir(), "sigsense-mcp-smoke-"));
 const broker = createReceiverBroker({ host: "127.0.0.1", port: 0, token: "smoke-token", configPath: join(temporary, "receiver.json") });
 let socket: WebSocket | undefined, client: Client | undefined;
 const state = { frequencyHz: 7_100_000, offsetHz: 0, sampleRate: 1_024_000, scheme: "AM", status: "playing", bandwidthHz: 6_000, volume: .8 } as SdrState;
 const capture = { format: "wav", data: encodeWav(new Float32Array(2400), 48000), sampleRate: 48000, samples: 2400 };
 const controller = {
   getState: () => ({ ...state }), setScheme: (value: SdrState["scheme"]) => { state.scheme = value; },
   setBandwidth: (value: number) => { state.bandwidthHz = value; }, setFrequency: async (value: number) => { state.frequencyHz = value; },
   setOffset: (value: number) => { state.offsetHz = value; }, setVolume: (value: number) => { state.volume = value; },
   setGain: async (value: number) => { state.gainDb = value; }, setAutoGain: async (value: boolean) => { state.autoGain = value; },
   setAudioNoiseReduction: (value: SdrState["audioNoiseReduction"]) => { state.audioNoiseReduction = value; },
   captureDiagnosticAudio: async (_ms: number, _max: number, stage: string) => ({ ...capture, stage }),
   captureDiagnosticIq: async () => ({ format: "complex-f32-le", data: Buffer.alloc(2048).toString("base64"), samples: 256, sampleRate: state.sampleRate, centerHz: state.frequencyHz }),
   getDiagnosticSnapshot: () => ({ state: { ...state }, audio: { underruns: 0 } }),
   stop: async () => { state.status = "idle"; },
 } as unknown as RadioController;
 const values = new Array(16).fill(-90); values[3] = -50; values[8] = -10;
 const execute = createReceiverCommandExecutor(controller, () => ({ values, centerHz: 7_100_000, centerOffsetHz: 0, sampleRate: 16000, binHz: 1000, startHz: 7_092_000, capturedAt: new Date().toISOString() }));
 try {
  const { port } = await broker.listen();
  socket = new WebSocket(`ws://127.0.0.1:${port}/receiver?token=${broker.token}`, { origin: "http://127.0.0.1:3000" });
  await new Promise<void>((ready, reject) => {
    socket!.once("open", () => socket!.send(JSON.stringify({ type: "hello", sessionId: "smoke-browser", url: "http://127.0.0.1:3000/", title: "Smoke receiver", state })));
    socket!.on("message", (data) => {
      const message = JSON.parse(data.toString());
      if (message.type === "welcome") ready();
      if (message.type === "command") void execute(message as ReceiverCommand).then(
        (result) => socket!.send(JSON.stringify({ type: "result", id: message.id, result })),
        (error) => socket!.send(JSON.stringify({ type: "result", id: message.id, error: String(error) })),
      );
    });
    socket!.once("error", reject);
  });
  const env = Object.fromEntries(Object.entries(process.env).filter((entry): entry is [string, string] => typeof entry[1] === "string"));
  const transport = new StdioClientTransport({ command: process.execPath, args: ["--import", "tsx", resolve("server/receiver-mcp/index.ts")], cwd: resolve("."),
    env: { ...env, SIGSENSE_RECEIVER_BROKER_URL: `http://127.0.0.1:${port}`, SIGSENSE_RECEIVER_TOKEN_FILE: join(temporary, "receiver.json") }, stderr: "pipe" });
  client = new Client({ name: "receiver-mcp-smoke", version: "1.0.0" }); await client.connect(transport);
  assert.equal((await client.listTools()).tools.length, 19);
  const call = async (name: string, args: Record<string, unknown> = {}) => {
    const result = await client!.callTool({ name, arguments: args });
    assert.ok(!result.isError, `${name}: ${JSON.stringify(result.content)}`); return result;
  };
  assert.equal(((await call("list_receivers")).structuredContent as { sessions: unknown[] }).sessions.length, 1);
  await call("configure", { scheme: "USB", bandwidthHz: 2400, gainDb: 0, audioNoiseReduction: "low" });
  const configured = (await call("get_state")).structuredContent as SdrState;
  assert.equal(configured.scheme, "USB"); assert.equal(configured.bandwidthHz, 2400); assert.equal(configured.gainDb, 0);
  await call("tune", { frequencyHz: 9_420_000, center: true, scheme: "AM", bandwidthHz: 6000 });
  assert.equal(state.frequencyHz + state.offsetHz, 9_420_000);
  assert.equal(((await call("find_signal")).structuredContent as { frequencyHz: number }).frequencyHz, 7_095_000);
  await call("get_spectrum"); await call("get_signals"); await call("get_diagnostics");
  const audio = await call("capture_audio", { durationMs: 50, maxSamples: 2400, stage: "raw" });
  assert.equal((audio.structuredContent as { stage: string }).stage, "raw"); assert.ok(audio.content.some((block) => block.type === "audio"));
  const saved = (await call("capture_iq", { durationMs: 10, maxSamples: 256 })).structuredContent as { uri: string; path: string; bytes: number };
  assert.ok(saved.path.endsWith(".cf32")); assert.equal(saved.bytes, 2048);
  const resource = await client.readResource({ uri: saved.uri });
  assert.equal(Buffer.from((resource.contents[0] as { blob: string }).blob, "base64").length, 2048); await rm(saved.path);
  const rejected = await client.callTool({ name: "configure", arguments: { mode: "USB" } });
  assert.ok(rejected.isError, "incorrect parameter names must not silently succeed");
  await call("stop"); assert.equal(state.status, "idle");
  process.stderr.write("receiver-mcp smoke ok: 19 tools; real browser executor; mode/tune readback, signals, audio, IQ resource, invalid input, stop\n");
 } finally {
  await client?.close().catch(() => undefined); socket?.terminate(); await broker.close(); await rm(temporary, { recursive: true, force: true });
 }
}
void main().catch((error) => { process.stderr.write(`${error instanceof Error ? error.stack : error}\n`); process.exitCode = 1; });
