"use client";

import { AfcControl } from "@/components/sdr/afc-control";
import { ModeSchemeChips, SCHEME_LABELS } from "@/components/sdr/mode-scheme-chips";
import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";
import { SquelchControl } from "@/components/sdr/squelch-control";
import type { DemodScheme } from "@/lib/sdr/types";

const SCHEME_META: Record<
  DemodScheme,
  { hasBw: boolean; hasStereo: boolean }
> = {
  WBFM: { hasBw: true, hasStereo: true },
  NBFM: { hasBw: true, hasStereo: false },
  AM: { hasBw: true, hasStereo: false },
  USB: { hasBw: true, hasStereo: false },
  LSB: { hasBw: true, hasStereo: false },
  CW: { hasBw: true, hasStereo: false },
};

type ModeControlProps = {
  /** When false, scheme chips live in the primary toolbar instead. */
  showSchemes?: boolean;
};

export function ModeControl({ showSchemes = true }: ModeControlProps) {
  const controller = useRadioController();
  const state = useSdrState();
  const meta = SCHEME_META[state.scheme];

  return (
    <fieldset className="control-group">
      <legend className="control-label">
        {showSchemes ? "Mode" : `Mode · ${SCHEME_LABELS[state.scheme]}`}
      </legend>

      {showSchemes && <ModeSchemeChips />}

      {meta.hasBw && (
        <label className="mt-3 block text-xs text-muted">
          Bandwidth · {state.bandwidthHz.toLocaleString()} Hz
          <input
            type="range"
            className="slider mt-1"
            min={500}
            max={state.scheme === "WBFM" ? 200_000 : 20_000}
            step={100}
            value={state.bandwidthHz}
            onChange={(e) => controller.setBandwidth(Number(e.target.value))}
          />
        </label>
      )}

      <SquelchControl />
      <AfcControl />

      {meta.hasStereo && (
        <label className="mt-2 flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={state.stereo}
            onChange={(e) => controller.setStereo(e.target.checked)}
          />
          Stereo
          {state.stereoActive && (
            <span className="text-xs text-accent">· locked</span>
          )}
        </label>
      )}

      <label className="mt-3 block text-xs text-muted">
        Offset · {state.offsetHz.toLocaleString()} Hz
        <input
          type="range"
          className="slider mt-1"
          min={-Math.floor(state.sampleRate / 2)}
          max={Math.floor(state.sampleRate / 2)}
          step={100}
          value={state.offsetHz}
          onChange={(e) => controller.setOffset(Number(e.target.value))}
        />
      </label>
    </fieldset>
  );
}
