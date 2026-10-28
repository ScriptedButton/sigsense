import type { BleDecodeResult } from "@/lib/bleTypes";

/** localStorage key for a per-device API address (e.g. a phone on the LAN). */
export const API_BASE_KEY = "sigsense.apiBase";

const DEFAULT_API_BASE = (process.env.NEXT_PUBLIC_API_URL || "/api").replace(
  /\/+$/,
  "",
);

/** The saved override, if any. Takes effect on the next page load. */
export function apiBaseOverride(): string | null {
  try {
    const saved = localStorage.getItem(API_BASE_KEY)?.trim();
    return saved ? saved.replace(/\/+$/, "") : null;
  } catch {
    return null;
  }
}

/** Save (or clear, with null/empty) the API address override. */
export function setApiBaseOverride(value: string | null): void {
  try {
    const clean = value?.trim().replace(/\/+$/, "");
    if (clean) localStorage.setItem(API_BASE_KEY, clean);
    else localStorage.removeItem(API_BASE_KEY);
  } catch {
    /* storage blocked: the override simply doesn't persist */
  }
}

export const DEFAULT_API_BASE_URL = DEFAULT_API_BASE;

const API_BASE =
  (typeof window !== "undefined" ? apiBaseOverride() : null) ?? DEFAULT_API_BASE;

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
  notes?: string;
  /** Artemis-DB page id, present only for artemis prototypes. */
  pageId?: string;
};

export function wsUrl(path: string): string {
  const url = new URL(`${API_BASE}${path}`, window.location.origin);
  url.protocol = url.protocol.replace(/^http/, "ws");
  return url.toString();
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
  if (
    name.endsWith(".wav") ||
    name.endsWith(".wave") ||
    file.type.includes("wav")
  ) {
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

/** Pull a readable message out of FastAPI `{"detail": "..."}` (or plain text). */
export async function apiErrorMessage(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail[0]) {
      const first = body.detail[0] as { msg?: string };
      if (typeof first.msg === "string") return first.msg;
    }
  } catch {
    /* not JSON */
  }
  return text || `Request failed (${res.status})`;
}

export async function configureTuner(body: Record<string, unknown>) {
  const res = await fetch(`${API_BASE}/sdr/tuner`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await apiErrorMessage(res));
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

export type BleDecodeOptions = {
  dtype?: string;
  sampleRate?: number;
  centerFreq?: number;
  channel?: number;
  phy?: "auto" | "LE1M" | "LE2M";
  connections?: { access_address: string; crc_init: number | string }[];
};

function bleFormData(file: File, opts?: BleDecodeOptions): FormData {
  const form = new FormData();
  form.append("file", file);
  form.append("dtype", opts?.dtype ?? defaultIqDtype(file));
  if (opts?.sampleRate != null)
    form.append("sample_rate", String(opts.sampleRate));
  if (opts?.centerFreq != null)
    form.append("center_freq", String(opts.centerFreq));
  if (opts?.channel != null) form.append("channel", String(opts.channel));
  form.append("phy", opts?.phy ?? "auto");
  form.append("connection_contexts", JSON.stringify(opts?.connections ?? []));
  return form;
}

export async function decodeBle(
  file: File,
  opts?: BleDecodeOptions,
): Promise<BleDecodeResult> {
  const res = await fetch(`${API_BASE}/analyze/ble`, {
    method: "POST",
    body: bleFormData(file, opts),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export { API_BASE };

export type BleCaptureAnalysis = {
  backend: string;
  version: string;
  packet_count: number;
  packet_limit: number;
  protocols: string[];
  packets: {
    number: string;
    timestamp: string;
    length: string;
    protocols: string[];
    summary: string;
    layers: Record<string, unknown>;
  }[];
  warnings: string[];
};

export async function analyzeBleCapture(
  file: File,
): Promise<BleCaptureAnalysis> {
  if (file.size > 32 * 1024 * 1024) throw new Error("Capture exceeds 32 MiB");
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${API_BASE}/analyze/ble/capture`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export async function exportBleCapture(
  file: File,
  opts?: BleDecodeOptions,
): Promise<Blob> {
  const form = bleFormData(file, opts);
  form.append("output_format", "pcap");
  const response = await fetch(`${API_BASE}/analyze/ble`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) throw new Error(await response.text());
  return response.blob();
}

export async function decryptBlePayload(body: {
  session_key_hex: string;
  iv_hex: string;
  header_hex: string;
  ciphertext_mic_hex: string;
  packet_counter: number;
  direction: string;
}): Promise<{ plaintext_hex: string; mic_ok: boolean }> {
  const response = await fetch(`${API_BASE}/analyze/ble/decrypt`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export type Spectrum = {
  start_hz: number;
  bin_hz: number;
  power_dbfs: (number | null)[];
  noise_floor_dbfs: number;
  timestamp: number;
  swept?: boolean;
};
export type SignalTrack = {
  id: string;
  name: string;
  identity_type: string;
  identity_note: string;
  label: string;
  confidence: number;
  center_freq_hz: number;
  bandwidth_hz: number;
  power_dbfs: number;
  last_seen: number;
  observations: number;
  packet_count: number;
  channels: number[];
  frame_id: string;
  evidence: string;
  device_address?: string;
  alternatives?: { label: string; confidence: number }[];
  history?: number[];
  snr_db?: number | null;
  first_seen?: number;
  band_plan?: { label?: string; service?: string; channel?: number } | null;
  discovered_name?: string;
  low_hz?: number;
  high_hz?: number;
};
export type SavedSignal = {
  id: string;
  name: string;
  created_at: number;
  bytes: number;
  duration_s: number;
  center_freq_hz: number;
  sample_rate_hz: number;
  device_name?: string;
  demo: boolean;
  duplicate?: boolean;
  classification?: { label: string; confidence: number };
};
export type LiveFrame = {
  /** "spectrum" arrives ahead of the full "frame" it belongs to. */
  type: string;
  frame_id?: string;
  spectrum: Spectrum;
  sweep?: Spectrum;
  playback?: boolean;
  progress?: number;
  recording?: SavedSignal;
  tracks: SignalTrack[];
  observations: SignalTrack[];
  prediction: AnalyzeResult;
  scanner?: {
    enabled: boolean;
    position: number;
    steps: number;
    sweeps: number;
  };
  tuner: {
    center_freq_hz: number;
    sample_rate_hz: number;
    lna_gain_db?: number | null;
    vga_gain_db?: number | null;
    amp_enable?: boolean | null;
    backend: string;
    demo: boolean;
    rf_power_dbfs?: number;
    ble_hop?: boolean;
    ble_decode?: boolean;
    upconverter?: string;
    lo_offset_hz?: number;
  };
  ble?: BleDecodeResult;
};
export async function signalRequest<T>(
  path: string,
  body?: unknown,
  method = "POST",
): Promise<T> {
  const response = await fetch(
    `${API_BASE}${path}`,
    body === undefined
      ? {}
      : {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.detail || `Request failed (${response.status})`);
  }
  return response.json();
}
