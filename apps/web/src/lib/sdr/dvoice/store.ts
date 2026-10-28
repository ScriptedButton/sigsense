"use client";

/**
 * Digital voice UI store: one engine connection shared by the panel, the call
 * detail sheet and the "last call" chip. Settings and talkgroup preferences
 * persist in localStorage; everything else comes from the engine.
 */
import { useSyncExternalStore } from "react";

import {
  DvClient,
  DvHttpError,
  callsFromResponse,
  dvApi,
  type DvCapabilities,
  type DvStatusResponse,
} from "./client";
import {
  DEFAULT_SETTINGS,
  EMPTY_PREFS,
  INITIAL_STATE,
  buildConfigPayload,
  mergeCallLog,
  reduceEvent,
  reduceTranscript,
  removeCall,
  talkgroupKey,
  type DvConnection,
  type DvSettings,
  type DvState,
  type TalkgroupPrefs,
  type TalkgroupRule,
} from "./model";
import { PcmPlayer } from "./pcm-player";
import type { DvAudioFrame, DvProtocol } from "./types";

const SETTINGS_KEY = "sigsense.dvoice.settings";
const PREFS_KEY = "sigsense.dvoice.talkgroups";
const SEEN_KEY = "sigsense.dvoice.intro-seen";

export type DvUiState = {
  dv: DvState;
  capabilities: DvCapabilities | null;
  /** Set when /dvoice/capabilities could not be reached (engine not installed or API down). */
  capabilitiesError: string | null;
  engineStatus: DvStatusResponse | null;
  settings: DvSettings;
  prefs: TalkgroupPrefs;
  /** Audio output state. */
  audio: { unlocked: boolean; muted: boolean; callNum: number | null; dropped: number };
  introSeen: boolean;
  configError: string | null;
  /** True while the user has digital voice switched on. */
  running: boolean;
};

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? ({ ...fallback, ...(JSON.parse(raw) as object) } as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage blocked: the choice still applies for this visit */
  }
}

class DvStore {
  private state: DvUiState;
  private listeners = new Set<() => void>();
  private client: DvClient;
  readonly player = new PcmPlayer();
  private tuning = { offsetHz: 0, frequencyHz: null as number | null };
  private configTimer: ReturnType<typeof setTimeout> | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private loaded = false;
  private source: "hackrf" | "upload" = "hackrf";
  /** Bumped by every user start/stop so a slow `attach()` never overrides a newer choice. */
  private intent = 0;

  constructor() {
    this.state = {
      dv: INITIAL_STATE,
      capabilities: null,
      capabilitiesError: null,
      engineStatus: null,
      settings: DEFAULT_SETTINGS,
      prefs: EMPTY_PREFS,
      audio: { unlocked: false, muted: false, callNum: null, dropped: 0 },
      introSeen: true,
      configError: null,
      running: false,
    };
    this.client = new DvClient({
      onEvent: (event) => this.patch({ dv: reduceEvent(this.state.dv, event) }),
      onTranscript: (transcript) => this.patch({ dv: reduceTranscript(this.state.dv, transcript) }),
      onAudio: (frame) => this.onAudio(frame),
      onConnection: (connection) => this.setConnection(connection),
    });
    this.player.subscribe(() => this.syncAudio());
  }

