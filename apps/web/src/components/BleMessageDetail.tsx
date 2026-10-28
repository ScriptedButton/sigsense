"use client";

import { broadcastType } from "@/lib/bleGroups";
import type { BlePacket } from "@/lib/bleTypes";
import { BleReplayButton } from "./BleReplayButton";
import { BleSaveButton } from "./BleSaveButton";
import type { MessageEntry } from "./BleMessageList";

const names: Record<string, string> = {
  ADV_IND: "Device announcement",
  ADV_NONCONN_IND: "Non-connectable broadcast",
  ADV_SCAN_IND: "Scannable device announcement",
  ADV_DIRECT_IND: "Directed device announcement",
  SCAN_REQ: "Scan request",
  SCAN_RSP: "Device scan response",
  CONNECT_IND: "Connection request",
  ADV_EXT_IND: "Extended announcement",
  AUX_CONNECT_RSP: "Connection response",
  LL_DATA: "Connection data",
  LL_CONTROL: "Link control message",
  LL_EMPTY: "Connection keep-alive",
};

const bytesOf = (hex: string) =>
  hex.replace(/^0x/i, "").replace(/\s+/g, "").match(/.{1,2}/g) ?? [];

const canReplay = (packet: BlePacket) =>
  (packet.pdu_type_name.startsWith("ADV") || packet.pdu_type_name === "SCAN_RSP") &&
  packet.packet_kind !== "data" &&
  (packet.phy ?? "LE1M") === "LE1M" &&
  [0, 1, 2, 4, 6].includes(packet.pdu_type) &&
  [37, 38, 39].includes(packet.channel ?? -1) &&
  packet.length <= 37;

const fieldRows = (fields: Record<string, unknown>) =>
  Object.entries(fields)
    .filter(([, value]) => value != null && typeof value !== "object")
    .slice(0, 12)
    .map(([key, value]) => ({ key: key.replaceAll("_", " "), value: String(value) }));

export function BleMessageDetail({
  entry,
  defaultChannel,
  demo = false,
  onSaved,
}: {
  entry: MessageEntry;
  defaultChannel?: number | null;
  demo?: boolean;
  onSaved?: () => Promise<void>;
}) {
  const packet = entry.packet;
  const title = entry.typeLabel || broadcastType(packet);
  const ads = packet.ad_structures ?? [];
  const fields = packet.protocol_fields ? fieldRows(packet.protocol_fields) : [];
  const channels = entry.channels.length ? entry.channels.join(" / ") : String(packet.channel ?? "?");
  return (
    <article className="min-w-0 px-4 py-4">
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="truncate text-sm font-medium">{title}</h4>
          <p className="mt-1 text-xs text-[var(--muted)]">
            {names[packet.pdu_type_name] ?? packet.pdu_type_name}
          </p>
        </div>
        <span className={`shrink-0 text-[10px] ${packet.crc_ok ? "text-emerald-300" : "text-amber-200"}`}>
          {packet.crc_ok ? "Checksum verified" : "Checksum failed"}
        </span>
      </header>
      <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Fact label="Channel" value={channels} />
        <Fact label="PHY" value={packet.phy ?? "LE1M"} />
        <Fact label="Payload" value={`${packet.length} bytes`} />
        <Fact
          label="Heard"
          value={entry.count !== undefined ? `${entry.count} times` : packet.timestamp_seconds != null ? `+${(packet.timestamp_seconds * 1000).toFixed(1)} ms` : "Once"}
        />
      </dl>
      {ads.length > 0 && (
        <dl className="mt-4 space-y-2 border-t border-[var(--line)] pt-3 text-xs">
          {ads.map((ad, index) => (
            <div key={`${ad.type}-${index}`} className="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-3">
              <dt className="text-[var(--muted)]">
                {ad.type === 8 || ad.type === 9 ? "Device name" : ad.type === 255 ? "Manufacturer" : ad.label}
              </dt>
              <dd className="break-words">{ad.detail}</dd>
            </div>
          ))}
        </dl>
      )}
      {fields.length > 0 && (
        <dl className="mt-4 space-y-2 text-xs">
          {fields.map((field) => (
            <div key={field.key} className="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-3">
              <dt className="text-[var(--muted)]">{field.key}</dt>
              <dd className="break-all font-mono text-[11px]">{field.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {packet.packet_kind === "data" && (
        <p className="mt-3 text-xs text-[var(--muted)]">
          Encrypted link data stays unreadable without this connection’s session key.
        </p>
      )}
      {packet.warnings?.map((warning) => (
        <p key={warning} className="mt-2 text-xs text-amber-200">{warning}</p>
      ))}
      <HexBlock label="Payload" hex={packet.payload_hex} />
      <details className="mt-4 text-[11px] text-[var(--muted)]">
        <summary className="cursor-pointer">Access address, header, and CRC</summary>
        <div className="mt-3 space-y-3">
          <p className="font-mono">Access address {packet.access_address}</p>
          {packet.summary && <p>{packet.summary}</p>}
          <HexBlock label="Header" hex={packet.header_hex} />
          <HexBlock label="CRC" hex={packet.crc_hex} />
        </div>
      </details>
      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-[var(--line)] pt-3">
        <BleSaveButton
          compact
          packets={[packet]}
          name={title}
          kind="broadcast"
          demo={demo}
          onSaved={onSaved}
        />
        {canReplay(packet) && (
          <details className="text-[11px] text-amber-200">
            <summary className="cursor-pointer">Transmit this packet</summary>
            <p className="mt-2 text-[var(--muted)]">
              Sends this advertisement from the HackRF. Playback of a saved IQ window stays in the capture library.
            </p>
            <BleReplayButton packet={packet} defaultChannel={packet.channel ?? defaultChannel} />
          </details>
        )}
      </div>
    </article>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wider text-[var(--muted)]">{label}</dt>
      <dd className="mt-1 font-mono text-xs">{value}</dd>
    </div>
  );
}

function HexBlock({ label, hex }: { label: string; hex: string }) {
  const bytes = bytesOf(hex);
  if (!bytes.length) return null;
  return (
    <div className="mt-4">
      <div className="text-[10px] uppercase tracking-wider text-[var(--muted)]">{label}</div>
      <p className="mt-1 font-mono text-[11px] leading-6 tracking-wide">
        {bytes.map((byte, index) => (
          <span key={`${byte}-${index}`}>
            {index > 0 && index % 8 === 0 ? "  " : index > 0 ? " " : ""}
            {byte.toUpperCase()}
          </span>
        ))}
      </p>
    </div>
  );
}
