import {
  RemoteTranscriptionEngine,
  fetchSttStatus,
  type TranscribeMeta,
  type TranscribeOptions,
  type TranscribeTapConfig,
} from "./remote-transcription-engine";
import {
  TranscriptionEngine,
  type TranscriptionEngineKind,
  type TranscriptionListener,
  type TranscriptionState,
} from "./transcription-engine";

export type EnginePreference = "auto" | "server" | "device";

const PREF_KEY = "sigsense.stt-engine";
const OPTIONS_KEY = "sigsense.stt-options";

function readStored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeStored(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage blocked: the choice still applies for this visit */
  }
}

/**
 * One transcription surface for the controller and panel, backed by either the
 * server engine (GPU Whisper, the default when the server has it) or the
 * on-device engine (offline fallback). Same interface as TranscriptionEngine.
 */
export class TranscriptionHub {
  readonly device = new TranscriptionEngine();
  readonly remote: RemoteTranscriptionEngine;
  private preference: EnginePreference = "auto";
  private active: TranscriptionEngineKind = "server";
  private listeners = new Set<TranscriptionListener>();
  private snapshot: TranscriptionState;
  private starting = false;

  constructor(getMeta: () => TranscribeMeta) {
    this.remote = new RemoteTranscriptionEngine(getMeta);
    this.snapshot = { ...this.remote.getState() };
    this.device.subscribe(() => this.onEngineChange("device"));
    this.remote.subscribe(() => this.onEngineChange("server"));
  }

  /** Read persisted settings; call once on the client (not during SSR). */
  restore(): void {
    const stored = readStored<EnginePreference>(PREF_KEY, "auto");
    if (stored === "auto" || stored === "server" || stored === "device") this.preference = stored;
    const options = readStored<Partial<TranscribeOptions>>(OPTIONS_KEY, {});
    this.remote.setOptions({
      language: typeof options.language === "string" ? options.language : "en",
      prompt: typeof options.prompt === "string" ? options.prompt : "",
    });
  }

  getState(): TranscriptionState {
    return this.snapshot;
  }

  subscribe(listener: TranscriptionListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  getPreference(): EnginePreference {
    return this.preference;
  }

  getOptions(): TranscribeOptions {
    return this.remote.getOptions();
  }

  setOptions(options: Partial<TranscribeOptions>): void {
    this.remote.setOptions(options);
    writeStored(OPTIONS_KEY, this.remote.getOptions());
  }

  setTap(send: ((config: TranscribeTapConfig) => void) | null): void {
    this.remote.setTap(send);
    // A HackRF just attached while the browser engine was running: move to the
    // server tap so the phone stops doing the work.
    if (send && this.snapshot.enabled && this.active === "device" && this.preference !== "device") {
      void this.restart();
    }
  }

  ingestServerEvent(event: Parameters<RemoteTranscriptionEngine["ingestServerEvent"]>[0]): void {
    this.remote.ingestServerEvent(event);
  }

  async setPreference(preference: EnginePreference): Promise<void> {
    this.preference = preference;
    writeStored(PREF_KEY, preference);
    if (this.snapshot.enabled) await this.restart();
    else this.refresh();
  }

  async setEnabled(enabled: boolean): Promise<void> {
    if (!enabled) {
      await Promise.all([this.device.setEnabled(false), this.remote.setEnabled(false)]);
      this.refresh();
      return;
    }
    if (this.starting || this.snapshot.enabled) return;
    this.starting = true;
    try {
      this.active = await this.choose();
      this.refresh();
      await this.engine().setEnabled(true);
    } finally {
      this.starting = false;
      this.refresh();
    }
  }

  clear(): void {
    this.device.clear();
    this.remote.clear();
  }

  dispose(): void {
    this.device.dispose();
    this.remote.dispose();
  }

  pushAudio(mono: Float32Array, sampleRate: number): void {
    this.engine().pushAudio(mono, sampleRate);
  }

  private engine(): TranscriptionEngine | RemoteTranscriptionEngine {
    return this.active === "server" ? this.remote : this.device;
  }

  private async choose(): Promise<TranscriptionEngineKind> {
    if (this.preference === "device") return "device";
    if (this.preference === "server") return "server";
    const status = await fetchSttStatus();
    return status?.available ? "server" : "device";
  }

  private async restart(): Promise<void> {
    await Promise.all([this.device.setEnabled(false), this.remote.setEnabled(false)]);
    this.starting = false;
    await this.setEnabled(true);
  }

  private onEngineChange(kind: TranscriptionEngineKind): void {
    if (kind !== this.active) return;
    // Auto mode: if the server engine fails outright, fall back to the browser.
    const state = this.engine().getState();
    if (
      kind === "server" &&
      this.preference === "auto" &&
      state.enabled &&
      state.status === "error" &&
      !this.starting
    ) {
      void this.fallBackToDevice(state.error);
      return;
    }
    this.refresh();
  }

  private async fallBackToDevice(reason: string | null): Promise<void> {
    await this.remote.setEnabled(false);
    this.active = "device";
    this.starting = true;
    try {
      await this.device.setEnabled(true);
    } finally {
      this.starting = false;
    }
    this.snapshot = {
      ...this.device.getState(),
      engine: "device",
      note: `Server speech unavailable (${reason ?? "error"}) — using on-device.`,
    };
    for (const listener of this.listeners) listener(this.snapshot);
  }

  private refresh(): void {
    this.snapshot = { ...this.engine().getState(), engine: this.active };
    for (const listener of this.listeners) listener(this.snapshot);
  }
}
