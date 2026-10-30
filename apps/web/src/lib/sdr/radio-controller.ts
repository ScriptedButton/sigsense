import type { Demodulator } from "@jtarrio/signals/demod/demodulator.js";
import {
  getMode,
  getSchemes,
  modeParameters,
} from "@jtarrio/signals/demod/modes.js";
import type { SampleCounter } from "@jtarrio/signals/demod/sample-counter.js";
import type { StreamingSpectrum as Spectrum, SpectrumFrameInfo } from "./streaming-spectrum";
import type { Radio, RtlProvider } from "@jtarrio/webrtlsdr/radio.js";

import { clamp } from "./format";
import { KiwiClient } from "./kiwi/client";
import { kiwiBandpass, kiwiModulation } from "./kiwi/modes";
import {
  KIWI_DEFAULT_ZOOM,
  kiwiZoomForSpanHz,
  kiwiZoomSpanHz,
  type KiwiSession,
} from "./kiwi/protocol";
import { OpenWebRxClient } from "./openwebrx/client";
import { owrxBandpass, owrxModulation, schemeFromOwrx } from "./openwebrx/modes";
import { OpenWebRxPlayback } from "./openwebrx/playback";
import type { OpenWebRxSession } from "./openwebrx/protocol";
import { unshiftSpectrum } from "./openwebrx/spectrum";
import {
  openWebRxMixedContentMessage,
  openWebRxWebSocketUrl,
} from "./openwebrx/url";
import { RtlTcpDeviceProvider } from "./rtl-tcp/device";
import {
  rtlServerMixedContentMessage,
  rtlServerWebSocketUrl,
} from "./rtl-tcp/url";
import { DSP_UNSUPPORTED_MESSAGE, loadDsp, type Dsp } from "./dsp";
import {
  FskDecoderEngine,
} from "./fsk/engine";
import type {
  FskConfig,
  FskListener,
  FskPresetId,
  FskState,
} from "./fsk/types";
import { Ft8DecoderEngine } from "./ft8/engine";
import type {
  Ft8Config,
  Ft8Listener,
  Ft8State,
} from "./ft8/types";
import { MorseDecoderEngine } from "./morse/engine";
import type {
  MorseConfig,
  MorseListener,
  MorsePresetId,
  MorseState,
} from "./morse/types";
import { P25DecoderEngine } from "./p25/engine";
import { P25IqReceiver } from "./p25/iq-receiver";
import type {
  P25Config,
  P25Listener,
  P25State,
} from "./p25/types";
import { ScannerEngine } from "./scanner/engine";
import { AutonomousFinder, type FinderState, type SpeechAssessment } from "./autofinder/engine";
import { FinderCleaner } from "./autofinder/cleaner";
import type { ReceiverSpectrum } from "./receiver-control/protocol";
import type {
  ScanChannel,
  ScannerConfig,
  ScannerListener,
  ScannerState,
} from "./scanner/types";
import { WefaxDecoderEngine } from "./wefax/engine";
import type {
  WefaxConfig,
  WefaxListener,
  WefaxPresetId,
  WefaxState,
} from "./wefax/types";
import type { SignalIdSensitivity } from "./signal-id/engine";
import type { BackendTrack } from "./signal-id/backend-tracks";
import { HybridSignalId, type SignalCorrectionPatch } from "./signal-id/hybrid";
import type { SignalIdListener, SignalIdState } from "./signal-id/types";
import { AfcTracker, isAfcScheme } from "./afc";
import {
  AUTO_SQUELCH_DEFAULT_MARGIN_DB,
  SQUELCH_DEFAULT_DB,
  SQUELCH_MAX_DB,
  SQUELCH_MIN_DB,
  SquelchGate,
} from "./squelch";
import { AudioEnhancer, loadVoiceIsolation, restoreAudioEnhancementSettings, setAudioEnhancementSettings, type AudioNoiseReduction } from "./audio-enhancement";
import { TappingPlayer } from "./tapping-player";
import { captureAudioStream, captureIqStream, type PcmListener, type IqListener } from "./receiver-control/capture";
import {
  type TranscriptionListener,
  type TranscriptionState,
} from "./transcription-engine";
import { TranscriptionHub, type EnginePreference } from "./transcription-hub";
import type { TranscribeOptions, TranscribeTapConfig } from "./remote-transcription-engine";
import {
  DEFAULT_SAMPLE_RATE,
  FFT_SIZE,
  nearestSampleRate,
  type ConnectionMode,
  type DemodScheme,
  type SdrState,
} from "./types";

export type SpectrumListener = (
  spectrum: Float32Array,
  centerHz: number | undefined,
  info?: SpectrumFrameInfo,
) => void;

export type StateListener = (state: SdrState) => void;

/**
 * A radio owned outside this controller (the HackRF API stream). It feeds
 * spectra in and receives tuning requests, so the scope, waterfall, 3D view
 * and Identify tools work on it like any other source.
 */
export type ExternalDemod = {
  scheme: DemodScheme;
  bandwidthHz: number;
  offsetHz: number;
};

export type ExternalSource = {
  /** Start/stop speech-to-text on the server's own copy of the demodulated audio. */
  onTranscribe?: (config: TranscribeTapConfig) => void;
  /** Retune request from the display or frequency controls. */
  onTune?: (centerHz: number) => void;
  /** Capture-rate request from the Spectrum width control. */
  onSampleRate?: (hz: number) => void;
  /** Demodulator settings changed (mode, filter width, offset from centre). */
  onDemod?: (cfg: ExternalDemod) => void;
  /** The user pressed Stop while this source was active. */
  onStop?: () => void;
};

/** Channel widths per scheme, matching the DSP's own defaults (used before it loads). */
const DEFAULT_BANDWIDTH_HZ: Record<DemodScheme, number> = {
  WBFM: 150_000,
  NBFM: 10_000,
  AM: 15_000,
  USB: 2_800,
  LSB: 2_800,
  CW: 50,
};

function isWebUsbSupported(): boolean {
  return typeof navigator !== "undefined" && "usb" in navigator;
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) {
    if (err.name === "NotFoundError") {
      return "No device selected. Plug in your RTL-SDR and try again.";
    }
    if (err.name === "SecurityError") {
      return "WebUSB blocked. Use HTTPS or localhost in Chrome/Edge.";
    }
    if (err.name === "NetworkError" || /claim/i.test(err.message)) {
      return "Could not claim the USB device. Close other SDR apps and retry (on Linux, unload dvb_usb_rtl28xxu).";
    }
    return err.message;
  }
  return String(err);
}

export class RadioController {
  private radio: Radio | null = null;
  private demodulator: Demodulator | null = null;
  private spectrum: Spectrum | null = null;
  private sampleCounter: SampleCounter | null = null;
  private player: TappingPlayer | null = null;
  private untap: (() => void) | null = null;
  private readonly transcription = new TranscriptionHub(() => ({
    frequencyHz: this.state.frequencyHz + this.state.offsetHz,
    mode: this.state.scheme,
    sourceLabel: this.state.connection === "hackrf" ? "HackRF" : this.state.connection,
  }));
  private readonly fsk = new FskDecoderEngine();
  private readonly ft8 = new Ft8DecoderEngine();
  private readonly morse = new MorseDecoderEngine();
  private readonly wefax = new WefaxDecoderEngine();
  private readonly p25 = new P25DecoderEngine();
  private readonly p25Iq = new P25IqReceiver();
  private readonly scanner = new ScannerEngine();
  private finder: AutonomousFinder | null = null;
  private finderSpectrum: ReceiverSpectrum | null = null;
  private finderUntap: (() => void) | null = null;
  private finderCommandDepth = 0;
  private finderMuted = false;
  private backendMonitorMuted = false;
  private monitorMuteOwners = new Set<object>();
  private get monitorMuted(): boolean { return this.backendMonitorMuted || this.monitorMuteOwners.size > 0; }
  private backendCleanActive = false;
  private backendCleanPlayback: OpenWebRxPlayback | null = null;
  private manualCleaningHz: number | null = null;
  setBackendMonitorMuted(muted: boolean, owner?: object): void {
    if (owner) {
      if (muted) this.monitorMuteOwners.add(owner);
      else this.monitorMuteOwners.delete(owner);
    } else this.backendMonitorMuted = muted;
    this.backendCleanPlayback?.setVolume(this.monitorMuted ? 0 : this.state.volume);
    const volume = this.monitorMuted || this.backendCleanActive || this.finderMuted || this.digitalMute ? 0 : this.state.volume;
    this.demodulator?.setVolume(volume); this.owrxPlayback?.setVolume(volume);
  }
  private finderCleaner: FinderCleaner | null = null;
  private finderCleanerTap: (() => void) | null = null;
  private cleanerSpectrum: ReceiverSpectrum | null = null;
  private cleanerSpectrumTap: (() => void) | null = null;
  private readonly signalId = new HybridSignalId();
  private readonly squelchGate = new SquelchGate();
  private readonly afcTracker = new AfcTracker();
  private spectrumBuffer = new Float32Array(FFT_SIZE);
  private knownModes: Record<string, ReturnType<typeof getMode>> = {};
  private stateListeners = new Set<StateListener>();
  private spectrumListeners = new Set<SpectrumListener>();
  private displayListeners = new Set<SpectrumListener>();
  private detailSpectrum: Spectrum | null = null;
  private detailBuffer = new Float32Array(8192);
  private spectrumView = { offsetHz: 0, spanHz: Infinity };
  private lastSquelchEvalMs = 0;
  private lastAfcEvalMs = 0;
  private digitalMute = false;
  private owrx: OpenWebRxClient | null = null;
  private owrxPlayback: OpenWebRxPlayback | null = null;
  private owrxUntap: (() => void) | null = null;
  private owrxSession: OpenWebRxSession | null = null;
  private owrxListeners = new Set<() => void>();
  private owrxSpectrum = new Float32Array(0);
  private owrxExpectedClose = false;
  private owrxLastHdMs = 0;
  private kiwi: KiwiClient | null = null;
  private kiwiSession: KiwiSession | null = null;
  private kiwiListeners = new Set<() => void>();
  private kiwiSpectrum = new Float32Array(0);
  private kiwiExpectedClose = false;
  private sessionEpoch = 0;
  private external: ExternalSource | null = null;
  /** Browser DSP, loaded on first use of an RTL/demo/rtl_tcp source. */
  private dsp: Dsp | null = null;
  /** Audio output prepared inside a user gesture, adopted on attach. */
  private pendingExternalPlayback: OpenWebRxPlayback | null = null;
  private externalSpectrum = new Float32Array(0);
  /** A retune the HackRF hasn't answered yet; older-centre rows are dropped. */
  private externalPending: { hz: number; since: number } | null = null;
  private onSampleClick = () => this.emitSpectrum();
  private diagnosticAudioListeners = new Set<PcmListener>();
  private diagnosticIqListeners = new Set<IqListener>();
  private onTapAudio = (mono: Float32Array, sampleRate: number) => {
    if (this.diagnosticAudioListeners.size > 0) {
      // Local decoder taps use equal-power stereo summing. Diagnostic raw
      // and playback PCM must instead share the same per-channel amplitude.
      const diagnosticMono = this.player ? mono.map((sample) => sample * Math.SQRT1_2) : mono;
      for (const listener of this.diagnosticAudioListeners) listener(diagnosticMono, sampleRate);
    }
    this.transcription.pushAudio(mono, sampleRate);
    this.fsk.pushAudio(mono, sampleRate);
    this.ft8.pushAudio(mono, sampleRate);
    this.morse.pushAudio(mono, sampleRate);
    this.wefax.pushAudio(mono, sampleRate);
  };

