"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { signalRequest } from "@/lib/api";
import { knownBleDevices, savedBroadcastLabels } from "@/lib/bleSavedGroups";
import { broadcastKey } from "@/lib/bleGroups";
import { BleSavedLibrary } from "./BleSavedLibrary";
import { BleDeviceGroup } from "./BleDeviceGroup";
import { BleSortFilterBar } from "./BleSortFilterBar";
import {
  DEFAULT_FILTERS,
  DEFAULT_SORT,
  activeFilterCount,
  arrivalOrder,
  sortAndFilterGroups,
  typeCounts,
  type BleFilters,
  type BleSort,
} from "@/lib/bleSort";
import { RfTrackRow } from "./RfTrackRow";
import type { SignalTrack } from "@/lib/api";
import {
  bleTrackGroupKey,
  detectionsToMarkdown,
  groupPacketsByDevice,
  isAppleContinuityPacket,
  isLedRemotePacket,
  packetKey,
} from "@/lib/bleGroups";
import type { BleDecodeResult, BlePacket, SavedBleMessages } from "@/lib/bleTypes";
export type { BlePacket, BleDecodeResult };

const SORT_STORAGE_KEY = "sigsense.ble-sort";
/** Live rows re-sort this often, so a changing count doesn't make them jump. */
const LIVE_RESORT_MS = 2000;

