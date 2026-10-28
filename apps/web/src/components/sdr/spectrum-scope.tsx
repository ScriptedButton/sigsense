"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useSpectrumStream } from "@/components/sdr/sdr-provider";
import {
  useSpectrumNav,
  type SpectrumNavHandlers,
} from "@/components/sdr/use-spectrum-nav";
import {
  drawSignalMarkers,
  MARKER_HOVER_ZONE,
  type PlacedMarker,
  type SignalMarker,
} from "@/lib/sdr/signal-id/scope-overlay";
import {
  axisTickStepHz,
  formatAxisHz,
  offsetHzToX,
  type ViewportStore,
  viewSpanHz,
} from "@/lib/sdr/spectrum-viewport";
import { dbRangeFor, type DbRangeId, type NoiseFilterId } from "@/lib/sdr/display-settings";
import { SpectrumNoiseFilter } from "@/lib/sdr/spectrum-noise-filter";

type SpectrumScopeProps = {
  sampleRate: number;
  frequencyHz: number;
  offsetHz: number;
  bandwidthHz: number;
  /** Live viewport shared with the waterfall; pans never touch React state. */
  store: ViewportStore;
  onTuneOffset: (offsetHz: number) => void;
  onCenterOnOffset: (offsetHz: number) => void;
  onPanLo: (deltaHz: number) => void;
  dbRange: DbRangeId;
  noiseFilter?: NoiseFilterId;
  /** Identified signals to annotate over the trace. */
  markers?: SignalMarker[];
  /** Right-click or long-press on a marker: offer to correct that signal. */
  onMarkerCorrect?: (markerId: string) => void;
};

