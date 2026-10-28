"use client";

import { useMemo } from "react";

import { FreqLookupResults } from "@/components/sdr/freq-lookup/freq-lookup-results";
import {
  useRadioController,
  useSdrState,
} from "@/components/sdr/sdr-provider";
import { useShortwaveSchedule } from "@/components/sdr/shortwave/use-shortwave-schedule";
import { formatFrequency } from "@/lib/sdr/format";
import { lookupFrequency } from "@/lib/sdr/freq-lookup";
import type { FreqLookupHit } from "@/lib/sdr/freq-lookup";

/**
 * Identify → Frequency: reverse lookup for the current dial.
 * Composes GMRS, band plan, SigID catalog, and EiBi shortwave.
 */
export function FreqLookupPanel() {
  const controller = useRadioController();
  const radio = useSdrState();
  const { schedule, nowMs } = useShortwaveSchedule();

  const listenHz = radio.frequencyHz + radio.offsetHz;

  const hits = useMemo(
    () =>
      lookupFrequency(listenHz, {
        scheme: radio.scheme,
        bandwidthHz: radio.bandwidthHz,
        shortwaveEntries: schedule?.entries,
        now: new Date(nowMs),
        limit: 14,
      }),
    [
      listenHz,
      radio.scheme,
      radio.bandwidthHz,
      schedule?.entries,
      nowMs,
    ],
  );

  const onTune = (hit: FreqLookupHit) => {
    if (hit.scheme) controller.setScheme(hit.scheme);
    const hz = hit.frequencyHz ?? listenHz;
    void controller.setFrequency(hz);
    controller.setOffset(0);
  };

  return (
    <fieldset className="control-group">
      <legend className="control-label">Frequency lookup</legend>
      <p className="mb-2 text-[0.65rem] leading-relaxed text-muted">
        What’s on {formatFrequency(listenHz)} · GMRS · band plan · SigID ·
        shortwave schedule
      </p>

      {hits.length === 0 ? (
        <p className="text-[0.65rem] text-muted">
          No catalog matches at this frequency. Try a known allocation or run
          Signal ID on the current window.
        </p>
      ) : (
        <FreqLookupResults hits={hits} onTune={onTune} />
      )}
    </fieldset>
  );
}
