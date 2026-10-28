/**
 * IQ SampleReceiver that DDCs to the demod offset and feeds P25 C4FM.
 */

import type { SampleBlock } from "@jtarrio/signals/radio/sample_block.js";
import type { SampleReceiver } from "@jtarrio/signals/radio/sample_receiver.js";

import { C4fmDemodulator } from "./c4fm";
import { P25_BASEBAND_RATE } from "./types";

export type P25DibitSink = (dibits: number[], lock: number) => void;

/**
 * Downsconverts full-rate IQ to ~48 kHz complex baseband around `offsetHz`,
 * then C4FM-demodulates to dibits.
 */
export class P25IqReceiver implements SampleReceiver {
  private sampleRate = 1_024_000;
  private offsetHz = 0;
  private enabled = false;
  private phase = 0;
  private decimAccumI = 0;
  private decimAccumQ = 0;
  private decimCount = 0;
  private decimFactor = 1;
  private readonly c4fm = new C4fmDemodulator();
  private sink: P25DibitSink | null = null;
  private bbI = new Float32Array(4096);
  private bbQ = new Float32Array(4096);
  private bbLen = 0;

  setSink(sink: P25DibitSink | null): void {
    this.sink = sink;
  }

  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
    if (!enabled) this.reset();
  }

  isEnabled(): boolean {
    return this.enabled;
  }

  setOffsetHz(hz: number): void {
    this.offsetHz = hz;
  }

  setSampleRate(sampleRate: number): void {
    this.sampleRate = sampleRate;
    this.decimFactor = Math.max(1, Math.round(sampleRate / P25_BASEBAND_RATE));
    this.c4fm.setSampleRate(sampleRate / this.decimFactor);
    this.reset();
  }

  reset(): void {
    this.phase = 0;
    this.decimAccumI = 0;
    this.decimAccumQ = 0;
    this.decimCount = 0;
    this.bbLen = 0;
    this.c4fm.reset();
  }

  receiveSamples(block: SampleBlock): void {
    if (!this.enabled || !this.sink) return;
    const { I, Q } = block;
    const n = I.length;
    const lo = (-2 * Math.PI * this.offsetHz) / this.sampleRate;
    const factor = this.decimFactor;

    for (let i = 0; i < n; i++) {
      const c = Math.cos(this.phase);
      const s = Math.sin(this.phase);
      this.phase += lo;
      if (this.phase > Math.PI) this.phase -= 2 * Math.PI;
      else if (this.phase < -Math.PI) this.phase += 2 * Math.PI;

      const ii = I[i]!;
      const qq = Q[i]!;
      // Mix
      const mi = ii * c - qq * s;
      const mq = ii * s + qq * c;

      this.decimAccumI += mi;
      this.decimAccumQ += mq;
      this.decimCount++;
      if (this.decimCount < factor) continue;

      if (this.bbLen >= this.bbI.length) {
        this.flushBb();
      }
      this.bbI[this.bbLen] = this.decimAccumI / factor;
      this.bbQ[this.bbLen] = this.decimAccumQ / factor;
      this.bbLen++;
      this.decimAccumI = 0;
      this.decimAccumQ = 0;
      this.decimCount = 0;
    }
    this.flushBb();
  }

  private flushBb(): void {
    if (this.bbLen === 0 || !this.sink) return;
    const I = this.bbI.subarray(0, this.bbLen);
    const Q = this.bbQ.subarray(0, this.bbLen);
    const dibits = this.c4fm.push(I, Q);
    this.bbLen = 0;
    if (dibits.length) this.sink(dibits, this.c4fm.getLock());
  }

  /** Direct dibit injection for smoke / offline tests. */
  pushDibits(dibits: number[], lock = 1): void {
    this.sink?.(dibits, lock);
  }
}
