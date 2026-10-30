"use client";

import { useEffect, useState } from "react";

import { ShortwaveRow } from "@/components/sdr/shortwave/shortwave-row";
import type { ShortwaveChannel } from "@/lib/sdr/shortwave/types";

type ShortwaveListProps = {
  items: readonly ShortwaveChannel[];
  onTune: (frequencyHz: number) => void;
  pageSize?: number;
};

export function ShortwaveList({
  items,
  onTune,
  pageSize = 40,
}: ShortwaveListProps) {
  const [visibleCount, setVisibleCount] = useState(pageSize);
  useEffect(() => setVisibleCount(pageSize), [items, pageSize]);
  if (items.length === 0) {
    return (
      <p className="sw-empty text-[0.7rem] leading-relaxed text-muted">
        No broadcasts match these filters. Try All, clear the band, or search a
        station name.
      </p>
    );
  }

  const shown = items.slice(0, visibleCount);
  const remaining = items.length - shown.length;

  return (
    <div className="sw-list-wrap">
      <ul className="sw-list" aria-label="Shortwave broadcasts">
        {shown.map((channel) => (
          <ShortwaveRow
            key={channel.frequencyHz}
            channel={channel}
            onTune={onTune}
          />
        ))}
      </ul>
      {remaining > 0 && (
        <button
          type="button"
          className="sw-nav-button m-1 w-[calc(100%-0.5rem)]"
          onClick={() => setVisibleCount((count) => count + pageSize)}
        >
          Show {Math.min(pageSize, remaining)} more channels · {remaining} remaining
        </button>
      )}
    </div>
  );
}
