import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { WebSocket } from "ws";

import { createReceiverBroker } from "./broker";

async function fixture(defaultTimeoutMs = 100) {
  const directory = mkdtempSync(join(tmpdir(), "receiver-broker-"));
  const configPath = join(directory, "control.json");
  const broker = createReceiverBroker({ host: "127.0.0.1", port: 0, token: "test-token", configPath, defaultTimeoutMs });
  const { port } = await broker.listen();
  const base = `http://127.0.0.1:${port}`;
  return { broker, port, base, configPath };
}

function openReceiver(port: number, token = "test-token", origin = "http://127.0.0.1:3000"): Promise<WebSocket> {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(`ws://127.0.0.1:${port}/receiver?token=${encodeURIComponent(token)}`, { origin });
    socket.once("open", () => resolve(socket));
    socket.once("error", reject);
  });
}

function nextJson(socket: WebSocket): Promise<Record<string, unknown>> {
  return new Promise((resolve) => socket.once("message", (raw) => resolve(JSON.parse(raw.toString()))));
}

async function hello(socket: WebSocket, sessionId: string) {
  const received = nextJson(socket);
  socket.send(JSON.stringify({ type: "hello", sessionId, state: { frequencyHz: 7_1e6 }, url: "http://127.0.0.1:3000/", title: "Radio" }));
  assert.deepEqual(await received, { type: "welcome", sessionId });
}

test("health is public but session data requires bearer auth", async () => {
  const f = await fixture();
  try {
    const health = await fetch(`${f.base}/health`);
    assert.equal(health.status, 200);
    assert.deepEqual(await health.json(), { ok: true, version: 1 });
    const denied = await fetch(`${f.base}/sessions`);
    assert.equal(denied.status, 401);
    const allowed = await fetch(`${f.base}/sessions`, { headers: { authorization: "Bearer test-token" } });
    assert.deepEqual(await allowed.json(), { sessions: [] });
    assert.equal(statSync(f.configPath).mode & 0o777, 0o600);
    assert.equal(JSON.parse(readFileSync(f.configPath, "utf8")).token, "test-token");
  } finally {
    await f.broker.close();
  }
});

test("websocket rejects bad tokens and cross-origin browsers", async () => {
  const f = await fixture();
  try {
    await assert.rejects(openReceiver(f.port, "wrong"));
    await assert.rejects(openReceiver(f.port, "test-token", "http://evil.example:3000"));
    const socket = await openReceiver(f.port);
    socket.terminate();
  } finally {
    await f.broker.close();
  }
});

test("routes correlated commands and exposes current telemetry", async () => {
  const f = await fixture();
  const socket = await openReceiver(f.port);
  try {
    await hello(socket, "one");
    socket.send(JSON.stringify({ type: "telemetry", state: { frequencyHz: 7_100_000 }, diagnostics: { clipping: 0 } }));

    const commandSeen = new Promise<void>((resolve) => socket.once("message", (raw) => {
      const command = JSON.parse(raw.toString());
      assert.equal(command.type, "command");
      assert.equal(command.action, "tune");
      assert.deepEqual(command.args, { frequencyHz: 7_200_000 });
      socket.send(JSON.stringify({ type: "result", id: command.id, result: { frequencyHz: 7_200_000 } }));
      resolve();
    }));
    const response = await fetch(`${f.base}/command`, {
      method: "POST",
      headers: { authorization: "Bearer test-token", "content-type": "application/json" },
      body: JSON.stringify({ action: "tune", args: { frequencyHz: 7_200_000 } }),
    });
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { result: { frequencyHz: 7_200_000 } });
    await commandSeen;

    const sessions = await fetch(`${f.base}/sessions`, { headers: { authorization: "Bearer test-token" } });
    const body = await sessions.json() as { sessions: Array<{ id: string; diagnostics: unknown }> };
    assert.equal(body.sessions[0]?.id, "one");
    assert.deepEqual(body.sessions[0]?.diagnostics, { clipping: 0 });
  } finally {
    socket.terminate();
    await f.broker.close();
  }
});

test("requires selection with concurrent sessions and fails pending work on timeout or disconnect", async () => {
  const f = await fixture(40);
  const one = await openReceiver(f.port);
  const two = await openReceiver(f.port);
  try {
    await hello(one, "one");
    await hello(two, "two");
    const headers = { authorization: "Bearer test-token", "content-type": "application/json" };
    const ambiguous = await fetch(`${f.base}/command`, { method: "POST", headers, body: JSON.stringify({ action: "get_state" }) });
    assert.equal(ambiguous.status, 409);
    assert.equal((await ambiguous.json()).error.code, "session_required");

    const timed = fetch(`${f.base}/command`, { method: "POST", headers, body: JSON.stringify({ sessionId: "one", action: "get_state", timeoutMs: 20 }) });
    await nextJson(one);
    const timedResponse = await timed;
    assert.equal(timedResponse.status, 504);
    assert.equal((await timedResponse.json()).error.code, "command_timeout");

    const disconnected = fetch(`${f.base}/command`, { method: "POST", headers, body: JSON.stringify({ sessionId: "two", action: "get_state" }) });
    await nextJson(two);
    two.terminate();
    const disconnectedResponse = await disconnected;
    assert.equal(disconnectedResponse.status, 502);
    assert.equal((await disconnectedResponse.json()).error.code, "session_disconnected");
  } finally {
    one.terminate();
    two.terminate();
    await f.broker.close();
  }
});
