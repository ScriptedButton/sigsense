"""Bit/dibit helpers for the DMR decoder (original code)."""

from __future__ import annotations

import numpy as np


def dibits_to_bits(dibits: np.ndarray) -> np.ndarray:
    """uint8 dibit values 0..3 -> bits, most significant bit of each dibit first."""
    d = np.asarray(dibits, dtype=np.uint8)
    out = np.empty(d.size * 2, dtype=np.uint8)
    out[0::2] = (d >> 1) & 1
    out[1::2] = d & 1
    return out


def bits_to_dibits(bits: np.ndarray) -> np.ndarray:
    b = np.asarray(bits, dtype=np.uint8)
    if b.size % 2:
        raise ValueError("odd number of bits")
    return ((b[0::2] << 1) | b[1::2]).astype(np.uint8)


def bits_to_int(bits) -> int:
    value = 0
    for b in np.asarray(bits, dtype=np.uint8).ravel():
        value = (value << 1) | int(b)
    return value


def int_to_bits(value: int, width: int) -> np.ndarray:
    return np.array([(value >> (width - 1 - i)) & 1 for i in range(width)], dtype=np.uint8)


def bits_to_bytes(bits: np.ndarray) -> bytes:
    b = np.asarray(bits, dtype=np.uint8)
    if b.size % 8:
        raise ValueError("bit count must be a multiple of 8")
    return np.packbits(b).tobytes()


def bytes_to_bits(data: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(bytes(data), dtype=np.uint8))
