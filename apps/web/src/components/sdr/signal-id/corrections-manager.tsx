"use client";

import { useMemo, useRef, useState } from "react";

import { Sheet } from "@/components/sdr/sheet";
import { useCorrections } from "@/components/sdr/signal-id/use-corrections";
import {
  deleteAllCorrections,
  deleteCorrection,
  deleteLabel,
  exportCorrectionsUrl,
  importCorrections,
  thumbnailUrl,
  type Correction,
} from "@/lib/sdr/signal-id/corrections";

type Props = {
  onClose: () => void;
  onChanged?: () => void;
};

/** Everything the user has taught the model, with undo and export. */
export function CorrectionsManager({ onClose, onChanged }: Props) {
  const { data, error, refresh } = useCorrections(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const groups = useMemo(() => {
    const by = new Map<string, Correction[]>();
    for (const item of data?.items ?? []) {
      const list = by.get(item.label) ?? [];
      list.push(item);
      by.set(item.label, list);
    }
    return [...by.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [data]);

  const run = async (work: () => Promise<string | void>) => {
    setBusy(true);
    setProblem(null);
    setMessage(null);
    try {
      const done = await work();
      if (done) setMessage(done);
      await refresh();
      onChanged?.();
    } catch (err) {
      setProblem(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet title="Your corrections" onClose={onClose}>
      <div className="correct-sheet">
        <p className="correct-hint">
          Each correction teaches the model what a signal looks like. Remove one
          and the model stops using it straight away.
        </p>

        {(error || problem) && (
          <p className="correct-error" role="alert">
            {problem ?? error}
          </p>
        )}
        {message && (
          <p className="correct-note" role="status">
            {message}
          </p>
        )}

        {!data ? (
          <p className="correct-hint">Loading…</p>
        ) : groups.length === 0 ? (
          <p className="correct-hint">
            Nothing yet. Open a signal in Identify and choose Correct to teach the
            model.
          </p>
        ) : (
          <ul className="corrections-groups">
            {groups.map(([label, items]) => (
              <li key={label} className="corrections-group">
                <div className="corrections-group-head">
                  <h3>
                    {label} <span>×{items.length}</span>
                  </h3>
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={busy}
                    aria-label={`Remove all ${items.length} corrections labelled ${label}`}
                    onClick={() =>
                      run(async () => {
                        const out = await deleteLabel(label);
                        return `Removed ${out.removed} correction${out.removed === 1 ? "" : "s"} for ${label}.`;
                      })
                    }
                  >
                    Remove all
                  </button>
                </div>
                <ul className="corrections-items">
                  {items.map((item) => (
                    <li key={item.id} className="corrections-item">
                      {/* eslint-disable-next-line @next/next/no-img-element -- tiny API-served PNG */}
                      <img
                        src={thumbnailUrl(item.id)}
                        alt=""
                        width={44}
                        height={44}
                        loading="lazy"
                      />
                      <span className="corrections-item-text">
                        <span>
                          {item.wrong_label ? `was ${item.wrong_label}` : "new label"}
                        </span>
                        <span className="correct-hint">
                          {new Date(item.created_at * 1000).toLocaleString()}
                          {item.note ? ` · ${item.note}` : ""}
                        </span>
                      </span>
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={busy}
                        aria-label={`Remove this correction for ${label}`}
                        onClick={() => run(() => deleteCorrection(item.id).then(() => undefined))}
                      >
                        Remove
                      </button>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}

        <div className="correct-actions correct-actions-wrap">
          <a className="btn-secondary" href={exportCorrectionsUrl()} download>
            Export
          </a>
          <button
            type="button"
            className="btn-secondary"
            disabled={busy}
            onClick={() => fileRef.current?.click()}
          >
            Import…
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".zip,application/zip"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file) return;
              void run(async () => {
                const out = await importCorrections(file);
                return `Imported ${out.added} correction${out.added === 1 ? "" : "s"}.`;
              });
            }}
          />
          {data && data.total > 0 &&
            (confirmAll ? (
              <>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      setConfirmAll(false);
                      const out = await deleteAllCorrections();
                      return `Removed all ${out.removed} corrections.`;
                    })
                  }
                >
                  Yes, remove all {data.total}
                </button>
                <button type="button" className="btn-secondary" onClick={() => setConfirmAll(false)}>
                  Keep them
                </button>
              </>
            ) : (
              <button type="button" className="btn-secondary" onClick={() => setConfirmAll(true)}>
                Remove all…
              </button>
            ))}
        </div>
      </div>
    </Sheet>
  );
}
