import type { FinderState } from "./engine";
import type { SdrState } from "../types";
import type { CleanerState } from "./cleaner";

export type ServiceGain = { autoGain: true } | { autoGain: false; gainDb: number };
export type ServiceStatus = {
  at: string;
  status: "waiting" | "yielding" | "starting" | "scanning" | "paused" | "error" | "stopped";
  message: string;
  sessionId: string | null;
  receiver: SdrState | null;
  finder: FinderState | null;
  gain?: ServiceGain;
};
export type SignalFinding = {
  cleaning?: CleanerState;
  frequencyHz: number;
  bandwidthHz: number;
  band: string;
  firstSeen: string;
  lastSeen: string;
  speechRatio: number;
  confirmations: number;
};
export type SignalCheck = FinderState["recent"][number] & { id: string };
export type ServiceSnapshot = ServiceStatus & {
  version: 1;
  enabled: boolean;
  findings: SignalFinding[];
  checks: SignalCheck[];
};
export type ServiceView = ServiceSnapshot & { online: boolean; stale: boolean; bridgeUrl?: string };
export const SERVICE_ACTIONS = ["resume", "pause", "skip", "release"] as const;
export type ServiceAction = typeof SERVICE_ACTIONS[number];
export type ServiceControl = { action: ServiceAction } | ({ action: "gain" } & ServiceGain);

/** Shared validation at both HTTP boundaries; never coerce unsafe hardware values. */
export function parseServiceControl(value: unknown): ServiceControl | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const body = value as Record<string, unknown>;
  if (body.action === "gain") {
    if (body.autoGain === true && body.gainDb === undefined) return { action: "gain", autoGain: true };
    if (body.autoGain === false && typeof body.gainDb === "number" && Number.isFinite(body.gainDb) && body.gainDb >= 0 && body.gainDb <= 50) {
      return { action: "gain", autoGain: false, gainDb: body.gainDb };
    }
    return null;
  }
  return typeof body.action === "string" && (SERVICE_ACTIONS as readonly string[]).includes(body.action)
    ? { action: body.action as ServiceAction } : null;
}
export const SERVICE_STALE_MS = 45_000;
