/**
 * Pure digital-voice UI logic: event reducer, call-log filtering/sorting,
 * formatters, exports and the config payload. No DOM, no network, and only type
 * imports, so node tests can load this file on its own.
 */
import type {
  DvCall,
  DvEncryptionInfo,
  DvEngineStatus,
  DvError,
  DvEvent,
  DvLinkStats,
  DvProtocol,
  DvSystemInfo,
  DvTalkgroup,
} from "./types";

export const MAX_CALLS = 500;
export const MAX_ERRORS = 20;

export type DvConnection = "idle" | "connecting" | "open" | "closed" | "unavailable";

/** A call document as the engine's recorder serves it: the Call plus what it stored. */
export type DvCallRecord = DvCall & { has_audio?: boolean; transcript?: string | null };

/** Out-of-band speech-to-text for a call (`{"type":"transcript","kind":...}`; no envelope). */
export type DvTranscriptMessage = {
  kind: "partial" | "final";
  call_id: string;
  text: string;
};

export type DvCallTranscript = { final: string[]; partial: string };

export type DvState = {
  connection: DvConnection;
  engine: DvEngineStatus | null;
  links: Record<string, DvLinkStats>;
  systems: Record<string, DvSystemInfo>;
  /** Newest first; includes calls still in progress (ended_at === null). */
  calls: DvCall[];
  talkgroups: Record<string, DvTalkgroup>;
  errors: DvError[];
  transcripts: Record<string, DvCallTranscript>;
  /** Epoch ms (envelope `t`) of the newest event, for "no data" detection. */
  lastEventAt: number | null;
};

export const INITIAL_STATE: DvState = {
  connection: "idle",
  engine: null,
  links: {},
  systems: {},
  calls: [],
  talkgroups: {},
  errors: [],
  transcripts: {},
  lastEventAt: null,
};

const MAX_TRANSCRIPTS = 200;

/** Fold one transcript message in: partials replace the pending text, finals append. */
export function reduceTranscript(state: DvState, msg: DvTranscriptMessage): DvState {
  const text = (msg.text ?? "").trim();
  if (!msg.call_id) return state;
  const current = state.transcripts[msg.call_id] ?? { final: [], partial: "" };
  const next: DvCallTranscript =
    msg.kind === "final"
      ? { final: text ? [...current.final, text] : current.final, partial: "" }
      : { final: current.final, partial: text };
  const transcripts = { ...state.transcripts, [msg.call_id]: next };
  const ids = Object.keys(transcripts);
  if (ids.length > MAX_TRANSCRIPTS) delete transcripts[ids[0]];
  return { ...state, transcripts };
}

/** What to show as a call's transcript: live text first, else what the recorder stored. */
export function transcriptText(state: DvState, call: DvCall & { transcript?: string | null }): string {
  const live = state.transcripts[call.call_id];
  const parts = live ? [...live.final, live.partial].filter(Boolean) : [];
  if (parts.length) return parts.join(" ");
  return (call.transcript ?? "").trim();
}

export function talkgroupKey(protocol: DvProtocol, id: number): string {
  return `${protocol}:${id}`;
}

function upsertCall(calls: DvCall[], call: DvCall): DvCall[] {
  const index = calls.findIndex((c) => c.call_id === call.call_id);
  if (index === -1) return [call, ...calls].slice(0, MAX_CALLS);
  const next = calls.slice();
  next[index] = call;
  return next;
}

/** Fold one engine event into the state. Returns the same object for no-ops. */
export function reduceEvent(state: DvState, event: DvEvent): DvState {
  const base = { ...state, lastEventAt: event.t };
  switch (event.type) {
    case "status":
      return { ...base, engine: event.data };
    case "link_stats":
      return { ...base, links: { ...state.links, [event.channel]: event.data } };
    case "system_info":
      // One entry per channel and protocol: a channel can report several systems (e.g. P25 and DMR).
      return { ...base, systems: { ...state.systems, [`${event.channel}:${event.data.protocol}`]: event.data } };
    case "call_start":
    case "call_update":
    case "call_end":
      return { ...base, calls: upsertCall(state.calls, event.data) };
    case "talkgroup": {
      const key = talkgroupKey(event.data.protocol, event.data.id);
      return { ...base, talkgroups: { ...state.talkgroups, [key]: event.data } };
    }
    case "error":
      return { ...base, errors: [event.data, ...state.errors].slice(0, MAX_ERRORS) };
    case "sync":
    case "trunk":
      return base;
    default:
      return state;
  }
}

