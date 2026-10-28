import {
  WEFAX_APT_START_IOC288_HZ,
  WEFAX_APT_START_IOC576_HZ,
  WEFAX_APT_STOP_HZ,
  WEFAX_MAX_LINES,
  wefaxLineSeconds,
  wefaxLineWidth,
  type WefaxConfig,
  type WefaxIoc,
  type WefaxStatus,
} from "./types";

export type WefaxDecodeResult = {
  status: WefaxStatus;
  lines: number;
  /** True when at least one full line was appended this push. */
  advanced: boolean;
  toneHz: number;
  lock: number;
  /** IOC may flip after APT start detection. */
  ioc: WefaxIoc;
};

type Goertzel = { s1: number; s2: number; coeff: number };

/**
 * Streaming HF weatherfax (radiofax) decoder.
 * Recovers gray levels from USB/LSB AFSK (1500–2300 Hz) via quadrature FM demod.
 */
export class WefaxDecoder {
  private config: WefaxConfig;
  private sampleRate = 48_000;
  private status: WefaxStatus = "listening";

  // Quadrature FM demod state
  private ncoPhase = 0;
  private iLp = 0;
  private qLp = 0;
  private prevI = 0;
  private prevQ = 0;
  private toneEma = 1900;
  private lockEma = 0;

  // Goertzel APT detectors on demodulated gray level (B/W FSK rate)
  private aptN = 0;
  private aptBlock = 0;
  private g300: Goertzel = { s1: 0, s2: 0, coeff: 0 };
  private g450: Goertzel = { s1: 0, s2: 0, coeff: 0 };
  private g675: Goertzel = { s1: 0, s2: 0, coeff: 0 };
  private aptLevelDc = 0.5;
  private aptToneMs = 0;
  private aptCandidate: WefaxIoc | null = null;
  private stopToneMs = 0;

  // Raster timing
  private width = wefaxLineWidth(576);
  private samplesPerLine = 1;
  private sampleInLine = 0;
  private phaseOffset = 0;
  private pixels: Uint8Array = new Uint8Array(0);
  private hit: Uint8Array = new Uint8Array(0);
  private lines = 0;

  // Phasing: histogram of white-edge positions
  private phaseHist: Float32Array = new Float32Array(0);
  private phaseLines = 0;
  private lastLevel = 0;
  private ioc: WefaxIoc = 576;

  constructor(config: WefaxConfig) {
    this.config = config;
    this.ioc = config.ioc;
    this.applyGeometry();
  }

  setConfig(config: WefaxConfig): void {
    const iocChanged = config.ioc !== this.config.ioc;
    const lpmChanged = config.lpm !== this.config.lpm;
    const slantChanged = config.slantPct !== this.config.slantPct;
    this.config = config;
    if (this.status === "listening" || this.status === "idle") {
      this.ioc = config.ioc;
    }
    if (iocChanged || lpmChanged || slantChanged) {
      this.applyGeometry();
    } else {
      this.updateAptCoeffs();
    }
  }

  setSampleRate(rate: number): void {
    if (rate === this.sampleRate) return;
    this.sampleRate = rate;
    this.applyGeometry();
  }

  getPixels(): Uint8Array {
    return this.pixels;
  }

  getWidth(): number {
    return this.width;
  }

  getLines(): number {
    return this.lines;
  }

  getIoc(): WefaxIoc {
    return this.ioc;
  }

  getStatus(): WefaxStatus {
    return this.status;
  }

  /** Jump straight into imaging (manual sync / mid-chart tune). */
  forceStart(): void {
    this.ioc = this.config.ioc;
    this.applyGeometry();
    this.clearImage();
    this.sampleInLine = 0;
    this.phaseOffset = 0;
    this.status = "imaging";
    this.aptToneMs = 0;
    this.stopToneMs = 0;
  }

