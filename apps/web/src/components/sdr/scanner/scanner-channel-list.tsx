"use client";

import { ScannerChannelRow } from "@/components/sdr/scanner/scanner-channel-row";
import type { ScanBank } from "@/lib/sdr/scanner/types";

type ScannerChannelListProps = {
  bank: ScanBank;
  currentChannelId: string | null;
  listenHz: number;
  scanning: boolean;
};

export function ScannerChannelList({
  bank,
  currentChannelId,
  listenHz,
  scanning,
}: ScannerChannelListProps) {
  if (bank.channels.length === 0) {
    return (
      <p className="mt-2 text-[0.65rem] text-muted">
        Empty bank — add the current dial frequency below.
      </p>
    );
  }

  return (
    <ul className="scanner-list mt-2" aria-label={`${bank.name} channels`}>
      {bank.channels.map((ch) => (
        <li key={ch.id}>
          <ScannerChannelRow
            channel={ch}
            canRemove={!bank.builtin}
            active={
              scanning
                ? ch.id === currentChannelId
                : Math.abs(listenHz - ch.frequencyHz) < 500
            }
          />
        </li>
      ))}
    </ul>
  );
}
