"use client";

import { useRadioController, useWefaxState } from "@/components/sdr/sdr-provider";
import {
  WEFAX_PRESETS,
  type WefaxIoc,
  type WefaxLpm,
  type WefaxPresetId,
  type WefaxSyncMode,
} from "@/lib/sdr/wefax/types";

const PRESET_IDS = Object.keys(WEFAX_PRESETS) as Exclude<
  WefaxPresetId,
  "custom"
>[];

export function WefaxSettings() {
  const controller = useRadioController();
  const wefax = useWefaxState();
  const { config } = wefax;

  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap gap-1">
        {PRESET_IDS.map((id) => (
          <button
            key={id}
            type="button"
            className={`mode-chip ${config.preset === id ? "mode-chip-active" : ""}`}
            onClick={() => controller.setWefaxPreset(id)}
          >
            {WEFAX_PRESETS[id].label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <label className="block text-xs text-muted">
          Black · {config.blackHz} Hz
          <input
            type="range"
            className="slider mt-1"
            min={1000}
            max={2000}
            step={25}
            value={config.blackHz}
            onChange={(e) =>
              controller.patchWefaxConfig({ blackHz: Number(e.target.value) })
            }
          />
        </label>
        <label className="block text-xs text-muted">
          White · {config.whiteHz} Hz
          <input
            type="range"
            className="slider mt-1"
            min={1800}
            max={2800}
            step={25}
            value={config.whiteHz}
            onChange={(e) =>
              controller.patchWefaxConfig({ whiteHz: Number(e.target.value) })
            }
          />
        </label>
      </div>

      <label className="block text-xs text-muted">
        Slant · {config.slantPct > 0 ? "+" : ""}
        {config.slantPct.toFixed(1)}%
        <input
          type="range"
          className="slider mt-1"
          min={-5}
          max={5}
          step={0.1}
          value={config.slantPct}
          onChange={(e) =>
            controller.patchWefaxConfig({ slantPct: Number(e.target.value) })
          }
        />
      </label>

      <div className="flex flex-wrap items-center gap-3 text-sm text-ink">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={config.inverted}
            onChange={(e) => controller.setWefaxInverted(e.target.checked)}
          />
          Invert
        </label>
        <label className="flex items-center gap-2">
          <span className="text-xs text-muted">IOC</span>
          <select
            className="fsk-select"
            value={config.ioc}
            onChange={(e) =>
              controller.patchWefaxConfig({
                ioc: Number(e.target.value) as WefaxIoc,
              })
            }
          >
            <option value={576}>576</option>
            <option value={288}>288</option>
          </select>
        </label>
        <label className="flex items-center gap-2">
          <span className="text-xs text-muted">LPM</span>
          <select
            className="fsk-select"
            value={config.lpm}
            onChange={(e) =>
              controller.patchWefaxConfig({
                lpm: Number(e.target.value) as WefaxLpm,
              })
            }
          >
            <option value={120}>120</option>
            <option value={60}>60</option>
          </select>
        </label>
        <label className="flex items-center gap-2">
          <span className="text-xs text-muted">Sync</span>
          <select
            className="fsk-select"
            value={config.sync}
            onChange={(e) =>
              controller.patchWefaxConfig({
                sync: e.target.value as WefaxSyncMode,
              })
            }
          >
            <option value="auto">Auto APT</option>
            <option value="manual">Manual</option>
          </select>
        </label>
      </div>
    </div>
  );
}
