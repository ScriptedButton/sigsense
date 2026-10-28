"use client";

import type { Spectrum } from "@/lib/api";

type Props = {
  spectrum?: Spectrum;
  stitched: boolean;
  peakHold: boolean;
  onPeakHoldChange: (value: boolean) => void;
  floor: number;
  onFloorChange: (value: number) => void;
  zoomed: boolean;
  onResetZoom: () => void;
};

export function SpectrumToolbar({
  spectrum,
  stitched,
  peakHold,
  onPeakHoldChange,
  floor,
  onFloorChange,
  zoomed,
  onResetZoom,
}: Props) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] px-4 py-3">
      <div>
        <span className="text-sm font-medium">
          {spectrum?.swept ? "Swept frequency overview" : "RF spectrum & waterfall"}
        </span>
        <span className="ml-3 text-[10px] uppercase tracking-wider text-[var(--muted)]">
          {spectrum?.swept
            ? "Sequential captures · unsampled bins blank"
            : stitched
              ? "Stitched while the receiver retunes"
              : "Scroll to zoom · drag to pan · click to inspect"}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-4 text-xs text-[var(--muted)]">
        {zoomed && (
          <button
            type="button"
            className="rounded-md border border-[var(--line)] px-2 py-1 hover:border-[var(--signal)]"
            onClick={onResetZoom}
          >
            Fit band
          </button>
        )}
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={peakHold}
            onChange={(e) => onPeakHoldChange(e.target.checked)}
          />
          Peak hold
        </label>
        <label className="flex items-center gap-2">
          Floor
          <input
            aria-label="Display floor"
            type="range"
            min="-150"
            max="-45"
            value={floor}
            onChange={(e) => onFloorChange(Number(e.target.value))}
          />
          {floor} dB
        </label>
      </div>
    </div>
  );
}
