"use client";

import { useCallback, useState, useSyncExternalStore } from "react";

import {
  useRadioController,
  useSdrState,
  useSpectrumStream,
} from "./sdr-provider";
import type { SpectrumFrameInfo } from "@/lib/sdr/streaming-spectrum";
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
  const controller = useRadioController();
  const radio = useSdrState();
  // Re-render only when the displayed zoom (one decimal) actually changes,
  // not on every frame of a pan or eased zoom.
  const zoom = useSyncExternalStore(
    (notify) => store.subscribe(notify),
    () => Math.round(store.get().zoom * 10) / 10,
    () => MIN_ZOOM,
  );
  const [resolution, setResolution] = useState<SpectrumFrameInfo | undefined>();
  useSpectrumStream(useCallback((_spectrum, _center, info) => {
    setResolution((previous) => previous?.binHz === info?.binHz && previous?.averagedFfts === info?.averagedFfts ? previous : info);
  }, []), true);
  const span = viewSpanHz(sampleRate, zoom);
  const zoomed = zoom > MIN_ZOOM + 0.02;
  const spanPresets = [20_000, 100_000, 500_000].filter(
    (preset) => preset <= sampleRate && sampleRate / preset <= 48,
  );

  const setSpan = (spanHz: number) => {
    const viewport = store.get();
    store.jump({
      zoom: sampleRate / spanHz,
      centerOffsetHz: viewport.centerOffsetHz,
    });
  };

  const pan = (direction: -1 | 1) => {
    const viewport = store.get();
    const currentSpan = viewSpanHz(sampleRate, viewport.zoom);
    const stepHz = currentSpan * 0.5 * direction;
    const maxCenterHz = Math.max(0, sampleRate / 2 - currentSpan / 2);
    const desiredCenterHz = viewport.centerOffsetHz + stepHz;
    const nextCenterHz = Math.max(
      -maxCenterHz,
      Math.min(maxCenterHz, desiredCenterHz),
    );
    const hardwarePanHz = desiredCenterHz - nextCenterHz;

    store.jump({ ...viewport, centerOffsetHz: nextCenterHz });
    if (Math.abs(hardwarePanHz) >= 1) {
      void controller.panFrequency(Math.round(hardwarePanHz));
    }
  };

  const returnToSignal = () => {
    const viewport = store.get();
    store.jump({ ...viewport, centerOffsetHz: radio.offsetHz });
  };

  const buttonClass =
    "rounded border border-line bg-surface-2 px-2 py-0.5 text-[0.65rem] font-medium text-ink transition-[border-color,background] duration-150 hover:border-[oklch(0.40_0.02_355)] disabled:cursor-not-allowed disabled:opacity-40";

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-1.5">
      <p className="nav-chrome-hint text-xs text-muted">
        Click tune · drag pan · scroll zoom · double-click center
        <span className="hidden sm:inline"> · ←→ pan · +/− zoom</span>
      </p>
      <p className="touch-hint text-[0.68rem] text-muted">
        Tap tunes · drag pans · pinch zooms · double-tap centers
      </p>
      <div className="flex flex-wrap items-center justify-end gap-2">
        <div className="flex items-center gap-1" role="group" aria-label="Spectrum navigation">
          <button type="button" className={buttonClass} onClick={() => pan(-1)} title="Pan left; retune the receiver at the capture edge" aria-label="Pan spectrum left">
            ←
          </button>
          <button type="button" className={buttonClass} onClick={() => store.zoomAt(0.5, 1.6)} disabled={zoom >= 47.9} title="Zoom in" aria-label="Zoom spectrum in">
            +
          </button>
          <button type="button" className={buttonClass} onClick={() => store.zoomAt(0.5, 1 / 1.6)} disabled={!zoomed} title="Zoom out" aria-label="Zoom spectrum out">
            −
          </button>
          <button type="button" className={buttonClass} onClick={() => pan(1)} title="Pan right; retune the receiver at the capture edge" aria-label="Pan spectrum right">
            →
          </button>
          <button type="button" className={buttonClass} onClick={returnToSignal} title="Move the view back to the tuned passband">
            Find signal
          </button>
        </div>
        {spanPresets.length > 0 && (
          <div className="flex items-center gap-1" role="group" aria-label="Visible spectrum span">
            {spanPresets.map((preset) => (
              <button
                key={preset}
                type="button"
                className={buttonClass}
                aria-pressed={Math.abs(span - preset) / preset < 0.03}
                onClick={() => setSpan(preset)}
                title={`Show a ${formatBandwidth(preset)} span`}
              >
                {formatBandwidth(preset)}
              </button>
            ))}
          </div>
        )}
        <p className="font-mono text-[0.7rem] tabular-nums text-muted">
          Span {formatBandwidth(span)}
          {zoomed ? ` · ${zoom.toFixed(1)}×` : ""}
          {resolution ? ` · ${resolution.binHz.toFixed(1)} Hz/bin · ${resolution.averagedFfts} FFT avg` : ""}
        </p>
        {zoomed && (
          <button
            type="button"
            className={buttonClass}
            onClick={onResetZoom}
          >
            Reset zoom
          </button>
        )}
      </div>
    </div>
  );
}
