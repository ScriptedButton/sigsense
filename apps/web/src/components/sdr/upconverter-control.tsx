"use client";

import { useHackrf } from "@/components/HackrfPanel";
import { useSdrState } from "@/components/sdr/sdr-provider";
import { haptic } from "@/lib/haptics";
import {
  getUpconverter,
  UPCONVERTERS,
  type UpconverterId,
} from "@/lib/sdr/upconverters";

/** Spec-sheet style: "125 MHz", "10 kHz". */
const specHz = (hz: number) =>
  hz >= 1e6 ? `${hz / 1e6} MHz` : `${hz / 1e3} kHz`;

const OPTIONS: { id: UpconverterId; label: string; title: string }[] = [
  { id: "none", label: "Direct", title: "Antenna connected straight to the HackRF" },
  ...UPCONVERTERS.map((u) => ({
    id: u.id,
    label: u.shortLabel,
    title: `${u.label}: ${specHz(u.rfMinHz)} – ${specHz(u.rfMaxHz)}`,
  })),
];

/** Chip row choosing what sits between the antenna and the HackRF. */
export function UpconverterChips({ large = false }: { large?: boolean }) {
  const { upconverter, setUpconverter } = useHackrf();
  return (
    <div className="flex flex-wrap gap-1" role="group" aria-label="Upconverter">
      {OPTIONS.map((option) => {
        const active = upconverter === option.id;
        return (
          <button
            key={option.id}
            type="button"
            className={`mode-chip ${large ? "mode-chip-md" : ""} ${active ? "mode-chip-active" : ""}`}
            title={option.title}
            aria-pressed={active}
            onClick={() => {
              if (active) return;
              haptic(10);
              setUpconverter(option.id);
            }}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/** What the selected upconverter does and how to set it up. */
export function UpconverterHint({ className = "" }: { className?: string }) {
  const converter = getUpconverter(useHackrf().upconverter);
  if (!converter) {
    return (
      <p className={className}>
        Using an upconverter for HF? Pick it here so frequencies stay correct.
      </p>
    );
  }
  return (
    <p className={className}>
      Tunes the HackRF {specHz(converter.loHz)} higher; frequencies
      shown are the real ones. Covers {specHz(converter.rfMinHz)} –{" "}
      {specHz(converter.rfMaxHz)}. {converter.note}
    </p>
  );
}

/** Upconverter choice for the Radio rail; HackRF only. */
export function UpconverterControl() {
  const { connection } = useSdrState();
  if (connection !== "hackrf" && connection !== "disconnected") return null;
  return (
    <fieldset className="control-group">
      <legend
        className="control-label"
        title="A converter between antenna and HackRF that shifts HF signals up into a range the HackRF receives well."
      >
        Upconverter
      </legend>
      <UpconverterChips />
      <UpconverterHint className="mt-2 text-[0.7rem] leading-snug text-muted" />
    </fieldset>
  );
}
