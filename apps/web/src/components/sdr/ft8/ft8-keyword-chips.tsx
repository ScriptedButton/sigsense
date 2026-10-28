"use client";

import { CallsignButton } from "@/components/sdr/callsign/callsign-button";
import {
  ft8KeywordKindLabel,
  type Ft8KeywordHit,
  type Ft8KeywordKind,
} from "@/lib/sdr/ft8/keywords";

type Ft8KeywordChipsProps = {
  keywords: Ft8KeywordHit[];
};

const KIND_CLASS: Record<Ft8KeywordKind, string> = {
  procedure: "ft8-kw-procedure",
  report: "ft8-kw-report",
  grid: "ft8-kw-grid",
  callsign: "ft8-kw-callsign",
};

export function Ft8KeywordChips({ keywords }: Ft8KeywordChipsProps) {
  if (keywords.length === 0) return null;

  return (
    <ul className="ft8-kw-chips" aria-label="Identified FT8 keywords">
      {keywords.map((kw) => {
        const title = `${ft8KeywordKindLabel(kw.kind)} · ${kw.meaning}`;
        const body = (
          <>
            <span className="ft8-kw-chip-token">{kw.token}</span>
            <span className="ft8-kw-chip-meaning">
              {kw.kind === "callsign" ? "Look up" : kw.meaning}
            </span>
          </>
        );

        return (
          <li key={`${kw.kind}-${kw.token}-${kw.index}`}>
            {kw.kind === "callsign" ? (
              <CallsignButton
                callsign={kw.token}
                className={`ft8-kw-chip ft8-kw-chip-btn ${KIND_CLASS[kw.kind]}`}
                title={`${title} — click to look up`}
              >
                {body}
              </CallsignButton>
            ) : (
              <span
                className={`ft8-kw-chip ${KIND_CLASS[kw.kind]}`}
                title={title}
              >
                {body}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
