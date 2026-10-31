"use client";

import { useEffect, useRef, useState } from "react";
import { DEFAULT_API_BASE_URL, apiBaseOverride, wsUrl } from "@/lib/api";
import type { CleanerState } from "@/lib/sdr/autofinder/cleaner";

/** Backend PCM monitor, with an aligned original/processed A/B and no second SDR connection. */
export function CleanerPanel({ cleaning, muteLocal }: { cleaning?: CleanerState; muteLocal?: (muted: boolean) => void }) {
  const [mode, setMode] = useState<"off" | "clean" | "raw">("off");
  const [error, setError] = useState<string | null>(null);
  const [volume, setVolume] = useState(.8);
  const [receiving, setReceiving] = useState(false);
  const [retry, setRetry] = useState(0);
  const [worker, setWorker] = useState<{ configured: boolean; ready: boolean; engine?: string; device?: string; accelerated?: boolean; gpuName?: string; message?: string } | null>(null);
  const context = useRef<AudioContext | null>(null);
  const gain = useRef<GainNode | null>(null);
  const nodes = useRef(new Set<AudioBufferSourceNode>());
  const active = cleaning?.status === "optimizing";
  const selected = (next: typeof mode) => {
    setError(null);
    if (next !== "off") {
      try {
        context.current ??= new AudioContext();
        if (!gain.current) {
          gain.current = context.current.createGain();
          gain.current.connect(context.current.destination);
        }
        gain.current.gain.value = volume;
        void context.current.resume().catch(() => setError("Your browser could not start audio playback."));
      } catch (cause) { setError(String(cause)); return; }
    }
    setMode(next);
  };
  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const base = apiBaseOverride() ?? DEFAULT_API_BASE_URL;
        const response = await fetch(`${base}/autofinder/cleaner/worker`, { cache: "no-store", signal: abort.signal });
        if (response.ok && !abort.signal.aborted) setWorker(await response.json());
      } catch { /* The stream's status still reports its own actual processing path. */ }
      if (!abort.signal.aborted) timer = setTimeout(() => { void poll(); }, 10000);
    };
    void poll();
    return () => { abort.abort(); clearTimeout(timer); };
  }, []);
  useEffect(() => {
    if (gain.current) gain.current.gain.value = volume;
  }, [volume]);
  useEffect(() => {
    muteLocal?.(mode !== "off" && active);
    return () => muteLocal?.(false);
  }, [mode, active, muteLocal]);
  useEffect(() => () => { void context.current?.close(); }, []);
  useEffect(() => {
    if (mode === "off" || !active || !cleaning?.streamId) return;
    let cancelled = false, nextTime = 0;
    let reconnect: ReturnType<typeof setTimeout> | undefined;
    const socket = new WebSocket(wsUrl(`/autofinder/cleaner/listen/${encodeURIComponent(cleaning.streamId)}?stage=${mode}`));
    socket.binaryType = "arraybuffer";
    socket.onmessage = (event) => {
      if (cancelled) return;
      if (typeof event.data === "string") {
        const state = JSON.parse(event.data);
        if (state.status === "error") setError(state.message ?? "Backend cleanup failed.");
        if (state.status === "stopped") { socket.close(); setReceiving(false); }
        return;
      }
      const ctx = context.current, output = gain.current;
      if (!ctx || !output || ctx.state === "closed") return;
      const bytes = new DataView(event.data as ArrayBuffer);
      const samples = new Float32Array(bytes.byteLength / 2);
      for (let i = 0; i < samples.length; i++) samples[i] = bytes.getInt16(i * 2, true) / 32768;
      const buffer = ctx.createBuffer(1, samples.length, 16000);
      buffer.copyToChannel(samples, 0);
      const node = ctx.createBufferSource(); node.buffer = buffer; node.connect(output);
      nodes.current.add(node); node.onended = () => { nodes.current.delete(node); node.disconnect(); };
      if (nextTime < ctx.currentTime || nextTime - ctx.currentTime > .5) {
        // Drop stale scheduled audio rather than accumulating latency after a network stall.
        for (const old of nodes.current) if (old !== node) old.stop();
        nextTime = ctx.currentTime + .08;
      }
      node.start(nextTime); nextTime += buffer.duration; setReceiving(true); setError(null);
    };
    socket.onerror = () => { if (!cancelled) setError("Cannot connect to the cleaned audio stream."); };
    socket.onclose = () => { if (!cancelled) { setReceiving(false); reconnect = setTimeout(() => setRetry((value) => value + 1), 2000); } };
    return () => {
      cancelled = true; socket.close();
      clearTimeout(reconnect);
      for (const node of nodes.current) node.stop();
      nodes.current.clear(); setReceiving(false);
    };
  }, [mode, active, cleaning?.streamId, retry]);

  const metrics = cleaning?.metrics;
  const processing = active ? cleaning?.processing : undefined;
  return <section className="mt-3 rounded border border-line p-2" aria-label="Adaptive signal cleaning">
    <h4 className="text-xs font-medium text-ink">Adaptive signal cleaning</h4>
    <p className="mt-1 text-[0.65rem] text-muted">
      {!cleaning || cleaning.status === "stopped" ? "Starts automatically when the finder confirms speech, or on demand with Clean this signal on server." : cleaning.status === "starting" ? "Starting backend noise suppression…" : cleaning.status === "error" ? cleaning.message : `${cleaning.profile ?? "Natural"} · ${cleaning.iterations ?? 0} optimization passes`}
    </p>
    {cleaning?.neuralAvailable === false && <p className="mt-1 text-[0.65rem] text-danger">Neural cleanup unavailable: {cleaning.warning}. Spectral cleanup remains active.</p>}
    {processing && <p className="mt-1 text-[0.65rem] text-muted" aria-label="Active processing location">
      {processing.location === "lan" ? `LAN ${processing.accelerated ? "GPU" : "CPU"}` : processing.location === "spectral" ? "Local spectral fallback" : "Local CPU"}
      {` · ${processing.engine ?? "Unknown engine"}`}{processing.gpuName ? ` · ${processing.gpuName}` : ""}
      {processing.roundTripMs != null ? ` · ${processing.roundTripMs.toFixed(0)} ms round trip` : ""}
      {processing.fallback ? " · LAN fallback active" : ""}
    </p>}
    {processing?.warning && <p className="mt-1 text-[0.65rem] text-danger">{processing.warning}</p>}
    {!processing && worker && <p className="mt-1 text-[0.65rem] text-muted" aria-label="LAN processing worker status">
      {!worker.configured ? "Processing: local CPU · LAN worker not configured" : worker.ready
        ? `LAN ${worker.accelerated ? "GPU" : "CPU"} worker ready · ${worker.engine ?? "Enhancement"}${worker.gpuName ? ` · ${worker.gpuName}` : ""}`
        : `LAN worker offline · ${worker.message ?? "Local fallback available"}`}
    </p>}
    {metrics && <p className="mt-1 text-[0.65rem] text-muted">{metrics.quietMeasured ? `${metrics.noiseReductionDb.toFixed(1)} dB measured quiet-interval noise reduction` : "Waiting for a quiet interval to measure noise"} · {(metrics.speechRetention * 100).toFixed(0)}% speech-spectrum similarity</p>}
    {Boolean(cleaning?.notchesHz?.length) && <p className="mt-1 text-[0.65rem] text-muted">Whistle filters: {cleaning?.notchesHz?.map((hz) => `${hz.toFixed(0)} Hz`).join(", ")}</p>}
    <div className="mt-2 flex flex-wrap gap-1">
      <button type="button" className={`mode-chip ${mode === "clean" ? "mode-chip-active" : ""}`} disabled={!active && mode === "off"} onClick={() => selected("clean")}>Listen cleaned</button>
      <button type="button" className={`mode-chip ${mode === "raw" ? "mode-chip-active" : ""}`} disabled={!active && mode === "off"} onClick={() => selected("raw")}>Compare original</button>
      {mode !== "off" && <button type="button" className="mode-chip" onClick={() => selected("off")}>Stop monitor</button>}
    </div>
    {mode !== "off" && <label className="mt-2 flex items-center gap-2 text-[0.65rem] text-muted">Monitor volume
      <input aria-label="Cleaned monitor volume" type="range" min="0" max="1" step="0.05" value={volume} onChange={(event) => setVolume(Number(event.target.value))} />
      <span>{receiving ? mode === "raw" ? "Original" : "Cleaned" : "Waiting for speech…"}</span>
    </label>}
    <p className="mt-1 text-[0.6rem] text-muted">Same received signal, processed on the server. Filters are rechecked continuously to preserve speech as reception changes.</p>
    {error && <p role="alert" className="mt-1 text-xs text-danger">{error}</p>}
  </section>;
}
