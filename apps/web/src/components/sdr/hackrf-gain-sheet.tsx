"use client";

import { Sheet } from "@/components/sdr/sheet";
import { useHackrf } from "@/components/HackrfPanel";
import { UpconverterChips, UpconverterHint } from "@/components/sdr/upconverter-control";
import { haptic } from "@/lib/haptics";

type StageProps = {
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
};

/** A big slider with −/+ steppers: thumbs are poor at hitting an 8 dB step. */
function Stage({ label, hint, value, min, max, step, onChange }: StageProps) {
  const set = (next: number) => {
    const clamped = Math.min(max, Math.max(min, next));
    if (clamped !== value) haptic(8);
    onChange(clamped);
  };
  return (
    <div className="gain-stage">
      <div className="gain-stage-head">
        <span className="gain-stage-label">{label}</span>
        <span className="gain-stage-value font-mono tabular-nums">{value} dB</span>
      </div>
      <div className="gain-stage-row">
        <button
          type="button"
          className="gain-stage-step"
          aria-label={`Decrease ${label} by ${step} dB`}
          disabled={value <= min}
          onClick={() => set(value - step)}
        >
          −
        </button>
        <input
          type="range"
          className="slider gain-stage-slider"
          min={min}
          max={max}
          step={step}
          value={value}
          aria-label={`${label} gain`}
          aria-valuetext={`${value} dB`}
          onChange={(e) => set(Number(e.target.value))}
        />
        <button
          type="button"
          className="gain-stage-step"
          aria-label={`Increase ${label} by ${step} dB`}
          disabled={value >= max}
          onClick={() => set(value + step)}
        >
          +
        </button>
      </div>
      <p className="gain-stage-hint">{hint}</p>
    </div>
  );
}

/** HackRF front-end gain: LNA, VGA and the RF amp, sized for a thumb. */
export function HackrfGainSheet({ onClose }: { onClose: () => void }) {
  const { gain, setGain } = useHackrf();
  return (
    <Sheet title="HackRF gain" onClose={onClose}>
      <div className="gain-sheet">
        <Stage
          label="LNA"
          hint="RF stage at the antenna, 8 dB steps. Raise for weak signals; lower if strong stations smear across the band."
          value={gain.lna}
          min={0}
          max={40}
          step={8}
          onChange={(lna) => setGain({ lna })}
        />
        <Stage
          label="VGA"
          hint="Baseband stage after mixing, 2 dB steps. Raise until the noise floor lifts, not further."
          value={gain.vga}
          min={0}
          max={62}
          step={2}
          onChange={(vga) => setGain({ vga })}
        />
        <label className="gain-amp">
          <input
            type="checkbox"
            checked={gain.amp}
            onChange={(e) => {
              haptic(10);
              setGain({ amp: e.target.checked });
            }}
          />
          <span>
            RF amp <small>(+14 dB, can overload near strong transmitters)</small>
          </span>
        </label>
        <p className="gain-sheet-note">
          Each change restarts the HackRF stream briefly once you stop moving the slider.
        </p>
        <div className="gain-stage">
          <span className="gain-stage-label">Upconverter</span>
          <div className="mt-2">
            <UpconverterChips large />
          </div>
          <UpconverterHint className="gain-stage-hint" />
        </div>
      </div>
    </Sheet>
  );
}
