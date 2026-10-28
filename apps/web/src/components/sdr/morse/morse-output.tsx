"use client";

import { useEffect, useRef, type ReactNode } from "react";

import { CallsignButton } from "@/components/sdr/callsign/callsign-button";
import {
  findMorseKeywords,
  keywordKindLabel,
  type MorseKeywordKind,
} from "@/lib/sdr/morse/keywords";

type MorseOutputProps = {
  text: string;
  enabled: boolean;
};

const KIND_CLASS: Record<MorseKeywordKind, string> = {
  procedure: "morse-kw-mark-procedure",
  qcode: "morse-kw-mark-qcode",
  prosign: "morse-kw-mark-prosign",
  callsign: "morse-kw-mark-callsign",
};

function renderHighlighted(text: string): ReactNode[] {
  const hits = findMorseKeywords(text);
  if (hits.length === 0) return [text];

  const nodes: ReactNode[] = [];
  let cursor = 0;
  for (const hit of hits) {
    if (hit.index > cursor) {
      nodes.push(text.slice(cursor, hit.index));
    }
    const end = hit.index + hit.token.length;
    const slice = text.slice(hit.index, end);
    const title = `${keywordKindLabel(hit.kind)} · ${hit.meaning}`;
    if (hit.kind === "callsign") {
      nodes.push(
        <CallsignButton
          key={`${hit.index}-${hit.token}`}
          callsign={hit.token}
          className={`morse-kw-mark morse-kw-mark-btn ${KIND_CLASS[hit.kind]}`}
          title={`${title} — click to look up`}
        >
          {slice}
        </CallsignButton>,
      );
    } else {
      nodes.push(
        <mark
          key={`${hit.index}-${hit.token}`}
          className={`morse-kw-mark ${KIND_CLASS[hit.kind]}`}
          title={title}
        >
          {slice}
        </mark>,
      );
    }
    cursor = end;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

export function MorseOutput({ text, enabled }: MorseOutputProps) {
  const scrollerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [text.length]);

  return (
    <div
      ref={scrollerRef}
      className="transcript-pane mt-3 font-mono text-sm leading-relaxed"
      aria-live="polite"
      aria-relevant="additions"
    >
      {text.length === 0 ? (
        <p className="font-sans text-sm text-muted">
          {enabled
            ? "Characters appear here as Morse elements decode. Keywords and Q-codes are highlighted; click a callsign to look it up."
            : "Enable Decode to read Morse / CW from the current demod."}
        </p>
      ) : (
        <pre className="m-0 whitespace-pre-wrap break-words text-ink">
          {renderHighlighted(text)}
        </pre>
      )}
    </div>
  );
}
