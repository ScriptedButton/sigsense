import {
  decodeBaudotChar,
  type BaudotShift,
} from "./baudot";
import type { FskConfig } from "./types";

export type FskDecodeResult = {
  chars: string;
  /** Soft lock quality 0…1 from recent mark/space separation. */
  lock: number;
};

type FrameState = "idle" | "char";

/**
 * Streaming async FSK / RTTY decoder.
 * Consumes mono PCM from the demod tap and emits characters.
 */
export class FskDecoder {
  private config: FskConfig;
  private sampleRate = 48_000;
  private shift: BaudotShift = "ltrs";

  // Soft bit path
  private filtered = 0;
  private softBit = 1; // mark idle
  private prevSoftBit = 1;
  private lockEma = 0;
  /** Running peak of |filtered| for AGC (narrow shifts look tiny under wide NBFM). */
  private peak = 0.05;

  // Goertzel accumulators (tones mode)
  private goertzelN = 0;
  private markS1 = 0;
  private markS2 = 0;
  private spaceS1 = 0;
  private spaceS2 = 0;
  private markCoeff = 0;
  private spaceCoeff = 0;
  private goertzelBlock = 0;

  // Frame timing
  private frame: FrameState = "idle";
  private samplesUntilSample = 0;
  private bitIndex = 0;
  private dataBits = 0;
  private dataBitCount = 5;
  private samplesPerBit = 1;
  private spaceRun = 0;

  constructor(config: FskConfig) {
    this.config = config;
    this.applyConfig(config);
  }

  setConfig(config: FskConfig): void {
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
    this.shift = "ltrs";
    this.filtered = 0;
    this.softBit = 1;
    this.prevSoftBit = 1;
    this.lockEma = 0;
    this.peak = 0.05;
    this.frame = "idle";
    this.samplesUntilSample = 0;
    this.bitIndex = 0;
    this.dataBits = 0;
    this.spaceRun = 0;
    this.resetGoertzel();
  }

  push(mono: Float32Array, sampleRate: number): FskDecodeResult {
    this.setSampleRate(sampleRate);
    let out = "";
    for (let i = 0; i < mono.length; i++) {
      const bit = this.detectBit(mono[i]!);
      const ch = this.advanceFrame(bit);
      if (ch) out += ch;
    }
    return { chars: out, lock: this.lockEma };
  }

  private applyConfig(config: FskConfig): void {
    this.samplesPerBit = Math.max(1, this.sampleRate / config.baud);
    this.dataBitCount = config.encoding === "baudot" ? 5 : 8;
    this.goertzelBlock = Math.max(
      8,
      Math.round(this.sampleRate / Math.max(config.baud * 2, 1)),
    );
    const markK =
      (2 * Math.PI * config.markHz) / this.sampleRate;
    const spaceK =
      (2 * Math.PI * config.spaceHz) / this.sampleRate;
    this.markCoeff = 2 * Math.cos(markK);
    this.spaceCoeff = 2 * Math.cos(spaceK);
    this.resetGoertzel();
  }

  private resetGoertzel(): void {
    this.goertzelN = 0;
    this.markS1 = 0;
    this.markS2 = 0;
    this.spaceS1 = 0;
    this.spaceS2 = 0;
  }

  /** Returns hard bit: 1 = mark, 0 = space. */
  private detectBit(sample: number): number {
    if (this.config.detector === "tones") {
      return this.detectToneBit(sample);
    }
    return this.detectDiscriminatorBit(sample);
  }

