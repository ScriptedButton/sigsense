"use client";

import { ShortwaveRow } from "@/components/sdr/shortwave/shortwave-row";
import type { ShortwaveLive } from "@/lib/sdr/shortwave/types";

type ShortwaveListProps = {
  items: readonly ShortwaveLive[];
  onTune: (frequencyHz: number) => void;
  limit?: number;
};

export function ShortwaveList({
  items,
  onTune,
  limit = 80,
}: ShortwaveListProps) {
  if (items.length === 0) {
    return (
      <p className="sw-empty text-[0.7rem] leading-relaxed text-muted">
        No broadcasts match these filters. Try All, clear the band, or search a
        station name.
      </p>
    );
  }

  const shown = items.slice(0, limit);
  const remaining = items.length - shown.length;

  return (
    <div className="sw-list-wrap">
      <ul className="sw-list" aria-label="Shortwave broadcasts">
        {shown.map((live) => (
          <ShortwaveRow
            key={`${live.entry.f}-${live.entry.a}-${live.entry.b}-${live.entry.n}-${live.entry.l}-${live.entry.s}`}
            live={live}
            onTune={onTune}
          />
        ))}
      </ul>
      {remaining > 0 && (
        <p className="mt-1 text-[0.65rem] text-muted">
          Showing {shown.length} of {items.length} — refine filters to narrow.
        </p>
      )}
    </div>
  );
}
