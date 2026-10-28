import type { AudioTapListener } from "@/lib/sdr/tapping-player";

const MAX_LEAD_SEC = 0.6;
const START_PAD_SEC = 0.05;

/**
 * Plays server-demodulated PCM and optionally forwards it to decoders.
 * Volume and squelch are applied to the samples so taps match the stick path.
 */
export class OpenWebRxPlayback {
  private ctx: AudioContext | null = null;
  private gain: GainNode | null = null;
  private nextTime = 0;
  private volume = 0.8;
  private squelched = false;
  private tapping = false;
  private listeners = new Set<AudioTapListener>();

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
    this.squelched = squelched;
  }

  setTapping(enabled: boolean): void {
    this.tapping = enabled;
  }

  onAudio(listener: AudioTapListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  push(pcm: Int16Array, sampleRate: number): void {
    if (pcm.length === 0 || sampleRate <= 0) return;
    this.unlock();
    const ctx = this.ctx;
    const gain = this.gain;
    if (!ctx || !gain) return;

    const mono = new Float32Array(pcm.length);
    const scale = (this.squelched ? 0 : this.volume) / 32768;
    for (let i = 0; i < pcm.length; i++) {
      mono[i] = (pcm[i] ?? 0) * scale;
    }

    if (this.tapping && this.listeners.size > 0) {
      for (const listener of this.listeners) {
        listener(mono, sampleRate);
      }
    }

    const buffer = ctx.createBuffer(1, mono.length, sampleRate);
    buffer.copyToChannel(mono, 0);
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
    const ctx = this.ctx;
    this.ctx = null;
    this.gain = null;
    this.nextTime = 0;
    if (ctx) void ctx.close();
  }
}
