import type { SampleGenerator } from "@jtarrio/signals/sources/generated.js";

import { encodeMorseUnits } from "./alphabet";
import { DEMO_MORSE_FREQUENCY_HZ, ditSeconds } from "./types";

const DEMO_MESSAGE = "CQ CQ DE SDRSENSE K ";
const DEMO_WPM = 20;

/**
 * On-off keyed CW carrier for Demo mode.
 * Tune CW to the demo frequency — the demod sidetone is ~600 Hz keyed Morse.
 */
export function createDemoMorseGenerator(amplitude = 0.06): SampleGenerator {
  const units = encodeMorseUnits(DEMO_MESSAGE);
  const ditSec = ditSeconds(DEMO_WPM);
  const periodSec = units.length * ditSec;

  return (sample, rate, _center, I, Q) => {
    for (let i = 0; i < I.length; i++) {
      const t = ((sample + i) / rate) % periodSec;
      const idx = Math.min(units.length - 1, Math.floor(t / ditSec));
      if (units[idx]) {
        const phase =
          (2 * Math.PI * DEMO_MORSE_FREQUENCY_HZ * (sample + i)) / rate;
        I[i] = amplitude * Math.cos(phase);
        Q[i] = amplitude * Math.sin(phase);
      } else {
        I[i] = 0;
        Q[i] = 0;
      }
    }
  };
}
