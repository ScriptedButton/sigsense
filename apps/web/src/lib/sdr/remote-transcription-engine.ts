import { API_BASE, wsUrl } from "@/lib/api";

import type {
  ServerSttStatus,
  TranscriptSegment,
  TranscriptionListener,
  TranscriptionState,
  TranscriptWord,
} from "./transcription-engine";

const TARGET_RATE = 16_000;
/** ~160 ms of audio per frame: small enough for low latency, big enough to be cheap. */
const FRAME_SAMPLES = 2560;
/** Drop audio rather than queue it when the socket backs up (a stalled phone link). */
const MAX_BUFFERED_BYTES = 256 * 1024;
const MAX_SEGMENTS = 200;
const META_INTERVAL_MS = 500;

export type TranscribeOptions = {
  language: string; // "auto" or an ISO code
  prompt: string; // extra vocabulary hints
};

export type TranscribeMeta = {
  frequencyHz: number | null;
  mode: string;
  sourceLabel: string;
};

/** Sent to a server-side tap (HackRF) instead of uploading audio. */
export type TranscribeTapConfig = {
  enabled: boolean;
  language: string;
  prompt: string;
  frequency_hz: number | null;
  mode: string;
  source_label: string;
};

type ServerEvent = {
  type: string;
  id?: string;
  text?: string;
  dropped?: boolean;
  start_s?: number;
  end_s?: number;
  confidence?: number;
  language?: string;
  latency_ms?: number;
  words?: TranscriptWord[];
  frequency_hz?: number | null;
  mode?: string;
  source_label?: string;
  state?: string;
  message?: string;
} & Partial<ServerSttStatus>;

