"""Small bit helpers for the NXDN decoder (WP-E). Dibit convention: dsp/types.py
(+3 -> 01, +1 -> 00, -1 -> 10, -3 -> 11), which is also the NXDN symbol mapping (TS 1-A figure 4.5-7)."""

from __future__ import annotations

import numpy as np


def dibits_to_bits(dibits: np.ndarray) -> np.ndarray:
    d = np.asarray(dibits, dtype=np.uint8)
    out = np.empty(d.size * 2, dtype=np.uint8)
    out[0::2] = (d >> 1) & 1
    out[1::2] = d & 1
    return out


def symbols_to_soft_bits(soft: np.ndarray) -> np.ndarray:
    """Normalised symbol levels (-3..+3) -> soft bits (+1 = confident 0, -1 = confident 1).

    First bit of a dibit is the sign (0 for +3/+1); second bit is 1 for the outer symbols."""
    s = np.asarray(soft, dtype=np.float64)
    out = np.empty(s.size * 2, dtype=np.float64)
    out[0::2] = np.clip(s / 2.0, -1.0, 1.0)
    out[1::2] = np.clip(2.0 - np.abs(s), -1.0, 1.0)
    return out


def bits_to_bytes(bits: np.ndarray) -> bytes:
    return np.packbits(np.asarray(bits, dtype=np.uint8)).tobytes()


def bits_to_int(bits: np.ndarray) -> int:
    value = 0
    for b in np.asarray(bits, dtype=np.uint8):
        value = (value << 1) | int(b)
    return value
