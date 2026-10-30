/** Bounded receive-only real RTL check. Releases a receiver opened by this test. */
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { ReceiverBrokerClient } from "../server/receiver-mcp/tools";
import type { FinderState } from "../src/lib/sdr/autofinder/engine";
import type { SdrState } from "../src/lib/sdr/types";

async function main() {
  const broker = new ReceiverBrokerClient();
  const receivers = await broker.sessions() as Array<{ id: string; url: string; state: SdrState }>;
  const receiver = receivers.find((value) => value.url.includes("receiverAgent=1"));
  assert.ok(receiver, "Dedicated receiver browser is not paired");
  assert.ok(!receivers.some((s) => s.id !== receiver.id && s.state.connection === "rtltcp" && s.state.status === "playing"), "User is listening through RTL; refusing to interrupt");
  const original = receiver.state;
  const command = (action: Parameters<typeof broker.command>[0]) => broker.command(action, {}, receiver.id, 25000);
  const progress: FinderState[] = [];
  try {
    await command("autofinder_start");
    const deadline = Date.now() + 65000;
    while (Date.now() < deadline) {
      await new Promise((done) => setTimeout(done, 2000));
      const state = await command("autofinder_state") as FinderState;
      progress.push(state);
      process.stdout.write(`${state.status}: ${state.band}, ${state.tilesScanned} tiles, ${state.candidatesChecked} audio checks\n`);
      assert.notEqual(state.status, "error", state.message);
      if (state.status === "listening" || (state.tilesScanned >= 4 && state.recent.length > 0)) break;
    }
    assert.ok(progress.some((state) => state.tilesScanned >= 1), "No autonomous RF scouting occurred");
    assert.ok(progress.some((state) => state.recent.length > 0), "No real neural audio assessment completed");
  } finally {
    await command("autofinder_stop");
    const state = await command("get_state") as SdrState;
    // Finder snapshots after auto-connect, but dial and demodulation remain the original settings.
    assert.equal(state.frequencyHz, original.frequencyHz); assert.equal(state.offsetHz, original.offsetHz);
    assert.equal(state.scheme, original.scheme);
    if (original.status !== "playing") await command("stop");
    const directory = resolve("../../.runtime/autofinder"); await mkdir(directory, { recursive: true, mode: 0o700 });
    await writeFile(resolve(directory, "live-smoke.json"), JSON.stringify({ at: new Date().toISOString(), original, progress,
      restored: state, final: await command("get_state") }, null, 2), { mode: 0o600 });
  }
}
void main().catch((error) => { process.stderr.write(`${error}\n`); process.exitCode = 1; });
