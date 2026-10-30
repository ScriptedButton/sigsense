import type { RadioController, SpectrumListener } from "@/lib/sdr/radio-controller";
import { loadDisplaySettings, saveDisplaySettings, type DisplaySettings } from "@/lib/sdr/display-settings";
import { loadShortwaveSchedule, filterSchedule } from "@/lib/sdr/shortwave/schedule";
import { SHORTWAVE_BANDS } from "@/lib/sdr/shortwave/bands";
import { isMarkerMode } from "@/lib/sdr/signal-id/scope-overlay";
import { RECEIVER_ACTIONS, RECEIVER_CONTROL_VERSION, type ReceiverBootstrap, type ReceiverCommand, type ReceiverResponse, type ReceiverSpectrum } from "./protocol";

const MAX_BUFFERED_BYTES = 1_000_000;
const MAX_SPECTRUM_BINS = 8_192;
const MODES = new Set(["WBFM", "NBFM", "AM", "USB", "LSB", "CW"]);
const NOISE_LEVELS = new Set(["off", "low", "medium", "high"]);

type DiagnosticController = RadioController & {
  captureDiagnosticAudio(durationMs?: number, maxSamples?: number, stage?: "playback" | "raw"): Promise<unknown>;
  captureDiagnosticIq(durationMs?: number, maxSamples?: number): Promise<unknown>;
  getDiagnosticSnapshot(): unknown;
};

function finite(args: Record<string, unknown>, key: string, min: number, max: number): number | undefined {
  const value = args[key];
  if (value === undefined) return undefined;
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) {
    throw new Error(`${key} must be a finite number from ${min} to ${max}`);
  }
  return value;
}

function boolean(args: Record<string, unknown>, key: string): boolean | undefined {
  const value = args[key];
  if (value === undefined) return undefined;
  if (typeof value !== "boolean") throw new Error(`${key} must be boolean`);
  return value;
}

function string(args: Record<string, unknown>, key: string): string | undefined {
  const value = args[key];
  if (value === undefined) return undefined;
  if (typeof value !== "string") throw new Error(`${key} must be a string`);
  return value;
}

function sessionId(): string {
  const key = "sigsense.receiver-control.session";
  let id: string | null = null;
  try { id = sessionStorage.getItem(key); } catch { /* private browsing */ }
  if (!id) {
    // randomUUID requires HTTPS, while this receiver runs on a LAN HTTP URL.
    id = typeof crypto.randomUUID === "function" ? crypto.randomUUID()
      : Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
    try { sessionStorage.setItem(key, id); } catch { /* private browsing */ }
  }
  return id;
}

function spectrumSignals(spectrum: ReceiverSpectrum | null) {
  if (!spectrum || spectrum.values.length < 3) return [];
  const sorted = [...spectrum.values].sort((a, b) => a - b);
  const floor = sorted[Math.floor(sorted.length / 2)];
  return spectrum.values.flatMap((powerDb, index) => {
    if (index === 0 || index === spectrum.values.length - 1 || powerDb < floor + 8) return [];
    if (powerDb <= spectrum.values[index - 1] || powerDb < spectrum.values[index + 1]) return [];
    const frequencyHz = (spectrum.startHz ?? spectrum.centerHz + spectrum.centerOffsetHz - Math.floor(spectrum.values.length / 2) * spectrum.binHz) + index * spectrum.binHz;
    if (Math.abs(frequencyHz - spectrum.centerHz) < 2 * spectrum.binHz) return [];
    return [{ frequencyHz, powerDb, aboveFloorDb: powerDb - floor }];
  }).sort((a, b) => b.powerDb - a.powerDb).slice(0, 50);
}

function toSerializable(value: unknown): unknown {
  if (value instanceof Float32Array || value instanceof Uint8Array) {
    const bytes = new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 0x8000) binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
    return { encoding: "base64", data: btoa(binary), samples: value.length, format: value instanceof Float32Array ? "float32-le" : "uint8" };
  }
  if (Array.isArray(value)) return value.map(toSerializable);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, toSerializable(item)]));
  return value;
}

function audioToWav(value: unknown): unknown {
  const samples = value instanceof Float32Array
    ? value
    : value && typeof value === "object" && (value as { samples?: unknown }).samples instanceof Float32Array
      ? (value as { samples: Float32Array }).samples
      : null;
  if (!samples) return toSerializable(value);
  const sampleRate = value && typeof value === "object" && typeof (value as { sampleRate?: unknown }).sampleRate === "number"
    ? (value as { sampleRate: number }).sampleRate : 48_000;
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset: number, word: string) => [...word].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); text(8, "WAVE"); text(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, index) => view.setInt16(44 + index * 2, Math.round(Math.max(-1, Math.min(1, sample)) * 32767), true));
  return { mimeType: "audio/wav", encoding: "base64", data: toSerializable(new Uint8Array(buffer)) instanceof Object ? (toSerializable(new Uint8Array(buffer)) as { data: string }).data : "", sampleRate, samples: samples.length };
}

