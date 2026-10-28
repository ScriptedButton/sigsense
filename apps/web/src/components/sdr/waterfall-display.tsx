"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useSdrState, useSpectrumStream } from "@/components/sdr/sdr-provider";
import {
  useSpectrumNav,
  type SpectrumNavHandlers,
} from "@/components/sdr/use-spectrum-nav";
import {
  dbRangeFor,
  getWaterfallPalette,
  waterfallFrameStride,
  waterfallRowsPerUpdate,
  type DbRangeId,
  type NoiseFilterId,
  type WaterfallDirectionId,
  type WaterfallPaletteId,
  type WaterfallSpeedId,
} from "@/lib/sdr/display-settings";
import { SpectrumNoiseFilter } from "@/lib/sdr/spectrum-noise-filter";
import {
  offsetHzToX,
  type ViewportStore,
  viewSpanHz,
} from "@/lib/sdr/spectrum-viewport";

type WaterfallDisplayProps = {
  sampleRate: number;
  frequencyHz: number;
  offsetHz: number;
  bandwidthHz: number;
  /** Live viewport shared with the scope; pans never touch React state. */
  store: ViewportStore;
  onTuneOffset: (offsetHz: number) => void;
  onCenterOnOffset: (offsetHz: number) => void;
  onPanLo: (deltaHz: number) => void;
  paletteId: WaterfallPaletteId;
  speed: WaterfallSpeedId;
  direction: WaterfallDirectionId;
  dbRange: DbRangeId;
  noiseFilter?: NoiseFilterId;
};

/** Spectrum rows kept for re-rendering after a pan, zoom or palette change. */
const MAX_ROWS = 1024;
const MIN_ROWS = 256;
/** Cap on stored floats (about 32 MB) so very wide FFTs keep fewer rows. */
const MAX_RING_FLOATS = 8_000_000;
const BLACK = 0xff000000;

/** Rows are kept as raw dB at source resolution, newest overwriting oldest. */
type RowRing = {
  n: number;
  capacity: number;
  data: Float32Array;
  rates: Float64Array;
  head: number;
  count: number;
};

function createRing(n: number): RowRing {
  const capacity = Math.min(
    MAX_ROWS,
    Math.max(MIN_ROWS, Math.floor(MAX_RING_FLOATS / n)),
  );
  return {
    n,
    capacity,
    data: new Float32Array(capacity * n),
    rates: new Float64Array(capacity),
    head: 0,
    count: 0,
  };
}

function pushRow(ring: RowRing, row: Float32Array, rate: number): void {
  const slot = ring.head;
  ring.data.set(row, slot * ring.n);
  ring.rates[slot] = rate;
  ring.head = (slot + 1) % ring.capacity;
  if (ring.count < ring.capacity) ring.count++;
}

/** Palette as packed little-endian RGBA so a pixel is one 32-bit write. */
function buildLut(id: WaterfallPaletteId): Uint32Array {
  const palette = getWaterfallPalette(id);
  const lut = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    const [r, g, b] = palette[i];
    lut[i] = (255 << 24) | (b << 16) | (g << 8) | r;
  }
  return lut;
}

/**
 * Waterfall uses CSS-pixel resolution (not devicePixelRatio). Heatmap rows
 * don't benefit from retina density.
 *
 * History is stored as raw spectrum rows, not pixels. Panning and zooming
 * re-sample those rows (and keep the past visible) instead of blanking the
 * waterfall; new rows just scroll the existing bitmap.
 */
