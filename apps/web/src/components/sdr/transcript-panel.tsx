"use client";

import { useEffect, useRef, useState } from "react";

import {
  useRadioController,
  useSdrState,
  useTranscriptionState,
} from "@/components/sdr/sdr-provider";
import type { EnginePreference } from "@/lib/sdr/transcription-hub";
import type { TranscriptSegment } from "@/lib/sdr/transcription-engine";
import { transcriptToJson, transcriptToSrt, transcriptToText } from "@/lib/sdr/transcript-export";

const LANGUAGES: { id: string; label: string }[] = [
  { id: "en", label: "English" },
  { id: "auto", label: "Auto-detect" },
  { id: "es", label: "Spanish" },
  { id: "fr", label: "French" },
  { id: "de", label: "German" },
  { id: "pt", label: "Portuguese" },
  { id: "it", label: "Italian" },
  { id: "nl", label: "Dutch" },
  { id: "ru", label: "Russian" },
  { id: "pl", label: "Polish" },
  { id: "ja", label: "Japanese" },
  { id: "zh", label: "Chinese" },
];

function download(filename: string, mime: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function shortModel(model: string): string {
  return model.replace(/^.*\//, "").replace(/^faster-/, "").replace(/-ct2$/, "");
}

function confidenceClass(c: number | undefined): string {
  if (c === undefined) return "";
  if (c >= 0.8) return "stt-conf-high";
  if (c >= 0.55) return "stt-conf-mid";
  return "stt-conf-low";
}

function SegmentText({ segment }: { segment: TranscriptSegment }) {
  // Show uncertain words (probability < 0.5) with a dotted underline.
  if (!segment.words?.length) return <>{segment.text}</>;
  return (
    <>
      {segment.words.map((word, i) => (
        <span
          key={i}
          className={word.p < 0.5 ? "stt-word-unsure" : undefined}
          title={word.p < 0.5 ? `Unsure (${Math.round(word.p * 100)}%)` : undefined}
        >
          {/* Whisper's tokens carry their own leading space, so "x-ray" stays whole. */}
          {i === 0 ? word.w.trimStart() : word.w}
        </span>
      ))}
    </>
  );
}

function Tag({ segment }: { segment: TranscriptSegment }) {
  const parts = [
    segment.frequencyHz ? `${(segment.frequencyHz / 1e6).toFixed(4)} MHz` : null,
    segment.mode || null,
  ].filter(Boolean);
  if (!parts.length) return null;
  return <span className="stt-tag">{parts.join(" · ")}</span>;
}

export function TranscriptPanel() {
  const controller = useRadioController();
  const radio = useSdrState();
  const tx = useTranscriptionState();
  const scrollerRef = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const live = radio.status === "playing";
  const [preference, setPreference] = useState<EnginePreference>("auto");
  const [language, setLanguage] = useState("en");
  const [prompt, setPrompt] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    // Settings are restored by the provider on mount; mirror them into the form.
    const sync = () => {
      setPreference(controller.getTranscriptionPreference());
      const options = controller.getTranscriptionOptions();
      setLanguage(options.language);
      setPrompt(options.prompt);
    };
    const id = requestAnimationFrame(sync);
    return () => cancelAnimationFrame(id);
  }, [controller]);

  useEffect(() => {
    const el = scrollerRef.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [tx.segments.length, tx.partial?.text]);

  const onScroll = () => {
    const el = scrollerRef.current;
    if (el) pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };

  const server = tx.engine === "server";
  const busy = tx.status === "loading" && !tx.enabled;
  const statusLabel = (() => {
    switch (tx.status) {
      case "loading":
        return tx.note ?? (server ? "Connecting to the server…" : `Loading Whisper… ${Math.round(tx.progress * 100)}%`);
      case "listening":
        return tx.note ?? (server ? "Listening — transcribing on the server" : "Listening to demod audio");
      case "transcribing":
        return "Transcribing…";
      case "ready":
        return "Model ready";
      case "error":
        return "Error";
      default:
        return "Off";
    }
  })();

  const engineLabel = tx.enabled
    ? server
      ? `Server${tx.server ? ` · ${shortModel(tx.server.model)} · ${tx.server.gpu ? "GPU" : "CPU"}` : ""}`
      : "On-device (tiny.en)"
    : null;

  const chooseEngine = (next: EnginePreference) => {
    setPreference(next);
    void controller.setTranscriptionPreference(next);
  };
  const chooseLanguage = (next: string) => {
    setLanguage(next);
    controller.setTranscriptionOptions({ language: next });
  };
  const commitPrompt = () => controller.setTranscriptionOptions({ prompt });

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(transcriptToText(tx.segments));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked: the export buttons still work */
    }
  };

  const empty = tx.segments.length === 0 && !tx.partial;

  return (
    <fieldset className="control-group stt-panel">
      <legend className="control-label">Live transcript</legend>

      <div className="stt-row">
        <button
          type="button"
          className={tx.enabled ? "btn-primary" : "btn-secondary"}
          disabled={busy}
          aria-pressed={tx.enabled}
          onClick={() => void controller.setTranscriptionEnabled(!tx.enabled)}
        >
          {tx.enabled ? "Stop" : "Transcribe"}
        </button>
        <button type="button" className="btn-secondary" disabled={empty} onClick={() => controller.clearTranscript()}>
          Clear
        </button>
        <button type="button" className="btn-secondary" disabled={tx.segments.length === 0} onClick={() => void copy()}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>

      <div className="stt-grid">
        <label className="stt-field">
          <span>Engine</span>
          <select value={preference} onChange={(e) => chooseEngine(e.target.value as EnginePreference)}>
            <option value="auto">Auto (server first)</option>
            <option value="server">Server (GPU) — recommended</option>
            <option value="device">On-device (offline)</option>
          </select>
        </label>
        <label className="stt-field">
          <span>Language</span>
          <select
            value={language}
            onChange={(e) => chooseLanguage(e.target.value)}
            disabled={preference === "device"}
            title={preference === "device" ? "The on-device model is English only" : undefined}
          >
            {LANGUAGES.map((l) => (
              <option key={l.id} value={l.id}>
                {l.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <p className="stt-status" role="status">
        <span className={`stt-dot stt-dot-${tx.status}`} aria-hidden />
        {statusLabel}
        {!live && tx.enabled && radio.connection !== "hackrf" ? " · waiting for RF" : null}
        {engineLabel ? <span className="stt-engine"> · {engineLabel}</span> : null}
        {server && tx.latencyMs != null ? <span className="stt-engine"> · {(tx.latencyMs / 1000).toFixed(1)} s</span> : null}
      </p>

      {tx.status === "loading" && !server && (
        <div
          className="mt-2 h-1 overflow-hidden rounded-full bg-line"
          role="progressbar"
          aria-valuenow={Math.round(tx.progress * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className="h-full bg-accent transition-[width] duration-200" style={{ width: `${Math.round(tx.progress * 100)}%` }} />
        </div>
      )}

      {tx.error && (
        <p className="mt-2 text-xs text-danger" role="alert">
          {tx.error}
        </p>
      )}

      <div ref={scrollerRef} onScroll={onScroll} className="transcript-pane stt-pane mt-3">
        {empty ? (
          <p className="text-sm text-muted">
            {tx.enabled
              ? "Speech appears here as it is heard — grey text is still being refined."
              : "Press Transcribe to turn the demodulated audio into text. Works best on clear voice (NBFM, AM, SSB); tune a voice channel first."}
          </p>
        ) : (
          <>
            <ul className="stt-list" aria-live="polite" aria-relevant="additions">
              {tx.segments.map((segment) => (
                <li key={segment.id} className={`stt-line ${confidenceClass(segment.confidence)}`}>
                  <div className="stt-meta">
                    <time dateTime={new Date(segment.at).toISOString()}>
                      {new Date(segment.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                    </time>
                    <Tag segment={segment} />
                    {segment.confidence !== undefined && (
                      <span className="stt-conf" title="Mean word confidence">
                        {Math.round(segment.confidence * 100)}%
                      </span>
                    )}
                  </div>
                  <p>
                    <SegmentText segment={segment} />
                  </p>
                </li>
              ))}
            </ul>
            {tx.partial && tx.partial.text && (
              <p className="stt-partial" aria-hidden>
                <span className="stt-partial-dot" />
                {tx.partial.text}
              </p>
            )}
          </>
        )}
      </div>

      <details className="stt-details">
        <summary>Vocabulary hints</summary>
        <p className="stt-help">
          Words the model should expect — callsigns, place names, jargon. Ten-codes, the phonetic alphabet and GMRS terms are
          already built in on the server.
        </p>
        <textarea
          className="stt-textarea"
          rows={3}
          value={prompt}
          placeholder="e.g. Callsigns KAB1234, WQXY567. Base, Dispatch, Unit 7."
          maxLength={600}
          onChange={(e) => setPrompt(e.target.value)}
          onBlur={commitPrompt}
          aria-label="Vocabulary hints for the speech model"
        />
      </details>

      <details className="stt-details">
        <summary>Export</summary>
        <div className="stt-row mt-2">
          <button type="button" className="btn-secondary" disabled={tx.segments.length === 0} onClick={() => download("transcript.txt", "text/plain", transcriptToText(tx.segments))}>
            Text
          </button>
          <button type="button" className="btn-secondary" disabled={tx.segments.length === 0} onClick={() => download("transcript.srt", "application/x-subrip", transcriptToSrt(tx.segments))}>
            SRT
          </button>
          <button type="button" className="btn-secondary" disabled={tx.segments.length === 0} onClick={() => download("transcript.json", "application/json", transcriptToJson(tx.segments))}>
            JSON
          </button>
        </div>
      </details>

      <p className="stt-help mt-2">
        {server
          ? "Audio is transcribed by a speech model running on your own SigSense server — nothing leaves your network."
          : "On-device mode runs a small English Whisper model in this browser: slower and less accurate, but fully offline."}
      </p>
    </fieldset>
  );
}
