"use client";

import { NoiseFilterControl } from "@/components/sdr/noise-filter-control";
import type {
  DbRangeId,
  NoiseFilterId,
  WaterfallDirectionId,
  WaterfallPaletteId,
  WaterfallSpeedId,
} from "@/lib/sdr/display-settings";
import {
  DB_RANGE_OPTIONS,
  WATERFALL_PALETTE_OPTIONS,
  WATERFALL_SPEED_OPTIONS,
} from "@/lib/sdr/display-settings";

type WaterfallControlsProps = {
  palette: WaterfallPaletteId;
  speed: WaterfallSpeedId;
  direction: WaterfallDirectionId;
  dbRange: DbRangeId;
  noiseFilter: NoiseFilterId;
  onPaletteChange: (id: WaterfallPaletteId) => void;
  onSpeedChange: (id: WaterfallSpeedId) => void;
  onDirectionChange: (id: WaterfallDirectionId) => void;
  onDbRangeChange: (id: DbRangeId) => void;
  onNoiseFilterChange: (id: NoiseFilterId) => void;
};

export function WaterfallControls({
  palette,
  speed,
  direction,
  dbRange,
  noiseFilter,
  onPaletteChange,
  onSpeedChange,
  onDirectionChange,
  onDbRangeChange,
  onNoiseFilterChange,
}: WaterfallControlsProps) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
      <label className="flex items-center gap-1.5 text-[0.65rem] text-muted">
        Palette
        <select
          className="input-sm"
          value={palette}
          aria-label="Waterfall color palette"
          onChange={(e) =>
            onPaletteChange(e.target.value as WaterfallPaletteId)
          }
        >
          {WATERFALL_PALETTE_OPTIONS.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-1.5 text-[0.65rem] text-muted">
        Speed
        <select
          className="input-sm"
          value={speed}
          aria-label="Waterfall scroll speed"
          onChange={(e) => onSpeedChange(e.target.value as WaterfallSpeedId)}
        >
          {WATERFALL_SPEED_OPTIONS.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-1.5 text-[0.65rem] text-muted">
        Newest
        <select
          className="input-sm"
          value={direction}
          aria-label="Waterfall scroll direction"
          onChange={(e) =>
            onDirectionChange(e.target.value as WaterfallDirectionId)
          }
        >
          <option value="top">Top</option>
          <option value="bottom">Bottom</option>
        </select>
      </label>

      <label className="flex items-center gap-1.5 text-[0.65rem] text-muted">
        Contrast
        <select
          className="input-sm"
          value={dbRange}
          aria-label="Spectrum and waterfall contrast range"
          onChange={(e) => onDbRangeChange(e.target.value as DbRangeId)}
        >
          {DB_RANGE_OPTIONS.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
      </label>

      <NoiseFilterControl
        value={noiseFilter}
        onChange={onNoiseFilterChange}
      />
    </div>
  );
}
