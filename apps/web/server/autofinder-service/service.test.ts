import assert from "node:assert/strict";
import test from "node:test";

import { AutofinderService, type ServiceStatus } from "./service";
import type { SdrState } from "../../src/lib/sdr/types";
import { INITIAL_FINDER_STATE, type FinderState } from "../../src/lib/sdr/autofinder/engine";
import { parseServiceControl } from "../../src/lib/sdr/autofinder/service-types";

const radio = (connection: SdrState["connection"] = "disconnected", status: SdrState["status"] = "idle") => ({ connection, status } as SdrState);

test("connects real RTL and starts the autonomous finder", async () => {
  let state = radio(); let finder = INITIAL_FINDER_STATE; const calls: string[] = []; const reports: ServiceStatus[] = [];
  const broker = {
    sessions: async () => [{ id: "agent", url: "http://local/?receiverAgent=1", state }],
    command: async (action: string, args: Record<string, unknown>) => {
      calls.push(`${action}:${String(args.url ?? "")}`);
      if (action === "get_state") return state;
      if (action === "connect") return state = radio("rtltcp", "playing");
      if (action === "autofinder_state") return finder;
      if (action === "autofinder_start") return finder = { ...INITIAL_FINDER_STATE, status: "searching", message: "Searching HF" } as FinderState;
      return {};
    },
  };
  const service = new AutofinderService(broker as never, "http://rtl:8081", async (report) => { reports.push(report); });
  assert.equal(await service.tick(), "scanning");
  assert.deepEqual(calls, ["get_state:", "connect:http://rtl:8081", "autofinder_state:", "autofinder_start:"]);
  assert.equal(reports.at(-1)?.finder?.status, "searching");
  assert.equal(reports.at(-1)?.receiver?.connection, "rtltcp");
});

test("yields a service-owned device to an interactive receiver", async () => {
  const state = radio("rtltcp", "playing"); const calls: string[] = [];
  let sessions = [{ id: "agent", url: "http://local/?receiverAgent=1", state }];
  const broker = {
    sessions: async () => sessions,
    command: async (action: string) => {
      calls.push(action);
      if (action === "get_state") return state;
      if (action === "autofinder_state") return { ...INITIAL_FINDER_STATE, status: "searching" };
      if (action === "autofinder_start") return { ...INITIAL_FINDER_STATE, status: "searching" };
      return {};
    },
  };
  const service = new AutofinderService(broker as never, "http://rtl:8081", async () => undefined);
  await service.tick();
  sessions = [...sessions, { id: "person", url: "http://local/", state }];
  assert.equal(await service.tick(), "yielding");
  assert.deepEqual(calls.slice(-2), ["autofinder_stop", "stop"]);
});

test("a dedicated browser's old demo detections are not published as real findings", async () => {
  let state = radio("demo", "playing");
  let finder: FinderState = { ...INITIAL_FINDER_STATE, recent: [{ at: new Date().toISOString(), frequencyHz: 5950000, accepted: true, speechRatio: 1 }] };
  const reports: ServiceStatus[] = [];
  const broker = {
    sessions: async () => [{ id: "agent", url: "http://local/?receiverAgent=1", state }],
    command: async (action: string) => {
      if (action === "get_state") return state;
      if (action === "connect") return state = radio("rtltcp", "playing");
      if (action === "autofinder_start") return finder = { ...INITIAL_FINDER_STATE, status: "searching" };
      return finder;
    },
  };
  const service = new AutofinderService(broker as never, "http://rtl:8081", async (report) => { reports.push(report); });
  await service.tick();
  assert.ok(reports.every((report) => !report.finder?.recent.length));
});

test("gain commands validate without coercion", () => {
  assert.deepEqual(parseServiceControl({ action: "gain", autoGain: false, gainDb: 50 }), { action: "gain", autoGain: false, gainDb: 50 });
  assert.deepEqual(parseServiceControl({ action: "gain", autoGain: true }), { action: "gain", autoGain: true });
  for (const gainDb of [-1, 51, NaN, Infinity, null, "50", true, undefined]) {
    assert.equal(parseServiceControl({ action: "gain", autoGain: false, gainDb }), null);
  }
  assert.equal(parseServiceControl({ action: "gain", autoGain: true, gainDb: 50 }), null);
  assert.equal(parseServiceControl({ action: "gain", autoGain: "true" }), null);
});

test("manual and Auto gain apply without restarting scans, queue during takeover, and reapply on reconnect", async () => {
  let state = { ...radio("rtltcp", "playing"), autoGain: false, gainDb: 50 };
  let competing = false;
  const calls: Array<{ action: string; args: Record<string, unknown>; id?: string }> = [];
  const reports: ServiceStatus[] = [];
  const broker = {
    sessions: async () => [ { id: "agent", url: "http://local/?receiverAgent=1", state },
      ...(competing ? [{ id: "person", url: "http://local/", state: radio("rtltcp", "playing") }] : []) ],
    command: async (action: string, args: Record<string, unknown>, id?: string) => {
      calls.push({ action, args, id });
      if (action === "connect") state = { ...state, status: "playing", connection: "rtltcp", autoGain: true, gainDb: 0 };
      if (action === "configure") state = { ...state, ...args };
      if (action === "stop") state = { ...state, status: "idle", connection: "disconnected" };
      if (["get_state", "connect", "configure"].includes(action)) return state;
      return { ...INITIAL_FINDER_STATE, status: "searching" };
    },
  };
  const service = new AutofinderService(broker as never, "http://rtl:8081", async (status) => { reports.push(status); });
  await service.tick();
  assert.deepEqual(service.gain, { autoGain: false, gainDb: 50 }, "Adopt existing manual gain on upgrade");
  calls.length = 0;
  await service.control({ action: "gain", autoGain: false, gainDb: 42.5 });
  assert.equal(state.gainDb, 42.5);
  assert.deepEqual(calls.filter(c => c.action === "configure").map(c => c.args), [{ autoGain: false, gainDb: 42.5 }]);
  assert.ok(!calls.some(c => ["stop", "connect", "autofinder_start", "autofinder_stop"].includes(c.action)));
  calls.length = 0;
  await service.tick();
  assert.ok(!calls.some(c => c.action === "configure"), "Do not reset adaptive gain on every heartbeat");
  await service.control({ action: "gain", autoGain: true });
  assert.equal(state.autoGain, true);
  competing = true;
  await service.tick();
  calls.length = 0;
  await service.control({ action: "gain", autoGain: false, gainDb: 50 });
  assert.ok(!calls.some(c => c.action === "configure" || c.action === "connect"));
  assert.deepEqual(reports.at(-1)?.gain, { autoGain: false, gainDb: 50 });
  competing = false;
  await service.tick();
  assert.equal(state.gainDb, 50); assert.equal(state.autoGain, false);
  assert.ok(calls.every(c => c.id === "agent"), "Never configure the interactive receiver");
  await service.control("pause");
  calls.length = 0;
  await service.control({ action: "gain", autoGain: true });
  assert.equal(service.enabled, false);
  assert.ok(!calls.some(c => c.action === "configure" || c.action === "connect"));
  assert.deepEqual(reports.at(-1)?.gain, { autoGain: true });
  await service.control("resume");
  assert.equal(state.autoGain, true);
});
