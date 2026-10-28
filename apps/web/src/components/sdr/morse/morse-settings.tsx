"use client";

import { useRadioController, useMorseState } from "@/components/sdr/sdr-provider";
import {
  MORSE_PRESETS,
  type MorsePresetId,
} from "@/lib/sdr/morse/types";

const PRESET_IDS = Object.keys(MORSE_PRESETS) as Exclude<
  MorsePresetId,
  "custom"
>[];

export function MorseSettings() {
  const controller = useRadioController();
  const morse = useMorseState();
  const { config } = morse;

  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap gap-1">
        {PRESET_IDS.map((id) => (
          <button
            key={id}
            type="button"
            className={`mode-chip ${config.preset === id ? "mode-chip-active" : ""}`}
            onClick={() => controller.setMorsePreset(id)}
          >
            {MORSE_PRESETS[id].label}
          </button>
        ))}
      </div>

      <label className="block text-xs text-muted">
        WPM · {config.wpm}
        <input
          type="range"
          className="slider mt-1"
          min={5}
          max={40}
          step={1}
          value={config.wpm}
          onChange={(e) =>
            controller.patchMorseConfig({ wpm: Number(e.target.value) })
          }
        />
      </label>

      <label className="block text-xs text-muted">
        Tone · {config.toneHz} Hz
        <input
          type="range"
          className="slider mt-1"
          min={300}
          max={1200}
          step={10}
          value={config.toneHz}
          disabled={config.detector === "envelope"}
          onChange={(e) =>
            controller.patchMorseConfig({ toneHz: Number(e.target.value) })
          }
        />
      </label>

      <label className="block text-xs text-muted">
        Sensitivity · {Math.round(config.sensitivity * 100)}%
        <input
          type="range"
          className="slider mt-1"
          min={0}
          max={100}
          step={1}
          value={Math.round(config.sensitivity * 100)}
          onChange={(e) =>
            controller.patchMorseConfig({
              sensitivity: Number(e.target.value) / 100,
            })
          }
        />
      </label>

      <div className="flex flex-wrap items-center gap-3 text-sm text-ink">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={config.autoSpeed}
            onChange={(e) =>
              controller.patchMorseConfig({ autoSpeed: e.target.checked })
            }
          />
          Auto speed
        </label>
        <label className="flex items-center gap-2">
          <span className="text-xs text-muted">Detect</span>
          <select
            className="fsk-select"
            value={config.detector}
            onChange={(e) =>
              controller.patchMorseConfig({
                detector: e.target.value as "tone" | "envelope",
              })
            }
          >
            <option value="tone">Tone</option>
            <option value="envelope">Envelope</option>
          </select>
        </label>
      </div>
    </div>
  );
}
