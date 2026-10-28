"use client";

import {
  useCallback,
  useEffect,
  useRef,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from "react";

import {
  offsetHzToX,
  type ViewportStore,
  viewSpanHz,
  xToOffsetHz,
} from "@/lib/sdr/spectrum-viewport";

export type SpectrumNavHandlers = {
  /** Live viewport; pans and zooms go here, not through React state. */
  store: ViewportStore;
  onTuneOffset: (offsetHz: number) => void;
  onCenterOnOffset: (offsetHz: number) => void;
  onPanLo: (deltaHz: number) => void;
  sampleRate: number;
  offsetHz: number;
  bandwidthHz: number;
};

type DragMode = "tune" | "pan-view" | "pan-lo";

type DragState = {
  /** Active gesture, or null until movement exceeds click slop. */
  mode: DragMode | null;
  /** Gesture that starts once the pointer moves past click slop. */
  dragMode: DragMode;
  /** When true, a stationary release tunes instead of panning. */
  clickTunes: boolean;
  pointerId: number;
  startX: number;
  lastX: number;
  lastTime: number;
  /** Canvas left edge in client px, read once per gesture. */
  left: number;
  /** Smoothed pan speed in Hz/ms (positive = view moving up in frequency). */
  velocity: number;
};

const TUNE_GRAB_PX = 14;
const LO_PAN_MIN_HZ = 500;
/** Pixels of movement before a press becomes a drag (avoids accidental LO pan). */
const CLICK_SLOP_PX = 5;
/** A release this long after the last move is a stop, not a flick. */
const FLICK_MAX_IDLE_MS = 90;
const ZOOM_PER_WHEEL_PX = 0.0015;
const ZOOM_PER_PINCH_PX = 0.01;
/** Fingers wobble more than a mouse: a touch must move this far to count as a drag. */
const TOUCH_SLOP_PX = 10;
/** Two taps this close in time and space are a double-tap. */
const DOUBLE_TAP_MS = 320;
const DOUBLE_TAP_PX = 28;

type PinchState = {
  ids: [number, number];
  dist: number;
  midX: number;
};

/**
 * Shared pointer + wheel navigation for spectrum and waterfall canvases.
 *
 * - Click anywhere: tune VFO to that frequency
 * - Drag near the passband: scrub the VFO
 * - Drag elsewhere (or when zoomed): pan the view with momentum; at 1× zoom,
 *   pan the LO
 * - Shift / middle-drag: always pan the LO
 * - Wheel: eased zoom toward cursor; horizontal scroll pans
 * - Double-click: center LO on that frequency
 *
 * Nothing here sets React state per event: the viewport lives in a
 * ViewportStore that canvases subscribe to, tuning is coalesced to one call
 * per frame, and the cursor is set directly on the element.
 */
export function useSpectrumNav(
  canvasRef: RefObject<HTMLCanvasElement | null>,
  { store, onTuneOffset, onCenterOnOffset, onPanLo, sampleRate, offsetHz, bandwidthHz }: SpectrumNavHandlers,
) {
  const storeRef = useRef(store);
  const sampleRateRef = useRef(sampleRate);
  const offsetRef = useRef(offsetHz);
  const bandwidthRef = useRef(bandwidthHz);
  const callbacksRef = useRef({ onTuneOffset, onCenterOnOffset, onPanLo });
  const widthRef = useRef(0);
  const dragRef = useRef<DragState | null>(null);
  const loPanAccumRef = useRef(0);
  const loPanPendingRef = useRef(false);
  const tunePendingRef = useRef<number | null>(null);
  const tuneRafRef = useRef(0);
  /** Every pointer currently down, so a second finger can start a pinch. */
  const pointersRef = useRef(new Map<number, { x: number; y: number }>());
  const pinchRef = useRef<PinchState | null>(null);
  const lastTapRef = useRef<{ time: number; x: number; y: number } | null>(null);
  /** When our own double-tap fired, so the browser's follow-up dblclick is ignored. */
  const doubleTapAtRef = useRef(0);

  useEffect(() => {
    storeRef.current = store;
    sampleRateRef.current = sampleRate;
    offsetRef.current = offsetHz;
    bandwidthRef.current = bandwidthHz;
    callbacksRef.current = { onTuneOffset, onCenterOnOffset, onPanLo };
  });

  const updateCursor = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const mode = dragRef.current?.mode;
    const cursor = mode
      ? mode === "tune"
        ? "col-resize"
        : "grabbing"
      : storeRef.current.get().zoom > 1.02
        ? "grab"
        : "crosshair";
    if (canvas.style.cursor !== cursor) canvas.style.cursor = cursor;
  }, [canvasRef]);

  const flushLoPan = useCallback(() => {
    loPanPendingRef.current = false;
    const delta = loPanAccumRef.current;
    if (Math.abs(delta) < LO_PAN_MIN_HZ) return;
    loPanAccumRef.current = 0;
    callbacksRef.current.onPanLo(Math.round(delta));
  }, []);

  const queueLoPan = useCallback(
    (deltaHz: number) => {
      loPanAccumRef.current += deltaHz;
      if (loPanPendingRef.current) return;
      loPanPendingRef.current = true;
      requestAnimationFrame(flushLoPan);
    },
    [flushLoPan],
  );

  /** Tuning retunes the whole app, so send at most one offset per frame. */
  const queueTune = useCallback((offset: number) => {
    tunePendingRef.current = offset;
    if (tuneRafRef.current) return;
    tuneRafRef.current = requestAnimationFrame(() => {
      tuneRafRef.current = 0;
      const pending = tunePendingRef.current;
      tunePendingRef.current = null;
      if (pending !== null) callbacksRef.current.onTuneOffset(pending);
    });
  }, []);

  const flushTune = useCallback(() => {
    if (tuneRafRef.current) cancelAnimationFrame(tuneRafRef.current);
    tuneRafRef.current = 0;
    const pending = tunePendingRef.current;
    tunePendingRef.current = null;
    if (pending !== null) callbacksRef.current.onTuneOffset(pending);
  }, []);

  const xToOffset = useCallback((x: number) => {
    return xToOffsetHz(
      x,
      widthRef.current,
      sampleRateRef.current,
      storeRef.current.get(),
    );
  }, []);

  /**
   * Decide what a *drag* should do. Clicks always tune (primary button,
   * no modifiers) so selecting a scan marker does not retune the LO.
   */
  const pickDragMode = useCallback(
    (x: number, shiftKey: boolean, button: number): DragMode => {
      if (shiftKey || button === 1) return "pan-lo";

      const width = widthRef.current;
      const vp = storeRef.current.get();
      const rate = sampleRateRef.current;
      const tuneX = offsetHzToX(offsetRef.current, width, rate, vp);
      const bwPx = Math.max(
        (bandwidthRef.current / viewSpanHz(rate, vp.zoom)) * width,
        8,
      );
      const nearTune = Math.abs(x - tuneX) <= Math.max(TUNE_GRAB_PX, bwPx / 2);

      if (nearTune) return "tune";
      if (vp.zoom > 1.02) return "pan-view";
      return "pan-lo";
    },
    [],
  );

  const applyMode = useCallback(
    (drag: DragState, clientX: number, deltaHz: number, time: number) => {
      const mode = drag.mode;
      if (mode === "tune") {
        queueTune(Math.round(xToOffset(clientX - drag.left)));
        return;
      }
      if (mode === "pan-view") {
        storeRef.current.panBy(deltaHz);
        const dt = Math.max(time - drag.lastTime, 1);
        // Weight recent motion so a flick registers over a slow start.
        drag.velocity = drag.velocity * 0.6 + (deltaHz / dt) * 0.4;
        drag.lastTime = time;
        return;
      }
      queueLoPan(deltaHz);
    },
    [queueLoPan, queueTune, xToOffset],
  );

  const onPointerDown = useCallback(
    (e: ReactPointerEvent<HTMLCanvasElement>) => {
      if (e.button !== 0 && e.button !== 1) return;
      const canvas = e.currentTarget;
      canvas.focus({ preventScroll: true });
      canvas.setPointerCapture(e.pointerId);
      pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });

      // A second finger turns the gesture into a pinch: drop any one-finger
      // drag (without tuning) and track the pair instead.
      if (pointersRef.current.size === 2) {
        const [a, b] = [...pointersRef.current.entries()];
        dragRef.current = null;
        tunePendingRef.current = null;
        loPanAccumRef.current = 0;
        lastTapRef.current = null;
        storeRef.current.panBy(0);
        pinchRef.current = {
          ids: [a[0], b[0]],
          dist: Math.max(Math.hypot(a[1].x - b[1].x, a[1].y - b[1].y), 1),
          midX: (a[1].x + b[1].x) / 2,
        };
        widthRef.current = canvas.getBoundingClientRect().width;
        return;
      }
      if (pointersRef.current.size > 2) return;
      // Grabbing the view cancels any momentum still coasting.
      storeRef.current.panBy(0);

      const rect = canvas.getBoundingClientRect();
      widthRef.current = rect.width;
      const dragMode = pickDragMode(
        e.clientX - rect.left,
        e.shiftKey,
        e.button,
      );
      // Primary click (no shift) always tunes on release if the pointer stays still.
      const clickTunes = e.button === 0 && !e.shiftKey;
      // Grabbing the passband starts tuning immediately so scrubbing feels instant.
      const startImmediately = dragMode === "tune";

      dragRef.current = {
        mode: startImmediately ? "tune" : null,
        dragMode,
        clickTunes,
        pointerId: e.pointerId,
        startX: e.clientX,
        lastX: e.clientX,
        lastTime: e.timeStamp,
        left: rect.left,
        velocity: 0,
      };

      if (startImmediately) {
        queueTune(Math.round(xToOffset(e.clientX - rect.left)));
      }
      updateCursor();
    },
    [pickDragMode, queueTune, updateCursor, xToOffset],
  );

  const onPointerMove = useCallback(
    (e: ReactPointerEvent<HTMLCanvasElement>) => {
      if (pointersRef.current.has(e.pointerId)) {
        pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      }
      const pinch = pinchRef.current;
      if (pinch) {
        const a = pointersRef.current.get(pinch.ids[0]);
        const b = pointersRef.current.get(pinch.ids[1]);
        const width = widthRef.current;
        if (!a || !b || width <= 0) return;
        const dist = Math.max(Math.hypot(a.x - b.x, a.y - b.y), 1);
        const midX = (a.x + b.x) / 2;
        const rect = e.currentTarget.getBoundingClientRect();
        const view = storeRef.current;
        // Zoom about the fingers' midpoint, then follow the midpoint sideways.
        view.zoomAt((midX - rect.left) / width, dist / pinch.dist);
        const vp = view.get();
        if (vp.zoom > 1.02) {
          const hzPerPx = viewSpanHz(sampleRateRef.current, vp.zoom) / width;
          view.panTargetBy(-(midX - pinch.midX) * hzPerPx);
        }
        pinch.dist = dist;
        pinch.midX = midX;
        return;
      }

      const drag = dragRef.current;
      if (!drag || drag.pointerId !== e.pointerId) return;

      const width = widthRef.current;
      if (width <= 0) return;
      const vp = storeRef.current.get();
      const hzPerPx = viewSpanHz(sampleRateRef.current, vp.zoom) / width;

      if (drag.mode === null) {
        const slop = e.pointerType === "touch" ? TOUCH_SLOP_PX : CLICK_SLOP_PX;
        if (Math.abs(e.clientX - drag.startX) < slop) return;
        // Include movement since press so the first pan step isn't lost.
        drag.mode = drag.dragMode;
        const total = -(e.clientX - drag.startX) * hzPerPx;
        drag.lastX = e.clientX;
        applyMode(drag, e.clientX, total, e.timeStamp);
        updateCursor();
        return;
      }

      // Coalesced events keep a fast flick smooth between frames.
      const events =
        typeof e.nativeEvent.getCoalescedEvents === "function"
          ? e.nativeEvent.getCoalescedEvents()
          : [];
      if (events.length > 1) {
        for (const ev of events) {
          const dx = ev.clientX - drag.lastX;
          drag.lastX = ev.clientX;
          applyMode(drag, ev.clientX, -dx * hzPerPx, ev.timeStamp);
        }
        return;
      }
      const dx = e.clientX - drag.lastX;
      drag.lastX = e.clientX;
      applyMode(drag, e.clientX, -dx * hzPerPx, e.timeStamp);
    },
    [applyMode, updateCursor],
  );

  const endDrag = useCallback(
    (e: ReactPointerEvent<HTMLCanvasElement>) => {
      pointersRef.current.delete(e.pointerId);
      if (e.type === "pointercancel") {
        // The browser took the gesture away; never turn that into a tune.
        if (dragRef.current?.pointerId === e.pointerId) dragRef.current = null;
        loPanAccumRef.current = 0;
        tunePendingRef.current = null;
        pinchRef.current = null;
        updateCursor();
        return;
      }
      if (pinchRef.current) {
        // The pinch ends when either finger lifts; the remaining finger must
        // lift and press again to start a fresh gesture (no surprise tune).
        if (pointersRef.current.size < 2) pinchRef.current = null;
        return;
      }
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== e.pointerId) return;
      dragRef.current = null;

      // Touch has no dblclick we can rely on: two quick taps center + reset zoom.
      if (drag.mode === null && e.pointerType !== "mouse") {
        const last = lastTapRef.current;
        const near =
          last &&
          e.timeStamp - last.time < DOUBLE_TAP_MS &&
          Math.hypot(e.clientX - last.x, e.clientY - last.y) < DOUBLE_TAP_PX;
        if (near) {
          lastTapRef.current = null;
          loPanAccumRef.current = 0;
          tunePendingRef.current = null;
          if (tuneRafRef.current) cancelAnimationFrame(tuneRafRef.current);
          tuneRafRef.current = 0;
          const offset = Math.round(xToOffset(e.clientX - drag.left));
          callbacksRef.current.onCenterOnOffset(offset);
          storeRef.current.jump({ zoom: 1, centerOffsetHz: 0 });
          doubleTapAtRef.current = performance.now();
          updateCursor();
          return;
        }
        lastTapRef.current = { time: e.timeStamp, x: e.clientX, y: e.clientY };
      }

      // Stationary (or near-stationary) primary press → tune, never pan.
      if (drag.mode === null && drag.clickTunes) {
        loPanAccumRef.current = 0;
        tunePendingRef.current = null;
        if (tuneRafRef.current) cancelAnimationFrame(tuneRafRef.current);
        tuneRafRef.current = 0;
        callbacksRef.current.onTuneOffset(
          Math.round(xToOffset(e.clientX - drag.left)),
        );
        updateCursor();
        return;
      }

      if (drag.mode === "tune") flushTune();

      if (drag.mode === "pan-view") {
        const idle = e.timeStamp - drag.lastTime;
        if (idle < FLICK_MAX_IDLE_MS) storeRef.current.fling(drag.velocity);
      }

      if (Math.abs(loPanAccumRef.current) >= LO_PAN_MIN_HZ) {
        flushLoPan();
      } else {
        loPanAccumRef.current = 0;
      }
      updateCursor();
    },
    [flushLoPan, flushTune, updateCursor, xToOffset],
  );

  const onDoubleClick = useCallback(
    (e: ReactMouseEvent<HTMLCanvasElement>) => {
      // A touch double-tap already centered; re-centering from the same pixel
      // would now land on a different frequency.
      if (performance.now() - doubleTapAtRef.current < 600) return;
      const rect = e.currentTarget.getBoundingClientRect();
      widthRef.current = rect.width;
      callbacksRef.current.onCenterOnOffset(
        Math.round(xToOffset(e.clientX - rect.left)),
      );
      storeRef.current.jump({ zoom: 1, centerOffsetHz: 0 });
    },
    [xToOffset],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    // Cache width so pointer handlers never force layout.
    widthRef.current = canvas.getBoundingClientRect().width;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (entry) widthRef.current = entry.contentRect.width;
    });
    observer.observe(canvas);

    const unsubscribe = store.subscribe(updateCursor);
    updateCursor();

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      widthRef.current = rect.width;
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? rect.width : 1;
      const view = storeRef.current;
      const vp = view.get();

      // Sideways scroll (trackpad) pans the view when zoomed.
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        if (vp.zoom > 1.02) {
          const hzPerPx = viewSpanHz(sampleRateRef.current, vp.zoom) / rect.width;
          view.panTargetBy(e.deltaX * unit * hzPerPx);
        }
        return;
      }

      // Pinch gestures arrive as ctrl+wheel with small deltas.
      const gain = e.ctrlKey ? ZOOM_PER_PINCH_PX : ZOOM_PER_WHEEL_PX;
      const factor = Math.exp(-e.deltaY * unit * gain);
      view.zoomAt((e.clientX - rect.left) / rect.width, factor);
    };

    const onKeyDown = (e: KeyboardEvent) => {
      const view = storeRef.current;
      const rate = sampleRateRef.current;
      const vp = view.get();
      const span = viewSpanHz(rate, vp.zoom);
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        const dir = e.key === "ArrowLeft" ? -1 : 1;
        const step = e.shiftKey ? span / 20 : span / 80;
        if (e.altKey || vp.zoom <= 1.02) {
          queueLoPan(dir * step);
        } else {
          view.panTargetBy(dir * step);
        }
        return;
      }
      if (e.key === "=" || e.key === "+" || e.key === "-" || e.key === "_") {
        e.preventDefault();
        const factor = e.key === "-" || e.key === "_" ? 1 / 1.25 : 1.25;
        // Zoom about the tuned frequency, matching the old behaviour.
        const tuned = offsetRef.current;
        const left = vp.centerOffsetHz - span / 2;
        const fraction = Math.min(1, Math.max(0, (tuned - left) / span));
        view.zoomAt(fraction, factor);
        return;
      }
      if (e.key === "0" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        view.jump({ zoom: 1, centerOffsetHz: 0 });
      }
    };

    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("keydown", onKeyDown);
    return () => {
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("keydown", onKeyDown);
      observer.disconnect();
      unsubscribe();
    };
  }, [canvasRef, queueLoPan, store, updateCursor]);

  useEffect(
    () => () => {
      if (tuneRafRef.current) cancelAnimationFrame(tuneRafRef.current);
    },
    [],
  );

  return {
    onPointerDown,
    onPointerMove,
    onPointerUp: endDrag,
    onPointerCancel: endDrag,
    onDoubleClick,
    tabIndex: 0,
  };
}
