/**
 * Plays the engine's 8 kHz voice frames through WebAudio with a small jitter
 * buffer. Frames are scheduled back to back; if the network stalls the player
 * re-anchors with a little head-room instead of clicking, and if it falls too far
 * behind it drops audio rather than growing latency.
 *
 * iOS Safari only lets an AudioContext start from a user gesture, so call
 * `unlock()` from a tap handler (the "Listen" button).
 */
import { AudioEnhancer } from "../audio-enhancement";
import type { DvAudioFrame } from "./types";

/** Head-room kept ahead of playback after (re)starting, in seconds. */
export const JITTER_S = 0.14;
/** Beyond this much queued audio the player skips ahead to stay near real time. */
export const MAX_AHEAD_S = 0.7;

type ContextCtor = typeof AudioContext;

export type PlayerSchedule = { startAt: number; nextTime: number; dropped: boolean };

/**
 * Where should the next frame start? Pure so it can be unit-tested.
 * `now` is the context clock, `nextTime` the end of audio already queued.
 */
export function scheduleFrame(now: number, nextTime: number, duration: number): PlayerSchedule {
  // Underrun (or first frame): re-anchor with head-room.
  if (nextTime < now + 0.01) {
    const startAt = now + JITTER_S;
    return { startAt, nextTime: startAt + duration, dropped: false };
  }
  // Way ahead of the clock: drop this frame so latency cannot build up.
  if (nextTime - now > MAX_AHEAD_S) {
    return { startAt: nextTime, nextTime, dropped: true };
  }
  return { startAt: nextTime, nextTime: nextTime + duration, dropped: false };
}

export function int16ToFloat(pcm: Int16Array): Float32Array {
  const out = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) out[i] = pcm[i] / 32768;
  return out;
}

export class PcmPlayer {
  private enhancer = new AudioEnhancer();
  private ctx: AudioContext | null = null;
  private gain: GainNode | null = null;
  private nextTime = 0;
  private volume = 0.8;
  private muted = false;
  /** Call number whose audio is playing; frames from other calls are ignored. */
  private lockedCall: number | null = null;
  private listeners = new Set<() => void>();
  droppedFrames = 0;

  get unlocked(): boolean {
    return this.ctx !== null && this.ctx.state === "running";
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private notify(): void {
    for (const listener of this.listeners) listener();
  }

  /** Create/resume the context. Must run inside a user gesture on iOS. */
  async unlock(): Promise<boolean> {
    if (typeof window === "undefined") return false;
    if (!this.ctx) {
      const Ctor: ContextCtor | undefined =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext?: ContextCtor }).webkitAudioContext;
      if (!Ctor) return false;
      this.ctx = new Ctor();
      this.gain = this.ctx.createGain();
      this.gain.connect(this.ctx.destination);
      this.applyGain();
    }
    try {
      await this.ctx.resume();
    } catch {
      /* resume needs a gesture; the caller can retry from another tap */
    }
    this.notify();
    return this.unlocked;
  }

  setVolume(volume: number): void {
    this.volume = Math.min(1, Math.max(0, volume));
    this.applyGain();
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    this.applyGain();
  }

  private applyGain(): void {
    if (this.gain) this.gain.gain.value = this.muted ? 0 : this.volume;
  }

  /** Follow this call (frames from others are skipped) or null to follow the next one heard. */
  lock(callNum: number | null): void {
    this.lockedCall = callNum;
  }

  get locked(): number | null {
    return this.lockedCall;
  }

  push(frame: DvAudioFrame): void {
    const ctx = this.ctx;
    const gain = this.gain;
    if (!ctx || !gain || ctx.state !== "running") return;
    if (this.lockedCall === null) this.lockedCall = frame.callNum;
    if (frame.callNum !== this.lockedCall) return;

    if (frame.pcm.length > 0) {
      const duration = frame.pcm.length / frame.sampleRate;
      const plan = scheduleFrame(ctx.currentTime, this.nextTime, duration);
      this.nextTime = plan.nextTime;
      if (plan.dropped) {
        this.droppedFrames += 1;
      } else {
        const buffer = ctx.createBuffer(1, frame.pcm.length, frame.sampleRate);
        buffer.copyToChannel(this.enhancer.process(int16ToFloat(frame.pcm), frame.sampleRate) as Float32Array<ArrayBuffer>, 0);
        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(gain);
        source.start(plan.startAt);
      }
    }
    if (frame.endOfCall) {
      this.enhancer.reset();
      this.lockedCall = null;
      this.nextTime = 0;
    }
  }

  /** Forget the queue, e.g. after a reconnect. */
  reset(): void {
    this.enhancer.reset();
    this.lockedCall = null;
    this.nextTime = 0;
  }

  dispose(): void {
    this.enhancer.reset();
    void this.ctx?.close();
    this.ctx = null;
    this.gain = null;
    this.listeners.clear();
  }
}
