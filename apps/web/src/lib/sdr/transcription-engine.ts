import { resampleLinear, rmsLevel } from "./audio-utils";

export type TranscriptWord = { w: string; start: number; end: number; p: number };

export type TranscriptSegment = {
  id: string;
  text: string;
  at: number;
  /** Set by the server engine: 0-1 mean word confidence. */
  confidence?: number;
  /** Seconds since the session started (server stream clock). */
  startS?: number;
  endS?: number;
  frequencyHz?: number | null;
  mode?: string;
  sourceLabel?: string;
  language?: string;
  latencyMs?: number;
  words?: TranscriptWord[];
};

/** What the server reports about its speech model (GET /transcribe/status). */
export type ServerSttStatus = {
  available: boolean;
  ready: boolean;
  loading: boolean;
  error: string | null;
  model: string;
  device: string;
  compute_type: string;
  cached: boolean;
  gpu: boolean;
  vram_mb: number | null;
  sessions: number;
};

export type TranscriptionEngineKind = "server" | "device";

export type TranscriptionStatus =
  | "idle"
  | "loading"
  | "ready"
  | "listening"
  | "transcribing"
  | "error";

export type TranscriptionState = {
  enabled: boolean;
  status: TranscriptionStatus;
  progress: number;
  segments: TranscriptSegment[];
  error: string | null;
  /** Which engine produced this state (the hub sets it). */
  engine?: TranscriptionEngineKind;
  /** Utterance still being spoken; revised until its final arrives. */
  partial?: TranscriptSegment | null;
  /** Server decode latency for the last result, ms. */
  latencyMs?: number | null;
  /** Human-readable detail, e.g. "Reconnecting…" or "Loading model on the server". */
  note?: string | null;
  server?: ServerSttStatus | null;
};

export type TranscriptionListener = (state: TranscriptionState) => void;

const TARGET_RATE = 16_000;
const CHUNK_SECONDS = 4;
const OVERLAP_SECONDS = 0.75;
const SILENCE_RMS = 0.008;
const MODEL_ID = "onnx-community/whisper-tiny";
const MAX_SEGMENTS = 80;

type AsrPipeline = (
  audio: Float32Array,
  options?: Record<string, unknown>,
) => Promise<{ text: string } | { text: string }[]>;

/**
 * Buffers demodulated PCM and runs multilingual browser-side Whisper in chunks.
 */
export class TranscriptionEngine {
  private state: TranscriptionState = {
    enabled: false,
    status: "idle",
    progress: 0,
    segments: [],
    error: null,
  };
  private listeners = new Set<TranscriptionListener>();
  private buffer = new Float32Array(0);
  private sampleRate = 48_000;
  private pipeline: AsrPipeline | null = null;
  private loadPromise: Promise<void> | null = null;
  private busy = false;
  private disposed = false;
  private options = { language: "en", task: "transcribe" as "transcribe" | "translate" };
  private optionsVersion = 0;

  setOptions(options: { language: string; task?: "transcribe" | "translate" }): void {
    const next = { language: options.language, task: options.task ?? "transcribe" };
    if (next.language === this.options.language && next.task === this.options.task) return;
    this.options = next;
    this.optionsVersion++;
    this.buffer = new Float32Array(0);
  }

  getState(): TranscriptionState {
    return this.state;
  }

