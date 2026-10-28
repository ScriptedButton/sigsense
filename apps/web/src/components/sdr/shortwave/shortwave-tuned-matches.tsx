"use client";

import { useMemo } from "react";

import { ShortwaveList } from "@/components/sdr/shortwave/shortwave-list";
import { useShortwaveSchedule } from "@/components/sdr/shortwave/use-shortwave-schedule";
import { useTuneShortwave } from "@/components/sdr/shortwave/use-tune-shortwave";
import { useSdrState } from "@/components/sdr/sdr-provider";
import { lookupAtFrequency } from "@/lib/sdr/shortwave/schedule";

/**
 * Pins EiBi matches for the current dial frequency above the explorer list.
 */
export function ShortwaveTunedMatches() {
  const radio = useSdrState();
  const tune = useTuneShortwave();
  const { schedule, nowMs } = useShortwaveSchedule();

  const listenHz = radio.frequencyHz + radio.offsetHz;

  const matches = useMemo(() => {
    if (!schedule) return [];
    return lookupAtFrequency(
      schedule.entries,
      listenHz,
      new Date(nowMs),
    );
  }, [schedule, listenHz, nowMs]);

  if (matches.length === 0) return null;

  const liveCount = matches.filter((m) => m.onAir).length;

  return (
    <div className="sw-tuned">
      <p className="mb-1 text-[0.65rem] text-muted">
        At your dial · {matches.length} schedule
        {matches.length === 1 ? "" : "s"}
        {liveCount > 0 ? ` · ${liveCount} on air` : ""}
      </p>
      <ShortwaveList items={matches} onTune={tune} limit={12} />
    </div>
  );
}
