import { signalRequest } from "@/lib/api";
import { mapBackendTrack, type BackendTrack } from "./backend-tracks";
import type { SignalIdSensitivity, SignalIdSource } from "./source";
import type { IdentifiedSignal, SignalIdListener, SignalIdState } from "./types";

/** Rows kept for each request (about 3 s at the sampling cadence below). */
const MAX_ROWS = 48;
/** Rows needed before a request is worth sending. */
const MIN_ROWS = 8;
/** At most one stored row per this interval, whatever the radio's frame rate. */
const ROW_INTERVAL_MS = 60;
/** Spacing between requests. */
const POST_INTERVAL_MS = 900;
/** Preserve narrow emitters up to the backend payload limit. */
const MAX_BINS = 16384;
/** After this many failed requests in a row the backend counts as unreachable. */
const FAILURES_BEFORE_UNREACHABLE = 2;
/** While unreachable, retry this often. */
const PROBE_INTERVAL_MS = 15_000;

const SENSITIVITY_DB: Record<SignalIdSensitivity, number> = {
  low: 16,
  medium: 10,
  high: 6,
};

type SpectrumBandsResponse = {
  busy?: boolean;
  noise_floor_db?: number;
  tracks?: (BackendTrack & { age_s?: number; stale?: boolean })[];
  approximation?: string;
  latency_ms?: number;
};

export type RemoteHealth = "ok" | "unreachable";

const EMPTY_STATE: SignalIdState = {
  enabled: false,
  status: "idle",
  signals: [],
  noiseFloorDb: -100,
  error: null,
  source: "backend-cnn",
};

