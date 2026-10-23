"use client";

import { useCallback, useMemo, useState } from "react";
import { BleDeviceGroup } from "@/components/BleDeviceGroup";
import {
  detectionsToMarkdown,
  groupPacketsByDevice,
  isAppleContinuityPacket,
  isLedRemotePacket,
  packetKey,
} from "@/lib/bleGroups";
import type { BleDecodeResult, BlePacket } from "@/lib/bleTypes";

export type { BlePacket, BleDecodeResult };

type Props = {
  result: BleDecodeResult | null;
  history?: BlePacket[];
  onClearHistory?: () => void;
  rfStatus?: {
    backend?: string;
    demo?: boolean;
    rf_power_dbfs?: number;
    iq_samples?: number;
    center_freq_hz?: number;
    ble_hop?: boolean;
  } | null;
};

export function BlePacketList({
  result,
  history,
  onClearHistory,
  rfStatus,
}: Props) {
  const [ledOnly, setLedOnly] = useState(false);
  const [hideApple, setHideApple] = useState(true);
  const [advertisersOnly, setAdvertisersOnly] = useState(true);
  const [copied, setCopied] = useState(false);

  const packets = useMemo(() => {
    const list = history?.length ? history : (result?.packets ?? []);
    const seen = new Set<string>();
    const unique: BlePacket[] = [];
    for (const p of list) {
      if (!p.crc_ok) continue;
      const k = packetKey(p);
      if (seen.has(k)) continue;
      seen.add(k);
      unique.push(p);
    }
    let filtered = unique;
    if (ledOnly) filtered = filtered.filter(isLedRemotePacket);
    else if (hideApple)
      filtered = filtered.filter((p) => !isAppleContinuityPacket(p));
    return filtered;
  }, [result, history, ledOnly, hideApple]);

  const groups = useMemo(
    () => groupPacketsByDevice(packets, { advertisersOnly }),
    [packets, advertisersOnly],
  );

  const ledCount = useMemo(() => {
    const list = history?.length ? history : (result?.packets ?? []);
    return list.filter((p) => p.crc_ok && isLedRemotePacket(p)).length;
  }, [result, history]);

  const defaultChannel = result?.channel ?? null;

  const copyMarkdown = useCallback(async () => {
    const md = detectionsToMarkdown(groups, {
      phy: result?.phy,
      channel: result?.channel,
      ledOnly,
      rfStatus,
    });
    try {
      await navigator.clipboard.writeText(md);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }, [groups, result?.phy, result?.channel, ledOnly, rfStatus]);

  if (!result && (!history || history.length === 0)) {
    return (
      <div className="border border-[var(--line)] bg-[var(--panel)] p-4 text-sm text-[var(--muted)]">
        BLE packets appear here when decode is enabled (HackRF @ 2402/2426/2480
        MHz) or after Analyze → Decode BLE.
      </div>
    );
  }

  return (
    <div className="border border-[var(--line)] bg-[var(--panel)]">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-[var(--line)] px-4 py-3">
        <div>
          <p className="font-[family-name:var(--font-display)] text-lg text-[var(--foreground)]">
            BLE {result?.phy ?? "LE1M"}
          </p>
          <p className="text-xs text-[var(--muted)]">
            ch {result?.channel ?? "?"}
            {rfStatus?.ble_hop ? " hopping" : ""} · {groups.length} device
            {groups.length === 1 ? "" : "s"} · {packets.length} PDU
            {packets.length === 1 ? "" : "s"} · LED-like {ledCount}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-[var(--muted)]">
            <input
              type="checkbox"
              checked={ledOnly}
              onChange={(e) => setLedOnly(e.target.checked)}
            />
            LED remotes only
          </label>
          <label className="flex items-center gap-2 text-xs text-[var(--muted)]">
            <input
              type="checkbox"
              checked={hideApple}
              disabled={ledOnly}
              onChange={(e) => setHideApple(e.target.checked)}
            />
            Hide Apple
          </label>
          <label className="flex items-center gap-2 text-xs text-[var(--muted)]">
            <input
              type="checkbox"
              checked={advertisersOnly}
              onChange={(e) => setAdvertisersOnly(e.target.checked)}
            />
            Advertisers only
          </label>
          {onClearHistory ? (
            <button
              type="button"
              onClick={onClearHistory}
              className="border border-[var(--line)] px-2.5 py-1 text-xs text-[var(--muted)] hover:border-[var(--signal)]"
            >
              Clear
            </button>
          ) : null}
          <button
            type="button"
            onClick={copyMarkdown}
            disabled={groups.length === 0}
            className="border border-[var(--line)] px-2.5 py-1 text-xs text-[var(--foreground)] hover:border-[var(--signal)] disabled:opacity-40"
          >
            {copied ? "Copied" : "Copy results"}
          </button>
        </div>
      </div>
      {groups.length === 0 ? (
        <div className="space-y-2 px-4 py-6 text-sm text-[var(--muted)]">
          <p>
            {ledOnly
              ? "No LED-remote AdvData yet. Remotes only TX on button press (ADV_NONCONN 0dff55aa… / 0dff4c00…)."
              : "No matching CRC-OK advertising PDUs."}
          </p>
          {rfStatus ? (
            <p className="font-mono text-xs">
              backend={rfStatus.backend ?? "?"}
              {rfStatus.demo ? " (demo)" : ""}
              {rfStatus.ble_hop ? " · hop 37/38/39" : ""}
              {rfStatus.rf_power_dbfs != null
                ? ` · RF ${rfStatus.rf_power_dbfs.toFixed(1)} dBFS`
                : ""}
              {rfStatus.center_freq_hz != null
                ? ` · ${(rfStatus.center_freq_hz / 1e6).toFixed(0)} MHz`
                : ""}
            </p>
          ) : null}
          <p className="text-xs">
            {ledOnly
              ? "Enable Hop ADV 37/38/39, hold the remote near the HackRF, and mash ON/OFF while watching this panel. Apple Continuity is not your remotes."
              : "Press the physical remote while hopping ADV channels. Apple Continuity (`4c00` after Flags) is not LED."}
          </p>
        </div>
      ) : (
        <div className="max-h-96 overflow-auto">
          {groups.map((g) => (
            <BleDeviceGroup
              key={g.key}
              group={g}
              defaultChannel={defaultChannel}
            />
          ))}
        </div>
      )}
    </div>
  );
}
