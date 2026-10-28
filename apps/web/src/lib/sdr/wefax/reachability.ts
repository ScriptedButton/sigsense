import {
  isFrequencyOnAir,
  isStationOnAir,
  utcMinuteNow,
} from "@/lib/sdr/wefax/schedule";
import {
  wefaxTuneHz,
  type WefaxFrequency,
  type WefaxStation,
} from "@/lib/sdr/wefax/stations";

export type ReachabilityTier = "good" | "fair" | "poor" | "off";

export type FrequencyReachability = {
  frequency: WefaxFrequency;
  tier: ReachabilityTier;
  /** 0…1 relative score within on-air freqs (0 if off). */
  score: number;
  distanceKm: number;
  reason: string;
};

export type StationReachability = {
  station: WefaxStation;
  onAir: boolean;
  distanceKm: number;
  /** Best on-air frequency, or best overall if all off. */
  best: FrequencyReachability | null;
  frequencies: FrequencyReachability[];
};

/** Continental US centroid fallback when geolocation is unavailable. */
export const DEFAULT_LISTENER = { lat: 39.8, lon: -98.5 };

export function haversineKm(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const R = 6371;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** Rough solar elevation proxy: positive = day at longitude. */
function isDaylightAt(lon: number, now = new Date()): boolean {
  const utcHours = now.getUTCHours() + now.getUTCMinutes() / 60;
  const localSolar = (utcHours + lon / 15 + 24) % 24;
  return localSolar >= 6 && localSolar < 18;
}

/**
 * Preferred HF band (MHz) for path length + day/night — rule-of-thumb only,
 * not a full VOACAP / ionosonde model.
 */
function preferredBandMhz(distanceKm: number, day: boolean): [number, number] {
  if (distanceKm < 500) return day ? [4, 8] : [2, 6];
  if (distanceKm < 2000) return day ? [8, 15] : [4, 10];
  if (distanceKm < 5000) return day ? [10, 18] : [6, 12];
  return day ? [12, 22] : [8, 15];
}

function bandScore(freqMhz: number, lo: number, hi: number): number {
  if (freqMhz >= lo && freqMhz <= hi) return 1;
  const mid = (lo + hi) / 2;
  const half = (hi - lo) / 2 || 1;
  const dist = Math.abs(freqMhz - mid);
  return Math.max(0, 1 - dist / (half * 2.5));
}

function tierFromScore(score: number, onAir: boolean): ReachabilityTier {
  if (!onAir) return "off";
  if (score >= 0.7) return "good";
  if (score >= 0.4) return "fair";
  return "poor";
}

function reasonFor(
  tier: ReachabilityTier,
  day: boolean,
  distanceKm: number,
  lo: number,
  hi: number,
): string {
  if (tier === "off") return "Not on air";
  const path =
    distanceKm < 500
      ? "short"
      : distanceKm < 2000
        ? "regional"
        : distanceKm < 5000
          ? "medium"
          : "long";
  const light = day ? "day" : "night";
  if (tier === "good") return `Good ${path} ${light} · aim ${lo}–${hi} MHz`;
  if (tier === "fair") return `Fair ${path} ${light} · prefer ${lo}–${hi} MHz`;
  return `Poor ${path} ${light} · try ${lo}–${hi} MHz`;
}

export function scoreFrequency(
  station: WefaxStation,
  frequency: WefaxFrequency,
  listenerLat: number,
  listenerLon: number,
  now = new Date(),
): FrequencyReachability {
  const nowMin = utcMinuteNow(now);
  const distanceKm = haversineKm(
    listenerLat,
    listenerLon,
    station.lat,
    station.lon,
  );
  const onAir = isFrequencyOnAir(frequency, station, nowMin);
  const midLon = (listenerLon + station.lon) / 2;
  const day = isDaylightAt(midLon, now);
  const [lo, hi] = preferredBandMhz(distanceKm, day);
  const freqMhz = frequency.assignedHz / 1e6;
  const raw = bandScore(freqMhz, lo, hi);
  const score = onAir ? raw : 0;
  const tier = tierFromScore(raw, onAir);
  return {
    frequency,
    tier,
    score,
    distanceKm,
    reason: reasonFor(tier, day, distanceKm, lo, hi),
  };
}

export function scoreStation(
  station: WefaxStation,
  listenerLat: number,
  listenerLon: number,
  now = new Date(),
): StationReachability {
  const nowMin = utcMinuteNow(now);
  const onAir = isStationOnAir(station, nowMin);
  const distanceKm = haversineKm(
    listenerLat,
    listenerLon,
    station.lat,
    station.lon,
  );
  const frequencies = station.frequencies.map((f) =>
    scoreFrequency(station, f, listenerLat, listenerLon, now),
  );
  const onAirFreqs = frequencies.filter((f) => f.tier !== "off");
  const pool = onAirFreqs.length > 0 ? onAirFreqs : frequencies;
  const best =
    pool.length === 0
      ? null
      : pool.reduce((a, b) => (b.score >= a.score ? b : a));

  return { station, onAir, distanceKm, best, frequencies };
}

export function bestTuneForStation(
  station: WefaxStation,
  listenerLat: number,
  listenerLon: number,
  now = new Date(),
): { assignedHz: number; tuneHz: number; reach: FrequencyReachability } | null {
  const scored = scoreStation(station, listenerLat, listenerLon, now);
  if (!scored.best) return null;
  const assignedHz = scored.best.frequency.assignedHz;
  return {
    assignedHz,
    tuneHz: wefaxTuneHz(assignedHz),
    reach: scored.best,
  };
}

export const REACH_TIER_LABEL: Record<ReachabilityTier, string> = {
  good: "Good",
  fair: "Fair",
  poor: "Poor",
  off: "Off",
};
