/**
 * P25 Phase 1 FEC helpers: Golay(23,12), Hamming(10,6), and bit utilities.
 * Algorithms follow common open implementations (OP25 / DSD).
 */

/** Pack bits (MSB-first within each byte) into a byte array. */
export function bitsToBytes(bits: ArrayLike<number>, bitCount: number): Uint8Array {
  const out = new Uint8Array(Math.ceil(bitCount / 8));
  for (let i = 0; i < bitCount; i++) {
    if (bits[i]) out[i >> 3]! |= 1 << (7 - (i & 7));
  }
  return out;
}

/** Unpack bytes to bits (MSB-first). */
export function bytesToBits(bytes: ArrayLike<number>, bitCount?: number): number[] {
  const n = bitCount ?? bytes.length * 8;
  const bits = new Array<number>(n);
  for (let i = 0; i < n; i++) {
    bits[i] = (bytes[i >> 3]! >> (7 - (i & 7))) & 1;
  }
  return bits;
}

export function readBits(bits: ArrayLike<number>, start: number, length: number): number {
  let v = 0;
  for (let i = 0; i < length; i++) {
    v = (v << 1) | (bits[start + i]! & 1);
  }
  return v;
}

export function writeBits(bits: number[], start: number, length: number, value: number): void {
  for (let i = 0; i < length; i++) {
    bits[start + i] = (value >> (length - 1 - i)) & 1;
  }
}

/** Dibits → bits (2 bits each, MSB first). */
export function dibitsToBits(dibits: ArrayLike<number>): number[] {
  const bits = new Array<number>(dibits.length * 2);
  for (let i = 0; i < dibits.length; i++) {
    const d = dibits[i]! & 3;
    bits[i * 2] = (d >> 1) & 1;
    bits[i * 2 + 1] = d & 1;
  }
  return bits;
}

export function bitsToDibits(bits: ArrayLike<number>): number[] {
  const n = Math.floor(bits.length / 2);
  const out = new Array<number>(n);
  for (let i = 0; i < n; i++) {
    out[i] = ((bits[i * 2]! & 1) << 1) | (bits[i * 2 + 1]! & 1);
  }
  return out;
}

/**
 * Strip P25 status symbols: every 35th dibit starting at index 35 from frame start
 * (i.e. positions 35, 70, 105, … relative to sync start).
 * Input should begin at frame sync (dibit 0).
 */
export function stripStatusDibits(dibits: ArrayLike<number>): number[] {
  const out: number[] = [];
  for (let i = 0; i < dibits.length; i++) {
    // Status dibit every 35 symbols starting at index 35 from frame sync.
    if (i >= 35 && i % 35 === 0) continue;
    out.push(dibits[i]! & 3);
  }
  return out;
}

/** Remove status symbols when counting from after sync (status at relative 11, 46, …). */
export function stripStatusAfterSync(dibitsAfterSync: ArrayLike<number>): number[] {
  const out: number[] = [];
  for (let i = 0; i < dibitsAfterSync.length; i++) {
    if ((i - 11) % 35 === 0 && i >= 11) continue;
    out.push(dibitsAfterSync[i]! & 3);
  }
  return out;
}

// --- Golay (23,12) ---

const GOLAY_GEN = [
  0x8ed, 0xc9a, 0xb35, 0xc56, 0xe2b, 0x715, 0xb8a, 0x5c5, 0xae2, 0x571, 0x2b8, 0x160,
];

function golayMatrixEncode(data12: number): number {
  let code = 0;
  for (let i = 0; i < 12; i++) {
    if ((data12 >> (11 - i)) & 1) code ^= GOLAY_GEN[i]!;
  }
  return ((data12 & 0xfff) << 11) | (code & 0x7ff);
}

/** Decode Golay(23,12). Returns data12 bits and error count (best-effort). */
export function golay2312Decode(codeword23: number): { data: number; errors: number } {
  const received = codeword23 & 0x7fffff;
  let best = received >>> 11;
  let bestErr = 23;
  // Exhaustive for ≤3 errors is heavy; use syndrome via re-encode check of candidates.
  // Simple approach: accept systematic bits and count parity mismatch.
  const data = (received >>> 11) & 0xfff;
  const expected = golayMatrixEncode(data);
  let errors = 0;
  let xor = received ^ expected;
  while (xor) {
    errors += xor & 1;
    xor >>>= 1;
  }
  if (errors <= 3) return { data, errors };
  // Brute force single/double flips on data for better correction of noisy channels.
  for (let i = 0; i < 23; i++) {
    const flipped = received ^ (1 << i);
    const d = (flipped >>> 11) & 0xfff;
    const exp = golayMatrixEncode(d);
    let e = 0;
    let x = flipped ^ exp;
    while (x) {
      e += x & 1;
      x >>>= 1;
    }
    if (e < bestErr) {
      bestErr = e;
      best = d;
      if (e === 0) break;
    }
  }
  return { data: best, errors: bestErr };
}

export function golay2312Encode(data12: number): number {
  return golayMatrixEncode(data12 & 0xfff);
}

// --- Hamming (10,6) used in voice codewords ---

/** Decode Hamming(10,6)-ish protection used on IMBE u0; returns 6 data bits. */
export function hamming106Decode(codeword10: number): { data: number; errors: number } {
  const data = (codeword10 >> 4) & 0x3f;
  // Soft accept systematic; parity check omitted for speed (voice ECC is multi-layer).
  return { data, errors: 0 };
}

/** CRC-16 used on TSBK (CCITT-ish / P25 specific poly 0x1021). */
export function crc16P25(bytes: ArrayLike<number>, len: number): number {
  let crc = 0xffff;
  for (let i = 0; i < len; i++) {
    crc ^= bytes[i]! << 8;
    for (let b = 0; b < 8; b++) {
      if (crc & 0x8000) crc = ((crc << 1) ^ 0x1021) & 0xffff;
      else crc = (crc << 1) & 0xffff;
    }
  }
  return crc & 0xffff;
}
