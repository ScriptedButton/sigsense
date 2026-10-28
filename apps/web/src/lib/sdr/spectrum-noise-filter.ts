/**
 * Display-side spectrum noise remover.
 * Compresses energy near the noise floor so hash is quieter while peak shapes
 * and skirts stay intact. Does not alter audio, squelch, or signal-id.
 */

export type NoiseFilterId = "off" | "low" | "medium" | "high";

export const NOISE_FILTER_OPTIONS: {
  id: NoiseFilterId;
  label: string;
}[] = [
  { id: "off", label: "Off" },
  { id: "low", label: "Low" },
  { id: "medium", label: "Medium" },
  { id: "high", label: "High" },
];

/**
 * How much of the near-floor excess to keep (1 = unchanged, 0 = flat floor).
 * Soft enough that the trace still looks like RF, not a binary comb.
 */
const FLOOR_RETAIN: Record<NoiseFilterId, number> = {
  off: 1,
  low: 0.55,
  medium: 0.32,
  high: 0.16,
};

/**
 * dB above the floor where compression ends and the signal passes 1:1.
 * Kept modest so weak carriers and skirts survive.
 */
const HINGE_DB: Record<NoiseFilterId, number> = {
  off: 0,
  low: 4,
  medium: 5,
  high: 6,
};

/** Temporal smoothing on the noise-floor estimate. */
const FLOOR_ALPHA = 0.18;

export function isNoiseFilterId(id: unknown): id is NoiseFilterId {
  return NOISE_FILTER_OPTIONS.some((o) => o.id === id);
}

/**
 * Stateful per-display filter. Reuses scratch buffers across frames so the
 * ~20 Hz spectrum path stays allocation-free after warmup.
 */
export class SpectrumNoiseFilter {
  private floorDb = -100;
  private hasFloor = false;
  private sorted: Float32Array | null = null;
  private out: Float32Array | null = null;

  reset(): void {
    this.hasFloor = false;
    this.floorDb = -100;
  }

  /**
   * Returns a filtered view of `spectrum`. When strength is off, returns the
   * original buffer unchanged. Otherwise writes into an internal buffer.
   */
  apply(spectrum: Float32Array, strength: NoiseFilterId): Float32Array {
    if (strength === "off") return spectrum;

    const n = spectrum.length;
    if (!this.out || this.out.length !== n) {
      this.out = new Float32Array(n);
      this.sorted = new Float32Array(n);
    }
    const out = this.out;
    const sorted = this.sorted!;

    const instant = percentile25(spectrum, sorted);
    if (!this.hasFloor) {
      this.floorDb = instant;
      this.hasFloor = true;
    } else {
      this.floorDb =
        this.floorDb * (1 - FLOOR_ALPHA) + instant * FLOOR_ALPHA;
    }

    const floor = this.floorDb;
    const retain = FLOOR_RETAIN[strength];
    const hinge = HINGE_DB[strength];
    // Continuity at the hinge: compressed height meets 1:1 slope.
    const hingeOut = hinge * retain;

    for (let i = 0; i < n; i++) {
      const db = spectrum[i];
      if (!Number.isFinite(db)) {
        out[i] = floor;
        continue;
      }

      const excess = db - floor;
      if (excess <= hinge) {
        // Compress grass and weak skirts toward the floor; keep texture.
        out[i] = floor + excess * retain;
      } else {
        // Strong bins: pass through with a continuous join at the hinge.
        out[i] = floor + hingeOut + (excess - hinge);
      }
    }

    return out;
  }
}

/** 25th-percentile noise floor — order-independent, no fftshift needed. */
function percentile25(spectrum: Float32Array, scratch: Float32Array): number {
  const n = spectrum.length;
  let count = 0;
  for (let i = 0; i < n; i++) {
    const v = spectrum[i];
    if (Number.isFinite(v)) scratch[count++] = v;
  }
  if (count === 0) return -100;
  const view = scratch.subarray(0, count);
  view.sort();
  return view[Math.floor(count * 0.25)];
}