  private state: SdrState = {
    connection: "disconnected",
    status: "idle",
    frequencyHz: 93_900_000,
    offsetHz: 0,
    sampleRate: DEFAULT_SAMPLE_RATE,
    scheme: "WBFM",
    volume: 0.8,
  audioNoiseReduction: "off",
  voiceIsolation: false,
  voiceIsolationLoading: false,
  audioEnhancementError: null,
    autoGain: true,
    gainDb: 20,
    bandwidthHz: 150_000,
    squelch: SQUELCH_DEFAULT_DB,
    autoSquelch: false,
    squelchOpen: true,
    afc: false,
    afcLocked: false,
    stereo: true,
    stereoActive: false,
    ppm: 0,
    biasTee: false,
    error: null,
    // Detect after mount to avoid SSR/client hydration mismatch.
    webUsbSupported: false,
  };

  getState(): SdrState {
    return this.state;
  }

  private voiceIsolationEpoch = 0;

  detectCapabilities(): void {
    const audio = restoreAudioEnhancementSettings();
    this.patch({ audioNoiseReduction: audio.noiseReduction, audioSpeechFilter: Boolean(audio.speechFilter) });
    if (audio.voiceIsolation) void this.setVoiceIsolation(true);
    this.patch({ webUsbSupported: isWebUsbSupported() });
  }

  subscribe(listener: StateListener): () => void {
    this.stateListeners.add(listener);
    // Do not call listener synchronously — useSyncExternalStore forbids it.
    return () => this.stateListeners.delete(listener);
  }

  onSpectrum(listener: SpectrumListener, display = false): () => void {
    this.spectrumListeners.add(listener);
    if (display) this.displayListeners.add(listener);
    return () => { this.spectrumListeners.delete(listener); this.displayListeners.delete(listener); };
  }

  setSpectrumView(offsetHz: number, spanHz: number): void {
    this.spectrumView = { offsetHz, spanHz };
    this.detailSpectrum?.setView(offsetHz, spanHz);
  }

  getDiagnosticSnapshot() {
    return { capturedAt: new Date().toISOString(), state: this.state,
      listenFrequencyHz: this.state.frequencyHz + this.state.offsetHz,
      audio: this.backendCleanActive ? this.backendCleanPlayback?.getDiagnostics() ?? null : this.player?.getDiagnostics() ?? this.owrxPlayback?.getDiagnostics() ?? null,
      cleaner: this.finder?.getState().cleaning ?? null,
      backendCleanActive: this.backendCleanActive,
      spectrum: this.spectrum?.getFrameInfo() ?? null,
      view: this.spectrumView,
      capabilities: { audioCapture: Boolean(this.player || this.owrxPlayback), iqCapture: Boolean(this.radio),
        browserDsp: Boolean(this.dsp), microphone: false } };
  }

  /** Demodulated PCM before playback cleanup/volume; does not replace the receiver. */
  onRawAudio(listener: PcmListener): () => void {
    this.diagnosticAudioListeners.add(listener);
    this.syncTapping();
    return () => { this.diagnosticAudioListeners.delete(listener); this.syncTapping(); };
  }

  captureDiagnosticAudio(durationMs = 1_000, maxSamples = 480_000, stage: "raw" | "playback" = "playback") {
    if (this.state.status !== "playing") throw new Error("Start the receiver before capturing audio");
    const player = stage === "playback" && this.backendCleanActive ? this.backendCleanPlayback : this.player ?? this.owrxPlayback;
    if (!player) throw new Error("This receiver has no browser audio stream");
    const state = { ...this.state };
    const subscribe = stage === "playback"
      ? (listener: PcmListener) => player.onPlaybackAudio(listener)
      : (listener: PcmListener) => {
          this.diagnosticAudioListeners.add(listener); this.syncTapping();
          return () => { this.diagnosticAudioListeners.delete(listener); this.syncTapping(); };
        };
    return captureAudioStream(subscribe, durationMs, maxSamples).then((result) => {
      if (this.state.frequencyHz + this.state.offsetHz !== state.frequencyHz + state.offsetHz || this.state.scheme !== state.scheme || this.state.connection !== state.connection) {
        throw new Error("Receiver tuning or source changed during audio capture");
      }
      return { ...result, stage, receiver: state };
    });
  }

  captureDiagnosticIq(durationMs = 250, maxSamples = 262_144) {
    if (!this.radio || this.state.status !== "playing") throw new Error("Native IQ capture requires a playing RTL-SDR, rtl_tcp, or demo receiver");
    return captureIqStream((listener) => {
      this.diagnosticIqListeners.add(listener);
      return () => { this.diagnosticIqListeners.delete(listener); };
    }, durationMs, maxSamples);
  }

  getTranscriptionState(): TranscriptionState {
    return this.transcription.getState();
  }

  subscribeTranscription(listener: TranscriptionListener): () => void {
    return this.transcription.subscribe(listener);
  }

  getTranscriptionPreference(): EnginePreference {
    return this.transcription.getPreference();
  }

  async setTranscriptionPreference(preference: EnginePreference): Promise<void> {
    await this.transcription.setPreference(preference);
    this.syncTapping();
  }

  getTranscriptionOptions(): TranscribeOptions {
    return this.transcription.getOptions();
  }

  setTranscriptionOptions(options: Partial<TranscribeOptions>): void {
    this.transcription.setOptions(options);
  }

  /** Restore saved speech settings (client only). */
  restoreTranscriptionSettings(): void {
    this.transcription.restore();
  }

  /** Transcript events that arrive from a server-side tap (the HackRF audio socket). */
  pushExternalTranscript(event: Parameters<TranscriptionHub["ingestServerEvent"]>[0]): void {
    this.transcription.ingestServerEvent(event);
  }

  async setTranscriptionEnabled(enabled: boolean): Promise<void> {
    await this.transcription.setEnabled(enabled);
    this.syncTapping();
  }

  clearTranscript(): void {
    this.transcription.clear();
  }

  getFskState(): FskState {
    return this.fsk.getState();
  }

  subscribeFsk(listener: FskListener): () => void {
    return this.fsk.subscribe(listener);
  }

  setFskEnabled(enabled: boolean): void {
    this.fsk.setEnabled(enabled);
    this.syncTapping();
  }

  setFskPreset(preset: Exclude<FskPresetId, "custom">): void {
    this.fsk.setPreset(preset);
  }

  patchFskConfig(partial: Partial<FskConfig>): void {
    this.fsk.patchConfig(partial);
  }

  setFskInverted(inverted: boolean): void {
    this.fsk.setInverted(inverted);
  }

  clearFskText(): void {
    this.fsk.clear();
  }

  getFt8State(): Ft8State {
    return this.ft8.getState();
  }

  subscribeFt8(listener: Ft8Listener): () => void {
    return this.ft8.subscribe(listener);
  }

  setFt8Enabled(enabled: boolean): void {
    this.ft8.setEnabled(enabled);
    this.syncTapping();
  }

  patchFt8Config(partial: Partial<Ft8Config>): void {
    this.ft8.patchConfig(partial);
  }

  clearFt8Messages(): void {
    this.ft8.clear();
  }

  getMorseState(): MorseState {
    return this.morse.getState();
  }

  subscribeMorse(listener: MorseListener): () => void {
    return this.morse.subscribe(listener);
  }

  setMorseEnabled(enabled: boolean): void {
    this.morse.setEnabled(enabled);
    this.syncTapping();
  }

  setMorsePreset(preset: Exclude<MorsePresetId, "custom">): void {
    this.morse.setPreset(preset);
  }

  patchMorseConfig(partial: Partial<MorseConfig>): void {
    this.morse.patchConfig(partial);
  }

  clearMorseText(): void {
    this.morse.clear();
  }

  getWefaxState(): WefaxState {
    return this.wefax.getState();
  }

  subscribeWefax(listener: WefaxListener): () => void {
    return this.wefax.subscribe(listener);
  }

  getWefaxImage(): { pixels: Uint8Array; width: number; lines: number } {
    return this.wefax.getImage();
  }

  setWefaxEnabled(enabled: boolean): void {
    this.wefax.setEnabled(enabled);
    this.syncTapping();
  }

  setWefaxPreset(preset: Exclude<WefaxPresetId, "custom">): void {
    this.wefax.setPreset(preset);
  }

  patchWefaxConfig(partial: Partial<WefaxConfig>): void {
    this.wefax.patchConfig(partial);
  }

  setWefaxInverted(inverted: boolean): void {
    this.wefax.setInverted(inverted);
  }

  forceWefaxStart(): void {
    this.wefax.forceStart();
  }

  clearWefaxImage(): void {
    this.wefax.clear();
  }

  getP25State(): P25State {
    return this.p25.getState();
  }

  subscribeP25(listener: P25Listener): () => void {
    return this.p25.subscribe(listener);
  }

  async setP25Enabled(enabled: boolean): Promise<void> {
    this.bindP25Hooks();
    this.p25Iq.setEnabled(enabled);
    await this.p25.setEnabled(enabled);
    if (enabled) {
      this.setScheme("NBFM");
      this.p25Iq.setOffsetHz(this.state.offsetHz);
    } else {
      this.setDigitalMute(false);
    }
  }

  getScannerState(): ScannerState {
    return this.scanner.getState();
  }

