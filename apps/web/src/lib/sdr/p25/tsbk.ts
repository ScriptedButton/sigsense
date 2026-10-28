/**
 * P25 Phase 1 TSBK (Trunking Signaling Block) parsers.
 * Bit layouts follow TIA-102.AABB / SDRTrunk / WaveCap conventions.
 */

import { bitsToBytes, dibitsToBits } from "./fec";

export type ChannelIdentifier = {
  identifier: number;
  bandwidthKhz: number;
  txOffsetMhz: number;
  channelSpacingKhz: number;
  baseFreqMhz: number;
};

export type VoiceGrant = {
  tgid: number;
  sourceId: number;
  channel: number;
  frequencyHz: number;
  emergency: boolean;
  encrypted: boolean;
};

export type TsbkMessage = {
  opcode: number;
  opcodeName: string;
  type: string;
  summary: string;
  tgid?: number;
  sourceId?: number;
  targetId?: number;
  channel?: number;
  frequencyHz?: number;
  emergency?: boolean;
  encrypted?: boolean;
  wacn?: number;
  systemId?: number;
  rfssId?: number;
  siteId?: number;
  identifier?: number;
  bandwidthKhz?: number;
  channelSpacingKhz?: number;
  baseFreqMhz?: number;
  txOffsetMhz?: number;
  grant2?: VoiceGrant;
  raw?: Uint8Array;
};

export const TSBK_OPCODE_NAMES: Record<number, string> = {
  0x00: "GRP_V_CH_GRANT",
  0x02: "GRP_V_CH_GRANT_UPDT",
  0x03: "GRP_V_CH_GRANT_UPDT_EXP",
  0x04: "UU_V_CH_GRANT",
  0x06: "UU_V_CH_GRANT_UPDT",
  0x28: "GRP_AFF_RSP",
  0x33: "IDEN_UP_TDMA",
  0x34: "IDEN_UP_VU",
  0x38: "SYS_SRV_BCAST",
  0x39: "SCCB",
  0x3a: "RFSS_STS_BCAST",
  0x3b: "NET_STS_BCAST",
  0x3c: "ADJ_STS_BCAST",
  0x3d: "IDEN_UP",
};

/**
 * Simplified trellis decode for TSBK: when dibits are already "data" (demo/smoke),
 * treat 48 dibits as 96 payload bits. Live RF path uses the same layout after a
 * soft Viterbi; here we accept hard dibits mapped 1:1 for interleaved blocks.
 */
export function tsbkDibitsToPayload(dibits: ArrayLike<number>): Uint8Array | null {
  // Need at least 48 dibits of coded data → 96 bits → 12 bytes (10 data + 2 CRC).
  if (dibits.length < 48) return null;
  const bits = dibitsToBits(Array.from(dibits).slice(0, 48));
  // For ideal/demo paths we store uncoded 96 bits directly.
  return bitsToBytes(bits, 96);
}

/** Parse one 12-byte TSBK (byte0 = LB|P|opcode). */
export function parseTsbkBytes(
  data: Uint8Array,
  channelIds: Map<number, ChannelIdentifier>,
): TsbkMessage | null {
  if (data.length < 10) return null;
  const opcode = data[0]! & 0x3f;
  const payload = data.subarray(1, 9);
  const name = TSBK_OPCODE_NAMES[opcode] ?? `OP_0x${opcode.toString(16)}`;

  const msg: TsbkMessage = {
    opcode,
    opcodeName: name,
    type: "UNKNOWN",
    summary: name,
    raw: data.slice(0, 12),
  };

  switch (opcode) {
    case 0x00:
      parseGrpGrant(payload, msg, channelIds);
      break;
    case 0x02:
      parseGrpGrantUpdate(payload, msg, channelIds);
      break;
    case 0x04:
      parseUuGrant(payload, msg, channelIds);
      break;
    case 0x3a:
      parseRfss(payload, msg);
      break;
    case 0x3b:
      parseNetStatus(payload, msg);
      break;
    case 0x34:
    case 0x3d:
      parseIdenUpVu(payload, msg);
      break;
    case 0x33:
      parseIdenUpTdma(payload, msg);
      break;
    default:
      msg.type = "UNKNOWN";
      msg.summary = `${name}`;
  }
  return msg;
}

