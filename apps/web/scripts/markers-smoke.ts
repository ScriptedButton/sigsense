/** Marker selection folds a fragmented broadcast-FM signal into one label. */
import { buildMarkers, markerLabel } from "../src/lib/sdr/signal-id/markers";
import { selectMarkers } from "../src/lib/sdr/signal-id/scope-overlay";
import type { IdentifiedSignal } from "../src/lib/sdr/signal-id/types";

let failed = 0;
const check = (ok: boolean, msg: string) => {
  console.log(`${ok ? "ok  " : "FAIL"} ${msg}`);
  if (!ok) failed++;
};

const identity = (name: string, score: number) => ({
  id: name,
  origin: "artemis" as const,
  name,
  url: "https://example.invalid",
  score,
  categories: [],
});

const sig = (
  id: string,
  offsetHz: number,
  bandwidthHz: number,
  label: string,
  confidence: number,
  snrDb: number,
  names: [string, number][] = [],
): IdentifiedSignal =>
  ({
    id,
    frequencyHz: 93.9e6 + offsetHz,
    offsetHz,
    bandwidthHz,
    snrDb,
    label,
    confidence,
    scheme: null,
    identities: names.map(([n, s]) => identity(n, s)),
    firstSeen: 0,
    lastSeen: 0,
  }) as unknown as IdentifiedSignal;

// The screenshot case: one 200 kHz WBFM plus fragments named after sub-structures.
const signals = [
  sig("a", 0, 200_000, "WBFM", 0.9, 35, [["Radio Data System", 0.9]]),
  sig("b", -60_000, 8_000, "CARRIER", 0.4, 20, [["Radio Data System", 0.8]]),
  sig("c", -40_000, 6_000, "UNKNOWN", 0.3, 18, [["Digital Radio Mondiale", 0.5]]),
  sig("d", 5_000, 12_000, "NBFM", 0.3, 22, [["CBET", 0.75]]),
  sig("e", 30_000, 10_000, "FSK", 0.35, 19, [["PSK2K", 0.6]]),
  sig("f", 70_000, 7_000, "CARRIER", 0.3, 15),
  sig("g", 1e6, 12_500, "NBFM", 0.8, 25, [["FRS / GMRS", 0.9]]),
];

const markers = buildMarkers(signals);
const strongest = selectMarkers(markers, "strongest");
check(strongest.length === 2, `strongest: 7 detections -> 2 markers (got ${strongest.length})`);
check((strongest[0].count ?? 1) === 6, `FM marker folded 5 fragments (count ${strongest[0].count})`);
check(strongest[0].label === "WBFM", `FM labelled by class, not RDS (got ${strongest[0].label})`);
check(strongest[1].label === "FRS / GMRS", `separate GMRS signal keeps its name (got ${strongest[1].label})`);
check(selectMarkers(markers, "all").length === 7, "all keeps every detection");
check(selectMarkers(markers, "off").length === 0, "off hides all");

check(markerLabel(signals[3]) === "CBET", "confident whole-signal catalog name is used");
check(markerLabel(signals[2]) === "UNKNOWN" || markerLabel(signals[2]) === "?", `weak catalog match falls back to class (got ${markerLabel(signals[2])})`);

// Adjacent narrow carriers must not fold into each other.
const adjacent = buildMarkers([
  sig("x", 0, 12_500, "NBFM", 0.8, 25),
  sig("y", 25_000, 12_500, "NBFM", 0.8, 25),
  sig("z", 50_000, 12_500, "NBFM", 0.8, 25),
]);
check(selectMarkers(adjacent, "strongest").length === 3, "separate carriers stay separate");

if (failed) process.exit(1);
console.log("markers smoke passed");
