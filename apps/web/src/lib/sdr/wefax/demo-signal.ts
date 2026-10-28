import type { SampleGenerator } from "@jtarrio/signals/sources/generated.js";

import {
  DEMO_WEFAX_FREQUENCY_HZ,
  WEFAX_APT_START_IOC576_HZ,
  WEFAX_APT_STOP_HZ,
  wefaxLineSeconds,
  wefaxLineWidth,
} from "./types";

const IOC = 576 as const;
const LPM = 120 as const;
const WIDTH = wefaxLineWidth(IOC);
const LINE_SEC = wefaxLineSeconds(LPM);
const BLACK_HZ = 1500;
const WHITE_HZ = 2300;

const START_SEC = 5;
const PHASE_LINES = 12;
const IMAGE_LINES = 160;
const STOP_SEC = 5;
const GAP_SEC = 2;

const PHASE_SEC = PHASE_LINES * LINE_SEC;
const IMAGE_SEC = IMAGE_LINES * LINE_SEC;
const CYCLE_SEC = START_SEC + PHASE_SEC + IMAGE_SEC + STOP_SEC + GAP_SEC;

/**
 * USB-style HF weatherfax — APT start, phasing, chart, stop.
 * Fax tones sit at `carrierHz` + 1.5–2.3 kHz (USB dial = carrier).
 */
export function createDemoWefaxGenerator(
  amplitude = 0.05,
  carrierHz = DEMO_WEFAX_FREQUENCY_HZ,
): SampleGenerator {
  return (sample, rate, _center, I, Q) => {
    fillWefax(sample, rate, I, Q, amplitude, carrierHz);
  };
}

/**
 * One fax at a time: transmits on the published carrier nearest the LO so
 * nearby NOAA freqs in a 1 MHz span don't collide in Demo.
 */
export function createDemoWefaxBank(
  carriersHz: readonly number[],
  amplitude = 0.05,
): SampleGenerator {
  const carriers = carriersHz.length > 0 ? carriersHz : [DEMO_WEFAX_FREQUENCY_HZ];
  return (sample, rate, center, I, Q) => {
    let best = carriers[0]!;
    let bestDist = Math.abs(best - center);
    for (let i = 1; i < carriers.length; i++) {
      const c = carriers[i]!;
      const d = Math.abs(c - center);
      if (d < bestDist) {
        best = c;
        bestDist = d;
      }
    }
    if (bestDist > rate * 0.48) {
      I.fill(0);
      Q.fill(0);
      return;
    }
    fillWefax(sample, rate, I, Q, amplitude, best);
  };
}

function fillWefax(
  sample: number,
  rate: number,
  I: Float32Array,
  Q: Float32Array,
  amplitude: number,
  carrierHz: number,
): void {
  for (let i = 0; i < I.length; i++) {
    const t = ((sample + i) / rate) % CYCLE_SEC;
    const audioHz = audioAt(t);
    if (audioHz <= 0) {
      I[i] = 0;
      Q[i] = 0;
      continue;
    }
    const rfHz = carrierHz + audioHz;
    const phase = (2 * Math.PI * rfHz * (sample + i)) / rate;
    I[i] = amplitude * Math.cos(phase);
    Q[i] = amplitude * Math.sin(phase);
  }
}

function audioAt(t: number): number {
  // ITU APT: alternate black/white at 300 Hz (start) / 450 Hz (stop).
  if (t < START_SEC) return aptAlternate(t, WEFAX_APT_START_IOC576_HZ);

  const afterStart = t - START_SEC;
  if (afterStart < PHASE_SEC) {
    return phasingTone(afterStart);
  }

  const afterPhase = afterStart - PHASE_SEC;
  if (afterPhase < IMAGE_SEC) {
    const line = Math.floor(afterPhase / LINE_SEC);
    const x = (afterPhase % LINE_SEC) / LINE_SEC;
    const level = chartLevel(line, x);
    return BLACK_HZ + level * (WHITE_HZ - BLACK_HZ);
  }

  const afterImage = afterPhase - IMAGE_SEC;
  if (afterImage < STOP_SEC) {
    return aptAlternate(afterImage, WEFAX_APT_STOP_HZ);
  }

  return 0;
}

