import { wsUrl } from "@/lib/api";
import type { RadioController } from "../radio-controller";
import { KiwiClient } from "./client";
import { kiwiBandpass, kiwiModulation } from "./modes";
import { kiwiZoomForSpanHz } from "./protocol";

export type DiversityStatus = {
  state: "off" | "connecting" | "local-only" | "matched" | "error";
  message?: string;
  matchConfidence?: number;
  alignmentMs?: number | null;
  clockDriftPpm?: number;
  attenuationDb?: number;
  receiverName?: string;
};
export type SpectrumOverlay = { local: (number | null)[]; kiwi: (number | null)[]; spanHz: number };
export type DiversityMonitor = "off" | "clean" | "raw";

/** Resample in FREQUENCY, not FFT index: receivers have different spans/clocks.
 * Levels are relative to each receiver's own median, not calibrated RF power. */
export function projectSpectrum(bins: Float32Array, center: number, span: number,
  listen: number, viewSpan: number, shifted: boolean, count = 192): (number | null)[] {
  const result: (number | null)[] = [];
  for (let i = 0; i < count; i++) {
    const hz = listen + ((i + .5) / count - .5) * viewSpan;
    const first = Math.floor(((hz - viewSpan / count / 2 - center) / span + .5) * bins.length);
    const last = Math.floor(((hz + viewSpan / count / 2 - center) / span + .5) * bins.length);
    if (last < 0 || first >= bins.length) { result.push(null); continue; }
    let value = -Infinity;
    for (let index = Math.max(0, first); index <= Math.min(last, bins.length - 1); index++) {
      const sample = bins[shifted ? index : (index + Math.floor(bins.length / 2)) % bins.length];
      if (Number.isFinite(sample)) value = Math.max(value, sample);
    }
    result.push(Number.isFinite(value) ? value : null);
  }
  const values = result.filter((v): v is number => v !== null).sort((a, b) => a - b);
  const floor = values[Math.floor(values.length / 2)] ?? 0;
  return result.map((value) => value === null ? null : Math.max(-10, Math.min(50, value - floor)));
}

/** Owns a SECOND Kiwi session; never calls RadioController.connectKiwi(). */
export class KiwiDiversity {
  private kiwi: KiwiClient | null = null;
  private socket: WebSocket | null = null;
  private untap: (() => void)[] = [];
  private localRate = 0;
  private referenceRate = 0;
  private ready = false;
  private tuning = "";
  private settlingUntil = 0;
  private lastSpectrum = 0;
  private overlay: SpectrumOverlay = { local: [], kiwi: [], spanHz: 16000 };
  private monitor: DiversityMonitor = "off";
  private context: AudioContext | null = null;
  private gain: GainNode | null = null;
  private nodes = new Set<AudioBufferSourceNode>();
  private nextTime = 0;
  private unmuteTimer: ReturnType<typeof setTimeout> | undefined;
  private watchdog: ReturnType<typeof setInterval> | undefined;
  private lastLocal = 0;
  private lastReference = 0;
  private muted = false;
  private status: DiversityStatus = { state: "off" };

  constructor(private controller: RadioController,
    private onStatus: (status: DiversityStatus) => void,
    private onOverlay: (overlay: SpectrumOverlay) => void) {}

  private patch(status: Partial<DiversityStatus>) {
    this.status = { ...this.status, ...status };
    this.onStatus(this.status);
  }