/** Replace the log with what GET /dvoice/calls returned, keeping live updates that are newer. */
export function mergeCallLog(state: DvState, fetched: DvCall[]): DvState {
  const byId = new Map<string, DvCall>();
  for (const call of fetched) byId.set(call.call_id, call);
  for (const call of state.calls) {
    const other = byId.get(call.call_id);
    if (!other || call.updated_at >= other.updated_at) byId.set(call.call_id, call);
  }
  const calls = [...byId.values()].sort((a, b) => b.started_at - a.started_at).slice(0, MAX_CALLS);
  return { ...state, calls };
}

export function removeCall(state: DvState, callId: string): DvState {
  return { ...state, calls: state.calls.filter((c) => c.call_id !== callId) };
}

export function activeCalls(state: DvState): DvCall[] {
  return state.calls.filter((c) => c.ended_at === null);
}

// ---------------------------------------------------------------- formatting

export const PROTOCOL_LABELS: Record<DvProtocol, string> = {
  p25p1: "P25 Phase 1",
  p25p2: "P25 Phase 2",
  dmr: "DMR",
  nxdn48: "NXDN 4800",
  nxdn96: "NXDN 9600",
  dstar: "D-STAR",
  ysf: "YSF",
  m17: "M17",
  dpmr: "dPMR",
  x2tdma: "X2-TDMA",
  provoice: "ProVoice",
  edacs: "EDACS",
  unknown: "Unknown",
};

export const PROTOCOL_SHORT: Record<DvProtocol, string> = {
  p25p1: "P25",
  p25p2: "P25-2",
  dmr: "DMR",
  nxdn48: "NXDN",
  nxdn96: "NXDN",
  dstar: "D-STAR",
  ysf: "YSF",
  m17: "M17",
  dpmr: "dPMR",
  x2tdma: "X2",
  provoice: "PV",
  edacs: "EDACS",
  unknown: "?",
};

/** What the protocol selector offers. `p25p2` is shown but marked not yet available. */
export const PROTOCOL_CHOICES: { id: "auto" | DvProtocol; label: string; note?: string }[] = [
  { id: "auto", label: "Auto" },
  { id: "p25p1", label: "P25 P1" },
  { id: "p25p2", label: "P25 P2", note: "not yet" },
  { id: "dmr", label: "DMR" },
  { id: "nxdn48", label: "NXDN 48" },
  { id: "nxdn96", label: "NXDN 96" },
  { id: "dstar", label: "D-STAR" },
  { id: "ysf", label: "YSF" },
  { id: "m17", label: "M17" },
];

