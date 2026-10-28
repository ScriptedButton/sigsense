"use client";

import { useP25State } from "@/components/sdr/sdr-provider";
import { formatFrequency } from "@/lib/sdr/format";
import { nacHex } from "@/lib/sdr/p25/types";

export function P25SystemStatus() {
  const p25 = useP25State();
  if (!p25.enabled) return null;
  const { system, lock, decodeRate, controlChannelHz, voiceFrequencyHz } = p25;

  return (
    <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
      <dt className="text-muted">NAC</dt>
      <dd className="font-mono text-ink">{nacHex(system.nac)}</dd>
      <dt className="text-muted">WACN / Sys</dt>
      <dd className="font-mono text-ink">
        {system.wacn != null ? system.wacn.toString(16).toUpperCase() : "—"} /{" "}
        {system.systemId != null
          ? system.systemId.toString(16).toUpperCase()
          : "—"}
      </dd>
      <dt className="text-muted">RFSS / Site</dt>
      <dd className="font-mono text-ink">
        {system.rfssId ?? "—"} / {system.siteId ?? "—"}
      </dd>
      <dt className="text-muted">Lock / rate</dt>
      <dd className="font-mono text-ink">
        {Math.round(lock * 100)}% · {decodeRate.toFixed(0)} fps
      </dd>
      {controlChannelHz != null ? (
        <>
          <dt className="text-muted">CC</dt>
          <dd className="font-mono text-ink">
            {formatFrequency(controlChannelHz)}
          </dd>
        </>
      ) : null}
      {voiceFrequencyHz != null ? (
        <>
          <dt className="text-muted">Voice</dt>
          <dd className="font-mono text-accent">
            {formatFrequency(voiceFrequencyHz)}
          </dd>
        </>
      ) : null}
    </dl>
  );
}