  async connect(address: string, password = "") {
    this.disconnect();
    const state = this.controller.getState();
    const hz = state.frequencyHz + state.offsetHz;
    if (state.status !== "playing" || hz < 0 || hz > 30000000 || state.connection === "demo") {
      throw new Error("Start a real shortwave receiver (below 30 MHz) first.");
    }
    this.patch({ state: "connecting", message: undefined, matchConfidence: 0 });
    const kiwi = new KiwiClient({
      onReady: () => { if (this.kiwi === kiwi) { this.ready = true; this.syncTuning(); } },
      onConfig: (config) => { if (this.kiwi === kiwi) this.patch({ receiverName: config.receiverName ?? undefined }); },
      onAudio: (pcm, rate) => {
        if (this.kiwi !== kiwi || !this.ready || performance.now() < this.settlingUntil) return;
        this.lastReference = performance.now();
        if (rate !== this.referenceRate) { this.referenceRate = rate; this.resetBackend(); }
        this.send(1, pcm);
      },
      onSpectrum: (bins, center, span) => {
        if (this.kiwi !== kiwi || performance.now() < this.settlingUntil) return;
        const radio = this.controller.getState();
        this.overlay.kiwi = projectSpectrum(bins, center, span, radio.frequencyHz + radio.offsetHz, this.overlay.spanHz, true);
        this.emitOverlay();
      },
      onServerError: (message) => { if (this.kiwi === kiwi) this.fail(message); },
      onClose: () => { if (this.kiwi === kiwi) this.fail("KiwiSDR disconnected; local reception restored."); },
    });
    this.kiwi = kiwi;
    this.untap.push(this.controller.onRawAudio((samples, rate) => {
      if (!this.ready || performance.now() < this.settlingUntil) return;
      this.lastLocal = performance.now();
      if (this.localRate !== rate) { this.localRate = rate; this.resetBackend(); }
      const pcm = new Int16Array(samples.length);
      for (let i = 0; i < pcm.length; i++) pcm[i] = Math.round(Math.max(-1, Math.min(1, samples[i])) * 32767);
      this.send(0, pcm);
    }));
    this.untap.push(this.controller.subscribe(() => this.syncTuning()));
    this.untap.push(this.controller.onSpectrum((bins, center, info) => {
      if (performance.now() < this.settlingUntil) return;
      const radio = this.controller.getState();
      this.overlay.local = projectSpectrum(bins, (center ?? radio.frequencyHz) + (info?.centerOffsetHz ?? 0),
        info?.sampleRate ?? radio.sampleRate, radio.frequencyHz + radio.offsetHz, this.overlay.spanHz, false);
      this.emitOverlay();
    }));
    this.watchdog = setInterval(() => {
      if (!this.ready || performance.now() < this.settlingUntil) return;
      if (performance.now() - Math.min(this.lastLocal, this.lastReference) > 4000) {
        this.resetBackend();
        this.overlay = { ...this.overlay, local: [], kiwi: [] };
        this.onOverlay(this.overlay);
        this.patch({ state: "local-only", message: "Waiting for live audio from both receivers. Local reception is unchanged." });
      }
    }, 1000);
    try { await kiwi.connect(address, password); }
    catch (error) {
      if (this.kiwi !== kiwi) return; // intentional disconnect cancelled the handshake
      this.fail(error instanceof Error ? error.message : String(error));
      throw error;
    }
  }

  private syncTuning() {
    const state = this.controller.getState();
    const hz = state.frequencyHz + state.offsetHz;
    if (state.status !== "playing" || hz > 30000000 || hz < 0 || state.connection === "demo") {
      this.fail("Local receiver stopped or left shortwave; Kiwi connection released.");
      return;
    }
    this.gain?.gain.setValueAtTime(state.volume, this.context!.currentTime);
    const tuning = `${hz}:${state.scheme}:${state.bandwidthHz}:${state.connection}`;
    if (!this.ready || tuning === this.tuning) return;
    this.tuning = tuning;
    this.settlingUntil = performance.now() + 700;
    this.lastLocal = this.lastReference = performance.now();
    this.overlay = { local: [], kiwi: [], spanHz: Math.max(16000, Math.min(48000, state.bandwidthHz * 3)) };
    this.onOverlay(this.overlay);
    this.resetBackend();
    const maxKhz = this.kiwi!.getConfig().maxFreqKhz;
    this.kiwi!.applyTuning({ frequencyHz: hz, mod: kiwiModulation(state.scheme), ...kiwiBandpass(state.scheme, state.bandwidthHz) });
    this.kiwi!.setWaterfallCenter(hz);
    this.kiwi!.setZoom(kiwiZoomForSpanHz(this.overlay.spanHz, maxKhz));
    this.patch({ state: "local-only", message: "Aligning both receivers; local-only until a stable match is verified.", matchConfidence: 0 });
  }

  private emitOverlay() {
    if (performance.now() - this.lastSpectrum < 150) return;
    this.lastSpectrum = performance.now();
    this.onOverlay({ ...this.overlay });
  }

  private send(channel: number, pcm: Int16Array) {
    if (!this.socket && this.localRate && this.referenceRate) this.openBackend();
    const socket = this.socket;
    if (socket?.readyState !== WebSocket.OPEN) return;
    if (socket.bufferedAmount > 192000) {
      this.fail("Processing connection fell behind. Local reception restored; reconnect to try again.");
      return;
    }
    // Bounded packets; explicit little-endian wire format on all platforms.
    const limit = Math.floor((channel === 0 ? this.localRate : this.referenceRate) / 4);
    for (let offset = 0; offset < pcm.length; offset += limit) {
      const count = Math.min(limit, pcm.length - offset);
      const packet = new ArrayBuffer(1 + count * 2), view = new DataView(packet);
      view.setUint8(0, channel);
      for (let i = 0; i < count; i++) view.setInt16(1 + i * 2, pcm[offset + i], true);
      socket.send(packet);
    }
  }

