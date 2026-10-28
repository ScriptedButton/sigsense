"use client";

import { useState } from "react";
import type { SignalTrack } from "@/lib/api";

export function RfTrackRow({
  track,
  active,
  selected,
  onSelect,
  onName,
}: {
  track: SignalTrack;
  active: boolean;
  selected: boolean;
  onSelect: () => void;
  onName: (name: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(track.name);
  return (
    <div className={`rounded-lg border p-3 ${selected ? "border-[var(--signal)] bg-teal-400/5" : "border-[var(--line)]"}`}>
      <button type="button" aria-expanded={open} onClick={() => {
        onSelect();
        setOpen((value) => !value);
      }} className="flex w-full flex-wrap items-center gap-3 text-left">
        <span className={`h-2 w-2 rounded-full ${active ? "bg-[var(--signal)]" : "bg-slate-600"}`} />
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{track.name}</span>
        <span className="text-xs text-[var(--signal)]">{track.label} · {Math.round(track.confidence * 100)}%</span>
        <span className="font-mono text-[10px] text-[var(--muted)]">{(track.center_freq_hz / 1e6).toFixed(3)} MHz</span>
        <span className="text-[10px] text-[var(--muted)]">{open ? "Hide details ▴" : "Inspect ▾"}</span>
      </button>
      {open && (
        <div className="mt-3 grid gap-3 border-t border-[var(--line)] pt-3 sm:grid-cols-2">
          <div>
            <p className="text-xs text-[var(--signal)]">{track.evidence}</p>
            <p className="mt-1 text-xs text-[var(--muted)]">{track.identity_note}</p>
            <p className="mt-2 font-mono text-[10px] text-[var(--muted)]">{track.device_address ?? track.id} · {track.identity_type === "rf_occupancy" ? "identity unverified" : track.channels.map((channel) => `CH ${channel}`).join(" / ")}</p>
            <p className="mt-1 text-[10px] text-[var(--muted)]">{track.packet_count ? `${track.packet_count} packets` : `${track.observations} observations`} · {track.bandwidth_hz.toFixed(0)} Hz bandwidth · {track.power_dbfs.toFixed(1)} dBFS</p>
          </div>
          <form className="flex items-start gap-2" onSubmit={async (event) => {
            event.preventDefault();
            await onName(name.trim());
          }}>
            <input className="field min-w-0 flex-1" aria-label="RF device name" maxLength={80} value={name} onChange={(event) => setName(event.target.value)} placeholder="Name this device" />
            <button className="rounded-md border border-[var(--line)] px-3 py-2 text-xs hover:border-[var(--signal)]" disabled={!name.trim()}>Save name</button>
          </form>
        </div>
      )}
    </div>
  );
}
