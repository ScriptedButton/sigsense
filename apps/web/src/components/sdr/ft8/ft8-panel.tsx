"use client";

import {
  useRadioController,
  useFt8State,
  useSdrState,
} from "@/components/sdr/sdr-provider";
import { Ft8KeywordChips } from "@/components/sdr/ft8/ft8-keyword-chips";
import { Ft8MessageList } from "@/components/sdr/ft8/ft8-message-list";
import { Ft8Settings } from "@/components/sdr/ft8/ft8-settings";
import { uniqueFt8Keywords } from "@/lib/sdr/ft8/keywords";
import { DEMO_FT8_FREQUENCY_HZ, FT8_CYCLE_SEC } from "@/lib/sdr/ft8/types";

export function Ft8Panel() {
  const controller = useRadioController();
  const radio = useSdrState();
  const ft8 = useFt8State();
  const live = radio.status === "playing";
  const remaining = Math.max(0, FT8_CYCLE_SEC - ft8.cycleSec);
  const keywords = uniqueFt8Keywords(ft8.messages);

  const statusLabel = (() => {
    switch (ft8.status) {
      case "decoding":
        return "Decoding period…";
      case "listening":
        return `Listening · next decode in ${remaining.toFixed(0)}s`;
      default:
        return "Off";
    }
  })();

  return (
    <fieldset className="control-group">
      <legend className="control-label">FT8 decoder</legend>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className={ft8.enabled ? "btn-primary" : "btn-secondary"}
          onClick={() => controller.setFt8Enabled(!ft8.enabled)}
          title="Decode FT8 from USB demodulated audio"
        >
          {ft8.enabled ? "Stop" : "Decode"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={ft8.messages.length === 0}
          onClick={() => controller.clearFt8Messages()}
        >
          Clear
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={ft8.messages.length === 0}
          onClick={() => {
            const text = ft8.messages
              .map(
                (m) =>
                  `${new Date(m.at).toISOString().slice(11, 19)} ${Math.round(m.snr)} ${Math.round(m.freq)} ${m.msg}`,
              )
              .join("\n");
            void navigator.clipboard.writeText(text);
          }}
        >
          Copy
        </button>
      </div>

      <p className="mt-2 text-xs text-muted">
        {statusLabel}
        {!live && ft8.enabled ? " · waiting for RF" : null}
      </p>

      {ft8.enabled ? (
        <div
          className="mt-2 h-1 overflow-hidden rounded-full bg-line"
          role="progressbar"
          aria-valuenow={Math.round(ft8.cycleSec)}
          aria-valuemin={0}
          aria-valuemax={FT8_CYCLE_SEC}
          aria-label="FT8 cycle progress"
        >
          <div
            className="h-full bg-accent transition-[width] duration-200"
            style={{
              width: `${Math.min(100, (ft8.cycleSec / FT8_CYCLE_SEC) * 100)}%`,
            }}
          />
        </div>
      ) : null}

      {ft8.error ? (
        <p className="mt-2 text-xs text-danger" role="alert">
          {ft8.error}
        </p>
      ) : null}

      <Ft8Settings />
      <Ft8KeywordChips keywords={keywords} />
      <Ft8MessageList messages={ft8.messages} enabled={ft8.enabled} />

      <p className="mt-2 text-[0.65rem] leading-relaxed text-muted">
        Use USB (or LSB) with squelch open. Demo mode has FT8 at{" "}
        {(DEMO_FT8_FREQUENCY_HZ / 1e6).toFixed(3)} MHz — tune there, select USB,
        then Decode. Decodes run on each UTC 15 s boundary. CQ, reports, and
        grids are identified; click a callsign to look it up.
      </p>
    </fieldset>
  );
}
