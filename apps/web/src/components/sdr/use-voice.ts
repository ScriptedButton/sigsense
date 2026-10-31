"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Recognition = {
  continuous: boolean; interimResults: boolean; lang: string;
  start(): void; stop(): void; abort(): void;
  onstart: (() => void) | null;
  onaudiostart: (() => void) | null;
  onspeechstart: (() => void) | null;
  onresult: ((e: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
};
type RecognitionCtor = new () => Recognition;

function ctor(): RecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

function insecureMessage(): string {
  return `The browser blocks the microphone on plain HTTP (${location.origin}). In Chrome/Edge open chrome://flags/#unsafely-treat-insecure-origin-as-secure, add ${location.origin}, set it to Enabled and relaunch — or open the app at http://localhost.`;
}

export const voiceSupported = () => ctor() !== null;

type Options = {
  /** Recognition stays off while true (assistant working or speaking) so it doesn't hear itself. */
  paused: boolean;
  onInterim: (text: string) => void;
  onFinal: (text: string) => void;
  onError: (message: string) => void;
};

/** Hands-free voice input: toggle on, speak commands, each finished phrase is passed to onFinal. */
export function useVoice({ paused, onInterim, onFinal, onError }: Options) {
  const [listening, setListening] = useState(false);
  const [status, setStatus] = useState("");
  const want = useRef(false);
  const rec = useRef<Recognition | null>(null);
  const pausedRef = useRef(paused);
  const handlers = useRef({ onInterim, onFinal, onError });
  handlers.current = { onInterim, onFinal, onError };
  pausedRef.current = paused;

  const begin = useCallback(() => {
    const Ctor = ctor();
    if (!Ctor || rec.current || !want.current || pausedRef.current) return;
    const r = new Ctor();
    r.continuous = false;
    r.interimResults = true;
    r.lang = navigator.language || "en-US";
    r.onstart = () => setStatus("Listening — say a command");
    r.onaudiostart = () => setStatus("Mic is live — say a command");
    r.onspeechstart = () => setStatus("Hearing you…");
    r.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const text = event.results[i][0].transcript;
        if (event.results[i].isFinal) { handlers.current.onInterim(""); handlers.current.onFinal(text.trim()); }
        else interim += text;
      }
      if (interim) { handlers.current.onInterim(interim); setStatus(`Hearing: “${interim}”`); }
    };
    r.onerror = (event) => {
      if (event.error === "no-speech") { setStatus("Didn't catch anything — still listening"); return; }
      if (event.error === "aborted") return;
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        want.current = false;
        setListening(false);
        const message = window.isSecureContext
          ? "Speech recognition was blocked (permission denied, or this browser's speech service is unavailable — Chromium builds without Google keys can't do it; use Chrome or Edge)."
          : insecureMessage();
        setStatus(message);
        handlers.current.onError(message);
      } else if (event.error === "network") {
        want.current = false;
        setListening(false);
        const message = "Speech recognition failed: the browser's speech service is unreachable. Chrome/Edge need internet access for it, and plain Chromium builds don't include it at all.";
        setStatus(message);
        handlers.current.onError(message);
      } else {
        setStatus(`Speech recognition error: ${event.error}`);
        handlers.current.onError(`Speech recognition error: ${event.error}`);
      }
    };
    r.onend = () => {
      rec.current = null;
      if (want.current && !pausedRef.current) setTimeout(begin, 250);
    };
    rec.current = r;
    try { r.start(); } catch { rec.current = null; }
  }, []);

  useEffect(() => {
    if (paused) { rec.current?.abort(); }
    else if (want.current) begin();
  }, [paused, begin]);

  useEffect(() => () => { want.current = false; rec.current?.abort(); }, []);

  const toggle = useCallback(async () => {
    if (want.current) {
      want.current = false;
      setListening(false);
      setStatus("");
      rec.current?.abort();
      handlers.current.onInterim("");
    } else {
      const fail = (message: string) => { setStatus(message); handlers.current.onError(message); };
      if (!ctor()) { fail("This browser has no speech recognition. Use Chrome or Edge."); return; }
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) { fail(insecureMessage()); return; }
      setStatus("Asking for microphone permission…");
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((track) => track.stop());
      } catch (error) {
        const name = error instanceof DOMException ? error.name : "";
        fail(name === "NotFoundError" ? "No microphone found on this device."
          : name === "NotAllowedError" ? "Microphone permission was denied. Click the lock icon in the address bar and allow the microphone."
          : `Could not open the microphone (${name || String(error)}).`);
        return;
      }
      want.current = true;
      setListening(true);
      begin();
    }
  }, [begin]);

  return { listening, toggle, status };
}

export function speak(text: string, onDone: () => void): void {
  if (typeof speechSynthesis === "undefined" || !text) { onDone(); return; }
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.onend = onDone;
  utterance.onerror = onDone;
  speechSynthesis.speak(utterance);
}

export function stopSpeaking(): void {
  if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
}
