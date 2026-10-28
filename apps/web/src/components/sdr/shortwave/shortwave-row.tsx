"use client";

import { useSdrState } from "@/components/sdr/sdr-provider";
import { bandForKHz } from "@/lib/sdr/shortwave/bands";
import { languageLabel } from "@/lib/sdr/shortwave/languages";
import {
  formatKHz,
  formatLiveEta,
  formatWindow,
} from "@/lib/sdr/shortwave/schedule";
import type { ShortwaveLive } from "@/lib/sdr/shortwave/types";

type ShortwaveRowProps = {
  live: ShortwaveLive;
  onTune: (frequencyHz: number) => void;
};

export function ShortwaveRow({ live, onTune }: ShortwaveRowProps) {
  const radio = useSdrState();
  const { entry } = live;
  const band = bandForKHz(entry.f);
  const listenHz = radio.frequencyHz + radio.offsetHz;
  const active =
    Math.abs(listenHz - live.frequencyHz) < 1_500 && radio.scheme === "AM";

  return (
    <li>
      <button
        type="button"
        className={`sw-row ${live.onAir ? "sw-row-live" : ""} ${
          active ? "sw-row-active" : ""
        }`}
        onClick={() => onTune(live.frequencyHz)}
        title={`Tune AM ${formatKHz(entry.f)} kHz · ${entry.n}`}
      >
        <span className="sw-row-freq font-mono">
          {formatKHz(entry.f)}
          <span className="sw-row-unit"> kHz</span>
        </span>
        <span className="sw-row-main">
          <span className="sw-row-name">{entry.n}</span>
          <span className="sw-row-meta">
            {languageLabel(entry.l)}
            {band ? ` · ${band.label}` : ""}
            {entry.i ? ` · ${entry.i}` : ""}
          </span>
        </span>
        <span className="sw-row-time">
          <span className="sw-row-eta">{formatLiveEta(live)}</span>
          <span className="sw-row-window font-mono">
            {formatWindow(entry)}
          </span>
        </span>
      </button>
    </li>
  );
}