function readSort(): BleSort {
  try {
    const saved = JSON.parse(localStorage.getItem(SORT_STORAGE_KEY) ?? "null") as BleSort | null;
    if (saved && typeof saved.key === "string" && (saved.dir === "asc" || saved.dir === "desc")) return saved;
  } catch {
    /* storage blocked or unreadable: use the default */
  }
  return DEFAULT_SORT;
}
type Props = {
  result: BleDecodeResult | null;
  history?: BlePacket[];
  onClearHistory?: () => void;
  listening?: boolean;
  savedView?: boolean;
  identificationRevision?: number;
  broadcastLabels?: Record<string, string>;
  getDevicePackets?: (key: string) => BlePacket[];
  tracks?: SignalTrack[];
  activeTrackIds?: string[];
  selectedTrackId?: string;
  onSelectTrack?: (track: SignalTrack) => void;
  onNameTrack?: (track: SignalTrack, name: string) => Promise<void>;
  rfStatus?: {
    backend?: string;
    demo?: boolean;
    rf_power_dbfs?: number;
    iq_samples?: number;
    center_freq_hz?: number;
    ble_hop?: boolean;
    ble_decode?: boolean;
  } | null;
};
export function BlePacketList({
  result,
  history,
  onClearHistory,
  rfStatus,
  listening,
  savedView = false,
  getDevicePackets,
  broadcastLabels,
  identificationRevision = 0,
  tracks = [],
  activeTrackIds = [],
  selectedTrackId,
  onSelectTrack,
  onNameTrack,
}: Props) {
  const [savedItems, setSavedItems] = useState<SavedBleMessages[]>([]);
  const [deviceNames, setDeviceNames] = useState<Record<string, string>>({});
  const [libraryError, setLibraryError] = useState("");
  const [deduplicate, setDeduplicate] = useState(true);
  const refreshLibrary = useCallback(async () => {
    const data = await signalRequest<{items: SavedBleMessages[]; device_names?: Record<string, string>}>("/ble/saved");
    setSavedItems(data.items);
    setDeviceNames(data.device_names ?? {});
    setLibraryError("");
  }, []);
  useEffect(() => {
    if (savedView) return;
    let active = true;
    signalRequest<{items: SavedBleMessages[]; device_names?: Record<string, string>}>("/ble/saved")
      .then((data) => {
        if (active) {
          setSavedItems(data.items);
          setDeviceNames(data.device_names ?? {});
        }
      })
      .catch((error) => { if (active) setLibraryError(String(error)); });
    return () => { active = false; };
  }, [savedView, identificationRevision]);
  const demo = rfStatus?.demo ?? false;
  const knownDevices = useMemo(() => knownBleDevices(savedItems, demo, deviceNames), [savedItems, demo, deviceNames]);
  const labels = useMemo(() => broadcastLabels ?? savedBroadcastLabels(savedItems.filter((item) => item.demo === demo)),
    [savedItems, broadcastLabels, demo]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [sort, setSortState] = useState<BleSort>(readSort);
  const [filters, setFilters] = useState<BleFilters>(DEFAULT_FILTERS);
  const [orderPaused, setOrderPaused] = useState(false);
  const [orderTick, setOrderTick] = useState(0);
  const setSort = useCallback((next: BleSort) => {
    setSortState(next);
    try {
      localStorage.setItem(SORT_STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* the choice still applies for this visit */
    }
  }, []);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const [frozen, setFrozen] = useState<BlePacket[] | null>(null);
  const source = history ?? result?.packets;
  const livePackets = useMemo(() => {
    const unique = new Map<string, BlePacket>();
    for (const p of source ?? [])
      if (p.crc_ok) unique.set(packetKey(p), p);
    return [...unique.values()];
  }, [source]);
  const packets = frozen ?? livePackets;
  const liveView = history !== undefined;
  const bleTracks = useMemo(() => new Map(tracks
    .map((track) => [bleTrackGroupKey(track.id), track] as const)
    .filter((entry): entry is readonly [string, SignalTrack] => entry[0] !== null)), [tracks]);
  const allGroups = useMemo(() => {
    const grouped = groupPacketsByDevice(packets).map((group) => {
      const known = knownDevices.get(group.key);
      const track = bleTracks.get(group.key);
      return known || track ? {...group,
        label: known?.name ?? track?.name ?? group.label,
        saved: known?.saved,
        identified: Boolean(known || track)} : group;
    });
    // A device should stay in place while its message count changes.
    return liveView
      ? grouped.sort((a, b) => a.key.localeCompare(b.key))
      : grouped;
  }, [packets, liveView, knownDevices, bleTracks]);
  const rfOnlyTracks = useMemo(() => tracks.filter((track) =>
    !allGroups.some((group) => bleTrackGroupKey(track.id) === group.key)), [tracks, allGroups]);
  const groups = useMemo(() => {
    const search = query.trim().toLowerCase();
    return allGroups
      .map((group) => ({
        ...group,
        packets: group.packets.filter((p) => {
          if (filter === "led" && !isLedRemotePacket(p)) return false;
          if (filter === "no-apple" && isAppleContinuityPacket(p)) return false;
          if (filter === "connections")
            return p.packet_kind === "data" || p.pdu_type_name === "CONNECT_IND";
          return true;
        }),
      }))
      .filter((group) => group.packets.length > 0)
      .map((group) => ({ ...group, packetCount: group.packets.length }))
      .filter((g) =>
        !search || [g.label, g.address, bleTracks.get(g.key)?.label, bleTracks.get(g.key)?.evidence, ...g.packets.flatMap((p) => [p.summary ?? "", labels[broadcastKey(p)] ?? ""])]
          .join(" ").toLowerCase().includes(search),
      );
  }, [allGroups, query, filter, labels, bleTracks]);
  const visibleRfTracks = useMemo(() => rfOnlyTracks.filter((track) =>
    !query || [track.name, track.label, track.id, track.evidence, track.identity_note].join(" ").toLowerCase().includes(query.trim().toLowerCase())), [rfOnlyTracks, query]);
  const metricsCtx = useMemo(() => ({
    order: arrivalOrder(packets),
    fallbackStrengthDbfs: (key: string) => bleTracks.get(key)?.power_dbfs ?? null,
  }), [packets, bleTracks]);
  const sortedRows = useMemo(
    () => sortAndFilterGroups(groups, sort, filters, metricsCtx),
    [groups, sort, filters, metricsCtx],
  );
  const typeTotals = useMemo(() => typeCounts(allGroups, metricsCtx), [allGroups, metricsCtx]);
  useEffect(() => {
    if (!liveView || orderPaused) return;
    const id = setInterval(() => setOrderTick((tick) => tick + 1), LIVE_RESORT_MS);
    return () => clearInterval(id);
  }, [liveView, orderPaused]);
  // Live: keep the last order until the next tick (or a sort/filter change);
  // saved sets are static, so they sort immediately.
  const liveOrder = useMemo(
    () => sortedRows.map((row) => row.group.key),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-sort only on a tick or when sort/filters change
    [orderTick, sort, filters],
  );
  const shownGroups = useMemo(() => {
    if (!liveView) return sortedRows.map((row) => row.group);
    const byKey = new Map(sortedRows.map((row) => [row.group.key, row.group]));
    const ordered = liveOrder.flatMap((key) => (byKey.has(key) ? [byKey.get(key)!] : []));
    const placed = new Set(liveOrder);
    // Devices that appeared since the last tick wait at the end until it fires.
    return [...ordered, ...sortedRows.map((row) => row.group).filter((group) => !placed.has(group.key))];
  }, [liveView, sortedRows, liveOrder]);
  const previouslyIdentified = useMemo(() => {
    const present = new Set([...allGroups.map((group) => group.key), ...rfOnlyTracks.map((track) => bleTrackGroupKey(track.id) ?? track.id)]);
    const search = query.trim().toLowerCase();
    return [...knownDevices.values()].filter((device) => !present.has(device.key) &&
      (!search || [device.name, device.key, ...device.broadcastNames].join(" ").toLowerCase().includes(search)));
  }, [allGroups, knownDevices, query, rfOnlyTracks]);
  const hasFilters = filter !== "all" || !!query || activeFilterCount(filters) > 0;
  const decoded = packets.length > 0;
  return (
    <section className="overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--panel)]">
      <div className="border-b border-[var(--line)] p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-lg font-medium">Live devices & RF signals</h3>
            <p className="mt-1 text-xs text-[var(--muted)]">
              Bluetooth messages and classified RF signals share one feed. Matching Bluetooth identities appear as a single device.
              {rfStatus?.demo ? " Synthetic demo source." : decoded ? " Bluetooth packets are checksum verified." : ""}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {liveView && (
              <button
                type="button"
                aria-pressed={frozen !== null}
                disabled={!packets.length}
                className="rounded-md border border-[var(--line)] px-3 py-2 text-xs hover:border-[var(--signal)] disabled:opacity-40"
                onClick={() => setFrozen(frozen ? null : livePackets)}
              >
                {frozen ? "Resume updates" : "Freeze to inspect"}
              </button>
            )}
            <span
              className={`rounded-full px-3 py-1 text-[10px] ${decoded ? "bg-teal-400/10 text-teal-200" : "bg-white/5 text-[var(--muted)]"}`}
            >
              {frozen
                ? "Inspection frozen"
                : decoded
                  ? "✓ Valid messages received"
                  : result
                    ? "No valid messages yet"
                    : "Waiting for capture"}
            </span>
          </div>
        </div>
        {frozen && (
          <p role="status" className="mt-3 text-xs text-amber-200">
            Messages are frozen for inspection.{" "}
            {listening
              ? "The receiver continues collecting recent messages in the background."
              : "Resume updates to return to recent messages."}
          </p>
        )}
        {listening !== undefined && !frozen && (
          <p className="mt-3 text-xs text-[var(--muted)]">
            {listening
              ? rfStatus?.ble_decode === false
                ? "Bluetooth decoding is off. Start Bluetooth listening above to enable it."
                : rfStatus?.ble_hop
                  ? "Listening across channels 37, 38 and 39. Press a remote button to see its broadcasts."
                  : "Receiver active on a fixed frequency. Enable advertising channel hopping to search all three channels."
              : "Receiver stopped. Start Bluetooth listening above to discover devices."}
          </p>
        )}
        <div className="mt-4 grid grid-cols-3 gap-3">
          <Stat value={allGroups.length + rfOnlyTracks.length} label="feed entities" />
          <Stat value={packets.length} label="verified BLE messages" />
          <Stat value={tracks.length} label="classified RF tracks" />
        </div>
        <label className="mt-4 flex items-center gap-2 text-xs text-[var(--muted)]">
          <input type="checkbox" checked={deduplicate} onChange={(event) => setDeduplicate(event.target.checked)} />
          Collapse identical broadcasts · retain repeat counts
        </label>
        <p className="mt-2 text-[11px] text-[var(--muted)]">Different payloads remain separate. Save a broadcast with a type label to identify it when it appears again.</p>
        {getDevicePackets && <p className="mt-2 text-[11px] text-[var(--muted)]">Device saves include all messages from this listening session. Sessions stop at 10,000 messages so they can be saved before starting another.</p>}
        <div className="mt-4 flex flex-wrap gap-2">
          <input
            className="field min-w-[10rem] flex-1 text-xs"
            aria-label="Search devices and signals"
            placeholder="Search name, address, RF class or message…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            aria-label="Bluetooth message filter"
            className="field text-xs"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="all">All Bluetooth messages</option>
            <option value="led">LED remotes</option>
            <option value="connections">Connection traffic</option>
            <option value="no-apple">Exclude Apple broadcasts</option>
          </select>
        </div>
        <BleSortFilterBar
          sort={sort}
          onSort={setSort}
          filters={filters}
          onFilters={setFilters}
          counts={typeTotals}
          shown={shownGroups.length}
          total={allGroups.length}
          live={liveView}
          orderPaused={orderPaused}
          onToggleOrderPaused={() => setOrderPaused((paused) => !paused)}
        />
      </div>
      {shownGroups.length || visibleRfTracks.length ? (
        <div className="max-h-[36rem] space-y-2 overflow-auto p-3">
          {shownGroups.map((group) => (
            <BleDeviceGroup
              key={group.key}
              group={group}
              track={bleTracks.get(group.key)}
              active={Boolean(bleTracks.get(group.key) && activeTrackIds.includes(bleTracks.get(group.key)!.id))}
              defaultChannel={result?.channel}
              deduplicate={deduplicate}
              labels={labels}
              demo={rfStatus?.demo}
              selected={selectedTrackId === bleTracks.get(group.key)?.id}
              onSelectTrack={onSelectTrack}
              onNameTrack={onNameTrack}
              onSaved={refreshLibrary}
              getDevicePackets={getDevicePackets ?? ((key) => allGroups.find((group) => group.key === key)?.packets ?? [])}
            />
          ))}
          {visibleRfTracks.map((track) => (
            <RfTrackRow key={track.id} track={track}
              active={activeTrackIds.includes(track.id)} selected={selectedTrackId === track.id}
              onSelect={() => onSelectTrack?.(track)}
              onName={async (name) => { if (onNameTrack) await onNameTrack(track, name); }} />
          ))}
        </div>
      ) : (
        <div className="space-y-3 p-5 text-sm text-[var(--muted)]">
          <p className="text-[var(--foreground)]">
            {hasFilters
              ? "No devices match these filters."
              : "No Bluetooth messages decoded yet."}
          </p>
          {hasFilters ? (
            <button
              className="text-xs text-[var(--signal)]"
              onClick={() => {
                setQuery("");
                setFilter("all");
              }}
            >
              Show all devices
            </button>
          ) : (
            <ol className="ml-4 list-decimal space-y-2 text-xs leading-relaxed">
              <li>
                In Live SDR, choose{" "}
                <strong>Listen for Bluetooth devices</strong>. Use Demo to try
                it without a radio, or HackRF for nearby signals.
              </li>
              <li>
                For a button remote, press a button several times while
                listening. Other devices may broadcast automatically.
              </li>
              <li>
                For a saved IQ file, use its original center frequency and
                sample rate. A waterfall image cannot provide packet bytes.
              </li>
            </ol>
          )}
          {result && (
            <p className="text-xs">
              No packets does not mean no devices: the capture may miss a short
              transmission, use the wrong channel, or contain a PHY this decoder
              does not support.
            </p>
          )}
        </div>
      )}
      {liveView && !savedView && filter === "all" && previouslyIdentified.length > 0 && (
        <details open className="border-t border-[var(--line)] p-4">
          <summary className="cursor-pointer text-sm font-medium">Previously identified · {previouslyIdentified.length}</summary>
          <p className="mt-2 text-xs text-[var(--muted)]">Not in the recent feed. These devices have no messages in the current display window.</p>
          <div className="mt-3 max-h-64 space-y-2 overflow-auto">
            {previouslyIdentified.map((device) => (
              <div key={device.key} className="rounded-lg border border-[var(--line)] p-3 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{device.name ?? "Saved Bluetooth device"}</span>
                  <span className="text-[var(--muted)]">{device.saved ? "Saved device" : "Named device"} · not in recent feed</span>
                </div>
                <p className="mt-1 font-mono text-[10px] text-[var(--muted)]">{device.key}</p>
                {!!device.broadcastNames.length && <p className="mt-2 text-[var(--signal)]">{device.broadcastNames.join(" · ")}</p>}
              </div>
            ))}
          </div>
        </details>
      )}
      {!!result?.warnings?.length && (
        <div className="space-y-1 border-t border-[var(--line)] px-4 py-3">
          {result.warnings.map((w) => (
            <p className="text-[11px] text-amber-200" key={w}>
              {w}
            </p>
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--line)] px-4 py-3 text-[11px] text-[var(--muted)]">
        <span>
          {hasFilters ? `${shownGroups.length} of ${allGroups.length} shown · ` : ""}
          {history && !savedView ? "Latest 100 messages shown · " : ""}Repeated broadcasts count as activity. Private addresses may change.
        </span>
        <div className="flex gap-3">
          {onClearHistory && (
            <button onClick={() => {
              setFrozen(null);
              onClearHistory();
            }}>Clear recent messages</button>
          )}
          <button
            disabled={!shownGroups.length}
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(
                  detectionsToMarkdown(shownGroups, {
                    phy: result?.phy,
                    channel: result?.channel,
                    rfStatus,
                  }),
                );
                setCopied(true);
                setCopyError(false);
                window.setTimeout(() => setCopied(false), 1600);
              } catch {
                setCopyError(true);
              }
            }}
          >
            {copied ? "Copied" : "Copy summary"}
          </button>
        </div>
        {copyError && (
          <span role="status">
            Clipboard unavailable. Copy requires a secure browser connection.
          </span>
        )}
      </div>
      {!savedView && <BleSavedLibrary items={savedItems} onRefresh={refreshLibrary} />}
      {libraryError && <p role="status" className="px-4 pb-3 text-xs text-amber-200">Bluetooth library unavailable: {libraryError}</p>}
    </section>
  );
}
function Stat({ value, label }: { value: number | string; label: string }) {
  return (
    <div className="rounded-lg bg-black/15 px-3 py-2">
      <div className="font-mono text-lg text-[var(--signal)]">{value}</div>
      <div className="mt-1 text-[10px] text-[var(--muted)]">{label}</div>
    </div>
  );
}
