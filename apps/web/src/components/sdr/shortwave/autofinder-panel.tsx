"use client";

import { useCallback, useState } from "react";
import { CleanerPanel } from "./cleaner-panel";
import { useAutofinderState, useRadioController, useSdrState } from "@/components/sdr/sdr-provider";

/** No channel programming: start, skip, stay, stop. The controller survives tab changes. */
export function AutofinderPanel() {
  const controller = useRadioController(), finder = useAutofinderState(), radio = useSdrState();
  const muteLocal = useCallback((muted: boolean) => controller.setBackendMonitorMuted(muted), [controller]);
  const [starting, setStarting] = useState(false), [error, setError] = useState<string | null>(null);
  const active = !["idle", "error"].includes(finder.status);
  const [cleaningError, setCleaningError] = useState<string | null>(null);
  const cleaningHz = controller.getSignalCleaningHz();
  const manualCleaning = cleaningHz !== null ? finder.cleaning : undefined;
  const cleaningBlocked = controller.signalCleaningUnavailable();
  const start = async () => {
    setStarting(true); setError(null);
    try { await controller.startAutofinder(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setStarting(false); }
  };
  return <section aria-label="Autonomous signal finder">
    <p className="text-[0.65rem] text-muted">No lists, ranges, or squelch setup. Learns productive bands, avoids rejected signals, and searches again when speech fades.</p>
    <div className="mt-3 flex flex-wrap gap-1">
      {!active ? <button type="button" className="mode-chip mode-chip-active" disabled={starting}
        onClick={() => void start()}>{starting ? "Connecting…" : "Find speech automatically"}</button> : <>
        <button type="button" className="mode-chip" disabled={!finder.frequencyHz || finder.status === "stopping"} onClick={() => controller.skipAutofinder()}>Skip signal</button>
        {finder.status === "listening" && <button type="button" className="mode-chip" onClick={() => controller.keepAutofinder()}>Stay here</button>}
        {finder.status === "keeping" && <button type="button" className="mode-chip" onClick={() => controller.resumeAutofinder()}>Resume automatic search</button>}
        <button type="button" className="mode-chip" disabled={finder.status === "stopping"} onClick={() => { void controller.stopAutofinder(); }}>Stop &amp; restore dial</button>
      </>}
    </div>
    <p className="mt-2 text-xs text-ink" role="status" aria-live="polite">{finder.message}</p>
    {finder.frequencyHz !== null && <p className="mt-1 text-xs text-ink">{(finder.frequencyHz / 1e6).toFixed(4)} MHz · AM
      {finder.speechRatio !== null && ` · ${(finder.speechRatio * 100).toFixed(0)}% speech activity`}</p>}
    {(active || finder.tilesScanned > 0) && <p className="mt-1 text-[0.65rem] text-muted">Pass {finder.round} · {finder.tilesScanned} spectrum tiles · {finder.candidatesChecked} audio checks</p>}
    {(error || finder.status === "error") && <p role="alert" className="mt-2 text-xs text-danger">{error ?? finder.message}</p>}
    {radio.connection === "demo" && <p className="mt-2 text-xs text-danger">Demo source: any detection is simulated, not a received station.</p>}
    <div className="mt-3 rounded border border-line p-2">
      <h4 className="text-xs font-medium text-ink">Clean what I&apos;m hearing</h4>
      <div className="mt-2 flex flex-wrap gap-1">
        {cleaningHz === null ? <button type="button" className="mode-chip" disabled={Boolean(cleaningBlocked)} title={cleaningBlocked ?? undefined}
          onClick={() => {
            setCleaningError(null);
            try { controller.startSignalCleaning(); }
            catch (cause) { setCleaningError(cause instanceof Error ? cause.message : String(cause)); }
          }}>Clean this signal on server</button>
          : <button type="button" className="mode-chip mode-chip-active" aria-pressed="true"
            onClick={() => controller.stopSignalCleaning()}>Stop server cleaning</button>}
      </div>
      <p className="mt-1 text-[0.65rem] text-muted" aria-live="polite">
        {cleaningHz === null
          ? cleaningBlocked ?? "Cleans the tuned AM signal on the server, using the LAN worker when available. Retuning stops it."
          : manualCleaning?.status === "error" ? manualCleaning.message
          : manualCleaning?.status === "optimizing" ? `Cleaning ${(cleaningHz / 1e6).toFixed(4)} MHz · details below.`
          : "Starting server cleaning…"}
      </p>
      {cleaningError && <p role="alert" className="mt-1 text-xs text-danger">{cleaningError}</p>}
    </div>
    <CleanerPanel cleaning={finder.cleaning} muteLocal={muteLocal} />
    <p className="mt-2 text-[0.6rem] text-muted">Local neural speech detection—not proof of intelligibility or station identity. Scouting is muted. Keeps running while this receiver page stays open, even when you switch panels. Manual tuning takes control.</p>
    {finder.recent.length > 0 && <details className="mt-2 text-[0.65rem] text-muted"><summary className="cursor-pointer">Recent AI checks</summary>
      {finder.recent.map((hit, index) => <p key={`${hit.at}-${index}`} className="mt-1">{(hit.frequencyHz / 1e6).toFixed(4)} MHz · {hit.accepted ? "speech in two samples" : "not sustained speech"} · {(hit.speechRatio * 100).toFixed(0)}%</p>)}
    </details>}
  </section>;
}
