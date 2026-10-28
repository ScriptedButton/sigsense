"use client";

import {
  useRadioController,
  useMorseState,
  useSdrState,
} from "@/components/sdr/sdr-provider";
import { MorseKeywordChips } from "@/components/sdr/morse/morse-keyword-chips";
import { MorseOutput } from "@/components/sdr/morse/morse-output";
import { MorseSettings } from "@/components/sdr/morse/morse-settings";
import { DEMO_MORSE_FREQUENCY_HZ } from "@/lib/sdr/morse/types";

export function MorsePanel() {
  const controller = useRadioController();
  const radio = useSdrState();
  const morse = useMorseState();
  const live = radio.status === "playing";

  const statusLabel = (() => {
    switch (morse.status) {
      case "decoding":
        return `Decoding · ~${Math.round(morse.wpmEstimate)} WPM · lock ${Math.round(morse.lock * 100)}%`;
      case "listening":
        return "Listening to demod audio";
      default:
        return "Off";
    }
  })();

  return (
    <fieldset className="control-group">
      <legend className="control-label">Morse decoder</legend>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className={morse.enabled ? "btn-primary" : "btn-secondary"}
          onClick={() => controller.setMorseEnabled(!morse.enabled)}
          title="Decode Morse / CW from demodulated audio"
        >
          {morse.enabled ? "Stop" : "Decode"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={morse.text.length === 0}
          onClick={() => controller.clearMorseText()}
        >
          Clear
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={morse.text.length === 0}
          onClick={() => void navigator.clipboard.writeText(morse.text)}
        >
          Copy
        </button>
      </div>

      <p className="mt-2 text-xs text-muted">
        {statusLabel}
        {!live && morse.enabled ? " · waiting for RF" : null}
      </p>

      <MorseSettings />
      <MorseKeywordChips keywords={morse.keywords} />
      <MorseOutput text={morse.text} enabled={morse.enabled} />

      <p className="mt-2 text-[0.65rem] leading-relaxed text-muted">
        Use CW (or USB with the pitch in the tone control). Demo mode has Morse at{" "}
        {(DEMO_MORSE_FREQUENCY_HZ / 1e6).toFixed(3)} MHz — tune there, select CW,
        then Decode. CQ, Q-codes, and prosigns are identified; click a callsign
        to look it up.
      </p>
    </fieldset>
  );
}
