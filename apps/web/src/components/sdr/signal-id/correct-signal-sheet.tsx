"use client";

import { useMemo, useRef, useState } from "react";

import { useRadioController } from "@/components/sdr/sdr-provider";
import { Sheet } from "@/components/sdr/sheet";
import { useCorrections } from "@/components/sdr/signal-id/use-corrections";
import { formatFrequency } from "@/lib/sdr/format";
import { displayClassFor } from "@/lib/sdr/signal-id/backend-tracks";
import { isBuiltinClass } from "@/lib/sdr/signal-id/backend-classes";
import {
  deleteCorrection,
  FALLBACK_CLASSES,
  NOT_A_SIGNAL,
  submitCorrection,
  type CorrectionResult,
} from "@/lib/sdr/signal-id/corrections";
import type { IdentifiedSignal } from "@/lib/sdr/signal-id/types";

const MAX_LABEL = 48;

type Props = {
  signal: IdentifiedSignal;
  onClose: () => void;
  /** Called after a correction is stored or undone, so counts can refresh. */
  onChanged?: () => void;
};

/** Why this signal cannot be corrected, or null when it can. */
export function correctionBlocker(signal: IdentifiedSignal): string | null {
  if (signal.basis !== "backend-cnn") {
    return "Corrections need the SigSense API. This label comes from the small on-device model.";
  }
  if (!signal.trackId) return "This signal has nothing captured to learn from.";
  if (signal.trackId.startsWith("ble:") || signal.trackId.startsWith("ble-link:")) {
    return "Bluetooth devices are identified from decoded packets, not from the spectrum.";
  }
  return null;
}

/**
 * Tell the model what a signal really is. The correction is stored and applies
 * to the very next frames; similar signals follow, other classes are left alone.
 */
export function CorrectSignalSheet({ signal, onClose, onChanged }: Props) {
  const controller = useRadioController();
  const { data } = useCorrections(true);
  const searchRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const [custom, setCustom] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CorrectionResult | null>(null);
  const [undone, setUndone] = useState(false);

  const current = signal.backendLabel ?? "";
  const blocker = correctionBlocker(signal);

  const options = useMemo(() => {
    const classes = data?.classes ?? [...FALLBACK_CLASSES];
    const customLabels = data?.custom ?? [];
    const guesses = (signal.alternatives ?? [])
      .map((a) => a.label)
      .filter((l) => l !== "unknown");
    const all = [...new Set([...guesses, ...customLabels, ...classes])].filter(
      (l) => l !== "Noise",
    );
    const q = query.trim().toLowerCase();
    return {
      guesses: new Set(guesses),
      customLabels: new Set(customLabels),
      list: q ? all.filter((l) => l.toLowerCase().includes(q)) : all,
    };
  }, [data, query, signal.alternatives]);

  const chosen = custom.trim() ? custom.trim() : picked;
  const canSave = !blocker && !saving && !!chosen && !result;

  const save = async () => {
    if (!chosen || !signal.trackId) return;
    setSaving(true);
    setError(null);
    try {
      const out = await submitCorrection({
        trackId: signal.trackId,
        session: signal.session,
        label: chosen,
        note,
      });
      setResult(out);
      const label = out.item.label;
      controller.applySignalCorrection(signal.trackId, {
        label,
        confidence: 1,
        learned: out.prediction.learned,
        displayLabel: displayClassFor(label),
        customLabel: !isBuiltinClass(label),
      });
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const undo = async () => {
    if (!result) return;
    setSaving(true);
    try {
      await deleteCorrection(result.item.id);
      setUndone(true);
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Sheet title="Correct this signal" onClose={onClose} initialFocus={searchRef}>
      <div className="correct-sheet">
        <p className="correct-summary">
          <span className="font-mono tabular-nums">{formatFrequency(signal.frequencyHz)}</span>{" "}
          is labelled{" "}
          <strong>{signal.unknown ? "Unknown" : current || signal.label}</strong>
          {signal.userCorrected ? " (set by you)" : ""}. What is it really?
        </p>

        {blocker ? (
          <p className="correct-note" role="status">
            {blocker}
          </p>
        ) : result ? (
          <div className="correct-done" role="status">
            {undone ? (
              <p>Removed. The model is back to what it said before.</p>
            ) : (
              <>
                <p>
                  <strong>Saved as {result.item.label}.</strong>{" "}
                  {result.prediction.was && result.prediction.was !== result.item.label
                    ? `It was ${result.prediction.was}. `
                    : ""}
                  Signals that look like this one will now be labelled{" "}
                  {result.item.label}
                  {result.prediction.learned
                    ? `, learned from ${result.prediction.learned.examples} correction${result.prediction.learned.examples === 1 ? "" : "s"}`
                    : ""}
                  . {result.refreshed > 0
                    ? `${result.refreshed} signal${result.refreshed === 1 ? "" : "s"} on screen updated now.`
                    : ""}
                </p>
                <p className="correct-hint">
                  Two or three examples at different strengths work best. If you
                  were wrong, undo it.
                </p>
              </>
            )}
            <div className="correct-actions">
              {!undone && (
                <button type="button" className="btn-secondary" disabled={saving} onClick={undo}>
                  Undo
                </button>
              )}
              <button type="button" className="btn-primary" onClick={onClose}>
                Done
              </button>
            </div>
          </div>
        ) : (
          <>
            <label className="correct-field">
              <span>Search signal types</span>
              <input
                ref={searchRef}
                type="search"
                className="field"
                placeholder="e.g. WFM, DMR, ADS-B…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
            </label>

            <div
              className="correct-options"
              role="radiogroup"
              aria-label="Signal type"
            >
              <button
                type="button"
                role="radio"
                aria-checked={picked === NOT_A_SIGNAL && !custom.trim()}
                className="correct-option correct-option-noise"
                onClick={() => {
                  setPicked(NOT_A_SIGNAL);
                  setCustom("");
                }}
              >
                Not a signal · noise or interference
              </button>
              {options.list.map((label) => (
                <button
                  key={label}
                  type="button"
                  role="radio"
                  aria-checked={picked === label && !custom.trim()}
                  className="correct-option"
                  onClick={() => {
                    setPicked(label);
                    setCustom("");
                  }}
                >
                  <span>{label}</span>
                  {label === current && <em>current</em>}
                  {options.guesses.has(label) && label !== current && <em>model&apos;s guess</em>}
                  {options.customLabels.has(label) && <em>yours</em>}
                </button>
              ))}
              {options.list.length === 0 && (
                <p className="correct-hint">No match. Name it below instead.</p>
              )}
            </div>

            <label className="correct-field">
              <span>Or name it yourself</span>
              <input
                type="text"
                className="field"
                maxLength={MAX_LABEL}
                placeholder="e.g. Airport ground control"
                value={custom}
                onChange={(e) => setCustom(e.target.value)}
              />
            </label>

            <label className="correct-field">
              <span>Note (optional)</span>
              <input
                type="text"
                className="field"
                maxLength={200}
                placeholder="Where or how you know"
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
            </label>

            {error && (
              <p className="correct-error" role="alert">
                {error}
              </p>
            )}

            <div className="correct-actions">
              <button type="button" className="btn-secondary" onClick={onClose}>
                Cancel
              </button>
              <button type="button" className="btn-primary" disabled={!canSave} onClick={save}>
                {saving ? "Saving…" : chosen ? `Save as ${chosen === NOT_A_SIGNAL ? "not a signal" : chosen}` : "Pick a type"}
              </button>
            </div>
          </>
        )}
      </div>
    </Sheet>
  );
}
