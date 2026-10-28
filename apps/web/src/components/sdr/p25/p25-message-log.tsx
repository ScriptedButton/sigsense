"use client";

import { useP25State } from "@/components/sdr/sdr-provider";
import { nacHex } from "@/lib/sdr/p25/types";

export function P25MessageLog() {
  const p25 = useP25State();
  if (!p25.enabled) return null;

  return (
    <div
      className="transcript-pane mt-3 max-h-40 overflow-y-auto font-mono text-[0.7rem] leading-relaxed"
      aria-live="polite"
    >
      {p25.messages.length === 0 ? (
        <p className="font-sans text-sm text-muted">
          TSBK / NID messages appear here.
        </p>
      ) : (
        <ul className="m-0 list-none space-y-1 p-0">
          {p25.messages.map((m) => (
            <li key={m.id} className="text-ink">
              <span className="text-muted">
                {nacHex(m.nac)} · {m.opcodeName}
              </span>{" "}
              {m.summary}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
