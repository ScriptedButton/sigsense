"use client";

import { CallsignButton } from "@/components/sdr/callsign/callsign-button";
import {
  keywordKindLabel,
  type MorseKeywordHit,
  type MorseKeywordKind,
} from "@/lib/sdr/morse/keywords";

type MorseKeywordChipsProps = {
  keywords: MorseKeywordHit[];
};

const KIND_CLASS: Record<MorseKeywordKind, string> = {
  procedure: "morse-kw-procedure",
  qcode: "morse-kw-qcode",
  prosign: "morse-kw-prosign",
  callsign: "morse-kw-callsign",
};

export function MorseKeywordChips({ keywords }: MorseKeywordChipsProps) {
  if (keywords.length === 0) return null;

  return (
    <ul className="morse-kw-chips" aria-label="Identified Morse keywords">
      {keywords.map((kw) => {
        const title = `${keywordKindLabel(kw.kind)} · ${kw.meaning}`;
        const body = (
          <>
            <span className="morse-kw-chip-token">{kw.token}</span>
            <span className="morse-kw-chip-meaning">
              {kw.kind === "callsign" ? "Look up" : kw.meaning}
            </span>
          </>
        );

        return (
          <li key={`${kw.token}-${kw.index}`}>
            {kw.kind === "callsign" ? (
              <CallsignButton
                callsign={kw.token}
                className={`morse-kw-chip morse-kw-chip-btn ${KIND_CLASS[kw.kind]}`}
                title={`${title} — click to look up`}
              >
                {body}
              </CallsignButton>
            ) : (
              <span
                className={`morse-kw-chip ${KIND_CLASS[kw.kind]}`}
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
