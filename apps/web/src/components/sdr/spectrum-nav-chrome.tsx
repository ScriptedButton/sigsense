"use client";

import { useSyncExternalStore } from "react";

import { formatBandwidth } from "@/lib/sdr/format";
import {
  MIN_ZOOM,
  type ViewportStore,
  viewSpanHz,
} from "@/lib/sdr/spectrum-viewport";

type SpectrumNavChromeProps = {
  sampleRate: number;
  store: ViewportStore;
  onResetZoom: () => void;
};

export function SpectrumNavChrome({
  sampleRate,
  store,
  onResetZoom,
}: SpectrumNavChromeProps) {
  // Re-render only when the displayed zoom (one decimal) actually changes,
  // not on every frame of a pan or eased zoom.
  const zoom = useSyncExternalStore(
    (notify) => store.subscribe(notify),
    () => Math.round(store.get().zoom * 10) / 10,
    () => MIN_ZOOM,
  );
  const span = viewSpanHz(sampleRate, zoom);
  const zoomed = zoom > MIN_ZOOM + 0.02;

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-1.5">
      <p className="nav-chrome-hint text-xs text-muted">
        Click tune · drag pan · scroll zoom · double-click center
        <span className="hidden sm:inline"> · ←→ pan · +/− zoom</span>
      </p>
      <p className="touch-hint text-[0.68rem] text-muted">
        Tap tunes · drag pans · pinch zooms · double-tap centers
      </p>
      <div className="flex items-center gap-2">
        <p className="font-mono text-[0.7rem] tabular-nums text-muted">
          Span {formatBandwidth(span)}
          {zoomed ? ` · ${zoom.toFixed(1)}×` : ""}
        </p>
        {zoomed && (
          <button
            type="button"
            className="rounded border border-line bg-surface-2 px-2 py-0.5 text-[0.65rem] font-medium text-ink transition-[border-color,background] duration-150 hover:border-[oklch(0.40_0.02_355)]"
            onClick={onResetZoom}
          >
            Reset zoom
          </button>
        )}
      </div>
    </div>
  );
}