function channelFreq(
  channel: number,
  channelIds: Map<number, ChannelIdentifier>,
): number {
  const ident = (channel >> 12) & 0xf;
  const num = channel & 0xfff;
  const entry = channelIds.get(ident);
  if (!entry) return 0;
  return (entry.baseFreqMhz + (num * entry.channelSpacingKhz) / 1000) * 1e6;
}

function parseGrpGrant(
  data: Uint8Array,
  msg: TsbkMessage,
  channelIds: Map<number, ChannelIdentifier>,
): void {
  const svc = data[0]!;
  const freqBand = (data[1]! >> 4) & 0xf;
  const channelNum = ((data[1]! & 0xf) << 8) | data[2]!;
  const channel = (freqBand << 12) | channelNum;
  const tgid = (data[3]! << 8) | data[4]!;
  const source = (data[5]! << 16) | (data[6]! << 8) | data[7]!;
  const frequencyHz = channelFreq(channel, channelIds);

  msg.type = "GROUP_VOICE_GRANT";
  msg.emergency = Boolean(svc & 0x80);
  msg.encrypted = Boolean(svc & 0x40);
  msg.channel = channel;
  msg.tgid = tgid;
  msg.sourceId = source;
  msg.frequencyHz = frequencyHz;
  msg.summary = `Grant TG ${tgid} → ${(frequencyHz / 1e6).toFixed(4)} MHz src ${source}`;
}

function parseGrpGrantUpdate(
  data: Uint8Array,
  msg: TsbkMessage,
  channelIds: Map<number, ChannelIdentifier>,
): void {
  const bandA = (data[0]! >> 4) & 0xf;
  const numA = ((data[0]! & 0xf) << 8) | data[1]!;
  const channelA = (bandA << 12) | numA;
  const tgidA = (data[2]! << 8) | data[3]!;
  const freqA = channelFreq(channelA, channelIds);

  msg.type = "GROUP_VOICE_GRANT_UPDATE";
  msg.tgid = tgidA;
  msg.channel = channelA;
  msg.frequencyHz = freqA;
  msg.summary = `Update TG ${tgidA} @ ${(freqA / 1e6 || 0).toFixed(4)} MHz`;

  const bandB = (data[4]! >> 4) & 0xf;
  const numB = ((data[4]! & 0xf) << 8) | data[5]!;
  const channelB = (bandB << 12) | numB;
  const tgidB = (data[6]! << 8) | data[7]!;
  if (tgidB && tgidB !== tgidA) {
    msg.grant2 = {
      tgid: tgidB,
      sourceId: 0,
      channel: channelB,
      frequencyHz: channelFreq(channelB, channelIds),
      emergency: false,
      encrypted: false,
    };
  }
}

function parseUuGrant(
  data: Uint8Array,
  msg: TsbkMessage,
  channelIds: Map<number, ChannelIdentifier>,
): void {
  const band = (data[0]! >> 4) & 0xf;
  const num = ((data[0]! & 0xf) << 8) | data[1]!;
  const channel = (band << 12) | num;
  const target = (data[2]! << 16) | (data[3]! << 8) | data[4]!;
  const source = (data[5]! << 16) | (data[6]! << 8) | data[7]!;
  msg.type = "UNIT_TO_UNIT_GRANT";
  msg.channel = channel;
  msg.targetId = target;
  msg.sourceId = source;
  msg.frequencyHz = channelFreq(channel, channelIds);
  msg.summary = `UU ${source}→${target} ch ${channel.toString(16)}`;
}

function parseRfss(data: Uint8Array, msg: TsbkMessage): void {
  // Layout (WaveCap/SDRTrunk approximate):
  // lra (8), system id (12), rfss (8), site (8), channel (16)
  const systemId = ((data[1]! & 0x0f) << 8) | data[2]!;
  const rfssId = data[3]!;
  const siteId = data[4]!;
  const channel = (data[5]! << 8) | data[6]!;
  msg.type = "RFSS_STATUS";
  msg.systemId = systemId;
  msg.rfssId = rfssId;
  msg.siteId = siteId;
  msg.channel = channel;
  msg.summary = `RFSS sys ${systemId.toString(16)} rfss ${rfssId} site ${siteId}`;
}