function newSessionId(): string {
  const c = globalThis.crypto;
  return c?.randomUUID
    ? c.randomUUID()
    : `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

/** Uint8Array to base64 without blowing the call stack on large inputs. */
function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

/**
 * Sends the spectrum the radio is already drawing to the backend, where the
 * CNN, open-set library and band plan identify what is in it. One request is
 * in flight at a time; the backend drops overlapping ones too.
 */
export class RemoteSignalIdEngine implements SignalIdSource {
  private state: SignalIdState = EMPTY_STATE;
  private listeners = new Set<SignalIdListener>();
  private rows: Float32Array[] = [];
  private lastRowAt = 0;
  private lastPostAt = 0;
  private inFlight = false;
  private failures = 0;
  private unreachable = false;
  private session = newSessionId();
  private epoch = 0;
  private sensitivity: SignalIdSensitivity = "medium";
  private centerHz = 0;
  private sampleRate = 0;
  private bins = 0;
  private iqMode = false;
  private iq = new Int16Array(65536 * 2);
  private iqCount = 0;

  constructor(private readonly onHealth: (health: RemoteHealth) => void) {}

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
    this.sensitivity = sensitivity;
  }

  setEnabled(enabled: boolean): void {
    if (enabled === this.state.enabled) return;
    this.clear();
    this.patch(
      enabled
        ? { enabled: true, status: "scanning", signals: [], error: null }
        : { enabled: false, status: "idle", signals: [], error: null },
    );
  }

  reset(): void {
    this.clear();
    if (this.state.signals.length > 0) this.patch({ signals: [] });
  }

  pushSpectrum(
    raw: Float32Array,
    centerHz: number | undefined,
    sampleRate: number,
    fallbackCenterHz: number,
  ): void {
    if (this.iqMode) return;
    const center = centerHz ?? fallbackCenterHz;
    if (Math.abs(center - this.centerHz) > 1 || sampleRate !== this.sampleRate) {
      this.clear();
      this.centerHz = center;
      this.sampleRate = sampleRate;
      this.patch({ signals: [] });
    }
    const now = Date.now();
    if (now - this.lastRowAt >= ROW_INTERVAL_MS) {
      this.lastRowAt = now;
      this.storeRow(raw);
    }
    if (!this.state.enabled) return;
    const spacing = this.unreachable ? PROBE_INTERVAL_MS : POST_INTERVAL_MS;
    if (
      this.inFlight ||
      this.rows.length < MIN_ROWS ||
      now - this.lastPostAt < spacing
    ) {
      return;
    }
    this.lastPostAt = now;
    void this.post(now);
  }

  setIqMode(enabled: boolean): void {
    if (enabled === this.iqMode) return;
    this.iqMode = enabled;
    this.reset();
  }

  /** Called before display averaging, with consecutive receiver IQ samples. */
  pushIq(I: Float32Array, Q: Float32Array, centerHz: number, sampleRate: number): void {
    if (!this.iqMode || !this.state.enabled) return;
    if (Math.abs(centerHz - this.centerHz) > 1 || sampleRate !== this.sampleRate) {
      this.clear();
      this.centerHz = centerHz;
      this.sampleRate = sampleRate;
      this.patch({ signals: [] });
    }
    const now = Date.now();
    const spacing = this.unreachable ? PROBE_INTERVAL_MS : POST_INTERVAL_MS;
    if (this.inFlight || now - this.lastPostAt < spacing) return;
    const count = Math.min(I.length, Q.length, this.iq.length / 2 - this.iqCount);
    for (let i = 0; i < count; i++) {
      this.iq[(this.iqCount + i) * 2] = Math.round(Math.max(-1, Math.min(1, I[i])) * 32767);
      this.iq[(this.iqCount + i) * 2 + 1] = Math.round(Math.max(-1, Math.min(1, Q[i])) * 32767);
    }
    this.iqCount += count;
    if (this.iqCount < this.iq.length / 2) return;
    this.lastPostAt = now;
    this.iqCount = 0;
    void this.send("/analyze/iq-bands", {
      session: this.session,
      center_freq_hz: this.centerHz,
      sample_rate_hz: this.sampleRate,
      data: toBase64(new Uint8Array(this.iq.buffer)),
      sensitivity_db: SENSITIVITY_DB[this.sensitivity],
    }, now, this.epoch);
  }

  /** Raw FFT order to ascending frequency, pooled to at most MAX_BINS. */
  private storeRow(raw: Float32Array): void {
    const n = raw.length & ~1;
    if (n < 16) return;
    const half = n >> 1;
    const out = new Float32Array(Math.min(n, MAX_BINS));
    for (let i = 0; i < out.length; i++) {
      // Cover the entire span, including a partial final group. Average power,
      // not dB: averaging logarithms buries a narrow signal among quiet bins.
      const start = Math.floor(i * n / out.length);
      const end = Math.floor((i + 1) * n / out.length);
      if (end - start === 1) {
        const v = raw[(start + half) % n];
        out[i] = Number.isFinite(v) ? v : -200;
        continue;
      }
      let peak = -200;
      for (let k = start; k < end; k++) {
        const v = raw[(k + half) % n];
        if (Number.isFinite(v)) peak = Math.max(peak, v);
      }
      let power = 0;
      for (let k = start; k < end; k++) {
        const v = raw[(k + half) % n];
        power += 10 ** (((Number.isFinite(v) ? v : -200) - peak) / 10);
      }
      out[i] = peak + 10 * Math.log10(power / (end - start));
    }
    if (this.bins !== out.length) {
      this.clear();
      this.patch({ signals: [] });
      this.bins = out.length;
    }
    this.rows.push(out);
    if (this.rows.length > MAX_ROWS) this.rows.shift();
  }

  private async post(now: number): Promise<void> {
    const rows = this.rows.slice();
    const bins = this.bins;
    const epoch = this.epoch;
    let min = Infinity;
    let max = -Infinity;
    for (const row of rows) {
      for (let i = 0; i < bins; i++) {
        const v = row[i];
        if (v < min) min = v;
        if (v > max) max = v;
      }
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) return;
    const dbMin = Math.max(min, max - 120);
    const dbMax = Math.max(max, dbMin + 40);
    const scale = 255 / (dbMax - dbMin);
    const bytes = new Uint8Array(rows.length * bins);
    rows.forEach((row, r) => {
      for (let i = 0; i < bins; i++) {
        const q = Math.round((row[i] - dbMin) * scale);
        bytes[r * bins + i] = q < 0 ? 0 : q > 255 ? 255 : q;
      }
    });

    await this.send("/analyze/spectrum-bands", {
      session: this.session,
      center_freq_hz: this.centerHz,
      sample_rate_hz: this.sampleRate,
      rows: rows.length,
      bins,
      db_min: dbMin,
      db_max: dbMax,
      data: toBase64(bytes),
      sensitivity_db: SENSITIVITY_DB[this.sensitivity],
    }, now, epoch);
  }

  private async send(path: string, payload: object, now: number, epoch: number): Promise<void> {
    this.inFlight = true;
    try {
      const response = await signalRequest<SpectrumBandsResponse>(path, payload);
      if (epoch !== this.epoch) return; // retuned while in flight
      this.markReachable();
      if (response.busy) return;
      this.apply(response, now);
    } catch (err) {
      if (epoch !== this.epoch) return;
      this.failures++;
      if (this.failures >= FAILURES_BEFORE_UNREACHABLE && !this.unreachable) {
        this.unreachable = true;
        this.patch({
          error: err instanceof Error ? err.message : String(err),
        });
        this.onHealth("unreachable");
      }
    } finally {
      if (epoch === this.epoch) this.inFlight = false;
    }
  }

  private markReachable(): void {
    this.failures = 0;
    if (this.unreachable) {
      this.unreachable = false;
      this.patch({ error: null });
      this.onHealth("ok");
    }
  }

  private apply(response: SpectrumBandsResponse, now: number): void {
    const noiseFloorDb = response.noise_floor_db ?? this.state.noiseFloorDb;
    const signals: IdentifiedSignal[] = (response.tracks ?? []).map((track) => ({
      ...mapBackendTrack(track, {
        centerHz: this.centerHz,
        noiseFloorDb,
        ageSec: track.age_s ?? 0,
        now,
      }),
      session: this.session,
    }));
    // Strongest first; 1 dB buckets so peak flicker does not reshuffle rows.
    signals.sort((a, b) => {
      if (Boolean(a.stale) !== Boolean(b.stale)) return a.stale ? 1 : -1;
      const bySnr = Math.round(b.snrDb) - Math.round(a.snrDb);
      return bySnr !== 0 ? bySnr : a.id.localeCompare(b.id);
    });
    this.patch({
      signals,
      noiseFloorDb,
      status: "scanning",
      error: null,
      source: "backend-cnn",
      detail: response.approximation,
    });
  }

  private clear(): void {
    this.rows = [];
    this.iqCount = 0;
    this.bins = 0;
    this.lastPostAt = 0;
    this.lastRowAt = 0;
    this.epoch++;
    this.inFlight = false;
    // A new session so the previous tuning's tracks are not carried over.
    this.session = newSessionId();
  }

  private patch(partial: Partial<SignalIdState>): void {
    this.state = { ...this.state, ...partial };
    for (const listener of this.listeners) listener(this.state);
  }
}

