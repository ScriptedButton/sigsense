import { clamp } from "./format";
import {
  cubeHelix,
  makeGradientPalette,
  type Rgb,
} from "./palette";
import {
  isNoiseFilterId,
  NOISE_FILTER_OPTIONS,
  type NoiseFilterId,
} from "./spectrum-noise-filter";
import { isMarkerMode, type MarkerMode } from "./signal-id/scope-overlay";
import { MAX_DB, MIN_DB } from "./types";

export type { NoiseFilterId };
export { NOISE_FILTER_OPTIONS };

export type WaterfallPaletteId =
  | "cubehelix"
  | "thermal"
  | "viridis"
  | "ocean"
  | "mono";

export type WaterfallSpeedId = "slow" | "normal" | "fast";
export type WaterfallDirectionId = "top" | "bottom";
export type DbRangeId = "wide" | "normal" | "tight";
export type FreqTargetId = "tune" | "center";
export type FreqFormatId = "auto" | "mhz" | "khz" | "hz";

export type DisplaySettings = {
  waterfallPalette: WaterfallPaletteId;
  waterfallSpeed: WaterfallSpeedId;
  waterfallDirection: WaterfallDirectionId;
  dbRange: DbRangeId;
  noiseFilter: NoiseFilterId;
  freqTarget: FreqTargetId;
  freqFormat: FreqFormatId;
  /** Which identified-signal markers the scope draws. */
  markerMode: MarkerMode;
};

export const DEFAULT_DISPLAY_SETTINGS: DisplaySettings = {
  waterfallPalette: "cubehelix",
  waterfallSpeed: "normal",
  waterfallDirection: "top",
  dbRange: "normal",
  noiseFilter: "off",
  freqTarget: "tune",
  freqFormat: "auto",
  markerMode: "strongest",
};

export const WATERFALL_PALETTE_OPTIONS: {
  id: WaterfallPaletteId;
  label: string;
}[] = [
  { id: "cubehelix", label: "Cubehelix" },
  { id: "thermal", label: "Thermal" },
  { id: "viridis", label: "Viridis" },
  { id: "ocean", label: "Ocean" },
  { id: "mono", label: "Mono" },
];

export const WATERFALL_SPEED_OPTIONS: {
  id: WaterfallSpeedId;
  label: string;
}[] = [
  { id: "slow", label: "Slow" },
  { id: "normal", label: "Normal" },
  { id: "fast", label: "Fast" },
];

export const DB_RANGE_OPTIONS: {
  id: DbRangeId;
  label: string;
  minDb: number;
  maxDb: number;
}[] = [
  { id: "wide", label: "Wide", minDb: -100, maxDb: -10 },
  { id: "normal", label: "Normal", minDb: MIN_DB, maxDb: MAX_DB },
  { id: "tight", label: "Tight", minDb: -70, maxDb: -25 },
];

export const FREQ_FORMAT_OPTIONS: {
  id: FreqFormatId;
  label: string;
}[] = [
  { id: "auto", label: "Auto" },
  { id: "mhz", label: "MHz" },
  { id: "khz", label: "kHz" },
  { id: "hz", label: "Hz" },
];

const PALETTE_CACHE = new Map<string, Rgb[]>();

function isWaterfallPaletteId(id: unknown): id is WaterfallPaletteId {
  return WATERFALL_PALETTE_OPTIONS.some((o) => o.id === id);
}

export function getWaterfallPalette(id: WaterfallPaletteId | string): Rgb[] {
  const key = isWaterfallPaletteId(id) ? id : "cubehelix";
  const cached = PALETTE_CACHE.get(key);
  if (cached) return cached;

  let palette: Rgb[];
  switch (key) {
    case "thermal":
      palette = makeGradientPalette([
        [0, 0, 0],
        [80, 0, 20],
        [200, 30, 10],
        [255, 140, 0],
        [255, 230, 80],
        [255, 255, 255],
      ]);
      break;
    case "viridis":
      palette = makeGradientPalette([
        [68, 1, 84],
        [59, 82, 139],
        [33, 145, 140],
        [94, 201, 98],
        [253, 231, 37],
      ]);
      break;
    case "ocean":
      palette = makeGradientPalette([
        [0, 8, 24],
        [0, 40, 80],
        [0, 110, 140],
        [40, 200, 180],
        [220, 255, 240],
      ]);
      break;
    case "mono":
      palette = makeGradientPalette([
        [0, 0, 0],
        [255, 255, 255],
      ]);
      break;
    case "cubehelix":
    default:
      palette = cubeHelix();
      break;
  }

  PALETTE_CACHE.set(key, palette);
  return palette;
}

export function dbRangeFor(id: DbRangeId): { minDb: number; maxDb: number } {
  const option = DB_RANGE_OPTIONS.find((o) => o.id === id) ?? DB_RANGE_OPTIONS[1];
  return { minDb: option.minDb, maxDb: option.maxDb };
}

/** Frames to accumulate before writing a waterfall row. */
export function waterfallFrameStride(speed: WaterfallSpeedId): number {
  if (speed === "slow") return 3;
  if (speed === "fast") return 1;
  return 2;
}

/** Rows to write per update (fast scrolls history quicker). */
export function waterfallRowsPerUpdate(speed: WaterfallSpeedId): number {
  return speed === "fast" ? 2 : 1;
}

const STORAGE_KEY = "sdrsense.display-settings";

export function loadDisplaySettings(): DisplaySettings {
  if (typeof window === "undefined") return DEFAULT_DISPLAY_SETTINGS;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_DISPLAY_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<DisplaySettings>;
    const next = { ...DEFAULT_DISPLAY_SETTINGS, ...parsed };
    if (!isNoiseFilterId(next.noiseFilter)) {
      next.noiseFilter = DEFAULT_DISPLAY_SETTINGS.noiseFilter;
    }
    if (!isMarkerMode(next.markerMode)) {
      next.markerMode = DEFAULT_DISPLAY_SETTINGS.markerMode;
    }
    return next;
  } catch {
    return DEFAULT_DISPLAY_SETTINGS;
  }
}

export function saveDisplaySettings(settings: DisplaySettings): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Ignore quota / private-mode failures.
  }
}

export function patchDisplaySettings(
  current: DisplaySettings,
  patch: Partial<DisplaySettings>,
): DisplaySettings {
  const next = { ...current, ...patch };
  saveDisplaySettings(next);
  return next;
}

/** Digit places for the VFO, most-significant first. */
export const FREQ_DIGIT_PLACES = [
  100_000_000, // 100 MHz
  10_000_000,
  1_000_000,
  100_000,
  10_000,
  1_000,
  100,
  10,
  1,
] as const;

export function digitAtPlace(hz: number, place: number): number {
  return Math.floor(Math.abs(hz) / place) % 10;
}

export function nudgeFrequencyPlace(
  hz: number,
  place: number,
  delta: number,
): number {
  return clamp(Math.round(hz + delta * place), 0, 1_766_000_000);
}
