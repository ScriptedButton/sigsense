"use client";

import { broadcastType } from "@/lib/bleGroups";
import type { BlePacket } from "@/lib/bleTypes";

export type MessageEntry = {
  key: string;
  packet: BlePacket;
  count?: number;
  channels: number[];
  typeLabel?: string;
};

const titleOf = (entry: MessageEntry) =>
  entry.typeLabel || broadcastType(entry.packet);

export function BleMessageList({
  entries,
  selectedKey,
  onSelect,
}: {
  entries: MessageEntry[];
  selectedKey: string;
  onSelect: (key: string) => void;
}) {
  return (
    <ul className="max-h-80 overflow-auto" aria-label="Messages from this device">
      {entries.map((entry) => {
        const active = entry.key === selectedKey;
        const channels = entry.channels.length
          ? entry.channels.join(" / ")
          : entry.packet.channel ?? "?";
        return (
          <li key={entry.key} className="border-b border-[var(--line)] last:border-b-0">
            <button
              type="button"
              aria-pressed={active}
              onClick={() => onSelect(entry.key)}
              className={`w-full px-3 py-2.5 text-left hover:bg-white/[0.03] ${active ? "bg-[var(--signal-dim)] shadow-[inset_2px_0_0_var(--signal)]" : ""}`}
            >
              <span className="block truncate text-xs text-[var(--foreground)]">
                {titleOf(entry)}
              </span>
              <span className="mt-1 block text-[10px] text-[var(--muted)]">
                CH {channels}
                {entry.count !== undefined ? ` · ${entry.count}×` : ""}
                {entry.packet.rssi_dbfs != null ? ` · ${entry.packet.rssi_dbfs.toFixed(1)} dBFS` : ""}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
