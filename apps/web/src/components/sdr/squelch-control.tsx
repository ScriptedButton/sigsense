"use client";

import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";
import {
  AUTO_SQUELCH_DEFAULT_MARGIN_DB,
  SQUELCH_MAX_DB,
} from "@/lib/sdr/squelch";

function squelchLabel(auto: boolean, level: number, open: boolean): string {
  if (!auto && level <= 0) return "Squelch · off";
  const amount = auto
    ? `${level > 0 ? level : AUTO_SQUELCH_DEFAULT_MARGIN_DB} dB margin`
    : `${level} dB`;
  const gate = open ? "open" : "closed";
  return `Squelch · ${amount} · ${gate}`;
}

/**
 * Manual and auto squelch for demod audio. Threshold is relative to the
 * spectrum noise floor so it filters hash without needing per-mode SNR.
 */
export function SquelchControl() {
  const controller = useRadioController();
  const state = useSdrState();
  const active = state.autoSquelch || state.squelch > 0;

  return (
    <div className="mt-2">
      <label
        className="flex items-center gap-2 text-sm text-ink"
        title="Squelch mutes the audio until a signal rises above the noise. Auto follows the noise floor for you; manual sets the margin in dB above it."
      >
        <input
          type="checkbox"
          checked={state.autoSquelch}
          onChange={(e) => controller.setAutoSquelch(e.target.checked)}
        />
        Auto squelch
      </label>

      <label className="mt-2 block text-xs text-muted">
        {squelchLabel(state.autoSquelch, state.squelch, state.squelchOpen)}
        <input
          type="range"
          className="slider mt-1"
          min={0}
          max={SQUELCH_MAX_DB}
          step={1}
          value={state.squelch}
          onChange={(e) => controller.setSquelch(Number(e.target.value))}
        />
      </label>

      {active && (
        <p
          className={`mt-1 text-[0.65rem] ${
            state.squelchOpen ? "text-accent" : "text-muted"
          }`}
          aria-live="polite"
        >
          {state.squelchOpen
            ? "Signal above threshold — audio unmuted"
            : "Noise floor — audio muted"}
        </p>
      )}
    </div>
  );
}
