import { clamp } from "./format";

/** Visible window over the FFT span. Zoom 1 = full sample-rate width. */
export type SpectrumViewport = {
  zoom: number;
  /** Center of the view as an offset from the LO, in Hz. */
  centerOffsetHz: number;
};

export const MIN_ZOOM = 1;
export const MAX_ZOOM = 48;

export const DEFAULT_VIEWPORT: SpectrumViewport = {
  zoom: 1,
  centerOffsetHz: 0,
};

export function viewSpanHz(sampleRate: number, zoom: number): number {
  return sampleRate / Math.max(zoom, MIN_ZOOM);
}

export function clampViewport(
  viewport: SpectrumViewport,
  sampleRate: number,
): SpectrumViewport {
  const zoom = clamp(viewport.zoom, MIN_ZOOM, MAX_ZOOM);
  const span = viewSpanHz(sampleRate, zoom);
  const maxCenter = Math.max(0, sampleRate / 2 - span / 2);
  return {
    zoom,
    centerOffsetHz: clamp(viewport.centerOffsetHz, -maxCenter, maxCenter),
  };
}

/** Map a horizontal CSS pixel to LO-relative offset (Hz). */
export function xToOffsetHz(
  x: number,
  width: number,
  sampleRate: number,
  viewport: SpectrumViewport,
): number {
  if (width <= 0) return 0;
  const span = viewSpanHz(sampleRate, viewport.zoom);
  const left = viewport.centerOffsetHz - span / 2;
  return left + (x / width) * span;
}

/** Map an LO-relative offset (Hz) to a horizontal CSS pixel. */
export function offsetHzToX(
  offsetHz: number,
  width: number,
  sampleRate: number,
  viewport: SpectrumViewport,
): number {
  const span = viewSpanHz(sampleRate, viewport.zoom);
  if (span <= 0) return width / 2;
  const left = viewport.centerOffsetHz - span / 2;
  return ((offsetHz - left) / span) * width;
}

/**
 * Zoom toward a focal LO-offset so the frequency under the cursor stays put.
 * `factor` > 1 zooms in.
 */
export function zoomViewportAt(
  viewport: SpectrumViewport,
  sampleRate: number,
  focalOffsetHz: number,
  factor: number,
): SpectrumViewport {
  const nextZoom = viewport.zoom * factor;
  const before = clampViewport(viewport, sampleRate);
  const after = clampViewport(
    { zoom: nextZoom, centerOffsetHz: before.centerOffsetHz },
    sampleRate,
  );

  const beforeSpan = viewSpanHz(sampleRate, before.zoom);
  const afterSpan = viewSpanHz(sampleRate, after.zoom);
  if (beforeSpan <= 0) return after;

  // Keep focalOffset at the same fractional position in the window.
  const frac =
    (focalOffsetHz - (before.centerOffsetHz - beforeSpan / 2)) / beforeSpan;
  const nextCenter = focalOffsetHz - (frac - 0.5) * afterSpan;
  return clampViewport(
    { zoom: after.zoom, centerOffsetHz: nextCenter },
    sampleRate,
  );
}

/** Pan the display window by a delta in Hz (positive = view moves up in freq). */
export function panViewport(
  viewport: SpectrumViewport,
  sampleRate: number,
  deltaHz: number,
): SpectrumViewport {
  return clampViewport(
    {
      zoom: viewport.zoom,
      centerOffsetHz: viewport.centerOffsetHz + deltaHz,
    },
    sampleRate,
  );
}

/** Nice tick step for a frequency axis spanning `spanHz`. */
export function axisTickStepHz(spanHz: number): number {
  const raw = spanHz / 8;
  const pow = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1))));
  const n = raw / pow;
  if (n < 1.5) return pow;
  if (n < 3.5) return 2 * pow;
  if (n < 7.5) return 5 * pow;
  return 10 * pow;
}

export function formatAxisHz(hz: number): string {
  const abs = Math.abs(hz);
  if (abs >= 1e6) return `${(hz / 1e6).toFixed(3)} M`;
  if (abs >= 1e3) return `${(hz / 1e3).toFixed(1)} k`;
  return `${Math.round(hz)}`;
}

/**
 * Zoom about a fixed screen position (0 = left edge, 1 = right edge) rather
 * than a frequency, so the point under the cursor holds still even while an
 * eased zoom is still converging on its target.
 */
