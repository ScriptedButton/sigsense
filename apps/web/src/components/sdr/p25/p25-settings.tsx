"use client";

import { useRadioController, useP25State } from "@/components/sdr/sdr-provider";
import { DEMO_P25_FREQUENCY_HZ, type P25Mode } from "@/lib/sdr/p25/types";

export function P25Settings() {
  const controller = useRadioController();
  const p25 = useP25State();
  const { config } = p25;

  const nacText =
    config.nac == null || config.nac < 0
      ? ""
      : config.nac.toString(16).toUpperCase();

  const ccText = config.controlChannelsHz
    .map((hz) => (hz / 1e6).toFixed(4))
    .join(", ");

  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap gap-1">
        {(
          [
            ["conventional", "Conventional"],
            ["trunk", "Trunk"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={`mode-chip ${config.mode === id ? "mode-chip-active" : ""}`}
            onClick={() =>
              controller.patchP25Config({
                mode: id as P25Mode,
                controlChannelsHz:
                  id === "trunk" && config.controlChannelsHz.length === 0
                    ? [DEMO_P25_FREQUENCY_HZ]
                    : config.controlChannelsHz,
              })
            }
          >
            {label}
          </button>
        ))}
      </div>

      <label className="block text-xs text-muted">
        NAC filter (hex, blank = any)
        <input
          type="text"
          className="fsk-select mt-1 w-full"
          placeholder="e.g. 293"
          value={nacText}
          onChange={(e) => {
            const raw = e.target.value.trim();
            if (!raw) {
              controller.patchP25Config({ nac: null });
              return;
            }
            const n = Number.parseInt(raw, 16);
            if (Number.isFinite(n)) {
              controller.patchP25Config({ nac: n & 0xfff });
            }
          }}
        />
      </label>

      {config.mode === "trunk" ? (
        <label className="block text-xs text-muted">
          Control channels (MHz, comma-separated)
          <input
            type="text"
            className="fsk-select mt-1 w-full"
            value={ccText}
            onChange={(e) => {
              const freqs = e.target.value
                .split(/[,\s]+/)
                .map((s) => Number.parseFloat(s))
                .filter((n) => Number.isFinite(n) && n > 0)
                .map((mhz) => Math.round(mhz * 1e6));
              controller.patchP25Config({ controlChannelsHz: freqs });
            }}
          />
        </label>
      ) : null}

      <label className="block text-xs text-muted">
        Hangtime · {config.hangtimeMs} ms
        <input
          type="range"
          className="slider mt-1"
          min={500}
          max={8000}
          step={100}
          value={config.hangtimeMs}
          onChange={(e) =>
            controller.patchP25Config({ hangtimeMs: Number(e.target.value) })
          }
        />
      </label>

      <div className="flex flex-wrap items-center gap-3 text-sm text-ink">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={config.playVoice}
            onChange={(e) =>
              controller.patchP25Config({ playVoice: e.target.checked })
            }
          />
          Play voice
        </label>
        {config.mode === "trunk" ? (
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={config.followGrants}
              onChange={(e) =>
                controller.patchP25Config({ followGrants: e.target.checked })
              }
            />
            Follow grants
          </label>
        ) : null}
      </div>
    </div>
  );
}
