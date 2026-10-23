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
  ad_structures?: {
    type: number;
    label: string;
    data_hex: string;
    detail: string;
  }[];
  summary?: string | null;
  led_remote?: boolean;
};

export type BleDecodeResult = {
  phy: string;
  sample_rate_hz: number;
  center_freq_hz?: number | null;
  channel: number | null;
  bit_count: number;
  packet_count: number;
  packets: BlePacket[];
};
