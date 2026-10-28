import { API_BASE } from "@/lib/api";
import { BACKEND_CLASSES } from "./backend-classes";

/** What the backend keeps for one correction. */
export type Correction = {
  id: string;
  label: string;
  note: string;
  created_at: number;
  /** What the model had said before the user corrected it. */
  wrong_label: string | null;
  track_id: string | null;
  source: string;
};

export type CorrectionsList = {
  items: Correction[];
  counts: Record<string, number>;
  total: number;
  classes: string[];
  custom: string[];
};

export type CorrectionResult = {
  item: Correction;
  track: { label: string; confidence: number } | null;
  prediction: {
    label: string;
    confidence: number;
    learned: { label: string; examples: number; similarity: number; weight: number } | null;
    was: string | null;
  };
  refreshed: number;
  counts: Record<string, number>;
};

export const NOT_A_SIGNAL = "__noise__";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export function listCorrections(): Promise<CorrectionsList> {
  return call("/classify/feedback");
}

export function submitCorrection(input: {
  trackId: string;
  session?: string;
  label: string;
  note?: string;
}): Promise<CorrectionResult> {
  return call(
    "/classify/feedback",
    json("POST", {
      track_id: input.trackId,
      session: input.session ?? null,
      label: input.label,
      note: input.note ?? "",
    }),
  );
}

export function deleteCorrection(id: string): Promise<{ removed: number }> {
  return call(`/classify/feedback/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function deleteLabel(label: string): Promise<{ removed: number }> {
  return call(`/classify/feedback?label=${encodeURIComponent(label)}`, { method: "DELETE" });
}

export function deleteAllCorrections(): Promise<{ removed: number }> {
  return call("/classify/feedback?all=true", { method: "DELETE" });
}

export async function importCorrections(file: File): Promise<{ added: number; total: number }> {
  const form = new FormData();
  form.append("file", file);
  return call("/classify/feedback/import", { method: "POST", body: form });
}

export const exportCorrectionsUrl = () => `${API_BASE}/classify/feedback/export`;
export const thumbnailUrl = (id: string) =>
  `${API_BASE}/classify/feedback/${encodeURIComponent(id)}/thumbnail.png`;

/** Class names to offer before (or without) an answer from the backend. */
export const FALLBACK_CLASSES: readonly string[] = BACKEND_CLASSES;
