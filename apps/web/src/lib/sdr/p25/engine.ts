/**
 * Owns P25 Phase 1 decoder state: C4FM frames → TSBK/LDU → trunk + IMBE voice.
 */

import { P25Framer } from "./framer";
import { ImbeVocoder } from "./imbe";
import { parseLdu } from "./ldu";
import {
  parseTsbkBytes,
  tsbkDibitsToPayload,
  type ChannelIdentifier,
  type TsbkMessage,
} from "./tsbk";
import { TrunkFollower } from "./trunk";
import {
  DEFAULT_P25_CONFIG,
  DEFAULT_P25_SYSTEM,
  type P25Call,
  type P25Config,
  type P25Listener,
  type P25Message,
  type P25RadioHooks,
  type P25State,
} from "./types";
import { P25VoicePlayer } from "./voice-player";

const MAX_MESSAGES = 80;
const MAX_RECENT_CALLS = 40;

export class P25DecoderEngine {
  private state: P25State = {
    enabled: false,
    status: "idle",
    config: { ...DEFAULT_P25_CONFIG },
    system: { ...DEFAULT_P25_SYSTEM },
    lock: 0,
    decodeRate: 0,
    controlChannelHz: null,
    voiceFrequencyHz: null,
    activeCalls: [],
    recentCalls: [],
    messages: [],
    error: null,
    imbeReady: false,
  };
  private listeners = new Set<P25Listener>();
  private framer = new P25Framer();
  private trunk = new TrunkFollower();
  private imbe = new ImbeVocoder();
  private voice = new P25VoicePlayer();
  private disposed = false;
  private msgSeq = 0;
  private framesWindow = 0;
  private framesWindowStart = 0;
  private encryptedHold = false;
  private initStarted = false;

  constructor() {
    this.trunk.setOnChange((ts) => {
      this.patch({
        controlChannelHz: ts.controlChannelHz,
        voiceFrequencyHz: ts.voiceFrequencyHz,
        activeCalls: ts.activeCall?.active ? [ts.activeCall] : [],
        status: this.deriveStatus(ts.parkedOnCc, Boolean(ts.activeCall?.active)),
      });
    });
  }

  getState(): P25State {
    return this.state;
  }