  reset(): void {
    this.status = "listening";
    this.ncoPhase = 0;
    this.iLp = 0;
    this.qLp = 0;
    this.prevI = 0;
    this.prevQ = 0;
    this.toneEma = (this.config.blackHz + this.config.whiteHz) / 2;
    this.lockEma = 0;
    this.sampleInLine = 0;
    this.phaseOffset = 0;
    this.phaseLines = 0;
    this.lastLevel = 0;
    this.ioc = this.config.ioc;
    this.applyGeometry();
    this.clearImage();
    this.resetAptAcc();
  }

  clearImage(): void {
    this.pixels = new Uint8Array(this.width * WEFAX_MAX_LINES);
    this.hit = new Uint8Array(this.width * WEFAX_MAX_LINES);
    this.lines = 0;
  }

  push(mono: Float32Array, sampleRate: number): WefaxDecodeResult {
    this.setSampleRate(sampleRate);
    const linesBefore = this.lines;
    const centerHz = (this.config.blackHz + this.config.whiteHz) / 2;

    for (let i = 0; i < mono.length; i++) {
      const sample = mono[i]!;
      const { freqHz, amp } = this.demodSample(sample, centerHz);
      this.toneEma = this.toneEma * 0.995 + freqHz * 0.005;

      const level = this.freqToLevel(freqHz);
      const halfSpan = Math.max(1, (this.config.whiteHz - this.config.blackHz) / 2);
      const sep = Math.min(1, Math.abs(freqHz - centerHz) / halfSpan);
      this.lockEma = this.lockEma * 0.997 + sep * Math.min(1, amp * 8) * 0.003;

      if (this.config.sync === "auto") {
        this.observeApt(level, amp);
      }

      if (this.status === "phasing") {
        this.observePhasing(level);
      } else if (this.status === "imaging") {
        this.writePixel(level);
      }
    }

    return {
      status: this.status,
      lines: this.lines,
      advanced: this.lines > linesBefore,
      toneHz: this.toneEma,
      lock: Math.min(1, this.lockEma),
      ioc: this.ioc,
    };
  }

  private applyGeometry(): void {
    this.width = wefaxLineWidth(this.ioc);
    const lineSec = wefaxLineSeconds(this.config.lpm);
    const slant = 1 + this.config.slantPct / 100;
    this.samplesPerLine = Math.max(1, this.sampleRate * lineSec * slant);
    this.phaseHist = new Float32Array(this.width);
    this.aptBlock = Math.max(64, Math.round(this.sampleRate / 25));
    this.phaseOffset =
      ((this.phaseOffset % this.samplesPerLine) + this.samplesPerLine) %
      this.samplesPerLine;
    this.updateAptCoeffs();

    if (this.pixels.length !== this.width * WEFAX_MAX_LINES) {
      const next = new Uint8Array(this.width * WEFAX_MAX_LINES);
      const nextHit = new Uint8Array(this.width * WEFAX_MAX_LINES);
      const copyLines = Math.min(this.lines, WEFAX_MAX_LINES);
      if (
        this.pixels.length >= copyLines * this.width &&
        copyLines > 0 &&
        this.pixels.length / Math.max(1, this.lines) === this.width
      ) {
        next.set(this.pixels.subarray(0, copyLines * this.width));
        nextHit.set(this.hit.subarray(0, copyLines * this.width));
      } else {
        this.lines = 0;
      }
      this.pixels = next;
      this.hit = nextHit;
    }
  }

  private updateAptCoeffs(): void {
    this.g300.coeff = 2 * Math.cos((2 * Math.PI * WEFAX_APT_START_IOC576_HZ) / this.sampleRate);
    this.g450.coeff = 2 * Math.cos((2 * Math.PI * WEFAX_APT_STOP_HZ) / this.sampleRate);
    this.g675.coeff = 2 * Math.cos((2 * Math.PI * WEFAX_APT_START_IOC288_HZ) / this.sampleRate);
  }

