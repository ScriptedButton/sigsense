"use client";

import { useEffect, useRef } from "react";

import { Ft8MessageText } from "@/components/sdr/ft8/ft8-message-text";
import type { Ft8Message } from "@/lib/sdr/ft8/types";

type Ft8MessageListProps = {
  messages: Ft8Message[];
  enabled: boolean;
};

function formatSnr(snr: number): string {
  const rounded = Math.round(snr);
  return rounded >= 0 ? `+${rounded}` : String(rounded);
}

export function Ft8MessageList({ messages, enabled }: Ft8MessageListProps) {
  const scrollerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  return (
    <div
      ref={scrollerRef}
      className="transcript-pane mt-3"
      aria-live="polite"
      aria-relevant="additions"
    >
      {messages.length === 0 ? (
        <p className="text-sm text-muted">
          {enabled
            ? "Decodes appear at the end of each 15 s UTC period. Keywords and grids are highlighted; click a callsign to look it up."
            : "Enable Decode to read FT8 from USB demod audio."}
        </p>
      ) : (
        <ul className="space-y-1.5 font-mono text-xs leading-snug">
          {messages.map((m) => (
            <li key={m.id} className="text-ink">
              <time
                className="mr-2 text-muted"
                dateTime={new Date(m.at).toISOString()}
              >
                {new Date(m.at).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                })}
              </time>
              <span className="mr-2 tabular-nums text-accent">
                {formatSnr(m.snr)}
              </span>
              <span className="mr-2 tabular-nums text-muted">
                {Math.round(m.freq)}
              </span>
              <Ft8MessageText msg={m.msg} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
