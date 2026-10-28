/**
 * P25 Phase 1 LDU1 / LDU2 voice frame extraction.
 *
 * Each LDU carries 9 IMBE voice frames (144 bits each before deinterleave/ECC).
 * Link Control (LDU1) and Encryption Sync (LDU2) are pulled from known bit maps.
 */

import { dibitsToBits, readBits } from "./fec";

export type LinkControl = {
  format: number;
  tgid: number | null;
  sourceId: number | null;
  targetId: number | null;
  emergency: boolean;
  encrypted: boolean;
};

export type EncryptionSync = {
  algid: number;
  keyId: number;
  mi: number[];
  encrypted: boolean;
};

export type LduVoice = {
  kind: "LDU1" | "LDU2";
  /** Nine 88-bit IMBE parameter frames (as 0/1 arrays). */
  imbeFrames: number[][];
  linkControl: LinkControl | null;
  encryption: EncryptionSync | null;
};

/** Clear ALGID values (unencrypted). */
export const ALGID_CLEAR = 0x80;

/**
 * Extract 9×144-bit voice codewords from LDU body bits (status already stripped).
 * Layout follows OP25 voice_codeword_bits relative offsets — we use a simplified
 * sequential packing for demo/smoke and a best-effort scrape for live RF.
 */
export function extractImbeFromLduBits(bits: ArrayLike<number>): number[][] {
  const frames: number[][] = [];
  // After sync+NID (~48+64 bits), voice data starts. With status stripped,
  // approximate: skip ~64 NID bits already removed from payload start in framer.
  // Payload begins at post-sync cleaned dibits → bits.
  // We take 9 chunks of 144 bits starting at offset 0 for sequential demo layout,
  // or interleaved live layout at stride 144 from bit 114 equivalent.
  const start = 0;
  for (let f = 0; f < 9; f++) {
    const frame: number[] = [];
    const base = start + f * 144;
    for (let i = 0; i < 144; i++) {
      frame.push(bits[base + i] ?? 0);
    }
    // Collapse 144 protected bits → 88 data bits (take first 88 for demo;
    // live path should Golay/Hamming decode — acceptable for clear synthesis attempts).
    frames.push(frame.slice(0, 88));
  }
  return frames;
}

export function parseLinkControl(bits: ArrayLike<number>): LinkControl | null {
  if (bits.length < 72) return null;
  const format = readBits(bits, 0, 8);
  // Group voice LC (0x00): TGID 16 + source 24
  if (format === 0x00 || format === 0x40) {
    const tgid = readBits(bits, 8, 16);
    const sourceId = readBits(bits, 24, 24);
    return {
      format,
      tgid,
      sourceId,
      targetId: null,
      emergency: false,
      encrypted: false,
    };
  }
  if (format === 0x03) {
    const targetId = readBits(bits, 8, 24);
    const sourceId = readBits(bits, 32, 24);
    return {
      format,
      tgid: null,
      sourceId,
      targetId,
      emergency: false,
      encrypted: false,
    };
  }
  return {
    format,
    tgid: null,
    sourceId: null,
    targetId: null,
    emergency: false,
    encrypted: false,
  };
}

export function parseEncryptionSync(bits: ArrayLike<number>): EncryptionSync | null {
  if (bits.length < 96) return null;
  const algid = readBits(bits, 0, 8);
  const keyId = readBits(bits, 8, 16);
  const mi: number[] = [];
  for (let i = 0; i < 9; i++) mi.push(readBits(bits, 24 + i * 8, 8));
  return {
    algid,
    keyId,
    mi,
    encrypted: algid !== ALGID_CLEAR && algid !== 0x00,
  };
}

export function parseLdu(
  kind: "LDU1" | "LDU2",
  payloadDibits: ArrayLike<number>,
): LduVoice {
  const bits = dibitsToBits(payloadDibits);
  const imbeFrames = extractImbeFromLduBits(bits);
  let linkControl: LinkControl | null = null;
  let encryption: EncryptionSync | null = null;

  if (kind === "LDU1") {
    // LC soft-extracted from mid-frame region (simplified offset).
    const lcBits = Array.from({ length: 72 }, (_, i) => bits[410 + i] ?? 0);
    linkControl = parseLinkControl(lcBits);
  } else {
    const esBits = Array.from({ length: 96 }, (_, i) => bits[410 + i] ?? 0);
    encryption = parseEncryptionSync(esBits);
  }

  return { kind, imbeFrames, linkControl, encryption };
}

/** Build sequential LDU dibits with 9×88 IMBE bit frames for demos. */
export function encodeLduVoiceDibits(imbeFrames: number[][]): number[] {
  const bits: number[] = [];
  for (let f = 0; f < 9; f++) {
    const frame = imbeFrames[f] ?? new Array(88).fill(0);
    for (let i = 0; i < 144; i++) {
      bits.push(i < 88 ? (frame[i] ?? 0) : 0);
    }
  }
  const dibits: number[] = [];
  for (let i = 0; i < Math.floor(bits.length / 2); i++) {
    dibits.push(((bits[i * 2]! & 1) << 1) | (bits[i * 2 + 1]! & 1));
  }
  return dibits;
}

/** Silence / idle IMBE parameter frame (88 zero bits) — synthesizes quiet audio. */
export function silentImbeFrame(): number[] {
  return new Array(88).fill(0);
}
