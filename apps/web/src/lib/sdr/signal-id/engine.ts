import { DEFAULT_SAMPLE_RATE } from "../types";
import { bandPlanMatch } from "./band-plan";
import { matchCatalog } from "./catalog";
import { classProbabilitiesFor, BUNDLED_MODEL, labelFor } from "./classifier";
import type { ModelWeights } from "./mlp";
import { detectInFrames, featuresFor } from "./pipeline";
import { classPriorFactors, fusePrior } from "./priors";
import { detectSignals, estimateNoiseFloor, shiftSpectrum } from "./detector";
import { extractFeatures } from "./features";
import { isCoarse, toTrainingGrid } from "./resample";
import { classInfo } from "./labels";
import {
  CLASS_COUNT,
  type Detection,
  type IdentifiedSignal,
  type SignalIdentity,
  type SignalIdListener,
  type SignalIdState,
} from "./types";

/** Frames retained for temporal features — 18 at 20 fps is roughly 0.9 s. */
const HISTORY_FRAMES = 18;
/** Classification is far slower than frame arrival, so run it on a timer. */
const ANALYSIS_INTERVAL_MS = 400;
/** Below this much history the temporal features are meaningless. */
const MIN_FRAMES = 10;
/**
 * A track survives this long without a matching detection before it is dropped.
 * Push-to-talk traffic (GMRS) is silent most of the time, so tracks are held
 * well past a single transmission.
 */
const TRACK_TIMEOUT_MS = 8000;
/** Past this silence a track is flagged stale and its confidence fades. */
const FRESH_MS = 2500;
/** Lowest fraction of confidence a held track fades to before it is dropped. */
const STALE_FLOOR = 0.3;
/** Shape inferred from under four source bins is weak evidence. */
const LOW_RES_FACTOR = 0.7;
/** Band-plan relabelling applies below this model confidence. */
const BAND_PLAN_CONFIDENCE = 0.55;
/** Centre/rate changes smaller than this do not invalidate history. */
const RETUNE_TOLERANCE_HZ = 1;
/** Weight given to each new observation when smoothing class probabilities. */
const SMOOTHING = 0.28;
/** Hide tracks until we have enough frames for a stable class estimate. */
const MIN_OBSERVATIONS = 2;

export type SignalIdSensitivity = "low" | "medium" | "high";

/** Knobs for A/B evaluation; the app uses the defaults. */
export type SignalIdOptions = {
  /** Weights to classify with (default: the bundled model). */
  model?: ModelWeights;
  /** Overrides the model's own calibration temperature. */
  temperature?: number;
  /** Fuse catalog frequency/bandwidth priors into the class probabilities. */
  priors?: boolean;
  /** Prior strength (exponent on the prior factors). */
  priorBeta?: number;
  /** Frames power-averaged for detection (1 = the original single-frame rule). */
  detectionFrames?: number;
  /** How per-frame probabilities are combined over a track's life. */
  smoothing?: "ema" | "log";
};

/** Frames of memory in log-domain smoothing; older evidence decays by this. */
const LOG_DECAY = 0.85;
/** Log-domain evidence is shared across this many correlated frames. */
const LOG_EVIDENCE_FRAMES = 4;

const SENSITIVITY_DB: Record<SignalIdSensitivity, number> = {
  low: 16,
  medium: 10,
  high: 6,
};

type Track = {
  id: string;
  peakBin: number;
  probabilities: Float32Array;
  /** Running log-evidence for log-domain smoothing. */
  logEvidence: Float32Array;
  observations: number;
  firstSeen: number;
  lastSeen: number;
  offsetHz: number;
  bandwidthHz: number;
  snrDb: number;
  lowResolution: boolean;
};

const EMPTY_STATE: SignalIdState = {
  enabled: false,
  status: "idle",
  signals: [],
  noiseFloorDb: -100,
  error: null,
};

/**
 * Scans live spectrum frames for active emitters and classifies each one's
 * modulation with the bundled model.
 *
 * Detections are associated into tracks across frames so a signal keeps a
 * stable identity, and its class probabilities are smoothed over time — a
 * single frame is often ambiguous, but a second of them rarely is.
 */
