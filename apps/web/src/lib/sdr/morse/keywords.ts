/**
 * CW / Morse procedural keywords, Q-codes, and prosigns.
 * Matched as whole tokens in decoded text (case-insensitive).
 */

export type MorseKeywordKind = "procedure" | "qcode" | "prosign" | "callsign";

export type MorseKeywordDef = {
  /** Canonical token (uppercase). */
  token: string;
  kind: MorseKeywordKind;
  /** Short operator-facing meaning. */
  meaning: string;
};

export type MorseKeywordHit = {
  token: string;
  kind: MorseKeywordKind;
  meaning: string;
  /** Character index in the full transcript where this hit starts. */
  index: number;
  /** When this hit was first recorded (ms). */
  at: number;
};

/** Amateur callsign: prefix + digit + 1–4 letter suffix (e.g. W1AW, VE3XYZ). */
const CALLSIGN_RE = /^[A-Z0-9]{1,3}\d[A-Z]{1,4}$/;

/**
 * Ordered longest-first where needed so multi-char tokens are preferred.
 * Single-letter K / R stay whole-word only via tokenisation.
 */
export const MORSE_KEYWORDS: readonly MorseKeywordDef[] = [
  // Procedural
  { token: "CQ", kind: "procedure", meaning: "Calling any station" },
  { token: "DE", kind: "procedure", meaning: "From / this is" },
  { token: "BK", kind: "procedure", meaning: "Break — invite reply" },
  { token: "PSE", kind: "procedure", meaning: "Please" },
  { token: "TNX", kind: "procedure", meaning: "Thanks" },
  { token: "TKS", kind: "procedure", meaning: "Thanks" },
  { token: "UR", kind: "procedure", meaning: "Your" },
  { token: "RST", kind: "procedure", meaning: "Signal report" },
  { token: "FB", kind: "procedure", meaning: "Fine business" },
  { token: "OM", kind: "procedure", meaning: "Old man (operator)" },
  { token: "YL", kind: "procedure", meaning: "Young lady (operator)" },
  { token: "XYL", kind: "procedure", meaning: "Wife of operator" },
  { token: "ES", kind: "procedure", meaning: "And" },
  { token: "HW", kind: "procedure", meaning: "How copy?" },
  { token: "CL", kind: "procedure", meaning: "Closing station" },
  { token: "DX", kind: "procedure", meaning: "Long-distance" },
  { token: "73", kind: "procedure", meaning: "Best regards" },
  { token: "88", kind: "procedure", meaning: "Love and kisses" },
  { token: "K", kind: "procedure", meaning: "Over — go ahead" },
  { token: "R", kind: "procedure", meaning: "Roger — received" },

  // Prosigns (decoded as multi-letter tokens from element map)
  { token: "AR", kind: "prosign", meaning: "End of message" },
  { token: "SK", kind: "prosign", meaning: "End of contact" },
  { token: "BT", kind: "prosign", meaning: "Break / separator" },
  { token: "KN", kind: "prosign", meaning: "Over to named station" },
  { token: "AS", kind: "prosign", meaning: "Wait" },
  { token: "VA", kind: "prosign", meaning: "End of contact (alt. SK)" },

  // Common Q-codes
  { token: "QRL", kind: "qcode", meaning: "Is this frequency in use?" },
  { token: "QRM", kind: "qcode", meaning: "Man-made interference" },
  { token: "QRN", kind: "qcode", meaning: "Natural noise / static" },
  { token: "QRO", kind: "qcode", meaning: "Increase power" },
  { token: "QRP", kind: "qcode", meaning: "Decrease power / low power" },
  { token: "QRQ", kind: "qcode", meaning: "Send faster" },
  { token: "QRS", kind: "qcode", meaning: "Send slower" },
  { token: "QRT", kind: "qcode", meaning: "Stop sending / closing" },
  { token: "QRU", kind: "qcode", meaning: "Have you anything for me?" },
  { token: "QRV", kind: "qcode", meaning: "I am ready" },
  { token: "QRX", kind: "qcode", meaning: "Stand by / call again later" },
  { token: "QRZ", kind: "qcode", meaning: "Who is calling me?" },
  { token: "QSB", kind: "qcode", meaning: "Fading" },
  { token: "QSL", kind: "qcode", meaning: "Acknowledge receipt" },
  { token: "QSO", kind: "qcode", meaning: "Contact / conversation" },
  { token: "QSY", kind: "qcode", meaning: "Change frequency" },
  { token: "QTH", kind: "qcode", meaning: "Location" },
  { token: "QTR", kind: "qcode", meaning: "Exact time" },
];

const KEYWORD_BY_TOKEN = new Map(
  MORSE_KEYWORDS.map((k) => [k.token, k] as const),
);

export function isLikelyCallsign(token: string): boolean {
  const t = token.toUpperCase();
  if (KEYWORD_BY_TOKEN.has(t)) return false;
  // Avoid mistaking pure numbers / single letters.
  if (t.length < 3 || t.length > 8) return false;
  return CALLSIGN_RE.test(t);
}

export function lookupMorseKeyword(token: string): MorseKeywordDef | null {
  const upper = token.toUpperCase();
  const known = KEYWORD_BY_TOKEN.get(upper);
  if (known) return known;
  if (isLikelyCallsign(upper)) {
    return {
      token: upper,
      kind: "callsign",
      meaning: "Amateur callsign",
    };
  }
  return null;
}

/**
 * Scan decoded Morse text for keywords / callsigns.
 * Returns hits in document order; overlapping tokens are not produced
 * because matching is whole-word (whitespace-separated).
 */
export function findMorseKeywords(
  text: string,
  now = Date.now(),
): MorseKeywordHit[] {
  const hits: MorseKeywordHit[] = [];
  // Match runs of alphanumerics (Morse decode emits A–Z / 0–9 / prosign letters).
  const re = /[A-Za-z0-9]+/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const raw = m[0]!;
    const def = lookupMorseKeyword(raw);
    if (!def) continue;
    hits.push({
      token: def.token,
      kind: def.kind,
      meaning: def.meaning,
      index: m.index,
      at: now,
    });
  }
  return hits;
}

/**
 * Unique keywords for the chip strip, in first-seen order within `text`.
 * Callsigns keep their last occurrence's casing via the token field.
 */
export function uniqueMorseKeywords(
  text: string,
  maxUnique = 24,
  now = Date.now(),
): MorseKeywordHit[] {
  const hits = findMorseKeywords(text, now);
  const out: MorseKeywordHit[] = [];
  const seen = new Set<string>();
  for (const hit of hits) {
    if (seen.has(hit.token)) continue;
    seen.add(hit.token);
    out.push(hit);
    if (out.length >= maxUnique) break;
  }
  return out;
}

export function keywordKindLabel(kind: MorseKeywordKind): string {
  switch (kind) {
    case "procedure":
      return "Procedure";
    case "qcode":
      return "Q-code";
    case "prosign":
      return "Prosign";
    case "callsign":
      return "Callsign";
  }
}
