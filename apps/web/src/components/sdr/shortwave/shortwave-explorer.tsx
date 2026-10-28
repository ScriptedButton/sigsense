"use client";

import { useMemo, useState } from "react";

import { ShortwaveFilters } from "@/components/sdr/shortwave/shortwave-filters";
import { ShortwaveList } from "@/components/sdr/shortwave/shortwave-list";
import { ShortwaveTunedMatches } from "@/components/sdr/shortwave/shortwave-tuned-matches";
import { useShortwaveSchedule } from "@/components/sdr/shortwave/use-shortwave-schedule";
import { useTuneShortwave } from "@/components/sdr/shortwave/use-tune-shortwave";
import {
  collectLanguages,
  filterSchedule,
} from "@/lib/sdr/shortwave/schedule";
import type {
  ShortwaveBandId,
  ShortwaveWhen,
} from "@/lib/sdr/shortwave/types";

/**
 * Browse EiBi shortwave schedules and one-tap tune AM.
 * Lives on the Radio rail beside GMRS channel presets.
 */
export function ShortwaveExplorer() {
  const tune = useTuneShortwave();
  const { schedule, nowMs } = useShortwaveSchedule();
  const [when, setWhen] = useState<ShortwaveWhen>("now");
  const [bandId, setBandId] = useState<ShortwaveBandId | null>(null);
  const [language, setLanguage] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const languages = useMemo(
    () => (schedule ? collectLanguages(schedule.entries) : []),
    [schedule],
  );

  const items = useMemo(() => {
    if (!schedule) return [];
    return filterSchedule(
      schedule.entries,
      { when, bandId, language, query },
      new Date(nowMs),
    );
  }, [schedule, when, bandId, language, query, nowMs]);

  const onAirCount = useMemo(() => {
    if (!schedule) return 0;
    return filterSchedule(
      schedule.entries,
      { when: "now", bandId: null, language: null, query: "" },
      new Date(nowMs),
    ).length;
  }, [schedule, nowMs]);

  return (
    <fieldset className="control-group">
      <legend className="control-label">Shortwave explorer</legend>
      {!schedule ? (
        <p className="text-[0.65rem] text-muted">Loading EiBi schedule…</p>
      ) : (
        <>
          <p className="mb-2 text-[0.65rem] leading-relaxed text-muted">
            EiBi {schedule.season} · {onAirCount.toLocaleString()} on air now ·
            tap to tune AM
          </p>

          <ShortwaveTunedMatches />

          <ShortwaveFilters
            when={when}
            onWhenChange={setWhen}
            bandId={bandId}
            onBandChange={setBandId}
            language={language}
            onLanguageChange={setLanguage}
            languages={languages}
            query={query}
            onQueryChange={setQuery}
          />

          <p className="mb-1 mt-2 text-[0.65rem] text-muted">
            {items.length.toLocaleString()} match
            {items.length === 1 ? "" : "es"}
          </p>

          <ShortwaveList items={items} onTune={tune} />

          <p className="mt-2 text-[0.6rem] leading-relaxed text-muted">
            Schedule ©{" "}
            <a
              className="underline decoration-line underline-offset-2 hover:text-ink"
              href={schedule.url}
              target="_blank"
              rel="noreferrer"
            >
              EiBi
            </a>
            {" · "}
            {schedule.fetchedAt}. Refresh with{" "}
            <code className="font-mono">pnpm fetch:shortwave</code>.
          </p>
        </>
      )}
    </fieldset>
  );
}
