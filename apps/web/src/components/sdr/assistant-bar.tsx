"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useRadioController } from "@/components/sdr/sdr-provider";
import { createReceiverCommandExecutor, getLatestReceiverSpectrum } from "@/lib/sdr/receiver-control/browser-client";
import { speak, stopSpeaking, useVoice, voiceSupported } from "@/components/sdr/use-voice";
import { uiClick, uiKey, uiRead, uiSet, uiSnapshot } from "@/lib/sdr/assistant/ui-driver";
import { LOCAL_MODELS, loadLocalModel, localLlmSupported, llamaStep, localStep } from "@/lib/sdr/assistant/local-llm";
import type { AssistantStep, Block, Message } from "@/lib/sdr/assistant/types";
import type { ReceiverAction } from "@/lib/sdr/receiver-control/protocol";

type LogLine = { id: number; kind: "user" | "assistant" | "action" | "error" | "progress"; text: string };

const MAX_STEPS = 12;
const BACKEND_KEY = "sigsense.assistant.backend";
const COLLAPSED_KEY = "sigsense.assistant.collapsed";

type Backend = "local" | "cloud" | "llama";

const cloudStep: AssistantStep = async (messages) => {
  const response = await fetch("/api/assistant", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messages }),
  });
  const data = await response.json().catch(() => null) as { content?: Block[]; error?: string } | null;
  if (!response.ok || !data?.content) throw new Error(data?.error ?? `Assistant error ${response.status}`);
  return data.content;
};

function storedBackend(): { backend: Backend; model: string } {
  const fallback = { backend: (localLlmSupported() ? "local" : "llama") as Backend, model: LOCAL_MODELS[0].id as string };
  try {
    const saved = JSON.parse(localStorage.getItem(BACKEND_KEY) ?? "null") as { backend?: string; model?: string } | null;
    if (saved?.backend === "cloud" || saved?.backend === "local" || saved?.backend === "llama") {
      return { backend: saved.backend, model: LOCAL_MODELS.some((m) => m.id === saved.model) ? saved.model! : fallback.model };
    }
  } catch { /* storage unavailable */ }
  return fallback;
}
const MAX_RESULT_CHARS = 6_000;
const SUGGESTIONS = [
  "Start the demo",
  "Tune to 7.2 MHz LSB",
  "What shortwave is on air now?",
  "Find the strongest signal",
  "Start the auto finder",
  "Quiet the noise",
];

type Finding = { frequencyHz: number; bandwidthHz: number; band: string; lastSeen: string; speechRatio: number; confirmations: number };

/** Findings already banked by the background finder service, best first. */
async function foundSignals() {
  const response = await fetch("/api/autofinder-service", { cache: "no-store" });
  if (!response.ok) throw new Error("Background finder results are unavailable");
  const view = await response.json() as { findings?: Finding[]; online?: boolean };
  const findings = [...(view.findings ?? [])]
    .sort((a, b) => b.confirmations - a.confirmations || b.speechRatio - a.speechRatio || Date.parse(b.lastSeen) - Date.parse(a.lastSeen))
    .slice(0, 8)
    .map((f) => ({ frequencyHz: f.frequencyHz, band: f.band, bandwidthHz: f.bandwidthHz, speechRatio: Number(f.speechRatio.toFixed(2)), confirmations: f.confirmations, lastSeen: f.lastSeen }));
  return { serviceOnline: !!view.online, count: view.findings?.length ?? 0, findings };
}

/** Mirrors the background-finder panel's Listen button. */
async function listenToFinding(controller: ReturnType<typeof useRadioController>, frequencyHz: unknown) {
  if (typeof frequencyHz !== "number") throw new Error("frequencyHz is required");
  const response = await fetch("/api/autofinder-service", { cache: "no-store" });
  const view = await response.json() as { findings?: Finding[]; online?: boolean; bridgeUrl?: string };
  const finding = view.findings?.find((f) => f.frequencyHz === frequencyHz);
  if (!finding) throw new Error("No such finding; call get_found_signals first");
  if (view.online) {
    await fetch("/api/autofinder-service", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "release" }) });
  }
  await controller.stopAutofinder(false);
  const state = () => controller.getState();
  if (state().status !== "playing" || !["device", "rtltcp"].includes(state().connection)) {
    await controller.connectRtlServer(view.bridgeUrl ?? `http://${location.hostname}:8081`);
  }
  if (state().status !== "playing") throw new Error(state().error ?? "Could not connect to the receiver.");
  controller.setScheme("AM"); controller.setBandwidth(finding.bandwidthHz);
  controller.setAutoSquelch(false); controller.setSquelch(0); controller.setAfc(false);
  controller.setAudioSpeechFilter(true); controller.setAudioNoiseReduction("low");
  await controller.setFrequency(finding.frequencyHz + 50_000); controller.setOffset(-50_000);
  controller.startSignalCleaning(finding.frequencyHz);
  return { listening: true, frequencyHz: finding.frequencyHz, band: finding.band };
}