export function SpectrumScope({
  sampleRate,
  frequencyHz,
  offsetHz,
  bandwidthHz,
  store,
  onTuneOffset,
  onCenterOnOffset,
  onPanLo,
  dbRange,
  noiseFilter = "off",
  markers,
  onMarkerCorrect,
}: SpectrumScopeProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const latestRef = useRef<Float32Array | null>(null);
  const centerRef = useRef<number | undefined>(undefined);
  const rafRef = useRef<number>(0);
  const dirtyRef = useRef(true);
  const markersRef = useRef<SignalMarker[]>([]);
  const placedRef = useRef<PlacedMarker[]>([]);
  const [hover, setHover] = useState<{ id: string; x: number; text: string } | null>(
    null,
  );
  const noiseFilterRef = useRef(new SpectrumNoiseFilter());
  const noiseStrengthRef = useRef(noiseFilter);
  const dbRef = useRef(dbRangeFor(dbRange));
  const sampleRateRef = useRef(sampleRate);
  const frequencyRef = useRef(frequencyHz);
  const offsetRef = useRef(offsetHz);
  const bandwidthRef = useRef(bandwidthHz);
  /** Canvas CSS size, kept current by a ResizeObserver (no layout reads in draw). */
  const sizeRef = useRef({ width: 0, height: 0 });

  // Props that affect the trace/overlays; mirrored to refs for the draw loop.
  useEffect(() => {
    markersRef.current = markers ?? [];
    noiseStrengthRef.current = noiseFilter;
    dbRef.current = dbRangeFor(dbRange);
    sampleRateRef.current = sampleRate;
    frequencyRef.current = frequencyHz;
    offsetRef.current = offsetHz;
    bandwidthRef.current = bandwidthHz;
    dirtyRef.current = true;
  }, [bandwidthHz, frequencyHz, offsetHz, sampleRate, dbRange, markers, noiseFilter]);

  // Pans, zooms and inertia arrive here at display rate.
  useEffect(() => store.subscribe(() => {
    dirtyRef.current = true;
  }), [store]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const measure = () => {
      sizeRef.current = { width: canvas.clientWidth, height: canvas.clientHeight };
      dirtyRef.current = true;
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    noiseFilterRef.current.reset();
    dirtyRef.current = true;
  }, [noiseFilter]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const spectrum = latestRef.current;
    if (!canvas || !spectrum) return;
    if (!dirtyRef.current) return;
    dirtyRef.current = false;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const { width, height } = sizeRef.current;
    if (width === 0 || height === 0) return;

    const targetW = Math.floor(width * dpr);
    const targetH = Math.floor(height * dpr);
    if (canvas.width !== targetW || canvas.height !== targetH) {
      canvas.width = targetW;
      canvas.height = targetH;
    }

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const vp = store.get();
    const { minDb, maxDb } = dbRef.current;
    const rate = sampleRateRef.current;
    const freq = frequencyRef.current;
    const offset = offsetRef.current;
    const bandwidth = bandwidthRef.current;
    const span = viewSpanHz(rate, vp.zoom);
    const toX = (off: number) => offsetHzToX(off, width, rate, vp);
    const hzToPx = (hz: number) => (hz / span) * width;

    ctx.fillStyle = "oklch(0.08 0 0)";
    ctx.fillRect(0, 0, width, height);

    // Grid — horizontal always; vertical from frequency ticks
    ctx.strokeStyle = "oklch(0.22 0.01 355)";
    ctx.lineWidth = 1;
    for (let i = 1; i < 4; i++) {
      const y = (height * i) / 4;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }

    const center = centerRef.current ?? freq;
    const leftHz = center + vp.centerOffsetHz - span / 2;
    const rightHz = leftHz + span;
    const step = axisTickStepHz(span);
    const firstTick = Math.ceil(leftHz / step) * step;

    ctx.font = "11px var(--font-mono), ui-monospace, monospace";

    let labelEnd = -Infinity; // right edge of the last label drawn
    for (let f = firstTick; f <= rightHz + 1; f += step) {
      const x = toX(f - center);
      ctx.strokeStyle = "oklch(0.22 0.01 355)";
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();

      const label = formatAxisHz(f);
      const tw = ctx.measureText(label).width;
      const lx = Math.min(Math.max(x - tw / 2, 4), width - tw - 4);
      // Edge clamping can push a label onto its neighbour on narrow screens.
      if (lx < labelEnd + 8) continue;
      labelEnd = lx + tw;
      ctx.fillStyle = "oklch(0.62 0.02 355)";
      ctx.fillText(label, lx, height - 6);
    }

    const n = spectrum.length;
    const half = n / 2;
    const dbSpan = maxDb - minDb;

    // Bandwidth highlight around offset
    const bwPx = hzToPx(bandwidth);
    const offsetPx = toX(offset);
    ctx.fillStyle = "oklch(0.62 0.18 355 / 0.12)";
    ctx.fillRect(offsetPx - bwPx / 2, 0, bwPx, height);

    // LO center marker (only when visible)
    const loX = toX(0);
    if (loX >= -1 && loX <= width + 1) {
      ctx.strokeStyle = "oklch(0.78 0.12 195 / 0.55)";
      ctx.beginPath();
      ctx.moveTo(loX, 0);
      ctx.lineTo(loX, height);
      ctx.stroke();
    }

    ctx.strokeStyle = "oklch(0.70 0.20 355)";
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(offsetPx, 0);
    ctx.lineTo(offsetPx, height);
    ctx.stroke();
    ctx.setLineDash([]);

    // Spectrum line — sample bins across the visible window
    ctx.beginPath();
    ctx.strokeStyle = "oklch(0.82 0.14 195)";
    ctx.lineWidth = 1.5;
    ctx.fillStyle = "oklch(0.78 0.12 195 / 0.18)";

    const leftOffset = vp.centerOffsetHz - span / 2;
    const denom = Math.max(width - 1, 1);
    // Bins per pixel: above 1 the trace takes each pixel's peak so narrow
    // carriers (GMRS, CW) are never skipped between samples.
    const binsPerPx = (span / rate) * (n - 1) / denom;
    for (let px = 0; px < width; px++) {
      const offsetAtX = leftOffset + (px / denom) * span;
      const frac = offsetAtX / rate + 0.5;
      const binFloat = frac * (n - 1);
      const i0 = Math.floor(binFloat);
      let dbRaw: number;
      if (binsPerPx > 1.2) {
        const lo = Math.max(0, Math.round(binFloat - binsPerPx / 2));
        const hi = Math.min(n - 1, Math.round(binFloat + binsPerPx / 2));
        dbRaw = -Infinity;
        for (let b = lo; b <= hi; b++) {
          const v = spectrum[(b + half) % n];
          if (v > dbRaw) dbRaw = v;
        }
      } else {
        const i1 = Math.min(Math.max(i0, 0) + 1, n - 1);
        const t = binFloat - i0;
        // fftshift: display index i reads spectrum[(i + half) % n]
        const shifted0 = spectrum[(((Math.max(i0, 0) % n) + n) % n + half) % n];
        const shifted1 = spectrum[(i1 + half) % n];
        dbRaw = shifted0 * (1 - t) + shifted1 * t;
      }
      const db = Number.isFinite(dbRaw) ? dbRaw : minDb;
      const y = height - ((db - minDb) / dbSpan) * height;
      if (px === 0) ctx.moveTo(px, y);
      else ctx.lineTo(px, y);
    }
    ctx.stroke();
    ctx.lineTo(width, height);
    ctx.lineTo(0, height);
    ctx.closePath();
    ctx.fill();

    ctx.fillStyle = "oklch(0.62 0.02 355)";
    ctx.fillText(`${maxDb} dB`, 6, 14);
    ctx.fillText(`${minDb} dB`, 6, height - 22);

    placedRef.current = drawSignalMarkers(ctx, {
      markers: markersRef.current,
      width,
      height,
      offsetToX: toX,
      hzToPx,
    });
  }, [store]);

  useEffect(() => {
    const loop = () => {
      draw();
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(rafRef.current);
  }, [draw]);

  useSpectrumStream(
    useCallback((spectrum, centerHz) => {
      const filtered = noiseFilterRef.current.apply(
        spectrum,
        noiseStrengthRef.current,
      );
      if (!latestRef.current || latestRef.current.length !== filtered.length) {
        latestRef.current = new Float32Array(filtered.length);
      }
      latestRef.current.set(filtered);
      centerRef.current = centerHz;
      dirtyRef.current = true;
    }, []),
  );

  const navHandlers: SpectrumNavHandlers = {
    onTuneOffset,
    onCenterOnOffset,
    onPanLo,
    store,
    sampleRate,
    offsetHz,
    bandwidthHz,
  };

  const nav = useSpectrumNav(canvasRef, navHandlers);

  const markerAt = (event: React.PointerEvent | React.MouseEvent, canvas: HTMLElement) => {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    return y <= MARKER_HOVER_ZONE
      ? placedRef.current.find((p) => x >= p.left - 2 && x <= p.right + 2)
      : undefined;
  };

  // Long-press (touch/pen) on a marker opens the correction sheet; a mouse uses
  // the context menu. Moving, lifting or a second finger cancels the press.
  const press = useRef<{ timer: number; x: number; y: number } | null>(null);
  const clearPress = () => {
    if (press.current) window.clearTimeout(press.current.timer);
    press.current = null;
  };
  useEffect(() => clearPress, []);
  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    nav.onPointerDown(event);
    clearPress();
    if (!onMarkerCorrect || event.pointerType === "mouse") return;
    const hit = markerAt(event, event.currentTarget);
    if (!hit) return;
    const canvas = event.currentTarget;
    const pointerId = event.pointerId;
    press.current = {
      x: event.clientX,
      y: event.clientY,
      timer: window.setTimeout(() => {
        press.current = null;
        // End the gesture without a tap-to-tune, then offer the correction.
        canvas.dispatchEvent(new PointerEvent("pointercancel", { pointerId, bubbles: true }));
        onMarkerCorrect(hit.marker.id);
      }, 600),
    };
  };
  const onPointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    clearPress();
    nav.onPointerUp(event);
  };
  const onContextMenu = (event: React.MouseEvent<HTMLCanvasElement>) => {
    if (!onMarkerCorrect) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const hit =
      y <= MARKER_HOVER_ZONE
        ? placedRef.current.find((p) => x >= p.left - 2 && x <= p.right + 2)
        : undefined;
    if (!hit) return;
    event.preventDefault();
    onMarkerCorrect(hit.marker.id);
  };

  // Marker details on hover; state only changes when the hovered marker does.
  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    nav.onPointerMove(event);
    const p = press.current;
    if (p && Math.hypot(event.clientX - p.x, event.clientY - p.y) > 10) clearPress();
    if (event.buttons !== 0) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const hit =
      y <= MARKER_HOVER_ZONE
        ? placedRef.current.find((p) => x >= p.left - 2 && x <= p.right + 2)
        : undefined;
    const id = hit?.marker.id ?? null;
    setHover((prev) =>
      prev?.id === id
        ? prev
        : hit
          ? { id: hit.marker.id, x, text: hit.marker.detail ?? hit.marker.label }
          : null,
    );
  };

  return (
    <>
    <canvas
      ref={canvasRef}
      className="absolute inset-0 h-full w-full touch-none"
      role="img"
      aria-label="RF spectrum scope. Click to tune, drag to pan, scroll to zoom, double-click to center."
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerLeave={() => setHover(null)}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onContextMenu={onContextMenu}
      onDoubleClick={nav.onDoubleClick}
    />
    {hover && (
      <div
        className="marker-tooltip"
        role="tooltip"
        style={{ left: Math.max(8, hover.x) }}
      >
        {hover.text}
      </div>
    )}
    </>
  );
}
