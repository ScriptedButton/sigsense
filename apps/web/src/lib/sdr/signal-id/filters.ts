import type { DisplayClass, IdentifiedSignal } from "./types";
import { SIGNAL_CLASSES } from "./types";

/** Modulation classes operators can filter the scan list by. */
export const FILTERABLE_CLASSES: DisplayClass[] = [
  ...SIGNAL_CLASSES.filter((c) => c !== "NOISE"),
  "SSB",
  "NOISE",
];

export type SignalIdFilters = {
  /** Restrict to one modulation class, or show every class. */
  classFilter: DisplayClass | "all";
  /** Drop NOISE / spur rows. */
  hideNoise: boolean;
  /** Keep only rows with a SigID catalog match. */
  identifiedOnly: boolean;
};

export const DEFAULT_SIGNAL_ID_FILTERS: SignalIdFilters = {
  classFilter: "all",
  hideNoise: true,
  identifiedOnly: false,
};

export function applySignalIdFilters(
  signals: IdentifiedSignal[],
  filters: SignalIdFilters,
): IdentifiedSignal[] {
  return signals.filter((signal) => {
    if (filters.hideNoise && signal.label === "NOISE") return false;
    if (filters.identifiedOnly && signal.identities.length === 0) return false;
    if (filters.classFilter !== "all" && signal.label !== filters.classFilter) {
      return false;
    }
    return true;
  });
}
