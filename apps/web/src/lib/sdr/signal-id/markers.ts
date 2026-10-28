import { formatFrequencyWith } from "../format";
import { classInfo } from "./labels";
import type { IdentifiedSignal } from "./types";
import type { SignalMarker } from "./scope-overlay";

/**
 * Catalog entries that describe a part of another emission (the RDS or pilot
 * inside a broadcast-FM signal, HD Radio sidebands) rather than a signal of
 * their own. Naming a marker after one makes a single transmitter read as
 * several.
 */
const SUB_STRUCTURE =
  /\b(rds|radio data syst\w*|sub-?carrier|sca|pilot|hd radio|iboc|stereo)\b/i;

/** A catalog name is only used when it is a confident, whole-signal match. */
const MIN_CATALOG_SCORE = 0.7;
const MAX_LABEL_CHARS = 28;

function clip(text: string): string {
  return text.length > MAX_LABEL_CHARS ? `${text.slice(0, MAX_LABEL_CHARS - 1)}…` : text;
}

/**
 * Display name for a detected signal: the model/band-plan title first, then
 * the model's class, and a catalog name only when it is a confident match that
 * is not a sub-structure of something larger.
 */
export function markerLabel(signal: IdentifiedSignal): string {
  if (signal.title) return clip(signal.title);
  const best = signal.identities.find(
    (identity) =>
      identity.score >= MIN_CATALOG_SCORE && !SUB_STRUCTURE.test(identity.name),
  );
  if (best) return clip(best.name);
  return signal.backendLabel ?? classInfo(signal.label).short;
}

function detail(signal: IdentifiedSignal, label: string): string {
  const info = classInfo(signal.label);
  const lines = [
    label,
    `${info.name} · ${Math.round(signal.confidence * 100)}%${signal.stale ? " · idle" : ""}`,
    `${formatFrequencyWith(signal.frequencyHz, "auto")} · ${(signal.bandwidthHz / 1000).toFixed(1)} kHz wide · ${signal.snrDb.toFixed(0)} dB SNR`,
  ];
  if (signal.bandPlanNote) lines.push(signal.bandPlanNote);
  const others = signal.identities
    .filter((identity) => identity.name !== label)
    .slice(0, 3)
    .map((identity) => identity.name);
  if (others.length > 0) lines.push(`Also matches: ${others.join(", ")}`);
  return lines.join("\n");
}

/** One marker per detected signal, ranked so the clearest ones win collisions. */
export function buildMarkers(signals: IdentifiedSignal[]): SignalMarker[] {
  return signals.map((signal) => {
    const label = markerLabel(signal);
    const staleFactor = signal.stale ? 0.4 : 1;
    return {
      id: signal.id,
      offsetHz: signal.offsetHz,
      bandwidthHz: signal.bandwidthHz,
      label,
      color: classInfo(signal.label).color,
      priority: (signal.confidence + Math.min(signal.snrDb, 40) / 40) * staleFactor,
      detail: detail(signal, label),
    };
  });
}
