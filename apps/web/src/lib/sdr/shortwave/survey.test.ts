import assert from "node:assert/strict";
import { test } from "node:test";
import { surveyCarriers, confirmSurveyCarriers, rankListeningCandidates, nearBroadcastChannel } from "./survey";
import type { ReceiverSpectrum } from "../receiver-control/protocol";

function spectrum(): ReceiverSpectrum {
  return { values: Array(2048).fill(-100), binHz: 125, startHz: 5_900_000,
    centerHz: 6_028_000, centerOffsetHz: 0, sampleRate: 256_000, capturedAt: new Date().toISOString() };
}
test("survey favors occupied sidebands over a taller empty carrier and excludes LO DC", () => {
  const s = spectrum();
  for (const [hz, level] of [[5_950_000, -75], [6_000_000, -65], [s.centerHz, -40]]) s.values[Math.round((hz - s.startHz!) / s.binHz)] = level;
  const carrier = Math.round((5_950_000 - s.startHz!) / s.binHz);
  for (let k = -22; k <= 22; k++) if (Math.abs(k) >= 4) s.values[carrier + k] = -87;
  const peaks = surveyCarriers(s, 5_900_000, 6_150_000);
  assert.equal(peaks[0].frequencyHz, 5_950_000);
  assert.ok(peaks.some((p) => p.frequencyHz === 6_000_000));
  assert.ok(peaks.every((p) => p.frequencyHz !== s.centerHz));
});
test("two-position check rejects tuner-relative spurs", () => {
  const make = (frequencyHz: number) => ({ frequencyHz, prominenceDb: 20, sidebandExcessDb: 6, score: 32 });
  const matches = confirmSurveyCarriers([make(5_950_000), make(6_010_000)], [make(5_950_125), make(6_135_000)], 263);
  assert.equal(matches.length, 1); assert.equal(matches[0].frequencyHz, 5_950_063);
});
test("flat, invalid, or unresolved spectra cannot manufacture candidates", () => {
  const s = spectrum(); assert.deepEqual(surveyCarriers(s, 0, 10e6), []);
  s.values.fill(NaN); assert.deepEqual(surveyCarriers(s, 0, 10e6), []);
  s.binHz = 5000; assert.deepEqual(surveyCarriers(s, 0, 10e6), []);
});
test("listening order favors a modulated broadcast-grid candidate without hiding off-grid signals", () => {
  const make = (frequencyHz: number, sidebandExcessDb: number, score: number) => ({ frequencyHz, sidebandExcessDb, score, prominenceDb: 20 });
  const original = [make(15_431_375, 15, 49), make(9_490_000, 3, 20), make(5_950_000, 0, 35)];
  assert.deepEqual(rankListeningCandidates(original).map((c) => c.frequencyHz), [9_490_000, 15_431_375, 5_950_000]);
  assert.equal(original[0].frequencyHz, 15_431_375);
  assert.equal(nearBroadcastChannel(9_490_125), true);
  assert.equal(nearBroadcastChannel(15_431_375), false);
  assert.equal(rankListeningCandidates([make(NaN, 4, 20)]).length, 0);
});
test("strong adjacent carriers recommend a narrow passband without reacting to ordinary sidebands", () => {
  const s = spectrum(), at = Math.round((5_950_000 - s.startHz!) / s.binHz);
  s.values[at] = -70;
  let target = surveyCarriers(s, 5_949_000, 5_951_000)[0];
  assert.equal(target.recommendedBandwidthHz, 6000);
  s.values[at + 40] = -75; // another carrier 5 kHz away
  target = surveyCarriers(s, 5_949_000, 5_951_000)[0];
  assert.equal(target.recommendedBandwidthHz, 4000);
  const confirmed = confirmSurveyCarriers([target], [{ ...target, recommendedBandwidthHz: 6000 }], 250);
  assert.equal(confirmed[0].recommendedBandwidthHz, 4000);
});
