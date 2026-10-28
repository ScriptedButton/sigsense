"use client";

import {
  useRadioController,
} from "@/components/sdr/sdr-provider";
import { formatFrequencyCompact } from "@/lib/sdr/format";
import type { ScanChannel } from "@/lib/sdr/scanner/types";

type ScannerChannelRowProps = {
  channel: ScanChannel;
  active: boolean;
  canRemove: boolean;
};

export function ScannerChannelRow({
  channel,
  active,
  canRemove,
}: ScannerChannelRowProps) {
  const controller = useRadioController();
  const mhz = formatFrequencyCompact(channel.frequencyHz);

  return (
    <div
      className={`scanner-row ${active ? "scanner-row-active" : ""} ${
        channel.lockedOut ? "scanner-row-locked" : ""
      }`}
    >
      <button
        type="button"
        className="scanner-row-tune"
        title={`Tune ${channel.label}`}
        onClick={() => void controller.tuneScannerChannel(channel)}
      >
        <span className="scanner-row-freq">
          {mhz}
          <span className="scanner-row-unit">
            {channel.frequencyHz >= 1e6 ? " M" : " k"}
          </span>
        </span>
        <span className="scanner-row-main">
          <span className="scanner-row-name">{channel.label}</span>
          <span className="scanner-row-meta">{channel.scheme}</span>
        </span>
      </button>
      <div className="scanner-row-actions">
        <button
          type="button"
          className="scanner-icon-btn"
          title={channel.lockedOut ? "Include in scan" : "Lock out of scan"}
          aria-pressed={channel.lockedOut}
          onClick={() => controller.toggleScannerLockout(channel.id)}
        >
          {channel.lockedOut ? "In" : "Out"}
        </button>
        {canRemove && (
          <button
            type="button"
            className="scanner-icon-btn"
            title="Remove channel"
            onClick={() => controller.removeScannerChannel(channel.id)}
          >
            ×
          </button>
        )}
      </div>
    </div>
  );
}
