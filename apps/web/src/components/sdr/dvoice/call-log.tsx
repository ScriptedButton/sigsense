"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  CopyButton,
  EmergencyBadge,
  EncryptionBadge,
  ProtocolBadge,
  downloadText,
  useTick,
} from "@/components/sdr/dvoice/parts";
import { Sheet } from "@/components/sdr/sheet";
import { dvApi } from "@/lib/sdr/dvoice/client";
import {
  CSV_COLUMNS,
  DEFAULT_FILTER,
  PROTOCOL_CHOICES,
  PROTOCOL_LABELS,
  activeFilterCount,
  callDurationS,
  callTitle,
  callsToCsv,
  callsToJson,
  filterCalls,
  formatClock,
  formatDuration,
  formatId,
  formatMhz,
  formatPercent,
  isEncrypted,
  sortCalls,
  type Aliases,
  type CallFilter,
  type CallSortKey,
} from "@/lib/sdr/dvoice/model";
import { dvStore } from "@/lib/sdr/dvoice/store";
import { transcribeWavUrl } from "@/lib/sdr/dvoice/transcribe";
import { fetchSttStatus } from "@/lib/sdr/remote-transcription-engine";
import type { DvCallRecord } from "@/lib/sdr/dvoice/model";
import type { DvCall } from "@/lib/sdr/dvoice/types";

const PAGE = 25;

const SORTS: { id: CallSortKey; label: string; dir: "asc" | "desc" }[] = [
  { id: "time", label: "Time", dir: "desc" },
  { id: "talkgroup", label: "Talkgroup", dir: "asc" },
  { id: "protocol", label: "Protocol", dir: "asc" },
  { id: "duration", label: "Duration", dir: "desc" },
];

