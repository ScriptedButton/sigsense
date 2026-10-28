import { resampleLinear } from "../audio-utils";
import type {
  Ft8WorkerRequest,
  Ft8WorkerResponse,
} from "./decode-worker";
import {
  DEFAULT_FT8_CONFIG,
  FT8_CYCLE_SEC,
  FT8_DECODE_RATE,
  type Ft8Config,
  type Ft8Listener,
  type Ft8Message,
  type Ft8State,
} from "./types";

const MAX_MESSAGES = 200;
/** Keep a little more than one cycle so boundary edges are covered. */
const MAX_BUFFER_SEC = 16;

function periodStartMs(nowMs: number): number {
  return Math.floor(nowMs / (FT8_CYCLE_SEC * 1000)) * (FT8_CYCLE_SEC * 1000);
}

/**
 * Buffers demodulated PCM on UTC 15 s FT8 periods and decodes in a worker.
 */
export class Ft8DecoderEngine {
  private state: Ft8State = {
    enabled: false,
    status: "idle",
    config: { ...DEFAULT_FT8_CONFIG },
    messages: [],
    cycleSec: 0,
    error: null,
  };
  private listeners = new Set<Ft8Listener>();
  private buffer = new Float32Array(0);
  private sampleRate = 48_000;
  private periodAt = periodStartMs(Date.now());
  private busy = false;
  private disposed = false;
  private worker: Worker | null = null;
  private nextJobId = 1;
  private pendingJobId: number | null = null;
  private cycleTimer: ReturnType<typeof setInterval> | null = null;

  getState(): Ft8State {
    return this.state;
  }

