import type { BlePacket } from "@/lib/bleTypes";

export type DeviceGroup = {
  key: string;
  address: string;
  label: string;
  saved?: boolean;
  identified?: boolean;
  led: boolean;
  packetCount: number;
  packets: BlePacket[];
};

function looksLikeLedRemote(p: BlePacket): boolean {
  if (p.led_remote) return true;
  const hex = p.payload_hex ?? "";
  return (
    hex.includes("0dff55aa") ||
    (p.pdu_type_name === "ADV_NONCONN_IND" && hex.includes("0dff4c00"))
  );
}

function localName(p: BlePacket): string | null {
  const ad = p.ad_structures?.find((a) => a.type === 0x08 || a.type === 0x09);
  return ad?.detail?.trim() || null;
}

export function packetKey(p: BlePacket): string {
  return `${p.capture_id ?? "legacy"}:${p.access_address}:${p.channel}:${p.phy ?? "LE1M"}:${p.sample_offset ?? p.offset_bits}:${p.header_hex}:${p.payload_hex}`;
}

/** Keep a bounded recent window without treating repeated RF broadcasts as duplicates. */
export function mergeRecentPackets(
  previous: BlePacket[],
  incoming: BlePacket[],
  limit = 100,
): BlePacket[] {
  const packets = new Map(previous.map((p) => [packetKey(p), p]));
  let changed = false;
  for (const p of incoming) {
    const key = packetKey(p);
    if (p.crc_ok && !packets.has(key)) {
      packets.set(key, p);
      changed = true;
    }
  }
  return changed ? [...packets.values()].slice(-limit) : previous;
}

export function isLedRemotePacket(p: BlePacket): boolean {
  return looksLikeLedRemote(p);
}

/** Apple Continuity / Find My style — not LED remotes (those use 0dff55aa / 0dff4c00). */
export function isAppleContinuityPacket(p: BlePacket): boolean {
  if (looksLikeLedRemote(p)) return false;
  if (p.summary?.includes("Apple Continuity")) return true;
  return (
    p.ad_structures?.some(
      (a) =>
        a.detail?.includes("Apple Continuity") ||
        a.detail?.includes("Apple manufacturer data"),
    ) ?? false
  );
}

export function isAdvertiserPdu(p: BlePacket): boolean {
  return p.pdu_type_name.startsWith("ADV") || p.pdu_type_name === "SCAN_RSP";
}

export function deviceKey(p: BlePacket): string {
  if (p.adv_address) {
    const header = parseInt(p.header_hex.slice(0, 2), 16);
    const random = !!(header & ([3, 5].includes(p.pdu_type) ? 0x80 : 0x40));
    const addressType = p.adv_address_type ?? (p.packet_kind === "data" ? "unknown" : random ? "random" : "public");
    return `${p.adv_address.toUpperCase()}:${addressType}`;
  }
  if (p.packet_kind === "data") return `link:${p.access_address}`;
  return `anon:${p.access_address}:${p.pdu_type_name}`;
}

export function groupPacketsByDevice(
  packets: BlePacket[],
  opts?: { advertisersOnly?: boolean },
): DeviceGroup[] {
  const map = new Map<string, BlePacket[]>();
  for (const p of packets) {
    const key = deviceKey(p);
    const list = map.get(key);
    if (list) list.push(p);
    else map.set(key, [p]);
  }

  const groups: DeviceGroup[] = [];
  for (const [key, list] of map) {
    if (opts?.advertisersOnly && !list.some(isAdvertiserPdu)) {
      continue; // drop SCAN_REQ-only phantom targets
    }
    const led = list.some(looksLikeLedRemote);
    const names = [
      ...new Set(list.map(localName).filter((n): n is string => !!n)),
    ];
    const address = list.find((p) => p.adv_address)?.adv_address ?? key;
    let label: string;
    if (names.length) label = names[0];
    else if (led) label = "LED remote";
    else if (address.startsWith("link:")) label = "Bluetooth connection";
    else if (address.startsWith("anon:"))
      label = "Anonymous Bluetooth broadcast";
    else label = "Unnamed Bluetooth device";

    groups.push({
      key,
      address,
      label,
      led,
      packetCount: list.length,
      packets: list,
    });
  }

  groups.sort((a, b) => {
    if (a.led !== b.led) return a.led ? -1 : 1;
    return b.packetCount - a.packetCount;
  });
  return groups;
}

type MarkdownOpts = {
  phy?: string;
  channel?: number | null;
  ledOnly?: boolean;
  rfStatus?: {
    backend?: string;
    demo?: boolean;
    rf_power_dbfs?: number;
    center_freq_hz?: number;
  } | null;
};

function packetMarkdown(p: BlePacket): string[] {
  const lines: string[] = [];
  const addr = p.adv_address ? ` ${p.adv_address}` : "";
  const led = looksLikeLedRemote(p) ? " · LED" : "";
  lines.push(`- **${p.pdu_type_name}**${addr}${led} · CRC OK`);
  if (p.channel != null) lines.push(`  - channel: ${p.channel}`);
  if (p.summary) lines.push(`  - ${p.summary}`);
  lines.push(`  - header: \`${p.header_hex}\``);
  lines.push(`  - payload: \`${p.payload_hex}\``);
  lines.push(`  - crc: \`${p.crc_hex}\``);
  if (p.ad_structures?.length) {
    for (const ad of p.ad_structures) {
      lines.push(`  - ${ad.label}: ${ad.detail}`);
    }
  }
  return lines;
}

