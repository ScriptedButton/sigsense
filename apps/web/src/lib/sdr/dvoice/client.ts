/**
 * Transport for the digital voice engine: the event/audio WebSocket (with
 * reconnect and event replay) and the REST surface. Contract: the docstring of
 * apps/api/sigsense_api/dvoice/api.py.
 */
import { API_BASE, wsUrl } from "@/lib/api";

import type { DvCallRecord, DvTranscriptMessage } from "./model";
import type { DvEvent } from "./types";
import { parseDvAudioFrame, type DvAudioFrame } from "./types";

const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 10_000;

export type ProtocolSupport = {
  implemented: boolean;
  reason?: string;
  symbol_rate?: number;
  modulation?: string;
  timeslots?: number;
};

export type LocalCodecStatus = {
  installed: boolean;
  path: string | null;
  source: string | null;
  default_path: string;
  default_present: boolean;
  library: string;
  codecs: Record<string, boolean>;
  protocols_with_audio: string[];
  last_error: string;
  build_command: string;
  uninstall_command: string;
  warning: string;
  manifest?: Record<string, unknown> | null;
};

export type LicenceNotices = {
  backends?: {
    name: string;
    codec: string;
    active: boolean;
    reason: string;
    licence: string;
    supplied_by: string;
    shipped_with_sigsense: boolean;
    licence_note: string;
  }[];
  policy?: Record<string, unknown>;
  local_codec?: LocalCodecStatus;
};

export type DvCapabilities = {
  contract_version?: number;
  /** Per protocol: whether this engine can decode it, and why not if it can't. */
  protocols?: Record<string, ProtocolSupport>;
  vocoders?: Record<string, { available: boolean; reason?: string; licence_note?: string; backend?: string }>;
  licence_notices?: LicenceNotices;
  numba?: boolean;
  gpu?: { available: boolean; name?: string };
  speech?: { available?: boolean; ready?: boolean };
  limits?: { max_channels?: number; max_voice_followers?: number };
  inputs?: { note?: string };
  encryption_policy?: string;
  disclaimer?: string;
};

export type DvChannelSnapshot = {
  state?: string;
  protocol?: string | null;
  mode?: string;
  decoder?: string;
  decoder_note?: string;
  offset_hz?: number;
  frequency_hz?: number | null;
  cpu_fraction?: number;
  queue_seconds?: number;
  dropped_items?: number;
};

export type DvStatusResponse = {
  enabled?: boolean;
  source?: string;
  engine?: {
    running?: boolean;
    channels?: Record<string, DvChannelSnapshot>;
    capture?: Record<string, unknown>;
    active_calls?: number;
    metrics?: Record<string, number>;
  };
  /** Where the HackRF samples come from: state "off" | ... plus a reason when something is wrong. */
  feeder?: { state: string; reason: string; fed_samples: number };
  recording?: { mode: string; audio: boolean; persistent: boolean; calls: number };
  disclaimer?: string;
  encryption_policy?: string;
  last_event_id?: number;
};

/** What GET /dvoice/config returns (the engine's persisted settings). */
export type DvServerConfig = {
  enabled: boolean;
  source: string;
  record: string;
  transcribe: boolean;
  language: string;
  max_recording_gb: number;
  talkgroup_labels: Record<string, string>;
  follow: { hold_talkgroups: number[]; skip_talkgroups: number[]; priority_talkgroups: number[]; hang_time_s: number };
  channels: { name: string; protocol: string; offset_hz: number; frequency_hz?: number | null }[];
};

