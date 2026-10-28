/**
 * Plays IMBE PCM via Web Audio, independent of the NBFM AudioPlayer.
 */

import { IMBE_SAMPLE_RATE } from "./imbe";

export class P25VoicePlayer {
  private ctx: AudioContext | null = null;
  private nextTime = 0;
  private volume = 0.85;
  private muted = false;

  setVolume(v: number): void {
    this.volume = Math.max(0, Math.min(1, v));
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
  }

  private ensureCtx(): AudioContext | null {
    if (typeof window === "undefined") return null;
    if (!this.ctx) {
      const Ctx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext })
          .webkitAudioContext;
      this.ctx = new Ctx();
      this.nextTime = this.ctx.currentTime;
    }
    if (this.ctx.state === "suspended") {
      void this.ctx.resume();
    }
    return this.ctx;
  }

  /** Queue 8 kHz mono float samples. */
  play(pcm8k: Float32Array): void {
    if (this.muted || pcm8k.length === 0) return;
    const ctx = this.ensureCtx();
    if (!ctx) return;

    const buffer = ctx.createBuffer(1, pcm8k.length, IMBE_SAMPLE_RATE);
    const ch = buffer.getChannelData(0);
    for (let i = 0; i < pcm8k.length; i++) {
      ch[i] = (pcm8k[i] ?? 0) * this.volume;
    }
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(ctx.destination);
    const start = Math.max(ctx.currentTime + 0.01, this.nextTime);
    src.start(start);
    this.nextTime = start + buffer.duration;
  }

  reset(): void {
    this.nextTime = this.ctx?.currentTime ?? 0;
  }

  dispose(): void {
    void this.ctx?.close();
    this.ctx = null;
  }
}