  subscribe(listener: Ft8Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  setEnabled(enabled: boolean): void {
    if (enabled === this.state.enabled) return;
    if (!enabled) {
      this.stopCycleClock();
      this.buffer = new Float32Array(0);
      this.busy = false;
      this.pendingJobId = null;
      this.patch({
        enabled: false,
        status: "idle",
        cycleSec: 0,
        error: null,
      });
      return;
    }

    this.ensureWorker();
    this.periodAt = periodStartMs(Date.now());
    this.buffer = new Float32Array(0);
    this.startCycleClock();
    this.patch({
      enabled: true,
      status: "listening",
      error: null,
      cycleSec: (Date.now() - this.periodAt) / 1000,
    });
  }

  patchConfig(partial: Partial<Ft8Config>): void {
    const config: Ft8Config = { ...this.state.config, ...partial };
    this.patch({ config });
  }

  clear(): void {
    this.patch({ messages: [], error: null });
  }

  dispose(): void {
    this.disposed = true;
    this.setEnabled(false);
    this.worker?.terminate();
    this.worker = null;
  }

  pushAudio(mono: Float32Array, sampleRate: number): void {
    if (!this.state.enabled || this.disposed || mono.length === 0) return;

    this.sampleRate = sampleRate;
    const now = Date.now();
    const currentPeriod = periodStartMs(now);

    if (currentPeriod > this.periodAt) {
      this.flushPeriod(this.periodAt);
      this.periodAt = currentPeriod;
    }

    const merged = new Float32Array(this.buffer.length + mono.length);
    merged.set(this.buffer);
    merged.set(mono, this.buffer.length);
    const maxSamples = Math.floor(MAX_BUFFER_SEC * sampleRate);
    this.buffer =
      merged.length > maxSamples
        ? merged.subarray(merged.length - maxSamples)
        : merged;
  }

  private flushPeriod(periodAt: number): void {
    if (this.buffer.length === 0) return;
    if (this.busy) {
      // Drop this period if still decoding — prefer staying responsive.
      this.buffer = new Float32Array(0);
      return;
    }

    const resampled = resampleLinear(
      this.buffer,
      this.sampleRate,
      FT8_DECODE_RATE,
    );
    this.buffer = new Float32Array(0);

    const worker = this.ensureWorker();
    if (!worker) {
      this.decodeOnMain(resampled, periodAt);
      return;
    }

    const id = this.nextJobId++;
    this.pendingJobId = id;
    this.busy = true;
    this.patch({ status: "decoding" });

    const request: Ft8WorkerRequest = {
      type: "decode",
      id,
      samples: resampled,
      sampleRate: FT8_DECODE_RATE,
      depth: this.state.config.depth,
      freqLow: this.state.config.freqLow,
      freqHigh: this.state.config.freqHigh,
      periodAt,
    };
    worker.postMessage(request, [resampled.buffer]);
  }

  private decodeOnMain(samples: Float32Array, periodAt: number): void {
    this.busy = true;
    this.patch({ status: "decoding" });
    void import("@e04/ft8ts")
      .then(({ decodeFT8, HashCallBook }) => {
        if (this.disposed || !this.state.enabled) return;
        const messages = decodeFT8(samples, {
          sampleRate: FT8_DECODE_RATE,
          depth: this.state.config.depth,
          freqLow: this.state.config.freqLow,
          freqHigh: this.state.config.freqHigh,
          hashCallBook: new HashCallBook(),
        });
        this.applyDecoded(periodAt, messages);
      })
      .catch((err) => {
        this.patch({
          status: this.state.enabled ? "listening" : "idle",
          error: err instanceof Error ? err.message : String(err),
        });
      })
      .finally(() => {
        this.busy = false;
      });
  }

  private applyDecoded(
    periodAt: number,
    decoded: Array<{
      msg: string;
      snr: number;
      dt: number;
      freq: number;
      sync: number;
    }>,
  ): void {
    if (!this.state.enabled || this.disposed) return;

    const sorted = [...decoded].sort(
      (a, b) => b.snr - a.snr || a.freq - b.freq,
    );
    const additions: Ft8Message[] = sorted.map((d, i) => ({
      id: `${periodAt}-${i}-${d.freq.toFixed(1)}-${d.msg}`,
      at: periodAt,
      msg: d.msg,
      snr: d.snr,
      dt: d.dt,
      freq: d.freq,
      sync: d.sync,
    }));

    const messages =
      additions.length === 0
        ? this.state.messages
        : [...this.state.messages, ...additions].slice(-MAX_MESSAGES);

    this.patch({
      messages,
      status: "listening",
      error: null,
    });
  }

  private ensureWorker(): Worker | null {
    if (this.worker) return this.worker;
    if (typeof Worker === "undefined") return null;

    try {
      const worker = new Worker(
        new URL("./decode-worker.ts", import.meta.url),
      );
      worker.onmessage = (event: MessageEvent<Ft8WorkerResponse>) => {
        const data = event.data;
        if (!data) return;
        if (data.type === "error") {
          if (data.id !== this.pendingJobId) return;
          this.busy = false;
          this.pendingJobId = null;
          this.patch({
            status: this.state.enabled ? "listening" : "idle",
            error: data.error,
          });
          return;
        }
        if (data.type !== "decoded") return;
        if (data.id !== this.pendingJobId) return;
        this.busy = false;
        this.pendingJobId = null;
        this.applyDecoded(data.periodAt, data.messages);
      };
      worker.onerror = (event) => {
        this.busy = false;
        this.pendingJobId = null;
        this.patch({
          status: this.state.enabled ? "listening" : "idle",
          error: event.message || "FT8 worker failed",
        });
      };
      this.worker = worker;
      return worker;
    } catch {
      return null;
    }
  }

  private startCycleClock(): void {
    this.stopCycleClock();
    this.cycleTimer = setInterval(() => {
      if (!this.state.enabled) return;
      const now = Date.now();
      const start = periodStartMs(now);
      if (start > this.periodAt && this.buffer.length > 0) {
        this.flushPeriod(this.periodAt);
        this.periodAt = start;
      }
      const cycleSec = (now - start) / 1000;
      if (Math.abs(cycleSec - this.state.cycleSec) >= 0.2) {
        this.patch({ cycleSec });
      }
    }, 200);
  }

  private stopCycleClock(): void {
    if (this.cycleTimer !== null) {
      clearInterval(this.cycleTimer);
      this.cycleTimer = null;
    }
  }

  private patch(partial: Partial<Ft8State>): void {
    this.state = { ...this.state, ...partial };
    for (const listener of this.listeners) {
      listener(this.state);
    }
  }
}
