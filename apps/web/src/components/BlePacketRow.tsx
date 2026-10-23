"use client";

import { BleReplayButton } from "@/components/BleReplayButton";
import { isLedRemotePacket, packetKey } from "@/lib/bleGroups";
import type { BlePacket } from "@/lib/bleTypes";

type Props = {
  packet: BlePacket;
  defaultChannel?: number | null;
  showReplay?: boolean;
};

export function BlePacketRow({
  packet: p,
  defaultChannel,
  showReplay = true,
}: Props) {
  const led = isLedRemotePacket(p);
  return (
    <li
      className={`px-4 py-3 text-sm ${led ? "bg-[var(--signal-dim)]" : ""}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-mono text-[var(--signal)]">
          {p.pdu_type_name}
          {p.adv_address ? (
            <span className="text-[var(--foreground)]">{` ${p.adv_address}`}</span>
          ) : null}
          {led ? (
            <span className="ml-2 text-xs text-amber-300">LED</span>
          ) : null}
        </p>
        <span className="text-emerald-400">CRC OK</span>
      </div>
      {p.summary ? (
        <p className="mt-1 break-all font-mono text-xs text-[var(--muted)]">
          {p.summary}
        </p>
      ) : null}
      {!led && p.ad_structures && p.ad_structures.length > 0 ? (
        <ul className="mt-1 space-y-0.5 font-mono text-xs text-[var(--muted)]">
          {p.ad_structures.map((ad) => (
            <li key={`${packetKey(p)}-${ad.type}-${ad.data_hex}`}>
              {ad.label}: {ad.detail}
            </li>
          ))}
        </ul>
      ) : null}
      {showReplay && p.pdu_type_name.startsWith("ADV") ? (
        <BleReplayButton packet={p} defaultChannel={defaultChannel} />
      ) : null}
    </li>
  );
}
