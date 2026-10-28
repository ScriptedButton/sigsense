import { bandPlanMatch } from "./band-plan";
import { matchCatalog } from "./catalog";
import { isBuiltinClass } from "./backend-classes";
import { classInfo } from "./labels";
import type { DisplayClass, IdentifiedSignal, SignalIdentity } from "./types";

/** A track as the backend serialises it (observe_frame / spectrum-bands). */
export type BackendTrack = {
  id: string;
  label: string;
  confidence: number;
  alternatives?: { label: string; confidence: number }[];
  band_plan?: { label?: string; service?: string; channel?: number } | null;
  center_freq_hz: number;
  bandwidth_hz: number;
  power_dbfs?: number | null;
  snr_db?: number | null;
  first_seen?: number;
  last_seen?: number;
  evidence?: string;
  identity_type?: string;
  name?: string;
  discovered_name?: string;
  /** Set when the user's corrections moved this label. */
  learned?: { label: string; examples: number; similarity: number; weight: number } | null;
  /** The user set this label by hand for this track. */
  user_corrected?: boolean;
};

const FSK_LIKE = /^(ASK|FSK|GFSK|MSK|GMSK)/;

/** Backend 49-class name to the UI's display class (colour, tuning, filters). */
export function displayClassFor(label: string): DisplayClass {
  switch (label) {
    case "unknown":
      return "UNKNOWN";
    case "AM":
      return "AM";
    case "NFM":
      return "NBFM";
    case "WFM":
      return "WBFM";
    case "USB":
    case "LSB":
    case "CW":
      return label;
    case "Noise":
      return "NOISE";
    default:
      return FSK_LIKE.test(label) ? "FSK" : "DIGITAL";
  }
}

/** Held this long after the last burst before the row reads as idle. */
export const STALE_AFTER_S = 2.5;

export type MapContext = {
  /** Receiver centre frequency, for the signal's offset. */
  centerHz: number;
  /** Noise floor to fall back on when the track carries no SNR. */
  noiseFloorDb: number;
  /** Seconds since the track was last seen (server clock). */
  ageSec: number;
  now?: number;
};

export function mapBackendTrack(
  track: BackendTrack,
  ctx: MapContext,
): IdentifiedSignal {
  const unknown = track.label === "unknown";
  const label = displayClassFor(track.label);
  const now = ctx.now ?? Date.now();
  const lastSeen = now - Math.max(0, ctx.ageSec) * 1000;
  const firstSeen =
    track.first_seen != null && track.last_seen != null
      ? lastSeen - (track.last_seen - track.first_seen) * 1000
      : lastSeen;
  const snr =
    track.snr_db ??
    (track.power_dbfs != null && Number.isFinite(ctx.noiseFloorDb)
      ? track.power_dbfs - ctx.noiseFloorDb
      : 0);

  const rf = track.identity_type == null || track.identity_type === "rf_occupancy";
  const matches: SignalIdentity[] = rf
    ? matchCatalog({
        frequencyHz: track.center_freq_hz,
        bandwidthHz: track.bandwidth_hz,
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
      }))
    : [];
  // A narrow signal on a licensed channel: the frequency allocation outranks
  // loosely matching wiki entries, exactly as the backend's band plan reports.
  const plan = rf
    ? bandPlanMatch({
        frequencyHz: track.center_freq_hz,
        bandwidthHz: track.bandwidth_hz,
        binHz: Math.max(500, track.bandwidth_hz / 4),
      })
    : null;
  const identities = plan
    ? [plan.identity, ...matches.filter((m) => m.id !== plan.identity.id)].slice(0, 3)
    : matches;

  return {
    id: `be-${track.id}`,
    frequencyHz: track.center_freq_hz,
    offsetHz: track.center_freq_hz - ctx.centerHz,
    bandwidthHz: track.bandwidth_hz,
    snrDb: snr,
    label,
    confidence: track.confidence,
    scheme: classInfo(label).scheme,
    identities,
    firstSeen,
    lastSeen,
    basis: "backend-cnn",
    trackId: track.id,
    learned: track.learned ?? null,
    userCorrected: track.user_corrected === true,
    customLabel: !isBuiltinClass(track.label),
    backendLabel: track.label,
    unknown,
    evidence: track.evidence,
    alternatives: track.alternatives?.slice(0, 3),
    bandPlanNote: track.band_plan?.label,
    title: track.discovered_name ?? (rf ? undefined : track.name),
    stale: ctx.ageSec > STALE_AFTER_S,
  };
}
