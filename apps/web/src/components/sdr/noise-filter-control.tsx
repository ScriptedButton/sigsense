"use client";

import {
  NOISE_FILTER_OPTIONS,
  type NoiseFilterId,
} from "@/lib/sdr/display-settings";

type NoiseFilterControlProps = {
  value: NoiseFilterId;
  onChange: (id: NoiseFilterId) => void;
};

/** Shared Noise select for scope waterfall chrome and the 3D spectrometer. */
export function NoiseFilterControl({
  value,
  onChange,
}: NoiseFilterControlProps) {
  return (
    <label className="flex items-center gap-1.5 text-[0.65rem] text-muted">
      Noise
      <select
        className="input-sm"
        value={value}
        aria-label="Spectrum noise filter strength"
        title="Suppress noise-floor bins on the spectrum display"
        onChange={(e) => onChange(e.target.value as NoiseFilterId)}
      >
        {NOISE_FILTER_OPTIONS.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
