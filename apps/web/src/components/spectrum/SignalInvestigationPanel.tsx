"use client";

import { useState } from "react";
import type { AnalyzeResult, SignalTrack } from "@/lib/api";
import type { InvestigationSubject } from "@/lib/signalSelection";
import { PowerHistoryChart } from "./PowerHistoryChart";

const mhz = (hz: number) => (hz / 1e6).toFixed(4);
const pct = (v: number) => `${Math.round(v * 100)}%`;

function ConfidenceBar({ value }: { value: number }) {
  return (
    <div className="h-1.5 rounded bg-white/5">
      <div
        className="h-full rounded bg-[var(--signal)] transition-all"
        style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }}
      />
    </div>
  );
}

function AlternativesList({
  items,
}: {
  items: { label: string; confidence: number }[];
}) {
  if (!items.length) return null;
  return (
    <div className="mt-4 space-y-2">
      <p className="text-[10px] uppercase tracking-widest text-[var(--muted)]">
        Model alternatives
      </p>
      {items.slice(0, 5).map((alt) => (
        <div
          key={alt.label}
          className="flex items-center justify-between gap-2 text-[11px] text-[var(--muted)]"
        >
          <span className="truncate">{alt.label}</span>
          <span className="font-mono shrink-0">{pct(alt.confidence)}</span>
        </div>
      ))}
    </div>
  );
}

function TrackInvestigation({
  track,
  onNameTrack,
}: {
  track: SignalTrack;
  onNameTrack?: (track: SignalTrack, name: string) => Promise<void>;
}) {
  const [name, setName] = useState(track.name ?? "");
  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-[10px] uppercase tracking-widest text-[var(--muted)]">
            Live classification
          </p>
          <p className="mt-1 text-2xl text-[var(--signal)]">{track.label}</p>
        </div>
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] ${
            track.confidence >= 0.7
              ? "bg-teal-400/15 text-teal-200"
              : "bg-white/5 text-[var(--muted)]"
          }`}
        >
          {pct(track.confidence)} confidence
        </span>
      </div>
      <ConfidenceBar value={track.confidence} />
      <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
        <div>
          <dt className="text-[var(--muted)]">Center</dt>
          <dd className="font-mono">{mhz(track.center_freq_hz)} MHz</dd>
        </div>
        <div>
          <dt className="text-[var(--muted)]">Bandwidth</dt>
          <dd className="font-mono">
            {(track.bandwidth_hz / 1e3).toFixed(1)} kHz
          </dd>
        </div>
        <div>
          <dt className="text-[var(--muted)]">Power</dt>
          <dd className="font-mono">{track.power_dbfs.toFixed(1)} dBFS</dd>
        </div>
        <div>
          <dt className="text-[var(--muted)]">Observations</dt>
          <dd className="font-mono">
            {track.packet_count
              ? `${track.packet_count} packets`
              : `${track.observations} dwells`}
          </dd>
        </div>
      </dl>
      <p className="mt-3 text-[11px] leading-relaxed text-[var(--muted)]">
        {track.evidence}
        {track.identity_note ? ` · ${track.identity_note}` : ""}
      </p>
      {track.history && track.history.length > 1 && (
        <div className="mt-4">
          <p className="mb-2 text-[10px] uppercase tracking-widest text-[var(--muted)]">
            Power history
          </p>
          <PowerHistoryChart values={track.history} />
        </div>
      )}
      <AlternativesList items={track.alternatives ?? []} />
      {onNameTrack && (
        <form
          className="mt-4 flex gap-2"
          onSubmit={async (e) => {
            e.preventDefault();
            const trimmed = name.trim();
            if (trimmed) await onNameTrack(track, trimmed);
          }}
        >
          <input
            className="field min-w-0 flex-1"
            aria-label="Signal name"
            maxLength={80}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name this signal"
          />
          <button
            type="submit"
            className="rounded-md border border-[var(--line)] px-3 py-2 text-xs hover:border-[var(--signal)]"
            disabled={!name.trim()}
          >
            Save
          </button>
        </form>
      )}
    </>
  );
}

function WindowInvestigation({ prediction }: { prediction: AnalyzeResult }) {
  return (
    <>
      <div>
        <p className="text-[10px] uppercase tracking-widest text-[var(--muted)]">
          Whole-window ML
        </p>
        <p className="mt-1 text-2xl text-[var(--signal)]">
          {prediction.is_unknown ? "unknown" : prediction.label}
        </p>
      </div>
      <ConfidenceBar value={prediction.confidence} />
      <p className="mt-2 text-xs text-[var(--muted)]">
        {pct(prediction.confidence)} model confidence · click the waterfall to
        inspect an occupied band
      </p>
      {prediction.meta && (
        <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
          {prediction.meta.bandwidth_hz != null && (
            <div>
              <dt className="text-[var(--muted)]">Est. bandwidth</dt>
              <dd className="font-mono">
                {(prediction.meta.bandwidth_hz / 1e3).toFixed(1)} kHz
              </dd>
            </div>
          )}
          {prediction.meta.duty_cycle != null && (
            <div>
              <dt className="text-[var(--muted)]">Duty cycle</dt>
              <dd className="font-mono">
                {(prediction.meta.duty_cycle * 100).toFixed(0)}%
              </dd>
            </div>
          )}
        </dl>
      )}
      <AlternativesList items={prediction.alternatives ?? []} />
    </>
  );
}

export function SignalInvestigationPanel({
  subject,
  cursorHz,
  cursorPower,
  running,
  onClearSelection,
  onNameTrack,
}: {
  subject: InvestigationSubject | null;
  cursorHz: number | null;
  cursorPower: number | null;
  running: boolean;
  onClearSelection?: () => void;
  onNameTrack?: (track: SignalTrack, name: string) => Promise<void>;
}) {
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-xs font-medium uppercase tracking-widest text-[var(--muted)]">
            Signal investigation
          </h3>
          <p className="mt-1 text-[11px] leading-relaxed text-[var(--muted)]">
            {running
              ? "Live CNN on isolated bands · CRC-valid BLE overrides RF occupancy"
              : "Start a stream to classify signals in real time"}
          </p>
        </div>
        {subject?.kind === "track" && onClearSelection && (
          <button
            type="button"
            className="shrink-0 text-[11px] text-[var(--muted)] hover:text-[var(--signal)]"
            onClick={onClearSelection}
          >
            Clear
          </button>
        )}
      </div>
      <div className="mt-4">
        {!subject ? (
          <p className="text-sm text-[var(--muted)]">
            Hover the waterfall for frequency and power. Click a bright region
            or a classified track to inspect ML results.
          </p>
        ) : subject.kind === "track" ? (
          <TrackInvestigation
            key={subject.track.id}
            track={subject.track}
            onNameTrack={onNameTrack}
          />
        ) : (
          <WindowInvestigation prediction={subject.prediction} />
        )}
      </div>
      {cursorHz !== null && (
        <div className="mt-4 border-t border-[var(--line)] pt-3 font-mono text-[11px] text-[var(--muted)]">
          Cursor · {mhz(cursorHz)} MHz
          {cursorPower !== null ? ` · ${cursorPower.toFixed(1)} dBFS` : ""}
        </div>
      )}
    </div>
  );
}
