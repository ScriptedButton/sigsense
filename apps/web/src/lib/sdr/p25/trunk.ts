/**
 * Single-dongle P25 trunk follower: park on CC, follow grants, return after hangtime.
 */

import type { P25Call, P25RadioHooks } from "./types";
import type { TsbkMessage, ChannelIdentifier, VoiceGrant } from "./tsbk";

export type TrunkState = {
  parkedOnCc: boolean;
  controlChannelHz: number | null;
  voiceFrequencyHz: number | null;
  activeCall: P25Call | null;
};

export class TrunkFollower {
  private hooks: P25RadioHooks | null = null;
  private controlChannels: number[] = [];
  private hangtimeMs = 2_000;
  private follow = true;
  private ccIndex = 0;
  private hangTimer: ReturnType<typeof setTimeout> | null = null;
  private state: TrunkState = {
    parkedOnCc: true,
    controlChannelHz: null,
    voiceFrequencyHz: null,
    activeCall: null,
  };
  private channelIds = new Map<number, ChannelIdentifier>();
  private onChange: ((s: TrunkState) => void) | null = null;

  setHooks(hooks: P25RadioHooks | null): void {
    this.hooks = hooks;
  }

  setOnChange(cb: ((s: TrunkState) => void) | null): void {
    this.onChange = cb;
  }

  setControlChannels(freqs: number[]): void {
    this.controlChannels = freqs.filter((f) => f > 0);
    if (this.controlChannels.length && !this.state.controlChannelHz) {
      this.state.controlChannelHz = this.controlChannels[0]!;
    }
  }

  setHangtimeMs(ms: number): void {
    this.hangtimeMs = Math.max(200, ms);
  }

  setFollow(follow: boolean): void {
    this.follow = follow;
  }

  getChannelIds(): Map<number, ChannelIdentifier> {
    return this.channelIds;
  }

  getState(): TrunkState {
    return this.state;
  }

  reset(): void {
    this.clearHang();
    this.state = {
      parkedOnCc: true,
      controlChannelHz: this.controlChannels[0] ?? null,
      voiceFrequencyHz: null,
      activeCall: null,
    };
    this.emit();
  }

  /** Park on the next / current control channel. */
  async parkOnControlChannel(): Promise<void> {
    if (!this.hooks || this.controlChannels.length === 0) return;
    const hz =
      this.controlChannels[this.ccIndex % this.controlChannels.length]!;
    this.state.controlChannelHz = hz;
    this.state.parkedOnCc = true;
    this.state.voiceFrequencyHz = null;
    this.state.activeCall = null;
    this.hooks.setSchemeNbfm();
    this.hooks.setOffset(0);
    this.hooks.setDigitalMute(false);
    await this.hooks.setFrequency(hz);
    this.emit();
  }

  handleTsbk(msg: TsbkMessage): VoiceGrant | null {
    if (msg.type === "IDENTIFIER_UPDATE_VU" || msg.type === "IDENTIFIER_UPDATE") {
      if (
        msg.identifier != null &&
        msg.baseFreqMhz != null &&
        msg.channelSpacingKhz != null
      ) {
        this.channelIds.set(msg.identifier, {
          identifier: msg.identifier,
          bandwidthKhz: msg.bandwidthKhz ?? 12.5,
          txOffsetMhz: msg.txOffsetMhz ?? 0,
          channelSpacingKhz: msg.channelSpacingKhz,
          baseFreqMhz: msg.baseFreqMhz,
        });
      }
    }

    if (
      msg.type === "GROUP_VOICE_GRANT" ||
      msg.type === "UNIT_TO_UNIT_GRANT" ||
      msg.type === "GROUP_VOICE_GRANT_UPDATE"
    ) {
      const tgid = msg.tgid ?? 0;
      const frequencyHz = msg.frequencyHz ?? 0;
      if (!tgid || !frequencyHz) return null;
      const grant: VoiceGrant = {
        tgid,
        sourceId: msg.sourceId ?? 0,
        channel: msg.channel ?? 0,
        frequencyHz,
        emergency: Boolean(msg.emergency),
        encrypted: Boolean(msg.encrypted),
      };
      void this.followGrant(grant);
      return grant;
    }
    return null;
  }

  async followGrant(grant: VoiceGrant): Promise<void> {
    if (!this.follow || !this.hooks) return;
    this.clearHang();
    const call: P25Call = {
      id: `${grant.tgid}-${grant.frequencyHz}-${Date.now()}`,
      tgid: grant.tgid,
      sourceId: grant.sourceId || null,
      frequencyHz: grant.frequencyHz,
      channel: grant.channel,
      encrypted: grant.encrypted,
      emergency: grant.emergency,
      startMs: Date.now(),
      lastActivityMs: Date.now(),
      active: true,
    };
    this.state.activeCall = call;
    this.state.voiceFrequencyHz = grant.frequencyHz;
    this.state.parkedOnCc = false;
    this.hooks.setSchemeNbfm();
    this.hooks.setOffset(0);
    this.hooks.setDigitalMute(true);
    await this.hooks.setFrequency(grant.frequencyHz);
    this.emit();
  }

  /** Voice activity on the current channel — refresh hangtime. */
  noteVoiceActivity(): void {
    if (!this.state.activeCall) return;
    this.state.activeCall = {
      ...this.state.activeCall,
      lastActivityMs: Date.now(),
    };
    this.clearHang();
    this.hangTimer = setTimeout(() => {
      void this.returnToControl();
    }, this.hangtimeMs);
    this.emit();
  }

  async returnToControl(): Promise<void> {
    this.clearHang();
    if (this.state.activeCall) {
      this.state.activeCall = { ...this.state.activeCall, active: false };
    }
    this.hooks?.setDigitalMute(false);
    await this.parkOnControlChannel();
  }

  private clearHang(): void {
    if (this.hangTimer) {
      clearTimeout(this.hangTimer);
      this.hangTimer = null;
    }
  }

  private emit(): void {
    this.onChange?.(this.state);
  }
}
