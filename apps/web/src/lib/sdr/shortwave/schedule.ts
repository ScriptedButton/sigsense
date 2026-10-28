import { bandById } from "@/lib/sdr/shortwave/bands";
import type {
  ShortwaveEntry,
  ShortwaveFilters,
  ShortwaveLive,
  ShortwaveSchedule,
} from "@/lib/sdr/shortwave/types";

const MINUTES_PER_DAY = 1_440;

/** UTC minute of day 0…1439. */
export function utcMinuteNow(now = new Date()): number {
  return now.getUTCHours() * 60 + now.getUTCMinutes();
}

/** EiBi weekday 1=Mon … 7=Sun. */
export function eibiWeekday(now = new Date()): number {
  const d = now.getUTCDay(); // 0=Sun
  return d === 0 ? 7 : d;
}

/**
 * Whether `days` code includes the given EiBi weekday.
 * Empty = daily. Numeric (e.g. "1245") and Mo/Tu tokens supported.
 * Exotic codes (1.Sa, Last7, Ram, …) return true so the row still appears.
 */
export function daysMatch(days: string, weekday: number): boolean {
  const raw = days.trim();
  if (!raw) return true;

  if (/^[1-7]+$/.test(raw)) {
    return raw.includes(String(weekday));
  }

  const DAY_TOKEN: Record<string, number> = {
    Mo: 1,
    Tu: 2,
    We: 3,
    Th: 4,
    Fr: 5,
    Sa: 6,
    Su: 7,
  };

  const tokens = raw.match(/Mo|Tu|We|Th|Fr|Sa|Su/g);
  if (tokens && tokens.length > 0 && !/[0-9.]/.test(raw)) {
    return tokens.some((t) => DAY_TOKEN[t] === weekday);
  }

  // Special / irregular — don't hide; show in list for manual judgment.
  return true;
}

/** True when `nowMin` falls in [start, end), supporting midnight wrap. */
export function timeInWindow(
  nowMin: number,
  startMin: number,
  endMin: number,
): boolean {
  if (startMin === endMin) return true; // 0000-0000 / 2400 style all-day
  if (endMin === 0 && startMin === 0) return true;
  if (endMin === MINUTES_PER_DAY) {
    return nowMin >= startMin;
  }
  if (startMin < endMin) {
    return nowMin >= startMin && nowMin < endMin;
  }
  // wraps midnight
  return nowMin >= startMin || nowMin < endMin;
}

export function minutesUntilStart(
  nowMin: number,
  startMin: number,
  endMin: number,
): number {
  if (timeInWindow(nowMin, startMin, endMin)) return 0;
  if (startMin >= nowMin) return startMin - nowMin;
  return MINUTES_PER_DAY - nowMin + startMin;
}

export function minutesUntilEnd(
  nowMin: number,
  startMin: number,
  endMin: number,
): number | null {
  if (!timeInWindow(nowMin, startMin, endMin)) return null;
  const end = endMin === 0 || endMin === MINUTES_PER_DAY ? MINUTES_PER_DAY : endMin;
  if (startMin < end) return end - nowMin;
  if (nowMin >= startMin) return MINUTES_PER_DAY - nowMin + (end % MINUTES_PER_DAY);
  return end - nowMin;
}

export function formatUtcHmm(min: number): string {
  const m = ((min % MINUTES_PER_DAY) + MINUTES_PER_DAY) % MINUTES_PER_DAY;
  const hh = String(Math.floor(m / 60)).padStart(2, "0");
  const mm = String(m % 60).padStart(2, "0");
  return `${hh}${mm}`;
}

export function formatWindow(entry: ShortwaveEntry): string {
  return `${formatUtcHmm(entry.a)}–${formatUtcHmm(entry.b)}`;
}

export function formatKHz(kHz: number): string {
  return Number.isInteger(kHz) ? String(kHz) : kHz.toFixed(1);
}

export function entryFrequencyHz(entry: ShortwaveEntry): number {
  return Math.round(entry.f * 1_000);
}

function toLive(
  entry: ShortwaveEntry,
  nowMin: number,
  weekday: number,
): ShortwaveLive | null {
  if (!daysMatch(entry.d, weekday)) return null;
  const onAir = timeInWindow(nowMin, entry.a, entry.b);
  return {
    entry,
    frequencyHz: entryFrequencyHz(entry),
    onAir,
    startsInMin: minutesUntilStart(nowMin, entry.a, entry.b),
    endsInMin: minutesUntilEnd(nowMin, entry.a, entry.b),
  };
}

