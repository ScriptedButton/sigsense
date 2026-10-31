import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import type { AddressInfo } from "node:net";
import { FinderStore, emptySnapshot, readSnapshot, serviceView } from "./store";
import { AutofinderService } from "./service";
import { createServiceServer } from "./http";
import { GET, POST } from "../../src/app/api/autofinder-service/route";
import { INITIAL_FINDER_STATE, type FinderState } from "../../src/lib/sdr/autofinder/engine";
import type { SdrState } from "../../src/lib/sdr/types";
import type { ServiceStatus } from "../../src/lib/sdr/autofinder/service-types";

test("findings survive process restart, repeated polls, rejections, errors, and offline reads", async () => {
  const directory = await mkdtemp(join(tmpdir(), "sigsense-finder-store-"));
  try {
    const path = join(directory, "state.json");
    const store = new FinderStore(emptySnapshot(), path);
    const at = new Date().toISOString();
    const status: ServiceStatus = { at, status: "scanning", message: "Checking", sessionId: "agent", receiver: null, gain: { autoGain: false, gainDb: 50 },
      finder: { ...INITIAL_FINDER_STATE, recent: [
        { at, frequencyHz: 5950000, speechRatio: .8, accepted: true, band: "49 m", bandwidthHz: 4000 },
        { at, frequencyHz: 6100000, speechRatio: .05, accepted: false },
      ] } };
    await store.update(status, true); await store.update(status, true);
    assert.equal(store.snapshot.findings.length, 1);
    assert.equal(store.snapshot.findings[0].confirmations, 1);
    assert.equal(store.snapshot.findings[0].bandwidthHz, 4000);
    assert.equal(store.snapshot.checks.length, 2);
    const next = new FinderStore(await readSnapshot(path), path);
    await next.update({ ...status, at: new Date(Date.now() + 1000).toISOString(), finder: null, status: "error" }, false);
    const restored = await readSnapshot(path);
    assert.deepEqual(restored.findings, store.snapshot.findings);
    assert.equal(restored.enabled, false);
    assert.deepEqual(restored.gain, { autoGain: false, gainDb: 50 });
    assert.equal(serviceView(restored, Date.now() + 90_000).stale, true);
    assert.equal((await stat(path)).mode & 0o777, 0o600);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("UI API controls the running supervisor, releases before listening, and serves saved findings offline", async () => {
  const directory = await mkdtemp(join(tmpdir(), "sigsense-finder-api-"));
  const previous = { port: process.env.SIGSENSE_AUTOFINDER_PORT, path: process.env.SIGSENSE_AUTOFINDER_STATUS, token: process.env.SIGSENSE_RECEIVER_TOKEN_FILE };
  const path = join(directory, "state.json"), tokenFile = join(directory, "token.json");
  await writeFile(tokenFile, JSON.stringify({ token: "test-token" }));
  const store = new FinderStore(emptySnapshot(), path);
  let receiver = { status: "idle", connection: "disconnected" } as SdrState;
  let finder: FinderState = INITIAL_FINDER_STATE;
  const calls: string[] = [];
  const broker = {
    sessions: async () => [{ id: "agent", url: "http://localhost:3000/?receiverAgent=1", state: receiver }],
    command: async (action: string, args: Record<string, unknown>) => {
      calls.push(action);
      if (action === "connect") { assert.equal(args.type, "rtltcp"); receiver = { ...receiver, connection: "rtltcp", status: "playing" }; }
      if (action === "get_state" || action === "connect") return receiver;
      if (action === "configure") return receiver = { ...receiver, ...args };
      if (action === "autofinder_start") {
        assert.equal(args.maxListenMs, 30000);
        finder = { ...INITIAL_FINDER_STATE, status: "searching", message: "Searching 49 m" };
      }
      if (action === "autofinder_stop") finder = { ...finder, status: "idle" };
      if (action === "stop") receiver = { ...receiver, status: "idle", connection: "disconnected" };
      return finder;
    },
  };
  let now = Date.now();
  const service: AutofinderService = new AutofinderService(broker as never, "http://rtl:8081", (status): Promise<void> => store.update(status, service.enabled), () => now);
  const server = createServiceServer(async () => "test-token", () => ({ ...serviceView(store.snapshot), bridgeUrl: "http://127.0.0.1:8088" }), (action) => service.control(action));
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  const port = (server.address() as AddressInfo).port;
  process.env.SIGSENSE_AUTOFINDER_PORT = String(port);
  process.env.SIGSENSE_AUTOFINDER_STATUS = path;
  process.env.SIGSENSE_RECEIVER_TOKEN_FILE = tokenFile;
  const request = (action: string, origin = "http://localhost:3000") => new Request("http://localhost:3000/api/autofinder-service", {
    method: "POST", headers: { origin, "Content-Type": "application/json" }, body: JSON.stringify({ action }),
  });
  try {
    await service.tick();
    finder = { ...finder, status: "listening", frequencyHz: 5950000, recent: [{ at: new Date().toISOString(), frequencyHz: 5950000, speechRatio: .9, accepted: true, band: "49 m", bandwidthHz: 6000 }] };
    await service.tick();
    let response = await GET(new Request("http://localhost:3000/api/autofinder-service"));
    let body = await response.json();
    assert.equal(body.online, true); assert.equal(body.findings[0].frequencyHz, 5950000);
    assert.equal(body.bridgeUrl, "http://localhost:8088/");
    response = await GET(new Request("http://localhost:3000/api/autofinder-service", {
      headers: { host: "192.168.1.131:3000", origin: "http://192.168.1.131:3000", "sec-fetch-site": "same-origin" },
    }));
    assert.equal(response.status, 200);
    assert.equal((await response.json()).bridgeUrl, "http://192.168.1.131:8088/");
    assert.equal(response.headers.get("cache-control"), "no-store, private");
    assert.equal((await fetch(`http://127.0.0.1:${port}/status`)).status, 401);
    assert.equal((await POST(request("pause", "http://untrusted.test"))).status, 403);
    assert.equal((await POST(request("tune"))).status, 400);
    assert.equal((await POST(new Request("http://localhost:3000/api/autofinder-service", { method: "POST", headers: { "Content-Type": "application/json" }, body: "x".repeat(1025) }))).status, 413);
    assert.equal((await POST(request("skip"))).status, 200);
    assert.ok(calls.includes("autofinder_skip"));
    const gainRequest = (gain: unknown) => new Request("http://localhost:3000/api/autofinder-service", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(gain),
    });
    assert.equal((await POST(gainRequest({ action: "gain", autoGain: false, gainDb: 51 }))).status, 400);
    assert.equal((await POST(gainRequest({ action: "gain", autoGain: false, gainDb: 50 }))).status, 200);
    assert.equal(receiver.gainDb, 50); assert.equal(receiver.autoGain, false);
    assert.deepEqual((await readSnapshot(path)).gain, { autoGain: false, gainDb: 50 });
    assert.equal((await POST(gainRequest({ action: "gain", autoGain: true }))).status, 200);
    assert.equal(receiver.autoGain, true);
    assert.equal((await fetch(`http://127.0.0.1:${port}/control`, { method: "POST", headers: { Authorization: "Bearer test-token" }, body: JSON.stringify({ action: "gain", autoGain: false, gainDb: "50" }) })).status, 400);
    calls.length = 0;
    response = await POST(request("release"));
    assert.equal(response.status, 200);
    assert.ok(calls.indexOf("stop") > calls.indexOf("autofinder_stop"));
    assert.equal(receiver.status, "idle");
    assert.equal((await response.json()).status, "yielding");
    assert.equal((await POST(request("pause"))).status, 200);
    assert.equal((await readSnapshot(path)).enabled, false);
    await service.tick(); assert.equal(receiver.status, "idle");
    assert.equal((await POST(request("resume"))).status, 200);
    assert.equal(receiver.status, "playing");
    // Recovery must restart an errored finder and preserve the previously confirmed signal.
    finder = { ...finder, status: "error", message: "Temporary inference failure" };
    await service.tick(); assert.equal(store.snapshot.status, "error");
    now += 10_001;
    await service.tick(); assert.equal(finder.status, "searching");
    assert.equal(store.snapshot.findings.length, 1);
    await service.stop();
    await new Promise<void>((done) => server.close(() => done()));
    response = await GET(new Request("http://localhost:3000/api/autofinder-service"));
    body = await response.json();
    assert.equal(body.online, false); assert.equal(body.findings.length, 1);
    assert.equal((await POST(request("resume"))).status, 503);
    assert.ok((await readFile(path, "utf8")).includes("5950000"));
  } finally {
    server.closeAllConnections();
    if (server.listening) await new Promise<void>((done) => server.close(() => done()));
    for (const [key, value] of Object.entries({ SIGSENSE_AUTOFINDER_PORT: previous.port, SIGSENSE_AUTOFINDER_STATUS: previous.path, SIGSENSE_RECEIVER_TOKEN_FILE: previous.token })) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
    await rm(directory, { recursive: true, force: true });
  }
});

test("the daemon restores a persisted pause and shuts down cleanly on SIGTERM", async () => {
  const directory = await mkdtemp(join(tmpdir(), "sigsense-finder-process-"));
  const path = join(directory, "state.json"), tokenFile = join(directory, "token.json");
  await writeFile(path, JSON.stringify({ ...emptySnapshot(), enabled: false, gain: { autoGain: false, gainDb: 50 } }));
  const requests: string[] = [];
  const broker = createServer((request, response) => {
    requests.push(request.url ?? "");
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ sessions: [] }));
  });
  await new Promise<void>((done) => broker.listen(0, "127.0.0.1", done));
  const port = (broker.address() as AddressInfo).port;
  await writeFile(tokenFile, JSON.stringify({ token: "process-test-token", port }));
  const child = spawn(process.execPath, ["--import", "tsx", fileURLToPath(new URL("./main.ts", import.meta.url))], {
    env: { ...process.env, SIGSENSE_AUTOFINDER_STATUS: path, SIGSENSE_RECEIVER_TOKEN_FILE: tokenFile,
      SIGSENSE_RECEIVER_BROKER_URL: `http://127.0.0.1:${port}`, SIGSENSE_AUTOFINDER_PORT: "0" }, stdio: ["ignore", "ignore", "pipe"],
  });
  let errors = "";
  child.stderr.on("data", (chunk) => { errors += chunk.toString(); });
  const exited = new Promise<number | null>((done, reject) => { child.once("error", reject); child.once("exit", done); });
  try {
    let ready = false;
    for (let attempt = 0; attempt < 100; attempt++) {
      if ((await readSnapshot(path)).status === "paused") { ready = true; break; }
      if (child.exitCode !== null) throw new Error(`Daemon exited early: ${errors}`);
      await new Promise((done) => setTimeout(done, 25));
    }
    assert.ok(ready, `Daemon did not restore paused state: ${errors}`);
    assert.ok(requests.every((request) => request === "/sessions"), "A paused daemon must never issue hardware commands");
    child.kill("SIGTERM");
    assert.equal(await exited, 0, errors);
    const saved = await readSnapshot(path);
    assert.equal(saved.status, "stopped"); assert.equal(saved.enabled, false);
    assert.deepEqual(saved.gain, { autoGain: false, gainDb: 50 });
  } finally {
    if (child.exitCode === null) { child.kill("SIGKILL"); await exited.catch(() => {}); }
    await new Promise<void>((done) => broker.close(() => done()));
    await rm(directory, { recursive: true, force: true });
  }
});
