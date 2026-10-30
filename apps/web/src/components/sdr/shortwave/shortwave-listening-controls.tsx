"use client";

import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";
import type { AudioNoiseReduction } from "@/lib/sdr/audio-enhancement";
import { useState } from "react";
import type { AudioEnhancementSettings } from "@/lib/sdr/audio-enhancement";
import { AM_LISTENING_PROFILES, cleanupEnabled } from "@/lib/sdr/shortwave/listening-profiles";

type BandwidthPreset = {
  label: string;
  hz: number;
  hint: string;
};

const AM_PRESETS: BandwidthPreset[] = [
  { label: "Wide", hz: 9_000, hint: "Full broadcast audio" },
  { label: "Voice", hz: 6_000, hint: "Clearer speech" },
  { label: "Narrow", hz: 4_000, hint: "Less adjacent interference" },
];

const SSB_PRESETS: BandwidthPreset[] = [
  { label: "Wide", hz: 3_000, hint: "Natural voice" },
  { label: "Voice", hz: 2_400, hint: "Typical communications" },
  { label: "Narrow", hz: 1_800, hint: "Crowded band" },
];

const NOISE_REDUCTION: { value: AudioNoiseReduction; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
];

/** Practical listening aids for shortwave AM and SSB voice reception. */
export function ShortwaveListeningControls() {
  const controller = useRadioController();
  const state = useSdrState();
  const [savedCleanup, setSavedCleanup] = useState<AudioEnhancementSettings | null>(null);
  const currentCleanup = { noiseReduction: state.audioNoiseReduction, voiceIsolation: state.voiceIsolation, speechFilter: Boolean(state.audioSpeechFilter) };
  const applyCleanup = (settings: AudioEnhancementSettings) => {
    controller.setAudioSpeechFilter(Boolean(settings.speechFilter));
    controller.setAudioNoiseReduction(settings.noiseReduction);
    void controller.setVoiceIsolation(settings.voiceIsolation);
  };
  const presets =
    state.scheme === "AM"
      ? AM_PRESETS
      : state.scheme === "USB" || state.scheme === "LSB"
        ? SSB_PRESETS
        : null;

  const openSquelch = () => {
    controller.setAutoSquelch(false);
    controller.setSquelch(0);
  };

  return (
    <fieldset className="control-group">
      <legend className="control-label">Shortwave listening</legend>

      {state.scheme === "AM" && <div className="mb-3">
        <p className="text-xs text-muted">One-tap listening setup</p>
        <div className="mt-2 grid grid-cols-3 gap-1" role="group" aria-label="AM listening setup">
          {AM_LISTENING_PROFILES.map((profile) => <button type="button" key={profile.label} className="mode-chip"
            onClick={() => { controller.setBandwidth(profile.bandwidthHz); applyCleanup(profile.enhancement); }}>
            {profile.label}
          </button>)}
        </div>
      </div>}
      <button type="button" className="mode-chip mb-3" disabled={!cleanupEnabled(currentCleanup) && !savedCleanup}
        aria-pressed={!cleanupEnabled(currentCleanup)}
        onClick={() => {
          if (cleanupEnabled(currentCleanup)) {
            setSavedCleanup(currentCleanup);
            applyCleanup({ noiseReduction: "off", speechFilter: false, voiceIsolation: false });
          } else if (savedCleanup) applyCleanup(savedCleanup);
        }}>
        {cleanupEnabled(currentCleanup) ? "Compare: bypass audio cleanup" : "Restore audio cleanup"}
      </button>

      {presets ? (
        <div>
          <p className="text-xs text-muted">
            Voice bandwidth · {(state.bandwidthHz / 1_000).toFixed(1)} kHz
          </p>
          <div
            className="mt-2 grid grid-cols-3 gap-1"
            role="group"
            aria-label={`${state.scheme} voice bandwidth`}
          >
            {presets.map((preset) => {
              const selected = Math.abs(state.bandwidthHz - preset.hz) < 50;
              return (
                <button
                  key={preset.hz}
                  type="button"
                  className={`mode-chip ${selected ? "mode-chip-active" : ""}`}
                  aria-pressed={selected}
                  title={preset.hint}
                  onClick={() => controller.setBandwidth(preset.hz)}
                >
                  <span className="block">{preset.label}</span>
                  <span className="block text-[0.6rem] opacity-75">
                    {preset.hz / 1_000}k
                  </span>
                </button>
              );
            })}
          </div>
          <p className="mt-2 text-[0.7rem] leading-snug text-muted">
            Hearing another station beside yours? Try Narrow. If speech sounds
            muffled, return to Voice or Wide.
          </p>
        </div>
      ) : (
        <p className="text-xs leading-snug text-muted">
          Choose AM, USB, or LSB to use shortwave voice bandwidth presets.
        </p>
      )}

      <label className="mt-4 block text-xs text-muted">
        Audio noise reduction
        <select
          className="input-sm mt-1 w-full"
          aria-label="Shortwave audio noise reduction"
          value={state.audioNoiseReduction}
          onChange={(event) =>
            controller.setAudioNoiseReduction(
              event.target.value as AudioNoiseReduction,
            )
          }
        >
          {NOISE_REDUCTION.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <p className="mt-1 text-[0.7rem] leading-snug text-muted">
        Start at Low. Stronger settings remove more steady hiss but can make
        weak speech sound metallic.
      </p>

      <label className="mt-3 flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={Boolean(state.audioSpeechFilter)}
          onChange={(event) => controller.setAudioSpeechFilter(event.target.checked)} />
        Speech filter · 150–3000 Hz
      </label>
      <p className="mt-1 text-[0.7rem] leading-snug text-muted">
        Cuts low rumble and high hiss. Leave off for full-range music or CW.
      </p>

      <label className="mt-3 flex items-center gap-2 text-sm text-ink">
        <input
          type="checkbox"
          checked={state.voiceIsolation}
          disabled={state.voiceIsolationLoading}
          onChange={(event) =>
            void controller.setVoiceIsolation(event.target.checked)
          }
        />
        Voice isolation
      </label>
      <p className="mt-1 text-[0.7rem] leading-snug text-muted" aria-live="polite">
        {state.voiceIsolationLoading
          ? "Loading the local voice model…"
          : "Optional speech-only processing. Leave off for music, CW, and digital signals."}
      </p>
      {state.audioEnhancementError && (
        <p role="alert" className="mt-1 text-xs text-danger">
          {state.audioEnhancementError}
        </p>
      )}

      {!state.squelchOpen && (
        <div className="mt-3 rounded border border-border p-2">
          <p className="text-xs leading-snug text-muted">
            Squelch is muting this weak signal.
          </p>
          <button
            type="button"
            className="mode-chip mt-2"
            onClick={openSquelch}
          >
            Open squelch
          </button>
        </div>
      )}
    </fieldset>
  );
}
