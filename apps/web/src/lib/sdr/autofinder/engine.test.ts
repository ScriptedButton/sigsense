import assert from "node:assert/strict";
import { test } from "node:test";
import { AutonomousFinder, type FinderHooks, type SpeechAssessment } from "./engine";
import { automaticSearchPlan, canTry, emptyMemory, rejectCandidate } from "./policy";
import type { SdrState } from "../types";

const pause = (ms = 2) => new Promise((done) => setTimeout(done, ms));
async function until(predicate: () => boolean) { for (let i = 0; i < 500; i++) { if (predicate()) return; await pause(); } throw new Error("Timed out"); }
const assessment = (speechLikely: boolean): SpeechAssessment => ({ speechLikely, speechRatio: speechLikely ? .8 : 0, model: "test neural detector", intelligibilityConfirmed: false });
function setup(assess: FinderHooks["assess"] = async () => assessment(false), quiet = 4) {
  const state = { status: "playing", connection: "rtltcp", frequencyHz: 9_490_000, offsetHz: 0, sampleRate: 1_024_000, scheme: "AM" } as SdrState;
  let muted = false, restores = 0, captured = 0, assessed = 0;
  const hooks: FinderHooks = {
    state: () => state,
    spectrum: () => {
      const binHz = 250, startHz = state.frequencyHz - 512000, values = Array(4096).fill(-100);
      const at = Math.round((5950000 - startHz) / binHz);
      if (at > 100 && at < values.length - 100) {
        values[at] = -70; for (let k = -10; k <= 10; k++) if (Math.abs(k) >= 2) values[at + k] = -87;
      }
      return { values, startHz, binHz, centerHz: state.frequencyHz, sampleRate: state.sampleRate, centerOffsetHz: 0, capturedAt: new Date().toISOString() };
    },
    tune: async (center, offset) => { state.frequencyHz = center; state.offsetHz = offset; },
    prepare: async () => {}, mute: (value) => { muted = value; },
    capture: async () => { captured++; return { data: "test" }; },
    assess: async (...args) => { assessed++; return assess(...args); }, ready: async () => {},
    restore: async (original, dial) => { restores++; if (dial) Object.assign(state, original); },
  };
  const engine = new AutonomousFinder(hooks, { settle: 2, poll: 1, quiet, retry: 2 }, emptyMemory());
  return { engine, hooks, state, counts: () => ({ muted, restores, captured, assessed }) };
}

