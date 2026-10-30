"use client";

import { useEffect, useRef, useState } from "react";
import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";
import { KiwiDiversity, type DiversityMonitor, type DiversityStatus, type SpectrumOverlay } from "@/lib/sdr/kiwi/diversity";

function spectrumPath(values: (number | null)[]) {
  let pen = false;
  return values.map((v, i) => {
    if (v === null) { pen = false; return ""; }
    const command = `${pen ? "L" : "M"}${(i / Math.max(1, values.length - 1) * 320).toFixed(1)},${(85 - (v + 10) * 1.25).toFixed(1)}`;
    pen = true;
    return command;
  }).join(" ");
}

export function KiwiDiversityPanel() {
  const controller = useRadioController();
  const radio = useSdrState();
  const client = useRef<KiwiDiversity | null>(null);
  const [address, setAddress] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<DiversityStatus>({ state: "off" });
  const [overlay, setOverlay] = useState<SpectrumOverlay>({ local: [], kiwi: [], spanHz: 16000 });
  const [monitor, setMonitor] = useState<DiversityMonitor>("off");
  useEffect(() => {
    const instance = new KiwiDiversity(controller, (next) => {
      setStatus(next);
      if (next.state === "off" || next.state === "error") setMonitor("off");
    }, setOverlay);
    client.current = instance;
    return () => { client.current = null; instance.disconnect(); };
  }, [controller]);
  const hz = radio.frequencyHz + radio.offsetHz;
  const available = radio.status === "playing" && hz >= 0 && hz <= 30000000 && radio.connection !== "demo";
  const connected = status.state !== "off" && status.state !== "error";
  const selectMonitor = (mode: DiversityMonitor) => {
    client.current?.setMonitor(mode);
    setMonitor(mode);
  };
  return <section className="my-3 rounded border border-line p-2" aria-label="KiwiSDR diversity reception">
    <h4 className="text-xs font-medium text-ink">KiwiSDR reference reception</h4>
    <p className="mt-1 text-[0.65rem] leading-relaxed text-muted">
      Connect a nearby Kiwi alongside your receiver. It follows your frequency and mode; matching audio guides removal of local-only interference, without mixing in the remote audio.
    </p>
    <p className="mt-1 text-[0.65rem] text-muted">
      Choose a nearby receiver from the <a className="underline" href="https://kiwisdr.com/public/" target="_blank" rel="noreferrer">public KiwiSDR directory</a>, then paste its address.
    </p>
    <div className="mt-2 flex flex-wrap gap-1">
      <input aria-label="Reference KiwiSDR address" className="min-w-0 flex-1 rounded border border-line bg-transparent px-2 py-1 text-xs" placeholder="http://nearby-kiwi:8073" value={address} disabled={connected} onChange={(event) => setAddress(event.target.value)} />
      <input aria-label="Reference KiwiSDR password (optional)" className="w-28 rounded border border-line bg-transparent px-2 py-1 text-xs" type="password" autoComplete="off" placeholder="Password (opt.)" value={password} disabled={connected} onChange={(event) => setPassword(event.target.value)} />
      <button type="button" className="rounded border border-line px-2 py-1 text-xs disabled:opacity-40" disabled={!connected && (!available || !address.trim())} onClick={() => {
        if (connected) { client.current?.disconnect(); return; }
        void client.current?.connect(address, password).catch((error: unknown) => setStatus({ state: "error", message: error instanceof Error ? error.message : String(error) }));
      }}>{connected ? "Disconnect Kiwi" : "Connect reference"}</button>
    </div>
    {!available && <p className="mt-1 text-[0.65rem] text-muted">Start your real receiver below 30 MHz first. This does not replace your local SDR connection.</p>}
    <p className="mt-2 text-[0.65rem] text-muted" role="status" aria-live="polite">
      {status.receiverName && `${status.receiverName} · `}
      {status.message ?? (status.state === "connecting" ? "Connecting to the reference Kiwi…" : "Reference disconnected.")}
    </p>
    {connected && <>
      <svg viewBox="0 0 320 100" className="mt-2 w-full rounded bg-black/25" role="img" aria-label="Local and Kiwi RF spectra aligned by frequency, relative to each receiver's noise floor">
        <path d="M160,5 V88 M0,85 H320" stroke="currentColor" opacity=".2" />
        <path d={spectrumPath(overlay.local)} fill="none" stroke="#38bdf8" strokeWidth="1.5" />
        <path d={spectrumPath(overlay.kiwi)} fill="none" stroke="#fbbf24" strokeWidth="1.2" opacity=".85" />
        <text x="2" y="98" fontSize="8" fill="currentColor">−{(overlay.spanHz / 2000).toFixed(0)} kHz</text>
        <text x="160" y="98" textAnchor="middle" fontSize="8" fill="currentColor">{(hz / 1000).toFixed(2)} kHz</text>
        <text x="318" y="98" textAnchor="end" fontSize="8" fill="currentColor">+{(overlay.spanHz / 2000).toFixed(0)} kHz</text>
      </svg>
      <p className="text-[0.6rem] text-muted"><span style={{ color: "#38bdf8" }}>Local</span> / <span style={{ color: "#fbbf24" }}>Kiwi</span> · relative RF levels, independently normalized (not calibrated power).</p>
      <p className="mt-1 text-[0.65rem] text-muted">
        Match: {Math.round((status.matchConfidence ?? 0) * 100)}%{status.state === "matched" && ` · alignment ${status.alignmentMs} ms · drift ${status.clockDriftPpm} ppm · mean spectral attenuation ${status.attenuationDb} dB`}
      </p>
      <div className="mt-2 flex flex-wrap gap-1" role="group" aria-label="Diversity audio comparison">
        {([["off", "Normal audio"], ["raw", "Delayed original"], ["clean", "Kiwi-guided cleanup"]] as const).map(([mode, label]) => <button type="button" key={mode} aria-pressed={monitor === mode} className={`rounded border px-2 py-1 text-[0.65rem] ${monitor === mode ? "border-sky-400 text-sky-300" : "border-line"}`} onClick={() => selectMonitor(mode)}>{label}</button>)}
      </div>
      <p className="mt-1 text-[0.6rem] leading-relaxed text-muted">Comparison adds about 2 seconds of alignment delay. No stable match means unchanged local audio. A second receiver cannot recover information neither receiver captured. Turn other audio monitors off while comparing.</p>
    </>}
  </section>;
}
