import { wsUrl } from "@/lib/api";

export type CleanerState = {
  status: "idle" | "starting" | "optimizing" | "error" | "stopped";
  streamId: string;
  frequencyHz: number;
  profile?: string;
  iterations?: number;
  neuralAvailable?: boolean;
  warning?: string | null;
  message?: string;
  updatedAt?: number;
  notchesHz?: number[];
  metrics?: { score: number; speechRetention: number; noiseReductionDb: number; eligible: boolean; quietMeasured?: boolean } | null;
  trials?: Array<{ profile: string; score: number; speechRetention: number; noiseReductionDb: number; eligible: boolean }>;
  processing?: {
    location: "lan" | "local" | "spectral";
    engine?: string;
    device?: string;
    accelerated?: boolean;
    gpuName?: string;
    configured?: boolean;
    fallback?: boolean;
    roundTripMs?: number | null;
    delayMs?: number;
    warning?: string | null;
  };
};
type Meta = { frequencyHz: number; source: string; sampleRate?: number; [key: string]: unknown };

/** Raw demodulation is uploaded independently of playback volume, NR, or neural enhancement. */
export class FinderCleaner {
  private socket: WebSocket | null = null;
  private pending = new Float32Array(0);
  private retryAt = 0;
  private lastMeta = 0;
  private epoch = 0;
  private target: number | null = null;
  private streamId = "";
  private rate = 0;
  private lastAudio = 0;
  private receivedAudio = false;

  constructor(private meta: () => Meta, private update: (state: CleanerState) => void,
    private adjustRf: (advice: Record<string, unknown>) => void,
    private audio: (pcm: Int16Array, rate: number) => void = () => {}) {}

  setTarget(hz: number | null) {
    if (this.target === hz) return;
    this.stop(); this.target = hz;
    if (hz !== null) this.retryAt = 0;
  }

  push(mono: Float32Array, rate: number) {
    if (this.target === null) return;
    if (!this.socket) {
      if (Date.now() < this.retryAt) return;
      this.connect(rate);
    }
    const socket = this.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    if (Date.now() - this.lastAudio > (this.receivedAudio ? 3000 : 20000)) {
      this.fail("Backend cleanup stopped returning audio; using receiver playback while reconnecting."); return;
    }
    if (rate !== this.rate || socket.bufferedAmount > rate * 2) {
      this.fail("Cleaner upload fell behind or its sample rate changed; reconnecting."); return;
    }
    const data = new Float32Array(this.pending.length + mono.length);
    data.set(this.pending); data.set(mono, this.pending.length);
    const count = Math.round(rate / 10);
    let offset = 0;
    while (data.length - offset >= count) {
      const bytes = new ArrayBuffer(count * 2), view = new DataView(bytes);
      for (let i = 0; i < count; i++) view.setInt16(i * 2, Math.round(Math.max(-1, Math.min(1, data[offset + i] || 0)) * 32767), true);
      socket.send(bytes); offset += count;
    }
    this.pending = data.slice(offset);
    if (Date.now() - this.lastMeta > 2000) {
      socket.send(JSON.stringify({ ...this.meta(), frequencyHz: this.target, sampleRate: rate }));
      this.lastMeta = Date.now();
    }
  }

  private connect(rate: number) {
    this.rate = rate;
    this.lastAudio = Date.now(); this.receivedAudio = false;
    const epoch = ++this.epoch;
    this.streamId = `clean-${Date.now().toString(36)}-${Array.from(crypto.getRandomValues(new Uint8Array(8)), (v) => v.toString(16).padStart(2, "0")).join("")}`;
    const socket = new WebSocket(wsUrl(`/autofinder/cleaner/source/${this.streamId}`));
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    this.update({ status: "starting", streamId: this.streamId, frequencyHz: this.target! });
    socket.onopen = () => {
      if (epoch !== this.epoch) return;
      socket.send(JSON.stringify({ ...this.meta(), frequencyHz: this.target, sampleRate: rate }));
      this.lastMeta = Date.now();
    };
    socket.onmessage = (event) => {
      if (epoch !== this.epoch) return;
      if (typeof event.data !== "string") {
        this.lastAudio = Date.now(); this.receivedAudio = true;
        this.audio(new Int16Array(event.data as ArrayBuffer), 16000); return;
      }
      try {
        const state = JSON.parse(event.data);
        if (state.status === "error") this.fail(state.message ?? "Backend cleanup failed.");
        else if (state.type === "rf") this.adjustRf(state);
        else this.update({ ...state, streamId: this.streamId, frequencyHz: this.target! });
      } catch { this.fail("Invalid backend cleaner response."); }
    };
    socket.onerror = () => { if (epoch === this.epoch) this.fail("Backend audio cleaner is unavailable; retrying."); };
    socket.onclose = () => { if (epoch === this.epoch) this.fail("Backend audio cleaner disconnected; retrying."); };
  }

  private fail(message: string) {
    const hz = this.target, streamId = this.streamId;
    this.disconnect(); this.retryAt = Date.now() + 5000;
    if (hz !== null) this.update({ status: "error", frequencyHz: hz, streamId, message });
  }

  private disconnect() {
    this.epoch++;
    const socket = this.socket; this.socket = null;
    this.pending = new Float32Array(0);
    socket?.close();
  }

  stop() {
    const hz = this.target;
    this.target = null;
    this.disconnect();
    if (hz !== null) this.update({ status: "stopped", frequencyHz: hz, streamId: this.streamId });
  }
}
