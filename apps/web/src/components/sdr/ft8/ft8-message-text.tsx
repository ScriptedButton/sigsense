"use client";

import type { ReactNode } from "react";

import { CallsignButton } from "@/components/sdr/callsign/callsign-button";
import {
  findFt8Keywords,
  ft8KeywordKindLabel,
  type Ft8KeywordKind,
} from "@/lib/sdr/ft8/keywords";

type Ft8MessageTextProps = {
  msg: string;
};

const KIND_CLASS: Record<Ft8KeywordKind, string> = {
  procedure: "ft8-kw-mark-procedure",
  report: "ft8-kw-mark-report",
  grid: "ft8-kw-mark-grid",
  callsign: "ft8-kw-mark-callsign",
};

function renderHighlighted(msg: string): ReactNode[] {
  const hits = findFt8Keywords(msg);
  if (hits.length === 0) return [msg];

  const nodes: ReactNode[] = [];
  let cursor = 0;
  for (const hit of hits) {
    if (hit.index > cursor) {
      nodes.push(msg.slice(cursor, hit.index));
    }
    const end = hit.index + hit.length;
    const slice = msg.slice(hit.index, end);
    const title = `${ft8KeywordKindLabel(hit.kind)} · ${hit.meaning}`;
    if (hit.kind === "callsign") {
      nodes.push(
        <CallsignButton
          key={`${hit.index}-${hit.token}`}
          callsign={hit.token}
          className={`ft8-kw-mark ft8-kw-mark-btn ${KIND_CLASS[hit.kind]}`}
          title={`${title} — click to look up`}
        >
          {slice}
        </CallsignButton>,
      );
    } else {
      nodes.push(
        <mark
          key={`${hit.index}-${hit.token}`}
          className={`ft8-kw-mark ${KIND_CLASS[hit.kind]}`}
          title={title}
        >
          {slice}
        </mark>,
      );
    }
    cursor = end;
  }
  if (cursor < msg.length) nodes.push(msg.slice(cursor));
  return nodes;
}

/** Inline FT8 message with keyword / callsign highlights. */
export function Ft8MessageText({ msg }: Ft8MessageTextProps) {
  return <>{renderHighlighted(msg)}</>;
}
