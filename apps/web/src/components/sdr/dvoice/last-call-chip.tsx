"use client";

import { PROTOCOL_SHORT, callTitle, isEncrypted } from "@/lib/sdr/dvoice/model";
import { useDvoice } from "@/lib/sdr/dvoice/store";

/** Small status-area chip: the call in progress, or the last one heard. Hidden when digital voice is off. */
export function LastCallChip() {
  const { running, dv, prefs } = useDvoice();
  if (!running) return null;
  const live = dv.calls.find((c) => c.ended_at === null);
  const call = live ?? dv.calls[0];
  if (!call) {
    return (
      <span className="dv-chip" title="Digital voice is on">
        Digital voice · waiting
      </span>
    );
  }
  const encrypted = isEncrypted(call);
  return (
    <span
      className={`dv-chip ${live ? "dv-chip-live" : ""} ${encrypted ? "dv-chip-encrypted" : ""}`}
      title={`${live ? "On air" : "Last call"}: ${callTitle(call, prefs.aliases)} · ${PROTOCOL_SHORT[call.protocol]}${encrypted ? " · encrypted" : ""}`}
    >
      {live ? <span className="dv-rec" aria-hidden="true" /> : null}
      {callTitle(call, prefs.aliases)} · {PROTOCOL_SHORT[call.protocol]}
      {encrypted ? <span aria-label="encrypted"> ⚿</span> : null}
    </span>
  );
}
