"use client";

import {
  useRadioController,
  useP25State,
  useSdrState,
} from "@/components/sdr/sdr-provider";
import { P25CallList } from "@/components/sdr/p25/p25-call-list";
import { P25MessageLog } from "@/components/sdr/p25/p25-message-log";
import { P25Settings } from "@/components/sdr/p25/p25-settings";
import { P25SystemStatus } from "@/components/sdr/p25/p25-system-status";
import { DEMO_P25_FREQUENCY_HZ } from "@/lib/sdr/p25/types";

export function P25Panel() {
  const controller = useRadioController();
  const radio = useSdrState();
  const p25 = useP25State();
  const live = radio.status === "playing";

  const statusLabel = (() => {
    switch (p25.status) {
      case "voice":
        return "Voice channel";
      case "trunking":
        return "Trunking · control channel";
      case "synced":
        return `Synced · lock ${Math.round(p25.lock * 100)}%`;
      case "listening":
        return "Listening for C4FM";
      case "error":
        return p25.error ?? "Error";
      default:
        return "Off";
    }
  })();

  return (
    <fieldset className="control-group">
      <legend className="control-label">P25 trunk</legend>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className={p25.enabled ? "btn-primary" : "btn-secondary"}
          onClick={() => void controller.setP25Enabled(!p25.enabled)}
          title="Decode P25 Phase 1 C4FM / trunk from IQ"
        >
          {p25.enabled ? "Stop" : "Decode"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={p25.messages.length === 0}
          onClick={() => controller.clearP25Messages()}
        >
          Clear log
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={
            p25.activeCalls.length === 0 && p25.recentCalls.length === 0
          }
          onClick={() => controller.clearP25Calls()}
        >
          Clear calls
        </button>
      </div>

      <p className="mt-2 text-xs text-muted">
        {statusLabel}
        {!live && p25.enabled ? " · waiting for RF" : null}
        {p25.enabled && !p25.imbeReady ? " · metadata only (IMBE loading)" : null}
      </p>
      {p25.error ? (
        <p className="mt-1 text-xs text-danger">{p25.error}</p>
      ) : null}

      <P25Settings />
      <P25SystemStatus />
      <P25CallList />
      <P25MessageLog />

      <p className="mt-2 text-[0.65rem] leading-relaxed text-muted">
        Phase 1 C4FM from IQ. Trunk mode parks on a control channel and follows
        voice grants (single dongle). Demo C4FM at{" "}
        {(DEMO_P25_FREQUENCY_HZ / 1e6).toFixed(4)} MHz — NBFM, then Decode.
        Encrypted calls are muted. IMBE vocoder is educational (see README).
      </p>
    </fieldset>
  );
}
