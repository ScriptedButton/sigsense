"use client";

import { useEffect, useState } from "react";
import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";
import { nearBroadcastChannel, type SurveyCarrier } from "@/lib/sdr/shortwave/survey";

type Survey = {
  status: string; completedAt?: string; startedAt?: string;
  candidates: Array<SurveyCarrier & { band: string }>;
  listeningTests: Array<{ frequencyHz: number; file: string; speechFile?: string; setting: { bandwidthHz: number; audioNoiseReduction: string } }>;
};

export function ShortwaveSurveyResults() {
  const controller = useRadioController(), radio = useSdrState();
  const [survey, setSurvey] = useState<Survey | null>(null);
  const [selectedHz, setSelectedHz] = useState<number | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const [refreshedAt, setRefreshedAt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    void fetch("/api/shortwave-survey", { cache: "no-store" }).then((r) => r.json()).then((value: Survey) => {
      if (!cancelled) setSurvey(value);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [refreshedAt]);
  if (!survey?.candidates.length) return null;

  const listen = async (frequencyHz: number) => {
    setBusy(true); setError(null);
    try {
      const candidate = survey.candidates.find((value) => value.frequencyHz === frequencyHz);
      controller.setScheme("AM"); controller.setBandwidth(candidate?.recommendedBandwidthHz ?? 6000);
      controller.setAutoSquelch(false); controller.setSquelch(0);
      controller.setAudioSpeechFilter(true); controller.setAudioNoiseReduction("low");
      // Keep the carrier away from the tuner's DC spur, even on an idle receiver.
      await controller.setFrequency(frequencyHz + 50_000);
      controller.setOffset(-50_000);
      setSelectedHz(frequencyHz);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  };
  const recordings = survey.listeningTests.filter((test) => test.frequencyHz === selectedHz);
  const selectedIndex = survey.candidates.findIndex((candidate) => candidate.frequencyHz === selectedHz);
  const stale = Date.now() - Date.parse(survey.completedAt ?? survey.startedAt ?? "") > 30 * 60_000;
  return (
    <section className="mb-3 rounded border border-line p-2" aria-label="Device shortwave survey">
      <h3 className="text-xs font-medium text-ink">Found by your RTL-SDR</h3>
      <div className="mt-2 flex flex-wrap gap-1" role="group" aria-label="Navigate surveyed signals">
        <button type="button" className="mode-chip" disabled={busy} onClick={() => void listen(survey.candidates[0].frequencyHz)}>Try best candidate</button>
        <button type="button" className="mode-chip" disabled={busy || selectedIndex <= 0}
          onClick={() => void listen(survey.candidates[selectedIndex - 1].frequencyHz)}>← Previous signal</button>
        <button type="button" className="mode-chip" disabled={busy || selectedIndex >= survey.candidates.length - 1}
          onClick={() => void listen(survey.candidates[selectedIndex + 1].frequencyHz)}>Next signal →</button>
        <button type="button" className="mode-chip" onClick={() => setRefreshedAt((value) => value + 1)}>Reload results</button>
      </div>
      <p className="mt-1 text-[0.65rem] text-muted">
        {survey.completedAt ? new Date(survey.completedAt).toLocaleString() : survey.status} · Stable across two tuner positions.
        These are RF candidates, not confirmed intelligible broadcasts.
      </p>
      {stale && <p className="mt-1 text-[0.65rem] text-muted">Older scan: reception may have changed. Reload retrieves the latest completed survey; it does not rescan.</p>}
      <div className="mt-2 flex flex-col gap-1">
        {survey.candidates.map((candidate) => (
          <button type="button" key={candidate.frequencyHz} disabled={busy}
            className={`mode-chip text-left ${selectedHz === candidate.frequencyHz ? "mode-chip-active" : ""}`}
            onClick={() => void listen(candidate.frequencyHz)}>
            {(candidate.frequencyHz / 1e6).toFixed(4)} MHz · {candidate.band}
            <span className="block text-[0.6rem] opacity-75">
              Carrier +{candidate.prominenceDb.toFixed(0)} dB · {candidate.sidebandExcessDb > 1.5 ? "sideband energy detected" : "little sideband energy"}
              {!nearBroadcastChannel(candidate.frequencyHz) && " · off 5 kHz grid; interference possible"}
              {candidate.recommendedBandwidthHz === 4000 && " · strong neighbor; narrow AM recommended"}
            </span>
          </button>
        ))}
      </div>
      <p className="mt-2 text-[0.65rem] text-muted">
        Select to tune AM with speech filtering and light noise reduction. Broadcast-grid candidates with sideband energy appear first; this is a heuristic, not station identification.
        {radio.status !== "playing" && " Connect RTL-SDR to listen live; recordings below work offline."}
      </p>
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
      {recordings.length > 0 && <details className="mt-2">
        <summary className="cursor-pointer text-[0.7rem]">Compare actual recordings</summary>
        <p className="mt-1 text-[0.6rem] text-muted">Bandwidth samples are successive live captures. Each speech-filter comparison uses exactly the same recorded audio.</p>
        {recordings.map((test) => <label key={test.file} className="mt-2 block text-[0.65rem] text-muted">
          {test.setting.bandwidthHz / 1000} kHz · NR {test.setting.audioNoiseReduction}
          <audio className="mt-1 w-full" controls preload="none" src={`/api/shortwave-survey?clip=${encodeURIComponent(test.file)}`} />
          {test.speechFile && <span className="mt-1 block">Same recording + speech filter
            <audio className="mt-1 w-full" controls preload="none" src={`/api/shortwave-survey?clip=${encodeURIComponent(test.speechFile)}`} />
          </span>}
        </label>)}
      </details>}
    </section>
  );
}
