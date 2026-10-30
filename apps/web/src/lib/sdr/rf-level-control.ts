/** Pure RTL-SDR ADC level analysis and conservative manual-gain control. */

export type RfLevelMetrics = {
  railFraction: number;
  nearRailFraction: number;
  rms: number;
  peak: number;
};

export type RfGainRecommendation = {
  gainDb: number;
  disableAutoGain: boolean;
  reason: "overload" | "headroom";
  metrics: RfLevelMetrics;
};

export type RfLevelControlOptions = {
  minGainDb?: number;
  maxGainDb?: number;
  /** Maximum exact-rail sample fraction tolerated before gain is reduced. */
  maxRailFraction?: number;
  /** Clean time required between cautious gain increases. */
  gainUpIntervalMs?: number;
};

export function analyzeUnsignedIq(iq: Uint8Array): RfLevelMetrics {
  const length = iq.length - (iq.length & 1);
  if (length === 0) {
    return { railFraction: 0, nearRailFraction: 0, rms: 0, peak: 0 };
  }

  let rails = 0;
  let nearRails = 0;
  let sumSquares = 0;
  let peak = 0;
  for (let index = 0; index < length; index++) {
    const byte = iq[index]!;
    if (byte === 0 || byte === 255) rails++;
    if (byte <= 2 || byte >= 253) nearRails++;
    const centered = (byte - 127.5) / 127.5;
    sumSquares += centered * centered;
    peak = Math.max(peak, Math.abs(centered));
  }
  return {
    railFraction: rails / length,
    nearRailFraction: nearRails / length,
    rms: Math.sqrt(sumSquares / length),
    peak,
  };
}

/**
 * Overload recovery is immediate; gain-up is deliberately slow. The caller
 * applies recommendations and feeds subsequent raw unsigned I/Q blocks back.
 */
export class RfLevelController {
  private readonly minGainDb: number;
  private readonly maxGainDb: number;
  private readonly maxRailFraction: number;
  private readonly gainUpIntervalMs: number;
  private lastAdjustmentMs: number | null = null;
  private cleanSinceMs: number | null = null;

  constructor(options: RfLevelControlOptions = {}) {
    this.minGainDb = options.minGainDb ?? 0;
    this.maxGainDb = options.maxGainDb ?? 50;
    this.maxRailFraction = options.maxRailFraction ?? 0.001;
    this.gainUpIntervalMs = options.gainUpIntervalMs ?? 30_000;
  }

  observe(
    iq: Uint8Array,
    currentGainDb: number,
    autoGain: boolean,
    nowMs: number,
  ): RfGainRecommendation | null {
    const metrics = analyzeUnsignedIq(iq);
    if (metrics.railFraction > this.maxRailFraction) {
      this.cleanSinceMs = null;
      this.lastAdjustmentMs = nowMs;
      // The live V4 captures railed 3.34% on AGC and 5.38% at 20 dB. Large
      // overloads need a decisive cut rather than several seconds of pumping.
      const reduction = metrics.railFraction > 0.02
        ? 12
        : metrics.railFraction > 0.005
          ? 8
          : 4;
      const gainDb = Math.max(this.minGainDb, currentGainDb - reduction);
      if (!autoGain && gainDb === currentGainDb) return null;
      return {
        gainDb,
        disableAutoGain: true,
        reason: "overload",
        metrics,
      };
    }

    if (this.cleanSinceMs === null) this.cleanSinceMs = nowMs;
    if (autoGain) return null;
    const sinceAdjustment = nowMs - (this.lastAdjustmentMs ?? this.cleanSinceMs);
    const cleanFor = nowMs - this.cleanSinceMs;
    // Only seek more gain with ample ADC headroom. This prevents a strong
    // narrow carrier from repeatedly driving gain up into clipping.
    if (
      metrics.peak < 0.5 &&
      metrics.rms < 0.2 &&
      cleanFor >= this.gainUpIntervalMs &&
      sinceAdjustment >= this.gainUpIntervalMs &&
      currentGainDb < this.maxGainDb
    ) {
      this.lastAdjustmentMs = nowMs;
      this.cleanSinceMs = nowMs;
      return {
        gainDb: Math.min(this.maxGainDb, currentGainDb + 1),
        disableAutoGain: true,
        reason: "headroom",
        metrics,
      };
    }
    return null;
  }
}
