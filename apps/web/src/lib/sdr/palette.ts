/** Color palettes for waterfall / spectrometer intensity. */
export type Rgb = [number, number, number];

export function cubeHelix(
  start = 2,
  rotations = 1,
  hue = 3,
  gamma = 1,
): Rgb[] {
  const palette: Rgb[] = new Array(256);
  for (let i = 0; i < 256; i++) {
    const fraction = i / 255;
    const g = Math.pow(fraction, gamma);
    const angle = 2 * Math.PI * (start / 3 + rotations * fraction);
    const amp = (hue * g * (1 - g)) / 2;
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    const r = g + amp * (-0.14861 * cos + 1.78277 * sin);
    const gr = g + amp * (-0.29227 * cos - 0.90649 * sin);
    const b = g + amp * 1.97294 * cos;
    palette[i] = [
      Math.floor(Math.max(0, Math.min(255, 256 * r))),
      Math.floor(Math.max(0, Math.min(255, 256 * gr))),
      Math.floor(Math.max(0, Math.min(255, 256 * b))),
    ];
  }
  return palette;
}

/** Interpolate a short list of stop colors into a 256-entry LUT. */
export function makeGradientPalette(stops: Rgb[]): Rgb[] {
  if (stops.length === 0) return cubeHelix();
  if (stops.length === 1) return new Array(256).fill(stops[0]);

  const palette: Rgb[] = new Array(256);
  const last = stops.length - 1;
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    const pos = t * last;
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, last);
    const f = pos - i0;
    const a = stops[i0];
    const b = stops[i1];
    palette[i] = [
      Math.round(a[0] + (b[0] - a[0]) * f),
      Math.round(a[1] + (b[1] - a[1]) * f),
      Math.round(a[2] + (b[2] - a[2]) * f),
    ];
  }
  return palette;
}

export const WATERFALL_PALETTE = cubeHelix();

export function dbToPaletteIndex(
  db: number,
  minDb: number,
  maxDb: number,
): number {
  if (!Number.isFinite(db)) return 0;
  const t = (db - minDb) / (maxDb - minDb);
  return Math.max(0, Math.min(255, Math.floor(t * 255)));
}
