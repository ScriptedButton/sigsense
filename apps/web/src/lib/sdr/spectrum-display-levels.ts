/** Stable display levels relative to the receiver noise, independent of RF gain. */
export class SpectrumDisplayLevels {
  private floor: number | null = null;
  private scratch = new Float32Array(2048);

  reset(): void { this.floor = null; }

  update(spectrum: Float32Array): { minDb: number; maxDb: number } {
    const stride = Math.max(1, Math.ceil(spectrum.length / this.scratch.length));
    let count = 0;
    for (let i = 0; i < spectrum.length; i += stride) {
      if (Number.isFinite(spectrum[i])) this.scratch[count++] = spectrum[i];
    }
    if (!count) return { minDb: -100, maxDb: -68 };
    const sorted = this.scratch.subarray(0, count).sort();
    const measured = sorted[Math.floor(count * .35)];
    // Follow ordinary noise changes gently; settle immediately after a gain or
    // source change so the screen does not stay washed out for several seconds.
    this.floor = this.floor == null || Math.abs(measured - this.floor) > 12
      ? measured : this.floor + .06 * (measured - this.floor);
    const minDb = this.floor + 2;
    const upper = sorted[Math.min(count - 1, Math.floor(count * .98))] + 6;
    return { minDb, maxDb: minDb + Math.max(32, Math.min(52, upper - minDb)) };
  }
}

/** Average consecutive periodograms in linear power, rather than retaining noise peaks. */
export class SpectrumRowAverage {
  private power = new Float64Array(0);
  private output = new Float32Array(0);
  private count = 0;

  reset(): void { this.count = 0; this.power.fill(0); }

  add(spectrum: Float32Array): void {
    if (this.power.length !== spectrum.length) {
      this.power = new Float64Array(spectrum.length);
      this.output = new Float32Array(spectrum.length);
      this.count = 0;
    }
    for (let i = 0; i < spectrum.length; i++) {
      const db = Number.isFinite(spectrum[i]) ? spectrum[i] : -200;
      this.power[i] += 10 ** (Math.max(-200, Math.min(100, db)) / 10);
    }
    this.count++;
  }

  take(): Float32Array {
    for (let i = 0; i < this.power.length; i++) {
      this.output[i] = 10 * Math.log10(this.power[i] / Math.max(1, this.count) + 1e-30);
    }
    this.reset();
    return this.output;
  }
}