  private resetAptAcc(): void {
    this.aptN = 0;
    this.aptToneMs = 0;
    this.aptCandidate = null;
    this.stopToneMs = 0;
    this.aptLevelDc = 0.5;
    this.g300.s1 = 0;
    this.g300.s2 = 0;
    this.g450.s1 = 0;
    this.g450.s2 = 0;
    this.g675.s1 = 0;
    this.g675.s2 = 0;
  }

  private demodSample(
    sample: number,
    centerHz: number,
  ): { freqHz: number; amp: number } {
    const w = (2 * Math.PI * centerHz) / this.sampleRate;
    this.ncoPhase += w;
    if (this.ncoPhase > Math.PI * 2) this.ncoPhase -= Math.PI * 2;

    const mixI = sample * Math.cos(this.ncoPhase);
    const mixQ = sample * -Math.sin(this.ncoPhase);

    // ~600 Hz LPF — keeps fax FM while rejecting hiss.
    const alpha = Math.min(0.5, (2 * Math.PI * 600) / this.sampleRate);
    this.iLp += alpha * (mixI - this.iLp);
    this.qLp += alpha * (mixQ - this.qLp);

    const dot = this.prevI * this.iLp + this.prevQ * this.qLp;
    const cross = this.prevI * this.qLp - this.prevQ * this.iLp;
    this.prevI = this.iLp;
    this.prevQ = this.qLp;

    const mag2 = this.iLp * this.iLp + this.qLp * this.qLp;
    const amp = Math.sqrt(mag2);
    if (mag2 < 1e-12) {
      return { freqHz: this.toneEma, amp: 0 };
    }
    const dPhi = Math.atan2(cross, dot);
    const offsetHz = (dPhi * this.sampleRate) / (2 * Math.PI);
    return { freqHz: centerHz + offsetHz, amp };
  }

  private freqToLevel(freqHz: number): number {
    const span = this.config.whiteHz - this.config.blackHz;
    let level = (freqHz - this.config.blackHz) / Math.max(1, span);
    if (this.config.inverted) level = 1 - level;
    return clamp01(level);
  }

  /**
   * ITU APT is black/white FSK alternating at 300/675/450 Hz — not a baseband
   * sine. Detect that modulation rate on the AC-coupled gray level.
   */
  private observeApt(level: number, amp: number): void {
    this.aptLevelDc = this.aptLevelDc * 0.995 + level * 0.005;
    const ac = level - this.aptLevelDc;
    goertzelAcc(this.g300, ac);
    goertzelAcc(this.g450, ac);
    goertzelAcc(this.g675, ac);
    this.aptN += 1;

    if (this.aptN < this.aptBlock) return;

    const p300 = goertzelPower(this.g300);
    const p450 = goertzelPower(this.g450);
    const p675 = goertzelPower(this.g675);
    const blockMs = (this.aptBlock / this.sampleRate) * 1000;
    // Level AC is ~±0.5; power scales ~N². Reject hiss / no-carrier.
    const floor = this.aptBlock * this.aptBlock * 0.004;
    const hasCarrier = amp > 0.02;

    if (this.status === "listening" || this.status === "starting") {
      const ioc576 =
        hasCarrier && p300 > floor && p300 > p450 * 2.5 && p300 > p675 * 2.5;
      const ioc288 =
        hasCarrier && p675 > floor && p675 > p450 * 2.5 && p675 > p300 * 2.5;
      if (ioc576 || ioc288) {
        const cand: WefaxIoc = ioc576 ? 576 : 288;
        if (this.aptCandidate !== cand) {
          this.aptCandidate = cand;
          this.aptToneMs = 0;
        }
        this.aptToneMs += blockMs;
        this.status = "starting";
        if (this.aptToneMs >= 2_500) {
          this.beginPhasing(cand);
        }
      } else {
        this.aptToneMs = Math.max(0, this.aptToneMs - blockMs * 0.5);
        if (this.aptToneMs < 200) {
          this.aptCandidate = null;
          if (this.status === "starting") this.status = "listening";
        }
      }
    }

    if (
      this.status === "imaging" ||
      this.status === "phasing" ||
      this.status === "stopped"
    ) {
      const stop =
        hasCarrier && p450 > floor && p450 > p300 * 2.5 && p450 > p675 * 2.5;
      if (stop) {
        this.stopToneMs += blockMs;
        if (this.stopToneMs >= 2_000) {
          this.status = "stopped";
        }
      } else {
        this.stopToneMs = Math.max(0, this.stopToneMs - blockMs * 0.5);
        // Re-arm once the stop FSK ends so the next chart can lock.
        if (this.status === "stopped" && this.stopToneMs < 400) {
          this.status = "listening";
          this.aptToneMs = 0;
          this.aptCandidate = null;
          this.stopToneMs = 0;
        }
      }
    }

    this.aptN = 0;
    this.g300.s1 = 0;
    this.g300.s2 = 0;
    this.g450.s1 = 0;
    this.g450.s2 = 0;
    this.g675.s1 = 0;
    this.g675.s2 = 0;
  }

