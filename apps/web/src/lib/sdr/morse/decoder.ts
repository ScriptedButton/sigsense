import { decodeMorseElements } from "./alphabet";
import { ditSeconds, type MorseConfig } from "./types";

export type MorseDecodeResult = {
  chars: string;
  /** Estimated WPM from recent dit timing. */
  wpmEstimate: number;
  /** Soft lock quality 0…1. */
  lock: number;
};

/**
 * Streaming Morse (CW) decoder.
 * Consumes mono PCM from the demod tap and emits characters.
 */
export class MorseDecoder {
  private config: MorseConfig;
  private sampleRate = 48_000;

  // Tone / envelope detection
  private goertzelN = 0;
  private goertzelBlock = 0;
  private s1 = 0;
  private s2 = 0;
  private coeff = 0;
  private envelope = 0;
  private peak = 0.02;
  private noise = 0.005;
  private keyed = false;
  private lockEma = 0;

  // Element timing (samples of current mark or space run)
  private runSamples = 0;
  private ditSamples = 0;
  private elements = "";
  private wpmEstimate = 20;
  /** Whether the current space has already emitted a character gap. */
  private spaceFlushedChar = false;
  /** Whether the current space has already emitted a word gap. */
  private spaceFlushedWord = false;

  constructor(config: MorseConfig) {
    this.config = config;
    this.wpmEstimate = config.wpm;
    this.applyConfig(config);
  }

  setConfig(config: MorseConfig): void {
    this.config = config;
    this.applyConfig(config);
    this.reset();
  }

  setSampleRate(rate: number): void {
    if (rate === this.sampleRate) return;
    this.sampleRate = rate;
    this.applyConfig(this.config);
    this.reset();
  }

  reset(): void {
    this.goertzelN = 0;
    this.s1 = 0;
    this.s2 = 0;
    this.envelope = 0;
    this.peak = 0.02;
    this.noise = 0.005;
    this.keyed = false;
    this.lockEma = 0;
    this.runSamples = 0;
    this.elements = "";
    this.spaceFlushedChar = false;
    this.spaceFlushedWord = false;
    this.prevOn = null;
    this.wpmEstimate = this.config.wpm;
    this.ditSamples = ditSeconds(this.config.wpm) * this.sampleRate;
  }

  push(mono: Float32Array, sampleRate: number): MorseDecodeResult {
    this.setSampleRate(sampleRate);
    let out = "";

    for (let i = 0; i < mono.length; i++) {
      const power = this.detectPower(mono[i]!);
      if (power == null) continue;

      const on = this.decideKeyed(power);
      const ch = this.step(on);
      if (ch) out += ch;
    }

    return {
      chars: out,
      wpmEstimate: this.wpmEstimate,
      lock: this.lockEma,
    };
  }

  private applyConfig(config: MorseConfig): void {
    // ~8 ms blocks — short enough for 40 WPM dits (~30 ms).
    this.goertzelBlock = Math.max(32, Math.round(this.sampleRate * 0.008));
    const k = (2 * Math.PI * config.toneHz) / this.sampleRate;
    this.coeff = 2 * Math.cos(k);
    this.ditSamples = ditSeconds(config.wpm) * this.sampleRate;
    this.wpmEstimate = config.wpm;
    this.s1 = 0;
    this.s2 = 0;
    this.goertzelN = 0;
  }

  /**
   * Returns instantaneous keyed-signal power once per analysis block,
   * or null while still accumulating.
   */
  private detectPower(sample: number): number | null {
    if (this.config.detector === "envelope") {
      const abs = Math.abs(sample);
      const alpha = Math.min(0.5, (2 * Math.PI * 80) / this.sampleRate);
      this.envelope += alpha * (abs - this.envelope);
      this.goertzelN++;
      if (this.goertzelN < this.goertzelBlock) return null;
      this.goertzelN = 0;
      return this.envelope;
    }

    const s0 = sample + this.coeff * this.s1 - this.s2;
    this.s2 = this.s1;
    this.s1 = s0;
    this.goertzelN++;
    if (this.goertzelN < this.goertzelBlock) return null;

    const power =
      this.s1 * this.s1 + this.s2 * this.s2 - this.coeff * this.s1 * this.s2;
    this.s1 = 0;
    this.s2 = 0;
    this.goertzelN = 0;
    return Math.sqrt(Math.max(0, power) / this.goertzelBlock);
  }

  private decideKeyed(power: number): boolean {
    this.peak = Math.max(power, this.peak * 0.9995);
    if (!this.keyed) {
      this.noise = this.noise * 0.995 + power * 0.005;
    }

    const floor = Math.max(this.noise * 2, this.peak * 0.08, 1e-5);
    const span = Math.max(this.peak - floor, 1e-5);
    const openAt = floor + span * (0.55 - this.config.sensitivity * 0.35);
    const closeAt = floor + span * (0.35 - this.config.sensitivity * 0.2);

    if (!this.keyed && power > openAt) this.keyed = true;
    else if (this.keyed && power < closeAt) this.keyed = false;

    const separation = Math.min(
      1,
      (this.peak - this.noise) / Math.max(this.peak, 1e-5),
    );
    this.lockEma = this.lockEma * 0.99 + separation * 0.01;
    return this.keyed;
  }

  private prevOn: boolean | null = null;

  private step(on: boolean): string | null {
    if (this.prevOn === null) {
      this.prevOn = on;
      this.runSamples = this.goertzelBlock;
      return null;
    }

    if (on === this.prevOn) {
      this.runSamples += this.goertzelBlock;
      // While holding a space, flush character / word gaps by duration.
      if (!on) return this.maybeFlushSpace();
      return null;
    }

    // Transition — finalize the run that just ended.
    const duration = this.runSamples;
    const wasOn = this.prevOn;
    this.prevOn = on;
    this.runSamples = this.goertzelBlock;

    if (wasOn) {
      this.endMark(duration);
      this.spaceFlushedChar = false;
      this.spaceFlushedWord = false;
      return null;
    }

    // Space → mark: any pending flush already happened during the space.
    this.spaceFlushedChar = false;
    this.spaceFlushedWord = false;
    return null;
  }

  private endMark(samples: number): void {
    const dit = this.ditSamples;
    if (samples < dit * 2) {
      this.elements += ".";
      if (this.config.autoSpeed) this.updateDit(samples);
    } else {
      this.elements += "-";
      if (this.config.autoSpeed && samples > dit * 2.2 && samples < dit * 4.5) {
        this.updateDit(samples / 3);
      }
    }
  }

  private maybeFlushSpace(): string | null {
    const dit = this.ditSamples;
    let out = "";

    if (!this.spaceFlushedChar && this.runSamples >= dit * 2) {
      this.spaceFlushedChar = true;
      const ch = this.flushElements();
      if (ch) out += ch;
    }

    if (!this.spaceFlushedWord && this.runSamples >= dit * 5) {
      this.spaceFlushedWord = true;
      out += " ";
    }

    return out || null;
  }

  private flushElements(): string | null {
    if (!this.elements) return null;
    const decoded = decodeMorseElements(this.elements);
    this.elements = "";
    return decoded;
  }

  private updateDit(samples: number): void {
    const minDit = ditSeconds(40) * this.sampleRate;
    const maxDit = ditSeconds(5) * this.sampleRate;
    const clamped = Math.min(maxDit, Math.max(minDit, samples));
    this.ditSamples = this.ditSamples * 0.85 + clamped * 0.15;
    this.wpmEstimate = 1.2 / (this.ditSamples / this.sampleRate);
  }
}
