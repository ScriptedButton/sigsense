import type { BlePacket } from "@/lib/bleTypes";

export type DeviceGroup = {
  key: string;
  address: string;
  label: string;
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
  const ad = p.ad_structures?.find(
    (a) => a.type === 0x08 || a.type === 0x09,
  );
  return ad?.detail?.trim() || null;
}

export function packetKey(p: BlePacket): string {
  return `${p.header_hex}:${p.payload_hex}`;
}

export function isLedRemotePacket(p: BlePacket): boolean {
  return looksLikeLedRemote(p);
}

/** Apple Continuity / Find My style — not LED remotes (those use 0dff55aa / 0dff4c00). */
export function isAppleContinuityPacket(p: BlePacket): boolean {
  if (looksLikeLedRemote(p)) return false;
  if (p.summary?.includes("Apple Continuity")) return true;
  return (
    p.ad_structures?.some((a) => a.detail?.includes("Apple Continuity")) ??
    false
  );
}

export function isAdvertiserPdu(p: BlePacket): boolean {
  return (
    p.pdu_type_name.startsWith("ADV") || p.pdu_type_name === "SCAN_RSP"
  );
}

export function deviceKey(p: BlePacket): string {
  if (p.adv_address) return p.adv_address.toUpperCase();
  return `anon:${packetKey(p)}`;
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
    else if (address.startsWith("anon:")) label = "Unknown";
    else label = address;

    list.sort((a, b) => {
      const score = (p: BlePacket) =>
        (looksLikeLedRemote(p) ? 0 : 1) +
        (p.pdu_type_name.startsWith("ADV") ? 0 : 2);
      return score(a) - score(b);
    });

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
    lines.push(
      `## ${g.label}${g.led ? " (LED)" : ""}`,
    );
    if (!g.address.startsWith("anon:")) {
      lines.push(`\`${g.address}\` · ${g.packetCount} PDU${g.packetCount === 1 ? "" : "s"}`);
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
