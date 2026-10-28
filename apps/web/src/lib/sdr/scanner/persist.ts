import {
  createDefaultBanks,
  findBank,
} from "@/lib/sdr/scanner/banks";
import {
  DEFAULT_SCANNER_CONFIG,
  SCANNER_STORAGE_KEY,
  type ScanBank,
  type ScanChannel,
  type ScannerConfig,
} from "@/lib/sdr/scanner/types";
import type { DemodScheme } from "@/lib/sdr/types";

const SCHEMES: readonly DemodScheme[] = [
  "WBFM",
  "NBFM",
  "AM",
  "USB",
  "LSB",
  "CW",
];

type StoredPayload = {
  version: 1;
  config: ScannerConfig;
  banks: ScanBank[];
};

function isScheme(v: unknown): v is DemodScheme {
  return typeof v === "string" && (SCHEMES as readonly string[]).includes(v);
}

function parseChannel(raw: unknown): ScanChannel | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  if (typeof o.id !== "string" || typeof o.label !== "string") return null;
  if (typeof o.frequencyHz !== "number" || !Number.isFinite(o.frequencyHz)) {
    return null;
  }
  if (!isScheme(o.scheme)) return null;
  const bandwidthHz =
    typeof o.bandwidthHz === "number" && Number.isFinite(o.bandwidthHz)
      ? o.bandwidthHz
      : undefined;
  return {
    id: o.id,
    label: o.label,
    frequencyHz: Math.round(o.frequencyHz),
    scheme: o.scheme,
    bandwidthHz,
    lockedOut: Boolean(o.lockedOut),
  };
}

function parseBank(raw: unknown): ScanBank | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  if (typeof o.id !== "string" || typeof o.name !== "string") return null;
  if (!Array.isArray(o.channels)) return null;
  const channels = o.channels
    .map(parseChannel)
    .filter((c): c is ScanChannel => c !== null);
  return {
    id: o.id,
    name: o.name,
    builtin: Boolean(o.builtin),
    channels,
  };
}

function parseConfig(raw: unknown, banks: ScanBank[]): ScannerConfig {
  const base = { ...DEFAULT_SCANNER_CONFIG };
  if (!raw || typeof raw !== "object") return base;
  const o = raw as Record<string, unknown>;
  const activeBankId =
    typeof o.activeBankId === "string" && findBank(banks, o.activeBankId)
      ? o.activeBankId
      : banks[0]?.id ?? base.activeBankId;
  return {
    activeBankId,
    dwellMs:
      typeof o.dwellMs === "number" && o.dwellMs >= 100
        ? Math.min(10_000, Math.round(o.dwellMs))
        : base.dwellMs,
    holdMs:
      typeof o.holdMs === "number" && o.holdMs >= 0
        ? Math.min(60_000, Math.round(o.holdMs))
        : base.holdMs,
    stopOnActivity:
      typeof o.stopOnActivity === "boolean"
        ? o.stopOnActivity
        : base.stopOnActivity,
    resumeOnQuiet:
      typeof o.resumeOnQuiet === "boolean"
        ? o.resumeOnQuiet
        : base.resumeOnQuiet,
  };
}

/**
 * Merge persisted banks with current builtins so new seed channels appear after
 * upgrades, while preserving user lockouts / custom banks / custom channels.
 */
export function mergeBanks(stored: ScanBank[]): ScanBank[] {
  const defaults = createDefaultBanks();
  const byId = new Map(stored.map((b) => [b.id, b]));
  const merged: ScanBank[] = [];

  for (const def of defaults) {
    const prev = byId.get(def.id);
    if (!prev) {
      merged.push(def);
      byId.delete(def.id);
      continue;
    }
    if (def.builtin) {
      // Keep builtin channel set, but restore lockouts from storage.
      const lockMap = new Map(
        prev.channels.map((c) => [c.id, c.lockedOut] as const),
      );
      merged.push({
        ...def,
        channels: def.channels.map((c) => ({
          ...c,
          lockedOut: lockMap.get(c.id) ?? c.lockedOut,
        })),
      });
    } else {
      merged.push(prev);
    }
    byId.delete(def.id);
  }

  // Extra user banks (non-default ids).
  for (const leftover of byId.values()) {
    if (!leftover.builtin) merged.push(leftover);
  }

  return merged;
}

export function loadScannerPersist(): {
  config: ScannerConfig;
  banks: ScanBank[];
} {
  const defaults = createDefaultBanks();
  if (typeof localStorage === "undefined") {
    return { config: { ...DEFAULT_SCANNER_CONFIG }, banks: defaults };
  }
  try {
    const raw = localStorage.getItem(SCANNER_STORAGE_KEY);
    if (!raw) {
      return { config: { ...DEFAULT_SCANNER_CONFIG }, banks: defaults };
    }
    const parsed = JSON.parse(raw) as Partial<StoredPayload>;
    const banks = mergeBanks(
      Array.isArray(parsed.banks)
        ? parsed.banks.map(parseBank).filter((b): b is ScanBank => b !== null)
        : defaults,
    );
    return { config: parseConfig(parsed.config, banks), banks };
  } catch {
    return { config: { ...DEFAULT_SCANNER_CONFIG }, banks: defaults };
  }
}

export function saveScannerPersist(
  config: ScannerConfig,
  banks: ScanBank[],
): void {
  if (typeof localStorage === "undefined") return;
  try {
    const payload: StoredPayload = { version: 1, config, banks };
    localStorage.setItem(SCANNER_STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // Quota / private mode — ignore.
  }
}
