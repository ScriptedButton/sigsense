"use client";

import {
  formatCountdown,
  formatUtcHmm,
  nextProductForStation,
  nextWefaxWorldwide,
  upcomingProducts,
  type WefaxLiveProduct,
} from "@/lib/sdr/wefax/schedule";
import type { WefaxStation } from "@/lib/sdr/wefax/stations";

type Props = {
  station: WefaxStation;
  nowMin: number;
  onTuneNext: (live: WefaxLiveProduct) => void;
};

export function WefaxSchedule({ station, nowMin, onTuneNext }: Props) {
  const local = nextProductForStation(station, nowMin);
  const world = nextWefaxWorldwide(nowMin);
  const upcoming = upcomingProducts(station, 150, nowMin).slice(0, 8);
  const elsewhere =
    world &&
    world.station.id !== station.id &&
    (world.transmitting ||
      !local?.transmitting ||
      world.minutesUntil < (local?.minutesUntil ?? 9999))
      ? world
      : null;

  return (
    <div className="wefax-schedule">
      <p className="mb-1.5 text-[0.65rem] font-medium text-muted">Schedule</p>

      {local ? (
        <div className="wefax-next-banner">
          <div className="min-w-0 flex-1">
            <p className="text-[0.65rem] uppercase tracking-wide text-muted">
              {local.transmitting ? "On air here" : "Next here"}
            </p>
            <p className="truncate font-mono text-sm text-ink">
              {station.callsign} · {formatUtcHmm(local.product.startMin)}
            </p>
            <p className="truncate text-[0.7rem] text-muted">
              {local.product.title}
            </p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            <span className="font-mono text-sm text-accent">
              {local.transmitting
                ? "LIVE"
                : formatCountdown(local.minutesUntil)}
            </span>
            <button
              type="button"
              className="btn-secondary !min-h-7 !px-2 !text-[0.65rem]"
              onClick={() => onTuneNext(local)}
            >
              Tune
            </button>
          </div>
        </div>
      ) : null}

      {elsewhere ? (
        <p className="mt-1.5 text-[0.65rem] text-muted">
          {elsewhere.transmitting ? "Also live" : "Sooner"} ·{" "}
          <button
            type="button"
            className="text-accent underline-offset-2 hover:underline"
            onClick={() => onTuneNext(elsewhere)}
          >
            {elsewhere.station.callsign} {formatUtcHmm(elsewhere.product.startMin)}
          </button>
          {" · "}
          {elsewhere.transmitting
            ? "LIVE"
            : formatCountdown(elsewhere.minutesUntil)}
        </p>
      ) : null}

      <ul className="wefax-schedule-list">
        {upcoming.map((item) => (
          <li key={`${item.product.startMin}-${item.product.title}`}>
            <button
              type="button"
              className={`wefax-schedule-row ${item.transmitting ? "wefax-schedule-row-live" : ""}`}
              onClick={() => onTuneNext(item)}
              title="Tune USB, start Demo if needed, and decode"
            >
              <span className="font-mono text-[0.7rem] text-accent">
                {formatUtcHmm(item.product.startMin)}
              </span>
              <span className="min-w-0 flex-1 truncate text-[0.7rem] text-ink">
                {item.product.title}
              </span>
              <span className="shrink-0 font-mono text-[0.65rem] text-muted">
                {item.transmitting
                  ? "LIVE"
                  : formatCountdown(item.minutesUntil)}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {upcoming.length === 0 ? (
        <p className="text-[0.65rem] text-muted">No charts in the next 2.5 h.</p>
      ) : null}
    </div>
  );
}
