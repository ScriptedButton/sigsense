/**
 * FT8 procedural tokens, signal reports, Maidenhead grids, and callsigns.
 * Matched as whole whitespace-separated tokens in decoded messages.
 */

import { isLikelyCallsign } from "@/lib/sdr/morse/keywords";

export type Ft8KeywordKind = "procedure" | "report" | "grid" | "callsign";

export type Ft8KeywordDef = {
  /** Canonical token (uppercase). */
  token: string;
  kind: Ft8KeywordKind;
  /** Short operator-facing meaning. */
  meaning: string;
};

export type Ft8KeywordHit = {
  token: string;
  kind: Ft8KeywordKind;
  meaning: string;
  /** Character index in the message where this hit starts. */
  index: number;
  /** Length of the matched slice in the original message (preserves casing). */
  length: number;
};

/** Standard FT8 / free-text procedure words. */
export const FT8_KEYWORDS: readonly Ft8KeywordDef[] = [
  { token: "CQ", kind: "procedure", meaning: "Calling any station" },
  { token: "DX", kind: "procedure", meaning: "Long-distance / DX CQ" },
  { token: "DE", kind: "procedure", meaning: "From / this is" },
  { token: "QRZ", kind: "procedure", meaning: "Who is calling me?" },
  { token: "TEST", kind: "procedure", meaning: "Test / contest CQ" },
  { token: "POTA", kind: "procedure", meaning: "Parks on the Air" },
  { token: "SOTA", kind: "procedure", meaning: "Summits on the Air" },
  { token: "FD", kind: "procedure", meaning: "Field Day" },
  { token: "RRR", kind: "procedure", meaning: "Reception confirmed" },
  { token: "RR73", kind: "procedure", meaning: "Roger + best regards (end)" },
  { token: "73", kind: "procedure", meaning: "Best regards" },
  { token: "TU", kind: "procedure", meaning: "Thank you" },
];

const KEYWORD_BY_TOKEN = new Map(
  FT8_KEYWORDS.map((k) => [k.token, k] as const),
);

/** Maidenhead locator: 4-char (FN42) or 6-char (FN42ab). */
const GRID_RE = /^[A-R]{2}[0-9]{2}([A-X]{2})?$/i;

/** FT8 SNR report: -24…+49, optional leading R (roger report). */
const REPORT_RE = /^R?[+-]?\d{1,2}$/i;

function isLikelyGrid(token: string): boolean {
  return GRID_RE.test(token);
}

function parseReport(token: string): Ft8KeywordDef | null {
  if (!REPORT_RE.test(token)) return null;
  const upper = token.toUpperCase();
  // Bare digits / signs that aren't reports (e.g. "42" grid field, "CQ").
  if (/^\d+$/.test(upper)) return null;
  if (KEYWORD_BY_TOKEN.has(upper)) return null;

  const roger = upper.startsWith("R");
  const numPart = roger ? upper.slice(1) : upper;
  if (!numPart || numPart === "+" || numPart === "-") return null;
  const value = Number(numPart);
  if (!Number.isFinite(value) || value < -24 || value > 49) return null;
  // Require an explicit sign or R-prefix so plain "12" isn't a report.
  if (!roger && !/^[+-]/.test(upper)) return null;

  const signed = value >= 0 ? `+${value}` : String(value);
  return {
    token: upper,
    kind: "report",
    meaning: roger
      ? `Roger report ${signed} dB`
      : `Signal report ${signed} dB`,
  };
}

/**
 * Callsign for FT8: plain, portable (`W1AW/QRP`), or hashed angle form (`<W1AW>`).
 * Returns the lookup token (without `/suffix` or brackets) when identifiable.
 */
export function extractFt8Callsign(raw: string): string | null {
  let t = raw.trim().toUpperCase();
  if (t.length < 3) return null;

  if (t.startsWith("<") && t.endsWith(">")) {
    t = t.slice(1, -1);
    // Truncated hashes like `...` are not look-uppable.
    if (!t || t.includes(".")) return null;
  }

  const base = t.includes("/") ? t.slice(0, t.indexOf("/")) : t;
  if (!isLikelyCallsign(base)) return null;
  return base;
}

export function lookupFt8Keyword(token: string): Ft8KeywordDef | null {
  const upper = token.toUpperCase();
  const known = KEYWORD_BY_TOKEN.get(upper);
  if (known) return known;

  const report = parseReport(token);
  if (report) return report;

  if (isLikelyGrid(token)) {
    return {
      token: upper,
      kind: "grid",
      meaning: "Maidenhead grid locator",
    };
  }

  const call = extractFt8Callsign(token);
  if (call) {
    return {
      token: call,
      kind: "callsign",
      meaning: "Amateur callsign",
    };
  }

  return null;
}

/**
 * Scan one FT8 message for keywords / callsigns / grids / reports.
 * Hits are in left-to-right order; tokens are whitespace-separated.
 */
export function findFt8Keywords(msg: string): Ft8KeywordHit[] {
  const hits: Ft8KeywordHit[] = [];
  const re = /\S+/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(msg)) !== null) {
    const raw = m[0]!;
    const def = lookupFt8Keyword(raw);
    if (!def) continue;
    hits.push({
      token: def.token,
      kind: def.kind,
      meaning: def.meaning,
      index: m.index,
      length: raw.length,
    });
  }
  return hits;
}

/**
 * Unique keywords across messages (oldest → newest), first-seen order.
 */
export function uniqueFt8Keywords(
  messages: readonly { msg: string }[],
  maxUnique = 24,
): Ft8KeywordHit[] {
  const out: Ft8KeywordHit[] = [];
  const seen = new Set<string>();
  for (const message of messages) {
    for (const hit of findFt8Keywords(message.msg)) {
      const key = `${hit.kind}:${hit.token}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(hit);
      if (out.length >= maxUnique) return out;
    }
  }
  return out;
}

export function ft8KeywordKindLabel(kind: Ft8KeywordKind): string {
  switch (kind) {
    case "procedure":
      return "Procedure";
    case "report":
      return "Report";
    case "grid":
      return "Grid";
    case "callsign":
      return "Callsign";
  }
}
