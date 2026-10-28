"use client";

import { useState } from "react";
import { replayBlePacket } from "@/lib/api";
import type { BlePacket } from "@/lib/bleTypes";

type Props = {
  packet: BlePacket;
  defaultChannel?: number | null;
};

const CHANNELS = [37, 38, 39] as const;

export function BleReplayButton({ packet, defaultChannel }: Props) {
  const initial =
    packet.channel && CHANNELS.includes(packet.channel as 37 | 38 | 39)
      ? packet.channel
      : (defaultChannel ?? 37);
  const [channel, setChannel] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const replay = async (dryRun = false) => {
    setBusy(true);
    setMsg(null);
    try {
      const res = await replayBlePacket({
        header_hex: packet.header_hex,
        payload_hex: packet.payload_hex,
        channel,
        repeats: 3,
        tx_gain: 20,
        dry_run: dryRun,
      });
      if (dryRun) {
        setMsg(`Dry-run OK · ${res.iq_samples ?? "?"} samples`);
      } else if (res.transmitted) {
        setMsg(`TX OK · ch${channel} · ${res.samples ?? res.iq_samples} samp`);
      } else {
        setMsg("Replay returned without TX");
      }
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-1 text-xs text-[var(--muted)]">
        Transmit channel
        <select
          className="field py-1 text-xs"
          value={channel}
          disabled={busy}
          onChange={(e) => setChannel(Number(e.target.value))}
        >
          {CHANNELS.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        disabled={busy}
        onClick={() => replay(false)}
        className="border border-[var(--signal)] px-2 py-1 text-xs text-[var(--signal)] hover:bg-[var(--signal-dim)] disabled:opacity-50"
      >
        {busy ? "Transmitting…" : "Transmit packet"}
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={() => replay(true)}
        className="border border-[var(--line)] px-2 py-1 text-xs text-[var(--muted)] hover:border-[var(--signal)] disabled:opacity-50"
      >
        Dry-run
      </button>
      {msg ? (
        <span className="max-w-full break-all font-mono text-[10px] text-[var(--muted)]">
          {msg}
        </span>
      ) : null}
    </div>
  );
}
