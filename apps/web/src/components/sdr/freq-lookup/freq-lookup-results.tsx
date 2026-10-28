"use client";

import type { FreqLookupHit } from "@/lib/sdr/freq-lookup";

const SOURCE_LABEL: Record<FreqLookupHit["source"], string> = {
  gmrs: "GMRS",
  "band-plan": "Band",
  sigid: "SigID",
  shortwave: "SW",
};

type FreqLookupResultsProps = {
  hits: FreqLookupHit[];
  onTune: (hit: FreqLookupHit) => void;
};

export function FreqLookupResults({ hits, onTune }: FreqLookupResultsProps) {
  return (
    <ul className="freq-lookup-list" aria-label="Frequency matches">
      {hits.map((hit) => (
        <li key={hit.id}>
          <button
            type="button"
            className={`freq-lookup-row ${hit.onAir ? "freq-lookup-row-live" : ""}`}
            onClick={() => onTune(hit)}
            title={
              hit.frequencyHz
                ? `Tune · ${hit.name}`
                : `Apply mode · ${hit.name}`
            }
          >
            <span className="freq-lookup-source">
              {SOURCE_LABEL[hit.source]}
            </span>
            <span className="freq-lookup-main">
              <span className="freq-lookup-name">{hit.name}</span>
              {(hit.detail || hit.meta) && (
                <span className="freq-lookup-detail">
                  {[hit.detail, hit.meta].filter(Boolean).join(" · ")}
                </span>
              )}
            </span>
            {hit.scheme && (
              <span className="freq-lookup-scheme">{hit.scheme}</span>
            )}
          </button>
          {hit.url && (
            <a
              className="freq-lookup-link"
              href={hit.url}
              target="_blank"
              rel="noreferrer"
            >
              Wiki
            </a>
          )}
        </li>
      ))}
    </ul>
  );
}
