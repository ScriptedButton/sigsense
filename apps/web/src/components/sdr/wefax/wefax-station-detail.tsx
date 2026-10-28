"use client";

import { useSdrState } from "@/components/sdr/sdr-provider";
import { useTuneWefax } from "@/components/sdr/wefax/use-tune-wefax";
import {
  REACH_TIER_LABEL,
  type StationReachability,
} from "@/lib/sdr/wefax/reachability";
import { isFrequencyOnAir } from "@/lib/sdr/wefax/schedule";
import {
  formatWefaxKhz,
  wefaxTuneHz,
  type WefaxStation,
} from "@/lib/sdr/wefax/stations";

type Props = {
  station: WefaxStation;
  score: StationReachability | undefined;
  nowMin: number;
};

export function WefaxStationDetail({ station, score, nowMin }: Props) {
  const tune = useTuneWefax();
  const radio = useSdrState();
  const distanceKm = score?.distanceKm ?? 0;
  const bestReach = score?.best;
  const bestAssigned = bestReach?.frequency.assignedHz;

  return (
    <div className="wefax-station-detail">
      <div className="mb-1.5 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm text-ink">
            <span className="font-mono text-accent">{station.callsign}</span>{" "}
            {station.name}
          </p>
          <p className="truncate text-[0.65rem] text-muted">
            {station.region} · {Math.round(distanceKm)} km · {station.powerKw}{" "}
            kW
            {score?.onAir ? " · on air" : " · idle"}
          </p>
        </div>
        <button
          type="button"
          className="btn-primary !min-h-7 shrink-0 !px-2.5 !text-[0.7rem]"
          disabled={bestAssigned == null}
          title={
            bestReach && bestAssigned != null
              ? `${bestReach.reason} · USB ${formatWefaxKhz(bestAssigned)} kHz`
              : "No frequency"
          }
          onClick={() => {
            if (bestAssigned == null) return;
            tune(bestAssigned, { decode: true });
          }}
        >
          Tune best
        </button>
      </div>

      <div className="flex flex-wrap gap-1">
        {station.frequencies.map((freq) => {
          const reach = score?.frequencies.find(
            (f) => f.frequency.id === freq.id,
          );
          const tier = reach?.tier ?? "off";
          const onAir = isFrequencyOnAir(freq, station, nowMin);
          const tuneHz = wefaxTuneHz(freq.assignedHz);
          const active =
            Math.abs(radio.frequencyHz - tuneHz) < 800 &&
            radio.scheme === "USB";

          return (
            <button
              key={freq.id}
              type="button"
              className={`preset-chip wefax-freq-chip ${active ? "preset-chip-active" : ""}`}
              title={`${REACH_TIER_LABEL[tier]} · assigned ${formatWefaxKhz(freq.assignedHz)} kHz · dial ${(tuneHz / 1e3).toFixed(1)} kHz USB${reach ? ` · ${reach.reason}` : ""}`}
              onClick={() => tune(freq.assignedHz, { decode: true })}
            >
              <span
                className={`wefax-reach-swatch wefax-reach wefax-reach-${tier}`}
                aria-hidden
              />
              {formatWefaxKhz(freq.assignedHz)}
              {onAir ? (
                <span className="wefax-freq-live" title="Transmitter scheduled">
                  live
                </span>
              ) : null}
            </button>
          );
        })}
      </div>

      {bestReach ? (
        <p className="mt-1.5 text-[0.6rem] leading-relaxed text-muted">
          {REACH_TIER_LABEL[bestReach.tier]} · {bestReach.reason}. Dial USB
          assigned − 1.9 kHz.
        </p>
      ) : null}
    </div>
  );
}
