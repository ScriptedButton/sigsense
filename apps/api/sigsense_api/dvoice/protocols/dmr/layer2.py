"""DMR layer-2 field codecs: CACH/TACT, slot type, EMB and the CACH short link control.

Both directions live here (the test encoder reuses the encode side) and are written
from ETSI TS 102 361-1 clauses 6, 9.1 and annex B (figures B.5, B.6, B.9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from sigsense_api.dvoice.fec import bch, crc, golay, hamming
from sigsense_api.dvoice.protocols.dmr import constants as C
from sigsense_api.dvoice.protocols.dmr.bits import bits_to_int, int_to_bits


@dataclass(frozen=True)
class Tact:
    at: int
    tc: int
    lcss: int
    corrected: int


def decode_cach(bits24: np.ndarray) -> tuple[Optional[Tact], np.ndarray]:
    """24 CACH bits (transmit order) -> (TACT or None when uncorrectable, 17 payload bits P16..P0)."""
    bits = np.asarray(bits24, dtype=np.uint8)
    if bits.size != C.CACH_BITS:
        raise ValueError("need 24 CACH bits")
    payload = bits[list(C.CACH_PAYLOAD_POSITIONS)]
    word7 = bits_to_int(bits[list(C.CACH_TACT_POSITIONS)])
    res = hamming.hamming_7_4_decode(word7)
    if res is None:
        return None, payload
    data4, corrected = res
    return Tact((data4 >> 3) & 1, (data4 >> 2) & 1, data4 & 3, corrected), payload


def encode_cach(at: int, tc: int, lcss: int, payload17: np.ndarray) -> np.ndarray:
    payload = np.asarray(payload17, dtype=np.uint8)
    if payload.size != 17:
        raise ValueError("need 17 payload bits")
    word7 = hamming.hamming_7_4_encode(((at & 1) << 3) | ((tc & 1) << 2) | (lcss & 3))
    out = np.zeros(C.CACH_BITS, dtype=np.uint8)
    out[list(C.CACH_TACT_POSITIONS)] = int_to_bits(word7, 7)
    out[list(C.CACH_PAYLOAD_POSITIONS)] = payload
    return out


@dataclass(frozen=True)
class SlotType:
    cc: int
    data_type: int
    corrected: int


def decode_slot_type(bits20: np.ndarray) -> Optional[SlotType]:
    res = golay.golay_20_8_decode(bits_to_int(bits20))
    if res is None:
        return None
    data8, corrected = res
    return SlotType(data8 >> 4, data8 & 0xF, corrected)


def encode_slot_type(cc: int, data_type: int) -> np.ndarray:
    return int_to_bits(golay.golay_20_8_encode(((cc & 0xF) << 4) | (data_type & 0xF)), 20)


@dataclass(frozen=True)
class Emb:
    cc: int
    pi: int
    lcss: int
    corrected: int


def decode_emb(bits16: np.ndarray) -> Optional[Emb]:
    res = bch.qr_16_7_6_decode(bits_to_int(bits16))
    if res is None:
        return None
    data7, corrected = res
    return Emb((data7 >> 3) & 0xF, (data7 >> 2) & 1, data7 & 3, corrected)


def encode_emb(cc: int, pi: int, lcss: int) -> np.ndarray:
    return int_to_bits(bch.qr_16_7_6_encode(((cc & 0xF) << 3) | ((pi & 1) << 2) | (lcss & 3)), 16)


# ---- short link control in the CACH (figures B.5, B.6) -------------------------------

SHORT_LC_ROWS = 4  # three Hamming(17,12,3) rows + a column-parity row


@dataclass(frozen=True)
class ShortLc:
    slco: int
    data: int  # 24 bits
    corrected: int


def encode_short_lc(slco: int, data24: int) -> list[np.ndarray]:
    """4-bit SLCO + 24 data bits (+ 8-bit CRC) -> the four 17-bit CACH payloads."""
    lc = int_to_bits(((slco & 0xF) << 24) | (data24 & 0xFFFFFF), 28)
    info = np.concatenate([lc, int_to_bits(crc.crc_compute("crc8_dmr_shortlc", lc), 8)])
    matrix = np.zeros((SHORT_LC_ROWS, 17), dtype=np.uint8)
    for r in range(3):
        matrix[r] = int_to_bits(hamming.hamming_17_12_3_encode(bits_to_int(info[12 * r : 12 * r + 12])), 17)
    matrix[3] = matrix[:3].sum(axis=0) & 1
    flat = matrix.T.ravel()  # columns top to bottom, left to right
    return [flat[17 * k : 17 * k + 17].copy() for k in range(SHORT_LC_ROWS)]


def decode_short_lc(payloads: list[np.ndarray]) -> Optional[ShortLc]:
    """Four 17-bit CACH payloads (first-to-last) -> Short LC, or None unless the CRC-8 passes."""
    if len(payloads) != SHORT_LC_ROWS:
        return None
    flat = np.concatenate([np.asarray(p, dtype=np.uint8) for p in payloads])
    matrix = flat.reshape(17, SHORT_LC_ROWS).T.copy()
    corrected = 0
    info = []
    for r in range(3):
        res = hamming.hamming_17_12_3_decode(bits_to_int(matrix[r]))
        if res is None:
            return None
        data12, fixed = res
        corrected += fixed
        info.append(int_to_bits(data12, 12))
    info_bits = np.concatenate(info)
    if not crc.crc_check("crc8_dmr_shortlc", info_bits):
        return None
    lc = info_bits[:28]
    return ShortLc(bits_to_int(lc[:4]), bits_to_int(lc[4:28]), corrected)
