"use client";

import { useEffect, useState } from "react";

import { loadShortwaveSchedule } from "@/lib/sdr/shortwave/schedule";
import type { ShortwaveSchedule } from "@/lib/sdr/shortwave/types";

/**
 * Lazy-loads the EiBi schedule once (shared promise) and ticks every 30s
 * so on-air windows stay current.
 */
export function useShortwaveSchedule(): {
  schedule: ShortwaveSchedule | null;
  nowMs: number;
} {
  const [schedule, setSchedule] = useState<ShortwaveSchedule | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    let cancelled = false;
    void loadShortwaveSchedule().then((loaded) => {
      if (!cancelled) setSchedule(loaded);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  return { schedule, nowMs };
}
