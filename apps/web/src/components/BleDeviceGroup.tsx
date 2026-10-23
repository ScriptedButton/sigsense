"use client";

import { useState } from "react";
import { BlePacketRow } from "@/components/BlePacketRow";
import type { DeviceGroup } from "@/lib/bleGroups";

type Props = {
  group: DeviceGroup;
  defaultOpen?: boolean;
  defaultChannel?: number | null;
};

export function BleDeviceGroup({
  group,
  defaultOpen = false,
  defaultChannel,
}: Props) {
  const [open, setOpen] = useState(defaultOpen || group.led);

  return (
    <div className="border-b border-[var(--line)] last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-[var(--signal-dim)]"
      >
        <div className="min-w-0">
          <p className="truncate font-[family-name:var(--font-display)] text-base text-[var(--foreground)]">
            {group.label}
            {group.led ? (
              <span className="ml-2 font-mono text-xs text-amber-300">
                LED
              </span>
            ) : null}
          </p>
          <p className="truncate font-mono text-xs text-[var(--muted)]">
            {group.address.startsWith("anon:") ? "—" : group.address}
          </p>
        </div>
        <div className="shrink-0 text-right text-xs text-[var(--muted)]">
          <p>{group.packetCount} PDU{group.packetCount === 1 ? "" : "s"}</p>
          <p className="font-mono">{open ? "▾" : "▸"}</p>
        </div>
      </button>
      {open ? (
        <ul className="divide-y divide-[var(--line)] border-t border-[var(--line)] bg-[var(--bg)]/40">
          {group.packets.map((p, i) => (
            <BlePacketRow
              key={`${p.header_hex}:${p.payload_hex}-${i}`}
              packet={p}
              defaultChannel={defaultChannel ?? p.channel}
            />
          ))}
        </ul>
      ) : null}
    </div>
  );
}
