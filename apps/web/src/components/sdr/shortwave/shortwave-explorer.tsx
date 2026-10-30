"use client";

import { useMemo, useState } from "react";

import { KiwiConnect } from "@/components/sdr/kiwi/kiwi-connect";
import { ShortwaveFilters } from "@/components/sdr/shortwave/shortwave-filters";
import { ShortwaveList } from "@/components/sdr/shortwave/shortwave-list";
import { ShortwaveListeningControls } from "@/components/sdr/shortwave/shortwave-listening-controls";
import { ReceivedSignals } from "@/components/sdr/shortwave/received-signals";
import { ShortwaveSurveyResults } from "@/components/sdr/shortwave/shortwave-survey-results";
import { AutofinderPanel } from "@/components/sdr/shortwave/autofinder-panel";
import { ServiceFinderPanel } from "@/components/sdr/shortwave/service-finder-panel";
import { KiwiDiversityPanel } from "@/components/sdr/shortwave/kiwi-diversity-panel";
import { ShortwaveTunedMatches } from "@/components/sdr/shortwave/shortwave-tuned-matches";
import { useShortwaveSchedule } from "@/components/sdr/shortwave/use-shortwave-schedule";
import { useTuneShortwave } from "@/components/sdr/shortwave/use-tune-shortwave";
import {
  adjacentBroadcastFrequency,
  collectLanguages,
  filterSchedule,
  firstBroadcastFrequencyForBand,
  groupBroadcastChannels,
} from "@/lib/sdr/shortwave/schedule";
import type { ShortwaveChannelSort } from "@/lib/sdr/shortwave/schedule";
import { SHORTWAVE_BANDS } from "@/lib/sdr/shortwave/bands";
import { useSdrState } from "@/components/sdr/sdr-provider";
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
  const radio = useSdrState();
  const { schedule, nowMs } = useShortwaveSchedule();
  const [when, setWhen] = useState<ShortwaveWhen>("now");
  const [bandId, setBandId] = useState<ShortwaveBandId | null>(null);
  const [language, setLanguage] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<ShortwaveChannelSort>("on-air");

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

  const listeningHz = radio.frequencyHz + radio.offsetHz;
  const channels = useMemo(
    () => groupBroadcastChannels(items, listeningHz, sort),
    [items, listeningHz, sort],
  );
  const previousHz = adjacentBroadcastFrequency(items, listeningHz, -1);
  const nextHz = adjacentBroadcastFrequency(items, listeningHz, 1);
  const filtersActive =
    when !== "now" || bandId !== null || language !== null || query !== "";
  const scheduleAgeDays = schedule
    ? Math.floor((nowMs - Date.parse(schedule.fetchedAt)) / 86_400_000)
    : 0;

  const resetFilters = () => {
    setWhen("now");
    setBandId(null);
    setLanguage(null);
    setQuery("");
  };

  const browseBand = (id: ShortwaveBandId) => {
    setBandId(id);
    setWhen("now");
    setLanguage(null);
    setQuery("");
    if (!schedule) return;
    const hz = firstBroadcastFrequencyForBand(schedule.entries, id, new Date(nowMs));
    if (hz !== null) tune(hz);
  };

  return (
    <fieldset className="control-group">
      <legend className="control-label">Shortwave explorer</legend>
      <AutofinderPanel />
      <ServiceFinderPanel />
      <KiwiDiversityPanel />
      <ShortwaveSurveyResults />
      {!schedule ? (
        <p className="text-[0.65rem] text-muted">Loading EiBi schedule…</p>
      ) : (
        <>
          <p className="mb-2 text-[0.65rem] leading-relaxed text-muted">
            EiBi {schedule.season} · {onAirCount.toLocaleString()} scheduled now
          </p>

          <div className="mb-2 flex flex-wrap gap-1" aria-label="Tune a shortwave band">
            {SHORTWAVE_BANDS.map((band) => (
              <button
                key={band.id}
                type="button"
                className={`mode-chip ${bandId === band.id ? "mode-chip-active" : ""}`}
                onClick={() => browseBand(band.id)}
                title={`Browse ${band.label} and tune its first scheduled channel`}
              >
                {band.label}
              </button>
            ))}
          </div>

          <ReceivedSignals />

          <ShortwaveTunedMatches />

          <div className="mb-2 text-[0.65rem] leading-relaxed text-muted">
            <p>
              Schedule matches are possible transmissions, not detected signals.
              {radio.connection === "disconnected"
                ? " Connect a receiver to hear and verify them."
                : radio.connection === "demo"
                  ? " Demo audio is simulated; connect a real receiver to find live stations."
                  : " Use the waterfall and audio to confirm what is actually present."}
              {scheduleAgeDays > 45 ? ` Schedule data is ${scheduleAgeDays} days old.` : ""}
            </p>
            {radio.connection === "disconnected" && (
              <div className="mt-2">
                <KiwiConnect align="center" />
              </div>
            )}
          </div>

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

          <div className="sw-navigation" aria-label="Broadcast navigation">
            <button
              type="button"
              className="sw-nav-button"
              disabled={previousHz === null}
              onClick={() => previousHz !== null && tune(previousHz)}
              title="Tune the previous visible frequency"
            >
              ← Previous
            </button>
            <button
              type="button"
              className="sw-nav-button"
              disabled={nextHz === null}
              onClick={() => nextHz !== null && tune(nextHz)}
              title="Tune the next visible frequency"
            >
              Next →
            </button>
            {filtersActive && (
              <button
                type="button"
                className="sw-nav-reset"
                onClick={resetFilters}
              >
                Clear filters
              </button>
            )}
          </div>

          <p className="mb-1 mt-2 text-[0.65rem] text-muted">
            {channels.length.toLocaleString()} scheduled frequenc
            {channels.length === 1 ? "y" : "ies"}
            {items.length !== channels.length && ` · ${items.length.toLocaleString()} broadcasts`}
          </p>

          <label className="mb-1 block text-[0.65rem] text-muted">
            Sort
            <select className="sw-select mt-0.5" value={sort} onChange={(event) => setSort(event.target.value as ShortwaveChannelSort)}>
              <option value="on-air">Scheduled now first</option>
              <option value="nearest">Nearest tuned frequency</option>
              <option value="frequency">Frequency</option>
            </select>
          </label>

          <details className="mb-2 rounded border border-line p-2">
            <summary className="cursor-pointer text-[0.7rem] font-medium text-ink">
              Improve speech clarity
            </summary>
            <div className="mt-2">
              <ShortwaveListeningControls />
            </div>
          </details>

          <ShortwaveList items={channels} onTune={tune} />

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