  private detectDiscriminatorBit(sample: number): number {
    // One-pole LPF ~ 1.5× baud to reject audio hiss.
    const alpha = Math.min(
      0.5,
      (2 * Math.PI * this.config.baud * 1.5) / this.sampleRate,
    );
    this.filtered += alpha * (sample - this.filtered);

    const abs = Math.abs(this.filtered);
    this.peak = Math.max(abs, this.peak * 0.9997);
    const norm = this.filtered / Math.max(this.peak, 1e-4);

    const level = this.config.inverted ? -norm : norm;
    // Hysteresis on normalized ±1 scale.
    const thresh = 0.15;
    if (level > thresh) this.softBit = 1;
    else if (level < -thresh) this.softBit = 0;

    const separation = Math.min(1, abs / Math.max(this.peak, 1e-4));
    this.lockEma = this.lockEma * 0.995 + separation * 0.005;
    return this.softBit;
  }

  private detectToneBit(sample: number): number {
    const s0m = sample + this.markCoeff * this.markS1 - this.markS2;
    this.markS2 = this.markS1;
    this.markS1 = s0m;

    const s0s = sample + this.spaceCoeff * this.spaceS1 - this.spaceS2;
    this.spaceS2 = this.spaceS1;
    this.spaceS1 = s0s;

    this.goertzelN++;
    if (this.goertzelN < this.goertzelBlock) return this.softBit;

    const markPower =
      this.markS1 * this.markS1 +
      this.markS2 * this.markS2 -
      this.markCoeff * this.markS1 * this.markS2;
    const spacePower =
      this.spaceS1 * this.spaceS1 +
      this.spaceS2 * this.spaceS2 -
      this.spaceCoeff * this.spaceS1 * this.spaceS2;

    this.resetGoertzel();

    let mark = markPower;
    let space = spacePower;
    if (this.config.inverted) {
      const tmp = mark;
      mark = space;
      space = tmp;
    }

    const total = mark + space + 1e-12;
    const score = (mark - space) / total;
    if (score > 0.08) this.softBit = 1;
    else if (score < -0.08) this.softBit = 0;

    this.lockEma = this.lockEma * 0.97 + Math.min(1, Math.abs(score)) * 0.03;
    return this.softBit;
  }

  private advanceFrame(bit: number): string | null {
    const prev = this.prevSoftBit;
    this.prevSoftBit = bit;

    if (this.frame === "idle") {
      // Start bit: mark → space edge while idle on mark.
      if (prev === 1 && bit === 0) {
        this.spaceRun = 1;
      } else if (bit === 0) {
        this.spaceRun++;
      } else {
        this.spaceRun = 0;
      }

      // Confirm start after ~0.5 bit of space, then sample mid-bit thereafter.
      if (this.spaceRun >= this.samplesPerBit * 0.45) {
        this.frame = "char";
        this.bitIndex = 0;
        this.dataBits = 0;
        // Next sample at center of first data bit:
        // remaining half of start + one full bit to first data mid.
        this.samplesUntilSample = this.samplesPerBit * 1.05;
        this.spaceRun = 0;
      }
      return null;
    }

    // In character
    this.samplesUntilSample -= 1;
    if (this.samplesUntilSample > 0) return null;

    if (this.bitIndex < this.dataBitCount) {
      this.dataBits |= bit << this.bitIndex;
      this.bitIndex++;
      this.samplesUntilSample = this.samplesPerBit;
      return null;
    }

    // Stop bit(s): expect mark; resync on failure.
    const stopOk = bit === 1;
    this.frame = "idle";
    this.samplesUntilSample = 0;
    this.spaceRun = 0;

    if (!stopOk) return null;
    return this.decodeFrame(this.dataBits);
  }

  private decodeFrame(bits: number): string | null {
    if (this.config.encoding === "baudot") {
      const { char, shift } = decodeBaudotChar(bits, this.shift);
      this.shift = shift;
      if (char === "\r") return null;
      if (char === "\n") return "\n";
      return char;
    }

    // ASCII 8N1 — printable + newline/tab.
    const code = bits & 0xff;
    if (code === 10 || code === 13) return "\n";
    if (code === 9) return "\t";
    if (code >= 32 && code <= 126) return String.fromCharCode(code);
    return null;
  }
}
