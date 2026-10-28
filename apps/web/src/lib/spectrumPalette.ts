export type Rgb = [number, number, number];

const palette = (value: number): Rgb => {
  const stops = [
    [3, 12, 24],
    [12, 39, 80],
    [29, 73, 130],
    [25, 151, 163],
    [65, 221, 184],
    [224, 239, 132],
    [255, 127, 85],
  ];
  const x = Math.max(0, Math.min(0.999, value)) * (stops.length - 1);
  const i = Math.floor(x);
  const t = x - i;
  return stops[i].map((v, j) =>
    Math.round(v * (1 - t) + stops[i + 1][j] * t),
  ) as Rgb;
};

export const EMPTY_WATERFALL: Rgb = [4, 12, 20];

export const WATERFALL_LUT = new Uint8ClampedArray(256 * 3);
for (let i = 0; i < 256; i++) {
  const [r, g, b] = palette(i / 255);
  WATERFALL_LUT[i * 3] = r;
  WATERFALL_LUT[i * 3 + 1] = g;
  WATERFALL_LUT[i * 3 + 2] = b;
}

export const colorFromPower = (
  value: number | null,
  floor: number,
): Rgb => {
  if (value === null) return EMPTY_WATERFALL;
  const t = Math.max(0, Math.min(1, (value - floor) / 85));
  const i = Math.round(t * 255) * 3;
  return [
    WATERFALL_LUT[i],
    WATERFALL_LUT[i + 1],
    WATERFALL_LUT[i + 2],
  ];
};