export function WaterfallDisplay({
  sampleRate,
  frequencyHz,
  offsetHz,
  bandwidthHz,
  store,
  onTuneOffset,
  onCenterOnOffset,
  onPanLo,
  paletteId,
  speed,
  direction,
  dbRange,
  noiseFilter = "off",
}: WaterfallDisplayProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imageRef = useRef<ImageData | null>(null);
  const pixelsRef = useRef<Uint32Array | null>(null);
  const ringRef = useRef<RowRing | null>(null);
  const sizeRef = useRef({ width: 0, height: 0 });
  const needFullRef = useRef(true);
  const pendingRowsRef = useRef(0);
  const frameSkipRef = useRef(0);
  const accumRef = useRef<Float32Array | null>(null);
  const hasAccumRef = useRef(false);
  const rafRef = useRef(0);
  const [noiseFilterEngine] = useState(() => new SpectrumNoiseFilter());

  const sampleRateRef = useRef(sampleRate);
  const offsetRef = useRef(offsetHz);
  const bandwidthRef = useRef(bandwidthHz);
  const noiseStrengthRef = useRef(noiseFilter);
  const lutRef = useRef<Uint32Array>(buildLut(paletteId));
  const speedRef = useRef(speed);
  const directionRef = useRef(direction);
  const dbRef = useRef(dbRangeFor(dbRange));
  // HackRF frames arrive at a few per second, so every one is a waterfall row.
  const sparseSource = useSdrState().connection === "hackrf";
  const sparseRef = useRef(sparseSource);

  // Per-column source-bin ranges for the current view and row rate.
  const mapLoRef = useRef(new Int32Array(0));
  const mapHiRef = useRef(new Int32Array(0));
  const mapKeyRef = useRef({
    width: -1,
    n: -1,
    rate: -1,
    zoom: -1,
    center: NaN,
  });

  const rebuildMap = useCallback(
    (width: number, n: number, rate: number): void => {
      const vp = store.get();
      const key = mapKeyRef.current;
      if (
        key.width === width &&
        key.n === n &&
        key.rate === rate &&
        key.zoom === vp.zoom &&
        key.center === vp.centerOffsetHz
      ) {
        return;
      }
      key.width = width;
      key.n = n;
      key.rate = rate;
      key.zoom = vp.zoom;
      key.center = vp.centerOffsetHz;
      if (mapLoRef.current.length !== width) {
        mapLoRef.current = new Int32Array(width);
        mapHiRef.current = new Int32Array(width);
      }
      const lo = mapLoRef.current;
      const hi = mapHiRef.current;
      const span = viewSpanHz(rate, vp.zoom);
      const leftOffset = vp.centerOffsetHz - span / 2;
      const denom = Math.max(width - 1, 1);
      // Above ~1 bin per pixel take each column's peak so narrow carriers
      // survive zooming out instead of falling between samples.
      const binsPerPx = ((span / rate) * (n - 1)) / denom;
      for (let x = 0; x < width; x++) {
        const frac = (leftOffset + (x / denom) * span) / rate + 0.5;
        if (frac < 0 || frac > 1) {
          lo[x] = -1; // outside what this row captured
          hi[x] = -1;
          continue;
        }
        const bin = frac * (n - 1);
        if (binsPerPx > 1.2) {
          lo[x] = Math.max(0, Math.round(bin - binsPerPx / 2));
          hi[x] = Math.min(n - 1, Math.round(bin + binsPerPx / 2));
        } else {
          lo[x] = hi[x] = Math.min(n - 1, Math.floor(bin));
        }
      }
    },
    [store],
  );

  /** Rasterise ring row `age` (0 = newest) into pixel row `y`. */
  const renderRow = useCallback(
    (ring: RowRing, age: number, y: number, width: number): void => {
      const pixels = pixelsRef.current;
      if (!pixels) return;
      const base = y * width;
      if (age >= ring.count) {
        pixels.fill(BLACK, base, base + width);
        return;
      }
      const slot = (ring.head - 1 - age + ring.capacity * 2) % ring.capacity;
      const n = ring.n;
      const half = n >> 1;
      const offset = slot * n;
      rebuildMap(width, n, ring.rates[slot]);
      const lo = mapLoRef.current;
      const hi = mapHiRef.current;
      const lut = lutRef.current;
      const { minDb, maxDb } = dbRef.current;
      const scale = 255 / (maxDb - minDb);
      const data = ring.data;
      for (let x = 0; x < width; x++) {
        const a = lo[x];
        if (a < 0) {
          pixels[base + x] = BLACK;
          continue;
        }
        const b = hi[x];
        let db = data[offset + ((a + half) % n)];
        for (let i = a + 1; i <= b; i++) {
          const v = data[offset + ((i + half) % n)];
          if (v > db) db = v;
        }
        let idx = Number.isFinite(db) ? Math.floor((db - minDb) * scale) : 0;
        idx = idx < 0 ? 0 : idx > 255 ? 255 : idx;
        pixels[base + x] = lut[idx];
      }
    },
    [rebuildMap],
  );

  const paint = useCallback(() => {
    rafRef.current = 0;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const { width, height } = sizeRef.current;
    if (width < 2 || height < 2) return;

    if (
      !imageRef.current ||
      imageRef.current.width !== width ||
      imageRef.current.height !== height
    ) {
      imageRef.current = new ImageData(width, height);
      pixelsRef.current = new Uint32Array(imageRef.current.data.buffer);
      needFullRef.current = true;
    }
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    const ctx = canvas.getContext("2d");
    const pixels = pixelsRef.current;
    const image = imageRef.current;
    if (!ctx || !pixels || !image) return;

    const ring = ringRef.current;
    const newestAtTop = directionRef.current === "top";

    if (needFullRef.current) {
      needFullRef.current = false;
      pendingRowsRef.current = 0;
      for (let y = 0; y < height; y++) {
        const age = newestAtTop ? y : height - 1 - y;
        if (ring) renderRow(ring, age, y, width);
        else pixels.fill(BLACK, y * width, (y + 1) * width);
      }
    } else if (pendingRowsRef.current > 0 && ring) {
      const rows = Math.min(pendingRowsRef.current, height);
      pendingRowsRef.current = 0;
      // Scroll the existing bitmap, then draw only the new rows.
      if (newestAtTop) {
        pixels.copyWithin(width * rows, 0, width * (height - rows));
        for (let r = 0; r < rows; r++) renderRow(ring, r, r, width);
      } else {
        pixels.copyWithin(0, width * rows, width * height);
        for (let r = 0; r < rows; r++) {
          renderRow(ring, r, height - 1 - r, width);
        }
      }
    }

    ctx.putImageData(image, 0, 0);

    const rate = sampleRateRef.current;
    const vp = store.get();
    const span = viewSpanHz(rate, vp.zoom);
    const bwPx = (bandwidthRef.current / span) * width;
    const offsetPx = offsetHzToX(offsetRef.current, width, rate, vp);
    const loX = offsetHzToX(0, width, rate, vp);

    ctx.fillStyle = "oklch(0.62 0.18 355 / 0.08)";
    ctx.fillRect(offsetPx - bwPx / 2, 0, bwPx, height);

    if (loX >= -1 && loX <= width + 1) {
      ctx.strokeStyle = "oklch(0.78 0.12 195 / 0.4)";
      ctx.beginPath();
      ctx.moveTo(loX, 0);
      ctx.lineTo(loX, height);
      ctx.stroke();
    }

    ctx.strokeStyle = "oklch(0.70 0.20 355 / 0.7)";
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(offsetPx, 0);
    ctx.lineTo(offsetPx, height);
    ctx.stroke();
    ctx.setLineDash([]);
  }, [renderRow, store]);

  const schedulePaint = useCallback(() => {
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(paint);
  }, [paint]);

  const redraw = useCallback(
    (full: boolean) => {
      if (full) needFullRef.current = true;
      schedulePaint();
    },
    [schedulePaint],
  );

  const clearHistory = useCallback(() => {
    ringRef.current = null;
    frameSkipRef.current = 0;
    accumRef.current = null;
    hasAccumRef.current = false;
    redraw(true);
  }, [redraw]);

  useSpectrumStream(
    useCallback(
      (spectrum) => {
        // A backgrounded phone tab does no drawing; skip the filter work too.
        if (typeof document !== "undefined" && document.hidden) return;
        const filtered = noiseFilterEngine.apply(
          spectrum,
          noiseStrengthRef.current,
        );
        // Peak-hold across skipped frames for slow mode.
        if (!accumRef.current || accumRef.current.length !== filtered.length) {
          accumRef.current = new Float32Array(filtered.length);
          accumRef.current.set(filtered);
          hasAccumRef.current = true;
        } else if (!hasAccumRef.current) {
          accumRef.current.set(filtered);
          hasAccumRef.current = true;
        } else {
          const acc = accumRef.current;
          for (let i = 0; i < filtered.length; i++) {
            const v = filtered[i];
            if (v > acc[i]) acc[i] = v;
          }
        }

        frameSkipRef.current += 1;
        if (
          !sparseRef.current &&
          frameSkipRef.current < waterfallFrameStride(speedRef.current)
        ) {
          return;
        }
        frameSkipRef.current = 0;

        const row = accumRef.current;
        let ring = ringRef.current;
        if (!ring || ring.n !== row.length) {
          ring = createRing(row.length);
          ringRef.current = ring;
          needFullRef.current = true;
        }
        const rows = waterfallRowsPerUpdate(speedRef.current);
        for (let r = 0; r < rows; r++) pushRow(ring, row, sampleRateRef.current);
        pendingRowsRef.current += rows;

        // Consume this stride window so the next frames start a fresh peak-hold.
        hasAccumRef.current = false;
        schedulePaint();
      },
      [noiseFilterEngine, schedulePaint],
    ),
  );

  // Mirror props into refs for the paint path, then repaint what changed.
  useEffect(() => {
    sampleRateRef.current = sampleRate;
    offsetRef.current = offsetHz;
    bandwidthRef.current = bandwidthHz;
    speedRef.current = speed;
    sparseRef.current = sparseSource;
    redraw(false);
  }, [sampleRate, offsetHz, bandwidthHz, speed, sparseSource, redraw]);

  // Look changes re-render the stored rows; history is kept.
  useEffect(() => {
    lutRef.current = buildLut(paletteId);
    directionRef.current = direction;
    dbRef.current = dbRangeFor(dbRange);
    redraw(true);
  }, [paletteId, direction, dbRange, redraw]);

  // The noise filter bakes into stored rows, so a change starts fresh.
  const noiseKey = useRef(noiseFilter);
  useEffect(() => {
    noiseStrengthRef.current = noiseFilter;
    if (noiseKey.current === noiseFilter) return;
    noiseKey.current = noiseFilter;
    noiseFilterEngine.reset();
    clearHistory();
  }, [noiseFilter, noiseFilterEngine, clearHistory]);

  // Pans, zooms and inertia re-sample history without touching React.
  useEffect(() => store.subscribe(() => redraw(true)), [store, redraw]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const measure = () => {
      sizeRef.current = {
        width: Math.max(1, Math.floor(canvas.clientWidth)),
        height: Math.max(1, Math.floor(canvas.clientHeight)),
      };
      redraw(true);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(canvas);
    return () => {
      observer.disconnect();
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    };
  }, [redraw]);

  const navHandlers: SpectrumNavHandlers = {
    store,
    onTuneOffset,
    onCenterOnOffset,
    onPanLo,
    sampleRate,
    offsetHz,
    bandwidthHz,
  };

  const nav = useSpectrumNav(canvasRef, navHandlers);

  // frequencyHz only labels the scope axis; the waterfall plots LO offsets.
  void frequencyHz;

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 h-full w-full touch-none outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
      role="img"
      tabIndex={nav.tabIndex}
      aria-label="RF waterfall. Click to tune, drag to pan, scroll to zoom, double-click to center."
      onPointerDown={nav.onPointerDown}
      onPointerMove={nav.onPointerMove}
      onPointerUp={nav.onPointerUp}
      onPointerCancel={nav.onPointerUp}
      onDoubleClick={nav.onDoubleClick}
    />
  );
}