export class DvHttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { cache: "no-store", ...init });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail) && body.detail[0]?.msg) detail = String(body.detail[0].msg);
    } catch {
      /* not JSON */
    }
    throw new DvHttpError(response.status, detail);
  }
  return (await response.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export type CallsPage = { items: DvCallRecord[]; total: number; limit: number; offset: number; next_offset: number | null };

export const dvApi = {
  status: () => request<DvStatusResponse>("/dvoice/status"),
  capabilities: () => request<DvCapabilities>("/dvoice/capabilities"),
  codec: () =>
    request<{ status: LocalCodecStatus; notices: LicenceNotices; capabilities: DvCapabilities["vocoders"] }>("/dvoice/codec"),
  getConfig: () => request<DvServerConfig>("/dvoice/config"),
  config: (payload: Record<string, unknown>) => request<DvServerConfig>("/dvoice/config", json("POST", payload)),
  calls: (limit = 200, offset = 0) => request<CallsPage>(`/dvoice/calls?limit=${limit}&offset=${offset}`),
  call: (callId: string) => request<DvCallRecord>(`/dvoice/calls/${encodeURIComponent(callId)}`),
  deleteCall: (callId: string) =>
    request<unknown>(`/dvoice/calls/${encodeURIComponent(callId)}`, { method: "DELETE" }),
  talkgroups: () => request<{ items: unknown[]; labels: Record<string, string> }>("/dvoice/talkgroups"),
  putTalkgroups: (labels: Record<string, string>) => request<unknown>("/dvoice/talkgroups", json("PUT", { labels })),
  /** The recorded WAV: 404 when none was kept, 403 for encrypted calls (never recorded). */
  audioUrl: (callId: string) => `${API_BASE}/dvoice/calls/${encodeURIComponent(callId)}/audio`,
  exportUrl: (fmt: "json" | "csv") => `${API_BASE}/dvoice/calls/export?fmt=${fmt}`,
};

/** Accept the paginated `{items}` the engine returns, an older `{calls}`, or a bare list. */
export function callsFromResponse(body: { items?: DvCallRecord[]; calls?: DvCallRecord[] } | DvCallRecord[]): DvCallRecord[] {
  if (Array.isArray(body)) return body;
  return body.items ?? body.calls ?? [];
}

const EVENT_TYPES = new Set([
  "status",
  "link_stats",
  "sync",
  "system_info",
  "call_start",
  "call_update",
  "call_end",
  "talkgroup",
  "trunk",
  "error",
]);

export type ParsedMessage =
  | { kind: "event"; event: DvEvent; id: number | null }
  | { kind: "transcript"; transcript: DvTranscriptMessage }
  | { kind: "ignored" };

/** Parse one text frame. Anything that isn't a known message is ignored, never thrown. */
export function parseMessage(text: string): ParsedMessage {
  try {
    const value = JSON.parse(text) as Record<string, unknown>;
    if (!value || typeof value !== "object" || typeof value.type !== "string") return { kind: "ignored" };
    if (value.type === "transcript") {
      const t = value as unknown as { kind?: string; call_id?: string; text?: string };
      if (typeof t.call_id !== "string" || typeof t.text !== "string") return { kind: "ignored" };
      return {
        kind: "transcript",
        transcript: { kind: t.kind === "final" ? "final" : "partial", call_id: t.call_id, text: t.text },
      };
    }
    if (!EVENT_TYPES.has(value.type) || value.data === undefined) return { kind: "ignored" };
    return { kind: "event", event: value as unknown as DvEvent, id: typeof value.id === "number" ? value.id : null };
  } catch {
    return { kind: "ignored" };
  }
}

export type DvClientHandlers = {
  onEvent: (event: DvEvent) => void;
  onTranscript: (transcript: DvTranscriptMessage) => void;
  onAudio: (frame: DvAudioFrame) => void;
  onConnection: (state: "connecting" | "open" | "closed") => void;
};

export class DvClient {
  private socket: WebSocket | null = null;
  private wanted = false;
  private retry = RECONNECT_MIN_MS;
  private timer: ReturnType<typeof setTimeout> | null = null;
  /** Newest numbered event seen; sent on reconnect so the server replays what was missed. */
  private lastEventId: number | null = null;

  constructor(private readonly handlers: DvClientHandlers) {}

  get isOpen(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  start(source = "hackrf", channel = "ch0"): void {
    this.wanted = true;
    this.open(source, channel);
  }

  stop(): void {
    this.wanted = false;
    this.lastEventId = null;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    const socket = this.socket;
    this.socket = null;
    socket?.close();
    this.handlers.onConnection("closed");
  }

  /** Retune or force a protocol on the live channel without reconnecting. */
  sendConfig(config: { channel?: string; protocol?: string; offset_hz?: number; frequency_hz?: number | null }): boolean {
    if (!this.isOpen) return false;
    this.socket!.send(JSON.stringify({ type: "config", channel: "ch0", ...config }));
    return true;
  }

  private open(source: string, channel: string): void {
    if (this.socket) return;
    this.handlers.onConnection("connecting");
    const replay = this.lastEventId !== null ? `&last_event_id=${this.lastEventId}` : "";
    const socket = new WebSocket(
      wsUrl(`/dvoice/stream?source=${encodeURIComponent(source)}&channel=${encodeURIComponent(channel)}${replay}`),
    );
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.retry = RECONNECT_MIN_MS;
      this.handlers.onConnection("open");
    };
    socket.onmessage = (message) => {
      if (this.socket !== socket) return;
      if (typeof message.data === "string") {
        const parsed = parseMessage(message.data);
        if (parsed.kind === "event") {
          if (parsed.id !== null) this.lastEventId = parsed.id;
          this.handlers.onEvent(parsed.event);
        } else if (parsed.kind === "transcript") {
          this.handlers.onTranscript(parsed.transcript);
        }
        return;
      }
      try {
        this.handlers.onAudio(parseDvAudioFrame(message.data as ArrayBuffer));
      } catch {
        /* a malformed frame is dropped, not fatal */
      }
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.handlers.onConnection("closed");
      if (!this.wanted) return;
      this.timer = setTimeout(() => {
        this.timer = null;
        if (this.wanted) this.open(source, channel);
      }, this.retry);
      this.retry = Math.min(RECONNECT_MAX_MS, this.retry * 2);
    };
    socket.onerror = () => socket.close();
  }
}
