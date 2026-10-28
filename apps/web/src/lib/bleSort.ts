import type { BlePacket } from "@/lib/bleTypes";
import { isAppleContinuityPacket, isLedRemotePacket, type DeviceGroup } from "@/lib/bleGroups";

/** Newest packets averaged for "strength", so one fading burst doesn't reorder rows. */
const RECENT_FOR_STRENGTH = 5;

export type BleDeviceType =
  | "named"
  | "unnamed"
  | "led-remote"
  | "apple"
  | "beacon"
  | "connectable"
  | "non-connectable"
  | "scannable"
  | "connection"
  | "anonymous";

export const DEVICE_TYPES: { id: BleDeviceType; label: string; hint: string }[] = [
  { id: "named", label: "Named", hint: "Broadcasts a device name" },
  { id: "unnamed", label: "Unnamed", hint: "No name in its broadcasts" },
  { id: "led-remote", label: "LED remote", hint: "Remote-control style broadcasts" },
  { id: "apple", label: "Apple", hint: "Apple Continuity / Find My style" },
  { id: "beacon", label: "Beacon", hint: "iBeacon or Eddystone" },
  { id: "connectable", label: "Connectable", hint: "Accepts connections (ADV_IND)" },
  { id: "non-connectable", label: "Non-connectable", hint: "Broadcast only (ADV_NONCONN_IND)" },
  { id: "scannable", label: "Scannable", hint: "Answers scan requests" },
  { id: "connection", label: "Connection traffic", hint: "Data or connection requests" },
  { id: "anonymous", label: "Anonymous", hint: "No advertiser address" },
];

export type BleSortKey =
  | "strength"
  | "peak"
  | "messages"
  | "last-seen"
  | "name"
  | "type";

export const SORT_OPTIONS: { id: BleSortKey; label: string; defaultDir: "asc" | "desc" }[] = [
  { id: "strength", label: "Signal strength", defaultDir: "desc" },
  { id: "peak", label: "Peak strength", defaultDir: "desc" },
  { id: "messages", label: "Message count", defaultDir: "desc" },
  { id: "last-seen", label: "Last seen", defaultDir: "desc" },
  { id: "name", label: "Name", defaultDir: "asc" },
  { id: "type", label: "Type", defaultDir: "asc" },
];

export type BleSort = { key: BleSortKey; dir: "asc" | "desc" };

export type BleFilters = {
  /** Show a device if it has ANY of these types. Empty = no type filter. */
  types: BleDeviceType[];
  /** Hide devices weaker than this (dBFS). null = no limit. */
  minStrengthDbfs: number | null;
  minMessages: number;
  /** Advertising channels (37-39); a device matches if seen on ANY. Empty = all. */
  channels: number[];
  phy: "all" | "LE1M" | "LE2M";
  identity: "all" | "identified" | "saved";
  addressType: "all" | "public" | "random";
};

export const DEFAULT_FILTERS: BleFilters = {
  types: [],
  minStrengthDbfs: null,
  minMessages: 0,
  channels: [],
  phy: "all",
  identity: "all",
  addressType: "all",
};

export const DEFAULT_SORT: BleSort = { key: "messages", dir: "desc" };

/** What sorting and filtering read from one device row. */
export type GroupMetrics = {
  /** Mean of the newest packets' RSSI, or the RF track's power as a fallback. */
  strengthDbfs: number | null;
  peakDbfs: number | null;
  messages: number;
  /** Position of the newest packet in the arrival order (bigger = newer). */
  lastSeen: number;
  channels: number[];
  phys: string[];
  types: Set<BleDeviceType>;
  addressTypes: Set<"public" | "random">;
};

/** Optional per-device context from outside the packets. */
export type MetricsContext = {
  /** Arrival position of each packet (same object identity as in the groups). */
  order?: Map<BlePacket, number>;
  /** RF power for a device when its packets carry no RSSI. */
  fallbackStrengthDbfs?: (groupKey: string) => number | null | undefined;
};

/** Arrival order for a chronological packet list, for lastSeen. */
export function arrivalOrder(packets: BlePacket[]): Map<BlePacket, number> {
  const order = new Map<BlePacket, number>();
  packets.forEach((packet, index) => order.set(packet, index));
  return order;
}

