import type { Player } from "@jtarrio/signals/demod/player.js";

import { AudioEnhancer } from "./audio-enhancement";
import { mixToMono } from "./audio-utils";
import { pcmMetrics } from "./receiver-control/capture";

export type AudioTapListener = (mono: Float32Array, sampleRate: number) => void;

const START_LEAD_SECONDS = 0.05;
const MAX_LEAD_SECONDS = 0.6;

/** Pure scheduling step, exported so alternating block sizes can be regression tested. */
export function scheduleAudioBlock(
  queuedUntil: number,
  now: number,
  duration: number,
): { start: number; queuedUntil: number } {
  const validQueue = queuedUntil >= now && queuedUntil - now <= MAX_LEAD_SECONDS;
  const start = validQueue ? queuedUntil : now + START_LEAD_SECONDS;
  return { start, queuedUntil: start + duration };
}

/** App-owned Web Audio queue; advances by the block just scheduled. */
class ScheduledAudioPlayer {
  private context: AudioContext | null = null;
  private gainNode: GainNode | null = null;
  private queuedUntil = 0;
  private volume = 0;
  private sources = new Set<AudioBufferSourceNode>();
  private underruns = 0;
  private queueResets = 0;

  get sampleRate(): number {
    return this.context?.sampleRate ?? 48_000;
  }

  setVolume(volume: number): void {
    this.volume = volume;
    if (this.gainNode) this.gainNode.gain.value = volume;
  }

  getVolume(): number {
    return this.volume;
  }

  play(left: Float32Array, right: Float32Array): void {
    if (!this.context || !this.gainNode) {
      const context = new AudioContext({ sampleRate: 48_000 });
      const gain = context.createGain();
      gain.gain.value = this.volume;
      gain.connect(context.destination);
      this.context = context;
      this.gainNode = gain;
    }

    const context = this.context;
    if (context.state === "suspended") void context.resume().catch(() => {});
    const length = Math.min(left.length, right.length);
    if (length === 0) return;
    const buffer = context.createBuffer(2, length, context.sampleRate);
    buffer.getChannelData(0).set(left.subarray(0, length));
    buffer.getChannelData(1).set(right.subarray(0, length));
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.gainNode);
    // A queue reset must cancel the old queue, otherwise the two timelines
    // overlap and play the same reception at different delays.
    if (this.queuedUntil - context.currentTime > MAX_LEAD_SECONDS) {
      this.queueResets++;
      for (const pending of this.sources) pending.stop();
      this.sources.clear();
    }
    this.sources.add(source);
    source.onended = () => {
      this.sources.delete(source);
      source.disconnect();
    };

    const scheduled = scheduleAudioBlock(
      this.queuedUntil,
      context.currentTime,
      buffer.duration,
    );
    if (this.queuedUntil > 0 && this.queuedUntil < context.currentTime) this.underruns++;
    source.start(scheduled.start);
    this.queuedUntil = scheduled.queuedUntil;
  }

  getDiagnostics() {
    return { sampleRate: this.sampleRate, contextState: this.context?.state ?? "not-created",
      queueLeadSeconds: this.context ? Math.max(0, this.queuedUntil - this.context.currentTime) : 0,
      activeSources: this.sources.size, underruns: this.underruns, queueResets: this.queueResets };
  }

  close(): void {
    for (const source of this.sources) source.stop();
    this.sources.clear();
    if (this.context) void this.context.close();
    this.context = null;
    this.gainNode = null;
    this.queuedUntil = 0;
  }
}

/**
 * Plays demodulated audio and optionally forwards mono PCM to listeners
 * (used for live transcription, FSK, and FT8 decoders).
 */
export class TappingPlayer implements Player {
  private readonly inner = new ScheduledAudioPlayer();
  private readonly enhancer = new AudioEnhancer();
  private listeners = new Set<AudioTapListener>();
  private playbackListeners = new Set<AudioTapListener>();
  private lastPlaybackMetrics: ReturnType<typeof pcmMetrics> | null = null;
  private tapping = false;
  /** When true, audio is silenced before play (squelch closed). */
  private squelched = false;

  get sampleRate(): number {
    return this.inner.sampleRate;
  }

  setVolume(volume: number): void {
    this.inner.setVolume(volume);
  }

  getVolume(): number {
    return this.inner.getVolume();
  }

  setSquelched(squelched: boolean): void {
    if (squelched && !this.squelched) this.enhancer.reset();
    this.squelched = squelched;
  }

  isSquelched(): boolean {
    return this.squelched;
  }

  play(left: Float32Array, right: Float32Array): void {
    const playLeft = this.squelched ? new Float32Array(left.length) : left;
    const playRight = this.squelched ? new Float32Array(right.length) : right;
    const rate = this.inner.sampleRate;
    const enhancedLeft = this.enhancer.process(playLeft, rate, 0);
    const enhancedRight = this.enhancer.process(playRight, rate, 1);
    this.inner.play(enhancedLeft, enhancedRight);
    const playbackMono = mixToMono(enhancedLeft, enhancedRight);
    for (let k = 0; k < playbackMono.length; k++) playbackMono[k] *= this.getVolume() * Math.SQRT1_2;
    this.lastPlaybackMetrics = pcmMetrics(playbackMono);
    for (const listener of this.playbackListeners) listener(playbackMono, rate);
    if (!this.tapping || this.listeners.size === 0) return;
    const mono = mixToMono(playLeft, playRight);
    for (const listener of this.listeners) {
      listener(mono, rate);
    }
  }

  resetAudioEnhancement(): void { this.enhancer.reset(); }
  close(): void { this.enhancer.reset(); this.inner.close(); }
  getDiagnostics() { return { ...this.inner.getDiagnostics(), playback: this.lastPlaybackMetrics, squelched: this.squelched, volume: this.getVolume() }; }
  onPlaybackAudio(listener: AudioTapListener): () => void {
    this.playbackListeners.add(listener);
    return () => { this.playbackListeners.delete(listener); };
  }

  setTapping(enabled: boolean): void {
    this.tapping = enabled;
  }

  isTapping(): boolean {
    return this.tapping;
  }

  onAudio(listener: AudioTapListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
}
