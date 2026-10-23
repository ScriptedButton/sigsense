"use client";

import { useState } from "react";
import type { AnalyzeResult } from "@/lib/api";

type Props = {
  result: AnalyzeResult | null;
  error?: string | null;
  onEnroll?: (label: string) => void;
};

function fmtHz(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e6) return `${(v / 1e6).toFixed(3)} MHz`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(2)} kHz`;
  return `${v.toFixed(1)} Hz`;
}

export function ResultPanel({ result, error, onEnroll }: Props) {
  if (error) {
    return (
      <div className="border border-red-500/40 bg-red-950/30 p-5 text-sm text-red-200">
        {error}
      </div>
    );
  }

  if (!result) {
    return (
      <div className="border border-[var(--line)] bg-[var(--panel)] p-5 text-sm text-[var(--muted)]">
        Results appear here after analysis.
      </div>
    );
  }

  return (
    <div className="border border-[var(--line)] bg-[var(--panel)] p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.2em] text-[var(--muted)]">
            Prediction
          </p>
          <p className="mt-1 font-[family-name:var(--font-display)] text-3xl text-[var(--signal)]">
            {result.label}
            {result.is_unknown ? (
              <span className="ml-2 text-base text-amber-300">open-set</span>
            ) : null}
          </p>
        </div>
        <p className="font-mono text-2xl text-[var(--foreground)]">
          {(result.confidence * 100).toFixed(1)}%
        </p>
      </div>

      <dl className="mt-6 grid grid-cols-2 gap-3 text-sm md:grid-cols-3">
        <Meta label="Bandwidth" value={fmtHz(result.meta.bandwidth_hz)} />
        <Meta
          label="Rel. center"
          value={fmtHz(result.meta.relative_center_hz)}
        />
        <Meta
          label="Duty cycle"
          value={
            result.meta.duty_cycle != null
              ? `${(result.meta.duty_cycle * 100).toFixed(0)}%`
              : "—"
          }
        />
        <Meta
          label="Duration"
          value={
            result.meta.duration_s != null
              ? `${result.meta.duration_s.toFixed(3)} s`
              : "—"
          }
        />
        <Meta label="Sample rate" value={fmtHz(result.meta.sample_rate_hz)} />
        <Meta label="Center freq" value={fmtHz(result.meta.center_freq_hz)} />
      </dl>

      <section className="mt-6">
        <h3 className="text-xs uppercase tracking-[0.2em] text-[var(--muted)]">
          Alternatives
        </h3>
        <ul className="mt-2 space-y-1">
          {result.alternatives.map((a) => (
            <li
              key={a.label}
              className="flex justify-between font-mono text-sm text-[var(--foreground)]"
            >
              <span>{a.label}</span>
              <span className="text-[var(--muted)]">
                {(a.confidence * 100).toFixed(1)}%
              </span>
            </li>
          ))}
        </ul>
      </section>

      {result.neighbors.length > 0 ? (
        <section className="mt-6">
          <h3 className="text-xs uppercase tracking-[0.2em] text-[var(--muted)]">
            Library neighbors
          </h3>
          <ul className="mt-2 space-y-1">
            {result.neighbors.map((n) => (
              <li
                key={n.id}
                className="flex justify-between font-mono text-sm"
              >
                <span>
                  {n.label}{" "}
                  <span className="text-[var(--muted)]">({n.source})</span>
                </span>
                <span className="text-[var(--muted)]">
                  {(n.similarity * 100).toFixed(1)}%
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {onEnroll && result.embedding ? (
        <EnrollForm
          onEnroll={onEnroll}
          defaultLabel={result.label === "unknown" ? "" : result.label}
        />
      ) : null}
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[var(--muted)]">{label}</dt>
      <dd className="font-mono text-[var(--foreground)]">{value}</dd>
    </div>
  );
}

function EnrollForm({
  onEnroll,
  defaultLabel,
}: {
  onEnroll: (label: string) => void;
  defaultLabel: string;
}) {
  const [label, setLabel] = useState(defaultLabel);
  return (
    <form
      className="mt-6 flex flex-wrap items-end gap-2 border-t border-[var(--line)] pt-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (label.trim()) onEnroll(label.trim());
      }}
    >
      <label className="flex min-w-[12rem] flex-1 flex-col gap-1 text-xs text-[var(--muted)]">
        Enroll as
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          className="border border-[var(--line)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--foreground)] outline-none focus:border-[var(--signal)]"
          placeholder="Custom signal name"
        />
      </label>
      <button
        type="submit"
        className="bg-[var(--signal)] px-4 py-2 text-sm font-medium text-[var(--ink)]"
      >
        Save to library
      </button>
    </form>
  );
}