function diagnosticMethod<T extends keyof DiagnosticController>(controller: DiagnosticController, name: T): DiagnosticController[T] {
  const method = controller[name];
  if (typeof method !== "function") throw new Error(`receiver does not support ${String(name)}`);
  return method.bind(controller) as DiagnosticController[T];
}

export function createReceiverCommandExecutor(controller: RadioController, getSpectrum: () => ReceiverSpectrum | null) {
  const diagnostic = controller as DiagnosticController;
  return async (command: ReceiverCommand): Promise<unknown> => {
    const a = command.args ?? {};
    const keys: Record<string, string[]> = {
      configure: ["frequencyHz", "offsetHz", "bandwidthHz", "sampleRate", "gainDb", "volume", "squelch", "ppm", "scheme", "audioNoiseReduction", "autoGain", "autoSquelch", "afc", "biasTee", "voiceIsolation", "audioSpeechFilter"],
      tune: ["frequencyHz", "scheme", "bandwidthHz", "center"], connect: ["type", "url", "password"],
      capture_audio: ["durationMs", "maxSamples", "stage"], capture_iq: ["durationMs", "maxSamples"],
      find_signal: ["frequencyHz"], get_schedule: ["when", "bandId", "language", "query"],
      set_display: Object.keys(loadDisplaySettings()),
      autofinder_start: ["maxListenMs"],
    };
    if (!a || typeof a !== "object" || Array.isArray(a)) throw new Error("command args must be an object");
    for (const key of Object.keys(a)) if (!(keys[command.action] ?? []).includes(key)) throw new Error(`unknown ${command.action} argument ${key}`);
    switch (command.action) {
      case "autofinder_state": return controller.getAutofinderState();
      case "autofinder_start": await controller.startAutofinder(finite(a, "maxListenMs", 10_000, 300_000)); return controller.getAutofinderState();
      case "autofinder_stop": await controller.stopAutofinder(); return controller.getAutofinderState();
      case "autofinder_skip": controller.skipAutofinder(); return controller.getAutofinderState();
      case "get_state": return controller.getState();
      case "configure": {
        const frequencyHz = finite(a, "frequencyHz", 0, 6e9);
        const offsetHz = finite(a, "offsetHz", -20e6, 20e6);
        const bandwidthHz = finite(a, "bandwidthHz", 50, 300_000);
        const sampleRate = finite(a, "sampleRate", 250_000, 20e6);
        const gainDb = finite(a, "gainDb", 0, 50);
        const volume = finite(a, "volume", 0, 1);
        const squelch = finite(a, "squelch", 0, 100);
        const ppm = finite(a, "ppm", -200, 200);
        const scheme = string(a, "scheme");
        const noise = string(a, "audioNoiseReduction");
        const speechFilter = boolean(a, "audioSpeechFilter");
        if (scheme && !MODES.has(scheme)) throw new Error("unsupported scheme");
        if (noise && !NOISE_LEVELS.has(noise)) throw new Error("unsupported audioNoiseReduction");
        const autoGain = boolean(a, "autoGain"), autoSquelch = boolean(a, "autoSquelch"), afc = boolean(a, "afc"), biasTee = boolean(a, "biasTee"), voiceIsolation = boolean(a, "voiceIsolation");
        if (scheme) controller.setScheme(scheme as never);
        if (bandwidthHz !== undefined) controller.setBandwidth(bandwidthHz);
        if (sampleRate !== undefined) await controller.setSampleRate(sampleRate);
        if (frequencyHz !== undefined) await controller.setFrequency(frequencyHz);
        if (offsetHz !== undefined) controller.setOffset(offsetHz);
        if (volume !== undefined) controller.setVolume(volume);
        if (autoGain !== undefined) await controller.setAutoGain(autoGain);
        if (gainDb !== undefined) await controller.setGain(gainDb);
        if (squelch !== undefined) controller.setSquelch(squelch);
        if (autoSquelch !== undefined) controller.setAutoSquelch(autoSquelch);
        if (afc !== undefined) controller.setAfc(afc);
        if (ppm !== undefined) await controller.setPpm(ppm);
        if (biasTee !== undefined) await controller.setBiasTee(biasTee);
        if (noise) controller.setAudioNoiseReduction(noise as never);
        if (speechFilter !== undefined) controller.setAudioSpeechFilter(speechFilter);
        if (voiceIsolation !== undefined) await controller.setVoiceIsolation(voiceIsolation);
        return controller.getState();
      }
      case "tune": {
        const hz = finite(a, "frequencyHz", 0, 6e9);
        if (hz === undefined) throw new Error("frequencyHz is required");
        const scheme = string(a, "scheme"), bandwidth = finite(a, "bandwidthHz", 50, 300_000), center = boolean(a, "center");
        if (scheme && !MODES.has(scheme)) throw new Error("unsupported scheme");
        if (scheme) controller.setScheme(scheme as never);
        if (bandwidth !== undefined) controller.setBandwidth(bandwidth);
        if (center) { await controller.setFrequency(hz); if (controller.getState().frequencyHz !== hz) throw new Error("Receiver rejected the center frequency change"); controller.setOffset(0); }
        else {
          const state = controller.getState();
          const offset = hz - state.frequencyHz;
          if (Math.abs(offset) <= state.sampleRate * 0.46) controller.setOffset(offset);
          else { await controller.setFrequency(hz); if (controller.getState().frequencyHz !== hz) throw new Error("Receiver rejected the frequency change"); controller.setOffset(0); }
        }
        return controller.getState();
      }
      case "connect": {
        const type = string(a, "type");
        const url = string(a, "url");
        if (type === "demo") await controller.connectDemo();
        else if (type === "rtltcp") {
          const address = url ?? localStorage.getItem("sdrsense.rtl-server.url") ?? "";
          if (!address) throw new Error("rtltcp requires url or a saved server address");
          await controller.connectRtlServer(address);
        }
        else if (type === "kiwi" && url) await controller.connectKiwi(url, string(a, "password") ?? "");
        else if (type === "openwebrx" && url) await controller.connectOpenWebRx(url);
        else throw new Error("connect type must be demo, rtltcp, kiwi, or openwebrx; remote types require url");
        return controller.getState();
      }
      case "stop": await controller.stop(); return controller.getState();
      case "get_spectrum": return getSpectrum();
      case "capture_audio": {
        const stage = string(a, "stage") ?? "playback";
        if (stage !== "playback" && stage !== "raw") throw new Error("stage must be playback or raw");
        const capture = diagnosticMethod(diagnostic, "captureDiagnosticAudio");
        return audioToWav(await capture(finite(a, "durationMs", 50, 10_000), finite(a, "maxSamples", 256, 480_000), stage));
      }
      case "capture_iq": {
        const capture = diagnosticMethod(diagnostic, "captureDiagnosticIq");
        return toSerializable(await capture(finite(a, "durationMs", 10, 2_000), finite(a, "maxSamples", 256, 262_144)));
      }
      case "get_diagnostics": return diagnosticMethod(diagnostic, "getDiagnosticSnapshot")();
      case "get_signals": return spectrumSignals(getSpectrum());
      case "find_signal": {
        const signals = spectrumSignals(getSpectrum());
        const near = finite(a, "frequencyHz", 0, 6e9);
        return near === undefined ? signals[0] ?? null : signals.sort((x, y) => Math.abs(x.frequencyHz - near) - Math.abs(y.frequencyHz - near))[0] ?? null;
      }
      case "get_schedule": {
        const schedule = await loadShortwaveSchedule();
        const when = string(a, "when") ?? "now";
        const bandId = string(a, "bandId");
        if (bandId && !SHORTWAVE_BANDS.some((band) => band.id === bandId)) throw new Error("Unknown shortwave bandId");
        if (!new Set(["now", "soon", "all"]).has(when)) throw new Error("when must be now, soon, or all");
        return filterSchedule(schedule.entries, { when: when as never, bandId: (string(a, "bandId") as never) ?? null, language: string(a, "language") ?? null, query: string(a, "query") ?? "" }).slice(0, 100);
      }
      case "get_display": return loadDisplaySettings();
      case "set_display": {
        const current = loadDisplaySettings();
        const allowed = new Set(Object.keys(current));
        for (const key of Object.keys(a)) if (!allowed.has(key)) throw new Error(`unknown display setting ${key}`);
        const enums: Record<string, string[]> = {
          waterfallPalette: ["cubehelix", "thermal", "viridis", "ocean", "mono"], waterfallSpeed: ["slow", "normal", "fast"],
          waterfallDirection: ["top", "bottom"], dbRange: ["auto", "wide", "normal", "tight"], noiseFilter: ["off", "low", "medium", "high"],
          freqTarget: ["tune", "center"], freqFormat: ["auto", "mhz", "khz", "hz"],
        };
        for (const [key, options] of Object.entries(enums)) if (a[key] !== undefined && !options.includes(a[key] as string)) throw new Error(`Invalid display setting ${key}`);
        finite(a, "waterfallRangeDb", 16, 60); finite(a, "waterfallFloorOffsetDb", -12, 12);
        if (a.markerMode !== undefined && !isMarkerMode(a.markerMode)) throw new Error("Invalid markerMode");
        const next = { ...current, ...a } as DisplaySettings;
        saveDisplaySettings(next);
        window.dispatchEvent(new CustomEvent("sdr-display-settings", { detail: next }));
        return next;
      }
      default: throw new Error(`unknown receiver action ${(command as { action: string }).action}`);
    }
  };
}