export class SignalIdEngine {
  private state: SignalIdState = EMPTY_STATE;
  private listeners = new Set<SignalIdListener>();
  /** Preallocated ring of shifted spectrum frames — avoids per-tick GC. */
  private ring: Float32Array[] = [];
  private ringStart = 0;
  private ringCount = 0;
  /** Stable chronological view passed into feature extraction. */
  private framesView: Float32Array[] = [];
  private tracks: Track[] = [];
  private lastAnalysis = 0;
  private sampleRate = DEFAULT_SAMPLE_RATE;
  private centerHz = 0;
  private sensitivity: SignalIdSensitivity = "medium";
  private nextId = 1;
  private readonly model: ModelWeights;
  private readonly temperature: number | undefined;
  private readonly usePriors: boolean;
  private readonly priorBeta: number;
  private readonly smoothing: "ema" | "log";
  private readonly detectionFrames: number | undefined;

  constructor(options: SignalIdOptions = {}) {
    this.model = options.model ?? BUNDLED_MODEL;
    this.temperature = options.temperature;
    // Off by default: on the held-out impairment evaluation the catalog prior
    // gained nothing when informative and lost 1-2.5 points when misleading.
    this.usePriors = options.priors ?? false;
    this.priorBeta = options.priorBeta ?? 1;
    this.smoothing = options.smoothing ?? "log";
    this.detectionFrames = options.detectionFrames;
  }

  getState(): SignalIdState {
    return this.state;
  }

