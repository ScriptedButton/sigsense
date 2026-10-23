const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export type AnalyzeResult = {
  label: string;
  confidence: number;
  is_unknown: boolean;
  alternatives: { label: string; confidence: number }[];
  neighbors: {
    id: number;
    label: string;
    source: string;
    notes: string;
    similarity: number;
  }[];
  meta: {
    sample_rate_hz?: number | null;
    center_freq_hz?: number | null;
    bandwidth_hz?: number | null;
    relative_center_hz?: number | null;
    duration_s?: number | null;
    duty_cycle?: number | null;
  };
  embedding?: number[] | null;
};

export type LibraryItem = {
  label: string;
  source: string;
  count: number;
  enrolled: boolean;
};

export function wsUrl(path: string): string {
  const base = API_BASE.replace(/^http/, "ws");
  return `${base}${path}`;
}

export async function analyzeImage(file: File): Promise<AnalyzeResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("include_embedding", "true");
  const res = await fetch(`${API_BASE}/analyze/image`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function defaultIqDtype(file: File): string {
  const name = file.name.toLowerCase();
  if (name.endsWith(".wav") || name.endsWith(".wave") || file.type.includes("wav")) {
    return "wav";
  }
  return "auto";
}

export async function analyzeIq(
  file: File,
  opts?: { dtype?: string; sampleRate?: number; centerFreq?: number },
): Promise<AnalyzeResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("dtype", opts?.dtype ?? defaultIqDtype(file));
  form.append("include_embedding", "true");
  if (opts?.sampleRate != null) {
    form.append("sample_rate", String(opts.sampleRate));
  }
  if (opts?.centerFreq != null) {
    form.append("center_freq", String(opts.centerFreq));
  }
  const res = await fetch(`${API_BASE}/analyze/iq`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchLibrary(): Promise<LibraryItem[]> {
  const res = await fetch(`${API_BASE}/library`);
  if (!res.ok) throw new Error(await res.text());
  const data = await res.json();
  return data.items;
}

export async function enrollSignal(
  label: string,
  embedding: number[],
  notes = "",
): Promise<{ id: number; label: string }> {
  const res = await fetch(`${API_BASE}/library/enroll`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label, embedding, notes }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchClasses(): Promise<string[]> {
  const res = await fetch(`${API_BASE}/classes`);
  if (!res.ok) throw new Error(await res.text());
  const data = await res.json();
  return data.classes;
}

export async function configureTuner(body: Record<string, unknown>) {
  const res = await fetch(`${API_BASE}/sdr/tuner`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchDevices() {
  const res = await fetch(`${API_BASE}/sdr/devices`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export type { BleDecodeResult } from "@/lib/bleTypes";

export type BleReplayResult = {
  ok: boolean;
  transmitted?: boolean;
  dry_run?: boolean;
  channel: number;
  center_freq_hz: number;
  sample_rate_hz: number;
  iq_samples?: number;
  samples?: number;
  repeats?: number;
  tx_gain?: number;
  amp?: boolean;
  log?: string;
};

export async function replayBlePacket(body: {
  header_hex: string;
  payload_hex: string;
  channel: number;
  sample_rate_hz?: number;
  tx_gain?: number;
  amp?: boolean;
  repeats?: number;
  dry_run?: boolean;
}): Promise<BleReplayResult> {
  const res = await fetch(`${API_BASE}/ble/replay`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchBlePreset() {
  const res = await fetch(`${API_BASE}/sdr/presets/ble`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{
    device_args: string;
    sample_rate_hz: number;
    gain_db: number;
    ble_decode: boolean;
    demo: boolean;
    demo_class: string;
    channels: { name: string; channel: number; center_freq_hz: number }[];
  }>;
}

export async function decodeBle(
  file: File,
  opts?: {
    dtype?: string;
    sampleRate?: number;
    centerFreq?: number;
    channel?: number;
  },
): Promise<BleDecodeResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("dtype", opts?.dtype ?? defaultIqDtype(file));
  if (opts?.sampleRate != null) {
    form.append("sample_rate", String(opts.sampleRate));
  }
  if (opts?.centerFreq != null) {
    form.append("center_freq", String(opts.centerFreq));
  }
  if (opts?.channel != null) {
    form.append("channel", String(opts.channel));
  }
  const res = await fetch(`${API_BASE}/analyze/ble`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export { API_BASE };
