"use client";

import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";
import type { DemodScheme } from "@/lib/sdr/types";

export const SCHEME_LABELS: Record<DemodScheme, string> = {
  WBFM: "WBFM",
  NBFM: "NBFM",
  AM: "AM",
  USB: "USB",
  LSB: "LSB",
  CW: "CW",
};

const SCHEMES = Object.keys(SCHEME_LABELS) as DemodScheme[];

type ModeSchemeChipsProps = {
  /** Slightly taller chips for the primary toolbar. */
  size?: "sm" | "md";
};

/** Always-visible demod scheme picker — the second step after frequency. */
export function ModeSchemeChips({ size = "sm" }: ModeSchemeChipsProps) {
  const controller = useRadioController();
  const state = useSdrState();

  return (
    <div
      className={`flex flex-wrap gap-1 ${size === "md" ? "gap-1.5" : ""}`}
      role="group"
      aria-label="Demodulation mode"
    >
      {SCHEMES.map((scheme) => (
        <button
          key={scheme}
          type="button"
          className={`mode-chip ${size === "md" ? "mode-chip-md" : ""} ${
            state.scheme === scheme ? "mode-chip-active" : ""
          }`}
          aria-pressed={state.scheme === scheme}
          onClick={() => controller.setScheme(scheme)}
        >
          {SCHEME_LABELS[scheme]}
        </button>
      ))}
    </div>
  );
}