function isBeacon(p: BlePacket): boolean {
  return (p.ad_structures ?? []).some((ad) => {
    const hex = ad.data_hex.toLowerCase();
    // iBeacon: Apple manufacturer data 4c00 0215; Eddystone: service data AA FE.
    return (ad.type === 0xff && hex.startsWith("4c000215")) || (ad.type === 0x16 && hex.startsWith("aafe"));
  });
}

function hasName(p: BlePacket): boolean {
  return (p.ad_structures ?? []).some((ad) => (ad.type === 0x08 || ad.type === 0x09) && ad.detail?.trim());
}

function addressTypeOf(p: BlePacket): "public" | "random" | null {
  if (p.adv_address_type) return p.adv_address_type;
  if (!p.adv_address) return null;
  const header = parseInt(p.header_hex.slice(0, 2), 16);
  if (Number.isNaN(header)) return null;
  const random = !!(header & ([3, 5].includes(p.pdu_type) ? 0x80 : 0x40));
  return random ? "random" : "public";
}

export function groupMetrics(group: DeviceGroup, ctx: MetricsContext = {}): GroupMetrics {
  const packets = group.packets;
  const rssi = packets
    .map((p) => p.rssi_dbfs)
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  let strength: number | null = null;
  if (rssi.length) {
    const recent = rssi.slice(-RECENT_FOR_STRENGTH);
    strength = recent.reduce((a, b) => a + b, 0) / recent.length;
  } else {
    strength = ctx.fallbackStrengthDbfs?.(group.key) ?? null;
  }
  const peak = rssi.length ? Math.max(...rssi) : strength;

  let lastSeen = -1;
  packets.forEach((p, i) => {
    const at = ctx.order?.get(p) ?? i;
    if (at > lastSeen) lastSeen = at;
  });

  const types = new Set<BleDeviceType>();
  types.add(packets.some(hasName) ? "named" : "unnamed");
  if (group.led || packets.some(isLedRemotePacket)) types.add("led-remote");
  if (packets.some(isAppleContinuityPacket)) types.add("apple");
  if (packets.some(isBeacon)) types.add("beacon");
  if (packets.some((p) => p.pdu_type_name === "ADV_IND" || p.pdu_type_name === "ADV_DIRECT_IND")) types.add("connectable");
  if (packets.some((p) => p.pdu_type_name === "ADV_NONCONN_IND")) types.add("non-connectable");
  if (packets.some((p) => p.pdu_type_name === "ADV_SCAN_IND" || p.pdu_type_name === "SCAN_RSP")) types.add("scannable");
  if (packets.some((p) => p.packet_kind === "data" || p.pdu_type_name === "CONNECT_IND")) types.add("connection");
  if (group.key.startsWith("anon:")) types.add("anonymous");

  const addressTypes = new Set<"public" | "random">();
  for (const p of packets) {
    const t = addressTypeOf(p);
    if (t) addressTypes.add(t);
  }

  return {
    strengthDbfs: strength,
    peakDbfs: peak,
    messages: group.packetCount,
    lastSeen,
    channels: [...new Set(packets.map((p) => p.channel).filter((c): c is number => c !== null))].sort((a, b) => a - b),
    phys: [...new Set(packets.map((p) => p.phy ?? "LE1M"))],
    types,
    addressTypes,
  };
}

export function activeFilterCount(filters: BleFilters): number {
  return (
    (filters.types.length ? 1 : 0) +
    (filters.minStrengthDbfs !== null ? 1 : 0) +
    (filters.minMessages > 0 ? 1 : 0) +
    (filters.channels.length ? 1 : 0) +
    (filters.phy !== "all" ? 1 : 0) +
    (filters.identity !== "all" ? 1 : 0) +
    (filters.addressType !== "all" ? 1 : 0)
  );
}

