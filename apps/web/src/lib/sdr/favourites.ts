import type { DemodScheme } from "./types";

export type Favourite = {
  id: string;
  label: string;
  frequencyHz: number;
  scheme: DemodScheme;
};

const KEY = "sigsense.favourites";
const MAX = 24;

const listeners = new Set<() => void>();
let cache: Favourite[] | null = null;

function read(): Favourite[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    if (!Array.isArray(raw)) return [];
    return raw.filter(
      (f): f is Favourite =>
        f &&
        typeof f.id === "string" &&
        typeof f.label === "string" &&
        typeof f.frequencyHz === "number" &&
        typeof f.scheme === "string",
    );
  } catch {
    return [];
  }
}

export function getFavourites(): Favourite[] {
  cache ??= typeof window === "undefined" ? [] : read();
  return cache;
}

export function subscribeFavourites(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function commit(next: Favourite[]): void {
  cache = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* kept in memory for this visit */
  }
  listeners.forEach((l) => l());
}

export function labelFor(frequencyHz: number): string {
  const mhz = frequencyHz / 1e6;
  return frequencyHz >= 1e6
    ? `${Number(mhz.toFixed(4))}`
    : `${Number((frequencyHz / 1e3).toFixed(1))}k`;
}

export function addFavourite(frequencyHz: number, scheme: DemodScheme): Favourite {
  const existing = getFavourites().find(
    (f) => Math.abs(f.frequencyHz - frequencyHz) < 500 && f.scheme === scheme,
  );
  if (existing) return existing;
  const fav: Favourite = {
    id: `${Date.now().toString(36)}-${Math.round(frequencyHz)}`,
    label: labelFor(frequencyHz),
    frequencyHz: Math.round(frequencyHz),
    scheme,
  };
  commit([fav, ...getFavourites()].slice(0, MAX));
  return fav;
}

export function removeFavourite(id: string): void {
  commit(getFavourites().filter((f) => f.id !== id));
}
