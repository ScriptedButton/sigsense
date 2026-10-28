import { ImaAdpcmDecoder } from "@/lib/sdr/openwebrx/adpcm";
import {
  EMPTY_KIWI_CONFIG,
  KIWI_CONNECT_TIMEOUT_MS,
  KIWI_DEFAULT_ZOOM,
  KIWI_KEEPALIVE_MS,
  kiwiZoomSpanHz,
  type KiwiConfig,
} from "./protocol";
import { decodeKiwiWaterfall } from "./spectrum";
import { kiwiMixedContentMessage, kiwiWebSocketUrls } from "./url";

const SND_FLAG_COMPRESSED = 0x10;
/** Flush coalesced PCM about this often to cut AudioBufferSourceNode churn. */
const AUDIO_FLUSH_MS = 80;
/** Cap waterfall callbacks so the UI thread can keep keepalive timers alive. */
const SPECTRUM_MIN_MS = 50;

export type KiwiTuneParams = {
  mod: string;
  low_cut: number;
  high_cut: number;
  /** Absolute listen frequency in Hz. */
  frequencyHz: number;
};

export type KiwiClientHandlers = {
  onSpectrum: (shiftedBins: Float32Array, centerHz: number, sampleRate: number) => void;
  onAudio: (pcm: Int16Array, sampleRate: number) => void;
  onConfig: (config: KiwiConfig) => void;
  onReady: () => void;
  onServerError: (message: string) => void;
  onClose: () => void;
};

/**
 * Browser client for a KiwiSDR receiver (dual SND + W/F websockets).
 * The server demodulates; this socket pair carries control, waterfall, and PCM.
 */
export class KiwiClient {
  private snd: WebSocket | null = null;
  private wf: WebSocket | null = null;
  private keepaliveTimer: ReturnType<typeof setInterval> | null = null;
  private connectTimer: ReturnType<typeof setTimeout> | null = null;
  private pendingReject: ((err: Error) => void) | null = null;
  private config: KiwiConfig = { ...EMPTY_KIWI_CONFIG };
  private readonly audioDecoder = new ImaAdpcmDecoder();
  private readonly wfDecoder = new ImaAdpcmDecoder();
  private audioCompression = true;
  private wfCompression = true;
  private sndReady = false;
  private wfReady = false;
  private sndSetupDone = false;
  private wfSetupDone = false;
  private readyEmitted = false;
  private closeNotified = false;
  private zoom = KIWI_DEFAULT_ZOOM;
  private wfCenterHz = 7_100_000;
  private closed = false;
  private password = "";
  private address = "";
  private lastSndKeepaliveMs = 0;
  private lastWfKeepaliveMs = 0;
  private lastSpectrumMs = 0;
  private audioChunks: Int16Array[] = [];
  private audioChunkSamples = 0;
  private audioFlushTimer: ReturnType<typeof setTimeout> | null = null;
  private audioOutRate = 12_000;

  constructor(private readonly handlers: KiwiClientHandlers) {}

  getConfig(): KiwiConfig {
    return this.config;
  }

  getZoom(): number {
    return this.zoom;
  }

  getWfCenterHz(): number {
    return this.wfCenterHz;
  }

  getSpectrumSpanHz(): number {
    return kiwiZoomSpanHz(this.zoom, this.config.maxFreqKhz);
  }

  connect(address: string, password = ""): Promise<void> {
    this.closeSockets();
    this.closed = false;
    this.closeNotified = false;
    this.password = password;
    this.address = address.trim();
    this.config = { ...EMPTY_KIWI_CONFIG };
    this.sndReady = false;
    this.wfReady = false;
    this.sndSetupDone = false;
    this.wfSetupDone = false;
    this.readyEmitted = false;
    this.audioDecoder.reset();
    this.wfDecoder.reset();
    this.audioChunks = [];
    this.audioChunkSamples = 0;

    // Prefer modern `/ws/kiwi/…` (v1.550+). Only fall back if the socket never opens.
    return this.connectOnce(false).catch((err) => {
      if (this.closed) throw err;
      if (!isLikelyPathError(err)) throw err;
      this.closeSockets();
      this.sndReady = false;
      this.wfReady = false;
      this.sndSetupDone = false;
      this.wfSetupDone = false;
      this.readyEmitted = false;
      return this.connectOnce(true);
    });
  }