export function matchesFilters(group: DeviceGroup, m: GroupMetrics, f: BleFilters): boolean {
  if (f.types.length && !f.types.some((t) => m.types.has(t))) return false;
  if (f.minStrengthDbfs !== null && (m.strengthDbfs === null || m.strengthDbfs < f.minStrengthDbfs)) return false;
  if (m.messages < f.minMessages) return false;
  if (f.channels.length && !f.channels.some((c) => m.channels.includes(c))) return false;
  if (f.phy !== "all" && !m.phys.includes(f.phy)) return false;
  if (f.identity === "identified" && !group.identified && !group.saved) return false;
  if (f.identity === "saved" && !group.saved) return false;
  if (f.addressType !== "all" && !m.addressTypes.has(f.addressType)) return false;
  return true;
}

const TYPE_RANK: BleDeviceType[] = [
  "connection",
  "led-remote",
  "beacon",
  "apple",
  "named",
  "connectable",
  "scannable",
  "non-connectable",
  "unnamed",
  "anonymous",
];

function primaryTypeRank(m: GroupMetrics): number {
  const index = TYPE_RANK.findIndex((t) => m.types.has(t));
  return index === -1 ? TYPE_RANK.length : index;
}

/**
 * Compare two rows for one key in the key's natural direction (ascending for
 * name/type, descending-first values like strength sort via `dir`). Missing
 * values always sort last whatever the direction.
 */
function compare(a: { g: DeviceGroup; m: GroupMetrics }, b: { g: DeviceGroup; m: GroupMetrics }, key: BleSortKey): number {
  switch (key) {
    case "strength":
    case "peak": {
      const av = key === "strength" ? a.m.strengthDbfs : a.m.peakDbfs;
      const bv = key === "strength" ? b.m.strengthDbfs : b.m.peakDbfs;
      if (av === null && bv === null) return 0;
      if (av === null) return 1; // handled by caller for direction
      if (bv === null) return -1;
      return av - bv;
    }
    case "messages":
      return a.m.messages - b.m.messages;
    case "last-seen":
      return a.m.lastSeen - b.m.lastSeen;
    case "name":
      return a.g.label.localeCompare(b.g.label, undefined, { sensitivity: "base", numeric: true });
    case "type":
      return primaryTypeRank(a.m) - primaryTypeRank(b.m);
  }
}

/**
 * Filter then sort. The sort is stable and always ends on the device key, so
 * equal rows never swap places between renders.
 */
export function sortAndFilterGroups<T extends DeviceGroup>(
  groups: T[],
  sort: BleSort,
  filters: BleFilters,
  ctx: MetricsContext = {},
): { group: T; metrics: GroupMetrics }[] {
  const rows = groups
    .map((g) => ({ g, m: groupMetrics(g, ctx) }))
    .filter(({ g, m }) => matchesFilters(g, m, filters));
  const sign = sort.dir === "asc" ? 1 : -1;
  rows.sort((a, b) => {
    const missingA = (sort.key === "strength" && a.m.strengthDbfs === null) || (sort.key === "peak" && a.m.peakDbfs === null);
    const missingB = (sort.key === "strength" && b.m.strengthDbfs === null) || (sort.key === "peak" && b.m.peakDbfs === null);
    if (missingA !== missingB) return missingA ? 1 : -1; // no signal reading: always last
    const c = compare(a, b, sort.key) * sign;
    return c !== 0 ? c : a.g.key.localeCompare(b.g.key);
  });
  return rows.map(({ g, m }) => ({ group: g as T, metrics: m }));
}

/** How many devices carry each type, for chip badges. */
export function typeCounts(groups: DeviceGroup[], ctx: MetricsContext = {}): Record<BleDeviceType, number> {
  const counts = Object.fromEntries(DEVICE_TYPES.map((t) => [t.id, 0])) as Record<BleDeviceType, number>;
  for (const g of groups) for (const t of groupMetrics(g, ctx).types) counts[t] += 1;
  return counts;
}

/** Plain-language strength label for a reading in dBFS. */
export function strengthLabel(dbfs: number | null): string {
  if (dbfs === null) return "no reading";
  if (dbfs >= -35) return "very strong";
  if (dbfs >= -50) return "strong";
  if (dbfs >= -65) return "moderate";
  if (dbfs >= -80) return "weak";
  return "very weak";
}
