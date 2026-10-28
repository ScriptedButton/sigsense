"""P25 Phase 1 FDMA framing helpers (TIA-102.BAAA-A section 8). Original code.

A data unit is: frame sync (24 dibits) + NID (32 dibits) + payload, with one
status symbol (a dibit) inserted after every 35 information dibits, counted from
the first sync dibit. A unit whose information length is a multiple of 35 dibits
carries a trailing status symbol (that is how the terminator comes to 72 dibits).

Dibit convention is the front end's (dsp/types.py): value = (bit1 << 1) | bit0,
+3 -> 1, +1 -> 0, -1 -> 2, -3 -> 3. The frame sync word is 0x5575F5FF77FF.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

SYNC_LEN = 24
NID_LEN = 32  # information dibits (64 bits)
HEADER_INFO_DIBITS = SYNC_LEN + NID_LEN  # 56
STATUS_PERIOD = 35  # information dibits between status symbols

# Table 8-1, expanded to dibits (1 = +3 = 01, 3 = -3 = 11).
SYNC_DIBITS = np.array([int(c) for c in "111113113311333313133333"], dtype=np.uint8)

# Data unit IDs (Table 8-4). TSDU (0x7) is the trunking signalling data unit; it is not
# in the BAAA copy this was written from (BAAA says the other values are reserved for
# trunking), see docs/digital-voice-architecture.md.
DUID_HDU = 0x0
DUID_TDU = 0x3
DUID_LDU1 = 0x5
DUID_TSDU = 0x7
DUID_LDU2 = 0xA
DUID_PDU = 0xC
DUID_TDULC = 0xF

DUID_NAMES = {
    DUID_HDU: "HDU",
    DUID_TDU: "TDU",
    DUID_LDU1: "LDU1",
    DUID_TSDU: "TSDU",
    DUID_LDU2: "LDU2",
    DUID_PDU: "PDU",
    DUID_TDULC: "TDULC",
}

# Information dibits (sync + NID + payload) of the fixed-length units.
HDU_INFO_DIBITS = 385  # 770 bits = FS 48 + NID 64 + header 648 + nulls 10
LDU_INFO_DIBITS = 840  # 1680 bits
TDU_INFO_DIBITS = 70  # 140 bits = FS + NID + 28 nulls
TDULC_INFO_DIBITS = 210  # 420 bits = FS + NID + LC 288 + nulls 20
TSBK_BLOCK_DIBITS = 98  # one trellis coded block, 196 bits


def total_dibits(info_dibits: int) -> int:
    """Information dibits -> dibits on the wire (status symbols included)."""
    return info_dibits + (info_dibits + STATUS_PERIOD - 1) // STATUS_PERIOD


def info_positions(n_info: int) -> np.ndarray:
    """Wire index of information dibit j, j in [0, n_info): j + j // 35."""
    j = np.arange(n_info, dtype=np.int64)
    return j + j // STATUS_PERIOD


def strip_status(frame: np.ndarray, n_info: int) -> Optional[np.ndarray]:
    """Information dibits of a unit that starts at frame[0], or None if too short."""
    if frame.size < total_dibits(n_info):
        return None
    return frame[info_positions(n_info)]


def insert_status(info: np.ndarray, status: int = 2) -> np.ndarray:
    """Information dibits -> wire dibits, inserting a status symbol after every 35
    (including a trailing one when the length is a multiple of 35)."""
    info = np.asarray(info, dtype=np.uint8)
    n = info.size
    out = np.full(total_dibits(n), status & 3, dtype=np.uint8)
    out[info_positions(n)] = info
    return out


def dibits_to_bits(dibits: np.ndarray) -> np.ndarray:
    d = np.asarray(dibits, dtype=np.uint8)
    bits = np.empty(d.size * 2, dtype=np.uint8)
    bits[0::2] = (d >> 1) & 1
    bits[1::2] = d & 1
    return bits


def bits_to_dibits(bits: np.ndarray) -> np.ndarray:
    b = np.asarray(bits, dtype=np.uint8)
    if b.size % 2:
        raise ValueError("bit count must be even")
    return ((b[0::2] << 1) | b[1::2]).astype(np.uint8)


def bits_to_int(bits: np.ndarray) -> int:
    v = 0
    for b in np.asarray(bits).tolist():
        v = (v << 1) | (b & 1)
    return v


def int_to_bits(value: int, width: int) -> np.ndarray:
    return np.array([(value >> (width - 1 - i)) & 1 for i in range(width)], dtype=np.uint8)


def sync_errors(window: np.ndarray, inverted: bool = False) -> int:
    """Dibit mismatches between a 24-dibit window and the sync word (or its inversion)."""
    ref = SYNC_DIBITS ^ 2 if inverted else SYNC_DIBITS
    return int(np.count_nonzero(np.asarray(window[:SYNC_LEN]) != ref))


# Layout of the 784 payload dibits after sync + NID in an LDU1/LDU2 (Figures 8-3, 8-4):
# VC1 VC2 LC0 VC3 LC1 VC4 LC2 VC5 LC3 VC6 LC4 VC7 LC5 VC8 LSD0 VC9 LSD1, where
# VC = 72-dibit IMBE voice frame, LC = 20 dibits (four (10,6) Hamming words of the
# 24-word link control / encryption sync word), LSD = 8 dibits (one (16,8,5) word).
# Voice frames sit exactly as drawn; the LC/LSD interleaving between them follows the
# figures and is cross-checked only by the encoder here (no real capture available).
LDU_SEGMENTS: tuple[tuple[str, int], ...] = (
    ("vc", 72), ("vc", 72), ("lc", 20), ("vc", 72), ("lc", 20), ("vc", 72), ("lc", 20),
    ("vc", 72), ("lc", 20), ("vc", 72), ("lc", 20), ("vc", 72), ("lc", 20), ("vc", 72),
    ("lsd", 8), ("vc", 72), ("lsd", 8),
)
assert HEADER_INFO_DIBITS + sum(n for _, n in LDU_SEGMENTS) == LDU_INFO_DIBITS


def split_ldu(info: np.ndarray) -> tuple[list[np.ndarray], np.ndarray, list[np.ndarray]]:
    """840 information dibits -> (9 voice frames of 72 dibits, 240 LC/ES bits, 2 LSD words of 16 bits)."""
    voice: list[np.ndarray] = []
    lc: list[np.ndarray] = []
    lsd: list[np.ndarray] = []
    pos = HEADER_INFO_DIBITS
    for kind, n in LDU_SEGMENTS:
        seg = info[pos : pos + n]
        pos += n
        (voice if kind == "vc" else lc if kind == "lc" else lsd).append(seg)
    lc_bits = dibits_to_bits(np.concatenate(lc))
    return voice, lc_bits, [dibits_to_bits(s) for s in lsd]
