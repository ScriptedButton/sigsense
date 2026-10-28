"use client";

import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";
import { BAND_PRESETS } from "@/lib/sdr/format";

type BandPresetsProps = {
  /** Inline strip for the primary toolbar (no fieldset chrome). */
  variant?: "panel" | "inline";
};

export function BandPresets({ variant = "panel" }: BandPresetsProps) {
  const controller = useRadioController();
  const state = useSdrState();

  const chips = (
    <div
      className={
        variant === "inline"
          ? "flex flex-nowrap gap-1.5"
          : "flex flex-wrap gap-1.5"
      }
    >
      {BAND_PRESETS.map((preset) => {
        const active =
          Math.abs(state.frequencyHz - preset.frequencyHz) < 1_000 &&
          state.scheme === preset.scheme;
        return (
          <button
            key={preset.id}
            type="button"
            className={`preset-chip ${variant === "inline" ? "preset-chip-nowrap" : ""} ${
              active ? "preset-chip-active" : ""
            }`}
            onClick={() => {
              controller.setScheme(preset.scheme);
              void controller.setFrequency(preset.frequencyHz);
              controller.setOffset(0);
            }}
          >
            {preset.label}
          </button>
        );
      })}
    </div>
  );

  if (variant === "inline") {
    return chips;
  }

  return (
    <fieldset className="control-group">
      <legend className="control-label">Presets</legend>
      {chips}
    </fieldset>
  );
}
