import type { SdrState } from "../types";
import type { CleanerState } from "./cleaner";
import type { ReceiverSpectrum } from "../receiver-control/protocol";
import { confirmSurveyCarriers, rankListeningCandidates, surveyCarriers } from "../shortwave/survey";
import { automaticSearchPlan, canTry, loadFinderMemory, rejectCandidate, saveFinderMemory, type FinderMemory, type SearchTile } from "./policy";

export type SpeechAssessment = { model: string; speechLikely: boolean; speechRatio: number; intelligibilityConfirmed: false };
export type FinderState = {
  cleaning?: CleanerState;
  status: "idle" | "starting" | "searching" | "checking" | "listening" | "keeping" | "stopping" | "error";
  message: string; band: string | null; frequencyHz: number | null; tilesScanned: number; candidatesChecked: number;
  speechRatio: number | null; round: number; recent: Array<{ frequencyHz: number; speechRatio: number; accepted: boolean; at: string; band?: string; bandwidthHz?: number }>;
};
export const INITIAL_FINDER_STATE: FinderState = { status: "idle", message: "Find speech automatically—no ranges or channel list.", band: null,
  frequencyHz: null, tilesScanned: 0, candidatesChecked: 0, speechRatio: null, round: 0, recent: [] };
export type FinderHooks = {
  state(): SdrState;
  spectrum(): ReceiverSpectrum | null;
  tune(centerHz: number, offsetHz: number): Promise<void>;
  prepare(bandwidthHz: number): Promise<void>;
  mute(muted: boolean): void;
  capture(): Promise<{ data: string }>;
  assess(data: string, signal: AbortSignal): Promise<SpeechAssessment>;
  ready(signal: AbortSignal): Promise<void>;
  restore(original: SdrState, dial: boolean): Promise<void>;
};
type Timing = { settle: number; poll: number; quiet: number; retry: number };
const TIMING: Timing = { settle: 900, poll: 100, quiet: 5000, retry: 10000 };

