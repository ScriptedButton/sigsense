"use client";

import {
  useRadioController,
  useFskState,
  useSdrState,
} from "@/components/sdr/sdr-provider";
import { FskOutput } from "@/components/sdr/fsk/fsk-output";
import { FskSettings } from "@/components/sdr/fsk/fsk-settings";
import { DEMO_FSK_FREQUENCY_HZ } from "@/lib/sdr/fsk/types";

export function FskPanel() {
  const controller = useRadioController();
  const radio = useSdrState();
  const fsk = useFskState();
  const live = radio.status === "playing";

  const statusLabel = (() => {
    switch (fsk.status) {
      case "decoding":
        return `Decoding · lock ${Math.round(fsk.lock * 100)}%`;
      case "listening":
        return "Listening to demod audio";
      default:
        return "Off";
    }
  })();

  return (
    <fieldset className="control-group">
      <legend className="control-label">FSK decoder</legend>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className={fsk.enabled ? "btn-primary" : "btn-secondary"}
          onClick={() => controller.setFskEnabled(!fsk.enabled)}
          title="Decode FSK / RTTY from demodulated audio"
        >
          {fsk.enabled ? "Stop" : "Decode"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={fsk.text.length === 0}
          onClick={() => controller.clearFskText()}
        >
          Clear
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={fsk.text.length === 0}
          onClick={() => void navigator.clipboard.writeText(fsk.text)}
        >
          Copy
        </button>
      </div>

      <p className="mt-2 text-xs text-muted">
        {statusLabel}
        {!live && fsk.enabled ? " · waiting for RF" : null}
      </p>

      <FskSettings />
      <FskOutput text={fsk.text} enabled={fsk.enabled} />

      <p className="mt-2 text-[0.65rem] leading-relaxed text-muted">
        Use NBFM for RF FSK / RTTY, or USB for AFSK tones. Demo mode has RTTY at{" "}
        {(DEMO_FSK_FREQUENCY_HZ / 1e6).toFixed(3)} MHz — tune there, select NBFM,
        then Decode.
      </p>
    </fieldset>
  );
}
