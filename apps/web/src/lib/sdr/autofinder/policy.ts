import { SHORTWAVE_BANDS } from "../shortwave/bands";

export type SearchTile = { id: string; label: string; lowHz: number; highHz: number; centerHz: number };
export type FinderMemory = { rejected: Record<string, number>; bands: Record<string, number> };
export const emptyMemory = (): FinderMemory => ({ rejected: {}, bands: {} });

/** Broadcast bands first, then fill every HF gap. No user-entered list or range. */
export function automaticSearchPlan(sampleRate: number, memory = emptyMemory()): SearchTile[] {
  const width = Math.max(50_000, Math.min(600_000, sampleRate * .55));
  const tiles: SearchTile[] = [];
  const split = (lowHz: number, highHz: number, label: string, id: string) => {
    const count = Math.ceil((highHz - lowHz) / width), span = (highHz - lowHz) / count;
    for (let i = 0; i < count; i++) {
      const low = lowHz + i * span, high = lowHz + (i + 1) * span;
      tiles.push({ id: `${id}-${i}`, label, lowHz: low, highHz: high, centerHz: Math.round((low + high) / 2 - 50_000) });
    }
  };
  // Start in the lower/middle broadcast bands; learned speech yields move productive tiles up.
  for (const id of ["49m", "31m", "41m", "60m", "25m", "19m", "75m", "90m", "120m", "22m", "16m", "15m", "13m", "11m"]) {
    const band = SHORTWAVE_BANDS.find((b) => b.id === id)!;
    split(band.loKHz * 1000, band.hiKHz * 1000, band.label, band.id);
  }
  const broadcasts = tiles.splice(0).sort((a, b) => (memory.bands[b.id] ?? 0) - (memory.bands[a.id] ?? 0));
  let low = 2_300_000;
  for (const band of SHORTWAVE_BANDS) {
    if (band.loKHz * 1000 > low) split(low, band.loKHz * 1000, "HF discovery", `gap-${low}`);
    low = band.hiKHz * 1000;
  }
  split(low, 30_000_000, "HF discovery", "gap-top");
  // Interleave gaps so a busy broadcast band cannot indefinitely starve exploration.
  const plan: SearchTile[] = [];
  while (broadcasts.length || tiles.length) {
    plan.push(...broadcasts.splice(0, 2), ...tiles.splice(0, 1));
  }
  return plan;
}

export function candidateKey(hz: number): string { return String(Math.round(hz / 1000)); }
export function rejectCandidate(memory: FinderMemory, hz: number, now: number, skipped = false): void {
  memory.rejected[candidateKey(hz)] = now + (skipped ? 15 : 3) * 60_000;
  const entries = Object.entries(memory.rejected).filter(([, until]) => until > now).sort((a, b) => b[1] - a[1]).slice(0, 300);
  memory.rejected = Object.fromEntries(entries);
}
export function canTry(memory: FinderMemory, hz: number, now: number): boolean { return (memory.rejected[candidateKey(hz)] ?? 0) <= now; }

export function loadFinderMemory(): FinderMemory {
  try {
    const saved = JSON.parse(localStorage.getItem("sigsense.autofinder.memory") ?? "null");
    const clean = (value: unknown) => value && typeof value === "object" && !Array.isArray(value)
      ? Object.fromEntries(Object.entries(value).filter(([key, score]) => key.length < 80 && typeof score === "number" && Number.isFinite(score)).slice(0, 300)) as Record<string, number> : {};
    return { rejected: clean(saved?.rejected), bands: clean(saved?.bands) };
  } catch { return emptyMemory(); }
}
export function saveFinderMemory(memory: FinderMemory): void {
  try { localStorage.setItem("sigsense.autofinder.memory", JSON.stringify(memory)); } catch { /* private mode */ }
}
