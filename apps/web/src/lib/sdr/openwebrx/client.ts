import { ImaAdpcmDecoder } from "./adpcm";
import {
  EMPTY_OWRX_CONFIG,
  OWRX_HANDSHAKE_CLIENT,
  OWRX_HANDSHAKE_SERVER,
  OWRX_HD_OUTPUT_RATE,
  OWRX_OP_AUDIO,
  OWRX_OP_HD_AUDIO,
  OWRX_OP_SPECTRUM,
  OWRX_OUTPUT_RATE,
  type OpenWebRxCompression,
  type OpenWebRxConfig,
  type OpenWebRxProfile,
} from "./protocol";
import { decodeSpectrumPayload } from "./spectrum";

export type OpenWebRxDspParams = {
  mod?: string;
  offset_freq?: number;
  low_cut?: number;
  high_cut?: number;
  squelch_level?: number;
  output_rate?: number;
  hd_output_rate?: number;
};

export type OpenWebRxClientHandlers = {
  onSpectrum: (shiftedBins: Float32Array, centerHz: number, sampleRate: number) => void;
  onAudio: (pcm: Int16Array, sampleRate: number, hd: boolean) => void;
  onConfig: (config: OpenWebRxConfig, resetTuning: boolean) => void;
  onProfiles: (profiles: OpenWebRxProfile[]) => void;
  onReceiverName: (name: string | null) => void;
  onServerError: (message: string) => void;
  onClose: () => void;
};

/**
 * Browser client for a local (or remote) OpenWebRX+ receiver.
 * The server demodulates; this socket carries control, waterfall rows, and PCM.
 */
export class OpenWebRxClient {
  private ws: WebSocket | null = null;
  private handshakeDone = false;
  private configCount = 0;
  private pendingProfile = false;
  private dspStarted = false;
  private config: OpenWebRxConfig = { ...EMPTY_OWRX_CONFIG };
  private readonly fftDecoder = new ImaAdpcmDecoder();
  private readonly audioDecoder = new ImaAdpcmDecoder();
  private readonly hdDecoder = new ImaAdpcmDecoder();
  private connectTimer: ReturnType<typeof setTimeout> | null = null;
  private pendingReject: ((err: Error) => void) | null = null;

  constructor(private readonly handlers: OpenWebRxClientHandlers) {}

  getConfig(): OpenWebRxConfig {
    return this.config;
  }

