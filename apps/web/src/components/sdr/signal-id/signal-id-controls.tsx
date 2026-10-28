import type { SignalIdSensitivity } from "@/lib/sdr/signal-id/engine";

const SENSITIVITIES: { value: SignalIdSensitivity; label: string; hint: string }[] = [
  { value: "low", label: "Low", hint: "Only strong signals, 16 dB above the floor" },
  { value: "medium", label: "Med", hint: "Balanced — 10 dB above the floor" },
  { value: "high", label: "High", hint: "Faint signals too, 6 dB above the floor" },
];

type SignalIdControlsProps = {
  enabled: boolean;
  /** The source is always identified (HackRF); the scan toggle does nothing. */
  locked?: boolean;
  sensitivity: SignalIdSensitivity;
  onToggle: () => void;
  onSensitivityChange: (value: SignalIdSensitivity) => void;
};

export function SignalIdControls({
  enabled,
  locked = false,
  sensitivity,
  onToggle,
  onSensitivityChange,
}: SignalIdControlsProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        className={enabled ? "btn-primary" : "btn-secondary"}
        onClick={onToggle}
        aria-pressed={enabled}
        disabled={locked}
        title={
          locked
            ? "The backend identifies HackRF signals continuously"
            : undefined
        }
      >
        {locked ? "Always on" : enabled ? "Stop scan" : "Scan band"}
      </button>

      <div
        className="ml-auto flex items-center gap-1"
        role="group"
        aria-label="Detection sensitivity"
      >
        {SENSITIVITIES.map((option) => (
          <button
            key={option.value}
            type="button"
            className={`mode-chip${sensitivity === option.value ? " mode-chip-active" : ""}`}
            onClick={() => onSensitivityChange(option.value)}
            aria-pressed={sensitivity === option.value}
            title={option.hint}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}
