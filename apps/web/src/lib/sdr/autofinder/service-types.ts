import type { FinderState } from "./engine";
import type { SdrState } from "../types";
import type { CleanerState } from "./cleaner";

export type ServiceStatus = {
  at: string;
  status: "waiting" | "yielding" | "starting" | "scanning" | "paused" | "error" | "stopped";
  message: string;
  sessionId: string | null;
  receiver: SdrState | null;
  finder: FinderState | null;
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
export const SERVICE_STALE_MS = 45_000;