  subscribe(listener: P25Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  setRadioHooks(hooks: P25RadioHooks | null): void {
    this.trunk.setHooks(hooks);
  }

  async setEnabled(enabled: boolean): Promise<void> {
    if (enabled === this.state.enabled) return;
    if (!enabled) {
      this.framer.reset();
      this.trunk.reset();
      this.voice.reset();
      this.imbe.reset();
      this.patch({
        enabled: false,
        status: "idle",
        lock: 0,
        error: null,
        activeCalls: [],
        voiceFrequencyHz: null,
      });
      return;
    }
    await this.ensureImbe();
    this.framer.reset();
    this.trunk.setControlChannels(this.state.config.controlChannelsHz);
    this.trunk.setHangtimeMs(this.state.config.hangtimeMs);
    this.trunk.setFollow(
      this.state.config.mode === "trunk" && this.state.config.followGrants,
    );
    this.patch({
      enabled: true,
      status: "listening",
      error: null,
    });
    if (
      this.state.config.mode === "trunk" &&
      this.state.config.controlChannelsHz.length
    ) {
      await this.trunk.parkOnControlChannel();
    }
  }

  patchConfig(partial: Partial<P25Config>): void {
    const config = { ...this.state.config, ...partial };
    this.trunk.setControlChannels(config.controlChannelsHz);
    this.trunk.setHangtimeMs(config.hangtimeMs);
    this.trunk.setFollow(config.mode === "trunk" && config.followGrants);
    this.voice.setMuted(!config.playVoice);
    this.patch({ config });
  }

  clearMessages(): void {
    this.patch({ messages: [] });
  }

  clearCalls(): void {
    this.patch({ activeCalls: [], recentCalls: [] });
  }

  dispose(): void {
    this.disposed = true;
    this.voice.dispose();
    this.imbe.dispose();
    this.trunk.reset();
    this.patch({
      enabled: false,
      status: "idle",
      lock: 0,
      error: null,
    });
  }

  /** Feed C4FM dibits from the IQ receiver. */
  pushDibits(dibits: number[], lock: number): void {
    if (!this.state.enabled || this.disposed) return;
    if (dibits.length === 0) return;

    const frames = this.framer.push(dibits);
    const now = performance.now?.() ?? Date.now();
    if (!this.framesWindowStart) this.framesWindowStart = now;
    this.framesWindow += frames.length;
    if (now - this.framesWindowStart > 1000) {
      this.patch({
        decodeRate: this.framesWindow,
        lock,
      });
      this.framesWindow = 0;
      this.framesWindowStart = now;
    } else if (Math.abs(lock - this.state.lock) > 0.05) {
      this.patch({ lock });
    }

    for (const frame of frames) {
      if (
        this.state.config.nac != null &&
        this.state.config.nac >= 0 &&
        frame.nac !== this.state.config.nac
      ) {
        continue;
      }

      this.patch({
        system: { ...this.state.system, nac: frame.nac },
        status:
          this.state.config.mode === "trunk" ? "trunking" : "synced",
      });

      if (frame.duidLabel === "TSBK") {
        this.handleTsbkFrame(frame.nac, frame.payload);
      } else if (frame.duidLabel === "LDU1" || frame.duidLabel === "LDU2") {
        this.handleLdu(frame.duidLabel, frame.payload);
      } else if (frame.duidLabel === "TDU" || frame.duidLabel === "TDULC") {
        this.trunk.noteVoiceActivity();
        void this.trunk.returnToControl();
      } else {
        this.pushMessage({
          opcode: null,
          opcodeName: frame.duidLabel,
          nac: frame.nac,
          summary: `${frame.duidLabel} NAC ${frame.nac.toString(16)}`,
        });
      }
    }
  }

  private handleTsbkFrame(nac: number, payload: number[]): void {
    const channelIds = this.trunk.getChannelIds();
    // Payload starts with NID (32 dibits after status strip) then coded TSBKs.
    // For robust demo/live hybrid: try parse at offset 24 and also raw 48-dibit windows.
    const msgs = this.extractTsbks(payload, channelIds);
    for (const msg of msgs) {
      this.pushMessage({
        opcode: msg.opcode,
        opcodeName: msg.opcodeName,
        nac,
        summary: msg.summary,
      });
      if (msg.wacn != null || msg.systemId != null || msg.rfssId != null) {
        this.patch({
          system: {
            nac,
            wacn: msg.wacn ?? this.state.system.wacn,
            systemId: msg.systemId ?? this.state.system.systemId,
            rfssId: msg.rfssId ?? this.state.system.rfssId,
            siteId: msg.siteId ?? this.state.system.siteId,
          },
        });
      }
      if (msg.type === "IDENTIFIER_UPDATE_VU" || msg.type === "IDENTIFIER_UPDATE") {
        this.trunk.handleTsbk(msg);
      } else if (this.state.config.mode === "trunk") {
        const grant = this.trunk.handleTsbk(msg);
        if (grant) this.recordCallFromGrant(grant, msg);
      }
    }
  }

  private extractTsbks(
    payload: number[],
    channelIds: Map<number, ChannelIdentifier>,
  ): TsbkMessage[] {
    const out: TsbkMessage[] = [];
    const tryAt = (offset: number) => {
      for (let b = 0; b < 3; b++) {
        const slice = payload.slice(offset + b * 48, offset + (b + 1) * 48);
        if (slice.length < 48) break;
        const bytes = tsbkDibitsToPayload(slice);
        if (!bytes) continue;
        const parsed = parseTsbkBytes(bytes, channelIds);
        if (parsed && parsed.type !== "UNKNOWN") out.push(parsed);
        else if (parsed && (bytes[0]! & 0x3f) === 0x00) out.push(parsed);
      }
    };
    tryAt(24);
    if (out.length === 0) tryAt(0);
    if (out.length === 0) tryAt(8);
    return out;
  }

  private handleLdu(kind: "LDU1" | "LDU2", payload: number[]): void {
    const ldu = parseLdu(kind, payload);
    this.patch({ status: "voice" });
    this.trunk.noteVoiceActivity();

    if (ldu.linkControl?.tgid != null) {
      this.upsertActiveCall({
        tgid: ldu.linkControl.tgid,
        sourceId: ldu.linkControl.sourceId,
        encrypted: Boolean(ldu.linkControl.encrypted),
        emergency: Boolean(ldu.linkControl.emergency),
      });
    }
    if (ldu.encryption) {
      this.encryptedHold = ldu.encryption.encrypted;
      if (ldu.encryption.encrypted) {
        this.pushMessage({
          opcode: null,
          opcodeName: "ENCRYPTED",
          nac: this.state.system.nac,
          summary: `Encrypted ALGID 0x${ldu.encryption.algid.toString(16)}`,
        });
      }
    }

    if (
      !this.state.config.playVoice ||
      this.encryptedHold ||
      !this.imbe.isReady()
    ) {
      return;
    }

    for (const frame of ldu.imbeFrames) {
      const pcm = this.imbe.decodeFrame(frame);
      this.voice.play(pcm);
    }
  }

  private recordCallFromGrant(
    grant: { tgid: number; sourceId: number; frequencyHz: number; channel: number; encrypted: boolean; emergency: boolean },
    msg: TsbkMessage,
  ): void {
    const call: P25Call = {
      id: `${grant.tgid}-${grant.frequencyHz}-${Date.now()}`,
      tgid: grant.tgid,
      sourceId: grant.sourceId || null,
      frequencyHz: grant.frequencyHz || msg.frequencyHz || 0,
      channel: grant.channel,
      encrypted: grant.encrypted,
      emergency: grant.emergency,
      startMs: Date.now(),
      lastActivityMs: Date.now(),
      active: true,
    };
    const recent = [call, ...this.state.recentCalls.filter((c) => c.tgid !== call.tgid)].slice(
      0,
      MAX_RECENT_CALLS,
    );
    this.patch({ recentCalls: recent });
  }

  private upsertActiveCall( partial: {
    tgid: number;
    sourceId: number | null;
    encrypted: boolean;
    emergency: boolean;
  }): void {
    const existing = this.state.activeCalls[0];
    const call: P25Call = {
      id: existing?.id ?? `voice-${partial.tgid}-${Date.now()}`,
      tgid: partial.tgid,
      sourceId: partial.sourceId,
      frequencyHz: this.state.voiceFrequencyHz ?? 0,
      channel: existing?.channel ?? null,
      encrypted: partial.encrypted,
      emergency: partial.emergency,
      startMs: existing?.startMs ?? Date.now(),
      lastActivityMs: Date.now(),
      active: true,
    };
    this.patch({ activeCalls: [call] });
  }

  private pushMessage(partial: Omit<P25Message, "id" | "timestampMs">): void {
    const msg: P25Message = {
      id: ++this.msgSeq,
      timestampMs: Date.now(),
      ...partial,
    };
    const messages = [msg, ...this.state.messages].slice(0, MAX_MESSAGES);
    this.patch({ messages });
  }

  private deriveStatus(
    parkedOnCc: boolean,
    onVoice: boolean,
  ): P25State["status"] {
    if (!this.state.enabled) return "idle";
    if (onVoice) return "voice";
    if (this.state.config.mode === "trunk") {
      return parkedOnCc ? "trunking" : "voice";
    }
    return this.state.lock > 0.2 ? "synced" : "listening";
  }

  private async ensureImbe(): Promise<void> {
    if (this.initStarted) return;
    this.initStarted = true;
    try {
      const ok = await this.imbe.init();
      this.patch({ imbeReady: ok });
      if (!ok) {
        this.patch({
          error: "IMBE vocoder failed to load — metadata still works",
        });
      }
    } catch (err) {
      this.patch({
        imbeReady: false,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  private patch(partial: Partial<P25State>): void {
    this.state = { ...this.state, ...partial };
    for (const listener of this.listeners) listener(this.state);
  }
}
