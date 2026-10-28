"use client";

import { useCallback, useEffect, useState } from "react";

import { FrequencyDigitDisplay } from "@/components/sdr/frequency-digit-display";
import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";
import {
  FREQ_FORMAT_OPTIONS,
  type FreqFormatId,
  type FreqTargetId,
} from "@/lib/sdr/display-settings";
import {
  formatFrequencyCompact,
  formatFrequencyWith,
  parseFrequencyInput,
} from "@/lib/sdr/format";

const STEPS = [
  { label: "1 Hz", hz: 1 },
  { label: "10 Hz", hz: 10 },
  { label: "100 Hz", hz: 100 },
  { label: "1 kHz", hz: 1_000 },
  { label: "5 kHz", hz: 5_000 },
  { label: "25 kHz", hz: 25_000 },
  { label: "100 kHz", hz: 100_000 },
  { label: "1 MHz", hz: 1_000_000 },
] as const;

type FrequencyControlProps = {
  freqTarget: FreqTargetId;
  freqFormat: FreqFormatId;
  onFreqTargetChange: (id: FreqTargetId) => void;
  onFreqFormatChange: (id: FreqFormatId) => void;
  /** Hero VFO lives in the header; rail can skip the digit row. */
  showDigits?: boolean;
};

export function FrequencyControl({
  freqTarget,
  freqFormat,
  onFreqTargetChange,
  onFreqFormatChange,
  showDigits = true,
}: FrequencyControlProps) {
  const controller = useRadioController();
  const state = useSdrState();
  const displayHz =
    freqTarget === "tune"
      ? state.frequencyHz + state.offsetHz
      : state.frequencyHz;

  const [draft, setDraft] = useState(formatFrequencyCompact(displayHz));
  const [unit, setUnit] = useState<"MHz" | "kHz" | "Hz">("MHz");
  const [stepHz, setStepHz] = useState(100_000);

  useEffect(() => {
    setDraft(formatFrequencyCompact(displayHz));
    if (displayHz >= 1e6) setUnit("MHz");
    else if (displayHz >= 1e3) setUnit("kHz");
    else setUnit("Hz");
  }, [displayHz]);

  const setAbsolute = useCallback(
    (hz: number) => {
      if (freqTarget === "tune") {
        controller.tuneToHz(hz);
      } else {
        void controller.setFrequency(hz);
      }
    },
    [controller, freqTarget],
  );

  const nudge = useCallback(
    (deltaHz: number) => {
      if (freqTarget === "tune") {
        controller.tuneToHz(displayHz + deltaHz);
      } else {
        void controller.nudgeFrequency(deltaHz);
      }
    },
    [controller, displayHz, freqTarget],
  );

  const commit = () => {
    const hz = parseFrequencyInput(draft, unit);
    if (hz == null) {
      setDraft(formatFrequencyCompact(displayHz));
      return;
    }
    setAbsolute(hz);
  };

  return (
    <fieldset className="control-group">
      <legend
        className="control-label"
        title="Listen is the frequency you hear (the offset from the receiver centre). Center is where the hardware is tuned; the offset moves inside its span."
      >
        Frequency
      </legend>

      <div className="mb-2 flex flex-wrap items-center gap-2">
        <div className="flex gap-1">
          <button
            type="button"
            className={`mode-chip ${freqTarget === "tune" ? "mode-chip-active" : ""}`}
            aria-pressed={freqTarget === "tune"}
            onClick={() => onFreqTargetChange("tune")}
          >
            Tune
          </button>
          <button
            type="button"
            className={`mode-chip ${freqTarget === "center" ? "mode-chip-active" : ""}`}
            aria-pressed={freqTarget === "center"}
            onClick={() => onFreqTargetChange("center")}
          >
            Center
          </button>
        </div>
        <label className="ml-auto flex items-center gap-1.5 text-[0.65rem] text-muted">
          Format
          <select
            className="input-sm"
            value={freqFormat}
            aria-label="Frequency readout format"
            onChange={(e) =>
              onFreqFormatChange(e.target.value as FreqFormatId)
            }
          >
            {FREQ_FORMAT_OPTIONS.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {showDigits && (
        <FrequencyDigitDisplay
          frequencyHz={displayHz}
          onChange={setAbsolute}
          size="md"
        />
      )}

      <p className={`${showDigits ? "mt-1.5" : ""} font-mono text-[0.7rem] tabular-nums text-accent`}>
        {formatFrequencyWith(displayHz, freqFormat)}
        <span className="text-muted">
          {" "}
          · {freqTarget === "tune" ? "listen" : "LO"}
        </span>
      </p>

      <div className="mt-2 flex items-stretch gap-1.5">
        <button
          type="button"
          className="btn-icon"
          aria-label={`Decrease by ${stepHz} Hz`}
          onClick={() => nudge(-stepHz)}
        >
          −
        </button>
        <div className="flex min-w-0 flex-1 items-stretch overflow-hidden rounded-md border border-line bg-surface-2 focus-within:border-primary">
          <input
            className="freq-input min-w-0 flex-1"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.currentTarget.blur();
              }
            }}
            inputMode="decimal"
            aria-label={
              freqTarget === "tune" ? "Tune frequency" : "Center frequency"
            }
          />
          <select
            className="border-l border-line bg-transparent px-2 text-sm text-muted outline-none"
            value={unit}
            onChange={(e) => setUnit(e.target.value as "MHz" | "kHz" | "Hz")}
            aria-label="Frequency unit"
          >
            <option value="MHz">MHz</option>
            <option value="kHz">kHz</option>
            <option value="Hz">Hz</option>
          </select>
        </div>
        <button
          type="button"
          className="btn-icon"
          aria-label={`Increase by ${stepHz} Hz`}
          onClick={() => nudge(stepHz)}
        >
          +
        </button>
      </div>
      <label className="mt-2 flex items-center gap-2 text-xs text-muted">
        Step
        <select
          className="input-sm flex-1"
          value={stepHz}
          onChange={(e) => setStepHz(Number(e.target.value))}
        >
          {STEPS.map((s) => (
            <option key={s.hz} value={s.hz}>
              {s.label}
            </option>
          ))}
        </select>
      </label>
    </fieldset>
  );
}
