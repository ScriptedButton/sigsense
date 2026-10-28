import type { SampleGenerator } from "@jtarrio/signals/sources/generated.js";
import { modulateFM } from "@jtarrio/signals/sources/generators.js";

import { encodeBaudotBits } from "./baudot";
import { DEMO_FSK_FREQUENCY_HZ } from "./types";

const DEMO_MESSAGE =
  "RYRYRY CQ CQ DE SDRSENSE FSK DEMO 12345 THE QUICK BROWN FOX\n";

/**
 * Baseband ±1 square wave at `baud`, driven by a repeating mark/space bit list.
 * Used as the modulating signal for `modulateFM`.
 */
function bitBaseband(bits: number[], baud: number): SampleGenerator {
  const n = bits.length;
  return (sample, rate, _center, I, Q) => {
    for (let i = 0; i < I.length; i++) {
      const idx = Math.floor(((sample + i) * baud) / rate) % n;
      const bit = bits[idx < 0 ? idx + n : idx]!;
      I[i] = bit ? 1 : -1;
      Q[i] = 0;
    }
  };
}

/** Simulated RTTY (45.45 baud, 170 Hz shift) for Demo mode. */
export function createDemoFskGenerator(
  amplitude = 0.09,
): SampleGenerator {
  const bits = encodeBaudotBits(DEMO_MESSAGE, 1.5);
  const shiftHz = 170;
  return modulateFM(
    DEMO_FSK_FREQUENCY_HZ,
    shiftHz / 2,
    amplitude,
    bitBaseband(bits, 45.45),
  );
}
