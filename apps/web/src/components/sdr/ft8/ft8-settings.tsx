"use client";

import { useRadioController, useFt8State } from "@/components/sdr/sdr-provider";
import type { Ft8Depth } from "@/lib/sdr/ft8/types";

const DEPTHS: { value: Ft8Depth; label: string }[] = [
  { value: 1, label: "Fast" },
  { value: 2, label: "Normal" },
  { value: 3, label: "Deep" },
  { value: 4, label: "Deeper" },
];

export function Ft8Settings() {
  const controller = useRadioController();
  const ft8 = useFt8State();
  const { config } = ft8;

  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap gap-1">
        {DEPTHS.map(({ value, label }) => (
          <button
            key={value}
            type="button"
            className={`mode-chip ${config.depth === value ? "mode-chip-active" : ""}`}
            onClick={() => controller.patchFt8Config({ depth: value })}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <label className="block text-xs text-muted">
          Low · {config.freqLow} Hz
          <input
            type="range"
            className="slider mt-1"
            min={100}
            max={2500}
            step={50}
            value={config.freqLow}
            onChange={(e) => {
              const freqLow = Number(e.target.value);
              controller.patchFt8Config({
                freqLow,
                freqHigh: Math.max(ft8.config.freqHigh, freqLow + 100),
              });
            }}
          />
        </label>
        <label className="block text-xs text-muted">
          High · {config.freqHigh} Hz
          <input
            type="range"
            className="slider mt-1"
            min={500}
            max={4000}
            step={50}
            value={config.freqHigh}
            onChange={(e) => {
              const freqHigh = Number(e.target.value);
              controller.patchFt8Config({
                freqHigh,
                freqLow: Math.min(ft8.config.freqLow, freqHigh - 100),
              });
            }}
          />
        </label>
      </div>
    </div>
  );
}
