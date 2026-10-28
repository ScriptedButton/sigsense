/**
 * International Morse (ITU) letter ↔ element maps.
 * `.` = dit, `-` = dah. Prosigns use concatenated letters without spaces.
 */

export const CHAR_TO_MORSE: Record<string, string> = {
  A: ".-",
  B: "-...",
  C: "-.-.",
  D: "-..",
  E: ".",
  F: "..-.",
  G: "--.",
  H: "....",
  I: "..",
  J: ".---",
  K: "-.-",
  L: ".-..",
  M: "--",
  N: "-.",
  O: "---",
  P: ".--.",
  Q: "--.-",
  R: ".-.",
  S: "...",
  T: "-",
  U: "..-",
  V: "...-",
  W: ".--",
  X: "-..-",
  Y: "-.--",
  Z: "--..",
  "0": "-----",
  "1": ".----",
  "2": "..---",
  "3": "...--",
  "4": "....-",
  "5": ".....",
  "6": "-....",
  "7": "--...",
  "8": "---..",
  "9": "----.",
  ".": ".-.-.-",
  ",": "--..--",
  "?": "..--..",
  "'": ".----.",
  "!": "-.-.--",
  "/": "-..-.",
  "(": "-.--.",
  ")": "-.--.-",
  "&": ".-...",
  ":": "---...",
  ";": "-.-.-.",
  "=": "-...-",
  "+": ".-.-.",
  "-": "-....-",
  _: "..--.-",
  '"': ".-..-.",
  $: "...-..-",
  "@": ".--.-.",
};

/** Reverse lookup built once from CHAR_TO_MORSE. */
export const MORSE_TO_CHAR: Record<string, string> = (() => {
  const out: Record<string, string> = {};
  for (const [ch, code] of Object.entries(CHAR_TO_MORSE)) {
    // First mapping wins for collisions (e.g. + vs AR).
    if (!(code in out)) out[code] = ch;
  }
  // Common prosigns (override letter collisions where useful).
  out["...-.-"] = "SK";
  out[".-.-."] = "AR";
  out["-...-"] = "BT";
  out["-.--."] = "KN";
  return out;
})();

export function decodeMorseElements(elements: string): string | null {
  if (!elements) return null;
  return MORSE_TO_CHAR[elements] ?? null;
}

/**
 * Encode text to a unit schedule: 1 = keyed, 0 = silence, each entry one dit-unit.
 * Character gap = 3 units off, word gap = 7 units off (standard timing).
 */
export function encodeMorseUnits(text: string): number[] {
  const units: number[] = [];
  const upper = text.toUpperCase();
  let pendingWordGap = false;

  for (let i = 0; i < upper.length; i++) {
    const ch = upper[i]!;
    if (ch === " " || ch === "\n" || ch === "\t") {
      pendingWordGap = true;
      continue;
    }
    const code = CHAR_TO_MORSE[ch];
    if (!code) continue;

    if (units.length > 0) {
      const gap = pendingWordGap ? 7 : 3;
      for (let g = 0; g < gap; g++) units.push(0);
      pendingWordGap = false;
    }

    for (let e = 0; e < code.length; e++) {
      if (e > 0) units.push(0); // intra-element gap
      const len = code[e] === "-" ? 3 : 1;
      for (let k = 0; k < len; k++) units.push(1);
    }
  }

  // Trailing word silence so the decoder can flush the last character.
  for (let g = 0; g < 7; g++) units.push(0);
  return units;
}
