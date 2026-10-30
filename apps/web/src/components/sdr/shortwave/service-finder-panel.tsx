"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";
import type { ServiceAction, ServiceView, SignalFinding } from "@/lib/sdr/autofinder/service-types";
import { CleanerPanel } from "./cleaner-panel";

const date = (at: string) => at ? new Date(at).toLocaleString() : "Never";
const frequency = (hz: number) => `${(hz / 1e6).toFixed(4)} MHz`;

/** A view of the unattended receiver, independent of this browser's local finder. */
export function ServiceFinderPanel() {
  const controller = useRadioController(), radio = useSdrState();
  const [view, setView] = useState<ServiceView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const mounted = useRef(false);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const response = await fetch("/api/autofinder-service", { cache: "no-store", signal });
    if (!response.ok) throw new Error("Unable to load background finder results.");
    const next = await response.json() as ServiceView;
    if (mounted.current) { setView(next); setRefreshError(null); }
  }, []);
  useEffect(() => {
    mounted.current = true;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { await refresh(abort.signal); }
      catch { if (!abort.signal.aborted) setRefreshError("Unable to refresh background results. Retrying…"); }
      if (!abort.signal.aborted) timer = setTimeout(poll, 3000);
    };
    void poll();
    return () => { mounted.current = false; abort.abort(); clearTimeout(timer); };
  }, [refresh]);

  const control = async (action: ServiceAction) => {
    const response = await fetch("/api/autofinder-service", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }), signal: AbortSignal.timeout(45_000),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error ?? "Background finder command failed.");
    if (mounted.current) setView(result as ServiceView);
  };
  const run = async (work: () => Promise<void>) => {
    setBusy(true); setError(null);
    try { await work(); }
    catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (mounted.current) setBusy(false); }
  };
  const listen = async (finding: SignalFinding) => {
    // Acknowledge device release before attempting a new single-client bridge connection.
    if (view?.online) await control("release");
    await controller.stopAutofinder(false);
    if (controller.getState().status !== "playing" || !["device", "rtltcp"].includes(controller.getState().connection)) {
      await controller.connectRtlServer(view?.bridgeUrl ?? `http://${location.hostname}:8081`);
    }
    if (controller.getState().status !== "playing") throw new Error(controller.getState().error ?? "Could not connect to the receiver.");
    controller.setScheme("AM"); controller.setBandwidth(finding.bandwidthHz);
    controller.setAutoSquelch(false); controller.setSquelch(0); controller.setAfc(false);
    controller.setAudioSpeechFilter(true); controller.setAudioNoiseReduction("low");
    await controller.setFrequency(finding.frequencyHz + 50_000); controller.setOffset(-50_000);
    controller.startSignalCleaning(finding.frequencyHz);
  };

  const finder = view?.finder;
  const online = Boolean(view?.online && !view.stale);
  const findings = showAll ? view?.findings : view?.findings.slice(0, 8);
  const label = !view ? "Loading…" : !online ? "Offline" : view.status === "scanning" ? finder?.status === "listening" ? "Speech detected" : "Searching" : view.status;
  return <section className="mb-3 rounded border border-line p-3" aria-label="Background shortwave finder">
    <div className="flex items-center justify-between gap-2">
      <h3 className="text-sm font-medium text-ink">Background shortwave finder</h3>
      <span className="text-[0.65rem] capitalize text-muted" role="status">{label}</span>
    </div>
    <p className="mt-1 text-[0.65rem] text-muted">Finds speech on your receiver across 2.3–30 MHz and saves results while your browser is closed.</p>
    <div className="mt-2 flex flex-wrap gap-1">
      {view?.enabled ? <button type="button" className="mode-chip" disabled={busy || !online} onClick={() => void run(() => control("pause"))}>Pause background search</button>
        : <button type="button" className="mode-chip mode-chip-active" disabled={busy || !online} onClick={() => void run(() => control("resume"))}>Resume background search</button>}
      <button type="button" className="mode-chip" disabled={busy || !online || !finder?.frequencyHz || view?.status !== "scanning"} onClick={() => void run(() => control("skip"))}>Skip background signal</button>
      <button type="button" className="mode-chip" disabled={busy} onClick={() => void run(() => refresh())}>Refresh findings</button>
    </div>
    {online && <p className="mt-2 text-xs text-ink">{view?.message}</p>}
    {online && view?.status === "scanning" && finder && <p className="mt-1 text-[0.65rem] text-muted">
      {finder.band ?? "HF"}{finder.frequencyHz ? ` · ${frequency(finder.frequencyHz)}` : ""}
      {finder.speechRatio !== null ? ` · ${(finder.speechRatio * 100).toFixed(0)}% speech activity` : ""}
      {` · Pass ${finder.round} · ${finder.tilesScanned} tiles · ${finder.candidatesChecked} checks`}
    </p>}
    {view && !online && <p className="mt-2 text-xs text-muted">Background finder is offline. Saved findings are still available. Start the background finder service on the receiver host to resume scanning.</p>}
    {view?.stale && view.at && <p className="mt-1 text-xs text-danger">Updates stopped; the scan status may be out of date.</p>}
    {online && view?.status === "error" && <p role="alert" className="mt-1 text-xs text-danger">{view.message}</p>}
    <p className="mt-2 text-[0.65rem] text-muted">Last update: {date(view?.at ?? "")}</p>
    <CleanerPanel cleaning={online && view?.status === "scanning" ? finder?.cleaning : undefined} />
    <h4 className="mt-3 text-xs font-medium text-ink">Detected speech {view ? `(${view.findings.length})` : ""}</h4>
    {view && !view.findings.length && <p className="mt-1 text-xs text-muted">No sustained speech found yet. Confirmed detections will appear here automatically.</p>}
    <div className="mt-2 flex flex-col gap-2">
      {findings?.map((finding) => <div key={finding.frequencyHz} className="rounded border border-line p-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-ink">{frequency(finding.frequencyHz)} · {finding.band}</span>
          <button type="button" className="mode-chip" disabled={busy || radio.status === "starting"} onClick={() => void run(() => listen(finding))}>Listen live</button>
        </div>
        <p className="mt-1 text-[0.65rem] text-muted">{(finding.speechRatio * 100).toFixed(0)}% speech activity · {finding.confirmations} confirmed {finding.confirmations === 1 ? "check" : "checks"} · AM {finding.bandwidthHz / 1000} kHz</p>
        <p className="mt-1 text-[0.65rem] text-muted">Last heard {date(finding.lastSeen)}{Date.now() - Date.parse(finding.lastSeen) > 30 * 60_000 ? " · Older finding; reception may have changed" : ""}</p>
        {finding.cleaning?.metrics && <p className="mt-1 text-[0.65rem] text-muted">Last cleanup: {finding.cleaning.profile}{finding.cleaning.metrics.quietMeasured ? ` · ${finding.cleaning.metrics.noiseReductionDb.toFixed(1)} dB quiet-interval reduction` : ""}</p>}
      </div>)}
    </div>
    {view && view.findings.length > 8 && <button type="button" className="mode-chip mt-2" onClick={() => setShowAll(!showAll)}>{showAll ? "Show fewer" : `Show all ${view.findings.length} signals`}</button>}
    {Boolean(view?.checks.length) && <details className="mt-3 text-[0.65rem] text-muted"><summary className="cursor-pointer">Recent signal checks</summary>
      {view?.checks.slice(0, 20).map((check) => <p className="mt-1" key={check.id}>{frequency(check.frequencyHz)} · {check.accepted ? "Speech in two samples" : "No sustained speech"} · {(check.speechRatio * 100).toFixed(0)}% · {date(check.at)}</p>)}
    </details>}
    <p className="mt-2 text-[0.6rem] text-muted">Speech activity does not establish intelligibility or station identity. Listen live takes over the receiver; background scanning resumes when you disconnect.</p>
    {(error || refreshError) && <p role="alert" className="mt-2 text-xs text-danger">{error ?? refreshError}</p>}
  </section>;
}