  private connectOnce(legacy: boolean): Promise<void> {
    const ts = Date.now();
    let urls: { snd: string; wf: string };
    try {
      urls = kiwiWebSocketUrls(this.address, ts, legacy);
    } catch (err) {
      return Promise.reject(err instanceof Error ? err : new Error(String(err)));
    }
    const mixed = kiwiMixedContentMessage(urls.snd);
    if (mixed) return Promise.reject(new Error(mixed));

    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = (err?: Error) => {
        if (settled) return;
        settled = true;
        this.pendingReject = null;
        this.clearConnectTimer();
        if (err) reject(err);
        else resolve();
      };
      this.pendingReject = (err) => finish(err);

      this.connectTimer = setTimeout(() => {
        finish(
          new Error(
            "Timed out waiting for KiwiSDR. Check the address and that a channel is free.",
          ),
        );
        this.closeSockets();
      }, KIWI_CONNECT_TIMEOUT_MS);

      try {
        this.snd = this.openSocket(urls.snd, "SND", finish);
        this.wf = this.openSocket(urls.wf, "W/F", finish);
      } catch (err) {
        finish(err instanceof Error ? err : new Error(String(err)));
      }
    });
  }

  private openSocket(
    url: string,
    kind: "SND" | "W/F",
    finish: (err?: Error) => void,
  ): WebSocket {
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    let didOpen = false;

    ws.onopen = () => {
      didOpen = true;
      this.send(ws, `SET auth t=kiwi p=${this.password}`);
      // Keepalive immediately so a slow handshake cannot starve the server.
      this.send(ws, "SET keepalive");
    };

    ws.onmessage = (event) => {
      const bytes = syncBytes(event.data);
      if (bytes) this.handleTagged(kind, bytes, finish);
    };

    ws.onerror = () => {
      if (!this.readyEmitted && !didOpen) {
        finish(new Error("kiwi_path_error: Could not open KiwiSDR websocket."));
      } else if (!this.readyEmitted) {
        finish(
          new Error(
            "Could not connect to KiwiSDR. Check the address and that the server is running.",
          ),
        );
      }
    };

    ws.onclose = () => {
      if (this.closed) return;
      if (!this.readyEmitted) {
        finish(
          new Error(
            didOpen
              ? "KiwiSDR closed the connection before the session was ready."
              : "kiwi_path_error: KiwiSDR websocket closed before open.",
          ),
        );
        return;
      }
      this.notifyClosed();
    };

    return ws;
  }

  close(): void {
    this.closed = true;
    this.clearConnectTimer();
    this.clearKeepalive();
    this.clearAudioFlush();
    this.flushAudio(true);
    const rejectPending = this.pendingReject;
    this.pendingReject = null;
    this.closeSockets();
    rejectPending?.(new Error("KiwiSDR connection closed."));
  }

  applyTuning(params: KiwiTuneParams): void {
    const freqKhz = params.frequencyHz / 1_000;
    this.send(
      this.snd,
      `SET mod=${params.mod} low_cut=${params.low_cut} high_cut=${params.high_cut} freq=${freqKhz.toFixed(3)}`,
    );
  }

  setWaterfallCenter(centerHz: number): void {
    this.wfCenterHz = Math.max(0, Math.round(centerHz));
    const cfKhz = this.wfCenterHz / 1_000;
    this.send(this.wf, `SET zoom=${this.zoom} cf=${cfKhz}`);
  }

  setZoom(zoom: number): void {
    this.zoom = Math.max(0, Math.min(14, Math.round(zoom)));
    const cfKhz = this.wfCenterHz / 1_000;
    this.send(this.wf, `SET zoom=${this.zoom} cf=${cfKhz}`);
  }

  private notifyClosed(): void {
    if (this.closed || this.closeNotified) return;
    this.closeNotified = true;
    this.closed = true;
    this.clearKeepalive();
    this.clearAudioFlush();
    this.handlers.onClose();
  }

  private handleTagged(
    kind: "SND" | "W/F",
    message: Uint8Array,
    finish: (err?: Error) => void,
  ): void {
    if (message.length < 3) return;
    const tag = String.fromCharCode(message[0]!, message[1]!, message[2]!);
    const body = message.subarray(3);

    if (tag === "MSG") {
      const text = utf8Decode(body.length > 0 ? body.subarray(1) : body);
      this.handleMsg(kind, text, finish);
      return;
    }
    if (tag === "SND" && kind === "SND") {
      this.pingKeepalive("SND");
      this.handleSnd(body);
      return;
    }
    if (tag === "W/F" && kind === "W/F") {
      this.pingKeepalive("W/F");
      this.handleWf(body.length > 0 ? body.subarray(1) : body);
    }
  }

  private handleMsg(
    kind: "SND" | "W/F",
    body: string,
    finish: (err?: Error) => void,
  ): void {
    for (const pair of body.split(" ")) {
      if (!pair) continue;
      const eq = pair.indexOf("=");
      const name = eq >= 0 ? pair.slice(0, eq) : pair;
      const value = eq >= 0 ? pair.slice(eq + 1) : null;
      this.handleMsgParam(kind, name, value, finish);
    }
  }

  private handleMsgParam(
    kind: "SND" | "W/F",
    name: string,
    value: string | null,
    finish: (err?: Error) => void,
  ): void {
    if (name === "too_busy") {
      finish(
        new Error(
          value
            ? `KiwiSDR is busy (all ${value} channels taken).`
            : "KiwiSDR is busy.",
        ),
      );
      return;
    }
    if (name === "down") {
      finish(new Error("KiwiSDR reports the server is down."));
      return;
    }
    if (name === "badp") {
      if (value && value !== "0") {
        finish(new Error(badPasswordMessage(value)));
      }
      return;
    }
    if (name === "redirect" && value) {
      finish(new Error(`KiwiSDR redirected to ${decodeUri(value)}.`));
      return;
    }

    if (name === "audio_rate" && value && kind === "SND") {
      const rate = Number.parseInt(value, 10);
      if (Number.isFinite(rate) && rate > 0) {
        this.config = { ...this.config, audioRate: rate };
        this.send(this.snd, `SET AR OK in=${rate} out=44100`);
      }
      return;
    }

    if (name === "sample_rate" && value && kind === "SND") {
      const rate = Number.parseFloat(value);
      if (Number.isFinite(rate) && rate > 0) {
        if (this.config.audioRate == null) {
          this.config = { ...this.config, audioRate: 12_000 };
          this.send(this.snd, "SET AR OK in=12000 out=44100");
        }
        this.config = { ...this.config, sampleRate: rate };
        this.audioOutRate = Math.round(rate);
        this.handlers.onConfig(this.config);
        // Prefer waiting for badp=0, but some nodes stream sample_rate first.
        this.setupSnd();
        this.sndReady = true;
        this.maybeReady(finish);
      }
      return;
    }

    if (name === "bandwidth" && value) {
      const hz = Number.parseFloat(value);
      if (Number.isFinite(hz) && hz > 0) {
        this.config = { ...this.config, maxFreqKhz: hz / 1_000 };
        this.handlers.onConfig(this.config);
      }
      return;
    }

    if (name === "version_maj" && value) {
      this.config = {
        ...this.config,
        versionMajor: Number.parseInt(value, 10),
      };
      return;
    }
    if (name === "version_min" && value) {
      this.config = {
        ...this.config,
        versionMinor: Number.parseInt(value, 10),
      };
      return;
    }

    if (name === "load_cfg" && value) {
      // Full JSON.parse of load_cfg freezes the UI (hundreds of KB). Pull the name lightly.
      const nameFromCfg = receiverNameFromCfgLight(value);
      if (nameFromCfg) {
        this.config = { ...this.config, receiverName: nameFromCfg };
        this.handlers.onConfig(this.config);
      }
      return;
    }

    if (name === "wf_setup" && kind === "W/F") {
      this.setupWf();
      this.wfReady = true;
      this.maybeReady(finish);
    }
  }

  private setupSnd(): void {
    if (this.sndSetupDone) return;
    this.sndSetupDone = true;
    this.send(this.snd, "SET squelch=0 max=0");
    this.send(this.snd, "SET genattn=0");
    this.send(this.snd, "SET gen=0 mix=-1");
    this.send(this.snd, "SET ident_user=SigSense");
    this.send(this.snd, `SET compression=${this.audioCompression ? 1 : 0}`);
    this.send(
      this.snd,
      "SET agc=1 hang=0 thresh=-100 slope=6 decay=1000 manGain=50",
    );
    this.send(
      this.snd,
      `SET mod=am low_cut=-4900 high_cut=4900 freq=${(this.wfCenterHz / 1000).toFixed(3)}`,
    );
    this.send(this.snd, "SET keepalive");
  }

  private setupWf(): void {
    if (this.wfSetupDone) return;
    this.wfSetupDone = true;
    this.send(this.wf, `SET wf_comp=${this.wfCompression ? 1 : 0}`);
    // Speed 1 = slowest waterfall; keeps the UI responsive on remote links.
    this.send(this.wf, "SET wf_speed=1");
    this.send(this.wf, "SET interp=13");
    this.send(this.wf, "SET maxdb=-10 mindb=-110");
    this.send(
      this.wf,
      `SET zoom=${this.zoom} cf=${(this.wfCenterHz / 1000).toFixed(3)}`,
    );
    this.send(this.wf, "SET keepalive");
  }

  private maybeReady(finish: (err?: Error) => void): void {
    if (this.readyEmitted || !this.sndReady || !this.wfReady) return;
    this.readyEmitted = true;
    this.clearConnectTimer();
    this.startKeepalive();
    this.handlers.onConfig(this.config);
    this.handlers.onReady();
    finish();
  }

  private handleSnd(body: Uint8Array): void {
    if (body.length < 7) return;
    const flags = body[0] ?? 0;
    const data = body.subarray(7);
    const compressed =
      (flags & SND_FLAG_COMPRESSED) !== 0 || this.audioCompression;

    let pcm: Int16Array;
    if (compressed) {
      pcm = this.audioDecoder.decode(data);
    } else {
      pcm = pcmFromBe(data);
    }
    if (pcm.length === 0) return;
    this.queueAudio(pcm);
  }

  private handleWf(body: Uint8Array): void {
    if (body.length < 12) return;
    const data = body.subarray(12);
    const now = performance.now();
    if (now - this.lastSpectrumMs < SPECTRUM_MIN_MS) return;
    this.lastSpectrumMs = now;

    const bins = decodeKiwiWaterfall(data, this.wfCompression, this.wfDecoder);
    if (!bins || bins.length === 0) return;
    const span = this.getSpectrumSpanHz();
    this.handlers.onSpectrum(bins, this.wfCenterHz, span);
  }

  private queueAudio(pcm: Int16Array): void {
    this.audioChunks.push(pcm);
    this.audioChunkSamples += pcm.length;
    const target = Math.max(1, Math.round((this.audioOutRate * AUDIO_FLUSH_MS) / 1000));
    if (this.audioChunkSamples >= target) {
      this.flushAudio(false);
      return;
    }
    if (this.audioFlushTimer == null) {
      this.audioFlushTimer = setTimeout(() => {
        this.audioFlushTimer = null;
        this.flushAudio(false);
      }, AUDIO_FLUSH_MS);
    }
  }

  private flushAudio(force: boolean): void {
    if (this.audioChunks.length === 0) return;
    if (!force && this.audioChunkSamples === 0) return;
    const total = this.audioChunkSamples;
    const merged = new Int16Array(total);
    let offset = 0;
    for (const chunk of this.audioChunks) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    this.audioChunks = [];
    this.audioChunkSamples = 0;
    this.clearAudioFlush();
    this.handlers.onAudio(merged, this.audioOutRate);
  }

  private pingKeepalive(kind: "SND" | "W/F"): void {
    const now = performance.now();
    if (kind === "SND") {
      if (now - this.lastSndKeepaliveMs < 1000) return;
      this.lastSndKeepaliveMs = now;
      this.send(this.snd, "SET keepalive");
      return;
    }
    if (now - this.lastWfKeepaliveMs < 1000) return;
    this.lastWfKeepaliveMs = now;
    this.send(this.wf, "SET keepalive");
  }

  private startKeepalive(): void {
    this.clearKeepalive();
    this.keepaliveTimer = setInterval(() => {
      this.send(this.snd, "SET keepalive");
      this.send(this.wf, "SET keepalive");
    }, KIWI_KEEPALIVE_MS);
  }

  private clearKeepalive(): void {
    if (this.keepaliveTimer != null) {
      clearInterval(this.keepaliveTimer);
      this.keepaliveTimer = null;
    }
  }

  private clearAudioFlush(): void {
    if (this.audioFlushTimer != null) {
      clearTimeout(this.audioFlushTimer);
      this.audioFlushTimer = null;
    }
  }

  private clearConnectTimer(): void {
    if (this.connectTimer != null) {
      clearTimeout(this.connectTimer);
      this.connectTimer = null;
    }
  }

  private send(ws: WebSocket | null, msg: string): void {
    if (ws?.readyState === WebSocket.OPEN) ws.send(msg);
  }

  private closeSockets(): void {
    closeSocket(this.snd);
    closeSocket(this.wf);
    this.snd = null;
    this.wf = null;
  }
}

