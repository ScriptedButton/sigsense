import { estimateNoiseFloor, shiftSpectrum } from "./signal-id/detector";

/** Manual threshold in dB above the spectrum noise floor. 0 = squelch off. */
export const SQUELCH_MIN_DB = 0;
export const SQUELCH_MAX_DB = 40;
export const SQUELCH_DEFAULT_DB = 0;

/** Extra margin above the tracked noise floor when auto-squelch is on. */
export const AUTO_SQUELCH_MIN_MARGIN_DB = 4;
export const AUTO_SQUELCH_MAX_MARGIN_DB = 24;
export const AUTO_SQUELCH_DEFAULT_MARGIN_DB = 10;

/** Keep audio open this long after the signal drops (seconds). */
const HANG_SECONDS = 0.25;
/** Hysteresis so the gate doesn't chatter near the threshold. */
const HYSTERESIS_DB = 2;

export type SquelchDecision = {
  open: boolean;
  channelDb: number;
  noiseDb: number;
  thresholdDb: number;
};

export type SquelchUpdateInput = {
  /** Peak level in the demod passband (dBFS). */
  channelDb: number;
  /** Global spectrum noise floor (dBFS). */
  noiseFloorDb: number;
  /** Manual threshold (dB above noise). Ignored as absolute when auto. */
  levelDb: number;
  auto: boolean;
  /** Wall-clock seconds since the previous update. */
  dtSeconds: number;
};

/**
 * Peak power in the tuned passband from a raw (FFT-order) spectrum frame.
 * Bins are mapped with DC at index 0; LO-relative `offsetHz` selects the channel.
 */
export function measureChannelPeakDb(
  rawSpectrum: Float32Array,
  sampleRate: number,
  offsetHz: number,
  bandwidthHz: number,
): number {
  const n = rawSpectrum.length;
  if (n === 0 || sampleRate <= 0) return -200;

  const binHz = sampleRate / n;
  const halfBw = Math.max(binHz, bandwidthHz / 2);
  const loHz = offsetHz - halfBw;
  const hiHz = offsetHz + halfBw;

  // FFT bins: 0 = DC (LO), then positive offsets, then negative (wrapped).
  let peak = -Infinity;
  let counted = 0;
  for (let i = 0; i < n; i++) {
    const hz = i <= n / 2 ? i * binHz : (i - n) * binHz;
    if (hz < loHz || hz > hiHz) continue;
    const v = rawSpectrum[i];
    if (!Number.isFinite(v)) continue;
    if (v > peak) peak = v;
    counted++;
  }

  if (counted === 0) return -200;
  return peak;
}

/** Noise floor + channel peak for the current tune. */
export function measureSquelchLevels(
  rawSpectrum: Float32Array,
  sampleRate: number,
  offsetHz: number,
  bandwidthHz: number,
  shiftedScratch?: Float32Array,
): { channelDb: number; noiseFloorDb: number } {
  const shifted = shiftSpectrum(rawSpectrum, shiftedScratch);
  return {
    channelDb: measureChannelPeakDb(
      rawSpectrum,
      sampleRate,
      offsetHz,
      bandwidthHz,
    ),
    noiseFloorDb: estimateNoiseFloor(shifted),
  };
}

/**
 * Spectrum-driven squelch with hang time and optional auto noise tracking.
 * Mutes when the tuned channel sits near the noise floor.
 */
export class SquelchGate {
  private noiseDb = -90;
  private open = true;
  private hangRemaining = 0;
  private shiftedScratch: Float32Array | null = null;

  reset(): void {
    this.noiseDb = -90;
    this.open = true;
    this.hangRemaining = 0;
  }

  isOpen(): boolean {
    return this.open;
  }

  /**
   * Evaluate a new spectrum frame. Returns whether audio should play.
   * When `levelDb` is 0 and auto is off, the gate stays open (squelch disabled).
   */
  update(input: SquelchUpdateInput): SquelchDecision {
    const { channelDb, noiseFloorDb, levelDb, auto, dtSeconds } = input;

    if (!auto && levelDb <= 0) {
      this.open = true;
      this.hangRemaining = 0;
      this.noiseDb = noiseFloorDb;
      return {
        open: true,
        channelDb,
        noiseDb: noiseFloorDb,
        thresholdDb: noiseFloorDb,
      };
    }

    // Auto: slowly follow quiet readings so the threshold rides above noise.
    if (auto) {
      const quiet = Math.min(channelDb, noiseFloorDb);
      const alpha = channelDb < this.noiseDb + HYSTERESIS_DB ? 0.25 : 0.04;
      this.noiseDb = this.noiseDb * (1 - alpha) + quiet * alpha;
    } else {
      this.noiseDb = noiseFloorDb;
    }

    const margin = auto
      ? clamp(
          levelDb > 0 ? levelDb : AUTO_SQUELCH_DEFAULT_MARGIN_DB,
          AUTO_SQUELCH_MIN_MARGIN_DB,
          AUTO_SQUELCH_MAX_MARGIN_DB,
        )
      : clamp(levelDb, SQUELCH_MIN_DB, SQUELCH_MAX_DB);

    const thresholdDb = this.noiseDb + margin;
    const openThreshold = thresholdDb;
    const closeThreshold = thresholdDb - HYSTERESIS_DB;

    if (channelDb >= openThreshold) {
      this.open = true;
      this.hangRemaining = HANG_SECONDS;
    } else if (channelDb < closeThreshold) {
      this.hangRemaining = Math.max(0, this.hangRemaining - Math.max(dtSeconds, 0));
      if (this.hangRemaining <= 0) this.open = false;
    }

    return {
      open: this.open,
      channelDb,
      noiseDb: this.noiseDb,
      thresholdDb,
    };
  }

  /** Convenience: measure levels from a raw FFT frame, then update. */
  updateFromSpectrum(
    rawSpectrum: Float32Array,
    sampleRate: number,
    offsetHz: number,
    bandwidthHz: number,
    levelDb: number,
    auto: boolean,
    dtSeconds: number,
  ): SquelchDecision {
    if (!this.shiftedScratch || this.shiftedScratch.length !== rawSpectrum.length) {
      this.shiftedScratch = new Float32Array(rawSpectrum.length);
    }
    const levels = measureSquelchLevels(
      rawSpectrum,
      sampleRate,
      offsetHz,
      bandwidthHz,
      this.shiftedScratch,
    );
    return this.update({
      channelDb: levels.channelDb,
      noiseFloorDb: levels.noiseFloorDb,
      levelDb,
      auto,
      dtSeconds,
    });
  }
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
