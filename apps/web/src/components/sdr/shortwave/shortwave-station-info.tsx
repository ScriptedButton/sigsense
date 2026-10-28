"use client";

import { useMemo } from "react";

import { useShortwaveSchedule } from "@/components/sdr/shortwave/use-shortwave-schedule";
import { useSdrState } from "@/components/sdr/sdr-provider";
import { languageLabel } from "@/lib/sdr/shortwave/languages";
import {
  formatKHz,
  formatLiveEta,
  formatWindow,
  lookupAtFrequency,
} from "@/lib/sdr/shortwave/schedule";

/**
 * Auto-shows EiBi broadcast identity when the listen frequency lands on a
 * scheduled shortwave channel. Renders nothing off-channel.
 */
export function ShortwaveStationInfo() {
  const radio = useSdrState();
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

  const primary = matches[0];
  const { entry } = primary;
  const extra = matches.length - 1;
  const onAirCount = matches.filter((m) => m.onAir).length;

  return (
    <div
      className="app-header-sw"
      role="status"
      aria-live="polite"
      title={
        extra > 0
          ? `${matches.length} broadcasts on this channel`
          : `${entry.n} · ${formatWindow(entry)} UTC`
      }
    >
      <p className="app-header-sw-primary">
        {primary.onAir ? (
          <span className="app-header-sw-live" aria-hidden>
            ●
          </span>
        ) : null}
        <span className="app-header-sw-name">{entry.n}</span>
        <span className="text-muted">
          {" · "}
          {languageLabel(entry.l)}
          {" · "}
          <span
            className={
              primary.onAir ? "app-header-sw-eta-live" : undefined
            }
          >
            {formatLiveEta(primary)}
          </span>
        </span>
      </p>
      <p className="app-header-sw-meta">
        <span className="font-mono tabular-nums">
          {formatKHz(entry.f)} kHz
        </span>
        <span className="text-muted">
          {" · "}
          <span className="font-mono">{formatWindow(entry)}</span>
          {" UTC"}
          {entry.i ? ` · ${entry.i}` : ""}
          {extra > 0
            ? ` · +${extra} more${onAirCount > 1 ? ` (${onAirCount} live)` : ""}`
            : ""}
        </span>
      </p>
    </div>
  );
}