const SOON_HORIZON_MIN = 180;

/**
 * Filter + rank schedule entries for the explorer list.
 * "now" = currently transmitting; "soon" = starts within 3 h; "all" = match filters only.
 */
export function filterSchedule(
  entries: readonly ShortwaveEntry[],
  filters: ShortwaveFilters,
  now = new Date(),
): ShortwaveLive[] {
  const nowMin = utcMinuteNow(now);
  const weekday = eibiWeekday(now);
  const band = filters.bandId ? bandById(filters.bandId) : null;
  const q = filters.query.trim().toLowerCase();

  const out: ShortwaveLive[] = [];
  for (const entry of entries) {
    if (band && (entry.f < band.loKHz || entry.f > band.hiKHz)) continue;
    if (filters.language && entry.l !== filters.language) continue;
    if (q) {
      const hay = `${entry.n} ${entry.i} ${entry.l} ${entry.f} ${entry.t}`.toLowerCase();
      if (!hay.includes(q)) continue;
    }

    const live = toLive(entry, nowMin, weekday);
    if (!live) continue;

    if (filters.when === "now" && !live.onAir) continue;
    if (
      filters.when === "soon" &&
      (live.onAir || live.startsInMin > SOON_HORIZON_MIN)
    ) {
      continue;
    }

    out.push(live);
  }

  out.sort((a, b) => {
    if (a.onAir !== b.onAir) return a.onAir ? -1 : 1;
    if (filters.when === "soon") {
      if (a.startsInMin !== b.startsInMin) return a.startsInMin - b.startsInMin;
    }
    return a.entry.f - b.entry.f || a.entry.n.localeCompare(b.entry.n);
  });

  return out;
}

export function formatLiveEta(live: ShortwaveLive): string {
  if (live.onAir) {
    if (live.endsInMin == null) return "On air";
    if (live.endsInMin < 60) return `${live.endsInMin}m left`;
    const h = Math.floor(live.endsInMin / 60);
    const m = live.endsInMin % 60;
    return m === 0 ? `${h}h left` : `${h}h ${m}m left`;
  }
  if (live.startsInMin < 60) return `in ${live.startsInMin}m`;
  const h = Math.floor(live.startsInMin / 60);
  const m = live.startsInMin % 60;
  return m === 0 ? `in ${h}h` : `in ${h}h ${m}m`;
}

/**
 * Broadcasts scheduled near `frequencyHz` (EiBi channel match).
 * Ranked on-air first, then closest kHz, then soonest start.
 */
export function lookupAtFrequency(
  entries: readonly ShortwaveEntry[],
  frequencyHz: number,
  now = new Date(),
  toleranceHz = 1_500,
): ShortwaveLive[] {
  const nowMin = utcMinuteNow(now);
  const weekday = eibiWeekday(now);
  const out: ShortwaveLive[] = [];

  for (const entry of entries) {
    const hz = entryFrequencyHz(entry);
    if (Math.abs(hz - frequencyHz) > toleranceHz) continue;
    const live = toLive(entry, nowMin, weekday);
    if (live) out.push(live);
  }

  out.sort((a, b) => {
    if (a.onAir !== b.onAir) return a.onAir ? -1 : 1;
    const da = Math.abs(a.frequencyHz - frequencyHz);
    const db = Math.abs(b.frequencyHz - frequencyHz);
    if (da !== db) return da - db;
    if (a.onAir && b.onAir) {
      return (b.endsInMin ?? 0) - (a.endsInMin ?? 0);
    }
    if (a.startsInMin !== b.startsInMin) return a.startsInMin - b.startsInMin;
    return a.entry.n.localeCompare(b.entry.n);
  });

  return out;
}

let schedulePromise: Promise<ShortwaveSchedule> | null = null;

/** Shared lazy load so header + explorer share one JSON fetch. */
export function loadShortwaveSchedule(): Promise<ShortwaveSchedule> {
  if (!schedulePromise) {
    schedulePromise = import("@/lib/sdr/shortwave/schedule.json").then(
      (mod) => mod.default as ShortwaveSchedule,
    );
  }
  return schedulePromise;
}

/** Distinct language codes present in the schedule, sorted by label-ish code. */
export function collectLanguages(
  entries: readonly ShortwaveEntry[],
): string[] {
  const set = new Set<string>();
  for (const e of entries) {
    if (e.l) set.add(e.l);
  }
  return [...set].sort((a, b) => a.localeCompare(b));
}
