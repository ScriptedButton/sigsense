import { AudioEnhancer } from "../audio-enhancement";
import type { AudioTapListener } from "@/lib/sdr/tapping-player";
import { pcmMetrics } from "../receiver-control/capture";

const MAX_LEAD_SEC = 0.6;
const START_PAD_SEC = 0.05;

/**
 * Plays server-demodulated PCM and optionally forwards it to decoders.
 * Squelch is applied to taps; listening volume is playback-only, matching the
 * local stick path and keeping decoder/transcription levels stable.
 */
export class OpenWebRxPlayback {
  constructor(private enhancer = new AudioEnhancer()) {}
  private ctx: AudioContext | null = null;
  private gain: GainNode | null = null;
  private nextTime = 0;
  private volume = 0.8;
  private squelched = false;
  private tapping = false;
  private listeners = new Set<AudioTapListener>();
  private playbackListeners = new Set<AudioTapListener>();
  private lastPlaybackMetrics: ReturnType<typeof pcmMetrics> | null = null;

  /** Create the audio context during the connect click so playback is allowed. */
  unlock(): void {
    if (typeof window === "undefined") return;
    if (!this.ctx) {
      const ctx = new AudioContext();
      const gain = ctx.createGain();
      gain.gain.value = 1;
      gain.connect(ctx.destination);
      this.ctx = ctx;
      this.gain = gain;
    }
    void this.ctx.resume();
  }

  setVolume(volume: number): void {
    this.volume = volume;
  }

  setSquelched(squelched: boolean): void {
    if (squelched && !this.squelched) this.enhancer.reset();
    this.squelched = squelched;
  }

  setTapping(enabled: boolean): void {
    this.tapping = enabled;
  }

  onAudio(listener: AudioTapListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onPlaybackAudio(listener: AudioTapListener): () => void {
    this.playbackListeners.add(listener);
    return () => { this.playbackListeners.delete(listener); };
  }

  getDiagnostics() {
    return { contextState: this.ctx?.state ?? "not-created", sampleRate: this.ctx?.sampleRate ?? null,
      queueLeadSeconds: this.ctx ? Math.max(0, this.nextTime - this.ctx.currentTime) : 0,
      playback: this.lastPlaybackMetrics, volume: this.volume, squelched: this.squelched };
  }

  push(pcm: Int16Array, sampleRate: number): void {
    if (pcm.length === 0 || sampleRate <= 0) return;
    this.unlock();
    const ctx = this.ctx;
    const gain = this.gain;
    if (!ctx || !gain) return;

    const mono = new Float32Array(pcm.length);
    const scale = (this.squelched ? 0 : 1) / 32768;
    for (let i = 0; i < pcm.length; i++) {
      mono[i] = (pcm[i] ?? 0) * scale;
    }

    if (this.tapping && this.listeners.size > 0) {
      for (const listener of this.listeners) {
        listener(mono, sampleRate);
      }
    }

    const enhanced = this.enhancer.process(mono, sampleRate);
    // Enhancement works at decoder level, independent of the user's volume.
    // Applying volume before RNNoise made quiet listening settings look like a
    // low-SNR signal and changed the amount of suppression.
    if (this.volume !== 1) {
      for (let i = 0; i < enhanced.length; i++) enhanced[i] *= this.volume;
    }
    this.lastPlaybackMetrics = pcmMetrics(enhanced);
    for (const listener of this.playbackListeners) listener(enhanced, sampleRate);
    const buffer = ctx.createBuffer(1, mono.length, sampleRate);
    buffer.copyToChannel(enhanced as Float32Array<ArrayBuffer>, 0);
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(gain);

    const now = ctx.currentTime;
    if (this.nextTime < now) this.nextTime = now + START_PAD_SEC;
    if (this.nextTime - now > MAX_LEAD_SEC) this.nextTime = now + START_PAD_SEC;
    source.start(this.nextTime);
    this.nextTime += buffer.duration;
  }

  close(): void {
    this.enhancer.reset();
    const ctx = this.ctx;
    this.ctx = null;
    this.gain = null;
    this.nextTime = 0;
    if (ctx) void ctx.close();
  }
}
