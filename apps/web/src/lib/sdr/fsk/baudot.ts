/** ITA2 / Baudot figures and letters shift codes. */
export const BAUDOT_LTRS = 0b11111;
export const BAUDOT_FIGS = 0b11011;

const LTRS: Record<number, string> = {
  0b00000: "",
  0b00001: "E",
  0b00010: "\n",
  0b00011: "A",
  0b00100: " ",
  0b00101: "S",
  0b00110: "I",
  0b00111: "U",
  0b01000: "\r",
  0b01001: "D",
  0b01010: "R",
  0b01011: "J",
  0b01100: "N",
  0b01101: "F",
  0b01110: "C",
  0b01111: "K",
  0b10000: "T",
  0b10001: "Z",
  0b10010: "L",
  0b10011: "W",
  0b10100: "H",
  0b10101: "Y",
  0b10110: "P",
  0b10111: "Q",
  0b11000: "O",
  0b11001: "B",
  0b11010: "G",
  0b11011: "",
  0b11100: "M",
  0b11101: "X",
  0b11110: "V",
  0b11111: "",
};

const FIGS: Record<number, string> = {
  0b00000: "",
  0b00001: "3",
  0b00010: "\n",
  0b00011: "-",
  0b00100: " ",
  0b00101: "'",
  0b00110: "8",
  0b00111: "7",
  0b01000: "\r",
  0b01001: "$",
  0b01010: "4",
  0b01011: "\u0007",
  0b01100: ",",
  0b01101: "!",
  0b01110: ":",
  0b01111: "(",
  0b10000: "5",
  0b10001: "+",
  0b10010: ")",
  0b10011: "2",
  0b10100: "#",
  0b10101: "6",
  0b10110: "0",
  0b10111: "1",
  0b11000: "9",
  0b11001: "?",
  0b11010: "&",
  0b11011: "",
  0b11100: ".",
  0b11101: "/",
  0b11110: ";",
  0b11111: "",
};

const CHAR_TO_CODE = new Map<string, { code: number; figs: boolean }>();
for (const [codeStr, ch] of Object.entries(LTRS)) {
  const code = Number(codeStr);
  if (!ch || code === BAUDOT_LTRS || code === BAUDOT_FIGS) continue;
  CHAR_TO_CODE.set(ch, { code, figs: false });
  CHAR_TO_CODE.set(ch.toLowerCase(), { code, figs: false });
}
for (const [codeStr, ch] of Object.entries(FIGS)) {
  const code = Number(codeStr);
  if (!ch || code === BAUDOT_LTRS || code === BAUDOT_FIGS) continue;
  if (!CHAR_TO_CODE.has(ch)) CHAR_TO_CODE.set(ch, { code, figs: true });
}

export type BaudotShift = "ltrs" | "figs";

/** Decode one 5-bit Baudot character; updates shift state. */
export function decodeBaudotChar(
  code: number,
  shift: BaudotShift,
): { char: string | null; shift: BaudotShift } {
  const bits = code & 0x1f;
  if (bits === BAUDOT_LTRS) return { char: null, shift: "ltrs" };
  if (bits === BAUDOT_FIGS) return { char: null, shift: "figs" };
  const table = shift === "figs" ? FIGS : LTRS;
  const char = table[bits] ?? "";
  return { char: char || null, shift };
}

/**
 * Encode a string to a mark/space bit stream (1 = mark, 0 = space)
 * with start + 5 data (LSB first) + stop bits for RTTY.
 */
export function encodeBaudotBits(text: string, stopBits = 1.5): number[] {
  const bits: number[] = [];
  for (let i = 0; i < 20; i++) bits.push(1);

  let figs = false;
  const emitStops = () => {
    const n = Math.max(1, Math.ceil(stopBits));
    for (let s = 0; s < n; s++) bits.push(1);
  };
  const emit = (code: number) => {
    bits.push(0);
    for (let b = 0; b < 5; b++) bits.push((code >> b) & 1);
    emitStops();
  };

  for (const raw of text) {
    if (raw === "\n") {
      emit(0b01000);
      emit(0b00010);
      continue;
    }
    const entry = CHAR_TO_CODE.get(raw);
    if (!entry) continue;
    if (entry.figs && !figs) {
      emit(BAUDOT_FIGS);
      figs = true;
    } else if (!entry.figs && figs) {
      emit(BAUDOT_LTRS);
      figs = false;
    }
    emit(entry.code);
  }

  for (let i = 0; i < 32; i++) bits.push(1);
  return bits;
}