function describe(name: string, input: Record<string, unknown>): string {
  const args = Object.entries(input).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ");
  return args ? `${name}(${args})` : name;
}

/** Plain-language control for the whole receiver: type what you want, the assistant drives the controls. */
export function AssistantBar() {
  const controller = useRadioController();
  const [value, setValue] = useState("");
  const [log, setLog] = useState<LogLine[]>([]);
  const [busy, setBusy] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [speakReplies, setSpeakReplies] = useState(true);
  const speakRepliesRef = useRef(true);
  speakRepliesRef.current = speakReplies;
  const runRef = useRef<(text: string) => Promise<void>>(async () => {});
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(COLLAPSED_KEY) === "1"; } catch { return false; }
  });
  const setCollapsedPersist = useCallback((next: boolean) => {
    setCollapsed(next);
    if (next) setOpen(false);
    try { localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0"); } catch { /* storage unavailable */ }
  }, []);
  const [{ backend, model }, setChoice] = useState(storedBackend);
  const choose = (next: { backend: Backend; model: string }) => {
    setChoice(next);
    try { localStorage.setItem(BACKEND_KEY, JSON.stringify(next)); } catch { /* storage unavailable */ }
  };
  const history = useRef<Message[]>([]);
  const lineId = useRef(0);
  const input = useRef<HTMLInputElement>(null);
  const logEnd = useRef<HTMLDivElement>(null);
  const execute = useRef<ReturnType<typeof createReceiverCommandExecutor> | null>(null);

  const push = useCallback((kind: LogLine["kind"], text: string) => {
    setLog((prev) => [...prev.slice(-60), { id: lineId.current++, kind, text }]);
  }, []);

  // "/" or Ctrl/⌘+K from anywhere focuses the bar.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const el = event.target as HTMLElement | null;
      const typing = !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
      if ((event.key === "k" && (event.ctrlKey || event.metaKey)) || (event.key === "/" && !typing)) {
        event.preventDefault();
        setCollapsedPersist(false);
        setOpen(true);
        setTimeout(() => input.current?.focus(), 0);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setCollapsedPersist]);

  useEffect(() => { logEnd.current?.scrollIntoView({ block: "end" }); }, [log, busy]);

  const run = useCallback(async (text: string) => {
    const prompt = text.trim();
    if (!prompt || busy) return;
    execute.current ??= createReceiverCommandExecutor(controller, getLatestReceiverSpectrum);
    stopSpeaking();
    setBusy(true);
    setOpen(true);
    setValue("");
    let lastReply = "";
    push("user", prompt);
    const messages: Message[] = [...history.current, { role: "user", content: prompt }];
    try {
      let modelStep: AssistantStep = backend === "llama" ? llamaStep : cloudStep;
      if (backend === "local") {
        if (!localLlmSupported()) throw new Error(window.isSecureContext
          ? "This browser has no WebGPU, so the on-device model can't run. Use desktop Chrome/Edge, or switch to Cloud."
          : `WebGPU is disabled because this page is on plain HTTP (${location.origin}). In Chrome/Edge open chrome://flags/#unsafely-treat-insecure-origin-as-secure, add ${location.origin}, enable it and relaunch. Or open the app via http://localhost.`);
        let shown = "";
        await loadLocalModel(model, (text) => {
          if (text === shown) return;
          shown = text;
          setLog((prev) => [...prev.filter((l) => l.kind !== "progress"), { id: -1, kind: "progress", text }]);
        });
        setLog((prev) => prev.filter((l) => l.kind !== "progress"));
        modelStep = localStep;
      }
      for (let step = 0; step < MAX_STEPS; step++) {
        const content = await modelStep(messages);
        messages.push({ role: "assistant", content });
        const reply = content.filter((b): b is Extract<Block, { type: "text" }> => b.type === "text").map((b) => b.text).join("\n").trim();
        if (reply) { push("assistant", reply); lastReply = reply; }
        const calls = content.filter((b): b is Extract<Block, { type: "tool_use" }> => b.type === "tool_use");
        if (calls.length === 0) break;
        const results: Block[] = [];
        for (const call of calls) {
          push("action", describe(call.name, call.input));
          try {
            const result = call.name === "get_found_signals" ? await foundSignals()
              : call.name === "listen_to_found_signal" ? await listenToFinding(controller, call.input.frequencyHz)
              : call.name === "ui_snapshot" ? uiSnapshot(call.input.filter)
              : call.name === "ui_click" ? uiClick(call.input.id, call.input.confirm)
              : call.name === "ui_set" ? uiSet(call.input.id, call.input.value)
              : call.name === "ui_key" ? uiKey(call.input)
              : call.name === "ui_read" ? uiRead(call.input.filter) : await execute.current({ type: "command", id: call.id, action: call.name as ReceiverAction, args: call.input });
            results.push({ type: "tool_result", tool_use_id: call.id, content: (JSON.stringify(result ?? null) ?? "null").slice(0, MAX_RESULT_CHARS) });
          } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            push("error", message);
            results.push({ type: "tool_result", tool_use_id: call.id, content: message, is_error: true });
          }
        }
        messages.push({ role: "user", content: results });
        if (step === MAX_STEPS - 1) push("error", "Stopped after too many steps.");
      }
      if (speakRepliesRef.current && lastReply) {
        setSpeaking(true);
        speak(lastReply, () => setSpeaking(false));
      }
      history.current = messages.slice(-40);
      // A trimmed history must still start on a plain user turn, or the API rejects it.
      while (history.current.length && !(history.current[0].role === "user" && typeof history.current[0].content === "string")) history.current.shift();
    } catch (error) {
      push("error", error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
      input.current?.focus();
    }
  }, [busy, controller, push, backend, model]);
  runRef.current = run;

  const voice = useVoice({
    paused: busy || speaking,
    onInterim: setValue,
    onFinal: (text) => { if (text) void runRef.current(text); },
    onError: (message) => { setOpen(true); push("error", message); },
  });

  if (collapsed) {
    return (
      <div className="assistant-bar assistant-bar-collapsed">
        <button type="button" className="mode-chip" onClick={() => setCollapsedPersist(false)} title="Show assistant ( / )">
          ✦ Assistant
        </button>
      </div>
    );
  }

  return (
    <div className="assistant-bar" data-open={open ? "true" : "false"}>
      <form
        className="assistant-form"
        onSubmit={(event) => { event.preventDefault(); void run(value); }}
      >
        <span className="assistant-spark" aria-hidden="true">✦</span>
        <input
          ref={input}
          className="assistant-input"
          value={value}
          disabled={busy}
          placeholder="Tell SigSense what to do — “tune to 7.2 MHz”, “what’s on air now?”"
          aria-label="Ask the assistant"
          autoComplete="off"
          onFocus={() => setOpen(true)}
          onKeyDown={(event) => { if (event.key === "Escape") { setOpen(false); input.current?.blur(); } }}
          onChange={(event) => setValue(event.target.value)}
        />
        <select
          className="assistant-model hide-touch"
          aria-label="Assistant model"
          title="Runs on this device (WebGPU, private, works offline after the first download) or via the server's Anthropic key"
          disabled={busy}
          value={backend === "local" ? model : backend}
          onChange={(event) => event.target.value === "cloud" || event.target.value === "llama"
            ? choose({ backend: event.target.value, model })
            : choose({ backend: "local", model: event.target.value })}
        >
          {LOCAL_MODELS.map((m) => <option key={m.id} value={m.id}>On-device · {m.label}</option>)}
          <option value="llama">llama.cpp server (LAN)</option>
          <option value="cloud">Cloud (Claude)</option>
        </select>
        <kbd className="assistant-kbd hide-touch">/</kbd>
        <button
          type="button"
          className="btn-secondary assistant-mic"
          data-on={voice.listening ? "true" : "false"}
          aria-pressed={voice.listening}
          disabled={!voiceSupported()}
          title={voiceSupported() ? "Voice mode: talk to SigSense hands-free" : "Voice needs Chrome or Edge"}
          onClick={voice.toggle}
        >
          {voice.listening ? (speaking ? "Speaking…" : busy ? "Working…" : "Listening…") : "🎤 Voice"}
        </button>
        <button
          type="button"
          className="btn-secondary assistant-speak hide-touch"
          aria-pressed={speakReplies}
          title={speakReplies ? "Spoken replies on" : "Spoken replies off"}
          onClick={() => { if (speakReplies) { stopSpeaking(); setSpeaking(false); } setSpeakReplies(!speakReplies); }}
        >
          {speakReplies ? "🔊" : "🔇"}
        </button>
        <button type="submit" className="btn-primary assistant-send" disabled={busy || !value.trim()}>
          {busy ? "Working…" : "Go"}
        </button>
        <button
          type="button"
          className="btn-secondary assistant-collapse"
          aria-label="Collapse assistant"
          title="Collapse assistant"
          onClick={() => setCollapsedPersist(true)}
        >
          ▴
        </button>
        {log.length > 0 && (
          <button
            type="button"
            className="btn-secondary assistant-clear"
            onClick={() => { setLog([]); history.current = []; }}
          >
            Clear
          </button>
        )}
      </form>
      {voice.status && (
        <p className="assistant-voice-status" role="status" data-listening={voice.listening ? "true" : "false"}>{voice.status}</p>
      )}
      {open && (
        <div className="assistant-panel" role="log" aria-live="polite">
          {log.length === 0 && (
            <div className="assistant-suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" className="mode-chip" disabled={busy} onClick={() => void run(s)}>{s}</button>
              ))}
            </div>
          )}
          {log.map((line) => (
            <p key={line.id} className={`assistant-line assistant-${line.kind}`}>{line.text}</p>
          ))}
          {busy && !log.some((l) => l.kind === "progress") && <p className="assistant-line assistant-action">Thinking…</p>}
          <div ref={logEnd} />
        </div>
      )}
    </div>
  );
}
