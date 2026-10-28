/**
 * Smoke: programmable scanner banks + engine hop / hold logic.
 * Run: pnpm exec tsx scripts/scanner-smoke.ts
 */

import { createDefaultBanks, scanableChannels } from "../src/lib/sdr/scanner/banks";
import { ScannerEngine } from "../src/lib/sdr/scanner/engine";
import { mergeBanks } from "../src/lib/sdr/scanner/persist";
import type { DemodScheme } from "../src/lib/sdr/types";

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(msg);
}

async function main() {
  const banks = createDefaultBanks();
  assert(banks.some((b) => b.id === "gmrs"), "gmrs bank");
  assert(banks.some((b) => b.id === "wx"), "wx bank");
  assert(banks.some((b) => b.id === "custom"), "custom bank");

  const gmrs = banks.find((b) => b.id === "gmrs")!;
  assert(gmrs.channels.length === 22, "gmrs 1–22");
  assert(scanableChannels(gmrs).length === 22, "all scanable initially");

  const merged = mergeBanks([
    {
      id: "gmrs",
      name: "GMRS 1–22",
      builtin: true,
      channels: gmrs.channels.map((c, i) =>
        i === 0 ? { ...c, lockedOut: true } : c,
      ),
    },
    {
      id: "custom",
      name: "Custom",
      builtin: false,
      channels: [
        {
          id: "user-1",
          label: "Mine",
          frequencyHz: 146_520_000,
          scheme: "NBFM" as DemodScheme,
          lockedOut: false,
        },
      ],
    },
  ]);

  const mergedGmrs = merged.find((b) => b.id === "gmrs")!;
  assert(mergedGmrs.channels[0].lockedOut, "lockout preserved");
  assert(
    merged
      .find((b) => b.id === "custom")!
      .channels.some((c) => c.id === "user-1"),
    "custom channels preserved",
  );

  const engine = new ScannerEngine();
  const tuned: number[] = [];
  let squelchOpen = false;
  let squelchActive = true;

  engine.setRadioHooks({
    setFrequency: async (hz) => {
      tuned.push(hz);
    },
    setScheme: () => {},
    setBandwidth: () => {},
    setOffset: () => {},
    getSquelchOpen: () => squelchOpen,
    getSquelchActive: () => squelchActive,
    isConnected: () => true,
  });

  engine.setActiveBank("wx");
  await engine.start();
  assert(engine.getState().status === "scanning", "scanning");
  assert(engine.getState().currentChannelId !== null, "on a channel");
  assert(tuned.length >= 1, "tuned at least once");

  const firstHz = tuned[0];
  await engine.skip();
  assert(tuned.length >= 2, "skip advanced");
  assert(tuned[tuned.length - 1] !== firstHz, "moved to next channel");

  squelchOpen = true;
  await new Promise((r) => setTimeout(r, 150));
  assert(engine.getState().status === "held", "held on activity");
  assert(engine.getState().holdReason === "activity", "activity reason");

  engine.stop();
  assert(engine.getState().status === "idle", "stopped");

  engine.dispose();
  console.log("scanner-smoke: ok");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