function closeSocket(ws: WebSocket | null): void {
  if (!ws) return;
  ws.onopen = null;
  ws.onmessage = null;
  ws.onerror = null;
  ws.onclose = null;
  if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
    ws.close();
  }
}

function syncBytes(data: unknown): Uint8Array | null {
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (ArrayBuffer.isView(data)) {
    const view = data as ArrayBufferView;
    return new Uint8Array(view.buffer, view.byteOffset, view.byteLength);
  }
  return null;
}

function pcmFromBe(data: Uint8Array): Int16Array {
  const n = data.byteLength - (data.byteLength % 2);
  if (n < 2) return new Int16Array();
  const out = new Int16Array(n / 2);
  const view = new DataView(data.buffer, data.byteOffset, n);
  for (let i = 0; i < out.length; i++) {
    out[i] = view.getInt16(i * 2, false);
  }
  return out;
}

function utf8Decode(bytes: Uint8Array): string {
  try {
    return new TextDecoder("utf-8").decode(bytes);
  } catch {
    return "";
  }
}

function decodeUri(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function isLikelyPathError(err: unknown): boolean {
  return err instanceof Error && err.message.startsWith("kiwi_path_error:");
}

function badPasswordMessage(code: string | null): string {
  switch (code) {
    case "1":
      return "KiwiSDR rejected the password, or all open channels are busy.";
    case "2":
      return "KiwiSDR is still starting. Try again in a moment.";
    case "5":
      return "KiwiSDR does not allow multiple connections from this IP. Wait about a minute and try again (close other Kiwi tabs too).";
    default:
      return "KiwiSDR authentication failed.";
  }
}

/** Avoid JSON.parse on multi-hundred-KB load_cfg payloads. */
function receiverNameFromCfgLight(encoded: string): string | null {
  let raw = encoded;
  try {
    raw = decodeURIComponent(encoded);
  } catch {
    // keep raw
  }
  const match =
    /"rx_name"\s*:\s*"((?:\\.|[^"\\])*)"/.exec(raw) ??
    /"name"\s*:\s*"((?:\\.|[^"\\])*)"/.exec(raw);
  if (!match?.[1]) return null;
  try {
    return JSON.parse(`"${match[1]}"`) as string;
  } catch {
    return match[1];
  }
}