test("automatic plan covers all HF without user channel lists and both LO views cover each tile", () => {
  for (const rate of [250000, 1024000, 2400000]) {
    const plan = automaticSearchPlan(rate), sorted = [...plan].sort((a, b) => a.lowHz - b.lowHz);
    assert.equal(plan[0].label, "49 m");
    assert.equal(sorted[0].lowHz, 2300000); assert.equal(sorted.at(-1)!.highHz, 30000000);
    for (let i = 0; i < sorted.length; i++) {
      const tile = sorted[i];
      if (i) assert.ok(Math.abs(sorted[i - 1].highHz - tile.lowHz) < 1);
      for (const center of [tile.centerHz, tile.centerHz + 100000]) {
        assert.ok(center - rate / 2 <= tile.lowHz); assert.ok(center + rate / 2 >= tile.highHz);
      }
    }
  }
});
test("rejected and skipped frequencies expire rather than disappearing forever", () => {
  const memory = emptyMemory(); rejectCandidate(memory, 5950000, 1000);
  assert.equal(canTry(memory, 5950125, 1001), false); assert.equal(canTry(memory, 5950000, 181001), true);
  rejectCandidate(memory, 5950000, 1000, true); assert.equal(canTry(memory, 5950000, 181001), false);
  assert.equal(canTry(memory, 5950000, 901001), true);
});
test("requires two independent speech-positive samples before unmuting and restores the original dial", async () => {
  const { engine, state, counts } = setup(async () => assessment(true));
  engine.start(); engine.start();
  await until(() => engine.getState().status === "listening");
  assert.equal(counts().assessed, 2); assert.equal(counts().muted, false);
  assert.equal(state.frequencyHz + state.offsetHz, 5950000);
  await engine.stop();
  assert.equal(state.frequencyHz, 9490000); assert.equal(state.offsetHz, 0);
  assert.equal(engine.getState().status, "idle"); assert.equal(counts().restores, 1);
});
test("RF peaks without neural speech do not unmute or stop continuous searching", async () => {
  const { engine, counts } = setup(); engine.start();
  await until(() => engine.getState().recent.length > 0);
  assert.equal(engine.getState().recent[0].accepted, false); assert.equal(counts().muted, true);
  await until(() => engine.getState().tilesScanned > 1); await engine.stop();
});
test("bounded service dwell resumes discovery even while speech continues", async () => {
  const { engine } = setup(async () => assessment(true));
  engine.start(12);
  await until(() => engine.getState().status === "listening");
  assert.equal(engine.getState().recent[0].band, "49 m");
  assert.equal(engine.getState().recent[0].bandwidthHz, 6000);
  await until(() => engine.getState().tilesScanned > 1);
  await engine.stop();
});
test("speech must persist in the second check; a single positive is not accepted", async () => {
  let calls = 0; const { engine, counts } = setup(async () => assessment(++calls === 1)); engine.start();
  await until(() => engine.getState().recent.length > 0);
  assert.equal(counts().assessed, 2); assert.equal(engine.getState().recent[0].accepted, false);
  await engine.stop();
});
test("fading speech resumes scouting, and stay/skip remains under user control", async () => {
  let calls = 0; const { engine, counts } = setup(async () => assessment(++calls <= 2)); engine.start();
  await until(() => engine.getState().status === "listening"); engine.keep();
  await pause(20); assert.equal(counts().assessed, 2); assert.equal(engine.getState().status, "keeping");
  engine.resume(); await until(() => engine.getState().tilesScanned > 1);
  assert.ok(counts().assessed >= 4); await engine.stop();
});
test("stop during an outstanding capture cannot later unmute or run neural assessment", async () => {
  const { engine, hooks, counts } = setup(async () => assessment(true));
  let release!: (value: { data: string }) => void;
  hooks.capture = () => new Promise((done) => { release = done; }); engine.start();
  await until(() => Boolean(release)); const stopped = engine.stop(); release({ data: "late" }); await stopped;
  assert.equal(counts().assessed, 0); assert.equal(engine.getState().status, "idle"); assert.equal(counts().muted, false);
});
test("skip wakes a long dwell immediately instead of waiting for the monitor timer", async () => {
  const { engine } = setup(async () => assessment(true), 5000); engine.start();
  await until(() => engine.getState().status === "listening"); engine.keep(); engine.skip();
  await until(() => engine.getState().tilesScanned > 1); await engine.stop();
});
test("stay requested during an in-flight quiet check cannot be overridden by its late result", async () => {
  let assessments = 0;
  const { engine, hooks } = setup(async () => assessment(++assessments <= 2));
  let captures = 0, release: ((value: { data: string }) => void) | null = null;
  hooks.capture = async () => {
    if (++captures === 4) return new Promise((done) => { release = done; });
    return { data: "test" };
  };
  engine.start(); await until(() => Boolean(release)); engine.keep();
  (release! as (value: { data: string }) => void)({ data: "late quiet check" });
  await pause(20); assert.equal(engine.getState().status, "keeping"); assert.equal(captures, 4);
  await engine.stop();
});
test("manual tuning does not get overwritten by delayed restore, and missing AI stops honestly", async () => {
  const { engine, state, counts } = setup(); engine.start(); await until(() => engine.getState().tilesScanned > 0);
  const stopped = engine.stop(false, true); state.frequencyHz = 10000000; await stopped;
  assert.equal(counts().restores, 0); assert.equal(state.frequencyHz, 10000000);
  const other = setup(); other.hooks.ready = async () => { throw new Error("AI unavailable"); }; other.engine.start();
  await until(() => !other.engine.active); assert.equal(other.engine.getState().status, "error"); assert.equal(other.counts().muted, false);
});