function parseNetStatus(data: Uint8Array, msg: TsbkMessage): void {
  const wacn = (data[0]! << 12) | (data[1]! << 4) | ((data[2]! >> 4) & 0xf);
  const systemId = ((data[2]! & 0xf) << 8) | data[3]!;
  const channel = (data[4]! << 8) | data[5]!;
  msg.type = "NETWORK_STATUS";
  msg.wacn = wacn & 0xfffff;
  msg.systemId = systemId;
  msg.channel = channel;
  msg.summary = `NET WACN ${msg.wacn.toString(16)} sys ${systemId.toString(16)}`;
}

function parseIdenUpVu(data: Uint8Array, msg: TsbkMessage): void {
  const identifier = (data[0]! >> 4) & 0xf;
  const bw = (((data[0]! & 0xf) << 5) | ((data[1]! >> 3) & 0x1f)) * 0.125;
  // tx offset signed
  const txRaw = ((data[1]! & 0x7) << 6) | ((data[2]! >> 2) & 0x3f);
  const txSign = data[2]! & 0x2 ? -1 : 1;
  const txOffsetMhz = txSign * txRaw * 0.25;
  const spacing = (((data[2]! & 0x1) << 9) | (data[3]! << 1) | ((data[4]! >> 7) & 1)) * 0.125;
  const base =
    (((data[4]! & 0x7f) << 25) |
      (data[5]! << 17) |
      (data[6]! << 9) |
      (data[7]! << 1)) *
    0.000005;

  msg.type = "IDENTIFIER_UPDATE_VU";
  msg.identifier = identifier;
  msg.bandwidthKhz = bw;
  msg.txOffsetMhz = txOffsetMhz;
  msg.channelSpacingKhz = spacing;
  msg.baseFreqMhz = base;
  msg.summary = `IDEN ${identifier}: base ${base.toFixed(4)} MHz Δ${spacing} kHz`;
}

function parseIdenUpTdma(data: Uint8Array, msg: TsbkMessage): void {
  parseIdenUpVu(data, msg);
  msg.type = "IDENTIFIER_UPDATE_TDMA";
}

/** Extract TSBK messages from a TSDU frame payload (status-stripped, post-NID). */
export function parseTsduPayload(
  payloadDibits: ArrayLike<number>,
  channelIds: Map<number, ChannelIdentifier>,
): TsbkMessage[] {
  // After NID (8 clean dibits already consumed by framer as part of payload start).
  // Payload[0..] starts with remaining NID parity + body. For demo we place TSBK
  // bytes directly after the 8 NID dibits in the payload.
  const msgs: TsbkMessage[] = [];
  // Skip first 24 dibits of BCH parity after NAC+DUID (32 NID dibits total − 8).
  let offset = 24;
  for (let block = 0; block < 3; block++) {
    const slice = Array.from(payloadDibits).slice(offset, offset + 48);
    if (slice.length < 48) break;
    const bytes = tsbkDibitsToPayload(slice);
    offset += 48;
    if (!bytes) continue;
    const parsed = parseTsbkBytes(bytes, channelIds);
    if (parsed) msgs.push(parsed);
  }
  return msgs;
}

/** Encode a minimal uncoded TSBK grant into 48 dibits for demos. */
export function encodeTsbkGrantDibits(opts: {
  opcode?: number;
  tgid: number;
  sourceId: number;
  channel: number;
  emergency?: boolean;
  encrypted?: boolean;
}): number[] {
  const opcode = opts.opcode ?? 0x00;
  let svc = 0;
  if (opts.emergency) svc |= 0x80;
  if (opts.encrypted) svc |= 0x40;
  const band = (opts.channel >> 12) & 0xf;
  const num = opts.channel & 0xfff;

  const out = new Uint8Array(12);
  out[0] = 0x80 | (opcode & 0x3f); // last block
  out[1] = svc;
  out[2] = (band << 4) | ((num >> 8) & 0xf);
  out[3] = num & 0xff;
  out[4] = (opts.tgid >> 8) & 0xff;
  out[5] = opts.tgid & 0xff;
  out[6] = (opts.sourceId >> 16) & 0xff;
  out[7] = (opts.sourceId >> 8) & 0xff;
  out[8] = opts.sourceId & 0xff;

  const bits: number[] = [];
  for (let i = 0; i < 12; i++) {
    for (let b = 7; b >= 0; b--) bits.push((out[i]! >> b) & 1);
  }
  const dibits: number[] = [];
  for (let i = 0; i < 48; i++) {
    dibits.push(((bits[i * 2]! & 1) << 1) | (bits[i * 2 + 1]! & 1));
  }
  return dibits;
}
