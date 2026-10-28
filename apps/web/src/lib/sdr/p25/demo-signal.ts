/**
 * Synthetic P25 Phase 1 C4FM for FakeRtl demo mode.
 * Emits a repeating TSDU with sync + NID + Group Voice Channel Grant.
 */

import type { SampleGenerator } from "@jtarrio/signals/sources/generated.js";
import { modulateFM } from "@jtarrio/signals/sources/generators.js";

import { dibitToSymbol } from "./c4fm";
import { encodeSyncAndNid } from "./framer";
import { encodeTsbkGrantDibits } from "./tsbk";
import { DEMO_P25_FREQUENCY_HZ, P25_SYMBOL_RATE } from "./types";

const DEMO_NAC = 0x293;
const DEMO_TGID = 1001;
const DEMO_SOURCE = 0x1a2b3c;
/** Channel: ident 0, number maps via demo IDEN base — use absolute-looking ch. */
const DEMO_CHANNEL = 0x012c; // ident 0, channel 300

function buildDemoDibits(): number[] {
  const syncNid = encodeSyncAndNid(DEMO_NAC, 0x7);
  // Pad NID BCH region (24 dibits) with zeros for simplified demo framing.
  const bchPad = new Array(24).fill(1);
  const tsbk = encodeTsbkGrantDibits({
    opcode: 0x00,
    tgid: DEMO_TGID,
    sourceId: DEMO_SOURCE,
    channel: DEMO_CHANNEL,
  });
  // Idle gap so framer can resync
  const gap = new Array(40).fill(1);
  return [...syncNid, ...bchPad, ...tsbk, ...gap];
}

function dibitBaseband(dibits: number[]): SampleGenerator {
  const n = dibits.length;
  return (sample, rate, _center, I, Q) => {
    for (let i = 0; i < I.length; i++) {
      const idx = Math.floor(((sample + i) * P25_SYMBOL_RATE) / rate) % n;
      const d = dibits[idx < 0 ? idx + n : idx]!;
      // Soft C4FM levels as "audio" modulating FM (deviation set below)
      I[i] = dibitToSymbol(d) / 3;
      Q[i] = 0;
    }
  };
}

/**
 * Simulated P25 C4FM (≈±1.8 kHz deviation levels) at the demo frequency.
 * Tune NBFM there and enable Trunk decode.
 */
export function createDemoP25Generator(amplitude = 0.08): SampleGenerator {
  const dibits = buildDemoDibits();
  // Peak deviation ≈ 1.8 kHz for ±3 symbols → maxF ~ 1800
  return modulateFM(
    DEMO_P25_FREQUENCY_HZ,
    1_800,
    amplitude,
    dibitBaseband(dibits),
  );
}
