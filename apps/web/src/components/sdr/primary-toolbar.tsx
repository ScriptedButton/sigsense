"use client";

import { BandPresets } from "@/components/sdr/band-presets";
import { ModeSchemeChips } from "@/components/sdr/mode-scheme-chips";
import { QuickVolume } from "@/components/sdr/quick-volume";

/**
 * Always-on strip for the daily loop after connect: pick mode, set volume,
 * jump to a band. Detailed RF/audio lives in the rail.
 */
export function PrimaryToolbar() {
  return (
    <div className="primary-toolbar">
      <div className="primary-toolbar-group">
        <span className="primary-toolbar-label">Mode</span>
        <ModeSchemeChips size="md" />
      </div>

      <div className="primary-toolbar-divider" aria-hidden />

      <div className="primary-toolbar-group primary-toolbar-volume">
        <QuickVolume />
      </div>

      <div className="primary-toolbar-divider" aria-hidden />

      <div className="primary-toolbar-group primary-toolbar-presets min-w-0 flex-1">
        <span className="primary-toolbar-label">Presets</span>
        <div className="primary-toolbar-presets-scroll">
          <BandPresets variant="inline" />
        </div>
      </div>
    </div>
  );
}
