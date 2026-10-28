"use client";

import {
  DISPLAY_MODES,
  type DisplayMode,
} from "@/lib/sdr/display-mode";

type DisplayModeToggleProps = {
  value: DisplayMode;
  onChange: (mode: DisplayMode) => void;
};

export function DisplayModeToggle({ value, onChange }: DisplayModeToggleProps) {
  return (
    <div
      className="flex gap-1"
      role="group"
      aria-label="Display mode"
    >
      {DISPLAY_MODES.map((mode) => (
        <button
          key={mode.id}
          type="button"
          className={`mode-chip ${value === mode.id ? "mode-chip-active" : ""}`}
          aria-pressed={value === mode.id}
          onClick={() => onChange(mode.id)}
        >
          {mode.label}
        </button>
      ))}
    </div>
  );
}
