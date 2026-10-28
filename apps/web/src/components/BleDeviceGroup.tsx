"use client";

import { useMemo, useState } from "react";
import { BleSaveButton } from "./BleSaveButton";
import type { BlePacket } from "@/lib/bleTypes";
import type { SignalTrack } from "@/lib/api";
import { BleMessageDetail } from "./BleMessageDetail";
import { BleMessageList, type MessageEntry } from "./BleMessageList";
import { broadcastKey, groupBroadcasts, packetKey, type DeviceGroup } from "@/lib/bleGroups";
import { groupMetrics, strengthLabel } from "@/lib/bleSort";
export function BleDeviceGroup({
  group,
  track,
  active = false,
  defaultOpen = false,
  defaultChannel,
  deduplicate = true,
  labels = {},
  demo = false,
  onSaved,
  getDevicePackets,
  selected = false,
  onSelectTrack,
  onNameTrack,
}: {
  group: DeviceGroup;
  track?: SignalTrack;
  active?: boolean;
  defaultOpen?: boolean;
  defaultChannel?: number | null;
  deduplicate?: boolean;
  labels?: Record<string, string>;
  demo?: boolean;
  onSaved?: () => Promise<void>;
  getDevicePackets?: (key: string) => BlePacket[];
  selected?: boolean;
  onSelectTrack?: (track: SignalTrack) => void;
  onNameTrack?: (track: SignalTrack, name: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [deviceName, setDeviceName] = useState(track?.name ?? "");
  const broadcasts = useMemo(() => groupBroadcasts(group.packets), [group.packets]);
  const channels = [
    ...new Set(group.packets.map((p) => p.channel).filter((c) => c !== null)),
  ];
  // Same reading the list sorts by: recent average, with the peak on hover.
  const { strengthDbfs: power, peakDbfs } = groupMetrics(group);
  const data = group.packets.some((p) => p.packet_kind === "data");
  const connected = group.packets.some(
    (p) => p.pdu_type_name === "CONNECT_IND",
  );
  const named = group.packets
    .flatMap((p) => p.ad_structures ?? [])
    .find((a) => a.type === 8 || a.type === 9);
  const recognizedBroadcasts = [...new Set(group.packets.map((packet) => labels[broadcastKey(packet)]).filter(Boolean))];
  const displayName = group.identified ? group.label : named?.detail || group.label;
  const address = group.address.startsWith("link:")
    ? `Link ${group.packets[0].access_address}`
    : group.address.startsWith("anon:")
      ? "No device address"
      : group.address;
  const role = group.led ? "Remote" : data ? "Connection" : connected ? "Connecting" : "Broadcasting";
  const entries: MessageEntry[] = deduplicate
    ? [...broadcasts].reverse().map((broadcast) => ({
        key: broadcast.key,
        packet: broadcast.packet,
        count: broadcast.count,
        channels: broadcast.channels,
        typeLabel: labels[broadcast.key],
      }))
    : [...group.packets].reverse().map((packet) => ({
        key: packetKey(packet),
        packet,
        channels: packet.channel == null ? [] : [packet.channel],
        typeLabel: labels[broadcastKey(packet)],
      }));
  const selectedMessage = entries.find((entry) => entry.key === selectedKey) ?? entries[0];
  return (
    <div className="overflow-hidden rounded-lg border border-[var(--line)]">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => {
          if (track) onSelectTrack?.(track);
          setOpen((v) => !v);
        }}
        className={`flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-white/[0.025] ${selected ? "bg-teal-400/5" : ""}`}
      >
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${active ? "bg-[var(--signal)]" : "bg-slate-600"}`} />
            <span className="truncate text-sm font-medium">{displayName}</span>
            <span className="shrink-0 rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-[var(--muted)]">{role}</span>
            {group.identified && (
              <span className="hidden shrink-0 text-[10px] text-sky-200 sm:inline">
                {group.saved ? "Saved" : "Identified"}
              </span>
            )}
          </div>
          <p className="mt-1 truncate pl-3.5 text-[11px] text-[var(--muted)]">
            <span className="font-mono">{address}</span>
            {channels.length ? ` · CH ${channels.join(" / ")}` : ""}
            {power !== null ? (
              <span title={`${strengthLabel(power)} · peak ${peakDbfs?.toFixed(1) ?? "n/a"} dBFS`}>
                {` · ${power.toFixed(1)} dBFS`}
              </span>
            ) : null}
            {recognizedBroadcasts.length ? ` · ${recognizedBroadcasts.join(" · ")}` : ""}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <div className="font-mono text-sm text-[var(--signal)]">{group.packetCount}</div>
          <div className="mt-0.5 text-[10px] text-[var(--muted)]">{open ? "Close" : "Inspect"}</div>
        </div>
      </button>
      {open && selectedMessage && (
        <div className="border-t border-[var(--line)] bg-black/15">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] px-4 py-3">
            <div className="min-w-0 text-[11px] text-[var(--muted)]">
              {track ? (
                <>
                  <span className={active ? "text-[var(--signal)]" : ""}>{active ? "RF active" : "RF recently observed"}</span>
                  {" · "}{track.label} · {Math.round(track.confidence * 100)}% · {(track.center_freq_hz / 1e6).toFixed(3)} MHz
                </>
              ) : deduplicate ? "Repeated payloads are grouped. A changed payload is a separate message." : "Newest message first."}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {track && (
                <form className="flex items-center gap-2" onSubmit={async (event) => {
                  event.preventDefault();
                  await onNameTrack?.(track, deviceName.trim());
                }}>
                  <input className="field w-40 min-w-0 py-1 text-xs" aria-label="Bluetooth device name" maxLength={80} value={deviceName} onChange={(event) => setDeviceName(event.target.value)} />
                  <button className="rounded-md border border-[var(--line)] px-2 py-1 text-[11px] hover:border-[var(--signal)]" disabled={!deviceName.trim()}>Save name</button>
                </form>
              )}
              <BleSaveButton compact packets={group.packets}
                getPackets={getDevicePackets ? () => getDevicePackets(group.key) : undefined}
                name={displayName} kind="device" demo={demo} onSaved={onSaved} />
            </div>
          </div>
          <div className="grid md:grid-cols-[15rem_minmax(0,1fr)]">
            <div className="border-b border-[var(--line)] md:border-r md:border-b-0">
              <BleMessageList entries={entries} selectedKey={selectedMessage.key} onSelect={setSelectedKey} />
            </div>
            <BleMessageDetail entry={selectedMessage} defaultChannel={defaultChannel} demo={demo} onSaved={onSaved} />
          </div>
        </div>
      )}
    </div>
  );
}
