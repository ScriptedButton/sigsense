import assert from "node:assert/strict";
import test from "node:test";

import { AutofinderService, type ServiceStatus } from "./service";
import type { SdrState } from "../../src/lib/sdr/types";
import { INITIAL_FINDER_STATE, type FinderState } from "../../src/lib/sdr/autofinder/engine";

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