  connect(wsUrl: string): Promise<void> {
    this.closeSocket();
    this.handshakeDone = false;
    this.configCount = 0;
    this.pendingProfile = false;
    this.dspStarted = false;
    this.config = { ...EMPTY_OWRX_CONFIG };

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

      let ws: WebSocket;
      try {
        ws = new WebSocket(wsUrl);
      } catch (err) {
        finish(err instanceof Error ? err : new Error(String(err)));
        return;
      }
      this.ws = ws;
      ws.binaryType = "arraybuffer";

      this.connectTimer = setTimeout(() => {
        if (!this.handshakeDone) {
          ws.close();
          finish(
            new Error(
              "Timed out waiting for OpenWebRX+. Check that the server is running.",
            ),
          );
        }
      }, 8_000);

      ws.onopen = () => {
        ws.send(OWRX_HANDSHAKE_CLIENT);
      };

      ws.onmessage = (event) => {
        if (typeof event.data === "string") {
          if (!this.handshakeDone) {
            if (event.data.startsWith(OWRX_HANDSHAKE_SERVER)) {
              this.handshakeDone = true;
              this.clearConnectTimer();
              this.sendJson({
                type: "connectionproperties",
                params: {
                  output_rate: OWRX_OUTPUT_RATE,
                  hd_output_rate: OWRX_HD_OUTPUT_RATE,
                },
              });
              finish();
            }
            return;
          }
          this.handleText(event.data);
          return;
        }
        if (!this.handshakeDone) return;
        void this.handleBinary(event.data);
      };

      ws.onerror = () => {
        if (!this.handshakeDone) {
          finish(
            new Error(
              "Could not connect to OpenWebRX+. Check the address and that the server is running.",
            ),
          );
        }
      };

      ws.onclose = () => {
        if (!settled) {
          finish(
            new Error(
              "OpenWebRX+ closed the connection before the handshake finished.",
            ),
          );
          return;
        }
        this.handlers.onClose();
      };
    });
  }

  close(): void {
    this.clearConnectTimer();
    const rejectPending = this.pendingReject;
    this.pendingReject = null;
    this.closeSocket();
    rejectPending?.(new Error("OpenWebRX+ connection closed."));
  }

  applyTuning(params: OpenWebRxDspParams, restart: boolean): void {
    const body: OpenWebRxDspParams = {
      output_rate: OWRX_OUTPUT_RATE,
      hd_output_rate: OWRX_HD_OUTPUT_RATE,
      squelch_level: -150,
      ...params,
    };
    if (restart || !this.dspStarted) {
      this.audioDecoder.reset();
      this.hdDecoder.reset();
      this.sendJson({ type: "dspcontrol", action: "start", params: body });
      this.dspStarted = true;
      return;
    }
    this.sendJson({ type: "dspcontrol", params: body });
  }

  setCenterFrequency(hz: number): void {
    this.sendJson({
      type: "setfrequency",
      params: { frequency: Math.round(hz) },
    });
  }

  selectProfile(profileId: string): void {
    this.pendingProfile = true;
    this.sendJson({ type: "selectprofile", params: { profile: profileId } });
  }

  private closeSocket(): void {
    const ws = this.ws;
    this.ws = null;
    if (!ws) return;
    ws.onopen = null;
    ws.onmessage = null;
    ws.onerror = null;
    ws.onclose = null;
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close();
    }
  }

  private clearConnectTimer(): void {
    if (this.connectTimer != null) {
      clearTimeout(this.connectTimer);
      this.connectTimer = null;
    }
  }

  private sendJson(message: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  private handleText(text: string): void {
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(text) as Record<string, unknown>;
    } catch {
      return;
    }
    const type = typeof msg.type === "string" ? msg.type : "";
    const value = msg.value;

    switch (type) {
      case "config":
        this.applyConfig(value);
        break;
      case "profiles":
        this.handlers.onProfiles(parseProfiles(value));
        break;
      case "receiver_details":
        this.handlers.onReceiverName(parseReceiverName(value));
        break;
      case "sdr_error":
      case "demodulator_error":
        this.handlers.onServerError(
          typeof value === "string" && value
            ? value
            : "OpenWebRX+ reported a receiver error.",
        );
        break;
      case "backoff":
        this.handlers.onServerError(
          typeof msg.reason === "string" && msg.reason
            ? msg.reason
            : "OpenWebRX+ asked the client to slow down.",
        );
        break;
      default:
        break;
    }
  }

  private applyConfig(value: unknown): void {
    const raw = isRecord(value) ? value : {};
    const previous = this.config;
    const next: OpenWebRxConfig = {
      centerFreqHz: numberOr(raw.center_freq, previous.centerFreqHz),
      sampleRate: numberOr(raw.samp_rate, previous.sampleRate),
      fftSize: numberOr(raw.fft_size, previous.fftSize),
      startMod:
        typeof raw.start_mod === "string" ? raw.start_mod : previous.startMod,
      startOffsetHz: numberOr(raw.start_offset_freq, previous.startOffsetHz),
      audioCompression: compressionOr(
        raw.audio_compression,
        previous.audioCompression,
      ),
      fftCompression: compressionOr(raw.fft_compression, previous.fftCompression),
      allowsCenterChanges:
        typeof raw.allow_center_freq_changes === "boolean"
          ? raw.allow_center_freq_changes
          : previous.allowsCenterChanges,
      profileId: profileIdFrom(raw) ?? previous.profileId,
    };
    this.config = next;
    const resetTuning = this.configCount === 0 || this.pendingProfile;
    this.configCount += 1;
    this.pendingProfile = false;
    this.handlers.onConfig(next, resetTuning);
  }

  private async handleBinary(data: unknown): Promise<void> {
    const bytes = await toBytes(data);
    if (bytes.length < 2) return;
    const opcode = bytes[0] ?? 0;
    const payload = bytes.subarray(1);
    if (opcode === OWRX_OP_SPECTRUM) {
      this.emitSpectrum(payload);
      return;
    }
    if (opcode === OWRX_OP_AUDIO) {
      this.emitAudio(payload, false);
      return;
    }
    if (opcode === OWRX_OP_HD_AUDIO) {
      this.emitAudio(payload, true);
    }
  }

  private emitSpectrum(payload: Uint8Array): void {
    const shifted = decodeSpectrumPayload(
      payload,
      this.config.fftCompression,
      this.fftDecoder,
      this.config.fftSize,
    );
    if (!shifted || shifted.length === 0) return;
    const center = this.config.centerFreqHz ?? 0;
    const rate = this.config.sampleRate ?? 0;
    if (rate <= 0) return;
    this.handlers.onSpectrum(shifted, center, rate);
  }

  private emitAudio(payload: Uint8Array, hd: boolean): void {
    const compression = this.config.audioCompression;
    let pcm: Int16Array;
    if (compression === "adpcm") {
      const decoder = hd ? this.hdDecoder : this.audioDecoder;
      pcm = decoder.decodeWithSync(payload);
    } else {
      pcm = pcmFromPayload(payload);
    }
    if (pcm.length === 0) return;
    const rate = hd ? OWRX_HD_OUTPUT_RATE : OWRX_OUTPUT_RATE;
    this.handlers.onAudio(pcm, rate, hd);
  }
}

function pcmFromPayload(payload: Uint8Array): Int16Array {
  const byteLength = payload.byteLength - (payload.byteLength % 2);
  if (byteLength < 2) return new Int16Array();
  const copy = payload.buffer.slice(
    payload.byteOffset,
    payload.byteOffset + byteLength,
  );
  return new Int16Array(copy);
}

async function toBytes(data: unknown): Promise<Uint8Array> {
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (typeof Blob !== "undefined" && data instanceof Blob) {
    return new Uint8Array(await data.arrayBuffer());
  }
  return new Uint8Array();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function numberOr(value: unknown, fallback: number | null): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function compressionOr(
  value: unknown,
  fallback: OpenWebRxCompression,
): OpenWebRxCompression {
  if (value === "none" || value === "adpcm") return value;
  return fallback;
}

function profileIdFrom(raw: Record<string, unknown>): string | null {
  const sdr = typeof raw.sdr_id === "string" ? raw.sdr_id : "";
  const profile = typeof raw.profile_id === "string" ? raw.profile_id : "";
  if (sdr && profile) return `${sdr}|${profile}`;
  return null;
}

function parseProfiles(value: unknown): OpenWebRxProfile[] {
  if (!Array.isArray(value)) return [];
  const profiles: OpenWebRxProfile[] = [];
  for (const entry of value) {
    if (!isRecord(entry)) continue;
    const id = typeof entry.id === "string" ? entry.id : "";
    if (!id) continue;
    const name = typeof entry.name === "string" && entry.name ? entry.name : id;
    profiles.push({ id, name });
  }
  return profiles;
}

function parseReceiverName(value: unknown): string | null {
  if (!isRecord(value)) return null;
  if (typeof value.receiver_name === "string" && value.receiver_name) {
    return value.receiver_name;
  }
  if (typeof value.name === "string" && value.name) return value.name;
  return null;
}
