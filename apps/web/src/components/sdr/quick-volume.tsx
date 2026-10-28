"use client";

import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";

/** Compact volume control for the always-visible primary toolbar. */
export function QuickVolume() {
  const controller = useRadioController();
  const state = useSdrState();
  const pct = Math.round(state.volume * 100);

  return (
    <label className="quick-volume">
      <span className="quick-volume-label">Vol</span>
      <input
        type="range"
        className="slider quick-volume-slider"
        min={0}
        max={100}
        value={pct}
        aria-valuetext={`${pct}%`}
        onChange={(e) => controller.setVolume(Number(e.target.value) / 100)}
      />
      <span className="quick-volume-value font-mono tabular-nums">{pct}</span>
    </label>
  );
}
