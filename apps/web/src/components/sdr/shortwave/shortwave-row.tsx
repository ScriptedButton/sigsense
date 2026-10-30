"use client";

import { useSdrState } from "@/components/sdr/sdr-provider";
import { bandForKHz } from "@/lib/sdr/shortwave/bands";
import { languageLabel } from "@/lib/sdr/shortwave/languages";
import {
  formatKHz,
  formatLiveEta,
  formatWindow,
} from "@/lib/sdr/shortwave/schedule";
import type { ShortwaveChannel } from "@/lib/sdr/shortwave/types";

type ShortwaveRowProps = {
  channel: ShortwaveChannel;
  onTune: (frequencyHz: number) => void;
};

export function ShortwaveRow({ channel, onTune }: ShortwaveRowProps) {
  const radio = useSdrState();
  const live = channel.primary;
  const { entry } = live;
  const band = bandForKHz(entry.f);
  const listenHz = radio.frequencyHz + radio.offsetHz;
  const active =
    Math.abs(listenHz - live.frequencyHz) < 1_500 && radio.scheme === "AM";
  const alternateNames = [...new Set(
    channel.broadcasts.slice(1).map((row) => row.entry.n).filter(Boolean),
  )];

  return (
    <li>
      <button
        type="button"
        className={`sw-row ${live.onAir ? "sw-row-live" : ""} ${
          active ? "sw-row-active" : ""
        }`}
        onClick={() => onTune(live.frequencyHz)}
        title={`Tune AM ${formatKHz(entry.f)} kHz · ${entry.n}`}
        aria-current={active ? "true" : undefined}
        aria-label={`${active ? "Tuned to" : "Tune"} ${formatKHz(entry.f)} kilohertz, ${entry.n}, ${formatLiveEta(live)}`}
      >
        <span className="sw-row-freq font-mono">
          {formatKHz(entry.f)}
          <span className="sw-row-unit"> kHz</span>
        </span>
        <span className="sw-row-main">
          <span className="sw-row-name">
            {entry.n}
            {channel.broadcasts.length > 1 && (
              <span className="sw-row-count"> +{channel.broadcasts.length - 1}</span>
            )}
          </span>
          <span className="sw-row-meta">
            {languageLabel(entry.l)}
            {band ? ` · ${band.label}` : ""}
            {entry.i ? ` · ${entry.i}` : ""}
          </span>
          {alternateNames.length > 0 && (
            <span className="sw-row-meta" title={alternateNames.join(", ")}>
              Also scheduled: {alternateNames.slice(0, 2).join(", ")}
              {alternateNames.length > 2 ? ` +${alternateNames.length - 2}` : ""}
            </span>
          )}
        </span>
        <span className="sw-row-time">
          {active && <span className="sw-row-tuned">Tuned</span>}
          <span className="sw-row-eta">{formatLiveEta(live)}</span>
          <span className="sw-row-window font-mono">
            {formatWindow(entry)}
          </span>
        </span>
      </button>
    </li>
  );
}
