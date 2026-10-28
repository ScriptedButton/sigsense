import { WefaxDecoder } from "./decoder";
import {
  DEFAULT_WEFAX_CONFIG,
  WEFAX_PRESETS,
  wefaxLineWidth,
  type WefaxConfig,
  type WefaxListener,
  type WefaxPresetId,
  type WefaxState,
} from "./types";

/**
 * Owns WEFAX decoder state and feeds it demodulated PCM from the audio tap.
 * Pixel buffer stays on the decoder; UI reads it via getImage() when frameId bumps.
 */
export class WefaxDecoderEngine {
  private state: WefaxState = {
    enabled: false,
    status: "idle",
    config: { ...DEFAULT_WEFAX_CONFIG },
    width: wefaxLineWidth(DEFAULT_WEFAX_CONFIG.ioc),
    lines: 0,
    frameId: 0,
    toneHz: 1900,
    lock: 0,
    error: null,
  };
  private listeners = new Set<WefaxListener>();
  private decoder = new WefaxDecoder(this.state.config);
  private disposed = false;
  private samplesSinceNotify = 0;
  private readonly notifyEverySamples = 4_800; // ~100 ms @ 48 kHz

  getState(): WefaxState {
    return this.state;
  }

  /** Grayscale scan lines [0, lines) × width — live buffer, do not retain across frames. */
  getImage(): { pixels: Uint8Array; width: number; lines: number } {
    return {
      pixels: this.decoder.getPixels(),
      width: this.decoder.getWidth(),
      lines: this.decoder.getLines(),
    };
  }

  subscribe(listener: WefaxListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  setEnabled(enabled: boolean): void {
    if (enabled === this.state.enabled) return;
    if (!enabled) {
      this.decoder.reset();
      this.patch({
        enabled: false,
        status: "idle",
        lines: 0,
        lock: 0,
        error: null,
        frameId: this.state.frameId + 1,
      });
      return;
    }
    this.decoder.reset();
    if (this.state.config.sync === "manual") {
      this.decoder.forceStart();
    }
    this.patch({
      enabled: true,
      status: this.decoder.getStatus(),
      width: this.decoder.getWidth(),
      lines: 0,
      error: null,
      frameId: this.state.frameId + 1,
    });
  }

  setPreset(preset: Exclude<WefaxPresetId, "custom">): void {
    const entry = WEFAX_PRESETS[preset];
    const config: WefaxConfig = { ...entry.config, preset };
    this.decoder.setConfig(config);
    this.patch({
      config,
      width: this.decoder.getWidth(),
      status: this.state.enabled ? this.decoder.getStatus() : "idle",
    });
  }

  patchConfig(partial: Partial<WefaxConfig>): void {
    const prevSync = this.state.config.sync;
    const config: WefaxConfig = {
      ...this.state.config,
      ...partial,
      preset: "custom",
    };
    this.decoder.setConfig(config);
    if (
      this.state.enabled &&
      config.sync === "manual" &&
      prevSync !== "manual" &&
      this.decoder.getStatus() !== "imaging"
    ) {
      this.decoder.forceStart();
    }
    this.patch({
      config,
      width: wefaxLineWidth(
        this.decoder.getIoc() !== config.ioc && config.sync === "auto"
          ? this.decoder.getIoc()
          : config.ioc,
      ),
      status: this.state.enabled ? this.decoder.getStatus() : "idle",
      lines: this.decoder.getLines(),
    });
  }

  setInverted(inverted: boolean): void {
    const config: WefaxConfig = { ...this.state.config, inverted };
    this.decoder.setConfig(config);
    this.patch({ config });
  }

  /** Skip APT and begin imaging with the current IOC/LPM. */
  forceStart(): void {
    if (!this.state.enabled) return;
    this.decoder.forceStart();
    this.patch({
      status: "imaging",
      lines: 0,
      width: this.decoder.getWidth(),
      frameId: this.state.frameId + 1,
    });
  }

  clear(): void {
    this.decoder.clearImage();
    if (this.state.enabled && this.state.config.sync === "manual") {
      this.decoder.forceStart();
    } else if (this.state.enabled) {
      this.decoder.reset();
    }
    this.patch({
      lines: 0,
      status: this.state.enabled
        ? this.state.config.sync === "manual"
          ? "imaging"
          : "listening"
        : this.state.status,
      frameId: this.state.frameId + 1,
      error: null,
    });
  }

  dispose(): void {
    this.disposed = true;
    this.decoder.reset();
    this.patch({
      enabled: false,
      status: "idle",
      lines: 0,
      lock: 0,
      error: null,
      frameId: this.state.frameId + 1,
    });
  }

  pushAudio(mono: Float32Array, sampleRate: number): void {
    if (!this.state.enabled || this.disposed) return;
    if (mono.length === 0) return;

    const result = this.decoder.push(mono, sampleRate);
    this.samplesSinceNotify += mono.length;

    const statusChanged = result.status !== this.state.status;
    const shouldNotify =
      result.advanced ||
      statusChanged ||
      this.samplesSinceNotify >= this.notifyEverySamples;

    if (!shouldNotify) {
      // Still track tone quietly for next notify.
      return;
    }
    this.samplesSinceNotify = 0;

    this.patch({
      status: result.status,
      lines: result.lines,
      width: this.decoder.getWidth(),
      toneHz: result.toneHz,
      lock: result.lock,
      frameId: result.advanced || statusChanged
        ? this.state.frameId + 1
        : this.state.frameId,
      config:
        result.ioc !== this.state.config.ioc && this.state.config.sync === "auto"
          ? { ...this.state.config, ioc: result.ioc, preset: "custom" }
          : this.state.config,
    });
  }

  private patch(partial: Partial<WefaxState>): void {
    this.state = { ...this.state, ...partial };
    for (const listener of this.listeners) {
      listener(this.state);
    }
  }
}
