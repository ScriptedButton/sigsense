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
  floorOffsetDb: number;
  rangeDb: number;
  noiseFilter: NoiseFilterId;
  onPaletteChange: (id: WaterfallPaletteId) => void;
  onSpeedChange: (id: WaterfallSpeedId) => void;
  onDirectionChange: (id: WaterfallDirectionId) => void;
  onDbRangeChange: (id: DbRangeId) => void;
  onFloorOffsetChange: (db: number) => void;
  onRangeChange: (db: number) => void;
  onNoiseFilterChange: (id: NoiseFilterId) => void;
};

export function WaterfallControls({
  palette,
  speed,
  direction,
  dbRange,
  floorOffsetDb,
  rangeDb,
  noiseFilter,
  onPaletteChange,
  onSpeedChange,
  onDirectionChange,
  onDbRangeChange,
  onFloorOffsetChange,
  onRangeChange,
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

      {dbRange === "auto" && (
        <>
          <label className="flex items-center gap-1.5 text-[0.65rem] text-muted" title="Lower the black level to reveal faint signals; raise it to darken background noise">
            Black level
            <input aria-label="Waterfall black level" type="range" min={-12} max={12} step={1} value={floorOffsetDb} onChange={(e) => onFloorOffsetChange(Number(e.target.value))} className="w-20" />
            <output className="w-9 font-mono">{floorOffsetDb} dB</output>
          </label>
          <label className="flex items-center gap-1.5 text-[0.65rem] text-muted" title="Smaller ranges give weak signals more color contrast">
            Range
            <input aria-label="Waterfall signal range" type="range" min={16} max={60} step={1} value={rangeDb} onChange={(e) => onRangeChange(Number(e.target.value))} className="w-20" />
            <output className="w-9 font-mono">{rangeDb} dB</output>
          </label>
        </>
      )}

      <NoiseFilterControl
        value={noiseFilter}
        onChange={onNoiseFilterChange}
      />
    </div>
  );
}
