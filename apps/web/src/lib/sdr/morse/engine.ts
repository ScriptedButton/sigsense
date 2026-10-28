import { MorseDecoder } from "./decoder";
import { uniqueMorseKeywords } from "./keywords";
import {
  DEFAULT_MORSE_CONFIG,
  MORSE_PRESETS,
  type MorseConfig,
  type MorseListener,
  type MorsePresetId,
  type MorseState,
} from "./types";

const MAX_TEXT = 4_000;

/**
 * Owns Morse decoder state and feeds it demodulated PCM from the audio tap.
 */
export class MorseDecoderEngine {
  private state: MorseState = {
    enabled: false,
    status: "idle",
    config: { ...DEFAULT_MORSE_CONFIG },
    text: "",
    keywords: [],
    lastChar: null,
    wpmEstimate: DEFAULT_MORSE_CONFIG.wpm,
    lock: 0,
    error: null,
  };
  private listeners = new Set<MorseListener>();
  private decoder = new MorseDecoder(this.state.config);
  private disposed = false;
  private charsSinceNotify = 0;

  getState(): MorseState {
    return this.state;
  }

  subscribe(listener: MorseListener): () => void {
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

  setPreset(preset: Exclude<MorsePresetId, "custom">): void {
    const entry = MORSE_PRESETS[preset];
    const config: MorseConfig = { ...entry.config, preset };
    this.decoder.setConfig(config);
    this.patch({
      config,
      wpmEstimate: config.wpm,
      status: this.state.enabled ? "listening" : "idle",
    });
  }

  patchConfig(partial: Partial<MorseConfig>): void {
    const config: MorseConfig = {
      ...this.state.config,
      ...partial,
      preset: "custom",
    };
    this.decoder.setConfig(config);
    this.patch({ config, wpmEstimate: config.wpm });
  }

  clear(): void {
    this.patch({ text: "", keywords: [], lastChar: null, error: null });
  }

  dispose(): void {
    this.disposed = true;
    this.decoder.reset();
    this.patch({
      enabled: false,
      status: "idle",
      text: "",
      keywords: [],
      lastChar: null,
      lock: 0,
      wpmEstimate: this.state.config.wpm,
      error: null,
    });
  }

  pushAudio(mono: Float32Array, sampleRate: number): void {
    if (!this.state.enabled || this.disposed) return;
    if (mono.length === 0) return;

    const { chars, lock, wpmEstimate } = this.decoder.push(mono, sampleRate);
    if (
      !chars &&
      Math.abs(lock - this.state.lock) < 0.02 &&
      Math.abs(wpmEstimate - this.state.wpmEstimate) < 0.5
    ) {
      return;
    }

    let text = this.state.text;
    let lastChar = this.state.lastChar;
    let keywords = this.state.keywords;
    if (chars) {
      text = (text + chars).slice(-MAX_TEXT);
      lastChar = chars[chars.length - 1] ?? lastChar;
      keywords = uniqueMorseKeywords(text);
      this.charsSinceNotify += chars.length;
    }

    const status = lock > 0.2 || chars ? "decoding" : "listening";

    if (!chars && this.charsSinceNotify === 0) {
      if (
        Math.abs(lock - this.state.lock) < 0.05 &&
        Math.abs(wpmEstimate - this.state.wpmEstimate) < 1 &&
        status === this.state.status
      ) {
        return;
      }
    }
    this.charsSinceNotify = 0;

    this.patch({ text, keywords, lastChar, lock, status, wpmEstimate });
  }

  private patch(partial: Partial<MorseState>): void {
    this.state = { ...this.state, ...partial };
    for (const listener of this.listeners) {
      listener(this.state);
    }
  }
}