export function zoomViewportAtFraction(
  viewport: SpectrumViewport,
  sampleRate: number,
  fraction: number,
  factor: number,
): SpectrumViewport {
  const before = clampViewport(viewport, sampleRate);
  const nextZoom = clamp(before.zoom * factor, MIN_ZOOM, MAX_ZOOM);
  const beforeSpan = viewSpanHz(sampleRate, before.zoom);
  const afterSpan = viewSpanHz(sampleRate, nextZoom);
  const focal = before.centerOffsetHz - beforeSpan / 2 + fraction * beforeSpan;
  return clampViewport(
    { zoom: nextZoom, centerOffsetHz: focal - (fraction - 0.5) * afterSpan },
    sampleRate,
  );
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** Time constant of the eased zoom/pan follow, in ms. */
const FOLLOW_TAU_MS = 70;
/** Inertial pan decay time constant, in ms. */
const FLING_TAU_MS = 320;
/** Below this speed (Hz/ms per Hz of span) a fling is considered stopped. */
const FLING_STOP = 1e-6;

/**
 * Viewport state outside React. Pointer drags, wheel zoom and inertia run at
 * display rate and notify canvases directly, so panning never re-renders the
 * component tree. `get()` is what is on screen; `target` is where eased
 * motion is heading.
 */
export class ViewportStore {
  private current: SpectrumViewport = DEFAULT_VIEWPORT;
  private target: SpectrumViewport = DEFAULT_VIEWPORT;
  /** Pan speed in span-fractions per ms; scales with zoom automatically. */
  private flingVelocity = 0;
  private sampleRate: number;
  private listeners = new Set<() => void>();
  private raf = 0;
  private lastTime = 0;

  constructor(sampleRate: number) {
    this.sampleRate = sampleRate;
  }

  get(): SpectrumViewport {
    return this.current;
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  setSampleRate(sampleRate: number): void {
    if (sampleRate === this.sampleRate) return;
    this.sampleRate = sampleRate;
    this.current = clampViewport(this.current, sampleRate);
    this.target = clampViewport(this.target, sampleRate);
    this.notify();
  }

  /** Snap straight to a viewport and stop any motion. */
  jump(next: SpectrumViewport): void {
    this.stopMotion();
    this.current = this.target = clampViewport(next, this.sampleRate);
    this.notify();
  }

  /** 1:1 pan while the pointer is down. */
  panBy(deltaHz: number): void {
    this.stopMotion();
    this.current = this.target = panViewport(
      this.current,
      this.sampleRate,
      deltaHz,
    );
    this.notify();
  }

  /** Eased zoom about a screen fraction; repeated calls compose. */
  zoomAt(fraction: number, factor: number): void {
    this.flingVelocity = 0;
    this.target = zoomViewportAtFraction(
      this.target,
      this.sampleRate,
      fraction,
      factor,
    );
    this.start();
  }

  /** Eased pan of the target (keyboard, trackpad). */
  panTargetBy(deltaHz: number): void {
    this.flingVelocity = 0;
    this.target = panViewport(this.target, this.sampleRate, deltaHz);
    this.start();
  }

  /** Release a drag with momentum. Velocity is in Hz per ms. */
  fling(velocityHzPerMs: number): void {
    // Momentum is motion the user asked the OS to remove.
    if (prefersReducedMotion()) return;
    const span = viewSpanHz(this.sampleRate, this.current.zoom);
    this.target = this.current;
    this.flingVelocity = velocityHzPerMs / span;
    if (Math.abs(this.flingVelocity) < FLING_STOP) {
      this.flingVelocity = 0;
      return;
    }
    this.start();
  }

  dispose(): void {
    this.stopMotion();
    this.listeners.clear();
  }

  private stopMotion(): void {
    this.flingVelocity = 0;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
  }

  private start(): void {
    if (this.raf) return;
    this.lastTime = performance.now();
    this.raf = requestAnimationFrame(this.tick);
  }

  private tick = (now: number): void => {
    this.raf = 0;
    const dt = Math.min(now - this.lastTime, 50);
    this.lastTime = now;

    if (this.flingVelocity !== 0) {
      const span = viewSpanHz(this.sampleRate, this.current.zoom);
      const next = panViewport(
        this.current,
        this.sampleRate,
        this.flingVelocity * span * dt,
      );
      // Stop dead against the edge of the capture window.
      if (next.centerOffsetHz === this.current.centerOffsetHz) {
        this.flingVelocity = 0;
      }
      this.current = this.target = next;
      this.flingVelocity *= Math.exp(-dt / FLING_TAU_MS);
      if (Math.abs(this.flingVelocity) < FLING_STOP) this.flingVelocity = 0;
    } else {
      const k = prefersReducedMotion() ? 1 : 1 - Math.exp(-dt / FOLLOW_TAU_MS);
      const cur = this.current;
      const tgt = this.target;
      // Zoom eases in log space so in and out feel symmetric.
      const zoom = Math.exp(
        Math.log(cur.zoom) + (Math.log(tgt.zoom) - Math.log(cur.zoom)) * k,
      );
      const centerOffsetHz =
        cur.centerOffsetHz + (tgt.centerOffsetHz - cur.centerOffsetHz) * k;
      const settled =
        Math.abs(Math.log(tgt.zoom / zoom)) < 0.0005 &&
        Math.abs(tgt.centerOffsetHz - centerOffsetHz) <
          viewSpanHz(this.sampleRate, tgt.zoom) * 0.0005;
      this.current = settled
        ? tgt
        : clampViewport({ zoom, centerOffsetHz }, this.sampleRate);
    }

    this.notify();
    const moving =
      this.flingVelocity !== 0 ||
      this.current.zoom !== this.target.zoom ||
      this.current.centerOffsetHz !== this.target.centerOffsetHz;
    if (moving) {
      this.raf = requestAnimationFrame(this.tick);
    }
  };

  private notify(): void {
    for (const listener of this.listeners) listener();
  }
}