export function CallLog({ calls, aliases, onOpen }: { calls: DvCall[]; aliases: Aliases; onOpen: (callId: string) => void }) {
  const [filter, setFilter] = useState<CallFilter>(DEFAULT_FILTER);
  const [sortKey, setSortKey] = useState<CallSortKey>("time");
  const [dir, setDir] = useState<"asc" | "desc">("desc");
  const [shown, setShown] = useState(PAGE);
  const sentinel = useRef<HTMLLIElement | null>(null);
  const anyLive = calls.some((c) => c.ended_at === null);
  useTick(1000, anyLive);

  const rows = useMemo(
    () => sortCalls(filterCalls(calls, filter, aliases), sortKey, dir, aliases),
    // The tick above re-renders running calls; durations are read at render time.
    [calls, filter, sortKey, dir, aliases],
  );
  const active = activeFilterCount(filter);
  const visible = rows.slice(0, shown);

  // Load more as the end of the list scrolls into view.
  useEffect(() => {
    const node = sentinel.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setShown((n) => n + PAGE);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [visible.length, rows.length]);

  const setSort = (key: CallSortKey) => {
    setSortKey(key);
    setDir(SORTS.find((s) => s.id === key)?.dir ?? "desc");
    setShown(PAGE);
  };
  const patch = (partial: Partial<CallFilter>) => {
    setFilter((f) => ({ ...f, ...partial }));
    setShown(PAGE);
  };

  return (
    <section className="dv-card" aria-label="Call log">
      <div className="dv-card-head">
        <strong>Call log</strong>
        <span className="dv-muted" aria-live="polite">
          {rows.length === calls.length ? `${calls.length} calls` : `${rows.length} of ${calls.length}`}
        </span>
        <span className="dv-spacer" />
        <button
          type="button"
          className="btn-secondary dv-small"
          disabled={rows.length === 0}
          onClick={() => downloadText("digital-voice-calls.csv", callsToCsv(rows, aliases), "text/csv")}
          title={`Columns: ${CSV_COLUMNS.join(", ")}`}
        >
          CSV
        </button>
        <button
          type="button"
          className="btn-secondary dv-small"
          disabled={rows.length === 0}
          onClick={() => downloadText("digital-voice-calls.json", callsToJson(rows), "application/json")}
        >
          JSON
        </button>
      </div>

      <div className="dv-filters" role="group" aria-label="Filter and sort calls">
        <input
          type="search"
          className="field dv-search"
          aria-label="Search calls"
          placeholder="Search talkgroup, ID, protocol…"
          value={filter.query}
          onChange={(e) => patch({ query: e.target.value })}
        />
        <label className="dv-inline">
          <span className="sr-only">Sort by</span>
          <select className="field" aria-label="Sort calls by" value={sortKey} onChange={(e) => setSort(e.target.value as CallSortKey)}>
            {SORTS.map((s) => (
              <option key={s.id} value={s.id}>
                Sort: {s.label}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="mode-chip"
          aria-label={`Sort direction: ${dir === "desc" ? "high to low" : "low to high"}. Press to reverse.`}
          onClick={() => setDir((d) => (d === "desc" ? "asc" : "desc"))}
        >
          {dir === "desc" ? "↓" : "↑"}
        </button>
        <select
          className="field"
          aria-label="Filter by protocol"
          value={filter.protocol}
          onChange={(e) => patch({ protocol: e.target.value as CallFilter["protocol"] })}
        >
          <option value="all">All protocols</option>
          {PROTOCOL_CHOICES.filter((p) => p.id !== "auto").map((p) => (
            <option key={p.id} value={p.id}>
              {PROTOCOL_LABELS[p.id as keyof typeof PROTOCOL_LABELS]}
            </option>
          ))}
        </select>
        <select
          className="field"
          aria-label="Filter by type"
          value={filter.flag}
          onChange={(e) => patch({ flag: e.target.value as CallFilter["flag"] })}
        >
          <option value="all">All calls</option>
          <option value="clear">Clear only</option>
          <option value="encrypted">Encrypted only</option>
          <option value="emergency">Emergency only</option>
        </select>
        <select
          className="field"
          aria-label="Minimum duration"
          value={filter.minDurationS}
          onChange={(e) => patch({ minDurationS: Number(e.target.value) })}
        >
          <option value={0}>Any length</option>
          <option value={2}>2 s or more</option>
          <option value={5}>5 s or more</option>
          <option value={15}>15 s or more</option>
        </select>
        {active > 0 ? (
          <button type="button" className="mode-chip" onClick={() => patch(DEFAULT_FILTER)}>
            Clear filters ({active})
          </button>
        ) : null}
      </div>

      {calls.length === 0 ? (
        <p className="dv-empty">No calls yet. They appear here as they are heard.</p>
      ) : rows.length === 0 ? (
        <p className="dv-empty">No calls match these filters.</p>
      ) : (
        <ul className="dv-log">
          {visible.map((call) => (
            <li key={call.call_id}>
              <button type="button" className="dv-row-btn" onClick={() => onOpen(call.call_id)} aria-label={`Open call ${callTitle(call, aliases)}`}>
                <span className="dv-log-time">{formatClock(call.started_at)}</span>
                <span className="dv-log-main">
                  <span className="dv-log-title">{callTitle(call, aliases)}</span>
                  <span className="dv-log-sub">
                    {call.source_id !== null ? `from ${call.source_id} · ` : ""}
                    {call.ended_at === null ? "live" : formatDuration(callDurationS(call))}
                  </span>
                </span>
                <span className="dv-log-flags">
                  <ProtocolBadge protocol={call.protocol} slot={call.slot} />
                  {call.emergency ? <EmergencyBadge /> : null}
                  <EncryptionBadge encryption={call.encryption} compact />
                </span>
              </button>
            </li>
          ))}
          <li ref={sentinel} className="dv-sentinel" aria-hidden="true" />
        </ul>
      )}
      {rows.length > visible.length ? (
        <button type="button" className="btn-secondary dv-wide" onClick={() => setShown((n) => n + PAGE)}>
          Show more ({rows.length - visible.length} left)
        </button>
      ) : null}
    </section>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="dv-detail-row">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export function CallDetailSheet({
  call,
  aliases,
  transcript: serverTranscript,
  onClose,
}: {
  call: DvCallRecord;
  aliases: Aliases;
  /** Text the engine already produced for this call (live or stored). */
  transcript: string;
  onClose: () => void;
}) {
  const encrypted = isEncrypted(call);
  // The recorder says whether audio was kept; when it doesn't say, try and handle a 404.
  const [audioError, setAudioError] = useState(call.has_audio === false && call.ended_at !== null);
  const live = call.ended_at === null;
  const [sttReady, setSttReady] = useState<boolean | null>(null);
  const [transcript, setTranscript] = useState<{ state: "idle" | "working" | "done" | "error"; text: string }>({
    state: "idle",
    text: "",
  });
  const [confirmDelete, setConfirmDelete] = useState(false);
  useTick(1000, call.ended_at === null);

  useEffect(() => {
    let cancelled = false;
    if (encrypted) return;
    void fetchSttStatus().then((status) => {
      if (!cancelled) setSttReady(Boolean(status?.available));
    });
    return () => {
      cancelled = true;
    };
  }, [encrypted]);

  const audioUrl = dvApi.audioUrl(call.call_id);
  const transcribe = async () => {
    setTranscript({ state: "working", text: "" });
    try {
      const result = await transcribeWavUrl(
        audioUrl,
        { frequencyHz: call.frequency_hz, mode: PROTOCOL_LABELS[call.protocol] },
        (partial) => setTranscript({ state: "working", text: partial }),
      );
      setTranscript({ state: "done", text: result.text || "(no speech recognised)" });
    } catch (error) {
      setTranscript({ state: "error", text: error instanceof Error ? error.message : "Transcription failed" });
    }
  };

  const meta = Object.entries(call.meta).filter(([key]) => key !== "call_num");
  const enc = call.encryption;
  return (
    <Sheet title={callTitle(call, aliases)} onClose={onClose}>
      <div className="dv-sheet-body">
        <div className="dv-badges">
          <ProtocolBadge protocol={call.protocol} slot={call.slot} />
          {call.emergency ? <EmergencyBadge /> : null}
          <EncryptionBadge encryption={enc} />
        </div>

        {encrypted ? (
          <p className="dv-banner dv-banner-encrypted">
            <strong>No audio: encrypted.</strong> The call is scrambled and is not decoded or recorded.
          </p>
        ) : (
          <div className="dv-audio">
            {live ? (
              <p className="dv-muted">This call is still in progress. A recording, if recording is on, is saved when it ends.</p>
            ) : audioError ? (
              <p className="dv-muted">No audio was kept for this call. Turn on recording in settings to keep future calls.</p>
            ) : (
              <audio
                controls
                preload="none"
                src={audioUrl}
                aria-label="Recorded call audio"
                onError={() => setAudioError(true)}
              />
            )}
          </div>
        )}

        <dl className="dv-detail">
          <Row label="Started" value={new Date(call.started_at * 1000).toLocaleString()} />
          <Row label="Duration" value={call.ended_at === null ? `${formatDuration(callDurationS(call))} (live)` : formatDuration(callDurationS(call))} />
          <Row label="Kind" value={call.kind} />
          <Row label="Talkgroup / target" value={formatId(call.target_id)} />
          <Row label="Source ID" value={formatId(call.source_id)} />
          <Row label="Slot" value={call.slot === null ? "—" : call.slot + 1} />
          <Row label="Frequency" value={formatMhz(call.frequency_hz)} />
          <Row label="Channel" value={call.channel ?? "—"} />
          <Row label="Priority" value={call.priority ?? "—"} />
          <Row label="Voice frames" value={`${call.voice_frames} (${call.voice_frames_bad} could not be repaired)`} />
          <Row label="BER" value={formatPercent(call.ber, 2)} />
          <Row label="Ended" value={call.end_reason ?? (call.ended_at === null ? "in progress" : "—")} />
          <Row
            label="Encryption"
            value={
              enc.state === "encrypted"
                ? `Encrypted · ${enc.algorithm_name ?? (enc.algorithm_id !== null ? `ALG 0x${enc.algorithm_id.toString(16)}` : "unknown algorithm")}${
                    enc.key_id !== null ? ` · key ${enc.key_id}` : ""
                  }${enc.mi_present ? " · MI present" : ""}`
                : enc.state === "clear"
                  ? "Clear"
                  : "Unknown"
            }
          />
          {meta.map(([key, value]) => (
            <Row key={key} label={key} value={typeof value === "object" ? JSON.stringify(value) : String(value)} />
          ))}
        </dl>

        {serverTranscript && transcript.state === "idle" ? (
          <div className="dv-transcript" aria-label="Transcript">
            {serverTranscript}
          </div>
        ) : null}
        {transcript.state !== "idle" ? (
          <div className={`dv-transcript dv-transcript-${transcript.state}`} role="status" aria-live="polite">
            {transcript.state === "working" && !transcript.text ? "Transcribing…" : transcript.text}
          </div>
        ) : null}

        <div className="dv-row dv-wrap">
          {!encrypted ? (
            <>
              <a className="btn-secondary dv-btn-link" href={audioUrl} download={`${call.call_id}.wav`}>
                Download WAV
              </a>
              <button
                type="button"
                className="btn-secondary"
                disabled={live || audioError || sttReady === false || transcript.state === "working"}
                title={sttReady === false ? "The speech service is not available on the API" : "Transcribe this recording"}
                onClick={() => void transcribe()}
              >
                {transcript.state === "working" ? "Transcribing…" : serverTranscript ? "Transcribe again" : "Transcribe"}
              </button>
            </>
          ) : null}
          <CopyButton text={JSON.stringify(call, null, 2)} label="Copy JSON" />
          {confirmDelete ? (
            <>
              <button
                type="button"
                className="btn-primary dv-danger"
                onClick={() => {
                  void dvStore().deleteCall(call.call_id);
                  onClose();
                }}
              >
                Yes, delete
              </button>
              <button type="button" className="btn-secondary" onClick={() => setConfirmDelete(false)}>
                Keep
              </button>
            </>
          ) : (
            <button type="button" className="btn-secondary" onClick={() => setConfirmDelete(true)}>
              Delete
            </button>
          )}
        </div>
      </div>
    </Sheet>
  );
}