  private getFinder(): AutonomousFinder {
    if (!this.finder) {
      const owned = async (work: () => Promise<void>) => {
        this.finderCommandDepth++;
        try { await work(); } finally { this.finderCommandDepth--; }
      };
      this.finder = new AutonomousFinder({
        state: () => this.state,
        spectrum: () => this.finderSpectrum,
        tune: (center, offset) => owned(async () => { await this.setFrequency(center); this.setOffset(offset); }),
        prepare: (bandwidth) => owned(async () => {
          if (this.state.sampleRate !== 1_024_000) await this.setSampleRate(1_024_000);
          this.setScheme("AM"); this.setBandwidth(bandwidth); this.setAutoSquelch(false); this.setSquelch(0); this.setAfc(false);
          this.setAudioSpeechFilter(true); this.setAudioNoiseReduction("low"); await this.setVoiceIsolation(false);
        }),
        mute: (muted) => {
          this.finderMuted = muted;
          const volume = this.digitalMute || muted || this.monitorMuted || this.backendCleanActive ? 0 : this.state.volume;
          this.demodulator?.setVolume(volume); this.owrxPlayback?.setVolume(volume);
        },
        capture: () => this.captureDiagnosticAudio(4000, 192000, "raw"),
        ready: async (signal) => {
          const response = await fetch("/api/autofinder/status", { signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]), cache: "no-store" });
          if (!response.ok || !(await response.json()).available) throw new Error("Local AI speech detector is unavailable. Start the SigSense API to search automatically.");
        },
        assess: async (data, signal) => {
          const bytes = Uint8Array.from(atob(data), (character) => character.charCodeAt(0));
          const response = await fetch("/api/autofinder/assess", { method: "POST", body: bytes, headers: { "Content-Type": "audio/wav" }, signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]) });
          if (!response.ok) throw new Error(`Local AI speech check failed (${response.status}); search stopped.`);
          return await response.json() as SpeechAssessment;
        },
        restore: (original, dial) => owned(async () => {
          if (!dial) return;
          this.setScheme(original.scheme); this.setBandwidth(original.bandwidthHz);
          this.setSquelch(original.squelch); this.setAutoSquelch(original.autoSquelch); this.setAfc(original.afc);
          this.setAudioSpeechFilter(Boolean(original.audioSpeechFilter)); this.setAudioNoiseReduction(original.audioNoiseReduction);
          await this.setVoiceIsolation(original.voiceIsolation);
          if (this.state.sampleRate !== original.sampleRate) await this.setSampleRate(original.sampleRate);
          await this.setFrequency(original.frequencyHz); this.setOffset(original.offsetHz);
          await this.setGain(original.gainDb); await this.setAutoGain(original.autoGain);
        }),
      });
      this.finder.subscribe(() => {
        this.syncFinderCleaner();
        if (!this.finder?.active && this.finder?.getState().status !== "starting") {
          this.finderUntap?.(); this.finderUntap = null;
        }
      });
    }
    return this.finder;
  }

  getAutofinderState(): FinderState { return this.getFinder().getState(); }
  private syncFinderCleaner(): void {
    const finder = this.finder?.getState();
    const target = finder && ["listening", "keeping"].includes(finder.status) ? finder.frequencyHz : this.manualCleaningHz;
    if (target === null || target === undefined) {
      this.finderCleanerTap?.(); this.finderCleanerTap = null;
      this.cleanerSpectrumTap?.(); this.cleanerSpectrumTap = null; this.cleanerSpectrum = null;
      this.finderCleaner?.setTarget(null);
      this.backendCleanActive = false;
      this.backendCleanPlayback?.close(); this.backendCleanPlayback = null;
      this.setBackendMonitorMuted(this.backendMonitorMuted);
      return;
    }
    if (!this.finderCleaner) this.finderCleaner = new FinderCleaner(() => {
      const state = this.state, spectrum = this.cleanerSpectrum;
      const tunedHz = state.frequencyHz + state.offsetHz;
      let detail: Record<string, unknown> | undefined;
      if (spectrum?.startHz !== undefined && spectrum.centerHz === state.frequencyHz && Date.now() - Date.parse(spectrum.capturedAt) < 1200) {
        const from = Math.max(0, Math.floor((tunedHz - 8000 - spectrum.startHz) / spectrum.binHz));
        const to = Math.min(spectrum.values.length, Math.ceil((tunedHz + 8000 - spectrum.startHz) / spectrum.binHz));
        detail = { values: spectrum.values.slice(from, to), startHz: spectrum.startHz + from * spectrum.binHz, binHz: spectrum.binHz };
      }
      return { frequencyHz: target, tunedHz, source: state.connection, gainDb: state.gainDb,
        rfClipFraction: state.rfClipFraction ?? 0, bandwidthHz: state.bandwidthHz, spectrum: detail };
    }, (cleaning) => {
      if (["error", "stopped"].includes(cleaning.status)) {
        this.backendCleanActive = false;
        this.backendCleanPlayback?.close(); this.backendCleanPlayback = null;
        this.setBackendMonitorMuted(this.backendMonitorMuted);
      }
      this.finder?.setCleaning(cleaning);
    }, (advice) => {
      const activeHz = this.finder?.active && ["listening", "keeping"].includes(this.finder.getState().status) ? this.finder.getState().frequencyHz : this.manualCleaningHz;
      if (!activeHz || advice.frequencyHz !== activeHz || this.state.status !== "playing") return;
      this.finderCommandDepth++;
      try {
        if (typeof advice.gainDb === "number" && advice.gainDb >= 0 && advice.gainDb < this.state.gainDb) void this.setGain(advice.gainDb).catch(() => {});
        if (advice.bandwidthHz === 4000 && this.state.bandwidthHz > 4000) this.setBandwidth(4000);
        if (typeof advice.offsetCorrectionHz === "number" && Math.abs(advice.offsetCorrectionHz) <= 250
          && Math.abs(this.state.frequencyHz + this.state.offsetHz + advice.offsetCorrectionHz - activeHz) <= 500) this.setOffset(this.state.offsetHz + advice.offsetCorrectionHz);
      } finally { this.finderCommandDepth--; }
    }, (pcm, rate) => {
      if (typeof location !== "undefined" && new URLSearchParams(location.search).get("receiverAgent") === "1") return;
      if (!this.backendCleanPlayback) this.prepareCleanPlayback();
      this.backendCleanActive = true;
      this.setBackendMonitorMuted(this.backendMonitorMuted);
      this.backendCleanPlayback?.push(pcm, rate);
    });
    this.finderCleaner.setTarget(target);
    if (!this.cleanerSpectrumTap) this.cleanerSpectrumTap = this.onSpectrum((values, center, info) => {
      if (this.cleanerSpectrum && Date.now() - Date.parse(this.cleanerSpectrum.capturedAt) < 400) return;
      const n = values.length, rate = info?.sampleRate ?? this.state.sampleRate, centerHz = center ?? this.state.frequencyHz;
      const centerOffsetHz = info?.centerOffsetHz ?? 0;
      this.cleanerSpectrum = { values: Array.from({ length: n }, (_, i) => values[(i + Math.floor(n / 2)) % n]),
        sampleRate: rate, centerHz, centerOffsetHz, startHz: centerHz + centerOffsetHz - rate / 2,
        binHz: rate / n, capturedAt: new Date().toISOString(), binOrder: "ascending-frequency" };
    });
    if (!this.finderCleanerTap) {
      const tap: PcmListener = (mono, rate) => {
        const current = this.finder?.getState();
        const activeHz = current && ["listening", "keeping"].includes(current.status) ? current.frequencyHz : this.manualCleaningHz;
        if (activeHz && Math.abs(this.state.frequencyHz + this.state.offsetHz - activeHz) < 1000) this.finderCleaner?.push(mono, rate);
      };
      this.diagnosticAudioListeners.add(tap); this.syncTapping();
      this.finderCleanerTap = () => { this.diagnosticAudioListeners.delete(tap); this.syncTapping(); };
    }
  }
  private prepareCleanPlayback(): void {
    if (typeof location !== "undefined" && new URLSearchParams(location.search).get("receiverAgent") === "1") return;
    this.backendCleanPlayback ??= new OpenWebRxPlayback(new AudioEnhancer(() => ({ noiseReduction: "off", voiceIsolation: false })));
    this.backendCleanPlayback.unlock();
    this.backendCleanPlayback.setVolume(this.monitorMuted ? 0 : this.state.volume);
  }
  startSignalCleaning(frequencyHz: number): void {
    if (this.state.status !== "playing" || this.state.scheme !== "AM") return;
    this.getFinder(); this.prepareCleanPlayback();
    this.manualCleaningHz = frequencyHz; this.syncFinderCleaner();
  }
  private cancelManualCleaning(): void {
    if (!this.finderCommandDepth && this.manualCleaningHz !== null) { this.manualCleaningHz = null; this.syncFinderCleaner(); }
  }
  subscribeAutofinder(listener: () => void): () => void { return this.getFinder().subscribe(listener); }
  async startAutofinder(maxListenMs?: number): Promise<void> {
    this.manualCleaningHz = null;
    this.prepareCleanPlayback();
    if (this.getFinder().active) return;
    this.scanner.stop();
    if (this.state.status !== "playing") {
      const host = typeof location === "undefined" ? "127.0.0.1" : location.hostname;
      await this.connectRtlServer(`http://${host}:8081`);
    }
    if (this.state.status !== "playing") throw new Error(this.state.error ?? "Could not connect to RTL-SDR");
    if (!["rtltcp", "device", "demo"].includes(this.state.connection)) throw new Error("Automatic HF search currently requires an RTL-SDR receiver");
    this.finderUntap?.();
    this.finderSpectrum = null;
    this.finderUntap = this.onSpectrum((spectrum, center, info) => {
      if (this.finderSpectrum && Date.now() - Date.parse(this.finderSpectrum.capturedAt) < 150) return;
      const rate = info?.sampleRate ?? this.state.sampleRate, n = spectrum.length;
      const centerHz = center ?? this.state.frequencyHz, offset = info?.centerOffsetHz ?? 0;
      this.finderSpectrum = { values: Array.from({ length: n }, (_, i) => spectrum[(i + Math.floor(n / 2)) % n]),
        centerHz, centerOffsetHz: offset, startHz: centerHz + offset - rate / 2,
        sampleRate: rate, binHz: rate / n, binOrder: "ascending-frequency", capturedAt: new Date().toISOString() };
    });
    try { this.getFinder().start(maxListenMs); } catch (error) { this.finderUntap?.(); this.finderUntap = null; throw error; }
  }
  stopAutofinder(keep = false): Promise<void> { return this.getFinder().stop(keep); }
  skipAutofinder(): void { this.getFinder().skip(); }
  keepAutofinder(): void { this.getFinder().keep(); }
  resumeAutofinder(): void { this.getFinder().resume(); }

  subscribeScanner(listener: ScannerListener): () => void {
    return this.scanner.subscribe(listener);
  }

  async startScanner(): Promise<void> {
    await this.finder?.stop();
    this.bindScannerHooks();
    await this.scanner.start();
  }

  stopScanner(): void {
    this.scanner.stop();
  }

  pauseScanner(): void {
    this.scanner.pause();
  }

  resumeScanner(): void {
    this.bindScannerHooks();
    this.scanner.resume();
  }

  async skipScanner(): Promise<void> {
    this.bindScannerHooks();
    await this.scanner.skip();
  }

  holdScanner(): void {
    this.scanner.hold();
  }

  patchScannerConfig(partial: Partial<ScannerConfig>): void {
    this.scanner.patchConfig(partial);
  }

  setScannerBank(bankId: string): void {
    this.scanner.setActiveBank(bankId);
  }

  toggleScannerLockout(channelId: string): void {
    this.scanner.toggleLockout(channelId);
  }

  addScannerChannel(input: {
    label: string;
    frequencyHz: number;
    scheme: DemodScheme;
    bandwidthHz?: number;
    bankId?: string;
  }): ScanChannel | null {
    return this.scanner.addChannel(input);
  }

  removeScannerChannel(channelId: string): void {
    this.scanner.removeChannel(channelId);
  }

  resetScannerBanks(): void {
    this.scanner.resetBuiltinBanks();
  }

  /** Tune to a memory channel without starting a scan. */
  async tuneScannerChannel(channel: ScanChannel): Promise<void> {
    this.setScheme(channel.scheme);
    if (channel.bandwidthHz != null) {
      this.setBandwidth(channel.bandwidthHz);
    }
    this.setOffset(0);
    await this.setFrequency(channel.frequencyHz);
  }

  patchP25Config(partial: Partial<P25Config>): void {
    this.p25.patchConfig(partial);
  }

  clearP25Messages(): void {
    this.p25.clearMessages();
  }

  clearP25Calls(): void {
    this.p25.clearCalls();
  }

  getSignalIdState(): SignalIdState {
    return this.signalId.getState();
  }

  subscribeSignalId(listener: SignalIdListener): () => void {
    return this.signalId.subscribe(listener);
  }

  setSignalIdEnabled(enabled: boolean): void {
    this.signalId.setEnabled(enabled);
  }

  getSignalIdSensitivity(): SignalIdSensitivity {
    return this.signalId.getSensitivity();
  }

  setSignalIdSensitivity(sensitivity: SignalIdSensitivity): void {
    this.signalId.setSensitivity(sensitivity);
  }

  /** Tunes to an identified signal, switching demodulator to match its class. */
  tuneToSignal(offsetHz: number, scheme: DemodScheme | null): void {
    if (scheme) this.setScheme(scheme);
    this.setOffset(offsetHz);
  }

  getSchemes(): string[] {
    return getSchemes();
  }

  /** Loads the DSP; reports a plain message where WASM relaxed SIMD is missing. */
  private async ensureDsp(): Promise<Dsp | null> {
    if (this.dsp) return this.dsp;
    try {
      this.dsp = await loadDsp();
      return this.dsp;
    } catch {
      this.patch({ status: "error", error: DSP_UNSUPPORTED_MESSAGE });
      return null;
    }
  }

  async connectDevice(): Promise<void> {
    if (!isWebUsbSupported()) {
      this.patch({
        error:
          "WebUSB is not available. Use Chrome, Edge, or Opera on desktop or Android.",
      });
      return;
    }
    const dsp = await this.ensureDsp();
    if (!dsp) return;
    await this.startWithProvider(new dsp.RtlProvider(), "device");
  }

  async connectDemo(): Promise<void> {
    const dsp = await this.ensureDsp();
    if (!dsp) return;
    const fake = new dsp.FakeRtlDeviceProvider(dsp.createDemoGenerator());
    await this.startWithProvider(new dsp.RtlProvider(fake), "demo");
  }

  /**
   * Connect to an RTL server (WebSocket bridge → rtl_tcp). Streams raw IQ so
   * demodulation and P25 run in the browser, same as WebUSB.
   */
  async connectRtlServer(address: string): Promise<void> {
    let wsUrl: string;
    try {
      wsUrl = rtlServerWebSocketUrl(address);
    } catch (err) {
      this.patch({
        status: "error",
        error: err instanceof Error ? err.message : String(err),
      });
      return;
    }
    const mixed = rtlServerMixedContentMessage(wsUrl);
    if (mixed) {
      this.patch({ status: "error", error: mixed });
      return;
    }

    const epoch = this.sessionEpoch;
    this.patch({ status: "starting", error: null });
    const dsp = await this.ensureDsp();
    if (!dsp || epoch !== this.sessionEpoch) return;
    try {
      // The unattended receiver shares the single-client RTL bridge with this browser.
      if (typeof location !== "undefined" && new URLSearchParams(location.search).get("receiverAgent") !== "1") {
        const response = await fetch("/api/autofinder-service", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "release" }), signal: AbortSignal.timeout(40_000) });
        // An offline supervisor does not prevent normal standalone receiver use.
        if (!response.ok && response.status !== 503 && response.status !== 404) {
          const body = await response.json().catch(() => null);
          throw new Error(`Could not release the background receiver: ${body?.error ?? `HTTP ${response.status}`}`);
        }
      }
    } catch (error) {
      if (epoch === this.sessionEpoch) this.patch({ status: "error", error: errorMessage(error) });
      return;
    }
    // A Stop or another connection while DSP/handoff was pending owns the receiver now.
    if (epoch !== this.sessionEpoch) return;
    const provider = new RtlTcpDeviceProvider(wsUrl, (metrics, gainDb) => {
      this.patch({ rfClipFraction: metrics.railFraction, ...(gainDb === null ? {} : { gainDb }) });
    });
    await this.startWithProvider(new dsp.RtlProvider(provider), "rtltcp");
  }

  /**
   * Connect to an OpenWebRX+ server. The server owns the radio and demodulator;
   * this app tunes it and renders the waterfall and audio it streams back.
   */
  async connectOpenWebRx(address: string): Promise<void> {
    let wsUrl: string;
    try {
      wsUrl = openWebRxWebSocketUrl(address);
    } catch (err) {
      this.patch({
        status: "error",
        error: err instanceof Error ? err.message : String(err),
      });
      return;
    }
    const mixed = openWebRxMixedContentMessage(wsUrl);
    if (mixed) {
      this.patch({ status: "error", error: mixed });
      return;
    }

    const playback = new OpenWebRxPlayback();
    playback.unlock();
    playback.setVolume(this.state.volume);
    playback.setTapping(this.wantAudioTap());

    await this.stop();
    const epoch = this.sessionEpoch;
    this.owrxExpectedClose = false;
    this.owrxLastHdMs = 0;
    this.owrxPlayback = playback;
    this.owrxUntap = playback.onAudio(this.onTapAudio);
    this.setOwrxSession({
      url: address.trim(),
      receiverName: null,
      profiles: [],
      profileId: null,
      allowsCenterChanges: false,
    });

    for (const scheme of getSchemes()) {
      this.knownModes[scheme] = getMode(scheme);
    }

    const client = new OpenWebRxClient({
      onSpectrum: (shifted, centerHz, sampleRate) => {
        if (epoch !== this.sessionEpoch) return;
        this.onOpenWebRxSpectrum(shifted, centerHz, sampleRate);
      },
      onAudio: (pcm, sampleRate, hd) => {
        if (epoch !== this.sessionEpoch) return;
        this.onOpenWebRxAudio(pcm, sampleRate, hd);
      },
      onConfig: (config, resetTuning) => {
        if (epoch !== this.sessionEpoch) return;
        this.onOpenWebRxConfig(config, resetTuning);
      },
      onProfiles: (profiles) => {
        if (epoch !== this.sessionEpoch || !this.owrxSession) return;
        this.setOwrxSession({ ...this.owrxSession, profiles });
      },
      onReceiverName: (receiverName) => {
        if (epoch !== this.sessionEpoch || !this.owrxSession) return;
        this.setOwrxSession({ ...this.owrxSession, receiverName });
      },
      onServerError: (message) => {
        if (epoch !== this.sessionEpoch) return;
        this.patch({ error: message });
      },
      onClose: () => {
        if (epoch !== this.sessionEpoch || this.owrxExpectedClose) return;
        if (this.state.status === "starting") return;
        this.owrxExpectedClose = true;
        this.teardownOpenWebRx();
        this.patch({
          connection: "disconnected",
          status: "error",
          error: "OpenWebRX+ closed the connection.",
          stereoActive: false,
        });
      },
    });

    this.owrx = client;
    this.patch({
      status: "starting",
      error: null,
      connection: "openwebrx",
    });

    try {
      await client.connect(wsUrl);
    } catch (err) {
      if (epoch !== this.sessionEpoch) {
        playback.close();
        return;
      }
      this.owrxExpectedClose = true;
      this.teardownOpenWebRx();
      this.patch({
        connection: "disconnected",
        status: "error",
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  selectOpenWebRxProfile(profileId: string): void {
    if (!this.owrx || !this.owrxSession) return;
    this.setOwrxSession({ ...this.owrxSession, profileId });
    this.signalId.reset();
    this.owrx.selectProfile(profileId);
  }

  getOpenWebRxSession(): OpenWebRxSession | null {
    return this.owrxSession;
  }

  subscribeOpenWebRx(listener: () => void): () => void {
    this.owrxListeners.add(listener);
    return () => this.owrxListeners.delete(listener);
  }

  /**
   * Connect to a KiwiSDR. The server owns the radio and demodulator;
   * this app tunes it and renders the waterfall and audio it streams back.
   */
  async connectKiwi(address: string, password = ""): Promise<void> {
    const playback = new OpenWebRxPlayback();
    playback.unlock();
    playback.setVolume(this.state.volume);
    playback.setTapping(this.wantAudioTap());

    await this.stop();
    const epoch = this.sessionEpoch;
    this.kiwiExpectedClose = false;
    this.owrxPlayback = playback;
    this.owrxUntap = playback.onAudio(this.onTapAudio);
    this.setKiwiSession({
      url: address.trim(),
      receiverName: null,
      zoom: KIWI_DEFAULT_ZOOM,
      maxFreqKhz: 30_000,
    });

    for (const scheme of getSchemes()) {
      this.knownModes[scheme] = getMode(scheme);
    }

    const client = new KiwiClient({
      onSpectrum: (shifted, centerHz, sampleRate) => {
        if (epoch !== this.sessionEpoch) return;
        this.onKiwiSpectrum(shifted, centerHz, sampleRate);
      },
      onAudio: (pcm, sampleRate) => {
        if (epoch !== this.sessionEpoch) return;
        this.owrxPlayback?.push(pcm, sampleRate);
      },
      onConfig: (config) => {
        if (epoch !== this.sessionEpoch || !this.kiwiSession) return;
        this.setKiwiSession({
          ...this.kiwiSession,
          receiverName: config.receiverName ?? this.kiwiSession.receiverName,
          maxFreqKhz: config.maxFreqKhz,
        });
      },
      onReady: () => {
        if (epoch !== this.sessionEpoch) return;
        const maxHz = (this.kiwiSession?.maxFreqKhz ?? 30_000) * 1_000;
        const span = client.getSpectrumSpanHz();
        let center = this.state.frequencyHz;
        if (center <= 0 || center > maxHz) center = 7_100_000;
        client.setWaterfallCenter(center);
        this.patch({
          connection: "kiwi",
          status: "playing",
          frequencyHz: center,
          sampleRate: span,
          offsetHz: 0,
          scheme: this.state.scheme === "WBFM" ? "AM" : this.state.scheme,
          bandwidthHz:
            this.state.scheme === "WBFM"
              ? DEFAULT_BANDWIDTH_HZ.AM
              : this.state.bandwidthHz,
          error: null,
        });
        this.pushKiwiTuning();
      },
      onServerError: (message) => {
        if (epoch !== this.sessionEpoch) return;
        this.patch({ error: message });
      },
      onClose: () => {
        if (epoch !== this.sessionEpoch || this.kiwiExpectedClose) return;
        if (this.state.status === "starting") return;
        this.kiwiExpectedClose = true;
        this.teardownKiwi();
        this.patch({
          connection: "disconnected",
          status: "error",
          error: "KiwiSDR closed the connection.",
          stereoActive: false,
        });
      },
    });

    this.kiwi = client;
    this.patch({
      status: "starting",
      error: null,
      connection: "kiwi",
    });

    try {
      await client.connect(address, password);
    } catch (err) {
      if (epoch !== this.sessionEpoch) {
        playback.close();
        return;
      }
      this.kiwiExpectedClose = true;
      this.teardownKiwi();
      this.patch({
        connection: "disconnected",
        status: "error",
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  getKiwiSession(): KiwiSession | null {
    return this.kiwiSession;
  }

  subscribeKiwi(listener: () => void): () => void {
    this.kiwiListeners.add(listener);
    return () => this.kiwiListeners.delete(listener);
  }

  /**
   * Create the audio output now, while the click that starts a connection is
   * still a user gesture; browsers keep a later-created context suspended.
   */
  prepareExternalAudio(): void {
    this.pendingExternalPlayback?.close();
    const playback = new OpenWebRxPlayback();
    playback.unlock();
    playback.setVolume(this.state.volume);
    this.pendingExternalPlayback = playback;
  }

  /** Backend identification of the external source's bands. */
  pushExternalTracks(
    tracks: BackendTrack[],
    noiseFloorDb: number,
    serverNow: number,
  ): void {
    if (!this.external) return;
    this.signalId.setExternalTracks(tracks, {
      centerHz: this.state.frequencyHz,
      noiseFloorDb,
      serverNow,
    });
  }

  /** Show a user's correction on a signal now, ahead of the next backend update. */
  applySignalCorrection(trackId: string, patch: SignalCorrectionPatch): void {
    this.signalId.applyCorrection(trackId, patch);
  }

  /** Demodulated PCM from an external source (mono Int16). */
  pushExternalAudio(pcm: Int16Array, sampleRate: number): void {
    if (!this.external) return;
    this.owrxPlayback?.push(pcm, sampleRate);
  }

  /** Adopt an externally owned radio (HackRF) as the active source. */
  async attachExternal(
    source: ExternalSource,
    init: { frequencyHz: number; sampleRate: number },
  ): Promise<void> {
    await this.stop();
    this.sessionEpoch += 1;
    this.signalId.reset();
    this.external = source;
    this.transcription.setTap((config) => this.external?.onTranscribe?.(config));
    // The backend identifies HackRF bands from its own IQ; the browser stops.
    this.signalId.setExternalMode(true);
    // Reuse the OpenWebRX output slot: volume, squelch and the decoder audio
    // tap already drive it.
    const playback = this.pendingExternalPlayback ?? new OpenWebRxPlayback();
    this.pendingExternalPlayback = null;
    playback.setVolume(this.state.volume);
    playback.setTapping(this.wantAudioTap());
    this.owrxPlayback = playback;
    this.owrxUntap = playback.onAudio(this.onTapAudio);
    for (const scheme of getSchemes()) {
      this.knownModes[scheme] ??= getMode(scheme);
    }
    this.patch({
      connection: "hackrf",
      status: "playing",
      error: null,
      frequencyHz: init.frequencyHz,
      sampleRate: init.sampleRate,
      offsetHz: 0,
      stereoActive: false,
    });
  }

  detachExternal(): void {
    if (!this.external) return;
    this.external = null;
    this.externalPending = null;
    this.transcription.setTap(null);
    this.signalId.setExternalMode(false);
    this.externalSpectrum = new Float32Array(0);
    this.owrxUntap?.();
    this.owrxUntap = null;
    this.owrxPlayback?.close();
    this.owrxPlayback = null;
    this.squelchGate.reset();
    this.afcTracker.reset();
    this.lastSquelchEvalMs = 0;
    this.lastAfcEvalMs = 0;
    this.signalId.reset();
    this.patch({
      connection: "disconnected",
      status: "idle",
      afcLocked: false,
    });
  }

  /**
   * Feed one spectrum row. `bins` is ascending in frequency (low edge first);
   * the controller stores raw FFT order (DC at index 0), so it is shifted here.
   */
  pushExternalSpectrum(
    bins: ArrayLike<number | null>,
    startHz: number,
    binHz: number,
    sampleRateHz?: number,
  ): void {
    if (!this.external) return;
    const n = bins.length & ~1;
    if (n < 2) return;
    if (this.externalSpectrum.length !== n) {
      this.externalSpectrum = new Float32Array(n);
    }
    const half = n >> 1;
    for (let i = 0; i < n; i++) {
      this.externalSpectrum[(i + half) % n] = bins[i] ?? -200;
    }
    const centerHz = startHz + binHz * half;
    const pending = this.externalPending;
    if (pending) {
      const arrived = Math.abs(centerHz - pending.hz) <= Math.max(2 * binHz, 2_000);
      const timedOut = performance.now() - pending.since > 4_000;
      if (arrived || timedOut) this.externalPending = null;
      else return; // still the old centre: it would plot on the wrong axis
    }
    // Prefer the tuner's rate: bin width times count jitters by rounding.
    const sampleRate = Math.round(sampleRateHz ?? binHz * n);
    if (
      Math.abs(centerHz - this.state.frequencyHz) > 1 ||
      sampleRate !== this.state.sampleRate
    ) {
      this.patch({ frequencyHz: Math.round(centerHz), sampleRate });
    }
    if (this.squelchActive()) this.evaluateSquelch(this.externalSpectrum);
    if (this.afcActive()) this.evaluateAfc(this.externalSpectrum);
    if (this.signalId.getState().enabled) {
      this.signalId.pushSpectrum(
        this.externalSpectrum,
        centerHz,
        sampleRate,
        this.state.frequencyHz,
      );
    }
    for (const listener of this.spectrumListeners) {
      listener(this.externalSpectrum, centerHz);
    }
  }

  async stop(): Promise<void> {
    this.manualCleaningHz = null;
    this.finderCleaner?.setTarget(null);
    this.backendCleanActive = false;
    this.backendCleanPlayback?.close(); this.backendCleanPlayback = null;
    await this.finder?.stop(false, true);
    this.sessionEpoch += 1;
    this.scanner.stop();
    if (this.external) {
      const source = this.external;
      this.detachExternal();
      source.onStop?.();
      return;
    }
    if (this.kiwi) {
      this.patch({ status: "stopping", error: null });
      this.kiwiExpectedClose = true;
      this.teardownKiwi();
      this.patch({
        connection: "disconnected",
        status: "idle",
        stereoActive: false,
      });
      return;
    }
    if (this.owrx || this.owrxPlayback) {
      this.patch({ status: "stopping", error: null });
      this.owrxExpectedClose = true;
      this.teardownOpenWebRx();
      this.patch({
        connection: "disconnected",
        status: "idle",
        stereoActive: false,
      });
      return;
    }
    if (!this.radio) {
      // RTL startup can still be loading DSP or waiting for receiver handoff.
      if (this.state.status === "starting") this.patch({ status: "idle", connection: "disconnected", error: null });
      return;
    }
    this.patch({ status: "stopping", error: null });
    try {
      await this.radio.stop();
    } catch (err) {
      this.patch({ status: "error", error: errorMessage(err) });
      return;
    }
    this.teardown();
    this.patch({
      connection: "disconnected",
      status: "idle",
      stereoActive: false,
    });
  }

  async setFrequency(hz: number): Promise<void> {
    this.cancelManualCleaning();
    if (this.finder?.active && !this.finderCommandDepth) void this.finder.stop(false, true);
    const maxHz =
      this.owrx || this.kiwi || this.external ? 6_000_000_000 : 1_766_000_000;
    const frequencyHz = clamp(Math.round(hz), 0, maxHz);
    if (this.external) {
      // Show the new centre now and hold the display until rows arrive from it,
      // rather than waiting out the stream restart with a stale axis.
      this.externalPending = { hz: frequencyHz, since: performance.now() };
      this.patch({ frequencyHz });
      this.signalId.reset();
      this.external.onTune?.(frequencyHz);
      return;
    }
    if (this.kiwi) {
      this.patch({ frequencyHz });
      this.signalId.reset();
      this.kiwi.setWaterfallCenter(frequencyHz);
      this.pushKiwiTuning();
      if (this.kiwiSession) {
        this.setKiwiSession({ ...this.kiwiSession, zoom: this.kiwi.getZoom() });
      }
      return;
    }
    if (this.owrx) {
      const previous = this.state.frequencyHz;
      if (!this.owrxSession?.allowsCenterChanges) {
        if (Math.abs(frequencyHz - previous) > 1) {
          this.patch({
            error:
              "This OpenWebRX+ profile keeps a fixed center frequency. Tune inside the spectrum window, or enable center changes on the server.",
          });
        }
        return;
      }
      this.patch({ frequencyHz });
      this.signalId.reset();
      this.owrx.setCenterFrequency(frequencyHz);
      return;
    }
    this.patch({ frequencyHz });
    // Retuning invalidates every tracked signal and the frame history behind it.
    this.signalId.reset();
    if (this.radio) await this.radio.setFrequency(frequencyHz);
  }

  async nudgeFrequency(deltaHz: number): Promise<void> {
    await this.setFrequency(this.state.frequencyHz + deltaHz);
  }

  /**
   * Change the capture sample rate (spectrum width). webrtlsdr only applies
   * the rate on start, so a live session is briefly stopped and restarted.
   */
  async setSampleRate(requestedHz: number): Promise<void> {
    const hz = requestedHz;
    if (this.external) {
      const hz = clamp(Math.round(requestedHz), 2_000_000, 20_000_000);
      this.patch({ sampleRate: hz, offsetHz: 0, error: null });
      this.external.onSampleRate?.(hz);
      return;
    }
    if (this.owrx) {
      this.patch({
        error: "Spectrum width comes from the OpenWebRX+ profile.",
      });
      return;
    }
    if (this.kiwi) {
      const maxKhz = this.kiwiSession?.maxFreqKhz ?? 30_000;
      const zoom = kiwiZoomForSpanHz(requestedHz, maxKhz);
      const span = kiwiZoomSpanHz(zoom, maxKhz);
      const half = span / 2;
      this.kiwi.setZoom(zoom);
      this.patch({
        sampleRate: span,
        offsetHz: clamp(this.state.offsetHz, -half, half),
        error: null,
      });
      if (this.kiwiSession) {
        this.setKiwiSession({ ...this.kiwiSession, zoom });
      }
      this.signalId.reset();
      this.pushKiwiTuning();
      return;
    }
    const requested = nearestSampleRate(hz);
    if (requested === this.state.sampleRate) return;

    const half = requested / 2;
    const offsetHz = clamp(this.state.offsetHz, -half, half);
    this.patch({ sampleRate: requested, offsetHz, error: null });
    this.signalId.reset();
    this.demodulator?.setFrequencyOffset(offsetHz);

    if (!this.radio) return;

    const wasPlaying = this.radio.isPlaying();
    try {
      if (wasPlaying) {
        this.patch({ status: "starting" });
        await this.radio.stop();
      }
      await this.radio.setSampleRate(requested);
      if (wasPlaying) {
        await this.radio.start();
        const actual = this.radio.getSampleRate();
        const halfActual = actual / 2;
        this.patch({
          sampleRate: actual,
          offsetHz: clamp(offsetHz, -halfActual, halfActual),
          status: "playing",
        });
        this.demodulator?.setFrequencyOffset(this.state.offsetHz);
        this.p25Iq.setSampleRate(actual);
        this.p25Iq.setOffsetHz(this.state.offsetHz);
      }
    } catch (err) {
      this.patch({
        status: "error",
        error: errorMessage(err),
      });
    }
  }

  setOffset(hz: number): void {
    this.cancelManualCleaning();
    if (this.finder?.active && !this.finderCommandDepth) void this.finder.stop(false, true);
    const half = this.state.sampleRate / 2;
    const offsetHz = clamp(Math.round(hz), -half, half);
    this.patch({ offsetHz });
    this.demodulator?.setFrequencyOffset(offsetHz);
    this.p25Iq.setOffsetHz(offsetHz);
    if (this.owrx) this.pushOpenWebRxTuning(false);
    if (this.kiwi) this.pushKiwiTuning();
    this.notifyExternalDemod();
  }

  /**
   * Tune the demodulator to an absolute frequency. Uses the offset when the
   * target sits comfortably inside the current sample window; otherwise retunes
   * the LO and clears the offset.
   */
  tuneToHz(absoluteHz: number): void {
    const target = Math.round(absoluteHz);
    const half = this.state.sampleRate / 2;
    const offset = target - this.state.frequencyHz;
    if (Math.abs(offset) <= half * 0.92) {
      this.setOffset(offset);
      return;
    }
    void this.setFrequency(target).then(() => {
      // Fixed-center remote receivers may reject the retune. Keep their
      // existing listen offset instead of silently jumping to the old center.
      if (this.state.frequencyHz === target) this.setOffset(0);
    });
  }

  /** Retune the LO so `absoluteHz` sits at center and clear the offset. */
  centerOnHz(absoluteHz: number): void {
    void this.setFrequency(Math.round(absoluteHz)).then(() => this.setOffset(0));
  }

  /**
   * Slide the LO by `deltaHz` while keeping the absolute listen frequency
   * stable (offset absorbs the opposite of the LO move).
   */
  async panFrequency(deltaHz: number): Promise<void> {
    if (deltaHz === 0) return;
    const listenHz = this.state.frequencyHz + this.state.offsetHz;
    await this.setFrequency(this.state.frequencyHz + deltaHz);
    this.setOffset(listenHz - this.state.frequencyHz);
  }

  setVolume(volume: number): void {
    const v = clamp(volume, 0, 1);
    this.patch({ volume: v });
    this.backendCleanPlayback?.setVolume(this.monitorMuted ? 0 : v);
    if (!this.digitalMute && !this.finderMuted && !this.monitorMuted && !this.backendCleanActive) {
      this.demodulator?.setVolume(v);
      this.owrxPlayback?.setVolume(v);
    }
  }

  setAudioNoiseReduction(level: AudioNoiseReduction): void {
    setAudioEnhancementSettings({ noiseReduction: level });
    this.patch({ audioNoiseReduction: level });
  }

  setAudioSpeechFilter(enabled: boolean): void {
    setAudioEnhancementSettings({ speechFilter: enabled });
    this.patch({ audioSpeechFilter: enabled });
  }

  async setVoiceIsolation(enabled: boolean): Promise<void> {
    const epoch = ++this.voiceIsolationEpoch;
    setAudioEnhancementSettings({ voiceIsolation: enabled });
    this.patch({ voiceIsolation: enabled, voiceIsolationLoading: enabled, audioEnhancementError: null });
    if (!enabled) return;
    try {
      await loadVoiceIsolation();
      if (epoch === this.voiceIsolationEpoch) this.patch({ voiceIsolationLoading: false });
    } catch (error) {
      if (epoch !== this.voiceIsolationEpoch) return;
      setAudioEnhancementSettings({ voiceIsolation: false });
      this.patch({ voiceIsolation: false, voiceIsolationLoading: false,
        audioEnhancementError: error instanceof Error ? error.message : "Voice isolation could not load" });
    }
  }

  async setAutoGain(auto: boolean): Promise<void> {
    this.patch({ autoGain: auto });
    if (this.external) return; // HackRF stages are set from the gain control
    if (!this.radio) return;
    await this.radio.setGain(auto ? null : this.state.gainDb);
  }

  async setGain(db: number): Promise<void> {
    const gainDb = clamp(db, 0, 50);
    this.patch({ gainDb, autoGain: false });
    if (this.external) return;
    if (this.radio) await this.radio.setGain(gainDb);
  }

  setScheme(scheme: DemodScheme): void {
    this.cancelManualCleaning();
    if (this.finder?.active && !this.finderCommandDepth) void this.finder.stop(false, true);
    this.afcTracker.reset();
    if (!this.dsp) {
      // No browser DSP (HackRF / OpenWebRX only): modes are registered by the DSP
      // modules, so getMode() would throw. The server or remote does the demodulating;
      // keep the same default widths the DSP would use.
      this.patch({
        scheme,
        bandwidthHz: DEFAULT_BANDWIDTH_HZ[scheme],
        stereo: false,
        afcLocked: false,
      });
      if (this.owrx) this.pushOpenWebRxTuning(false);
      if (this.kiwi) this.pushKiwiTuning();
      this.notifyExternalDemod();
      return;
    }
    if (!this.knownModes[scheme]) {
      this.knownModes[scheme] = getMode(scheme);
    }
    const mode = this.knownModes[scheme];
    this.demodulator?.setMode(mode);
    const params = modeParameters(mode);
    // Keep app squelch settings; library squelch stays at 0 (we gate in the player).
    if (params.hasSquelch()) {
      const cleared = params.setSquelch(0);
      this.demodulator?.setMode(cleared.mode);
      this.knownModes[scheme] = cleared.mode;
    }
    this.patch({
      scheme,
      bandwidthHz: params.getBandwidth(),
      stereo: params.hasStereo() ? params.getStereo() : false,
      afcLocked: false,
    });
    if (this.owrx) this.pushOpenWebRxTuning(false);
    if (this.kiwi) this.pushKiwiTuning();
    this.notifyExternalDemod();
  }

  setBandwidth(hz: number): void {
    this.cancelManualCleaning();
    if (this.finder?.active && !this.finderCommandDepth) void this.finder.stop(false, true);
    if (!this.dsp) {
      if (this.state.scheme === "WBFM") return; // fixed width, as in the DSP
      this.patch({ bandwidthHz: clamp(Math.round(hz), 50, 200_000) });
      if (this.owrx) this.pushOpenWebRxTuning(false);
      if (this.kiwi) this.pushKiwiTuning();
      this.notifyExternalDemod();
      return;
    }
    const scheme = this.state.scheme;
    const base =
      this.demodulator?.getMode() ??
      this.knownModes[scheme] ??
      getMode(scheme);
    const params = modeParameters(base);
    if (!params.hasBandwidth()) return;
    const next = params.setBandwidth(hz);
    this.knownModes[next.mode.scheme] = next.mode;
    this.demodulator?.setMode(next.mode);
    this.patch({ bandwidthHz: next.getBandwidth() });
    if (this.owrx) this.pushOpenWebRxTuning(false);
    if (this.kiwi) this.pushKiwiTuning();
    this.notifyExternalDemod();
  }

  setSquelch(level: number): void {
    const squelch = clamp(Math.round(level), SQUELCH_MIN_DB, SQUELCH_MAX_DB);
    this.patch({ squelch });
    if (!this.squelchActive()) {
      this.forceSquelchOpen();
    }
  }

  setAutoSquelch(enabled: boolean): void {
    this.patch({ autoSquelch: enabled });
    this.squelchGate.reset();
    if (!enabled && this.state.squelch <= 0) {
      this.forceSquelchOpen();
    } else if (enabled && this.state.squelch <= 0) {
      // Give auto a usable default margin when the slider was off.
      this.patch({ squelch: AUTO_SQUELCH_DEFAULT_MARGIN_DB });
    }
  }

  /**
   * Enable continuous auto-centering for AM / NBFM / WBFM. SSB and CW are
   * left alone — their carriers are suppressed or offset on purpose.
   */
  setAfc(enabled: boolean): void {
    this.afcTracker.reset();
    this.patch({ afc: enabled, afcLocked: false });
  }

  /**
   * One-shot: snap the tune offset onto the nearest AM / FM / WFM peak in the
   * current spectrum. Returns true when a centre was found and applied.
   */
  centerOnSignal(buffer?: Float32Array): boolean {
    if (!isAfcScheme(this.state.scheme)) return false;
    const spectrum = buffer ?? this.latestSpectrumBuffer();
    if (!spectrum || spectrum.length === 0) return false;
    const estimate = this.afcTracker.snap(
      spectrum,
      this.state.sampleRate,
      this.state.offsetHz,
      this.state.scheme,
    );
    if (!estimate) {
      this.patch({ afcLocked: false });
      return false;
    }
    this.setOffset(Math.round(estimate.offsetHz));
    this.patch({ afcLocked: true });
    return true;
  }

  setStereo(stereo: boolean): void {
    if (!this.demodulator) return;
    const params = modeParameters(this.demodulator.getMode());
    if (!params.hasStereo()) return;
    const next = params.setStereo(stereo);
    this.demodulator.setMode(next.mode);
    this.knownModes[next.mode.scheme] = next.mode;
    this.patch({ stereo });
  }

  async setPpm(ppm: number): Promise<void> {
    const value = clamp(Math.round(ppm), -200, 200);
    this.patch({ ppm: value });
    if (this.radio) await this.radio.setFrequencyCorrection(value);
  }

  async setBiasTee(enabled: boolean): Promise<void> {
    this.patch({ biasTee: enabled });
    if (this.radio) await this.radio.enableBiasTee(enabled);
  }

  private async startWithProvider(
    provider: RtlProvider,
    connection: ConnectionMode,
  ): Promise<void> {
    const dsp = this.dsp;
    if (!dsp) return; // the connect* callers load it first
    await this.stop();
    this.patch({ status: "starting", error: null, connection });

    const player = new TappingPlayer();
    player.setTapping(this.wantAudioTap());
    this.untap = player.onAudio(this.onTapAudio);

    const demodulator = new dsp.Demodulator({ player });
    const fftSize = this.state.frequencyHz < 30_000_000 ? 8192 : FFT_SIZE;
    const spectrum = new dsp.Spectrum(fftSize);
    this.spectrumBuffer = new Float32Array(fftSize);
    const detailSpectrum = new dsp.Spectrum(8192, .16, true);
    detailSpectrum.setView(this.spectrumView.offsetHz, this.spectrumView.spanHz);
    this.detailSpectrum = detailSpectrum;
    const sampleCounter = new dsp.SampleCounter(20);
    this.p25Iq.setSampleRate(this.state.sampleRate);
    this.p25Iq.setOffsetHz(this.state.offsetHz);
    this.p25Iq.setSink((dibits, lock) => this.p25.pushDibits(dibits, lock));
    this.bindP25Hooks();
    this.signalId.setIqMode(true);
    const signalIqReceiver = {
      sampleRate: this.state.sampleRate,
      setSampleRate(sampleRate: number) { this.sampleRate = sampleRate; },
      receiveSamples: (block: { I: Float32Array; Q: Float32Array; frequency: number }) => {
        for (const listener of this.diagnosticIqListeners) listener(block.I, block.Q, signalIqReceiver.sampleRate, block.frequency);
        this.signalId.pushIq(block.I, block.Q, block.frequency, signalIqReceiver.sampleRate);
      },
    };
    const receiver = dsp.CompositeReceiver.of(
      signalIqReceiver,
      spectrum,
      detailSpectrum,
      demodulator,
      sampleCounter,
      this.p25Iq,
    );
    const radio = new dsp.Radio(provider, receiver);

    this.player = player;
    this.demodulator = demodulator;
    this.spectrum = spectrum;
    this.sampleCounter = sampleCounter;
    this.radio = radio;

    for (const scheme of getSchemes()) {
      this.knownModes[scheme] = getMode(scheme);
    }

    sampleCounter.addEventListener("sample-click", this.onSampleClick);
    radio.addEventListener("radio", (e) => this.onRadioEvent(e.detail));
    demodulator.addEventListener("stereo-status", (e) => {
      this.patch({ stereoActive: Boolean(e.detail) });
    });

    try {
      // Clamp so a leftover HackRF rate (e.g. 4 MS/s) cannot be sent to RTL.
      const sampleRate = nearestSampleRate(this.state.sampleRate);
      if (sampleRate !== this.state.sampleRate) {
        this.patch({ sampleRate, offsetHz: 0 });
      }
      this.p25Iq.setSampleRate(sampleRate);

      await radio.setSampleRate(sampleRate);
      await radio.setFrequency(this.state.frequencyHz);
      // Tuner path (R820T/R828D); direct sampling is HF-only and breaks Blog V4.
      await radio.setDirectSamplingMethod(dsp.DirectSampling.Off);
      await radio.setFrequencyCorrection(this.state.ppm);
      await radio.setGain(this.state.autoGain ? null : this.state.gainDb);
      await radio.enableBiasTee(this.state.biasTee);

      demodulator.setVolume(this.state.volume);
      demodulator.setFrequencyOffset(this.state.offsetHz);

      const baseMode = this.knownModes[this.state.scheme] ?? getMode("WBFM");
      let mode = baseMode;
      let params = modeParameters(mode);
      if (params.hasBandwidth()) {
        params = params.setBandwidth(this.state.bandwidthHz);
        mode = params.mode;
      }
      if (params.hasSquelch()) {
        mode = params.setSquelch(0).mode;
        this.knownModes[mode.scheme] = mode;
      }
      demodulator.setMode(mode);

      this.squelchGate.reset();
      this.lastSquelchEvalMs = 0;
      this.player?.setSquelched(false);

      this.patch({
        bandwidthHz: modeParameters(mode).getBandwidth(),
        stereo: modeParameters(mode).hasStereo()
          ? modeParameters(mode).getStereo()
          : false,
        squelchOpen: true,
      });

      await radio.start();
      const actualRate = radio.getSampleRate();
      const half = actualRate / 2;
      this.p25Iq.setSampleRate(actualRate);
      this.patch({
        sampleRate: actualRate,
        offsetHz: clamp(this.state.offsetHz, -half, half),
        status: "playing",
      });
      demodulator.setFrequencyOffset(this.state.offsetHz);
      this.p25Iq.setOffsetHz(this.state.offsetHz);
    } catch (err) {
      this.teardown();
      this.patch({
        connection: "disconnected",
        status: "error",
        error: errorMessage(err),
      });
    }
  }

  private onRadioEvent(detail: { type: string; exception?: unknown; active?: boolean }) {
    if (detail.type === "started") {
      this.patch({ status: "playing" });
    } else if (detail.type === "stopped") {
      this.patch({ status: "idle" });
    } else if (detail.type === "error") {
      this.patch({
        status: "error",
        error: errorMessage(detail.exception ?? "Radio error"),
      });
    }
  }

  private emitSpectrum(): void {
    if (!this.spectrum) return;
    const scanning = this.signalId.getState().enabled;
    const squelchOn = this.squelchActive();
    const afcOn = this.afcActive();
    if (
      this.spectrumListeners.size === 0 &&
      !scanning &&
      !squelchOn &&
      !afcOn
    ) {
      return;
    }

    const fftSize = this.state.frequencyHz < 30_000_000 ? 8192 : FFT_SIZE;
    if (this.spectrum.size !== fftSize) {
      this.spectrum.size = fftSize;
      this.spectrumBuffer = new Float32Array(fftSize);
      return;
    }
    if (!this.spectrum.isReady()) return;
    this.spectrum.getSpectrum(this.spectrumBuffer);
    const center = this.spectrum.frequency();

    if (squelchOn) {
      this.evaluateSquelch();
    }
    if (afcOn) {
      this.evaluateAfc();
    }

    if (scanning) {
      this.signalId.pushSpectrum(
        this.spectrumBuffer,
        center,
        this.state.sampleRate,
        this.state.frequencyHz,
      );
    }
    const detail = this.detailSpectrum;
    const useDetail = detail?.isReady() && detail.getFrameInfo().sampleRate < this.state.sampleRate;
    if (detail && useDetail) detail.getSpectrum(this.detailBuffer);
    for (const listener of this.spectrumListeners) {
      if (detail && useDetail && this.displayListeners.has(listener)) {
        listener(this.detailBuffer, center, detail.getFrameInfo());
      } else {
        listener(this.spectrumBuffer, center, this.spectrum.getFrameInfo());
      }
    }
  }

  private notifyExternalDemod(): void {
    this.external?.onDemod?.({
      scheme: this.state.scheme,
      bandwidthHz: this.state.bandwidthHz,
      offsetHz: this.state.offsetHz,
    });
  }

  /** Current demodulator settings, for an external source that just attached. */
  getExternalDemod(): ExternalDemod {
    return {
      scheme: this.state.scheme,
      bandwidthHz: this.state.bandwidthHz,
      offsetHz: this.state.offsetHz,
    };
  }

  private squelchActive(): boolean {
    return this.state.autoSquelch || this.state.squelch > 0;
  }

  private afcActive(): boolean {
    return this.state.afc && isAfcScheme(this.state.scheme);
  }

  /** Prefer the live buffer for the active source (local / OWRX / Kiwi / HackRF). */
  private latestSpectrumBuffer(): Float32Array | null {
    if (this.owrx && this.owrxSpectrum.length > 0) return this.owrxSpectrum;
    if (this.kiwi && this.kiwiSpectrum.length > 0) return this.kiwiSpectrum;
    if (this.external && this.externalSpectrum.length > 0) {
      return this.externalSpectrum;
    }
    if (this.spectrum) {
      this.spectrum.getSpectrum(this.spectrumBuffer);
      return this.spectrumBuffer;
    }
    return null;
  }

  private forceSquelchOpen(): void {
    this.squelchGate.reset();
    this.player?.setSquelched(false);
    this.owrxPlayback?.setSquelched(false);
    if (!this.state.squelchOpen) {
      this.patch({ squelchOpen: true });
    }
  }

  private evaluateSquelch(buffer: Float32Array = this.spectrumBuffer): void {
    const now = performance.now();
    const dtSeconds =
      this.lastSquelchEvalMs > 0
        ? Math.min(0.5, (now - this.lastSquelchEvalMs) / 1000)
        : 0.05;
    this.lastSquelchEvalMs = now;

    const decision = this.squelchGate.updateFromSpectrum(
      buffer,
      this.state.sampleRate,
      this.state.offsetHz,
      this.state.bandwidthHz,
      this.state.squelch,
      this.state.autoSquelch,
      dtSeconds,
    );

    const closed = !decision.open;
    this.player?.setSquelched(closed);
    this.owrxPlayback?.setSquelched(closed);
    if (decision.open !== this.state.squelchOpen) {
      this.patch({ squelchOpen: decision.open });
    }
  }

  /**
   * Spectrum-driven AFC. Rate-limited so remote sources (OpenWebRX / Kiwi)
   * are not flooded with offset updates.
   */
  private evaluateAfc(buffer: Float32Array = this.spectrumBuffer): void {
    if (!this.afcActive()) {
      if (this.state.afcLocked) this.patch({ afcLocked: false });
      return;
    }
    const scheme = this.state.scheme;
    if (!isAfcScheme(scheme)) return;

    const now = performance.now();
    // ~10 Hz is enough for drift; remote retunes stay cheap.
    if (this.lastAfcEvalMs > 0 && now - this.lastAfcEvalMs < 100) return;
    this.lastAfcEvalMs = now;

    const result = this.afcTracker.update(
      buffer,
      this.state.sampleRate,
      this.state.offsetHz,
      scheme,
    );

    if (result.locked !== this.state.afcLocked) {
      this.patch({ afcLocked: result.locked });
    }
    if (result.offsetHz != null && result.offsetHz !== this.state.offsetHz) {
      this.setOffset(result.offsetHz);
    }
  }

  private teardown(): void {
    this.signalId.setIqMode(false);
    this.signalId.reset();
    this.squelchGate.reset();
    this.afcTracker.reset();
    this.lastSquelchEvalMs = 0;
    this.lastAfcEvalMs = 0;
    this.p25Iq.setEnabled(false);
    this.p25Iq.reset();
    this.setDigitalMute(false);
    this.sampleCounter?.removeEventListener("sample-click", this.onSampleClick);
    this.untap?.();
    this.untap = null;
    this.player?.setTapping(false);
    this.player?.setSquelched(false);
    this.player?.close();
    this.player = null;
    this.radio = null;
    this.demodulator = null;
    this.spectrum = null;
    this.detailSpectrum = null;
    this.sampleCounter = null;
  }

  private bindP25Hooks(): void {
    this.p25.setRadioHooks({
      setFrequency: (hz) => this.setFrequency(hz),
      setOffset: (hz) => this.setOffset(hz),
      setSchemeNbfm: () => this.setScheme("NBFM"),
      setDigitalMute: (muted) => this.setDigitalMute(muted),
      getFrequency: () => this.state.frequencyHz,
      getOffset: () => this.state.offsetHz,
    });
  }

  private bindScannerHooks(): void {
    this.scanner.setRadioHooks({
      setFrequency: (hz) => this.setFrequency(hz),
      setScheme: (scheme) => this.setScheme(scheme),
      setBandwidth: (hz) => this.setBandwidth(hz),
      setOffset: (hz) => this.setOffset(hz),
      getSquelchOpen: () => this.state.squelchOpen,
      getSquelchActive: () => this.squelchActive(),
      isConnected: () => this.state.connection !== "disconnected",
    });
  }

  private setDigitalMute(muted: boolean): void {
    this.digitalMute = muted;
    const volume = muted || this.finderMuted || this.monitorMuted || this.backendCleanActive ? 0 : this.state.volume;
    this.demodulator?.setVolume(volume);
    this.owrxPlayback?.setVolume(volume);
  }

  private wantAudioTap(): boolean {
    return (
      this.diagnosticAudioListeners.size > 0 ||
      this.transcription.getState().enabled ||
      this.fsk.getState().enabled ||
      this.ft8.getState().enabled ||
      this.morse.getState().enabled ||
      this.wefax.getState().enabled
    );
  }

  private syncTapping(): void {
    const tapping = this.wantAudioTap();
    this.player?.setTapping(tapping);
    this.owrxPlayback?.setTapping(tapping);
  }

  private onOpenWebRxConfig(
    config: {
      centerFreqHz: number | null;
      sampleRate: number | null;
      startMod: string | null;
      startOffsetHz: number | null;
      allowsCenterChanges: boolean;
      profileId: string | null;
    },
    resetTuning: boolean,
  ): void {
    const center = config.centerFreqHz ?? this.state.frequencyHz;
    const rate = config.sampleRate ?? this.state.sampleRate;
    let scheme = this.state.scheme;
    let offsetHz = this.state.offsetHz;
    let bandwidthHz = this.state.bandwidthHz;
    if (resetTuning) {
      scheme = schemeFromOwrx(config.startMod) ?? scheme;
      offsetHz = config.startOffsetHz ?? 0;
      if (!this.knownModes[scheme]) this.knownModes[scheme] = getMode(scheme);
      const mode = this.knownModes[scheme] ?? getMode(scheme);
      bandwidthHz = modeParameters(mode).getBandwidth();
      const params = modeParameters(mode);
      this.patch({
        stereo: params.hasStereo() ? params.getStereo() : false,
      });
    }
    const half = rate / 2;
    this.patch({
      connection: "openwebrx",
      status: "playing",
      frequencyHz: center,
      sampleRate: rate,
      scheme,
      offsetHz: clamp(Math.round(offsetHz), -half, half),
      bandwidthHz,
      error: null,
    });
    if (this.owrxSession) {
      this.setOwrxSession({
        ...this.owrxSession,
        allowsCenterChanges: config.allowsCenterChanges,
        profileId: config.profileId ?? this.owrxSession.profileId,
      });
    }
    this.pushOpenWebRxTuning(resetTuning);
  }

  private onOpenWebRxSpectrum(
    shifted: Float32Array,
    centerHz: number,
    sampleRate: number,
  ): void {
    if (!this.owrx?.getConfig().sampleRate) return;
    if (this.owrxSpectrum.length !== shifted.length) {
      this.owrxSpectrum = new Float32Array(shifted.length);
    }
    unshiftSpectrum(shifted, this.owrxSpectrum);

    if (this.squelchActive()) this.evaluateSquelch(this.owrxSpectrum);
    if (this.afcActive()) this.evaluateAfc(this.owrxSpectrum);
    if (this.signalId.getState().enabled) {
      this.signalId.pushSpectrum(
        this.owrxSpectrum,
        centerHz || this.state.frequencyHz,
        sampleRate,
        this.state.frequencyHz,
      );
    }
    for (const listener of this.spectrumListeners) {
      listener(this.owrxSpectrum, centerHz || this.state.frequencyHz);
    }
  }

  private onOpenWebRxAudio(
    pcm: Int16Array,
    sampleRate: number,
    hd: boolean,
  ): void {
    const now = performance.now();
    if (hd) this.owrxLastHdMs = now;
    else if (now - this.owrxLastHdMs < 750) return;
    this.owrxPlayback?.push(pcm, sampleRate);
  }

  private pushOpenWebRxTuning(restart: boolean): void {
    if (!this.owrx) return;
    const band = owrxBandpass(this.state.scheme, this.state.bandwidthHz);
    this.owrx.applyTuning(
      {
        mod: owrxModulation(this.state.scheme),
        offset_freq: this.state.offsetHz,
        low_cut: band.low_cut,
        high_cut: band.high_cut,
      },
      restart,
    );
  }

  private teardownOpenWebRx(): void {
    this.signalId.reset();
    this.squelchGate.reset();
    this.afcTracker.reset();
    this.lastSquelchEvalMs = 0;
    this.lastAfcEvalMs = 0;
    this.owrxLastHdMs = 0;
    this.owrx?.close();
    this.owrx = null;
    this.owrxUntap?.();
    this.owrxUntap = null;
    this.owrxPlayback?.close();
    this.owrxPlayback = null;
    this.owrxSpectrum = new Float32Array(0);
    this.setDigitalMute(false);
    this.setOwrxSession(null);
  }

  private onKiwiSpectrum(
    shifted: Float32Array,
    centerHz: number,
    sampleRate: number,
  ): void {
    if (!this.kiwi) return;
    if (this.kiwiSpectrum.length !== shifted.length) {
      this.kiwiSpectrum = new Float32Array(shifted.length);
    }
    unshiftSpectrum(shifted, this.kiwiSpectrum);

    // Only react when zoom/span changes — do not rewrite frequency every row
    // (that re-rendered the whole Receiver and starved keepalive).
    if (Math.abs(sampleRate - this.state.sampleRate) > 1) {
      const half = sampleRate / 2;
      this.patch({
        sampleRate,
        offsetHz: clamp(this.state.offsetHz, -half, half),
      });
    }

    if (this.squelchActive()) this.evaluateSquelch(this.kiwiSpectrum);
    if (this.afcActive()) this.evaluateAfc(this.kiwiSpectrum);
    if (this.signalId.getState().enabled) {
      this.signalId.pushSpectrum(
        this.kiwiSpectrum,
        centerHz || this.state.frequencyHz,
        sampleRate,
        this.state.frequencyHz + this.state.offsetHz,
      );
    }
    for (const listener of this.spectrumListeners) {
      listener(this.kiwiSpectrum, centerHz || this.state.frequencyHz);
    }
  }

  private pushKiwiTuning(): void {
    if (!this.kiwi) return;
    const band = kiwiBandpass(this.state.scheme, this.state.bandwidthHz);
    this.kiwi.applyTuning({
      mod: kiwiModulation(this.state.scheme),
      low_cut: band.low_cut,
      high_cut: band.high_cut,
      frequencyHz: this.state.frequencyHz + this.state.offsetHz,
    });
  }

  private teardownKiwi(): void {
    this.signalId.reset();
    this.squelchGate.reset();
    this.afcTracker.reset();
    this.lastSquelchEvalMs = 0;
    this.lastAfcEvalMs = 0;
    this.kiwi?.close();
    this.kiwi = null;
    this.owrxUntap?.();
    this.owrxUntap = null;
    this.owrxPlayback?.close();
    this.owrxPlayback = null;
    this.kiwiSpectrum = new Float32Array(0);
    this.setDigitalMute(false);
    this.setKiwiSession(null);
  }

  private setKiwiSession(session: KiwiSession | null): void {
    this.kiwiSession = session;
    for (const listener of this.kiwiListeners) listener();
  }

  private setOwrxSession(session: OpenWebRxSession | null): void {
    this.owrxSession = session;
    for (const listener of this.owrxListeners) listener();
  }

  private patch(partial: Partial<SdrState>): void {
    this.state = { ...this.state, ...partial };
    for (const listener of this.stateListeners) {
      listener(this.state);
    }
  }
}
