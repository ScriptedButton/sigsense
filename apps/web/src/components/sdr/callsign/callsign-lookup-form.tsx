"use client";

import { useId } from "react";

import type { CallsignServiceMode } from "@/lib/sdr/callsign/normalize";

type CallsignLookupFormProps = {
  value: string;
  mode: CallsignServiceMode;
  busy: boolean;
  onChange: (value: string) => void;
  onModeChange: (mode: CallsignServiceMode) => void;
  onSubmit: () => void;
};

const MODES: Array<{ id: CallsignServiceMode; label: string }> = [
  { id: "auto", label: "Auto" },
  { id: "amateur", label: "Amateur" },
  { id: "gmrs", label: "GMRS" },
];

export function CallsignLookupForm({
  value,
  mode,
  busy,
  onChange,
  onModeChange,
  onSubmit,
}: CallsignLookupFormProps) {
  const id = useId();
  const placeholder = mode === "gmrs" ? "WRNT570" : "W1AW";

  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <div className="flex flex-wrap gap-1" role="group" aria-label="Service">
        {MODES.map((m) => (
          <button
            key={m.id}
            type="button"
            className={`mode-chip ${mode === m.id ? "mode-chip-active" : ""}`}
            disabled={busy}
            onClick={() => onModeChange(m.id)}
          >
            {m.label}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-0 flex-1 text-xs text-muted" htmlFor={id}>
          Callsign
          <input
            id={id}
            className="fsk-select mt-1 w-full font-mono uppercase tracking-wide"
            value={value}
            autoComplete="off"
            spellCheck={false}
            placeholder={placeholder}
            disabled={busy}
            onChange={(e) => onChange(e.target.value.toUpperCase())}
          />
        </label>
        <button
          type="submit"
          className="btn-primary"
          disabled={busy || value.trim().length < 3}
        >
          {busy ? "Looking up…" : "Lookup"}
        </button>
      </div>
    </form>
  );
}