export function startReceiverControlClient(controller: RadioController): () => void {
  let stopped = false, socket: WebSocket | null = null, retry = 500, timer = 0;
  let latestSpectrum: ReceiverSpectrum | null = null, lastTelemetry = 0;
  const send = (value: unknown) => {
    if (socket?.readyState === WebSocket.OPEN && socket.bufferedAmount < MAX_BUFFERED_BYTES) socket.send(JSON.stringify(value));
  };
  const telemetry = () => {
    const now = Date.now();
    if (now - lastTelemetry < 1_000) return;
    lastTelemetry = now;
    send({ type: "telemetry", state: controller.getState(), spectrum: latestSpectrum, diagnostics: (controller as DiagnosticController).getDiagnosticSnapshot() });
  };
  const spectrumListener: SpectrumListener = (values, centerHz, info) => {
    const stride = Math.max(1, Math.ceil(values.length / MAX_SPECTRUM_BINS));
    const sampleRate = info?.sampleRate ?? controller.getState().sampleRate;
    const center = centerHz ?? controller.getState().frequencyHz;
    const shifted: number[] = [];
    for (let index = 0; index < values.length; index += stride) {
      let peak = -200;
      for (let k = index; k < Math.min(values.length, index + stride); k++) {
        const value = values[(k + Math.floor(values.length / 2)) % values.length];
        if (Number.isFinite(value)) peak = Math.max(peak, value);
      }
      shifted.push(peak);
    }
    latestSpectrum = { values: shifted, centerHz: center, sampleRate, centerOffsetHz: info?.centerOffsetHz ?? 0,
      startHz: center + (info?.centerOffsetHz ?? 0) - Math.floor(values.length / 2) * sampleRate / values.length,
      binOrder: "ascending-frequency", binHz: sampleRate / values.length * stride, capturedAt: new Date().toISOString() };
    telemetry();
  };
  const unsubscribeSpectrum = controller.onSpectrum(spectrumListener, true);
  const unsubscribeState = controller.subscribe(telemetry);
  const execute = createReceiverCommandExecutor(controller, () => latestSpectrum);
  const connect = async () => {
    try {
      const response = await fetch("/api/receiver-control", { cache: "no-store" });
      if (!response.ok) throw new Error(`bootstrap ${response.status}`);
      const bootstrap = await response.json() as ReceiverBootstrap;
      if (stopped) return;
      if (bootstrap.version !== RECEIVER_CONTROL_VERSION || !bootstrap.token || !Number.isInteger(bootstrap.port)) throw new Error("invalid receiver-control bootstrap");
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${protocol}://${location.hostname}:${bootstrap.port}/receiver?token=${encodeURIComponent(bootstrap.token)}`);
      socket.onopen = () => { retry = 500; send({ type: "hello", sessionId: sessionId(), state: controller.getState(), url: location.href, title: document.title }); telemetry(); };
      socket.onmessage = (event) => {
        void (async () => {
          let response: ReceiverResponse;
          let commandId = "unknown";
          try {
            const command = JSON.parse(String(event.data)) as ReceiverCommand;
            if (command && (command as { type: string }).type === "welcome") return;
            if (typeof command.id === "string") commandId = command.id;
            if (command.type !== "command" || typeof command.id !== "string" || !RECEIVER_ACTIONS.includes(command.action)) throw new Error("invalid command");
            response = { type: "result", id: command.id, result: await execute(command) };
          } catch (error) {
            response = { type: "result", id: commandId, error: error instanceof Error ? error.message : String(error) };
          }
          send(response);
        })();
      };
      socket.onclose = () => { if (!stopped) { timer = window.setTimeout(connect, retry); retry = Math.min(retry * 2, 30_000); } };
    } catch { if (!stopped) { timer = window.setTimeout(connect, retry); retry = Math.min(retry * 2, 30_000); } }
  };
  void connect();
  const heartbeat = window.setInterval(telemetry, 1_000);
  return () => { stopped = true; clearTimeout(timer); clearInterval(heartbeat); unsubscribeState(); unsubscribeSpectrum(); socket?.close(); };
}
