/** Continuous, overlapped power spectra from consecutive IQ, with filtered DDC. */
import { FFT } from "@jtarrio/signals/dsp/fft.js";
import { makeBlackmanWindow } from "@jtarrio/signals/dsp/coefficients.js";
import { getIqResampler, type IqResampler } from "@jtarrio/signals/dsp/resamplers.js";
import type { SampleBlock } from "@jtarrio/signals/radio/sample_block.js";

export type SpectrumFrameInfo = {
  sampleRate: number;
  centerOffsetHz: number;
  binHz: number;
  averagedFfts: number;
  sequence: number;
};

/** Raw FFT order; the sample clock and DDC offset are explicit metadata. */
export class StreamingSpectrum {
  private fft: FFT;
  private window: Float32Array;
  private inputI: Float32Array;
  private inputQ: Float32Array;
  private workI: Float32Array;
  private workQ: Float32Array;
  private sum: Float64Array;
  private history: Float32Array[] = [];
  private write = 0;
  private filled = 0;
  private sinceFft = 0;
  private historyHead = 0;
  private averageCount = 1;
  private inputRate = 1_024_000;
  private outputRate = 1_024_000;
  private centerOffset = 0;
  private requestedOffset = 0;
  private requestedSpan = Infinity;
  private phase = 0;
  private resampler: IqResampler | null = null;
  private mixedI = new Float32Array(0);
  private mixedQ = new Float32Array(0);
  private lastFrequency: number | undefined;
  private frames = 0;

  constructor(size = 8192, private averageSeconds = .16, private narrowOnly = false) {
    this.fft = FFT.ofLength(size);
    this.window = makeBlackmanWindow(this.fft.length);
    this.inputI = new Float32Array(this.fft.length);
    this.inputQ = new Float32Array(this.fft.length);
    this.workI = new Float32Array(this.fft.length);
    this.workQ = new Float32Array(this.fft.length);
    this.sum = new Float64Array(this.fft.length);
    this.configure(true);
  }

  get size(): number { return this.fft.length; }
  set size(size: number) {
    if (size === this.size) return;
    this.fft = FFT.ofLength(size);
    this.window = makeBlackmanWindow(this.fft.length);
    this.inputI = new Float32Array(this.size);
    this.inputQ = new Float32Array(this.size);
    this.workI = new Float32Array(this.size);
    this.workQ = new Float32Array(this.size);
    this.sum = new Float64Array(this.size);
    this.configure(true);
  }

  setSampleRate(rate: number): void {
    if (rate <= 0 || rate === this.inputRate) return;
    this.inputRate = rate;
    this.configure(true);
  }

  /** The visible span, not a request to retune the radio or change its clock. */
  setView(offsetHz: number, spanHz: number): void {
    this.requestedOffset = offsetHz;
    this.requestedSpan = Math.max(1, spanHz);
    this.configure();
  }

  frequency(): number | undefined { return this.lastFrequency; }
  isReady(): boolean { return this.history.length > 0; }
  get processedFfts(): number { return this.frames; }

  getFrameInfo(): SpectrumFrameInfo {
    return { sampleRate: this.outputRate, centerOffsetHz: this.centerOffset,
      binHz: this.outputRate / this.size, averagedFfts: this.history.length, sequence: this.frames };
  }

  private configure(force = false): void {
    const ratio = Math.max(1, 2 ** Math.floor(Math.log2(this.inputRate / (this.requestedSpan * 1.25))));
    const outputRate = this.inputRate / ratio;
    const limit = Math.max(0, (this.inputRate - outputRate) / 2);
    // Quantize DDC motion so a sub-pixel pan doesn't continually clear its FFT.
    const step = outputRate / 64;
    const center = ratio === 1 ? 0 : Math.max(-limit, Math.min(limit, Math.round(this.requestedOffset / step) * step));
    if (!force && outputRate === this.outputRate && center === this.centerOffset) return;
    this.outputRate = outputRate;
    this.centerOffset = center;
    this.resampler = ratio > 1 ? getIqResampler(this.inputRate, outputRate, {
      lowPassFrequency: outputRate * .45, taps: 63,
    }) : null;
    this.averageCount = Math.max(1, Math.min(48, Math.round(this.averageSeconds * outputRate / (this.size / 2))));
    this.resetHistory();
  }

  private resetHistory(): void {
    this.write = this.filled = this.sinceFft = this.historyHead = 0;
    this.phase = 0;
    this.history = [];
    this.sum.fill(0);
  }

  receiveSamples(block: SampleBlock): void {
    if (this.narrowOnly && this.outputRate === this.inputRate) return;
    if (this.lastFrequency !== block.frequency) {
      this.lastFrequency = block.frequency;
      this.configure(true); // never mix old tuning into a new frequency's history
    }
    let I = block.I, Q = block.Q;
    if (this.centerOffset !== 0) {
      if (this.mixedI.length !== I.length) {
        this.mixedI = new Float32Array(I.length);
        this.mixedQ = new Float32Array(I.length);
      }
      const delta = -2 * Math.PI * this.centerOffset / this.inputRate;
      // Oscillator recurrence avoids two trigonometric calls per input sample.
      const dc = Math.cos(delta), ds = Math.sin(delta);
      let c = Math.cos(this.phase), s = Math.sin(this.phase);
      for (let j = 0; j < I.length; j++) {
        this.mixedI[j] = I[j] * c - Q[j] * s;
        this.mixedQ[j] = I[j] * s + Q[j] * c;
        const next = c * dc - s * ds;
        s = s * dc + c * ds; c = next;
      }
      this.phase = (this.phase + delta * I.length) % (2 * Math.PI);
      I = this.mixedI; Q = this.mixedQ;
    }
    if (this.resampler) [I, Q] = this.resampler.resample(I, Q);
    for (let j = 0; j < I.length; j++) {
      this.inputI[this.write] = I[j]; this.inputQ[this.write] = Q[j];
      this.write = (this.write + 1) % this.size;
      if (this.filled < this.size) {
        this.filled++;
        if (this.filled === this.size) { this.compute(); this.sinceFft = 0; }
      } else if (++this.sinceFft >= this.size / 2) {
        this.compute(); this.sinceFft = 0;
      }
    }
  }

  private compute(): void {
    for (let j = 0; j < this.size; j++) {
      const source = (this.write + j) % this.size;
      this.workI[j] = this.inputI[source] * this.window[j];
      this.workQ[j] = this.inputQ[source] * this.window[j];
    }
    const [real, imag] = this.fft.transform(this.workI, this.workQ);
    let power: Float32Array;
    if (this.history.length < this.averageCount) {
      power = new Float32Array(this.size); this.history.push(power);
    } else {
      power = this.history[this.historyHead];
      for (let j = 0; j < this.size; j++) this.sum[j] -= power[j];
      this.historyHead = (this.historyHead + 1) % this.averageCount;
    }
    for (let j = 0; j < this.size; j++) {
      power[j] = real[j] ** 2 + imag[j] ** 2;
      this.sum[j] += power[j];
    }
    this.frames++;
  }

  getSpectrum(output: Float32Array): void {
    for (let j = 0; j < Math.min(output.length, this.size); j++) {
      output[j] = 10 * Math.log10(Math.max(this.sum[j] / Math.max(1, this.history.length), 1e-30));
    }
  }
}
