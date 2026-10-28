/**
 * Normalize / validate amateur and GMRS callsigns for lookup.
 */

import { isLikelyCallsign } from "@/lib/sdr/morse/keywords";

export type CallsignServiceMode = "auto" | "amateur" | "gmrs";

/**
 * Modern FCC GMRS callsigns: WQ/WR + 2 letters + 3 digits (e.g. WRNT570).
 * Older WQ-series and current WR-series both match.
 */
export function isGmrsCallsign(call: string): boolean {
  return /^W[QR][A-Z]{2}\d{3}$/.test(call.toUpperCase());
}

export function isUsAmateurCallsign(call: string): boolean {
  // Mainland US: K, N, W, and AA–AL group (excludes GMRS WQ/WR####).
  const upper = call.toUpperCase();
  if (isGmrsCallsign(upper)) return false;
  return /^[KNW]\d|^A[A-L]\d/.test(upper);
}

/** @deprecated use isUsAmateurCallsign */
export function isUsCallsign(call: string): boolean {
  return isUsAmateurCallsign(call);
}

function looksLikeAmateurCall(token: string): boolean {
  if (isGmrsCallsign(token)) return false;
  if (isLikelyCallsign(token)) return true;
  // Slightly looser for manual entry (e.g. special-event 1x1).
  return (
    /^[A-Z0-9]{3,8}$/.test(token) && /\d/.test(token) && /[A-Z]/.test(token)
  );
}

/** Strip noise and resolve portable forms (CALL/P, W4/G0ABC) to a base call. */
export function normalizeAmateurCallsign(raw: string): string | null {
  const cleaned = raw
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9/]/g, "");
  if (!cleaned) return null;

  const parts = cleaned.split("/").filter(Boolean);
  if (parts.length === 0) return null;

  if (parts.length >= 2) {
    const prefix = parts[0]!;
    const rest = parts[1]!;
    // Operating-away prefix: W4/G0ABC, VE3/W1AW → look up the call after /
    if (/^[A-Z]{1,2}\d{0,1}$/.test(prefix) && looksLikeAmateurCall(rest)) {
      return rest;
    }
    // CALL/P, CALL/M, CALL/QRP, CALL/4 → base before /
    if (looksLikeAmateurCall(prefix)) return prefix;
  }

  const call = parts[0]!;
  return looksLikeAmateurCall(call) ? call : null;
}

export function normalizeGmrsCallsign(raw: string): string | null {
  const cleaned = raw
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "");
  if (!cleaned) return null;
  return isGmrsCallsign(cleaned) ? cleaned : null;
}

/**
 * Normalize for the selected service mode.
 * `auto` prefers GMRS when the token matches the GMRS pattern.
 */
export function normalizeCallsign(
  raw: string,
  mode: CallsignServiceMode = "auto",
): string | null {
  if (mode === "gmrs") return normalizeGmrsCallsign(raw);
  if (mode === "amateur") return normalizeAmateurCallsign(raw);

  const gmrs = normalizeGmrsCallsign(raw);
  if (gmrs) return gmrs;
  return normalizeAmateurCallsign(raw);
}

export function detectCallsignService(
  call: string,
): "amateur" | "gmrs" | null {
  const upper = call.trim().toUpperCase();
  if (isGmrsCallsign(upper)) return "gmrs";
  if (normalizeAmateurCallsign(upper)) return "amateur";
  return null;
}