  private beginPhasing(ioc: WefaxIoc): void {
    this.ioc = ioc;
    this.applyGeometry();
    this.clearImage();
    this.phaseHist.fill(0);
    this.phaseLines = 0;
    this.sampleInLine = 0;
    this.phaseOffset = 0;
    this.status = "phasing";
    this.aptToneMs = 0;
    this.stopToneMs = 0;
  }

  private observePhasing(level: number): void {
    if (level > 0.7 && this.lastLevel < 0.45) {
      const col = Math.floor(
        ((this.sampleInLine % this.samplesPerLine) / this.samplesPerLine) *
          this.width,
      );
      if (col >= 0 && col < this.width) {
        this.phaseHist[col]! += 1;
      }
    }
    this.lastLevel = level;

    this.sampleInLine += 1;
    if (this.sampleInLine >= this.samplesPerLine) {
      this.sampleInLine -= this.samplesPerLine;
      this.phaseLines += 1;
      if (this.phaseLines >= 8) {
        let best = 0;
        let bestVal = -1;
        for (let c = 0; c < this.width; c++) {
          const v = this.phaseHist[c]!;
          if (v > bestVal) {
            bestVal = v;
            best = c;
          }
        }
        this.phaseOffset = (best / this.width) * this.samplesPerLine;
        this.sampleInLine = 0;
        this.status = "imaging";
      }
    }
  }

  private writePixel(level: number): void {
    if (this.lines >= WEFAX_MAX_LINES) return;

    const pos = this.sampleInLine + this.phaseOffset;
    const wrapped =
      ((pos % this.samplesPerLine) + this.samplesPerLine) % this.samplesPerLine;
    const col = Math.min(
      this.width - 1,
      Math.floor((wrapped / this.samplesPerLine) * this.width),
    );
    const idx = this.lines * this.width + col;
    const prev = this.hit[idx]!;
    const next = Math.round(level * 255);
    if (prev === 0) {
      this.pixels[idx] = next;
      this.hit[idx] = 1;
    } else {
      this.pixels[idx] = Math.round(this.pixels[idx]! * 0.65 + next * 0.35);
    }

    this.sampleInLine += 1;
    if (this.sampleInLine >= this.samplesPerLine) {
      this.sampleInLine -= this.samplesPerLine;
      this.lines += 1;
    }
  }
}

function goertzelAcc(g: Goertzel, sample: number): void {
  const s0 = sample + g.coeff * g.s1 - g.s2;
  g.s2 = g.s1;
  g.s1 = s0;
}

function goertzelPower(g: Goertzel): number {
  return Math.max(0, g.s1 * g.s1 + g.s2 * g.s2 - g.coeff * g.s1 * g.s2);
}

function clamp01(n: number): number {
  return Math.min(1, Math.max(0, n));
}
