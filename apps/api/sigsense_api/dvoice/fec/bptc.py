"""Block product turbo codes for DMR (CONTRACT v1). Owner: WP-A (WP-D consumes). Original code.

BPTC(196,96), ETSI TS 102 361-1 clause B.1.1: a 13 x 15 matrix. The 96 information
bits (I(95) first) are preceded by three reserved zero bits R(2..0) and laid out
row-wise in the 9 x 11 data region; each of the 9 data rows gets Hamming(15,11,3)
parity (H_Rx), then every one of the 15 columns gets Hamming(13,9,3) parity (H_Cx) in
rows 10-13. The 195 matrix bits, preceded by one reserved bit R(3) = 0, are
interleaved by index -> index * 181 mod 196.

Embedded signalling BPTC (B.2.1): an 8 x 16 matrix; rows 1-7 carry 11 bits plus
Hamming(16,11,4) parity (72 LC bits + a 5-bit checksum fill the 7 x 11 information
region), row 8 is an even column-parity row. The matrix is transmitted by columns,
top to bottom and left to right, 32 bits per burst.
"""

from __future__ import annotations

import numpy as np

from sigsense_api.dvoice.fec import hamming
from sigsense_api.dvoice.fec.types import FecResult

_ROWS, _COLS = 13, 15
_DATA_ROWS, _DATA_COLS = 9, 11
_INTERLEAVE_INDEX = (np.arange(196) * 181) % 196  # matrix bit n (R(3) = 0) -> transmit slot


def _word(bits) -> int:
    value = 0
    for b in bits:
        value = (value << 1) | int(b)
    return value


def _bits(value: int, width: int) -> list[int]:
    return [(value >> (width - 1 - i)) & 1 for i in range(width)]


def bptc_196_96_encode(bits96: np.ndarray) -> np.ndarray:
    """96 info bits (I(95) first) -> 196 bits in over-the-air order (for synthetic test frames)."""
    info = np.asarray(bits96, dtype=np.uint8).ravel()
    if info.size != 96:
        raise ValueError("need 96 information bits")
    stream = np.concatenate([np.zeros(3, np.uint8), info])  # R(2),R(1),R(0), I(95)..I(0)
    matrix = np.zeros((_ROWS, _COLS), dtype=np.uint8)
    for r in range(_DATA_ROWS):
        data = stream[r * _DATA_COLS : (r + 1) * _DATA_COLS]
        matrix[r, :] = _bits(hamming.hamming_15_11_3_encode(_word(data)), _COLS)
    for c in range(_COLS):
        matrix[:, c] = _bits(hamming.hamming_13_9_3_encode(_word(matrix[:_DATA_ROWS, c])), _ROWS)
    flat = np.concatenate([[0], matrix.ravel()]).astype(np.uint8)  # R(3) at index 0
    out = np.empty(196, dtype=np.uint8)
    out[_INTERLEAVE_INDEX] = flat
    return out


def bptc_196_96_decode(bits196: np.ndarray) -> FecResult:
    """196 received bits in over-the-air order (not yet de-interleaved) -> 96 info bits.

    Iterative row/column Hamming decoding (up to 6 passes). ok is True when every row
    and column is a valid codeword at the end. That is NOT an integrity check: random
    input converges to some codeword about 10% of the time, so callers must verify the
    CRC (CSBK/header CRC-CCITT with its data-type mask)."""
    rx = np.asarray(bits196, dtype=np.uint8).ravel()
    if rx.size != 196:
        raise ValueError("need 196 bits")
    flat = rx[_INTERLEAVE_INDEX]
    matrix = flat[1:].reshape(_ROWS, _COLS).copy()
    corrected = 0
    clean = False
    for _ in range(6):
        changed = False
        for c in range(_COLS):
            res = hamming.hamming_13_9_3_decode(_word(matrix[:, c]))
            if res is not None and res[1]:
                matrix[:, c] = _bits(hamming.hamming_13_9_3_encode(res[0]), _ROWS)
                corrected += res[1]
                changed = True
        for r in range(_ROWS):
            res = hamming.hamming_15_11_3_decode(_word(matrix[r, :]))
            if res is not None and res[1]:
                matrix[r, :] = _bits(hamming.hamming_15_11_3_encode(res[0]), _COLS)
                corrected += res[1]
                changed = True
        if not changed:
            clean = True
            break
    ok = clean and _all_valid(matrix)
    stream = matrix[:_DATA_ROWS, :_DATA_COLS].ravel()  # R(2),R(1),R(0), I(95)..I(0)
    return FecResult(stream[3:].astype(np.uint8), corrected, bool(ok))


