import type { SampleGenerator } from "@jtarrio/signals/sources/generated.js";

import { extractFt8Symbols, SYMBOL_SEC, TONE_SPACING_HZ } from "./symbols";
import {
  DEMO_FT8_FREQUENCY_HZ,
  FT8_CYCLE_SEC,
  FT8_TX_SEC,
} from "./types";

const DEMO_MESSAGE = "CQ K1ABC FN42";
const AUDIO_BASE_HZ = 1_500;

/**
 * USB-style FT8 on the 20 m dial — hopping CW tones at dial + audio base.
 * Cycle timing uses wall-clock UTC so it lines up with the decoder's 15 s slots.
 */
export function createDemoFt8Generator(amplitude = 0.05): SampleGenerator {
  const symbols = extractFt8Symbols(DEMO_MESSAGE, AUDIO_BASE_HZ);

  return (sample, rate, _center, I, Q) => {
    const wall0 = Date.now() / 1000;
    for (let i = 0; i < I.length; i++) {
      const cycleT = (wall0 + i / rate) % FT8_CYCLE_SEC;
      if (cycleT >= FT8_TX_SEC || symbols.length === 0) {
        I[i] = 0;
        Q[i] = 0;
        continue;
      }
      const symIdx = Math.min(
        Math.floor(cycleT / SYMBOL_SEC),
        symbols.length - 1,
      );
      const audioHz = AUDIO_BASE_HZ + symbols[symIdx]! * TONE_SPACING_HZ;
      const rfHz = DEMO_FT8_FREQUENCY_HZ + audioHz;
      const phase = (2 * Math.PI * rfHz * (sample + i)) / rate;
      I[i] = amplitude * Math.cos(phase);
      Q[i] = amplitude * Math.sin(phase);
    }
  };
}