  subscribe(listener: SignalIdListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  getSensitivity(): SignalIdSensitivity {
    return this.sensitivity;
  }

  setSensitivity(sensitivity: SignalIdSensitivity): void {
    if (sensitivity === this.sensitivity) return;
    this.sensitivity = sensitivity;
    this.tracks = [];
    this.lastAnalysis = 0;
  }

  setEnabled(enabled: boolean): void {
    if (enabled === this.state.enabled) return;
    if (!enabled) {
      this.clearBuffers();
      this.patch({ enabled: false, status: "idle", signals: [], error: null });
      return;
    }
    this.clearBuffers();
    this.patch({ enabled: true, status: "scanning", error: null });
  }

  /** Drops all accumulated history, e.g. after retuning or disconnecting. */
  reset(): void {
    this.clearBuffers();
    if (this.state.signals.length > 0) {
      this.patch({ signals: [] });
    }
  }

  /**
   * Accepts a raw FFT-order spectrum frame. Cheap on most calls: frames are
   * only buffered, with detection and inference running on a timer.
   */
  pushSpectrum(
    raw: Float32Array,
    centerHz: number | undefined,
    sampleRate: number,
    fallbackCenterHz: number,
  ): void {
    if (!this.state.enabled) return;

    const center = centerHz ?? fallbackCenterHz;
    if (
      Math.abs(center - this.centerHz) > RETUNE_TOLERANCE_HZ ||
      Math.abs(sampleRate - this.sampleRate) > RETUNE_TOLERANCE_HZ
    ) {
      this.centerHz = center;
      this.sampleRate = sampleRate;
      this.clearBuffers();
    }

    this.pushFrame(raw);

    const now = Date.now();
    if (now - this.lastAnalysis < ANALYSIS_INTERVAL_MS) return;
    if (this.ringCount < MIN_FRAMES) return;
    this.lastAnalysis = now;

    try {
      this.analyse(now);
    } catch (err) {
      this.patch({
        status: "error",
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  private pushFrame(raw: Float32Array): void {
    const n = raw.length;
    if (this.ringCount < HISTORY_FRAMES) {
      const buf = new Float32Array(n);
      shiftSpectrum(raw, buf);
      this.ring.push(buf);
      this.ringCount = this.ring.length;
      return;
    }

    // Overwrite the oldest slot and advance the start pointer.
    const slot = this.ring[this.ringStart];
    if (slot.length !== n) {
      this.clearBuffers();
      this.pushFrame(raw);
      return;
    }
    shiftSpectrum(raw, slot);
    this.ringStart = (this.ringStart + 1) % HISTORY_FRAMES;
  }

  /** Chronological frame list for the current ring contents (reused array). */
  private framesInOrder(): Float32Array[] {
    const count = this.ringCount;
    this.framesView.length = count;
    if (count < HISTORY_FRAMES) {
      for (let i = 0; i < count; i++) this.framesView[i] = this.ring[i];
      return this.framesView;
    }
    for (let i = 0; i < count; i++) {
      this.framesView[i] = this.ring[(this.ringStart + i) % HISTORY_FRAMES];
    }
    return this.framesView;
  }

  private analyse(now: number): void {
    const frames = this.framesInOrder();
    const latest = frames[frames.length - 1];
    const binHz = this.sampleRate / latest.length;
    const { detections, noiseFloorDb } = detectInFrames(
      frames,
      binHz,
      SENSITIVITY_DB[this.sensitivity],
      this.detectionFrames,
    );

    const centerBin = latest.length / 2;
    const claimed = new Set<Track>();

    for (const detection of detections) {
      const track = this.matchTrack(detection, claimed) ?? this.createTrack(detection, now);
      claimed.add(track);

      // The model learned ~500 Hz bins; re-bin other resolutions onto that grid.
      const { features, lowResolution } = featuresFor(
        frames,
        detection,
        noiseFloorDb,
        binHz,
      );
      const probabilities = classProbabilitiesFor(
        this.model,
        features,
        this.temperature,
      );

      if (this.smoothing === "log") {
        // Product of per-frame evidence with forgetting: consistent frames
        // reinforce each other faster than an average allows.
        const first = track.observations === 0;
        for (let i = 0; i < CLASS_COUNT; i++) {
          track.logEvidence[i] =
            (first ? 0 : track.logEvidence[i] * LOG_DECAY) +
            Math.log(probabilities[i] + 1e-6);
        }
        const share = Math.min(track.observations + 1, LOG_EVIDENCE_FRAMES);
        const shared = softmaxOf(track.logEvidence, 1 / share);
        track.probabilities.set(shared);
      } else {
        // Exponential moving average: one frame is noisy, a run of them is not.
        const weight = track.observations === 0 ? 1 : SMOOTHING;
        for (let i = 0; i < CLASS_COUNT; i++) {
          track.probabilities[i] =
            track.probabilities[i] * (1 - weight) + probabilities[i] * weight;
        }
      }

      track.observations++;
      track.lastSeen = now;
      track.peakBin = detection.peakBin;
      track.offsetHz = (detection.peakBin - centerBin) * binHz;
      track.bandwidthHz = (detection.hiBin - detection.loBin + 1) * binHz;
      track.snrDb = detection.peakDb - noiseFloorDb;
      track.lowResolution = lowResolution;
    }

    this.tracks = this.tracks.filter((t) => now - t.lastSeen <= TRACK_TIMEOUT_MS);

    const signals: IdentifiedSignal[] = this.tracks
      .filter((t) => t.observations >= MIN_OBSERVATIONS)
      .map((track) => {
        const frequencyHz = this.centerHz + track.offsetHz;
        const fused = this.usePriors
          ? fusePrior(
              track.probabilities,
              classPriorFactors(frequencyHz, track.bandwidthHz),
              this.priorBeta,
            )
          : track.probabilities;
        const modelResult = labelFor(fused);
        let label = modelResult.label;
        let rawConfidence = modelResult.confidence;
        if (track.lowResolution) rawConfidence *= LOW_RES_FACTOR;
        const identities: SignalIdentity[] = matchCatalog({
          frequencyHz,
          bandwidthHz: track.bandwidthHz,
          label,
        }).map((m) => ({
          id: m.id,
          origin: m.origin,
          name: m.name,
          url: m.url,
          score: m.score,
          description: m.description,
          categories: m.categories,
          mode: m.mode,
          modulation: m.modulation,
        }));

        // A strong catalog hit that agrees with the MLP is extra evidence —
        // nudge confidence up so the UI ranks clearer IDs first.
        // Band plan: on a known FRS/GMRS channel the frequency outweighs an
        // unsure model, which cannot resolve a 12.5 kHz signal in wide bins.
        let basis: "model" | "band-plan" = "model";
        let bandPlanNote: string | undefined;
        const plan = bandPlanMatch({
          frequencyHz,
          bandwidthHz: track.bandwidthHz,
          binHz,
        });
        if (plan) {
          const existing = identities.findIndex((i) => i.id === plan.identity.id);
          if (existing > 0) identities.splice(existing, 1);
          if (existing !== 0) identities.unshift(plan.identity);
          if (
            (rawConfidence < BAND_PLAN_CONFIDENCE || label === "NOISE") &&
            plan.score >= 0.7
          ) {
            label = "NBFM";
            rawConfidence = Math.max(rawConfidence, plan.score * LOW_RES_FACTOR);
            basis = "band-plan";
            bandPlanNote = plan.note;
          }
        }

        const top = identities[0];
        let confidence =
          top && top.score >= 0.7
            ? Math.min(1, rawConfidence + 0.08 * top.score)
            : rawConfidence;

        // Held burst: fade rather than vanish so PTT traffic does not flicker.
        const silence = now - track.lastSeen;
        const stale = silence > FRESH_MS;
        if (stale) {
          const fade = (silence - FRESH_MS) / (TRACK_TIMEOUT_MS - FRESH_MS);
          confidence *= Math.max(STALE_FLOOR, 1 - fade * (1 - STALE_FLOOR));
        }

        return {
          id: track.id,
          frequencyHz,
          offsetHz: track.offsetHz,
          bandwidthHz: track.bandwidthHz,
          snrDb: track.snrDb,
          label,
          confidence,
          scheme: classInfo(label).scheme,
          identities,
          firstSeen: track.firstSeen,
          lastSeen: track.lastSeen,
          basis,
          bandPlanNote,
          lowResolution: track.lowResolution,
          stale,
        };
      })
      // Strongest first; 1 dB buckets so peak flicker does not reshuffle under the cursor.
      .sort((a, b) => {
        const snrBucket = (db: number) => Math.round(db);
        const bySnr = snrBucket(b.snrDb) - snrBucket(a.snrDb);
        return bySnr !== 0 ? bySnr : a.id.localeCompare(b.id);
      });

    this.patch({ signals, noiseFloorDb, status: "scanning", error: null });
  }

  /** Nearest unclaimed track whose position overlaps this detection. */
  private matchTrack(detection: Detection, claimed: Set<Track>): Track | null {
    const width = detection.hiBin - detection.loBin + 1;
    const tolerance = Math.max(3, width * 0.5);
    let best: Track | null = null;
    let bestDistance = Infinity;
    for (const track of this.tracks) {
      if (claimed.has(track)) continue;
      const distance = Math.abs(track.peakBin - detection.peakBin);
      if (distance <= tolerance && distance < bestDistance) {
        bestDistance = distance;
        best = track;
      }
    }
    return best;
  }

  private createTrack(detection: Detection, now: number): Track {
    const track: Track = {
      id: `sig-${this.nextId++}`,
      peakBin: detection.peakBin,
      probabilities: new Float32Array(CLASS_COUNT),
      logEvidence: new Float32Array(CLASS_COUNT),
      observations: 0,
      firstSeen: now,
      lastSeen: now,
      offsetHz: 0,
      bandwidthHz: 0,
      snrDb: 0,
      lowResolution: false,
    };
    this.tracks.push(track);
    return track;
  }

  private clearBuffers(): void {
    this.ring = [];
    this.ringStart = 0;
    this.ringCount = 0;
    this.framesView = [];
    this.tracks = [];
    this.lastAnalysis = 0;
  }

  private patch(partial: Partial<SignalIdState>): void {
    this.state = { ...this.state, ...partial };
    for (const listener of this.listeners) listener(this.state);
  }
}

function softmaxOf(logits: Float32Array, scale: number): Float32Array {
  let max = -Infinity;
  for (let i = 0; i < logits.length; i++) max = Math.max(max, logits[i] * scale);
  const out = new Float32Array(logits.length);
  let sum = 0;
  for (let i = 0; i < logits.length; i++) {
    out[i] = Math.exp(logits[i] * scale - max);
    sum += out[i];
  }
  for (let i = 0; i < out.length; i++) out[i] /= sum || 1;
  return out;
}
