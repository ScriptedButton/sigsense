"use client";

import { useEffect, useRef, useState } from "react";

import { Sheet } from "@/components/sdr/sheet";
import { haptic } from "@/lib/haptics";

type FrequencyKeypadProps = {
  frequencyHz: number;
  onChange: (hz: number) => void;
  onClose: () => void;
};

/** Full-precision readout: GMRS channels sit on 12.5 kHz steps (462.5625). */
function describeHz(hz: number): string {
  if (hz >= 1e6) return `${Number((hz / 1e6).toFixed(6))} MHz`;
  if (hz >= 1e3) return `${Number((hz / 1e3).toFixed(3))} kHz`;
  return `${hz} Hz`;
}

/** Smallest and largest frequencies the receivers can tune. */
const MIN_HZ = 100_000;
const MAX_HZ = 6_000_000_000;

const STEPS: { label: string; hz: number }[] = [
  { label: "−1 MHz", hz: -1_000_000 },
  { label: "−100 kHz", hz: -100_000 },
  { label: "−12.5 kHz", hz: -12_500 },
  { label: "+12.5 kHz", hz: 12_500 },
  { label: "+100 kHz", hz: 100_000 },
  { label: "+1 MHz", hz: 1_000_000 },
];

/**
 * Typed entry is MHz ("462.5625"); a trailing k means kHz ("810k") and a
 * trailing g means GHz. A bare number of 7 digits or more is taken as Hz.
 */
export function parseFrequencyInput(text: string): number | null {
  const m = text
    .trim()
    .toLowerCase()
    .match(/^(\d+(?:[.,]\d*)?|[.,]\d+)\s*(g|m|k|hz)?(?:hz)?$/);
  if (!m) return null;
  const value = parseFloat(m[1].replace(",", "."));
  if (!Number.isFinite(value)) return null;
  const unit = m[2];
  let hz: number;
  if (unit === "g") hz = value * 1e9;
  else if (unit === "k") hz = value * 1e3;
  else if (unit === "hz") hz = value;
  else if (unit === "m") hz = value * 1e6;
  else hz = value >= 1e7 ? value : value * 1e6;
  hz = Math.round(hz);
  return hz >= MIN_HZ && hz <= MAX_HZ ? hz : null;
}

/**
 * Phone-friendly frequency entry. The digit row is hard to hit with a thumb,
 * so this offers a numeric field and big step buttons in a bottom sheet.
 */
export function FrequencyKeypad({
  frequencyHz,
  onChange,
  onClose,
}: FrequencyKeypadProps) {
  const [text, setText] = useState(() =>
    String(Number((frequencyHz / 1e6).toFixed(6))),
  );
  const inputRef = useRef<HTMLInputElement>(null);
  const parsed = parseFrequencyInput(text);

  useEffect(() => {
    inputRef.current?.select();
  }, []);

  const apply = (hz: number) => {
    haptic(12);
    onChange(hz);
    onClose();
  };

  return (
    <Sheet title="Frequency" onClose={onClose} initialFocus={inputRef}>
      <div className="freq-keypad">
        <form
          className="freq-keypad-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (parsed !== null) apply(parsed);
          }}
        >
          <input
            ref={inputRef}
            className="freq-keypad-input"
            inputMode="decimal"
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            aria-label="Frequency in MHz (add k for kHz)"
            aria-invalid={parsed === null}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <span className="freq-keypad-unit">MHz</span>
          <button
            type="submit"
            className="btn-primary"
            disabled={parsed === null}
          >
            Tune
          </button>
        </form>
        <p className="freq-keypad-hint" aria-live="polite">
          {parsed === null
            ? "Enter a frequency in MHz, e.g. 462.5625 · add k for kHz (810k)"
            : `= ${describeHz(parsed)}`}
        </p>
        <div className="freq-keypad-steps">
          {STEPS.map((step) => (
            <button
              key={step.label}
              type="button"
              className="btn-secondary"
              onClick={() => {
                const next = Math.min(
                  MAX_HZ,
                  Math.max(MIN_HZ, frequencyHz + step.hz),
                );
                onChange(next);
                setText(String(Number((next / 1e6).toFixed(6))));
              }}
            >
              {step.label}
            </button>
          ))}
        </div>
      </div>
    </Sheet>
  );
}
