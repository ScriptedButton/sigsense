"use client";

import { useP25State } from "@/components/sdr/sdr-provider";
import { formatFrequency } from "@/lib/sdr/format";

export function P25CallList() {
  const p25 = useP25State();
  const calls = [
    ...p25.activeCalls,
    ...p25.recentCalls.filter(
      (c) => !p25.activeCalls.some((a) => a.id === c.id),
    ),
  ].slice(0, 12);

  if (!p25.enabled) return null;

  return (
    <div className="mt-3">
      <p className="text-[0.65rem] uppercase tracking-wide text-muted">Calls</p>
      {calls.length === 0 ? (
        <p className="mt-1 text-xs text-muted">No grants or voice yet.</p>
      ) : (
        <ul className="mt-1 max-h-36 space-y-1 overflow-y-auto text-xs">
          {calls.map((c) => (
            <li
              key={c.id}
              className={`flex flex-wrap items-baseline justify-between gap-2 font-mono ${
                c.active ? "text-accent" : "text-ink"
              }`}
            >
              <span>
                TG {c.tgid}
                {c.sourceId != null ? ` · src ${c.sourceId}` : ""}
                {c.encrypted ? " · ENC" : ""}
                {c.emergency ? " · EMG" : ""}
              </span>
              <span className="text-muted">
                {c.frequencyHz > 0 ? formatFrequency(c.frequencyHz) : "—"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
