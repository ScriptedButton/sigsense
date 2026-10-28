import {
  WEFAX_CHART_DURATION_MIN,
  WEFAX_STATIONS,
  type UtcMinute,
  type WefaxFreqWindow,
  type WefaxFrequency,
  type WefaxProduct,
  type WefaxStation,
} from "@/lib/sdr/wefax/stations";

export function utcMinuteNow(now = new Date()): UtcMinute {
  return now.getUTCHours() * 60 + now.getUTCMinutes();
}

export function formatUtcHmm(min: UtcMinute): string {
  const m = ((min % 1440) + 1440) % 1440;
  const hh = Math.floor(m / 60);
  const mm = m % 60;
  return `${String(hh).padStart(2, "0")}${String(mm).padStart(2, "0")}Z`;
}

/** True if `t` is inside [start, end), wrapping midnight. */
export function inUtcWindow(
  t: UtcMinute,
  window: WefaxFreqWindow,
): boolean {
  const { startMin, endMin } = window;
  if (startMin === endMin) return true;
  if (startMin < endMin) return t >= startMin && t < endMin;
  return t >= startMin || t < endMin;
}

export function inAnyUtcWindow(
  t: UtcMinute,
  windows: readonly WefaxFreqWindow[],
): boolean {
  return windows.some((w) => inUtcWindow(t, w));
}

export function isStationOnAir(
  station: WefaxStation,
  nowMin = utcMinuteNow(),
): boolean {
  return inAnyUtcWindow(nowMin, station.windows);
}

export function isFrequencyOnAir(
  freq: WefaxFrequency,
  station: WefaxStation,
  nowMin = utcMinuteNow(),
): boolean {
  if (!isStationOnAir(station, nowMin)) return false;
  if (!freq.windows) return true;
  return inAnyUtcWindow(nowMin, freq.windows);
}

export type WefaxLiveProduct = {
  station: WefaxStation;
  product: WefaxProduct;
  /** Minutes until start (0 if currently transmitting). */
  minutesUntil: number;
  transmitting: boolean;
};

function minutesUntilStart(nowMin: UtcMinute, startMin: UtcMinute): number {
  return (startMin - nowMin + 1440) % 1440;
}

function isTransmitting(nowMin: UtcMinute, startMin: UtcMinute): boolean {
  const elapsed = (nowMin - startMin + 1440) % 1440;
  return elapsed < WEFAX_CHART_DURATION_MIN;
}

function elapsedSinceStart(nowMin: UtcMinute, startMin: UtcMinute): number {
  return (nowMin - startMin + 1440) % 1440;
}

/** Prefer live charts (most recently started), else soonest upcoming. */
function isBetterLive(
  candidate: WefaxLiveProduct,
  best: WefaxLiveProduct,
  nowMin: UtcMinute,
): boolean {
  if (candidate.transmitting !== best.transmitting) {
    return candidate.transmitting;
  }
  if (candidate.transmitting) {
    return (
      elapsedSinceStart(nowMin, candidate.product.startMin) <
      elapsedSinceStart(nowMin, best.product.startMin)
    );
  }
  if (candidate.minutesUntil !== best.minutesUntil) {
    return candidate.minutesUntil < best.minutesUntil;
  }
  return candidate.product.startMin < best.product.startMin;
}

/** Current chart (if any) or the next upcoming product for a station. */
export function nextProductForStation(
  station: WefaxStation,
  nowMin = utcMinuteNow(),
): WefaxLiveProduct | null {
  if (station.products.length === 0) return null;

  let best: WefaxLiveProduct | null = null;
  for (const product of station.products) {
    const transmitting = isTransmitting(nowMin, product.startMin);
    const minutesUntil = transmitting
      ? 0
      : minutesUntilStart(nowMin, product.startMin);
    const candidate: WefaxLiveProduct = {
      station,
      product,
      minutesUntil,
      transmitting,
    };
    if (!best || isBetterLive(candidate, best, nowMin)) best = candidate;
  }
  return best;
}

/** Soonest live/next product across all stations. */
export function nextWefaxWorldwide(
  nowMin = utcMinuteNow(),
): WefaxLiveProduct | null {
  let best: WefaxLiveProduct | null = null;
  for (const station of WEFAX_STATIONS) {
    const next = nextProductForStation(station, nowMin);
    if (!next) continue;
    if (!best || isBetterLive(next, best, nowMin)) best = next;
  }
  return best;
}

/** Upcoming products in the next `horizonMin` minutes (includes current). */
export function upcomingProducts(
  station: WefaxStation,
  horizonMin = 180,
  nowMin = utcMinuteNow(),
): WefaxLiveProduct[] {
  const out: WefaxLiveProduct[] = [];
  for (const product of station.products) {
    const transmitting = isTransmitting(nowMin, product.startMin);
    const minutesUntil = transmitting
      ? 0
      : minutesUntilStart(nowMin, product.startMin);
    if (minutesUntil > horizonMin && !transmitting) continue;
    out.push({ station, product, minutesUntil, transmitting });
  }
  out.sort((a, b) => {
    if (a.transmitting !== b.transmitting) return a.transmitting ? -1 : 1;
    return a.minutesUntil - b.minutesUntil;
  });
  return out;
}

export function formatCountdown(minutesUntil: number): string {
  if (minutesUntil <= 0) return "now";
  if (minutesUntil < 60) return `${minutesUntil}m`;
  const h = Math.floor(minutesUntil / 60);
  const m = minutesUntil % 60;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}
