export type BlePacket = {
  access_address: string;
  channel: number | null;
  pdu_type: number;
  pdu_type_name: string;
  length: number;
  header_hex: string;
  payload_hex: string;
  crc_hex: string;
  crc_ok: boolean;
  rssi_dbfs?: number | null;
  offset_bits: number;
  dewhitened: boolean;
  adv_address?: string | null;
  adv_address_type?: "random" | "public";
  ad_structures?: {
    type: number;
    label: string;
    data_hex: string;
    detail: string;
  }[];
  summary?: string | null;
  led_remote?: boolean;
  capture_id?: string | null;
  phy?: string;
  packet_kind?: "advertising" | "data";
  sample_offset?: number | null;
  timestamp_seconds?: number | null;
  cfo_hz?: number | null;
  aa_errors?: number;
  protocol_fields?: Record<string, unknown>;
  warnings?: string[];
};

export type BleDecodeResult = {
  phy: string;
  sample_rate_hz: number;
  center_freq_hz?: number | null;
  channel: number | null;
  bit_count: number;
  packet_count: number;
  packets: BlePacket[];
  warnings?: string[];
  scope?: string;
  truncated?: boolean;
  connections?: Record<string, unknown>[];
};

export type SavedBleMessages = {
  id: string;
  name: string;
  kind: "broadcast" | "device";
  device_keys?: string[];
  created_at: number;
  message_count: number;
  broadcast_count: number;
  replayable_count: number;
  broadcast_key: string | null;
  demo: boolean;
  packets?: BlePacket[];
  duplicate?: boolean;
};
