"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

type SheetProps = {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** Element to focus first; defaults to the first focusable control. */
  initialFocus?: React.RefObject<HTMLElement | null>;
};

const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

/** Past this much downward drag (or a quick flick) the sheet closes. */
const CLOSE_DISTANCE = 90;
const CLOSE_VELOCITY = 0.6; // px per ms

/**
 * Bottom sheet for phones: dims the page, traps focus, restores it on close,
 * closes on Esc / backdrop tap / swipe down, and lifts above the on-screen
 * keyboard (visualViewport) so a focused field is never hidden.
 */
export function Sheet({ title, onClose, children, initialFocus }: SheetProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [dragY, setDragY] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [viewport, setViewport] = useState<{ lift: number; height: number } | null>(null);
  const drag = useRef<{ startY: number; startT: number; id: number } | null>(null);

  // Keep the sheet above the keyboard and within the visible area.
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const update = () =>
      setViewport({
        lift: Math.max(0, window.innerHeight - (vv.height + vv.offsetTop)),
        height: vv.height,
      });
    update();
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    return () => {
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
    };
  }, []);

  // Focus in, trap Tab, restore focus to whatever opened the sheet.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const target =
      initialFocus?.current ?? panel?.querySelector<HTMLElement>(FOCUSABLE) ?? panel;
    target?.focus({ preventScroll: true });

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panel) return;
      const items = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      opener?.focus?.({ preventScroll: true });
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once per open
  }, []);

  const onDragStart = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    drag.current = { startY: event.clientY, startT: event.timeStamp, id: event.pointerId };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  }, []);

  const onDragMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== event.pointerId) return;
    setDragY(Math.max(0, event.clientY - d.startY));
  }, []);

  const onDragEnd = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const d = drag.current;
      if (!d || d.id !== event.pointerId) return;
      drag.current = null;
      const dy = event.clientY - d.startY;
      const velocity = dy / Math.max(1, event.timeStamp - d.startT);
      setDragging(false);
      if (dy > CLOSE_DISTANCE || velocity > CLOSE_VELOCITY) {
        onClose();
      } else {
        setDragY(0);
      }
    },
    [onClose],
  );

  const sheet = (
    <div className="sheet-root" style={{ bottom: viewport ? `${viewport.lift}px` : 0 }}>
      <div className="sheet-backdrop" onClick={onClose} aria-hidden />
      <div
        ref={panelRef}
        className="sheet-panel"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        data-dragging={dragging || undefined}
        style={{
          transform: dragY ? `translateY(${dragY}px)` : undefined,
          maxHeight: viewport ? `${Math.max(240, viewport.height - 24)}px` : undefined,
        }}
      >
        <div
          className="sheet-grab"
          onPointerDown={onDragStart}
          onPointerMove={onDragMove}
          onPointerUp={onDragEnd}
          onPointerCancel={onDragEnd}
        >
          <span className="sheet-grab-bar" aria-hidden />
          <h2 className="sheet-title">{title}</h2>
          <button
            type="button"
            className="sheet-close"
            aria-label={`Close ${title}`}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>
  );

  // Portal to the body so no transformed ancestor can trap the fixed overlay.
  return typeof document === "undefined" ? null : createPortal(sheet, document.body);
}
