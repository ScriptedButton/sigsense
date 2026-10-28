/**
 * Upconverters between the antenna and the HackRF. Must match the ids in the
 * API's `sigsense_api/upconverters.py`; the API applies the LO offset, so
 * every frequency in the app stays the real RF frequency.
 */
export type UpconverterId = "none" | "ham-it-up";

export type Upconverter = {
  id: Exclude<UpconverterId, "none">;
  label: string;
  shortLabel: string;
  loHz: number;
  rfMinHz: number;
  rfMaxHz: number;
  /** Where to land when switching on while tuned outside the range. */
  defaultHz: number;
  note: string;
};

export const UPCONVERTERS: readonly Upconverter[] = [
  {
    id: "ham-it-up",
    label: "Nooelec Ham It Up",
    shortLabel: "Ham It Up",
    loHz: 125_000_000,
    rfMinHz: 10_000,
    rfMaxHz: 65_000_000,
    defaultHz: 7_100_000,
    note: "Set the switch on the Ham It Up to upconvert (not bypass) and power it over USB.",
  },
];

export function getUpconverter(id: UpconverterId): Upconverter | null {
  return UPCONVERTERS.find((u) => u.id === id) ?? null;
}

export function inUpconverterRange(u: Upconverter, hz: number): boolean {
  return hz >= u.rfMinHz && hz <= u.rfMaxHz;
}

const STORAGE_KEY = "sigsense.upconverter";
const listeners = new Set<() => void>();

export function subscribeUpconverter(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function readUpconverter(): UpconverterId {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored && getUpconverter(stored as UpconverterId) ? (stored as UpconverterId) : "none";
  } catch {
    return "none";
  }
}

export function writeUpconverter(id: UpconverterId) {
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    /* The choice still applies for this visit when storage is blocked. */
  }
  listeners.forEach((listener) => listener());
}
