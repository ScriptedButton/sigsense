import { encodeFT8 } from "@e04/ft8ts";

import { FT8_DECODE_RATE, FT8_TX_SEC } from "./types";

const SYMBOL_SEC = 0.16;
const TONE_SPACING_HZ = 6.25;

/**
 * Recover the 79 FT8 channel symbols from an encoded audio waveform by
 * correlating each symbol period against the eight expected tones.
 */
export function extractFt8Symbols(
  message: string,
  baseFrequencyHz: number,
): number[] {
  const audio = encodeFT8(message, {
    sampleRate: FT8_DECODE_RATE,
    baseFrequency: baseFrequencyHz,
  });
  const samplesPerSymbol = Math.floor(FT8_DECODE_RATE * SYMBOL_SEC);
  const nSymbols = Math.min(
    79,
    Math.floor(audio.length / samplesPerSymbol),
  );
  const symbols = new Array<number>(nSymbols);

  for (let s = 0; s < nSymbols; s++) {
    const start = s * samplesPerSymbol;
    let bestTone = 0;
    let bestPower = -1;
    for (let tone = 0; tone < 8; tone++) {
      const f = baseFrequencyHz + tone * TONE_SPACING_HZ;
      let re = 0;
      let im = 0;
      for (let i = 0; i < samplesPerSymbol; i++) {
        const x = audio[start + i]!;
        const ph = (2 * Math.PI * f * i) / FT8_DECODE_RATE;
        re += x * Math.cos(ph);
        im += x * Math.sin(ph);
      }
      const power = re * re + im * im;
      if (power > bestPower) {
        bestPower = power;
        bestTone = tone;
      }
    }
    symbols[s] = bestTone;
  }

  return symbols;
}

export { SYMBOL_SEC, TONE_SPACING_HZ, FT8_TX_SEC };
