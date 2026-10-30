import type { ReceiverSpectrum } from "../receiver-control/protocol";

export type SurveyCarrier = { frequencyHz: number; prominenceDb: number; sidebandExcessDb: number; score: number; recommendedBandwidthHz?: number };

/** A listening-order heuristic, not an identification or a reason to hide off-channel signals. */
export function nearBroadcastChannel(frequencyHz: number): boolean {
  return Math.abs(frequencyHz - Math.round(frequencyHz / 5000) * 5000) <= 750;
}
export function rankListeningCandidates<T extends SurveyCarrier>(candidates: readonly T[]): T[] {
  const tier = (c: T) => c.sidebandExcessDb > 1.5 ? (nearBroadcastChannel(c.frequencyHz) ? 0 : 1) : 2;
  return [...candidates].filter((c) => [c.frequencyHz, c.score, c.sidebandExcessDb, c.prominenceDb].every(Number.isFinite))
    .sort((a, b) => tier(a) - tier(b) || b.score - a.score || a.frequencyHz - b.frequencyHz);
}

/** Prefer a narrower AM passband when a distinct strong neighbor lies 4–8 kHz away. */
function recommendedBandwidth(values: number[], index: number, binHz: number, floor: number): number {
  const peak = values[index];
  for (let k = Math.ceil(4000 / binHz); k <= Math.floor(8000 / binHz); k++) {
    for (const side of [-1, 1]) {
      const at = index + side * k, value = values[at];
      if (Number.isFinite(value) && value > floor + 10 && value > peak - 12 && value > values[at - 1] && value >= values[at + 1]) return 4000;
    }
  }
  return 6000;
}

/** Rank AM candidates by both carrier and occupied sidebands, not peak height alone. */
export function surveyCarriers(spectrum: ReceiverSpectrum, lowHz: number, highHz: number): SurveyCarrier[] {
  const { values, binHz } = spectrum;
  if (!(binHz > 0) || binHz > 1_000 || values.length < 64) return [];
  const start = spectrum.startHz ?? spectrum.centerHz + spectrum.centerOffsetHz - Math.floor(values.length / 2) * binHz;
  const radius = Math.max(12, Math.ceil(12_000 / binHz));
  const candidates: SurveyCarrier[] = [];
  for (let index = radius; index < values.length - radius; index++) {
    const frequencyHz = start + index * binHz, peak = values[index];
    if (frequencyHz < lowHz || frequencyHz > highHz || Math.abs(frequencyHz - spectrum.centerHz) < 3_000) continue;
    if (!Number.isFinite(peak) || peak <= values[index - 1] || peak < values[index + 1]) continue;
    const floorBins: number[] = [], speechBins: number[] = [];
    for (let k = index - radius; k <= index + radius; k++) {
      const delta = Math.abs(k - index) * binHz, db = values[k];
      if (!Number.isFinite(db)) continue;
      if (delta >= 4_000 && delta <= 12_000) floorBins.push(db);
      if (delta >= 400 && delta <= 2_800) speechBins.push(db);
    }
    if (floorBins.length < 8 || speechBins.length < 4) continue;
    floorBins.sort((a, b) => a - b);
    const floor = floorBins[Math.floor(floorBins.length / 2)];
    const sidebandDb = 10 * Math.log10(speechBins.reduce((sum, db) => sum + 10 ** (db / 10), 0) / speechBins.length);
    const prominenceDb = peak - floor, sidebandExcessDb = sidebandDb - floor;
    if (prominenceDb < 10) continue;
    const score = Math.min(prominenceDb, 40) + Math.min(20, sidebandExcessDb) * 2;
    candidates.push({ frequencyHz: Math.round(frequencyHz), prominenceDb, sidebandExcessDb, score,
      recommendedBandwidthHz: recommendedBandwidth(values, index, binHz, floor) });
  }
  candidates.sort((a, b) => b.score - a.score);
  const distinct: SurveyCarrier[] = [];
  for (const candidate of candidates) {
    if (distinct.every((other) => Math.abs(candidate.frequencyHz - other.frequencyHz) >= 4_000)) distinct.push(candidate);
    if (distinct.length === 12) break;
  }
  return distinct;
}

/** A real RF frequency persists after an LO shift; DC/IF artifacts move with the LO. */
export function confirmSurveyCarriers(first: readonly SurveyCarrier[], second: readonly SurveyCarrier[], toleranceHz: number): SurveyCarrier[] {
  return first.flatMap((candidate) => {
    const match = second.filter((other) => Math.abs(other.frequencyHz - candidate.frequencyHz) <= toleranceHz)
      .sort((a, b) => Math.abs(a.frequencyHz - candidate.frequencyHz) - Math.abs(b.frequencyHz - candidate.frequencyHz))[0];
    if (!match) return [];
    return [{ frequencyHz: Math.round((candidate.frequencyHz + match.frequencyHz) / 2),
      prominenceDb: Math.min(candidate.prominenceDb, match.prominenceDb),
      sidebandExcessDb: Math.min(candidate.sidebandExcessDb, match.sidebandExcessDb),
      score: Math.min(candidate.score, match.score),
      recommendedBandwidthHz: Math.min(candidate.recommendedBandwidthHz ?? 6000, match.recommendedBandwidthHz ?? 6000) }];
  }).sort((a, b) => b.score - a.score);
}