/** Closed-loop receiver: RF scouting → two-LO confirmation → neural audio check → dwell/reacquire. */
export class AutonomousFinder {
  private state: FinderState = INITIAL_FINDER_STATE;
  private listeners = new Set<() => void>();
  private abort: AbortController | null = null;
  private task: Promise<void> | null = null;
  private memory: FinderMemory;
  private exit: "restore" | "keep" | "manual" = "restore";
  private skipRequested = false;
  private wake: (() => void) | null = null;
  constructor(private hooks: FinderHooks, private timing = TIMING, memory?: FinderMemory) { this.memory = memory ?? loadFinderMemory(); }
  getState = () => this.state;
  setCleaning(cleaning: CleanerState) { this.patch({ cleaning }); }
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  get active() { return this.task !== null; }
  start(maxListenMs?: number): void {
    if (this.task) return;
    const original = { ...this.hooks.state() };
    if (original.status !== "playing" || !["rtltcp", "device", "demo"].includes(original.connection)) throw new Error("Connect an RTL-SDR receiver first");
    this.abort = new AbortController(); this.exit = "restore"; this.skipRequested = false;
    this.patch({ ...INITIAL_FINDER_STATE, status: "starting", message: "Loading the local neural speech detector…" });
    this.task = this.run(original, this.abort.signal, maxListenMs).catch((error) => {
      if (!this.abort?.signal.aborted) this.patch({ status: "error", message: error instanceof Error ? error.message : String(error) });
    }).finally(async () => {
      try {
        // Manual tuning takes ownership immediately; do not later overwrite the user's new dial/settings.
        if (this.exit !== "manual" && this.hooks.state().status === "playing") await this.hooks.restore(original, this.exit === "restore");
      } catch (error) { this.patch({ status: "error", message: `Could not restore receiver: ${String(error)}` }); }
      this.hooks.mute(false); this.task = null; this.abort = null; saveFinderMemory(this.memory);
      if (this.state.status !== "error") this.patch({ status: "idle", message: this.exit === "keep" ? "Automatic searching stopped; keeping this frequency." : "Automatic searching stopped." });
      else this.patch({});
    });
  }
  stop(keep = false, manual = false): Promise<void> {
    this.exit = manual ? "manual" : keep ? "keep" : "restore";
    if (this.task) this.patch({ status: "stopping", message: "Stopping automatic search…" });
    this.abort?.abort(); this.hooks.mute(false);
    return this.task ?? Promise.resolve();
  }
  skip(): void {
    if (!this.task || this.state.frequencyHz === null) return;
    rejectCandidate(this.memory, this.state.frequencyHz, Date.now(), true); saveFinderMemory(this.memory);
    this.skipRequested = true; this.hooks.mute(true);
    this.wake?.();
    this.patch({ message: "Skipping this signal; avoiding it for 15 minutes…" });
  }
  keep(): void { if (this.state.status === "listening") { this.patch({ status: "keeping", message: "Staying here until you resume searching." }); } }
  resume(): void { if (this.state.status === "keeping") this.patch({ status: "listening", message: "Monitoring speech; will resume searching if it fades." }); }
  private patch(patch: Partial<FinderState>) { this.state = { ...this.state, ...patch }; for (const listener of this.listeners) listener(); }
  private check(signal: AbortSignal) {
    signal.throwIfAborted();
    if (this.hooks.state().status !== "playing") throw new Error("Receiver disconnected; automatic search stopped");
  }
  private sleep(ms: number, signal: AbortSignal, wakeable = false) {
    signal.throwIfAborted();
    return new Promise<void>((resolve, reject) => {
      const cleanup = () => { signal.removeEventListener("abort", cancel); if (this.wake === done) this.wake = null; };
      const done = () => { clearTimeout(timer); cleanup(); resolve(); };
      const timer = setTimeout(done, ms);
      const cancel = () => { clearTimeout(timer); cleanup(); reject(signal.reason); };
      signal.addEventListener("abort", cancel, { once: true });
      if (wakeable) this.wake = done;
    });
  }
  private async snapshot(center: number, signal: AbortSignal) {
    this.check(signal); await this.hooks.tune(center, 0); this.check(signal);
    const after = Date.now(); await this.sleep(this.timing.settle, signal);
    for (let attempt = 0; attempt < 30; attempt++) {
      this.check(signal);
      const snapshot = this.hooks.spectrum();
      if (snapshot && snapshot.centerHz === center && Date.parse(snapshot.capturedAt) > after) return snapshot;
      await this.sleep(this.timing.poll, signal);
    }
    throw new Error("No fresh receiver spectrum; automatic search stopped");
  }
  private async audio(signal: AbortSignal) {
    this.check(signal);
    const captured = await this.hooks.capture(); this.check(signal);
    const assessed = await this.hooks.assess(captured.data, signal); this.check(signal);
    if (!Number.isFinite(assessed.speechRatio) || assessed.speechRatio < 0 || assessed.speechRatio > 1 || typeof assessed.speechLikely !== "boolean") throw new Error("Invalid neural speech assessment");
    return assessed;
  }
  private async candidate(hz: number, bandwidth: number, tile: SearchTile, signal: AbortSignal, maxListenMs?: number) {
    this.skipRequested = false; this.hooks.mute(true);
    this.patch({ status: "checking", frequencyHz: hz, speechRatio: null, candidatesChecked: this.state.candidatesChecked + 1,
      message: "Checking raw audio with local AI; scanning noise is muted…" });
    await this.hooks.prepare(bandwidth); this.check(signal);
    await this.hooks.tune(hz + 50_000, -50_000); this.check(signal);
    await this.sleep(this.timing.settle, signal);
    if (this.skipRequested) return;
    const first = await this.audio(signal);
    const second = first.speechLikely && !this.skipRequested ? await this.audio(signal) : null;
    const accepted = first.speechLikely && Boolean(second?.speechLikely) && !this.skipRequested;
    this.patch({ speechRatio: second?.speechRatio ?? first.speechRatio, recent: [{ frequencyHz: hz, speechRatio: second?.speechRatio ?? first.speechRatio,
      accepted, at: new Date().toISOString(), band: tile.label, bandwidthHz: bandwidth }, ...this.state.recent].slice(0, 100) });
    if (!accepted) { if (!this.skipRequested) rejectCandidate(this.memory, hz, Date.now()); return; }
    this.memory.bands[tile.id] = Math.min(20, (this.memory.bands[tile.id] ?? 0) + 1); saveFinderMemory(this.memory);
    this.patch({ status: "listening", message: "AI detected speech in two samples—not a verified transcript. Listening and monitoring for fading…" });
    this.hooks.mute(false);
    const listeningSince = Date.now();
    let quiet = 0;
    while (!this.skipRequested) {
      await this.sleep(this.timing.quiet, signal, true); this.check(signal);
      if (this.skipRequested) break;
      if (this.state.status === "keeping") continue;
      if (maxListenMs && Date.now() - listeningSince >= maxListenMs) break;
      const current = await this.audio(signal);
      if (this.skipRequested) break;
      if (this.getState().status === "keeping") continue;
      this.patch({ speechRatio: current.speechRatio });
      quiet = current.speechLikely ? 0 : quiet + 1;
      if (quiet >= 2) break;
    }
    this.hooks.mute(true);
    if (!this.skipRequested) rejectCandidate(this.memory, hz, Date.now());
  }
  private async run(original: SdrState, signal: AbortSignal, maxListenMs?: number) {
    await this.hooks.ready(signal); this.check(signal); this.hooks.mute(true);
    await this.hooks.prepare(6000); this.check(signal);
    while (true) {
      this.patch({ round: this.state.round + 1 });
      const plan = automaticSearchPlan(this.hooks.state().sampleRate, this.memory);
      for (const tile of plan) {
        this.check(signal);
        this.patch({ status: "searching", band: tile.label, frequencyHz: null, speechRatio: null,
          message: `Searching ${tile.label}; checking a second tuner position to reject receiver spurs…` });
        const first = await this.snapshot(tile.centerHz, signal), second = await this.snapshot(tile.centerHz + 100_000, signal);
        const candidates = rankListeningCandidates(confirmSurveyCarriers(surveyCarriers(first, tile.lowHz, tile.highHz), surveyCarriers(second, tile.lowHz, tile.highHz), Math.max(first.binHz, second.binHz) * 2.1));
        this.patch({ tilesScanned: this.state.tilesScanned + 1 });
        // Always try the strongest confirmed carrier, even if RF-only sideband ranking is inconclusive.
        for (const candidate of candidates.filter((c) => canTry(this.memory, c.frequencyHz, Date.now())).slice(0, 3)) {
          await this.candidate(candidate.frequencyHz, candidate.recommendedBandwidthHz ?? 6000, tile, signal, maxListenMs); this.check(signal);
        }
      }
      // Decay old successes and periodically retry; no dead-end "nothing found" state.
      for (const key of Object.keys(this.memory.bands)) this.memory.bands[key] *= .9;
      saveFinderMemory(this.memory);
      this.patch({ status: "searching", message: "No sustained speech found. Continuing automatically after a short retry interval…" });
      await this.sleep(this.timing.retry, signal);
    }
  }
}
