/**
 * Smoke: weatherfax schedule + reachability helpers.
 * Run: pnpm exec tsx scripts/wefax-schedule-smoke.ts
 */

import {
  bestTuneForStation,
  haversineKm,
  scoreStation,
} from "../src/lib/sdr/wefax/reachability";
import {
  formatUtcHmm,
  inUtcWindow,
  isStationOnAir,
  nextProductForStation,
  nextWefaxWorldwide,
  utcMinuteNow,
} from "../src/lib/sdr/wefax/schedule";
import {
  WEFAX_STATIONS,
  wefaxTuneHz,
} from "../src/lib/sdr/wefax/stations";

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(msg);
}

const boston = WEFAX_STATIONS.find((s) => s.id === "nmf")!;
assert(boston, "Boston station present");
assert(wefaxTuneHz(9_110_000) === 9_108_100, "carrier offset 1.9 kHz");
assert(formatUtcHmm(150) === "0230Z", "format 0230Z");
assert(inUtcWindow(200, { startMin: 150, endMin: 639 }), "in morning window");
assert(!inUtcWindow(700, { startMin: 150, endMin: 639 }), "outside morning");
assert(
  inUtcWindow(10, { startMin: 1039, endMin: 236 }),
  "overnight wrap",
);

const noon = 12 * 60;
assert(isStationOnAir(boston, 200), "Boston on air morning");
assert(!isStationOnAir(boston, noon), "Boston idle midday gap");

const next = nextProductForStation(boston, 149);
assert(next, "next product before 0230");
assert(next.product.startMin === 150, "next is test pattern");
assert(next.minutesUntil === 1, "1 minute until");

const live = nextProductForStation(boston, 152);
assert(live?.transmitting, "transmitting shortly after start");

const worldAt149 = nextWefaxWorldwide(149);
assert(worldAt149, "worldwide next exists");
assert(worldAt149.transmitting, "something live at 0149Z");
assert(
  worldAt149.minutesUntil === 0 &&
    elapsedOk(worldAt149.product.startMin, 149),
  "live pick is within chart window",
);

const bostonNext = nextProductForStation(boston, 149);
assert(bostonNext?.product.startMin === 150, "Boston next is 0230Z test");
assert(bostonNext.minutesUntil === 1, "Boston in 1m");

const livePick = nextProductForStation(boston, 160);
assert(livePick?.transmitting, "still in chart window");
assert(livePick.product.startMin === 153, "prefer most recent live chart");

function elapsedOk(startMin: number, nowMin: number): boolean {
  return (nowMin - startMin + 1440) % 1440 < 12;
}

const dist = haversineKm(42.24, -70.95, 42.24, -70.95);
assert(dist < 1, "zero distance");

const scored = scoreStation(boston, 40.7, -74.0, new Date("2024-06-15T06:00:00Z"));
assert(scored.distanceKm > 200 && scored.distanceKm < 500, "NYC–Boston ~300km");
assert(scored.frequencies.length === boston.frequencies.length, "all freqs scored");

const best = bestTuneForStation(
  boston,
  40.7,
  -74.0,
  new Date("2024-06-15T06:00:00Z"),
);
assert(best, "best tune exists");
assert(best.tuneHz === wefaxTuneHz(best.assignedHz), "tune = assigned − 1.9k");

const liveNow = nextWefaxWorldwide();
console.log(
  `ok · ${WEFAX_STATIONS.length} stations · now ${formatUtcHmm(utcMinuteNow())} · ${liveNow?.transmitting ? "LIVE" : "next"} ${liveNow?.station.callsign} ${liveNow ? formatUtcHmm(liveNow.product.startMin) : "—"}`,
);
