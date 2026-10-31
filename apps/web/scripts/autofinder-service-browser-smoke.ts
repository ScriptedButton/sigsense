/** Rendered production UI test. All findings and RTL IQ are synthetic; no hardware access. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import type { AddressInfo } from "node:net";
import WebSocket, { WebSocketServer } from "ws";
import { ReceiverBrokerClient } from "../server/receiver-mcp/tools";
import { INITIAL_FINDER_STATE } from "../src/lib/sdr/autofinder/engine";
import type { ServiceView } from "../src/lib/sdr/autofinder/service-types";

const wait = (ms: number) => new Promise((done) => setTimeout(done, ms));
const processing = { location: "lan" as const, configured: true, engine: "demucs-dns64", device: "cuda:0", accelerated: true,
  gpuName: "Fixture GPU", roundTripMs: 12.2, delayMs: 100, fallback: false };
async function until(work: () => Promise<unknown>, message: string) {
  for (let i = 0; i < 100; i++) { if (await work()) return; await wait(150); }
  throw new Error(message);
}

async function main() {
  const profile = await mkdtemp(join(tmpdir(), "sigsense-finder-ui-"));
  const marker = crypto.randomUUID();
  const url = new URL(process.env.SIGSENSE_TEST_URL ?? "http://127.0.0.1:3001/");
  url.searchParams.set("autofinderServiceSmoke", marker);
  const events: string[] = [];
  const fakeRtl = new WebSocketServer({ host: "127.0.0.1", port: 0 });
  await new Promise<void>((done) => fakeRtl.once("listening", done));
  fakeRtl.on("connection", (socket, request) => {
    if (request.url?.startsWith("/cleaner-source")) {
      socket.on("message", (_data, binary) => {
        if (!binary) {
          socket.send(JSON.stringify({ status: "optimizing", profile: "Balanced", iterations: 2, neuralAvailable: true,
            processing,
            metrics: { score: 6, speechRetention: .96, noiseReductionDb: 8, eligible: true, quietMeasured: true } }));
        } else {
          events.push("cleaner-upload");
          socket.send(Buffer.alloc(3200));
        }
      });
      return;
    }
    if (request.url?.startsWith("/cleaner-listen")) {
      events.push(request.url.includes("stage=raw") ? "monitor-raw" : "monitor-clean");
      const pcm = Buffer.alloc(3200);
      for (let i = 0; i < 1600; i++) pcm.writeInt16LE(Math.round(1000 * Math.sin(2 * Math.PI * 500 * i / 16000)), i * 2);
      const timer = setInterval(() => { if (socket.readyState === WebSocket.OPEN) socket.send(pcm); }, 100);
      socket.once("close", () => clearInterval(timer));
      return;
    }
    events.push("rtl-connected");
    const header = Buffer.alloc(12); header.write("RTL0"); header.writeUInt32BE(5, 4);
    socket.send(header);
    const iq = Buffer.alloc(65536, 128);
    const timer = setInterval(() => { if (socket.readyState === WebSocket.OPEN && socket.bufferedAmount < 2e6) socket.send(iq); }, 25);
    socket.once("close", () => clearInterval(timer));
  });
  const browser = spawn(process.env.CHROMIUM_BIN ?? "/usr/lib/chromium/chromium", ["--headless", "--no-sandbox", "--disable-extensions", "--password-store=basic", "--disable-dev-shm-usage", "--disable-gpu", "--no-first-run", "--remote-debugging-port=0", "--autoplay-policy=no-user-gesture-required", `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
  let browserErrors = "", passed = false;
  browser.stderr.on("data", (data) => { browserErrors = (browserErrors + data.toString()).slice(-4000); });
  let debug: WebSocket | undefined;
  const broker = new ReceiverBrokerClient();
  let sessionId = "";
  try {
    await until(async () => { try { return Boolean(await readFile(join(profile, "DevToolsActivePort"), "utf8")); } catch { return false; } }, "Chromium did not start");
    const port = (await readFile(join(profile, "DevToolsActivePort"), "utf8")).split("\n")[0];
    const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json() as Array<{ url: string; webSocketDebuggerUrl: string }>;
    const page = pages.find((entry) => entry.url === "about:blank"); assert.ok(page);
    debug = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise<void>((done, reject) => { debug!.once("open", done); debug!.once("error", reject); });
    let sequence = 0;
    let loaded = false;
    let failNextRelease = true;
    let holdNextRelease = false;
    let heldRelease: string | null = null;
    const pending = new Map<number, { resolve(value: Record<string, unknown>): void; reject(error: Error): void }>();
    const cdp = (method: string, params: Record<string, unknown> = {}) => new Promise<Record<string, unknown>>((done, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timed out: ${method}`)); }, 15000);
      pending.set(id, { resolve: (value) => { clearTimeout(timer); done(value); }, reject: (error) => { clearTimeout(timer); reject(error); } });
      debug!.send(JSON.stringify({ id, method, params }));
    });
    const at = new Date().toISOString();
    let view: ServiceView = { version: 1, at, enabled: true, online: true, stale: false, status: "scanning", message: "Searching 49 m", sessionId: "fixture", receiver: null,
      finder: { ...INITIAL_FINDER_STATE, status: "checking", frequencyHz: 6100000, band: "49 m", tilesScanned: 25, candidatesChecked: 8, round: 2,
        cleaning: { status: "optimizing", streamId: "fixture-clean", frequencyHz: 5950000, profile: "Balanced", iterations: 2, neuralAvailable: true,
          processing,
          metrics: { score: 6, speechRetention: .96, noiseReductionDb: 8, eligible: true, quietMeasured: true } } },
      findings: [{ frequencyHz: 5950000, bandwidthHz: 4000, band: "49 m", firstSeen: at, lastSeen: at, speechRatio: .82, confirmations: 2 }],
      checks: [{ id: "fixture-check", frequencyHz: 6100000, speechRatio: .01, accepted: false, at }] };
    debug.on("message", (raw) => {
      const message = JSON.parse(raw.toString());
      if (message.id) {
        const handler = pending.get(message.id); if (!handler) return;
        pending.delete(message.id);
        if (message.error) handler.reject(new Error(JSON.stringify(message.error))); else handler.resolve(message.result ?? {});
      } else if (message.method === "Page.loadEventFired") {
        loaded = true;
      } else if (message.method === "Fetch.requestPaused") {
        const request = message.params.request;
        if (new URL(request.url).pathname.endsWith("/autofinder/cleaner/worker")) {
          void cdp("Fetch.fulfillRequest", { requestId: message.params.requestId, responseCode: 200,
            responseHeaders: [{ name: "Content-Type", value: "application/json" }],
            body: Buffer.from(JSON.stringify({ ...processing, ready: true })).toString("base64") });
          return;
        }
        if (request.method === "POST") {
          const command = JSON.parse(request.postData);
          const action = command.action;
          events.push(action);
          if (action === "release" && holdNextRelease) {
            holdNextRelease = false; heldRelease = message.params.requestId; return;
          }
          if (action === "release" && failNextRelease) {
            failNextRelease = false;
            void cdp("Fetch.fulfillRequest", { requestId: message.params.requestId, responseCode: 502, responseHeaders: [{ name: "Content-Type", value: "application/json" }], body: Buffer.from(JSON.stringify({ error: "Receiver release failed" })).toString("base64") });
            return;
          }
          if (action === "pause") view = { ...view, enabled: false, status: "paused", message: "Background search paused." };
          if (action === "resume") view = { ...view, enabled: true, status: "scanning", message: "Searching 49 m" };
          if (action === "release") view = { ...view, status: "yielding", message: "Receiver available for listening." };
          if (action === "gain") view = { ...view, gain: command.autoGain ? { autoGain: true } : { autoGain: false, gainDb: command.gainDb } };
        }
        void cdp("Fetch.fulfillRequest", { requestId: message.params.requestId, responseCode: 200,
          responseHeaders: [{ name: "Content-Type", value: "application/json" }], body: Buffer.from(JSON.stringify(view)).toString("base64") });
      }
    });
    await cdp("Page.enable");
    await cdp("Fetch.enable", { patterns: [{ urlPattern: "*/api/autofinder-service*", requestStage: "Request" },
      { urlPattern: "*/autofinder/cleaner/worker", requestStage: "Request" }] });
    await cdp("Page.addScriptToEvaluateOnNewDocument", { source: `(() => { const Native = window.WebSocket; window.WebSocket = class extends Native { constructor(url, protocols) { const address = new URL(url, location.href); const base = 'ws://127.0.0.1:${(fakeRtl.address() as AddressInfo).port}'; const target = address.pathname.includes('/autofinder/cleaner/source/') ? base + '/cleaner-source' : address.pathname.includes('/autofinder/cleaner/listen/') ? base + '/cleaner-listen' + address.search : address.port === '8081' ? base : url; super(target, protocols); } }; })();` });
    await cdp("Page.navigate", { url: url.href });
    process.stdout.write("finder-ui: navigating to app\n");
    await until(async () => loaded, "Page did not finish loading");
    const evaluate = async (expression: string) => {
      const result = await cdp("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true, userGesture: true });
      if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
      return (result.result as { value?: unknown })?.value;
    };
    const text = () => evaluate(`document.querySelector('[aria-label="Background shortwave finder"]')?.textContent ?? ''`) as Promise<string>;
    const click = (label: string) => evaluate(`(() => { const panel = document.querySelector('[aria-label="Background shortwave finder"]'); const button = [...panel.querySelectorAll('button')].find(b => b.textContent === ${JSON.stringify(label)}); if (!button || button.disabled) throw Error('Button unavailable: ${label}'); button.click(); })()`);
    await until(async () => (await text()).includes("5.9500 MHz"), "Saved finding did not render");
    process.stdout.write("finder-ui: finding rendered\n");
    assert.ok((await text()).includes("82% speech activity"));
    assert.ok((await text()).includes("LAN GPU · demucs-dns64 · Fixture GPU · 12 ms round trip"));
    await evaluate(`document.querySelector('[aria-label="Background shortwave finder"] form').requestSubmit()`);
    await until(async () => (await text()).includes("Manual 50 dB selected."), "Manual background gain did not render");
    await click("Auto gain");
    await until(async () => (await text()).includes("Auto gain selected."), "Auto background gain did not render");
    await click("Listen cleaned");
    await until(async () => events.includes("monitor-clean") && await evaluate(`document.querySelector('[aria-label="Background shortwave finder"] [aria-label="Adaptive signal cleaning"] label span')?.textContent === 'Cleaned'`), "Cleaned monitor did not schedule PCM playback");
    await click("Compare original");
    await until(async () => events.includes("monitor-raw") && await evaluate(`document.querySelector('[aria-label="Background shortwave finder"] [aria-label="Adaptive signal cleaning"] label span')?.textContent === 'Original'`), "Original comparison did not schedule PCM playback");
    await click("Stop monitor");
    // A direct Connect IQ failure must reach the visible receiver error banner,
    // rather than becoming an ignored rejected promise before RTL startup.
    const rtlForm = async (action: "open" | "connect" | "cancel") => {
      await evaluate(`(() => {
      if (${JSON.stringify(action)} === 'open') {
        const button = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'RTL server' && b.getClientRects().length);
        if (!button) throw Error('RTL server button unavailable'); button.click(); return;
      }
      const form = [...document.querySelectorAll('form')].find(f => f.querySelector('[aria-label="RTL server address"]') && f.getClientRects().length);
      if (!form) { if (${JSON.stringify(action)} === 'cancel') return; throw Error('RTL form unavailable'); }
      const label = ${JSON.stringify(action)} === 'connect' ? 'Connect IQ' : 'Cancel';
      [...form.querySelectorAll('button')].find(b => b.textContent.trim() === label).click();
    })()`);
      if (action === "open") await until(async () => await evaluate(`[...document.querySelectorAll('form')].some(f => f.querySelector('[aria-label="RTL server address"]') && f.getClientRects().length)`), "RTL form did not open");
    };
    await rtlForm("open");
    await rtlForm("connect");
    await until(async () => await evaluate(`document.querySelector('.sdr-error[role="alert"]')?.textContent.includes('Could not release the background receiver: Receiver release failed')`), "Direct Connect IQ failure did not display an error");
    assert.ok(!events.includes("rtl-connected"), "A failed direct handoff must not open RTL");
    await rtlForm("cancel");
    holdNextRelease = true;
    await rtlForm("open"); await rtlForm("connect");
    await until(async () => heldRelease !== null, "Pending direct handoff not observed");
    const pendingSession = (await broker.sessions() as Array<{ id: string; url: string }>).find((entry) => entry.url.includes(marker));
    assert.ok(pendingSession); sessionId = pendingSession.id;
    await broker.command("stop", {}, sessionId);
    await cdp("Fetch.fulfillRequest", { requestId: heldRelease, responseCode: 200,
      responseHeaders: [{ name: "Content-Type", value: "application/json" }], body: Buffer.from(JSON.stringify(view)).toString("base64") });
    await wait(200);
    assert.equal((await broker.command("get_state", {}, sessionId) as { status: string }).status, "idle");
    assert.ok(!events.includes("rtl-connected"), "Stop during handoff must prevent a delayed connection");
    await rtlForm("cancel");
    failNextRelease = true;
    await click("Pause background search");
    await until(async () => (await text()).includes("Resume background search"), "Pause state did not render");
    process.stdout.write("finder-ui: paused\n");
    await click("Resume background search");
    await until(async () => (await text()).includes("Pause background search"), "Resume state did not render");
    process.stdout.write("finder-ui: resumed\n");
    await click("Skip background signal");
    await until(async () => events.includes("skip"), "Skip command not sent");
    process.stdout.write("finder-ui: skipped\n");
    await until(async () => await evaluate(`![...document.querySelectorAll('[aria-label="Background shortwave finder"] button')].some(b => b.disabled && b.textContent === 'Listen live')`), "UI remained busy");
    await click("Listen live");
    await until(async () => (await text()).includes("Receiver release failed"), "Failed handoff must be visible");
    assert.ok(!events.includes("rtl-connected"), "A failed handoff must not open the bridge");
    await click("Listen live");
    process.stdout.write("finder-ui: listen requested\n");
    await until(async () => {
      const sessions = await broker.sessions() as Array<{ id: string; url: string; state: { frequencyHz: number; offsetHz: number; connection: string; bandwidthHz: number } }>;
      const session = sessions.find((entry) => entry.url.includes(marker));
      sessionId = session?.id ?? "";
      return session?.state.connection === "rtltcp" && session.state.frequencyHz + session.state.offsetHz === 5950000 && session.state.bandwidthHz === 4000;
    }, "Listen live did not connect and tune the synthetic RTL stream");
    assert.ok(events.indexOf("release") < events.indexOf("rtl-connected"), "The receiver must be released before opening a listening stream");
    process.stdout.write("finder-ui: live tuning verified\n");
    await until(async () => events.includes("cleaner-upload"), "Listen live did not automatically upload raw audio for backend cleanup");
    await until(async () => await evaluate(`document.querySelector('[aria-label="Autonomous signal finder"] [aria-label="Adaptive signal cleaning"]')?.textContent.includes('Balanced')`), "Returned cleaner state did not reach the local finder UI");
    assert.ok(await evaluate(`document.querySelector('[aria-label="Autonomous signal finder"] [aria-label="Active processing location"]')?.textContent.includes('Fixture GPU')`));
    const diagnostics = await broker.command("get_diagnostics", {}, sessionId) as { backendCleanActive: boolean; audio: { contextState: string } };
    assert.equal(diagnostics.backendCleanActive, true, "Cleaned return audio must replace ordinary local playback automatically");
    assert.equal(diagnostics.audio.contextState, "running");
    await broker.command("tune", { frequencyHz: 6000000, center: true }, sessionId);
    const manual = await broker.command("get_diagnostics", {}, sessionId) as { backendCleanActive: boolean; cleaner: { status: string } };
    assert.equal(manual.backendCleanActive, false, "Manual tuning must cancel backend playback ownership");
    assert.equal(manual.cleaner.status, "stopped");
    await until(async () => await evaluate(`document.querySelector('[aria-label="Autonomous signal finder"] [aria-label="LAN processing worker status"]')?.textContent.includes('worker ready')`), "Idle LAN worker health did not render");
    await wait(300);
    const uploadsAfterStop = events.filter((event) => event === "cleaner-upload").length;
    await wait(300);
    assert.equal(events.filter((event) => event === "cleaner-upload").length, uploadsAfterStop, "Manual tuning must stop cleaner upload");
    // Findings remain visible after switching panels and losing the service heartbeat.
    await evaluate(`document.getElementById('rail-tab-stations').click()`);
    await until(async () => (await text()).includes("5.9500 MHz"), "Stations did not show background results");
    view = { ...view, online: false, stale: true, at: new Date(Date.now() - 90000).toISOString() };
    await click("Refresh findings");
    await until(async () => (await text()).includes("Background finder is offline"), "Offline state missing");
    assert.ok((await text()).includes("5.9500 MHz"));
    assert.ok((await text()).includes("Updates stopped"));
    await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    assert.equal(await evaluate("document.documentElement.scrollWidth > window.innerWidth"), false, "Mobile layout overflows horizontally");
    await cdp("Emulation.setDeviceMetricsOverride", { width: 1400, height: 1000, deviceScaleFactor: 1, mobile: false });
    const screenshot = await cdp("Page.captureScreenshot", { format: "png" });
    const output = resolve("../../.runtime/autofinder-service-ui-smoke.png");
    await mkdir(resolve("../../.runtime"), { recursive: true, mode: 0o700 });
    await writeFile(output, Uint8Array.from(Buffer.from(screenshot.data as string, "base64")));
    // Exercise the actual Next route (not the fixture proxy) with a browser
    // Origin of 127.0.0.1, while Next internally normalizes it to localhost.
    await broker.command("stop", {}, sessionId);
    await cdp("Fetch.disable");
    await rtlForm("open");
    await rtlForm("connect");
    await until(async () => {
      const state = await broker.command("get_state", {}, sessionId) as { status: string; connection: string };
      return state.status === "playing" && state.connection === "rtltcp";
    }, "Connect IQ failed through the actual same-origin handoff route");
    passed = true;
    process.stdout.write(`Background finder UI passed: findings, pause/resume/skip, release-before-listen, controller tuning, cleaned/original PCM playback, automatic backend upload/return, manual takeover, panel changes, offline/stale, mobile layout. Synthetic data only. Screenshot: ${output}\n`);
  } finally {
    if (!passed && browserErrors) process.stderr.write(browserErrors);
    if (sessionId) await broker.command("stop", {}, sessionId).catch(() => {});
    debug?.close(); browser.kill("SIGTERM");
    for (let i = 0; i < 30 && browser.exitCode === null; i++) await wait(100);
    if (browser.exitCode === null) browser.kill("SIGKILL");
    for (const client of fakeRtl.clients) client.terminate();
    await new Promise<void>((done) => fakeRtl.close(() => done()));
    await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
}

void main().catch((error) => { process.stderr.write(`${error instanceof Error ? error.stack : error}\n`); process.exitCode = 1; });
