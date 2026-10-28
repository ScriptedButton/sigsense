/**
 * Digital voice event contract v1: mirrors apps/api/sigsense_api/dvoice/events.py.
 * Owner: architect (WP-H consumes). Field names are checked against the Python
 * dataclasses by apps/api/tests/test_dvoice_contracts.py, so change both together
 * and bump CONTRACT_VERSION.
 *
 * Policy: clear voice only. `encryption` reports what the air interface says;
 * encrypted calls carry no audio and the UI must say so.
 */

export const DVOICE_CONTRACT_VERSION = 1;

export type DvProtocol =
  | "p25p1"
  | "p25p2"
  | "dmr"
  | "nxdn48"
  | "nxdn96"
  | "dstar"
  | "ysf"
  | "m17"
  | "dpmr"
  | "x2tdma"
  | "provoice"
  | "edacs"
  | "unknown";

export type DvCallKind = "group" | "private" | "all" | "data" | "telephone" | "unknown";
export type DvEncryptionState = "clear" | "encrypted" | "unknown";
export type DvSeverity = "info" | "warning" | "error";
export type DvLinkState = "searching" | "synced" | "voice" | "lost";

export type DvEncryptionInfo = {
  state: DvEncryptionState;
  algorithm_id: number | null;
  algorithm_name: string | null;
  key_id: number | null;
  mi_present: boolean;
  audio_decoded: boolean;
};

export type DvSystemInfo = {
  protocol: DvProtocol;
  wacn: number | null;
  system_id: number | null;
  nac: number | null;
  rfss_id: number | null;
  site_id: number | null;
  color_code: number | null;
  ran: number | null;
  network_id: number | null;
  name: string | null;
  control_channel_hz: number | null;
  extra: Record<string, unknown>;
};

export type DvTalkgroup = {
  id: number;
  protocol: DvProtocol;
  label: string | null;
  last_heard: number | null;
  call_count: number;
  encrypted_calls: number;
};

export type DvCall = {
  call_id: string;
  protocol: DvProtocol;
  slot: number | null;
  kind: DvCallKind;
  target_id: number | null;
  source_id: number | null;
  frequency_hz: number | null;
  channel: string | null;
  started_at: number;
  updated_at: number;
  ended_at: number | null;
  encryption: DvEncryptionInfo;
  emergency: boolean;
  priority: number | null;
  voice_frames: number;
  voice_frames_bad: number;
  ber: number | null;
  end_reason: string | null;
  meta: Record<string, unknown>;
};

export type DvLinkStats = {
  channel: string;
  protocol: DvProtocol | null;
  state: DvLinkState;
  sync_ratio: number;
  ber: number | null;
  fec_corrected: number;
  fec_failed: number;
  frames_ok: number;
  frames_bad: number;
  symbol_rate: number;
  snr_db: number | null;
  freq_error_hz: number | null;
  level: number | null;
  polarity_inverted: boolean;
};

export type DvSyncEvent = {
  protocol: DvProtocol;
  pattern: string;
  errors: number;
  inverted: boolean;
  symbol_index: number;
};

export type DvTrunkEvent = {
  kind: string;
  frequency_hz: number | null;
  talkgroup: number | null;
  source_id: number | null;
  slot: number | null;
  encrypted: boolean;
  detail: Record<string, unknown>;
};

export type DvError = {
  code: string;
  severity: DvSeverity;
  message: string;
  detail: Record<string, unknown>;
};

export type DvEngineStatus = {
  running: boolean;
  channels: Record<string, Record<string, unknown>>;
  vocoders: Record<
    string,
    { available: boolean; reason: string; licence_note: string; backend: string }
  >;
  contract_version: number;
};

type Envelope<T extends string, D> = {
  v: number;
  type: T;
  t: number; // epoch milliseconds
  channel: string;
  data: D;
};

export type DvEvent =
  | Envelope<"status", DvEngineStatus>
  | Envelope<"link_stats", DvLinkStats>
  | Envelope<"sync", DvSyncEvent>
  | Envelope<"system_info", DvSystemInfo>
  | Envelope<"call_start", DvCall>
  | Envelope<"call_update", DvCall>
  | Envelope<"call_end", DvCall>
  | Envelope<"talkgroup", DvTalkgroup>
  | Envelope<"trunk", DvTrunkEvent>
  | Envelope<"error", DvError>;

/** Binary voice frame header (16 bytes) followed by int16 LE PCM. See events.py. */
export const DV_AUDIO_MAGIC = 0xa7;
export const DV_AUDIO_HEADER_BYTES = 16;

export type DvAudioFrame = {
  slot: number | null;
  endOfCall: boolean;
  hadErrors: boolean;
  callNum: number;
  seq: number;
  sampleRate: number;
  pcm: Int16Array;
};

export function parseDvAudioFrame(data: ArrayBuffer): DvAudioFrame {
  const view = new DataView(data);
  if (data.byteLength < DV_AUDIO_HEADER_BYTES || view.getUint8(0) !== DV_AUDIO_MAGIC) {
    throw new Error("not a digital voice audio frame");
  }
  const slot = view.getUint8(2);
  const flags = view.getUint8(3);
  const samples = view.getUint16(14, true);
  if (data.byteLength !== DV_AUDIO_HEADER_BYTES + 2 * samples) {
    throw new Error("digital voice audio frame length mismatch");
  }
  return {
    slot: slot === 0xff ? null : slot,
    endOfCall: (flags & 0x01) !== 0,
    hadErrors: (flags & 0x02) !== 0,
    callNum: view.getUint32(4, true),
    seq: view.getUint32(8, true),
    sampleRate: view.getUint16(12, true),
    pcm: new Int16Array(data.slice(DV_AUDIO_HEADER_BYTES)),
  };
}

/** Upload kinds for WS /dvoice/stream?source=upload (first byte of each binary message). */
export const DV_INPUT_IQ16_48K = 0x01;
export const DV_INPUT_DISC16_48K = 0x02;
