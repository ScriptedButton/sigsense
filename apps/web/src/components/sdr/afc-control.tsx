"use client";

import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";
import { isAfcScheme } from "@/lib/sdr/afc";

/**
 * Auto-center (AFC) for AM / NBFM / WBFM: tracks the spectrum peak or
 * occupied-band centroid and nudges the demodulator offset onto the signal.
 */
export function AfcControl() {
  const controller = useRadioController();
  const state = useSdrState();
  const supported = isAfcScheme(state.scheme);
  const playing = state.status === "playing";

  if (!supported) {
    return (
      <p className="mt-2 text-[0.65rem] text-muted">
        Auto center is available in AM, NBFM, and WBFM.
      </p>
    );
  }

  return (
    <div className="mt-2">
      <label
        className="flex items-center gap-2 text-sm text-ink"
        title="When a signal sits near your tune point, auto center nudges the offset onto its carrier (AM) or occupied-band centre (FM / WBFM)."
      >
        <input
          type="checkbox"
          checked={state.afc}
          onChange={(e) => controller.setAfc(e.target.checked)}
        />
        Auto center
      </label>

      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          className="mode-chip"
          disabled={!playing}
          title="Snap once onto the nearest AM / FM / WFM peak in the passband"
          onClick={() => controller.centerOnSignal()}
        >
          Center now
        </button>
        {state.afc && (
          <span
            className={`text-[0.65rem] ${
              state.afcLocked ? "text-accent" : "text-muted"
            }`}
            aria-live="polite"
          >
            {state.afcLocked ? "Locked on signal" : "Searching…"}
          </span>
        )}
      </div>
    </div>
  );
}