  // ---- external store plumbing

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): DvUiState => this.state;

  private patch(partial: Partial<DvUiState>): void {
    this.state = { ...this.state, ...partial };
    for (const listener of this.listeners) listener();
  }

  /** Read persisted settings once, on the client (never during SSR). */
  load(): void {
    if (this.loaded || typeof window === "undefined") return;
    this.loaded = true;
    let seen = true;
    try {
      seen = localStorage.getItem(SEEN_KEY) === "1";
    } catch {
      /* treat as seen so we don't nag */
    }
    this.patch({
      settings: readJson(SETTINGS_KEY, DEFAULT_SETTINGS),
      prefs: readJson(PREFS_KEY, EMPTY_PREFS),
      introSeen: seen,
    });
  }

  // ---- connection

  private setConnection(connection: "connecting" | "open" | "closed"): void {
    const mapped: DvConnection =
      connection === "closed" ? (this.state.running ? "closed" : "idle") : connection;
    this.patch({ dv: { ...this.state.dv, connection: mapped } });
    if (connection === "open") {
      void this.refreshCalls();
      this.pushConfigSoon();
    }
  }

  /**
   * Switch digital voice on: connect the event socket and tell the engine to run
   * (`enabled: true` starts it; the engine's capture follows the receiver).
   */
  start(source: "hackrf" | "upload" = "hackrf"): void {
    this.load();
    this.intent += 1;
    this.source = source;
    this.patch({ running: true });
    this.client.start(source);
    void this.pushConfig();
    void this.refreshCapabilities();
    this.startPolling();
  }

  stop(): void {
    this.intent += 1;
    this.patch({ running: false });
    // Turn the engine off first so it stops decoding; then drop the socket.
    void this.pushConfig().finally(() => this.client.stop());
    this.player.reset();
    if (this.pollTimer !== null) clearInterval(this.pollTimer);
    this.pollTimer = null;
    this.patch({ dv: { ...this.state.dv, connection: "idle" } });
  }

  /**
   * If the engine is already running on the server (another tab, or from before a
   * reload) join it instead of showing "off".
   */
  async attach(): Promise<void> {
    this.load();
    if (this.state.running) return;
    const intent = this.intent;
    try {
      const config = await dvApi.getConfig();
      if (config.enabled && !this.state.running && intent === this.intent) {
        this.source = config.source === "upload" ? "upload" : "hackrf";
        this.patch({ running: true });
        this.client.start(this.source);
        void this.refreshCapabilities();
        this.startPolling();
      }
    } catch {
      /* no engine on this API; the panel explains when the user starts it */
    }
  }

  private startPolling(): void {
    if (this.pollTimer !== null) return;
    // Capabilities and status change when the user installs the codec or the engine restarts.
    this.pollTimer = setInterval(() => void this.refreshStatus(), 5_000);
  }

  // ---- REST

  async refreshCapabilities(): Promise<void> {
    try {
      const capabilities = await dvApi.capabilities();
      this.patch({ capabilities, capabilitiesError: null });
      if (!this.state.dv.engine) await this.refreshStatus();
    } catch (error) {
      const message =
        error instanceof DvHttpError && error.status === 404
          ? "The digital voice engine isn't installed on this API."
          : error instanceof Error
            ? error.message
            : "Could not reach the API";
      this.patch({
        capabilitiesError: message,
        dv: { ...this.state.dv, connection: this.state.running ? "unavailable" : this.state.dv.connection },
      });
    }
  }

  async refreshStatus(): Promise<void> {
    try {
      this.patch({ engineStatus: await dvApi.status() });
    } catch {
      /* the socket reports connection problems; status is best effort */
    }
  }

  async refreshCalls(): Promise<void> {
    try {
      const fetched = callsFromResponse(await dvApi.calls(300));
      this.patch({ dv: mergeCallLog(this.state.dv, fetched) });
    } catch {
      /* no log yet */
    }
  }

  async deleteCall(callId: string): Promise<void> {
    this.patch({ dv: removeCall(this.state.dv, callId) });
    try {
      await dvApi.deleteCall(callId);
    } catch {
      /* already gone server-side */
    }
  }

  // ---- settings and talkgroups

  setSettings(partial: Partial<DvSettings>): void {
    const settings = { ...this.state.settings, ...partial };
    writeJson(SETTINGS_KEY, settings);
    this.patch({ settings });
    if (partial.protocol !== undefined) {
      this.client.sendConfig({ protocol: partial.protocol });
    }
    this.pushConfigSoon();
  }

  setAlias(protocol: DvProtocol, id: number, alias: string): void {
    const key = talkgroupKey(protocol, id);
    const aliases = { ...this.state.prefs.aliases };
    if (alias.trim()) aliases[key] = alias.trim();
    else delete aliases[key];
    this.savePrefs({ ...this.state.prefs, aliases });
  }

  setRule(protocol: DvProtocol, id: number, rule: TalkgroupRule): void {
    const key = talkgroupKey(protocol, id);
    const rules = { ...this.state.prefs.rules };
    if (rule === "normal") delete rules[key];
    else rules[key] = rule;
    this.savePrefs({ ...this.state.prefs, rules });
  }

  private savePrefs(prefs: TalkgroupPrefs): void {
    writeJson(PREFS_KEY, prefs);
    this.patch({ prefs });
    this.pushConfigSoon();
  }

  markIntroSeen(): void {
    try {
      localStorage.setItem(SEEN_KEY, "1");
    } catch {
      /* ignore */
    }
    this.patch({ introSeen: true });
  }

  // ---- tuning (follows the receiver)

  setTuning(offsetHz: number, frequencyHz: number | null): void {
    const moved = Math.abs(offsetHz - this.tuning.offsetHz) >= 1;
    this.tuning = { offsetHz, frequencyHz };
    if (moved) this.pushConfigSoon();
  }

  private pushConfigSoon(): void {
    if (!this.state.running) return;
    if (this.configTimer !== null) clearTimeout(this.configTimer);
    this.configTimer = setTimeout(() => {
      this.configTimer = null;
      void this.pushConfig();
    }, 300);
  }

  private async pushConfig(): Promise<void> {
    try {
      await dvApi.config(
        buildConfigPayload({
          settings: this.state.settings,
          prefs: this.state.prefs,
          enabled: this.state.running,
          source: this.source,
          offsetHz: this.tuning.offsetHz,
          frequencyHz: this.tuning.frequencyHz,
        }),
      );
      if (this.state.configError) this.patch({ configError: null });
    } catch (error) {
      this.patch({
        configError: error instanceof Error ? error.message : "Could not apply settings",
      });
    }
  }

  // ---- audio

  async unlockAudio(): Promise<void> {
    await this.player.unlock();
    this.syncAudio();
  }

  /** Listen to one call (its `meta.call_num`), or null to follow whichever is heard next. */
  followCall(callNum: number | null): void {
    this.player.reset();
    this.player.lock(callNum);
    this.syncAudio();
  }

  setMuted(muted: boolean): void {
    this.player.setMuted(muted);
    this.patch({ audio: { ...this.state.audio, muted } });
  }

  private onAudio(frame: DvAudioFrame): void {
    if (this.state.settings.autoListen || this.player.locked !== null) this.player.push(frame);
    const callNum = this.player.locked;
    if (callNum !== this.state.audio.callNum) this.syncAudio();
  }

  private syncAudio(): void {
    const next = {
      unlocked: this.player.unlocked,
      muted: this.state.audio.muted,
      callNum: this.player.locked,
      dropped: this.player.droppedFrames,
    };
    const prev = this.state.audio;
    if (
      next.unlocked !== prev.unlocked ||
      next.callNum !== prev.callNum ||
      next.dropped !== prev.dropped
    ) {
      this.patch({ audio: next });
    }
  }
}

let singleton: DvStore | null = null;

export function dvStore(): DvStore {
  singleton ??= new DvStore();
  return singleton;
}

const SERVER_SNAPSHOT: DvUiState = {
  dv: INITIAL_STATE,
  capabilities: null,
  capabilitiesError: null,
  engineStatus: null,
  settings: DEFAULT_SETTINGS,
  prefs: EMPTY_PREFS,
  audio: { unlocked: false, muted: false, callNum: null, dropped: 0 },
  introSeen: true,
  configError: null,
  running: false,
};

export function useDvoice(): DvUiState {
  const store = dvStore();
  return useSyncExternalStore(store.subscribe, store.getSnapshot, () => SERVER_SNAPSHOT);
}
