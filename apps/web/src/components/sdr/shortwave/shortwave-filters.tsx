"use client";

import { SHORTWAVE_BANDS } from "@/lib/sdr/shortwave/bands";
import { languageLabel } from "@/lib/sdr/shortwave/languages";
import type {
  ShortwaveBandId,
  ShortwaveWhen,
} from "@/lib/sdr/shortwave/types";

type ShortwaveFiltersProps = {
  when: ShortwaveWhen;
  onWhenChange: (when: ShortwaveWhen) => void;
  bandId: ShortwaveBandId | null;
  onBandChange: (bandId: ShortwaveBandId | null) => void;
  language: string | null;
  onLanguageChange: (language: string | null) => void;
  languages: readonly string[];
  query: string;
  onQueryChange: (query: string) => void;
};

const WHEN_OPTIONS: { id: ShortwaveWhen; label: string }[] = [
  { id: "now", label: "On now" },
  { id: "soon", label: "Soon" },
  { id: "all", label: "All" },
];

export function ShortwaveFilters({
  when,
  onWhenChange,
  bandId,
  onBandChange,
  language,
  onLanguageChange,
  languages,
  query,
  onQueryChange,
}: ShortwaveFiltersProps) {
  return (
    <div className="sw-filters space-y-2">
      <div className="flex flex-wrap gap-1" role="group" aria-label="When">
        {WHEN_OPTIONS.map((opt) => (
          <button
            key={opt.id}
            type="button"
            className={`mode-chip ${when === opt.id ? "mode-chip-active" : ""}`}
            aria-pressed={when === opt.id}
            onClick={() => onWhenChange(opt.id)}
          >
            {opt.label}
          </button>
        ))}
      </div>

      <label className="block text-[0.65rem] text-muted">
        Search
        <input
          type="search"
          className="sw-search mt-0.5"
          placeholder="Station, language, kHz…"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
        />
      </label>

      <div className="grid grid-cols-2 gap-2">
        <label className="block text-[0.65rem] text-muted">
          Band
          <select
            className="sw-select mt-0.5"
            value={bandId ?? ""}
            onChange={(e) =>
              onBandChange(
                e.target.value ? (e.target.value as ShortwaveBandId) : null,
              )
            }
          >
            <option value="">Any</option>
            {SHORTWAVE_BANDS.map((b) => (
              <option key={b.id} value={b.id}>
                {b.label}
              </option>
            ))}
          </select>
        </label>

        <label className="block text-[0.65rem] text-muted">
          Language
          <select
            className="sw-select mt-0.5"
            value={language ?? ""}
            onChange={(e) => onLanguageChange(e.target.value || null)}
          >
            <option value="">Any</option>
            {languages.map((code) => (
              <option key={code} value={code}>
                {languageLabel(code)}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  );
}