/** Markdown dump of currently visible BLE detections (grouped by device). */
export function detectionsToMarkdown(
  groups: DeviceGroup[],
  opts: MarkdownOpts = {},
): string {
  const lines: string[] = [];
  const phy = opts.phy ?? "LE1M";
  const ch = opts.channel ?? "?";
  const pduCount = groups.reduce((n, g) => n + g.packetCount, 0);
  lines.push(`# BLE detections (${phy})`);
  lines.push("");
  lines.push(
    `Channel ${ch} · ${groups.length} device${groups.length === 1 ? "" : "s"} · ${pduCount} PDU${pduCount === 1 ? "" : "s"}${opts.ledOnly ? " · LED filter on" : ""}`,
  );
  if (opts.rfStatus) {
    const rf = opts.rfStatus;
    const parts = [
      rf.backend ? `backend=${rf.backend}` : null,
      rf.demo ? "demo" : null,
      rf.rf_power_dbfs != null
        ? `RF ${rf.rf_power_dbfs.toFixed(1)} dBFS`
        : null,
      rf.center_freq_hz != null
        ? `${(rf.center_freq_hz / 1e6).toFixed(0)} MHz`
        : null,
    ].filter(Boolean);
    if (parts.length) {
      lines.push("");
      lines.push(`RF: ${parts.join(" · ")}`);
    }
  }
  if (groups.length === 0) {
    lines.push("");
    lines.push("_No CRC-OK packets._");
    return lines.join("\n");
  }
  for (const g of groups) {
    lines.push("");
    lines.push(`## ${g.label}${g.led ? " (LED)" : ""}`);
    if (!g.address.startsWith("anon:")) {
      lines.push(
        `\`${g.address}\` · ${g.packetCount} PDU${g.packetCount === 1 ? "" : "s"}`,
      );
    } else {
      lines.push(`${g.packetCount} PDU${g.packetCount === 1 ? "" : "s"}`);
    }
    lines.push("");
    for (const p of g.packets) {
      lines.push(...packetMarkdown(p));
    }
  }
  lines.push("");
  return lines.join("\n");
}

/** Exact content identity across captures/channels; different payloads remain distinct. */
export function broadcastKey(p: BlePacket): string {
  return [p.access_address, p.phy ?? "LE1M", p.header_hex, p.payload_hex]
    .map((part) => part.toLowerCase()).join(":");
}

export function broadcastType(p: BlePacket): string {
  if (p.packet_kind === "data") return p.pdu_type_name;
  if (looksLikeLedRemote(p)) return "Remote control broadcast";
  if (isAppleContinuityPacket(p)) return "Apple broadcast";
  const fields = [...new Set((p.ad_structures ?? []).map((ad) =>
    ad.type === 255 ? "Manufacturer data" : ad.type === 8 || ad.type === 9 ? "Device name" : ad.label,
  ))];
  return fields.length ? fields.join(" · ") : p.pdu_type_name;
}

export function groupBroadcasts(packets: BlePacket[]) {
  const grouped = new Map<string, {key: string; packet: BlePacket; count: number; channels: number[]}>();
  for (const packet of packets) {
    const key = broadcastKey(packet);
    const group = grouped.get(key);
    if (group) {
      group.count += 1;
      if (packet.channel !== null && !group.channels.includes(packet.channel))
        group.channels.push(packet.channel);
    } else {
      grouped.set(key, {key, packet, count: 1, channels: packet.channel === null ? [] : [packet.channel]});
    }
  }
  return [...grouped.values()];
}


/** Associate captured link data using decoded connection evidence, not its LL header. */
export function attachDeviceIdentity(packets: BlePacket[], connections: Record<string, unknown>[]): BlePacket[] {
  return packets.map((packet) => {
    if (packet.packet_kind !== "data") return packet;
    const connection = connections.find((entry) =>
      String(entry.access_address).toLowerCase() === packet.access_address.toLowerCase());
    if (typeof connection?.advertiser_address !== "string" ||
        !["random", "public"].includes(String(connection.advertiser_address_type))) return packet;
    return {...packet, adv_address: connection.advertiser_address,
      adv_address_type: connection.advertiser_address_type as "random" | "public"};
  });
}

/** Full session storage is independent of the small live display window. */
export class BleSessionCapture {
  private readonly packets = new Map<string, BlePacket>();
  constructor(readonly limit = 10_000) {}
  get full() { return this.packets.size >= this.limit; }
  clear() { this.packets.clear(); }
  append(incoming: BlePacket[]): BlePacket[] {
    const accepted: BlePacket[] = [];
    for (const packet of incoming) {
      if (!packet.crc_ok) continue;
      const key = packetKey(packet);
      if (this.packets.has(key)) continue;
      if (this.full) break;
      this.packets.set(key, packet);
      accepted.push(packet);
    }
    return accepted;
  }
  forDevice(key: string): BlePacket[] {
    return [...this.packets.values()].filter((packet) => deviceKey(packet) === key);
  }
}

/** Convert tracked BLE identity to the packet-feed identity used for grouping. */
export function bleTrackGroupKey(id: string): string | null {
  if (id.startsWith("ble-link:")) return `link:${id.slice("ble-link:".length)}`;
  if (id.startsWith("ble:")) return id.slice("ble:".length);
  return null;
}
