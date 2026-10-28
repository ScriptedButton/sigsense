/**
 * P25 Phase 1 C4FM demodulator: IQ → dibits @ 4800 baud.
 *
 * Uses FM discriminator on complex baseband, LPF, and early-late timing.
 */

import { P25_SYMBOL_RATE } from "./types";

const DIBIT_LEVELS = [-3, -1, +1, +3];

export type C4fmConfig = {
  sampleRate: number;
  symbolRate: number;
};

export class C4fmDemodulator {
  private sampleRate = 48_000;
  private samplesPerSymbol = 10;
  private prevI = 1;
  private prevQ = 0;
  private filt = 0;
  private alpha = 0.2;
  private timing = 0;
  private phase = 0;
  private gain = 1;
  private lockEma = 0;
  private buf: number[] = [];

  setSampleRate(rate: number): void {
    if (rate === this.sampleRate) return;
    this.sampleRate = rate;
    this.samplesPerSymbol = Math.max(2, rate / P25_SYMBOL_RATE);
    this.alpha = Math.min(0.5, (2 * Math.PI * 5_200) / rate);
    this.reset();
  }

  reset(): void {
    this.prevI = 1;
    this.prevQ = 0;
    this.filt = 0;
    this.timing = 0;
    this.phase = 0;
    this.gain = 1;
    this.lockEma = 0;
    this.buf = [];
  }

  getLock(): number {
    return this.lockEma;
  }

  /**
   * Demodulate complex baseband to dibits (0..3).
   * I/Q are interleaved or parallel arrays of the same length.
   */
  push(I: Float32Array, Q: Float32Array): number[] {
    const out: number[] = [];
    const sps = this.samplesPerSymbol;
    const a = this.alpha;

    for (let i = 0; i < I.length; i++) {
      const i0 = I[i]!;
      const q0 = Q[i]!;
      // Discriminator: Im(conj(prev)*curr)
      const disc = this.prevI * q0 - this.prevQ * i0;
      const mag2 = this.prevI * this.prevI + this.prevQ * this.prevQ + 1e-12;
      const raw = disc / mag2;
      this.prevI = i0;
      this.prevQ = q0;

      this.filt += a * (raw - this.filt);
      this.buf.push(this.filt);

      this.timing += 1;
      if (this.timing >= sps) {
        this.timing -= sps;
        // Sample near center of symbol window
        const idx = Math.max(0, this.buf.length - Math.floor(sps / 2) - 1);
        const sample = this.buf[idx] ?? this.filt;
        // Keep a short buffer
        if (this.buf.length > sps * 4) this.buf = this.buf.slice(-Math.ceil(sps * 2));

        const abs = Math.abs(sample);
        this.gain = this.gain * 0.995 + abs * 0.005;
        const norm = sample / (this.gain / 2 + 1e-9);

        let best = 0;
        let bestDist = Infinity;
        for (let d = 0; d < 4; d++) {
          const dist = Math.abs(norm - DIBIT_LEVELS[d]!);
          if (dist < bestDist) {
            bestDist = dist;
            best = d;
          }
        }
        // Map constellation: -3→3, -1→2, +1→0, +3→1 (C4FM / P25 common mapping)
        // WaveCap/SDRTrunk: +3→1, +1→0, -1→2, -3→3
        const dibit =
          best === 0 ? 3 : best === 1 ? 2 : best === 2 ? 0 : 1;
        out.push(dibit);

        const ideal = DIBIT_LEVELS[best]!;
        const err = Math.min(1, bestDist / 2);
        const quality = 1 - err;
        this.lockEma = this.lockEma * 0.95 + quality * 0.05;
      }
    }
    return out;
  }

  /** Convert ideal soft levels (±1/±3) already at 1 sample/symbol into dibits. */
  pushSoftSymbols(symbols: ArrayLike<number>): number[] {
    const out: number[] = [];
    for (let i = 0; i < symbols.length; i++) {
      const s = symbols[i]!;
      let best = 0;
      let bestDist = Infinity;
      for (let d = 0; d < 4; d++) {
        const dist = Math.abs(s - DIBIT_LEVELS[d]!);
        if (dist < bestDist) {
          bestDist = dist;
          best = d;
        }
      }
      const dibit = best === 0 ? 3 : best === 1 ? 2 : best === 2 ? 0 : 1;
      out.push(dibit);
      this.lockEma = this.lockEma * 0.9 + 0.1;
    }
    return out;
  }
}

/** Map dibit → C4FM deviation symbol for modulators / demos. */
export function dibitToSymbol(dibit: number): number {
  switch (dibit & 3) {
    case 0:
      return +1;
    case 1:
      return +3;
    case 2:
      return -1;
    default:
      return -3;
  }
}

export function symbolToDibit(symbol: number): number {
  let best = 0;
  let bestDist = Infinity;
  for (let d = 0; d < 4; d++) {
    const dist = Math.abs(symbol - DIBIT_LEVELS[d]!);
    if (dist < bestDist) {
      bestDist = dist;
      best = d;
    }
  }
  return best === 0 ? 3 : best === 1 ? 2 : best === 2 ? 0 : 1;
}