/** Black↔white FSK square wave at the APT modulation rate. */
function aptAlternate(t: number, rateHz: number): number {
  const phase = (t * rateHz) % 1;
  return phase < 0.5 ? BLACK_HZ : WHITE_HZ;
}

/** Black line with a short white sync pulse at the leading edge. */
function phasingTone(t: number): number {
  const x = (t % LINE_SEC) / LINE_SEC;
  return x < 0.05 ? WHITE_HZ : BLACK_HZ;
}

/**
 * Synthetic weather-chart: frame, latitude grid, gray wash, and a callsign bar.
 */
function chartLevel(line: number, x: number): number {
  // Sync pulse strip (left margin)
  if (x < 0.025) return 1;
  if (x < 0.04) return 0;

  // Border
  if (line < 2 || line >= IMAGE_LINES - 2 || x > 0.98) return 0.15;

  const u = (x - 0.04) / 0.94;
  const v = line / IMAGE_LINES;

  // Lat/lon-ish grid
  const gridX = Math.abs((u * 8) % 1 - 0.5) < 0.02 ? 0.2 : 1;
  const gridY = Math.abs((v * 10) % 1 - 0.5) < 0.015 ? 0.25 : 1;
  let level = Math.min(gridX, gridY);

  // Soft isobar wash
  const wash = 0.35 + 0.45 * Math.sin(u * Math.PI * 3) * Math.cos(v * Math.PI * 2);
  level = Math.min(level, wash);

  // Center title bar (dark on light)
  if (v > 0.42 && v < 0.52 && u > 0.2 && u < 0.8) {
    const glyph = titleGlyph(u, v);
    level = glyph ? 0.08 : 0.92;
  }

  // Scale wedge bottom-left
  if (v > 0.85 && u < 0.25) {
    level = u / 0.25;
  }

  return clamp01(level);
}

/** Crude 5×7 bitmap for "WEFAX" across the title bar. */
function titleGlyph(u: number, v: number): boolean {
  const cols = "WEFAX";
  const local = (u - 0.2) / 0.6;
  const col = Math.min(cols.length - 1, Math.floor(local * cols.length));
  const cx = (local * cols.length) % 1;
  const cy = (v - 0.42) / 0.1;
  const ch = cols[col]!;
  return stamp(ch, cx, cy);
}

function stamp(ch: string, cx: number, cy: number): boolean {
  // Normalized cell with padding
  if (cx < 0.1 || cx > 0.9 || cy < 0.15 || cy > 0.85) return false;
  const x = Math.floor(((cx - 0.1) / 0.8) * 5);
  const y = Math.floor(((cy - 0.15) / 0.7) * 7);
  const bits = GLYPHS[ch];
  if (!bits || x < 0 || x > 4 || y < 0 || y > 6) return false;
  return (bits[y]! & (1 << (4 - x))) !== 0;
}

const GLYPHS: Record<string, number[]> = {
  W: [0b10001, 0b10001, 0b10001, 0b10101, 0b10101, 0b11011, 0b10001],
  E: [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111],
  F: [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b10000],
  A: [0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
  X: [0b10001, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b10001],
};

function clamp01(n: number): number {
  return Math.min(1, Math.max(0, n));
}

/** Exported for offline smoke tests — gray level at chart coordinates. */
export function demoChartLevel(line: number, x: number): number {
  return chartLevel(line, x);
}

export const DEMO_WEFAX_WIDTH = WIDTH;
export const DEMO_WEFAX_IMAGE_LINES = IMAGE_LINES;
export const DEMO_WEFAX_LINE_SEC = LINE_SEC;