export function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 10) return `${seconds.toFixed(1)} s`;
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}:${String(s).padStart(2, "0")}` : `${s} s`;
}

/** Call timestamps (started_at/updated_at/ended_at) are epoch SECONDS; event envelopes' `t` is ms. */
export const nowSeconds = (): number => Date.now() / 1000;

export function callDurationS(call: DvCall, now: number = nowSeconds()): number {
  const end = call.ended_at ?? now;
  return Math.max(0, end - call.started_at);
}

export function formatId(id: number | null): string {
  return id === null ? "—" : String(id);
}

export function formatNac(nac: number | null): string {
  return nac === null ? "—" : `0x${nac.toString(16).toUpperCase().padStart(3, "0")}`;
}

export function formatPercent(value: number | null, digits = 1): string {
  return value === null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
}

export function formatMhz(hz: number | null): string {
  return hz === null ? "—" : `${(hz / 1e6).toFixed(4)} MHz`;
}

export function formatClock(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export type Aliases = Record<string, string>;

export function aliasFor(protocol: DvProtocol, id: number | null, aliases: Aliases): string | null {
  if (id === null) return null;
  return aliases[talkgroupKey(protocol, id)] ?? aliases[String(id)] ?? null;
}

/** "Dispatch (4101)" when there is an alias, else "TG 4101" / "ID 4101" for private calls. */
export function callTitle(call: DvCall, aliases: Aliases): string {
  const alias = aliasFor(call.protocol, call.target_id, aliases);
  const kind = call.kind === "private" ? "ID" : call.kind === "all" ? "All call" : "TG";
  if (call.target_id === null) return call.kind === "all" ? "All call" : "Unknown target";
  return alias ? `${alias} (${call.target_id})` : `${kind} ${call.target_id}`;
}

export function encryptionBadge(enc: DvEncryptionInfo): { label: string; tone: "clear" | "encrypted" | "unknown" } {
  if (enc.state === "encrypted") {
    const algo = enc.algorithm_name ?? (enc.algorithm_id !== null ? `ALG 0x${enc.algorithm_id.toString(16).toUpperCase()}` : null);
    const key = enc.key_id !== null ? ` · key ${enc.key_id}` : "";
    return { label: algo ? `Encrypted · ${algo}${key}` : "Encrypted", tone: "encrypted" };
  }
  if (enc.state === "clear") return { label: "Clear", tone: "clear" };
  return { label: "Unknown", tone: "unknown" };
}

/** True when this call can never have audio, whatever the vocoder state. */
export function isEncrypted(call: DvCall): boolean {
  return call.encryption.state === "encrypted";
}

// ------------------------------------------------------------ filter / sort

export type CallFlagFilter = "all" | "clear" | "encrypted" | "emergency";
export type CallSortKey = "time" | "talkgroup" | "protocol" | "duration";

export type CallFilter = {
  query: string;
  protocol: "all" | DvProtocol;
  flag: CallFlagFilter;
  minDurationS: number;
};

export const DEFAULT_FILTER: CallFilter = { query: "", protocol: "all", flag: "all", minDurationS: 0 };

export function activeFilterCount(f: CallFilter): number {
  return (
    (f.query.trim() ? 1 : 0) +
    (f.protocol !== "all" ? 1 : 0) +
    (f.flag !== "all" ? 1 : 0) +
    (f.minDurationS > 0 ? 1 : 0)
  );
}

export function filterCalls(calls: DvCall[], filter: CallFilter, aliases: Aliases, now: number = nowSeconds()): DvCall[] {
  const q = filter.query.trim().toLowerCase();
  return calls.filter((call) => {
    if (filter.protocol !== "all" && call.protocol !== filter.protocol) return false;
    if (filter.flag === "encrypted" && !isEncrypted(call)) return false;
    if (filter.flag === "clear" && call.encryption.state !== "clear") return false;
    if (filter.flag === "emergency" && !call.emergency) return false;
    if (filter.minDurationS > 0 && callDurationS(call, now) < filter.minDurationS) return false;
    if (q) {
      const hay = [
        callTitle(call, aliases),
        PROTOCOL_LABELS[call.protocol],
        String(call.target_id ?? ""),
        String(call.source_id ?? ""),
        call.channel ?? "",
        call.encryption.algorithm_name ?? "",
      ]
        .join(" ")
        .toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

/** Stable sort; ties fall back to newest first so equal rows never swap. */
export function sortCalls(
  calls: DvCall[],
  key: CallSortKey,
  dir: "asc" | "desc",
  aliases: Aliases,
  now: number = nowSeconds(),
): DvCall[] {
  const sign = dir === "asc" ? 1 : -1;
  const value = (c: DvCall): number | string => {
    switch (key) {
      case "time":
        return c.started_at;
      case "talkgroup":
        return c.target_id ?? -1;
      case "protocol":
        return PROTOCOL_LABELS[c.protocol];
      case "duration":
        return callDurationS(c, now);
    }
  };
  return calls.slice().sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    const cmp =
      typeof va === "string" && typeof vb === "string"
        ? va.localeCompare(vb, undefined, { sensitivity: "base" })
        : (va as number) - (vb as number);
    if (cmp !== 0) return cmp * sign;
    return b.started_at - a.started_at || a.call_id.localeCompare(b.call_id);
  });
}

// ----------------------------------------------------------------- exports

function csvCell(value: unknown): string {
  const text = value === null || value === undefined ? "" : String(value);
  // Guard against spreadsheet formula injection from radio-supplied text.
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

export const CSV_COLUMNS = [
  "call_id",
  "protocol",
  "slot",
  "kind",
  "target_id",
  "alias",
  "source_id",
  "frequency_hz",
  "started_at_iso",
  "duration_s",
  "emergency",
  "encryption",
  "algorithm",
  "key_id",
  "voice_frames",
  "voice_frames_bad",
  "ber",
  "end_reason",
] as const;

export function callsToCsv(calls: DvCall[], aliases: Aliases): string {
  const rows = calls.map((c) =>
    [
      c.call_id,
      c.protocol,
      c.slot,
      c.kind,
      c.target_id,
      aliasFor(c.protocol, c.target_id, aliases),
      c.source_id,
      c.frequency_hz,
      new Date(c.started_at * 1000).toISOString(),
      callDurationS(c).toFixed(2),
      c.emergency,
      c.encryption.state,
      c.encryption.algorithm_name ?? c.encryption.algorithm_id,
      c.encryption.key_id,
      c.voice_frames,
      c.voice_frames_bad,
      c.ber,
      c.end_reason,
    ]
      .map(csvCell)
      .join(","),
  );
  return [CSV_COLUMNS.join(","), ...rows].join("\n") + "\n";
}

export function callsToJson(calls: DvCall[]): string {
  return JSON.stringify(calls, null, 2);
}

// ------------------------------------------------------ errors and hints

/** One-line fix for each stable error code from the engine. */
export function describeError(error: DvError): { title: string; fix: string } {
  switch (error.code) {
    case "vocoder_unavailable":
      return {
        title: "Voice audio is off",
        fix: "Call details still work. Set up the local codec to hear this protocol.",
      };
    case "sync_lost":
      return { title: "Signal lost", fix: "Check the tuning, antenna and gain, then wait for sync." };
    case "input_overrun":
      return { title: "Decoder fell behind", fix: "Some samples were dropped. Close other heavy panels or lower the sample rate." };
    case "input_stalled":
      return { title: "No samples arriving", fix: "The receiver stopped sending data. Reconnect the source." };
    case "unsupported_protocol":
      return { title: "Protocol not supported", fix: "Pick a supported protocol or leave it on Auto." };
    case "capture_unavailable":
      return { title: "Capture stopped", fix: "Connect the HackRF (or another source) and try again." };
    default:
      return { title: error.message || "Engine error", fix: "" };
  }
}

// ----------------------------------------------------- talkgroup preferences

export type TalkgroupRule = "normal" | "hold" | "skip" | "priority";

export type TalkgroupPrefs = {
  /** Keyed by `${protocol}:${id}`. */
  aliases: Aliases;
  rules: Record<string, TalkgroupRule>;
};

export const EMPTY_PREFS: TalkgroupPrefs = { aliases: {}, rules: {} };

export type DvSettings = {
  protocol: "auto" | DvProtocol;
  /** Hang time in seconds before returning to the control channel. */
  hangTimeS: number;
  /** Save clear-call audio on the API computer (encrypted calls are never recorded). */
  recording: boolean;
  /** Cap on stored audio, in GB; the engine drops the oldest recordings past it. */
  maxRecordingGb: number;
  /** Transcribe clear calls on the server as they happen. */
  transcribe: boolean;
  language: string;
  /** Follow the most recent call automatically. */
  autoListen: boolean;
};

export const DEFAULT_SETTINGS: DvSettings = {
  protocol: "auto",
  hangTimeS: 2,
  recording: false,
  maxRecordingGb: 5,
  transcribe: false,
  language: "en",
  autoListen: true,
};

function idsWithRule(prefs: TalkgroupPrefs, rule: TalkgroupRule): number[] {
  return Object.entries(prefs.rules)
    .filter(([, r]) => r === rule)
    .map(([key]) => Number(key.split(":").pop()))
    .filter((n) => Number.isFinite(n));
}

/**
 * The JSON for POST /dvoice/config (apps/api dvoice/api.py ConfigBody). `enabled` starts or stops
 * the engine. Encrypted calls are never followed, decoded or recorded, so there is no setting for it.
 */
export function buildConfigPayload(args: {
  settings: DvSettings;
  prefs: TalkgroupPrefs;
  enabled: boolean;
  source: "hackrf" | "upload";
  channel?: string;
  offsetHz: number;
  frequencyHz: number | null;
}): Record<string, unknown> {
  const { settings, prefs } = args;
  const labels: Record<string, string> = {};
  for (const [key, alias] of Object.entries(prefs.aliases)) {
    const id = key.split(":").pop();
    if (id && alias.trim()) labels[id] = alias.trim();
  }
  return {
    enabled: args.enabled,
    source: args.source,
    channels: [
      {
        name: args.channel ?? "ch0",
        protocol: settings.protocol,
        offset_hz: args.offsetHz,
        frequency_hz: args.frequencyHz,
        bandwidth_hz: 12_500,
        invert: null,
      },
    ],
    follow: {
      hold_talkgroups: idsWithRule(prefs, "hold"),
      skip_talkgroups: idsWithRule(prefs, "skip"),
      priority_talkgroups: idsWithRule(prefs, "priority"),
      hang_time_s: settings.hangTimeS,
    },
    talkgroup_labels: labels,
    record: settings.recording ? "audio" : "off",
    max_recording_gb: settings.maxRecordingGb,
    audio: true,
    transcribe: settings.transcribe,
    language: settings.language,
  };
}

// ------------------------------------------------------- link health

export type SignalGrade = "none" | "weak" | "fair" | "good";

/** A coarse quality grade for the meter from sync ratio and BER. */
export function linkGrade(link: DvLinkStats | undefined): SignalGrade {
  if (!link || link.state === "searching" || link.state === "lost") return "none";
  const ber = link.ber ?? 0;
  if (link.sync_ratio >= 0.9 && ber < 0.01) return "good";
  if (link.sync_ratio >= 0.6 && ber < 0.05) return "fair";
  return "weak";
}

export function linkStateLabel(link: DvLinkStats | undefined): string {
  if (!link) return "No data";
  switch (link.state) {
    case "voice":
      return "Voice";
    case "synced":
      return "Synced";
    case "lost":
      return "Signal lost";
    default:
      return "Searching…";
  }
}

// ------------------------------------------------------ voice availability

/** Vocoder id (events.py VocoderId) that decodes each protocol's voice, or null for none. */
export function vocoderIdFor(protocol: DvProtocol): string | null {
  switch (protocol) {
    case "p25p1":
      return "imbe_7200x4400";
    case "dmr":
    case "nxdn48":
    case "nxdn96":
    case "ysf":
    case "p25p2":
      return "ambe_plus2_3600x2450";
    case "dstar":
      return "ambe_3600x2400";
    case "m17":
      return "codec2_3200";
    default:
      return null;
  }
}

const AUDIO_LABELS: Partial<Record<DvProtocol, string[]>> = {
  p25p1: ["P25 Phase 1"],
  p25p2: ["P25 Phase 2"],
  dmr: ["DMR"],
  nxdn48: ["NXDN"],
  nxdn96: ["NXDN"],
  ysf: ["YSF"],
  dstar: ["D-STAR"],
  m17: ["M17"],
};

export type VoiceSources = {
  /** `vocoders` from the engine's status event (preferred: it is live). */
  engineVocoders?: Record<string, { available: boolean }> | null;
  /** `protocols_with_audio` from the local codec status. */
  localProtocols?: string[] | null;
  /** Active backends from the licence notices, e.g. Codec2 for M17. */
  backends?: { codec: string; active: boolean }[] | null;
};

/** Can this protocol's clear voice be turned into audio right now? */
export function voiceAvailable(protocol: DvProtocol, sources: VoiceSources): boolean {
  const id = vocoderIdFor(protocol);
  if (id === null) return false;
  const live = sources.engineVocoders?.[id];
  if (live) return live.available;
  if (AUDIO_LABELS[protocol]?.some((label) => sources.localProtocols?.includes(label))) return true;
  const codec = id.startsWith("codec2") ? "codec2" : id.split("_")[0];
  return Boolean(sources.backends?.some((b) => b.active && b.codec.toLowerCase().includes(codec)));
}