export async function fetchSttStatus(): Promise<ServerSttStatus | null> {
  try {
    const response = await fetch(`${API_BASE}/transcribe/status`, { cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as ServerSttStatus;
  } catch {
    return null;
  }
}

/**
 * Anti-aliased streaming downsampler to 16 kHz mono int16. A windowed-sinc low
 * pass runs first, then linear interpolation picks the 16 kHz samples, with the
 * filter tail and fractional position carried across chunks so there are no
 * seams between audio callbacks.
 */
export class Downsampler {
  private readonly taps: Float32Array;
  private tail: Float32Array;
  private lastFiltered = 0;
  /** Read position in the next block, where index 0 is `lastFiltered`. */
  private pos = 0;
  private readonly step: number;

  constructor(readonly sourceRate: number) {
    this.step = sourceRate / TARGET_RATE;
    const half = 24;
    const cutoff = Math.min(0.45 * TARGET_RATE, 0.45 * sourceRate) / sourceRate;
    this.taps = new Float32Array(half * 2 + 1);
    let sum = 0;
    for (let i = -half; i <= half; i++) {
      const sinc = i === 0 ? 2 * cutoff : Math.sin(2 * Math.PI * cutoff * i) / (Math.PI * i);
      const window = 0.54 + 0.46 * Math.cos((Math.PI * i) / (half + 1));
      this.taps[i + half] = sinc * window;
      sum += sinc * window;
    }
    for (let i = 0; i < this.taps.length; i++) this.taps[i] /= sum;
    this.tail = new Float32Array(this.taps.length - 1);
  }

  process(input: Float32Array): Float32Array {
    if (this.sourceRate === TARGET_RATE) return input;
    const n = this.taps.length;
    const joined = new Float32Array(this.tail.length + input.length);
    joined.set(this.tail);
    joined.set(input, this.tail.length);
    // One filtered sample per input sample; index 0 is the previous block's last.
    const all = new Float32Array(input.length + 1);
    all[0] = this.lastFiltered;
    for (let i = 0; i < input.length; i++) {
      let acc = 0;
      for (let k = 0; k < n; k++) acc += joined[i + k] * this.taps[k];
      all[i + 1] = acc;
    }
    this.tail = joined.slice(joined.length - (n - 1));
    this.lastFiltered = all[input.length];
    const out: number[] = [];
    let p = this.pos;
    while (p < all.length - 1) {
      const i0 = Math.floor(p);
      const frac = p - i0;
      out.push(all[i0] * (1 - frac) + all[i0 + 1] * frac);
      p += this.step;
    }
    this.pos = p - (all.length - 1);
    return Float32Array.from(out);
  }
}

const EMPTY: TranscriptionState = {
  enabled: false,
  status: "idle",
  progress: 0,
  segments: [],
  error: null,
  engine: "server",
  partial: null,
  latencyMs: null,
  note: null,
  server: null,
};

/**
 * Speech-to-text on the SigSense server: same public surface as the on-device
 * engine, so the controller and panel can use either. Audio goes up as 16 kHz
 * int16 over /transcribe (or, for a HackRF, never leaves the server: the demod
 * audio is tapped there and only transcript events come back).
 */
export class RemoteTranscriptionEngine {
  private state: TranscriptionState = EMPTY;
  private listeners = new Set<TranscriptionListener>();
  private socket: WebSocket | null = null;
  private downsampler: Downsampler | null = null;
  private pending = new Float32Array(FRAME_SAMPLES * 4);
  private pendingLen = 0;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private retries = 0;
  private lastMetaAt = 0;
  private lastMetaKey = "";
  private options: TranscribeOptions = { language: "en", prompt: "" };
  private tapSend: ((config: TranscribeTapConfig) => void) | null = null;
  private tapActive = false;
  private dropped = 0;
  private sessionStart = 0;

  constructor(private readonly getMeta: () => TranscribeMeta) {}

  getState(): TranscriptionState {
    return this.state;
  }

  subscribe(listener: TranscriptionListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  getOptions(): TranscribeOptions {
    return this.options;
  }

  /** Changing language/vocabulary restarts the server session (it keeps the transcript). */
  setOptions(options: Partial<TranscribeOptions>): void {
    this.options = { ...this.options, ...options };
    if (this.state.enabled) this.reconnect();
  }

  /** A server-side audio tap (HackRF) replaces the upload when set. */
  setTap(send: ((config: TranscribeTapConfig) => void) | null): void {
    const was = this.tapSend !== null;
    this.tapSend = send;
    if (this.state.enabled && was !== (send !== null)) this.reconnect();
  }

  async setEnabled(enabled: boolean): Promise<void> {
    if (enabled === this.state.enabled) return;
    if (!enabled) {
      this.teardown();
      this.patch({ enabled: false, status: "idle", error: null, partial: null, note: null });
      return;
    }
    this.sessionStart = Date.now();
    this.patch({ enabled: true, status: "loading", error: null, note: "Connecting to the server…" });
    // Model name / GPU for the status line (the audio tap doesn't report it itself).
    void fetchSttStatus().then((server) => {
      if (server && this.state.enabled && !this.state.server) this.patch({ server });
    });
    this.connect();
  }

  clear(): void {
    this.patch({ segments: [], partial: null, error: null });
  }

  dispose(): void {
    this.teardown();
    this.patch({ enabled: false, status: "idle", partial: null, note: null });
  }

  pushAudio(mono: Float32Array, sampleRate: number): void {
    if (!this.state.enabled || this.tapActive) return;
    const socket = this.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN || this.state.status === "loading") return;
    if (!this.downsampler || this.downsampler.sourceRate !== sampleRate) {
      this.downsampler = new Downsampler(sampleRate);
    }
    const down = this.downsampler.process(mono);
    this.appendPending(down);
    while (this.pendingLen >= FRAME_SAMPLES) {
      this.sendFrame(socket, this.pending.subarray(0, FRAME_SAMPLES));
      this.pending.copyWithin(0, FRAME_SAMPLES, this.pendingLen);
      this.pendingLen -= FRAME_SAMPLES;
    }
    this.maybeSendMeta(socket);
  }

  /** Transcript events from a server-side tap (the HackRF audio socket). */
  ingestServerEvent(event: ServerEvent): void {
    if (event.type === "transcript_status") {
      if (event.state === "error") {
        this.patch({ status: "error", error: event.message ?? "Transcription failed on the server", note: null });
      } else if (event.state === "loading") {
        this.patch({ status: "loading", note: "Loading the speech model on the server…" });
      } else if (event.state === "ready") {
        this.patch({ status: "listening", note: "Transcribing on the server", error: null });
      }
      return;
    }
    this.handleEvent(event);
  }

  // -- connection ------------------------------------------------------------

  private connect(): void {
    if (this.tapSend) {
      this.tapActive = true;
      this.tapSend(this.tapConfig(true));
      return;
    }
    this.tapActive = false;
    let socket: WebSocket;
    try {
      socket = new WebSocket(wsUrl("/transcribe"));
    } catch (err) {
      this.fail(err instanceof Error ? err.message : "Could not open the transcription socket");
      return;
    }
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    this.downsampler = null;
    this.pendingLen = 0;
    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.retries = 0;
      const meta = this.getMeta();
      socket.send(
        JSON.stringify({
          type: "config",
          sample_rate: TARGET_RATE,
          language: this.options.language,
          prompt: this.options.prompt,
          frequency_hz: meta.frequencyHz,
          mode: meta.mode,
          source_label: meta.sourceLabel,
        }),
      );
    };
    socket.onmessage = (message) => {
      if (this.socket !== socket || typeof message.data !== "string") return;
      try {
        this.onServerMessage(JSON.parse(message.data) as ServerEvent);
      } catch {
        /* ignore a malformed frame */
      }
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      if (this.state.enabled) this.scheduleRetry();
    };
    socket.onerror = () => {
      /* onclose follows; the retry path reports it */
    };
  }

  private onServerMessage(event: ServerEvent): void {
    if (event.type === "status") {
      if (event.state === "loading") {
        this.patch({ status: "loading", note: "Loading the speech model on the server (first use takes a few seconds)…" });
      } else if (event.state === "ready") {
        this.patch({ status: "listening", note: null, error: null, server: this.asStatus(event) });
      } else if (event.state === "error") {
        this.patch({ status: "error", error: event.message ?? "The server speech model failed to load", note: null });
        this.stopRetrying();
      }
      return;
    }
    this.handleEvent(event);
  }

  private asStatus(event: ServerEvent): ServerSttStatus | null {
    return event.model
      ? {
          available: Boolean(event.available),
          ready: Boolean(event.ready),
          loading: Boolean(event.loading),
          error: event.error ?? null,
          model: event.model,
          device: event.device ?? "",
          compute_type: event.compute_type ?? "",
          cached: Boolean(event.cached),
          gpu: Boolean(event.gpu),
          vram_mb: event.vram_mb ?? null,
          sessions: event.sessions ?? 0,
        }
      : this.state.server ?? null;
  }

  private handleEvent(event: ServerEvent): void {
    if (event.type !== "partial" && event.type !== "final") return;
    const base = this.sessionStart || Date.now();
    const segment: TranscriptSegment = {
      id: event.id ?? `s${Date.now()}`,
      text: event.text ?? "",
      at: base + (event.end_s ?? 0) * 1000,
      confidence: event.confidence,
      startS: event.start_s,
      endS: event.end_s,
      frequencyHz: event.frequency_hz ?? null,
      mode: event.mode,
      sourceLabel: event.source_label,
      language: event.language,
      latencyMs: event.latency_ms,
      words: event.words,
    };
    if (event.type === "partial") {
      this.patch({ partial: segment, status: "transcribing", latencyMs: event.latency_ms ?? this.state.latencyMs });
      return;
    }
    // Final: retire the partial for this utterance.
    const partial = this.state.partial && this.state.partial.id === segment.id ? null : this.state.partial;
    if (event.dropped || !segment.text.trim()) {
      this.patch({ partial, status: "listening" });
      return;
    }
    const segments = [...this.state.segments, segment].slice(-MAX_SEGMENTS);
    this.patch({ segments, partial, status: "listening", latencyMs: event.latency_ms ?? this.state.latencyMs });
  }

  private tapConfig(enabled: boolean): TranscribeTapConfig {
    const meta = this.getMeta();
    return {
      enabled,
      language: this.options.language,
      prompt: this.options.prompt,
      frequency_hz: meta.frequencyHz,
      mode: meta.mode,
      source_label: meta.sourceLabel,
    };
  }

  // -- audio out ----------------------------------------------------------------

  private appendPending(samples: Float32Array): void {
    if (this.pendingLen + samples.length > this.pending.length) {
      const grown = new Float32Array(Math.max(this.pending.length * 2, this.pendingLen + samples.length));
      grown.set(this.pending.subarray(0, this.pendingLen));
      this.pending = grown;
    }
    this.pending.set(samples, this.pendingLen);
    this.pendingLen += samples.length;
  }

  private sendFrame(socket: WebSocket, samples: Float32Array): void {
    if (socket.bufferedAmount > MAX_BUFFERED_BYTES) {
      this.dropped += 1; // the link can't keep up: skip rather than add latency
      if (this.dropped % 20 === 1) this.patch({ note: "Connection is slow — skipping audio to stay live" });
      return;
    }
    const pcm = new Int16Array(samples.length);
    for (let i = 0; i < samples.length; i++) {
      const v = Math.max(-1, Math.min(1, samples[i]));
      pcm[i] = v < 0 ? v * 32768 : v * 32767;
    }
    socket.send(pcm.buffer);
  }

  private maybeSendMeta(socket: WebSocket): void {
    const now = Date.now();
    if (now - this.lastMetaAt < META_INTERVAL_MS) return;
    this.lastMetaAt = now;
    const meta = this.getMeta();
    const key = `${meta.frequencyHz}|${meta.mode}|${meta.sourceLabel}`;
    if (key === this.lastMetaKey) return;
    this.lastMetaKey = key;
    socket.send(
      JSON.stringify({
        type: "meta",
        frequency_hz: meta.frequencyHz,
        mode: meta.mode,
        source_label: meta.sourceLabel,
      }),
    );
  }

  // -- lifecycle ------------------------------------------------------------------

  private scheduleRetry(): void {
    this.stopRetrying();
    this.retries += 1;
    const delay = Math.min(8000, 500 * 2 ** Math.min(this.retries, 5));
    this.patch({
      status: "loading",
      note: `Server connection lost — reconnecting${this.retries > 2 ? ` (try ${this.retries})` : ""}…`,
    });
    if (this.retries >= 6) {
      this.patch({
        status: "error",
        error: "Can't reach the SigSense server for transcription. Check you're on the same network, or switch to On-device.",
        note: null,
      });
    }
    this.retry = setTimeout(() => {
      this.retry = null;
      if (this.state.enabled) this.connect();
    }, delay);
  }

  private stopRetrying(): void {
    if (this.retry !== null) clearTimeout(this.retry);
    this.retry = null;
  }

  private reconnect(): void {
    this.closeSocket();
    this.stopRetrying();
    this.connect();
  }

  private closeSocket(): void {
    const old = this.socket;
    this.socket = null;
    if (old) {
      try {
        if (old.readyState === WebSocket.OPEN) old.send(JSON.stringify({ type: "flush" }));
        old.close();
      } catch {
        /* already closing */
      }
    }
  }

  private teardown(): void {
    this.stopRetrying();
    this.closeSocket();
    if (this.tapActive && this.tapSend) this.tapSend(this.tapConfig(false));
    this.tapActive = false;
    this.pendingLen = 0;
    this.downsampler = null;
    this.retries = 0;
  }

  private fail(message: string): void {
    this.patch({ status: "error", error: message, note: null });
  }

  private patch(partial: Partial<TranscriptionState>): void {
    this.state = { ...this.state, engine: "server", ...partial };
    for (const listener of this.listeners) listener(this.state);
  }
}
