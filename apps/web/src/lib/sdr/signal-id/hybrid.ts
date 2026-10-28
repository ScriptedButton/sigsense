import {
  mapBackendTrack,
  type BackendTrack,
} from "./backend-tracks";
import { SignalIdEngine } from "./engine";
import { RemoteSignalIdEngine, type RemoteHealth } from "./remote-engine";
import type { SignalIdSensitivity, SignalIdSource } from "./source";
import type { IdentifiedSignal, SignalIdListener, SignalIdState } from "./types";

/** Backend tracks older than this are gone (matches the server's burst hold). */
const EXTERNAL_HOLD_S = 10;
/**
 * A correction shows at once from the server's reply; the next track poll
 * carries the same label, so this only bridges the gap between them.
 */
const OVERRIDE_MS = 6000;

export type SignalCorrectionPatch = {
  label: string;
  confidence: number;
  learned?: IdentifiedSignal["learned"];
  displayLabel: IdentifiedSignal["label"];
  customLabel: boolean;
};

/**
 * Routes signal identification to the backend ML pipeline:
 *  - browser sources (RTL-SDR, rtl_tcp, OpenWebRX, demo): spectrum rows go to
 *    the backend CNN via RemoteSignalIdEngine;
 *  - the HackRF: the backend already identifies bands on its own IQ, so its
 *    tracks are shown as they arrive and the browser never classifies;
 *  - only if the backend cannot be reached does the small on-device model run,
 *    and the state says so.
 */
export class HybridSignalId implements SignalIdSource {
  private readonly remote: RemoteSignalIdEngine;
  private readonly local = new SignalIdEngine();
  private listeners = new Set<SignalIdListener>();
  private userEnabled = false;
  private external = false;
  private fallback = false;
  private externalSignals: IdentifiedSignal[] = [];
  private externalNoiseDb = -100;
  private overrides = new Map<string, { patch: SignalCorrectionPatch; until: number }>();
  private state: SignalIdState;

  constructor() {
    this.remote = new RemoteSignalIdEngine((health) => this.onHealth(health));
    this.state = this.compose();
    this.remote.subscribe(() => this.refresh());
    this.local.subscribe(() => this.refresh());
  }

  getState(): SignalIdState {
    return this.state;
  }

  subscribe(listener: SignalIdListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  getSensitivity(): SignalIdSensitivity {
    return this.remote.getSensitivity();
  }

  setSensitivity(sensitivity: SignalIdSensitivity): void {
    this.remote.setSensitivity(sensitivity);
    this.local.setSensitivity(sensitivity);
  }

  setEnabled(enabled: boolean): void {
    this.userEnabled = enabled;
    this.remote.setEnabled(enabled);
    this.local.setEnabled(enabled && this.fallback);
    this.refresh();
  }

  reset(): void {
    this.remote.reset();
    this.local.reset();
    this.externalSignals = [];
    this.refresh();
  }

  pushSpectrum(
    raw: Float32Array,
    centerHz: number | undefined,
    sampleRate: number,
    fallbackCenterHz: number,
  ): void {
    if (this.external) return; // the backend identifies HackRF bands itself
    this.remote.pushSpectrum(raw, centerHz, sampleRate, fallbackCenterHz);
    if (this.fallback && this.userEnabled) {
      this.local.pushSpectrum(raw, centerHz, sampleRate, fallbackCenterHz);
    }
  }

  /** The HackRF stream is the active source (true) or the browser's (false). */
  setExternalMode(on: boolean): void {
    if (on === this.external) return;
    this.external = on;
    this.externalSignals = [];
    this.remote.reset();
    this.refresh();
  }

  /** Backend tracks from the HackRF stream; `serverNow` is the server clock. */
  setExternalTracks(
    tracks: BackendTrack[],
    ctx: { centerHz: number; noiseFloorDb: number; serverNow: number },
  ): void {
    if (!this.external) return;
    const now = Date.now();
    const signals: IdentifiedSignal[] = [];
    for (const track of tracks) {
      const ageSec = Math.max(0, ctx.serverNow - (track.last_seen ?? ctx.serverNow));
      if (ageSec > EXTERNAL_HOLD_S) continue;
      signals.push(
        mapBackendTrack(track, {
          centerHz: ctx.centerHz,
          noiseFloorDb: ctx.noiseFloorDb,
          ageSec,
          now,
        }),
      );
    }
    signals.sort((a, b) => {
      if (Boolean(a.stale) !== Boolean(b.stale)) return a.stale ? 1 : -1;
      const bySnr = Math.round(b.snrDb) - Math.round(a.snrDb);
      return bySnr !== 0 ? bySnr : a.id.localeCompare(b.id);
    });
    this.externalSignals = signals;
    this.externalNoiseDb = ctx.noiseFloorDb;
    this.refresh();
  }

  /** Show a correction immediately, ahead of the next backend update. */
  applyCorrection(trackId: string, patch: SignalCorrectionPatch): void {
    this.overrides.set(`be-${trackId}`, { patch, until: Date.now() + OVERRIDE_MS });
    this.refresh();
    setTimeout(() => this.refresh(), OVERRIDE_MS + 50);
  }

  private withOverrides(signals: IdentifiedSignal[]): IdentifiedSignal[] {
    if (this.overrides.size === 0) return signals;
    const now = Date.now();
    for (const [id, o] of this.overrides) if (o.until < now) this.overrides.delete(id);
    if (this.overrides.size === 0) return signals;
    return signals.map((signal) => {
      const o = this.overrides.get(signal.id);
      if (!o) return signal;
      return {
        ...signal,
        label: o.patch.displayLabel,
        backendLabel: o.patch.label,
        confidence: o.patch.confidence,
        unknown: false,
        learned: o.patch.learned ?? signal.learned,
        userCorrected: true,
        customLabel: o.patch.customLabel,
        evidence: "Corrected by you",
      };
    });
  }

  private onHealth(health: RemoteHealth): void {
    const fallback = health === "unreachable";
    if (fallback === this.fallback) return;
    this.fallback = fallback;
    this.local.reset();
    this.local.setEnabled(this.userEnabled && fallback);
    this.refresh();
  }

  private compose(): SignalIdState {
    if (this.external) {
      return {
        enabled: true,
        status: "scanning",
        signals: this.withOverrides(this.externalSignals),
        noiseFloorDb: this.externalNoiseDb,
        error: null,
        source: "backend-cnn",
        detail: "Backend CNN on the HackRF's IQ, band by band",
      };
    }
    if (this.fallback) {
      return {
        ...this.local.getState(),
        enabled: this.userEnabled,
        source: "offline-fallback",
        detail: "Backend unreachable, so the small on-device model is running",
      };
    }
    const remote = this.remote.getState();
    return {
      ...remote,
      enabled: this.userEnabled,
      signals: this.withOverrides(remote.signals),
    };
  }

  private refresh(): void {
    const next = this.compose();
    const prev = this.state;
    const same =
      prev.enabled === next.enabled &&
      prev.status === next.status &&
      prev.signals === next.signals &&
      prev.noiseFloorDb === next.noiseFloorDb &&
      prev.error === next.error &&
      prev.source === next.source &&
      prev.detail === next.detail;
    if (same) return;
    this.state = next;
    for (const listener of this.listeners) listener(next);
  }
}
