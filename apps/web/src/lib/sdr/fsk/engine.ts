import { FskDecoder } from "./decoder";
import {
  DEFAULT_FSK_CONFIG,
  FSK_PRESETS,
  type FskConfig,
  type FskListener,
  type FskPresetId,
  type FskState,
} from "./types";

const MAX_TEXT = 4_000;

/**
 * Owns FSK decoder state and feeds it demodulated PCM from the audio tap.
 */
export class FskDecoderEngine {
  private state: FskState = {
    enabled: false,
    status: "idle",
    config: { ...DEFAULT_FSK_CONFIG },
    text: "",
    lastChar: null,
    lock: 0,
    error: null,
  };
  private listeners = new Set<FskListener>();
  private decoder = new FskDecoder(this.state.config);
  private disposed = false;
  private charsSinceNotify = 0;

  getState(): FskState {
    return this.state;
  }

  subscribe(listener: FskListener): () => void {
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
        lock: 0,
        error: null,
      });
      return;
    }
    this.decoder.reset();
    this.patch({
      enabled: true,
      status: "listening",
      error: null,
    });
  }

  setPreset(preset: Exclude<FskPresetId, "custom">): void {
    const entry = FSK_PRESETS[preset];
    const config: FskConfig = { ...entry.config, preset };
    this.decoder.setConfig(config);
    this.patch({ config, status: this.state.enabled ? "listening" : "idle" });
  }

  patchConfig(partial: Partial<FskConfig>): void {
    const config: FskConfig = {
      ...this.state.config,
      ...partial,
      preset: "custom",
    };
    this.decoder.setConfig(config);
    this.patch({ config });
  }

  setInverted(inverted: boolean): void {
    const config: FskConfig = { ...this.state.config, inverted };
    // Keep preset id when only polarity flips.
    this.decoder.setConfig(config);
    this.patch({ config });
  }

  clear(): void {
    this.patch({ text: "", lastChar: null, error: null });
  }

  dispose(): void {
    this.disposed = true;
    this.decoder.reset();
    this.patch({
      enabled: false,
      status: "idle",
      text: "",
      lastChar: null,
      lock: 0,
      error: null,
    });
  }

  pushAudio(mono: Float32Array, sampleRate: number): void {
    if (!this.state.enabled || this.disposed) return;
    if (mono.length === 0) return;

    const { chars, lock } = this.decoder.push(mono, sampleRate);
    if (!chars && Math.abs(lock - this.state.lock) < 0.02) return;

    let text = this.state.text;
    let lastChar = this.state.lastChar;
    if (chars) {
      text = (text + chars).slice(-MAX_TEXT);
      lastChar = chars[chars.length - 1] ?? lastChar;
      this.charsSinceNotify += chars.length;
    }

    const status =
      lock > 0.15 || chars ? "decoding" : "listening";

    // Throttle UI updates when only lock drifts.
    if (!chars && this.charsSinceNotify === 0) {
      if (Math.abs(lock - this.state.lock) < 0.05 && status === this.state.status) {
        return;
      }
    }
    this.charsSinceNotify = 0;

    this.patch({ text, lastChar, lock, status });
  }

  private patch(partial: Partial<FskState>): void {
    this.state = { ...this.state, ...partial };
    for (const listener of this.listeners) {
      listener(this.state);
    }
  }
}