  private openBackend() {
    const socket = new WebSocket(wsUrl("/kiwi-diversity"));
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    socket.onopen = () => {
      if (this.socket === socket && socket.readyState === WebSocket.OPEN)
        socket.send(JSON.stringify({ type: "configure", localRate: this.localRate, referenceRate: this.referenceRate }));
    };
    socket.onmessage = (event) => {
      if (this.socket !== socket) return;
      if (typeof event.data === "string") {
        try {
          const status = JSON.parse(event.data);
          if (status.type === "error") { this.fail(status.message ?? "Diversity processing failed"); return; }
          if (status.type === "status") this.patch({ ...status, message: status.state === "matched"
            ? "Same transmission verified. Suppressing local-only interference."
            : "No stable match: passing through your local signal without reference suppression." });
        } catch { this.fail("Invalid diversity processing response"); }
      } else this.play(event.data as ArrayBuffer);
    };
    socket.onerror = () => { if (this.socket === socket) this.fail("Cannot reach the Kiwi diversity backend."); };
    socket.onclose = () => { if (this.socket === socket) this.fail("Diversity processing disconnected; local reception restored."); };
  }

  setMonitor(mode: DiversityMonitor) {
    this.flushPlayback();
    this.monitor = mode;
    if (mode === "off") return;
    try {
      this.context ??= new AudioContext();
      this.gain ??= this.context.createGain();
      this.gain.disconnect();
      this.gain.connect(this.context.destination);
      this.gain.gain.value = this.controller.getState().volume;
      void this.context.resume().catch(() => this.fail("Browser blocked comparison playback."));
    } catch { this.fail("Your browser cannot play comparison audio."); }
  }

  private play(buffer: ArrayBuffer) {
    const ctx = this.context;
    if (this.monitor === "off" || !ctx || ctx.state !== "running" || !this.gain) return;
    const view = new DataView(buffer);
    if (view.byteLength % 4 !== 0) { this.fail("Invalid diversity audio packet"); return; }
    const samples = new Float32Array(view.byteLength / 4);
    const channelOffset = this.monitor === "raw" ? 2 : 0;
    for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 4 + channelOffset, true) / 32768;
    if (!samples.length) return;
    if (this.nextTime < ctx.currentTime || this.nextTime - ctx.currentTime > .5) {
      this.flushPlayback();
      this.nextTime = ctx.currentTime + .06;
    }
    const audio = ctx.createBuffer(1, samples.length, 16000);
    audio.copyToChannel(samples, 0);
    const node = ctx.createBufferSource();
    node.buffer = audio; node.connect(this.gain);
    this.nodes.add(node);
    node.onended = () => { this.nodes.delete(node); node.disconnect(); };
    node.start(this.nextTime);
    this.nextTime += audio.duration;
    this.controller.setBackendMonitorMuted(true, this);
    this.muted = true;
    clearTimeout(this.unmuteTimer);
    this.unmuteTimer = setTimeout(() => this.flushPlayback(), 800);
  }

  private flushPlayback() {
    clearTimeout(this.unmuteTimer);
    for (const node of this.nodes) node.stop();
    this.nodes.clear();
    this.nextTime = 0;
    if (this.muted) this.controller.setBackendMonitorMuted(false, this);
    this.muted = false;
  }

  private resetBackend() {
    const socket = this.socket;
    this.socket = null;
    socket?.close();
    this.flushPlayback();
  }

  private fail(message: string) {
    this.disconnect();
    this.patch({ state: "error", message });
  }

  disconnect() {
    this.ready = false;
    const kiwi = this.kiwi;
    this.kiwi = null;
    kiwi?.close();
    this.resetBackend();
    for (const untap of this.untap) untap();
    this.untap = [];
    clearInterval(this.watchdog);
    this.watchdog = undefined;
    this.localRate = this.referenceRate = 0;
    this.tuning = "";
    this.monitor = "off";
    void this.context?.close();
    this.context = null; this.gain = null;
    this.overlay = { local: [], kiwi: [], spanHz: 16000 };
    this.onOverlay(this.overlay);
    this.status = { state: "off" };
    this.onStatus(this.status);
  }
}
