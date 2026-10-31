/**
 * Remembered rail layout: which tab and which sections were open. Storage can
 * be missing or blocked (private windows), so every access falls back.
 */
const PREFIX = "sigsense.rail.";

export function readRailPref<T extends string>(
  key: string,
  fallback: T,
  allowed?: readonly T[],
): T {
  try {
    const value = localStorage.getItem(PREFIX + key) as T | null;
    if (value === null) return fallback;
    if (allowed && !allowed.includes(value)) return fallback;
    return value;
  } catch {
    return fallback;
  }
}

export function writeRailPref(key: string, value: string): void {
  try {
    localStorage.setItem(PREFIX + key, value);
  } catch {
    // Not persisted; the layout still works for this visit.
  }
}
