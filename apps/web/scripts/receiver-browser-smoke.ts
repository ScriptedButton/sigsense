/** Real Chromium + production app + official MCP client. Uses DEMO only; never takes the user's RTL hardware. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import WebSocket from "ws";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { existsSync } from "node:fs";
import { Client } from "@modelcontextprotocol/client";
import { StdioClientTransport } from "@modelcontextprotocol/client/stdio";
import { ReceiverBrokerClient } from "../server/receiver-mcp/tools";

const delay = (milliseconds: number) => new Promise((done) => setTimeout(done, milliseconds));

async function main() {
  const profile = await mkdtemp(join(tmpdir(), "sigsense-browser-smoke-"));
  const marker = crypto.randomUUID();
  const url = new URL(process.env.SIGSENSE_TEST_URL ?? "http://192.168.1.131:3000/");
  url.searchParams.set("receiverMcpSmoke", marker);
  const executable = process.env.CHROMIUM_BIN ?? (existsSync("/usr/lib/chromium/chromium") ? "/usr/lib/chromium/chromium" : "chromium");
  const browser = spawn(executable, ["--headless", "--no-sandbox", "--disable-extensions", "--password-store=basic", "--disable-dev-shm-usage", "--disable-gpu", "--no-first-run", "--remote-debugging-port=0", "--autoplay-policy=no-user-gesture-required", `--user-data-dir=${profile}`, url.href], { stdio: ["ignore", "ignore", "pipe"] });
  let browserErrors = "";
  browser.stderr.on("data", (data) => { browserErrors = (browserErrors + data.toString()).slice(-6000); });
  let client: Client | undefined, sessionId = "";
  try {
    const broker = new ReceiverBrokerClient();
    for (let attempt = 0; attempt < 60; attempt++) {
      const found = (await broker.sessions()).find((value) => String((value as { url?: string }).url).includes(marker)) as { id: string } | undefined;
      if (found) { sessionId = found.id; break; }
      if (browser.exitCode !== null) throw new Error(`Chromium exited: ${browserErrors}`);
      await delay(500);
    }
    assert.ok(sessionId, `Production browser failed to pair with broker. ${browserErrors}`);
    const transport = new StdioClientTransport({ command: process.execPath, args: ["--import", "tsx", resolve("server/receiver-mcp/index.ts")], cwd: resolve("."), stderr: "pipe" });
    client = new Client({ name: "sigsense-real-browser-test", version: "1.0.0" });
    await client.connect(transport);
    const call = async (name: string, args: Record<string, unknown> = {}) => {
      const result = await client!.callTool({ name, arguments: { sessionId, ...args } });
      assert.ok(!result.isError, `${name}: ${JSON.stringify(result.content)}`);
      return result;
    };
    const state = (await call("get_state")).structuredContent as { status: string };
    assert.equal(state.status, "idle", "Test browser must start disconnected");
    await call("configure", { scheme: "AM", bandwidthHz: 6000, volume: .8, audioNoiseReduction: "off", voiceIsolation: false });
    await call("tune", { frequencyHz: 810000, scheme: "AM", center: true });
    const connected = (await call("connect", { type: "demo" })).structuredContent as { connection: string; status: string };
    assert.equal(connected.connection, "demo"); assert.equal(connected.status, "playing");
    await delay(1000);
    const diagnostics = (await call("get_diagnostics")).structuredContent as { audio: { contextState: string }; listenFrequencyHz: number };
    assert.equal(diagnostics.listenFrequencyHz, 810000);
    assert.equal(diagnostics.audio.contextState, "running");
    const filteredState = (await call("configure", { audioSpeechFilter: true })).structuredContent as { audioSpeechFilter: boolean };
    assert.equal(filteredState.audioSpeechFilter, true);
    for (const stage of ["raw", "playback"]) {
      const audio = await call("capture_audio", { durationMs: 100, maxSamples: 4800, stage });
      const meta = audio.structuredContent as { samples: number; sampleRate: number; metrics: { rms: number; invalidSamples: number } };
      assert.equal(meta.samples, 4800); assert.equal(meta.sampleRate, 48000);
      assert.ok(meta.metrics.rms > .001); assert.equal(meta.metrics.invalidSamples, 0);
      const block = audio.content.find((item) => item.type === "audio") as { data: string } | undefined;
      assert.ok(block); assert.equal(Buffer.from(block.data, "base64").toString("ascii", 0, 4), "RIFF");
    }
    const iq = await call("capture_iq", { durationMs: 10, maxSamples: 4096 });
    const saved = iq.structuredContent as { uri: string; path: string; bytes: number; metadata: { samples: number; format: string } };
    assert.equal(saved.bytes, 4096 * 8); assert.equal(saved.metadata.format, "complex-f32-le");
    const resource = await client.readResource({ uri: saved.uri });
    assert.equal(Buffer.from((resource.contents[0] as { blob: string }).blob, "base64").length, saved.bytes);
    await rm(saved.path);
    const spectrum = (await call("get_spectrum")).structuredContent as { values: unknown[]; binOrder: string };
    assert.ok(spectrum.values.length > 100); assert.equal(spectrum.binOrder, "ascending-frequency");
    await call("set_display", { waterfallPalette: "ocean", waterfallRangeDb: 32 });
    assert.equal(((await call("get_display")).structuredContent as { waterfallPalette: string }).waterfallPalette, "ocean");
    const scheduled = (await call("get_schedule", { when: "all", query: "BBC" })).structuredContent as { result: unknown[] };
    assert.ok(scheduled.result.length > 0);
    // Exercise actual rendered controls, not just the receiver command API.
    const port = (await readFile(join(profile, "DevToolsActivePort"), "utf8")).split("\n")[0];
    const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json() as Array<{ url: string; webSocketDebuggerUrl: string }>;
    const page = pages.find((value) => value.url.includes(marker));
    assert.ok(page);
    const debug = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise<void>((done, reject) => { debug.once("open", done); debug.once("error", reject); });
    let sequence = 0;
    const evaluate = (expression: string) => new Promise<unknown>((done, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => { debug.off("message", receive); reject(new Error("UI evaluation timed out")); }, 5000);
      const receive = (data: WebSocket.RawData) => {
        const message = JSON.parse(data.toString());
        if (message.id !== id) return;
        clearTimeout(timer); debug.off("message", receive);
        if (message.error || message.result?.exceptionDetails) reject(new Error(JSON.stringify(message)));
        else done(message.result?.result?.value);
      };
      debug.on("message", receive); debug.send(JSON.stringify({ id, method: "Runtime.evaluate", params: { expression, returnByValue: true } }));
    });
    try {
      await evaluate(`document.getElementById("rail-tab-stations").click()`);
      for (let i = 0; i < 30; i++) {
        if (await evaluate(`Boolean(document.querySelector('[aria-label="Device shortwave survey"] button'))`)) break;
        await delay(100);
      }
      const click = (text: string) => evaluate(`(() => { const b = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(text)}); if (!b || b.disabled) throw Error('Button unavailable'); b.click(); return true; })()`);
      const report = await (await fetch(new URL("/api/shortwave-survey", url))).json() as { candidates: Array<{ frequencyHz: number }> };
      if (report.candidates.length) {
        await click("Try best candidate"); await delay(300);
        const tuned = (await call("get_state")).structuredContent as { frequencyHz: number; offsetHz: number; audioSpeechFilter: boolean };
        assert.equal(tuned.frequencyHz + tuned.offsetHz, report.candidates[0].frequencyHz);
        assert.equal(tuned.audioSpeechFilter, true);
        if (report.candidates.length > 1) {
          await click("Next signal →"); await delay(300);
          const next = (await call("get_state")).structuredContent as { frequencyHz: number; offsetHz: number };
          assert.equal(next.frequencyHz + next.offsetHz, report.candidates[1].frequencyHz);
        }
      }
      await click("Natural"); await delay(100);
      assert.equal(((await call("get_state")).structuredContent as { bandwidthHz: number }).bandwidthHz, 9000);
      await click("Speech"); await delay(100);
      await click("Compare: bypass audio cleanup"); await delay(100);
      const bypass = (await call("get_state")).structuredContent as { audioSpeechFilter: boolean; audioNoiseReduction: string; bandwidthHz: number };
      assert.equal(bypass.audioSpeechFilter, false); assert.equal(bypass.audioNoiseReduction, "off"); assert.equal(bypass.bandwidthHz, 6000);
      await click("Restore audio cleanup"); await delay(100);
      assert.equal(((await call("get_state")).structuredContent as { audioSpeechFilter: boolean }).audioSpeechFilter, true);
      const originalDial = (await call("get_state")).structuredContent as { frequencyHz: number; offsetHz: number };
      await click("Find speech automatically");
      for (let attempt = 0; attempt < 60; attempt++) {
        const finder = (await call("autofinder_state")).structuredContent as { status: string; tilesScanned: number; message: string };
        assert.notEqual(finder.status, "error", finder.message);
        if (finder.tilesScanned > 0) break;
        await delay(100);
      }
      assert.ok(((await call("autofinder_state")).structuredContent as { tilesScanned: number }).tilesScanned > 0);
      await evaluate(`document.getElementById("rail-tab-radio").click()`); await delay(100);
      assert.notEqual(((await call("autofinder_state")).structuredContent as { status: string }).status, "idle");
      await evaluate(`document.getElementById("rail-tab-stations").click()`); await delay(100);
      await click("Stop & restore dial");
      for (let i = 0; i < 60; i++) {
        if (((await call("autofinder_state")).structuredContent as { status: string }).status === "idle") break;
        await delay(100);
      }
      const restored = (await call("get_state")).structuredContent as { frequencyHz: number; offsetHz: number };
      assert.equal(restored.frequencyHz, originalDial.frequencyHz); assert.equal(restored.offsetHz, originalDial.offsetHz);
      process.stderr.write("autofinder UI smoke ok: one-click start, real neural API readiness, automatic RF scouting, survives panel changes, stop restores dial (DEMO only)\n");
      process.stderr.write("receiver UI smoke ok: best candidate, next signal, listening presets, exact cleanup bypass/restore\n");
    } finally { debug.close(); }
    await call("stop");
    assert.equal(((await call("get_state")).structuredContent as { status: string }).status, "idle");
    process.stderr.write("receiver browser smoke ok: Chromium on LAN HTTP, real app/controller, MCP tune/connect, raw+playback audio, native IQ resource, spectrum, display, schedule, stop (DEMO only)\n");
  } finally {
    await client?.close().catch(() => {});
    browser.kill("SIGTERM");
    for (let i = 0; i < 20 && browser.exitCode === null; i++) await delay(100);
    if (browser.exitCode === null) browser.kill("SIGKILL");
    await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
}

void main().catch((error) => { process.stderr.write(`${error instanceof Error ? error.stack : error}\n`); process.exitCode = 1; });
