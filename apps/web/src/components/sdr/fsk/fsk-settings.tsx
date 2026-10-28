"use client";

import { useRadioController, useFskState } from "@/components/sdr/sdr-provider";
import {
  FSK_PRESETS,
  type FskPresetId,
} from "@/lib/sdr/fsk/types";

const PRESET_IDS = Object.keys(FSK_PRESETS) as Exclude<FskPresetId, "custom">[];

export function FskSettings() {
  const controller = useRadioController();
  const fsk = useFskState();
  const { config } = fsk;

  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap gap-1">
        {PRESET_IDS.map((id) => (
          <button
            key={id}
            type="button"
            className={`mode-chip ${config.preset === id ? "mode-chip-active" : ""}`}
            onClick={() => controller.setFskPreset(id)}
          >
            {FSK_PRESETS[id].label}
          </button>
        ))}
      </div>

      <label className="block text-xs text-muted">
        Baud · {config.baud % 1 === 0 ? config.baud : config.baud.toFixed(2)}
        <input
          type="range"
          className="slider mt-1"
          min={45}
          max={1200}
          step={1}
          value={Math.round(config.baud)}
          onChange={(e) =>
            controller.patchFskConfig({ baud: Number(e.target.value) })
          }
        />
      </label>

      {config.detector === "tones" ? (
        <div className="grid grid-cols-2 gap-2">
          <label className="block text-xs text-muted">
            Mark · {config.markHz} Hz
            <input
              type="range"
              className="slider mt-1"
              min={500}
              max={3000}
              step={25}
              value={config.markHz}
              onChange={(e) =>
                controller.patchFskConfig({ markHz: Number(e.target.value) })
              }
            />
          </label>
          <label className="block text-xs text-muted">
            Space · {config.spaceHz} Hz
            <input
              type="range"
              className="slider mt-1"
              min={500}
              max={3000}
              step={25}
              value={config.spaceHz}
              onChange={(e) =>
                controller.patchFskConfig({ spaceHz: Number(e.target.value) })
              }
            />
          </label>
        </div>
      ) : (
        <label className="block text-xs text-muted">
          Shift · {config.shiftHz} Hz
          <input
            type="range"
            className="slider mt-1"
            min={85}
            max={1000}
            step={5}
            value={config.shiftHz}
            onChange={(e) =>
              controller.patchFskConfig({ shiftHz: Number(e.target.value) })
            }
          />
        </label>
      )}

      <div className="flex flex-wrap items-center gap-3 text-sm text-ink">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={config.inverted}
            onChange={(e) => controller.setFskInverted(e.target.checked)}
          />
          Invert
        </label>
        <label className="flex items-center gap-2">
          <span className="text-xs text-muted">Encoding</span>
          <select
            className="fsk-select"
            value={config.encoding}
            onChange={(e) =>
              controller.patchFskConfig({
                encoding: e.target.value as "baudot" | "ascii",
              })
            }
          >
            <option value="baudot">Baudot</option>
            <option value="ascii">ASCII</option>
          </select>
        </label>
        <label className="flex items-center gap-2">
          <span className="text-xs text-muted">Detect</span>
          <select
            className="fsk-select"
            value={config.detector}
            onChange={(e) =>
              controller.patchFskConfig({
                detector: e.target.value as "discriminator" | "tones",
              })
            }
          >
            <option value="discriminator">FM level</option>
            <option value="tones">AFSK tones</option>
          </select>
        </label>
      </div>
    </div>
  );
}