def _all_valid(matrix: np.ndarray) -> bool:
    for c in range(_COLS):
        res = hamming.hamming_13_9_3_decode(_word(matrix[:, c]))
        if res is None or res[1]:
            return False
    for r in range(_ROWS):
        res = hamming.hamming_15_11_3_decode(_word(matrix[r, :]))
        if res is None or res[1]:
            return False
    return True


# ---- embedded LC ----------------------------------------------------------

def _lc_checksum(lc72: np.ndarray) -> int:
    octets = [_word(lc72[i : i + 8]) for i in range(0, 72, 8)]
    return sum(octets) % 31


def _lc_to_info_rows(lc72: np.ndarray, checksum: int) -> np.ndarray:
    """7 x 11 information matrix: rows 0-1 are 11 LC bits, rows 2-6 are 10 LC bits + CS(4..0)."""
    rows = np.zeros((7, 11), dtype=np.uint8)
    pos = 0
    for r in range(7):
        n = 11 if r < 2 else 10
        rows[r, :n] = lc72[pos : pos + n]
        pos += n
        if r >= 2:
            rows[r, 10] = (checksum >> (6 - r)) & 1  # r=2 -> CS(4) ... r=6 -> CS(0)
    return rows


def embedded_lc_encode(bits72: np.ndarray) -> np.ndarray:
    """72 LC bits (LC(71) first) -> 128 bits in transmit order (four 32-bit burst fields)."""
    lc = np.asarray(bits72, dtype=np.uint8).ravel()
    if lc.size != 72:
        raise ValueError("need 72 LC bits")
    info = _lc_to_info_rows(lc, _lc_checksum(lc))
    matrix = np.zeros((8, 16), dtype=np.uint8)
    for r in range(7):
        matrix[r, :] = _bits(hamming.hamming_16_11_4_encode(_word(info[r])), 16)
    matrix[7, :] = matrix[:7, :].sum(axis=0) & 1  # even parity per column
    return matrix.T.ravel().copy()  # columns top-to-bottom, left-to-right


def embedded_lc_decode(bits128: np.ndarray) -> FecResult:
    """DMR embedded LC: 128 bits collected over bursts B..E -> 72 LC bits, checksum verified.

    Each Hamming(16,11,4) row corrects one error. One row with two errors is
    rebuilt from the column-parity row. ok requires clean rows, even column
    parity and a matching 5-bit checksum."""
    rx = np.asarray(bits128, dtype=np.uint8).ravel()
    if rx.size != 128:
        raise ValueError("need 128 bits")
    matrix = rx.reshape(16, 8).T.copy()  # transmitted by columns
    corrected = 0
    failed = []
    for r in range(7):
        res = hamming.hamming_16_11_4_decode(_word(matrix[r]))
        if res is None:
            failed.append(r)
        else:
            if res[1]:
                matrix[r] = _bits(hamming.hamming_16_11_4_encode(res[0]), 16)
                corrected += res[1]
    ok = True
    rows_corrected = corrected
    if len(failed) == 1:
        # Column parity gives the damaged row: XOR of all the other rows (incl. parity row).
        r = failed[0]
        others = np.bitwise_xor.reduce(np.delete(matrix, r, axis=0), axis=0)
        candidate = _word(others)
        if hamming.hamming_16_11_4_decode(candidate) is not None and not hamming.hamming_16_11_4_decode(candidate)[1]:
            corrected += int((matrix[r] != others).sum())
            matrix[r] = others
        else:
            ok = False
    elif failed:
        ok = False
    if ok:
        # Column parity must now be even. One bad column is a lone error in the (unprotected)
        # parity row, which leaves the data intact; more than one means a row was miscorrected.
        mismatched = int(((matrix.sum(axis=0)) & 1).sum())
        if mismatched == 1:
            corrected += 1
        elif mismatched > 1:
            ok = False
    del rows_corrected
    info = np.zeros((7, 11), dtype=np.uint8)
    for r in range(7):
        info[r] = matrix[r, :11]
    lc = np.concatenate([info[0], info[1]] + [info[r, :10] for r in range(2, 7)]).astype(np.uint8)
    checksum = sum(int(info[r, 10]) << (6 - r) for r in range(2, 7))
    if checksum != _lc_checksum(lc):
        ok = False
    return FecResult(lc, corrected, ok)
