/**
 * P25 Phase 1 frame sync + NID extraction.
 */

import { dibitsToBits, readBits, stripStatusAfterSync } from "./fec";
import { duidLabel, type P25Duid } from "./types";

/** Frame sync as dibits — hex 0x5575F5FF77FF (SDRTrunk / WaveCap). */
export const FRAME_SYNC_DIBITS = [
  1, 1, 1, 1, 1, 3, 1, 1, 3, 3, 1, 1, 3, 3, 3, 3, 1, 3, 1, 3, 3, 3, 3, 3,
] as const;

export const FRAME_SYNC_LEN = FRAME_SYNC_DIBITS.length;

/** Inverted polarity sync (dibit XOR 2). */
export const FRAME_SYNC_DIBITS_INV = FRAME_SYNC_DIBITS.map((d) => d ^ 2);

export type NidInfo = {
  nac: number;
  duid: number;
  duidLabel: P25Duid;
  inverted: boolean;
  syncScore: number;
};

export type FramedUnit = {
  nac: number;
  duid: number;
  duidLabel: P25Duid;
  inverted: boolean;
  /** Dibits after sync, status symbols removed. */
  payload: number[];
  /** Absolute start index in the input stream (sync start). */
  syncIndex: number;
};

const MAX_ERRORS = 4;

function syncErrors(
  window: ArrayLike<number>,
  pattern: ArrayLike<number>,
): number {
  let err = 0;
  for (let i = 0; i < pattern.length; i++) {
    if ((window[i]! & 3) !== (pattern[i]! & 3)) err++;
  }
  return err;
}

/**
 * Find best frame sync in a dibit buffer.
 * Returns start index or -1.
 */
export function findFrameSync(
  dibits: ArrayLike<number>,
  start = 0,
): { index: number; inverted: boolean; errors: number } | null {
  const n = dibits.length;
  let best: { index: number; inverted: boolean; errors: number } | null = null;

  for (let i = start; i <= n - FRAME_SYNC_LEN - 8; i++) {
    const eNorm = syncErrors(
      Array.from({ length: FRAME_SYNC_LEN }, (_, k) => dibits[i + k]!),
      FRAME_SYNC_DIBITS,
    );
    const eInv = syncErrors(
      Array.from({ length: FRAME_SYNC_LEN }, (_, k) => dibits[i + k]!),
      FRAME_SYNC_DIBITS_INV,
    );
    const inverted = eInv < eNorm;
    const errors = inverted ? eInv : eNorm;
    if (errors > MAX_ERRORS) continue;
    if (!best || errors < best.errors) {
      best = { index: i, inverted, errors };
      if (errors === 0) break;
    }
  }
  return best;
}

/** Extract NAC + DUID from 8 dibits immediately after sync (raw, no BCH). */
export function extractNidRaw(
  dibitsAfterSync: ArrayLike<number>,
  inverted: boolean,
): NidInfo | null {
  if (dibitsAfterSync.length < 12) return null;
  // Status at relative index 11 — take first 11 dibits then skip status for remaining
  const cleaned = stripStatusAfterSync(dibitsAfterSync);
  if (cleaned.length < 8) return null;

  const nidDibits = cleaned.slice(0, 8).map((d) => (inverted ? d ^ 2 : d));
  const bits = dibitsToBits(nidDibits);
  const nac = readBits(bits, 0, 12);
  const duid = readBits(bits, 12, 4);
  return {
    nac,
    duid,
    duidLabel: duidLabel(duid),
    inverted,
    syncScore: 1,
  };
}

/** Approximate body length in dibits (including sync) by DUID. */
export function frameBodyDibits(duid: number): number {
  switch (duid & 0xf) {
    case 0x0: // HDU
      return 36 + 648 / 2; // rough
    case 0x3: // TDU
      return 36 + 28;
    case 0x5: // LDU1
    case 0xa: // LDU2
      return 864; // 1728 bits ≈ 864 dibits total common figure
    case 0x7: // TSDU / TSBK
      return 36 + 196;
    case 0xf: // TDULC
      return 36 + 60;
    default:
      return 200;
  }
}

/**
 * Streaming framer: feed dibits, emit framed units when sync+NID+body available.
 */
export class P25Framer {
  private buf: number[] = [];
  private maxBuf = 4_000;

  reset(): void {
    this.buf = [];
  }

  push(dibits: ArrayLike<number>): FramedUnit[] {
    for (let i = 0; i < dibits.length; i++) {
      this.buf.push(dibits[i]! & 3);
    }
    if (this.buf.length > this.maxBuf) {
      this.buf = this.buf.slice(-this.maxBuf);
    }

    const frames: FramedUnit[] = [];
    let guard = 0;
    while (guard++ < 32) {
      const sync = findFrameSync(this.buf, 0);
      if (!sync) break;

      const after = this.buf.slice(sync.index + FRAME_SYNC_LEN);
      const nid = extractNidRaw(after, sync.inverted);
      if (!nid) {
        this.buf = this.buf.slice(sync.index + 1);
        continue;
      }

      const bodyLen = frameBodyDibits(nid.duid);
      const need = FRAME_SYNC_LEN + Math.max(40, bodyLen - FRAME_SYNC_LEN);
      if (this.buf.length < sync.index + need) break;

      const frameSlice = this.buf.slice(sync.index, sync.index + need);
      const payloadRaw = frameSlice.slice(FRAME_SYNC_LEN);
      const polarity = sync.inverted
        ? payloadRaw.map((d) => d ^ 2)
        : payloadRaw;
      const payload = stripStatusAfterSync(polarity);

      frames.push({
        nac: nid.nac,
        duid: nid.duid,
        duidLabel: nid.duidLabel,
        inverted: sync.inverted,
        payload,
        syncIndex: sync.index,
      });

      // Advance past this sync
      this.buf = this.buf.slice(sync.index + FRAME_SYNC_LEN + 8);
    }
    return frames;
  }
}

/** Build a sync + raw NID (no BCH) dibit sequence for demos / smoke tests. */
export function encodeSyncAndNid(nac: number, duid: number): number[] {
  const bits = new Array<number>(16).fill(0);
  for (let i = 0; i < 12; i++) bits[i] = (nac >> (11 - i)) & 1;
  for (let i = 0; i < 4; i++) bits[12 + i] = (duid >> (3 - i)) & 1;
  const nidDibits: number[] = [];
  for (let i = 0; i < 8; i++) {
    nidDibits.push(((bits[i * 2]! & 1) << 1) | (bits[i * 2 + 1]! & 1));
  }
  // Insert status placeholder at relative index 11 (after 11 NID dibits) —
  // for short NID-only tests we just append sync + 8 NID dibits.
  return [...FRAME_SYNC_DIBITS, ...nidDibits];
}
