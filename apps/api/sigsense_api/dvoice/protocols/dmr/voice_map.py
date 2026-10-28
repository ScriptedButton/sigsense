"""DMR AMBE+2 voice frame mapping: 72 air bits -> AMBE+2 3600x2450 channel frame -> 49 parameter bits.

ETSI TS 102 361-1 defines only the "vocoder socket" (three 72-bit vocoder frames per voice burst). How
those 72 bits are arranged and protected is the vocoder vendor's channel coding. This module is that
step, written for SigSense (original code), from these facts:

PROVENANCE (record kept in docs/digital-voice-codec-setup.md as well)
  1. Frame structure and channel decode (rows of 24/23/11/14 bits, Golay(23,12) on rows 0 and 1, the
     scrambler seeded from row 0, 25 unprotected bits, 49 parameter bits out): the public interface of
     the mbelib decoder, https://github.com/szechyjs/mbelib at commit
     9a04ed5c78176a9965f3d43f7aa1b1f5330e771f (ambe3600x2450.c, ecc.c, ecc_const.h), licence: ISC
     ("Permission to use, copy, modify, and/or distribute this software for any purpose with or without
     fee is hereby granted ..."). Numeric constants only: 173, 13849, 65536 (scrambler recurrence).
  2. Air-bit order -> (row, column) permutation: numeric table `dmr_const.h` (arrays rW, rX, rY, rZ, 36
     entries each) in the original DSD project, https://github.com/szechyjs/dsd at commit
     6605925162f412ef6d927aa1b2eab8049973f6a3 (file sha256
     133427d0bd2eb0a6000bd441d6f1d0e8168c5a6ce24f768d031366b948b60bc9), whose file header carries the ISC
     licence ("Copyright (C) 2010 DSD Author ... Permission to use, copy, modify, and/or distribute this
     software for any purpose with or without fee is hereby granted, provided that the above copyright
     notice and this permission notice appear in all copies."). Only the numeric indices are used here
     (a fact needed for interoperability); no code or structure was copied. (GitHub reports this repo's
     licence as "NOASSERTION" because it has no LICENSE file, but the source headers state ISC; the
     `COPYRIGHT` file is the project's notice. Not legal advice.)
  3. The 25-bit/49-bit split and Golay(24,12)+Golay(23,12) structure agree with the half-rate vocoder
     description in the public DVSI patent US 8,359,197 B2 ("This half-rate implementation uses a 20 ms
     frame containing 72 bits, where the bits are divided into 23 FEC bits and 49 voice or tone bits";
     "a pseudo-random scrambling sequence is generated from a modulation key ... XORed with the 23
     output bits of the [23,12] encoder"). The patent has no interleave table.
  4. NXDN voice shares the same permutation and adds a 144-bit descrambling mask (NXDN_VOICE_MASK,
     `nxdnpr` in the same DSD repo's nxdn_const.h). Provided here as data for the NXDN decoder; it is NOT
     verified against the NXDN specification.

WHAT THIS PROVES: the tests show the pieces are self-consistent (the permutation is a bijection, encode and
decode invert each other, Golay/scrambler behave as the sources describe). Correctness against real over-the-air
DMR traffic can only be shown with a capture: see apps/api/scripts/verify_dmr_voice_map.py.

Bit conventions
  * `air72`: 72 hard bits (uint8 0/1) in the order they arrive in the voice burst, first transmitted first;
    each pair is one dibit, the first bit being the high bit ("bit 1") of the dibit.
  * frame: uint8[4, 24]. Row 0 holds 24 bits (col 0 = extended-Golay parity bit, cols 1..23 = Golay(23,12)
    codeword, data in cols 23..12); row 1 holds 23 bits (cols 0..22, data in cols 22..11, scrambled); row 2
    holds 11 bits (cols 10..0); row 3 holds 14 bits (cols 13..0). Unused cells are 0.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from sigsense_api.dvoice.fec import golay

# (row, column) of the HIGH bit of dibit i (rW[i], rX[i]) and of the LOW bit (rY[i], rZ[i]).
_W = (0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 2, 0, 2, 0, 2, 0, 2, 0, 2, 0, 2, 0, 2)
_X = (23, 10, 22, 9, 21, 8, 20, 7, 19, 6, 18, 5, 17, 4, 16, 3, 15, 2, 14, 1, 13, 0, 12, 10, 11, 9, 10, 8, 9, 7, 8, 6, 7, 5, 6, 4)
_Y = (0, 2, 0, 2, 0, 2, 0, 2, 0, 3, 0, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3)
_Z = (5, 3, 4, 2, 3, 1, 2, 0, 1, 13, 0, 12, 22, 11, 21, 10, 20, 9, 19, 8, 18, 7, 17, 6, 16, 5, 15, 4, 14, 3, 13, 2, 12, 1, 11, 0)

#: air bit index (0..71) -> (row, col) in the 4x24 channel frame.
AIR_TO_CELL: tuple[tuple[int, int], ...] = tuple(
    cell for i in range(36) for cell in ((_W[i], _X[i]), (_Y[i], _Z[i]))
)

#: Valid cells per row (row 0: 24, row 1: 23, row 2: 11, row 3: 14 -> 72 total).
ROW_WIDTHS = (24, 23, 11, 14)

#: NXDN only: descrambling mask for the high bit of each dibit across the four 72-bit voice frames of a
#: voice channel (144 bits), from the same ISC-licensed DSD source. Unverified against the NXDN spec.
NXDN_VOICE_MASK = np.array(
    [int(c) for c in
     "100100010100001010110100111111011001001001011011111100100110101001100110000000110001100101000110"
     "100101111111010001011000111010110010110011110001"],
    dtype=np.uint8,
)


def _validate_table() -> None:
    cells = set(AIR_TO_CELL)
    if len(AIR_TO_CELL) != 72 or len(cells) != 72:
        raise AssertionError("AIR_TO_CELL is not a bijection over 72 bits")
    for row, col in cells:
        if not (0 <= row < 4 and 0 <= col < ROW_WIDTHS[row]):
            raise AssertionError(f"cell {(row, col)} outside the frame")
    if len(NXDN_VOICE_MASK) != 144:
        raise AssertionError("NXDN mask must hold 144 bits")


_validate_table()

_ROWS = np.array([r for r, _ in AIR_TO_CELL], dtype=np.intp)
_COLS = np.array([c for _, c in AIR_TO_CELL], dtype=np.intp)


def air72_to_frame(bits72, *, descramble: Optional[np.ndarray] = None) -> np.ndarray:
    """72 air bits -> uint8[4,24] channel frame. `descramble` (72 bits, applied to the HIGH bit of every
    dibit) is the NXDN mask slice; leave it None for DMR."""
    air = np.asarray(bits72, dtype=np.uint8).reshape(-1) & 1
    if air.size != 72:
        raise ValueError(f"expected 72 bits, got {air.size}")
    if descramble is not None:
        mask = np.zeros(72, dtype=np.uint8)
        mask[0::2] = np.asarray(descramble, dtype=np.uint8).reshape(-1)[:36] & 1
        air = air ^ mask
    frame = np.zeros((4, 24), dtype=np.uint8)
    frame[_ROWS, _COLS] = air
    return frame


def frame_to_air72(frame, *, scramble: Optional[np.ndarray] = None) -> np.ndarray:
    """Inverse of air72_to_frame."""
    fr = np.asarray(frame, dtype=np.uint8).reshape(4, 24)
    air = (fr[_ROWS, _COLS] & 1).astype(np.uint8)
    if scramble is not None:
        mask = np.zeros(72, dtype=np.uint8)
        mask[0::2] = np.asarray(scramble, dtype=np.uint8).reshape(-1)[:36] & 1
        air = air ^ mask
    return air


# -- channel code --------------------------------------------------------------------------------


def _word_from_row(fr: np.ndarray, row: int, lo: int) -> int:
    """23 cells fr[row][lo .. lo+22] -> int with the highest column as the MSB (mbelib's in[22])."""
    return sum(int(fr[row][lo + j]) << j for j in range(23))


def _row1_mask(data12: int) -> list[int]:
    """Mask for row 1, index = column. pr[0] = 16*data12; pr[k] = (173*pr[k-1] + 13849) mod 65536;
    column 22 takes bit 15 of pr[1], column 21 pr[2], ... column 0 pr[23]."""
    pr = 16 * data12
    mask = [0] * 23
    for k in range(1, 24):
        pr = (173 * pr + 13849) & 0xFFFF
        mask[23 - k] = pr >> 15
    return mask


def decode_ambe2450_frame(frame) -> tuple[np.ndarray, int]:
    """4x24 channel frame -> (49 parameter bits, corrected-error count). Total errors count every
    Golay-corrected bit (data or parity) in rows 0 and 1."""
    fr = np.asarray(frame, dtype=np.uint8).reshape(4, 24).copy()

    # Row 0: Golay(23,12) over cols 1..23 (col 0 is the extended-code parity bit, unused for decoding).
    res0 = golay.golay_23_12_decode(_word_from_row(fr, 0, 1))
    if res0 is None:  # never for the perfect (23,12) code, kept for safety
        data0, err0 = _word_from_row(fr, 0, 1) >> 11, 0
    else:
        data0, err0 = res0

    # Row 1 is scrambled with a sequence derived from row 0's corrected data bits.
    mask = _row1_mask(data0)
    for j in range(23):
        fr[1][j] ^= mask[j]
    res1 = golay.golay_23_12_decode(_word_from_row(fr, 1, 0))
    if res1 is None:
        data1, err1 = _word_from_row(fr, 1, 0) >> 11, 0
    else:
        data1, err1 = res1

    out = np.zeros(49, dtype=np.uint8)
    out[0:12] = [(data0 >> (11 - i)) & 1 for i in range(12)]
    out[12:24] = [(data1 >> (11 - i)) & 1 for i in range(12)]
    out[24:35] = [fr[2][10 - i] for i in range(11)]
    out[35:49] = [fr[3][13 - i] for i in range(14)]
    return out, int(err0 + err1)


def encode_ambe2450_frame(bits49) -> np.ndarray:
    """49 parameter bits -> 4x24 channel frame (the inverse of decode; used by tests and by the encoder)."""
    b = np.asarray(bits49, dtype=np.uint8).reshape(-1) & 1
    if b.size != 49:
        raise ValueError(f"expected 49 bits, got {b.size}")
    data0 = int("".join(str(int(x)) for x in b[0:12]), 2)
    data1 = int("".join(str(int(x)) for x in b[12:24]), 2)
    fr = np.zeros((4, 24), dtype=np.uint8)
    cw0 = golay.golay_23_12_encode(data0)
    for j in range(23):
        fr[0][1 + j] = (cw0 >> j) & 1
    fr[0][0] = int(bin(cw0).count("1") & 1)  # extended-Golay overall parity (even weight over 24 bits)
    cw1 = golay.golay_23_12_encode(data1)
    mask = _row1_mask(data0)
    for j in range(23):
        fr[1][j] = ((cw1 >> j) & 1) ^ mask[j]
    for i in range(11):
        fr[2][10 - i] = b[24 + i]
    for i in range(14):
        fr[3][13 - i] = b[35 + i]
    return fr


def decode_air72(bits72) -> tuple[np.ndarray, int]:
    """The DMR default `ambe_channel_decode` hook: 72 air bits -> (49 parameter bits, errors)."""
    return decode_ambe2450_frame(air72_to_frame(bits72))


def encode_air72(bits49) -> np.ndarray:
    """49 parameter bits -> the 72 air bits of one DMR voice frame (for the test encoder)."""
    return frame_to_air72(encode_ambe2450_frame(bits49))


def golay_consistency(bits72) -> tuple[int, int]:
    """(errors corrected, 0|1) quick check used by the capture-verification script: the number of
    Golay corrections the frame needed. A correct mapping gives mostly 0-1 on clean frames; a wrong one
    gives about the number expected from random words (see the script)."""
    _, errs = decode_air72(bits72)
    return errs, int(errs == 0)