  subscribe(listener: TranscriptionListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  async setEnabled(enabled: boolean): Promise<void> {
    if (enabled === this.state.enabled) return;
    if (!enabled) {
      this.buffer = new Float32Array(0);
      this.patch({
        enabled: false,
        status: this.pipeline ? "ready" : "idle",
        error: null,
      });
      return;
    }

    this.patch({ enabled: true, error: null });
    try {
      await this.ensureModel();
      if (this.disposed) return;
      this.patch({ status: "listening" });
    } catch (err) {
      this.patch({
        enabled: false,
        status: "error",
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  clear(): void {
    this.patch({ segments: [], error: null });
  }

  dispose(): void {
    this.disposed = true;
    this.buffer = new Float32Array(0);
    this.pipeline = null;
    this.loadPromise = null;
    this.patch({
      enabled: false,
      status: "idle",
      progress: 0,
      error: null,
    });
  }

  pushAudio(mono: Float32Array, sampleRate: number): void {
    if (!this.state.enabled || this.disposed) return;
    this.sampleRate = sampleRate;

    const merged = new Float32Array(this.buffer.length + mono.length);
    merged.set(this.buffer);
    merged.set(mono, this.buffer.length);
    this.buffer = merged;

    const need = Math.floor(CHUNK_SECONDS * sampleRate);
    if (this.buffer.length >= need && !this.busy) {
      void this.processChunk();
    }
  }

  private async ensureModel(): Promise<void> {
    if (this.pipeline) {
      this.patch({ status: "ready", progress: 1 });
      return;
    }
    if (this.loadPromise) return this.loadPromise;

    this.loadPromise = (async () => {
      this.patch({ status: "loading", progress: 0, error: null });
      const { pipeline, env } = await import("@huggingface/transformers");
      env.allowLocalModels = false;
      // CacheStorage is unavailable on plain HTTP LAN origins. Forcing it on
      // makes Transformers.js throw before any inference backend can load.
      env.useBrowserCache = typeof globalThis.caches !== "undefined";

      const progress_callback = (data: {
        status?: string;
        progress?: number;
      }) => {
        if (typeof data.progress === "number") {
          const p = data.progress > 1 ? data.progress / 100 : data.progress;
          this.patch({ progress: Math.max(0, Math.min(1, p)) });
        }
      };

      let asr: AsrPipeline;
      try {
        asr = (await pipeline("automatic-speech-recognition", MODEL_ID, {
          device: "webgpu",
          dtype: "fp32",
          progress_callback,
        })) as AsrPipeline;
      } catch {
        try {
          asr = (await pipeline("automatic-speech-recognition", MODEL_ID, {
            device: "wasm",
            dtype: "fp32",
            progress_callback,
          })) as AsrPipeline;
        } catch {
          asr = (await pipeline("automatic-speech-recognition", MODEL_ID, {
            device: "wasm",
            dtype: "q8",
            progress_callback,
          })) as AsrPipeline;
        }
      }

      this.pipeline = asr;
      this.patch({ status: "ready", progress: 1 });
    })();

    try {
      await this.loadPromise;
    } catch (err) {
      this.loadPromise = null;
      throw err;
    }
  }

  private async processChunk(): Promise<void> {
    if (!this.pipeline || this.busy || !this.state.enabled) return;

    const need = Math.floor(CHUNK_SECONDS * this.sampleRate);
    if (this.buffer.length < need) return;

    const chunk = this.buffer.slice(0, need);
    const keep = Math.floor(OVERLAP_SECONDS * this.sampleRate);
    this.buffer = this.buffer.slice(Math.max(0, need - keep));

    if (rmsLevel(chunk) < SILENCE_RMS) {
      this.patch({ status: "listening" });
      return;
    }

    this.busy = true;
    const version = this.optionsVersion;
    const options = { ...this.options };
    this.patch({ status: "transcribing" });

    try {
      const audio16k = resampleLinear(chunk, this.sampleRate, TARGET_RATE);
      const result = await this.pipeline(audio16k, {
        task: options.task,
        ...(options.language === "auto" ? {} : { language: options.language }),
      });
      if (version !== this.optionsVersion) {
        this.patch({ status: this.state.enabled ? "listening" : "ready" });
        return;
      }
      const raw = Array.isArray(result) ? result[0]?.text : result.text;
      const text = cleanTranscript(raw ?? "");
      if (text) {
        const segment: TranscriptSegment = {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          text,
          at: Date.now(),
        };
        const segments = [...this.state.segments, segment].slice(-MAX_SEGMENTS);
        this.patch({
          segments,
          status: this.state.enabled ? "listening" : "ready",
        });
      } else {
        this.patch({ status: this.state.enabled ? "listening" : "ready" });
      }
    } catch (err) {
      this.patch({
        status: "error",
        error: err instanceof Error ? err.message : String(err),
      });
    } finally {
      this.busy = false;
      // Drain if more audio queued while we were busy.
      const needMore = Math.floor(CHUNK_SECONDS * this.sampleRate);
      if (this.state.enabled && this.buffer.length >= needMore) {
        void this.processChunk();
      }
    }
  }

  private patch(partial: Partial<TranscriptionState>): void {
    this.state = { ...this.state, ...partial };
    for (const listener of this.listeners) {
      listener(this.state);
    }
  }
}

function cleanTranscript(text: string): string {
  const trimmed = text.replace(/\s+/g, " ").trim();
  if (!trimmed) return "";
  // Drop common Whisper silence hallucinations.
  const lower = trimmed.toLowerCase();
  const junk = [
    "thanks for watching",
    "thank you for watching",
    "subscribe",
    "♪",
    "[blank_audio]",
    "(blank_audio)",
    "you",
  ];
  if (junk.some((j) => lower === j || lower === `${j}.`)) return "";
  if (trimmed.length < 2) return "";
  return trimmed;
}
