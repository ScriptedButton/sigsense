"use client";

import {
  useCallback,
  useState,
  type KeyboardEvent,
  type WheelEvent,
} from "react";

import {
  FREQ_DIGIT_PLACES,
  digitAtPlace,
  nudgeFrequencyPlace,
} from "@/lib/sdr/display-settings";

type FrequencyDigitDisplayProps = {
  frequencyHz: number;
  onChange: (hz: number) => void;
  /** Compact chrome · rail control · hero header. */
  size?: "sm" | "md" | "lg";
};

/**
 * Instrument-style VFO: each digit is a place value. Click to select, scroll or
 * ↑↓ to nudge that place — the usual SDR habit for fine/coarse tuning.
 */
export function FrequencyDigitDisplay({
  frequencyHz,
  onChange,
  size = "md",
}: FrequencyDigitDisplayProps) {
  const [activePlace, setActivePlace] = useState<number>(1_000);

  const applyNudge = useCallback(
    (place: number, delta: number) => {
      onChange(nudgeFrequencyPlace(frequencyHz, place, delta));
    },
    [frequencyHz, onChange],
  );

  const onWheelDigit = useCallback(
    (e: WheelEvent, place: number) => {
      e.preventDefault();
      e.stopPropagation();
      applyNudge(place, e.deltaY > 0 ? -1 : 1);
    },
    [applyNudge],
  );

  const onKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "ArrowUp") {
        e.preventDefault();
        applyNudge(activePlace, 1);
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        applyNudge(activePlace, -1);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        const idx = FREQ_DIGIT_PLACES.indexOf(
          activePlace as (typeof FREQ_DIGIT_PLACES)[number],
        );
        if (idx > 0) setActivePlace(FREQ_DIGIT_PLACES[idx - 1]);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        const idx = FREQ_DIGIT_PLACES.indexOf(
          activePlace as (typeof FREQ_DIGIT_PLACES)[number],
        );
        if (idx >= 0 && idx < FREQ_DIGIT_PLACES.length - 1) {
          setActivePlace(FREQ_DIGIT_PLACES[idx + 1]);
        }
      }
    },
    [activePlace, applyNudge],
  );

  // Dim leading zeros until the first significant digit (keep at least Hz place).
  let seenSignificant = frequencyHz >= 100_000_000;

  const digitClass =
    size === "sm"
      ? "freq-digit freq-digit-sm"
      : size === "lg"
        ? "freq-digit freq-digit-lg"
        : "freq-digit freq-digit-md";

  return (
    <>
    <div
      className={`freq-digit-row ${size === "lg" ? "freq-digit-row-lg" : ""}`}
      role="group"
      aria-label="Frequency digits. Select a digit, then scroll or use arrow keys to tune."
      tabIndex={0}
      onKeyDown={onKeyDown}
    >
      {FREQ_DIGIT_PLACES.map((place) => {
        const digit = digitAtPlace(frequencyHz, place);
        if (digit !== 0) seenSignificant = true;
        const leading = !seenSignificant && place > 1;
        const showDot = place === 1_000_000 || place === 1_000;

        return (
          <span key={place} className="contents">
            <button
              type="button"
              className={`${digitClass} ${
                activePlace === place ? "freq-digit-active" : ""
              } ${leading ? "freq-digit-leading" : ""}`}
              aria-label={`Tune ${place.toLocaleString()} Hz place, currently ${digit}`}
              aria-pressed={activePlace === place}
              onClick={() => setActivePlace(place)}
              onWheel={(e) => onWheelDigit(e, place)}
            >
              {digit}
            </button>
            {showDot && (
              <span className="freq-digit-sep" aria-hidden>
                .
              </span>
            )}
          </span>
        );
      })}
      <span className="freq-digit-unit">Hz</span>
    </div>
    {/* Touch has no scroll wheel or arrow keys: step the selected digit. */}
    <div className="freq-digit-steppers" role="group" aria-label="Step the selected digit">
      <button
        type="button"
        aria-label={`Increase ${activePlace.toLocaleString()} Hz place`}
        onClick={() => applyNudge(activePlace, 1)}
      >
        ▲
      </button>
      <button
        type="button"
        aria-label={`Decrease ${activePlace.toLocaleString()} Hz place`}
        onClick={() => applyNudge(activePlace, -1)}
      >
        ▼
      </button>
    </div>
    </>
  );
}
