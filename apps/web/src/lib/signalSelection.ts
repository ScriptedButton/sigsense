import type { AnalyzeResult, SignalTrack } from "@/lib/api";

export type FrequencyView = { min: number; max: number };

export function trackSpan(track: SignalTrack): FrequencyView {
  const half = track.bandwidth_hz / 2;
  return {
    min: track.center_freq_hz - half,
    max: track.center_freq_hz + half,
  };
}

export function hzInTrack(hz: number, track: SignalTrack): boolean {
  const { min, max } = trackSpan(track);
  return hz >= min && hz <= max;
}

/** Prefer active observations, then fall back to persisted tracks. */
export function findTrackAtHz(
  hz: number,
  observations: SignalTrack[],
  tracks: SignalTrack[],
): SignalTrack | undefined {
  const pool = [...observations, ...tracks];
  let best: SignalTrack | undefined;
  let bestScore = -1;
  for (const track of pool) {
    if (!hzInTrack(hz, track)) continue;
    const span = trackSpan(track);
    const centerDist = Math.abs(track.center_freq_hz - hz);
    const norm = centerDist / Math.max(1, span.max - span.min);
    const score =
      (track.observations ?? 0) * 2 +
      track.confidence * 10 -
      norm * 5 +
      (observations.some((o) => o.id === track.id) ? 3 : 0);
    if (score > bestScore) {
      bestScore = score;
      best = track;
    }
  }
  return best;
}

export function powerAtHz(
  spectrum: { start_hz: number; bin_hz: number; power_dbfs: (number | null)[] },
  hz: number,
): number | null {
  const idx = Math.round((hz - spectrum.start_hz) / spectrum.bin_hz);
  if (idx < 0 || idx >= spectrum.power_dbfs.length) return null;
  return spectrum.power_dbfs[idx];
}

export type InvestigationSubject =
  | { kind: "track"; track: SignalTrack }
  | { kind: "window"; prediction: AnalyzeResult; tunerCenterHz: number; tunerRateHz: number };

export function investigationFromSelection(
  selected: SignalTrack | undefined,
  prediction: AnalyzeResult | undefined,
  tuner: { center_freq_hz: number; sample_rate_hz: number },
): InvestigationSubject | null {
  if (selected) return { kind: "track", track: selected };
  if (prediction)
    return {
      kind: "window",
      prediction,
      tunerCenterHz: tuner.center_freq_hz,
      tunerRateHz: tuner.sample_rate_hz,
    };
  return null;
}
