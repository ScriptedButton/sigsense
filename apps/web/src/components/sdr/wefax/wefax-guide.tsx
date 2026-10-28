"use client";

import { useEffect, useMemo, useState } from "react";
import { WefaxMap } from "@/components/sdr/wefax/wefax-map";
import { WefaxSchedule } from "@/components/sdr/wefax/wefax-schedule";
import { WefaxStationDetail } from "@/components/sdr/wefax/wefax-station-detail";
import { useListenerLocation } from "@/components/sdr/wefax/use-listener-location";
import { useTuneWefax } from "@/components/sdr/wefax/use-tune-wefax";
import {
  bestTuneForStation,
  scoreStation,
} from "@/lib/sdr/wefax/reachability";
import {
  nextProductForStation,
  utcMinuteNow,
  type WefaxLiveProduct,
} from "@/lib/sdr/wefax/schedule";
import {
  WEFAX_STATIONS,
  getWefaxStation,
} from "@/lib/sdr/wefax/stations";

export function WefaxGuide() {
  const { location, requestPrecise, locating } = useListenerLocation();
  const tune = useTuneWefax();
  const [selectedId, setSelectedId] = useState(WEFAX_STATIONS[0]!.id);
  const [nowMin, setNowMin] = useState(() => utcMinuteNow());
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => {
      setNowMin(utcMinuteNow());
      setNowMs(Date.now());
    }, 15_000);
    return () => window.clearInterval(id);
  }, []);

  const station = getWefaxStation(selectedId) ?? WEFAX_STATIONS[0]!;
  const now = useMemo(() => new Date(nowMs), [nowMs]);

  const scores = useMemo(
    () =>
      WEFAX_STATIONS.map((s) =>
        scoreStation(s, location.lat, location.lon, now),
      ),
    [location.lat, location.lon, now],
  );

  const selectedScore = scores.find((s) => s.station.id === station.id);

  const tuneLive = (live: WefaxLiveProduct) => {
    setSelectedId(live.station.id);
    const best = bestTuneForStation(
      live.station,
      location.lat,
      location.lon,
      now,
    );
    const assigned =
      best?.assignedHz ?? live.station.frequencies[0]?.assignedHz;
    if (assigned == null) return;
    tune(assigned, { decode: true });
  };

  // Prefer a station that is currently transmitting when first mounting.
  useEffect(() => {
    for (const s of WEFAX_STATIONS) {
      const next = nextProductForStation(s, utcMinuteNow());
      if (next?.transmitting) {
        setSelectedId(s.id);
        return;
      }
    }
  }, []);

  return (
    <div className="wefax-guide mt-3 space-y-3 border-t border-line pt-3">
      <WefaxMap
        stations={WEFAX_STATIONS}
        scores={scores}
        selectedId={station.id}
        listener={location}
        onSelect={setSelectedId}
        onLocate={requestPrecise}
        locating={locating}
      />
      <WefaxStationDetail
        station={station}
        score={selectedScore}
        nowMin={nowMin}
      />
      <WefaxSchedule
        station={station}
        nowMin={nowMin}
        onTuneNext={tuneLive}
      />
    </div>
  );
}
