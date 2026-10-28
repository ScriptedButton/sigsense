"""P25 Phase 1 IMBE voice code words (TIA-102.BAAA-A section 5.3). Original code.

A voice frame on the air is 144 bits (72 dibits): the 88 IMBE parameter bits
u0..u7 protected as

    c0      = Golay(23,12) of u0                       (u0: 12 bits)
    c1..c3  = Golay(23,12) of u1..u3, XOR PN mask m1..m3
    c4..c6  = Hamming(15,11) of u4..u6, XOR PN mask m4..m6   (u4..u6: 11 bits)
    c7      = u7 uncoded                                (7 bits)

where the 114-bit PN sequence is generated from u0: p0 = 16*u0,
p(n) = (173*p(n-1) + 13849) mod 65536, and bit 15 of p1..p114 fills m1..m6.
The code words are then interleaved by the schedule of Table 5-1 (fec.interleave
'imbe7200x4400_frame').

`decode_voice_frame` returns the 88 parameter bits, concatenated u0..u7 with each
word MSB first - the layout the vocoder adapters take (vocoder/base.py).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sigsense_api.dvoice.fec import golay, hamming, interleave

WORD_BITS = (23, 23, 23, 23, 15, 15, 15, 7)  # c0..c7
INFO_BITS = (12, 12, 12, 12, 11, 11, 11, 7)  # u0..u7
_OFFSETS = tuple(int(x) for x in np.cumsum((0,) + WORD_BITS[:-1]))
_PERM = interleave.table("imbe7200x4400_frame")

# Frames this damaged are reported as erased so the vocoder layer conceals them.
ERASE_TOTAL_ERRORS = 14
ERASE_C0_AND_TOTAL = (3, 6)


@dataclass
class VoiceFrameResult:
    bits: np.ndarray  # uint8, 88
    errors: int  # corrected bit errors over c0..c6
    c0_errors: int
    erased: bool  # too damaged to trust (heuristic, see ERASE_*)


def pn_masks(u0: int) -> list[int]:
    """The six PN masks m1..m6 as integers (leftmost bit = most significant)."""
    p = (16 * u0) & 0xFFFF
    bits = []
    for _ in range(114):
        p = (173 * p + 13849) & 0xFFFF
        bits.append(p >> 15)
    masks = []
    pos = 0
    for width in (23, 23, 23, 15, 15, 15):
        v = 0
        for k in range(width):
            v = (v << 1) | bits[pos + k]
        masks.append(v)
        pos += width
    return masks


def _word(bits: np.ndarray, i: int) -> int:
    v = 0
    for b in bits[_OFFSETS[i] : _OFFSETS[i] + WORD_BITS[i]].tolist():
        v = (v << 1) | b
    return v


def _int_bits(value: int, width: int) -> list[int]:
    return [(value >> (width - 1 - k)) & 1 for k in range(width)]


def decode_voice_frame(bits144: np.ndarray) -> VoiceFrameResult:
    bits = np.asarray(bits144, dtype=np.uint8)
    if bits.size != 144:
        raise ValueError("a P25 voice frame is 144 bits")
    cw = interleave.deinterleave(bits, _PERM)

    d0 = golay.golay_23_12_decode(_word(cw, 0))
    u0, e0 = d0 if d0 is not None else (0, 3)
    masks = pn_masks(u0)

    info = [u0]
    errors = e0
    for i in (1, 2, 3):
        r = golay.golay_23_12_decode(_word(cw, i) ^ masks[i - 1])
        u, e = r if r is not None else (0, 3)
        info.append(u)
        errors += e
    for i in (4, 5, 6):
        r = hamming.p25_hamming_15_11_3_decode(_word(cw, i) ^ masks[i - 1])
        u, e = r if r is not None else (0, 1)
        info.append(u)
        errors += e
    info.append(_word(cw, 7))

    out: list[int] = []
    for u, width in zip(info, INFO_BITS):
        out.extend(_int_bits(u, width))
    erased = errors >= ERASE_TOTAL_ERRORS or (e0 >= ERASE_C0_AND_TOTAL[0] and errors >= ERASE_C0_AND_TOTAL[1])
    return VoiceFrameResult(np.array(out, dtype=np.uint8), errors, e0, erased)


def encode_voice_frame(bits88: np.ndarray) -> np.ndarray:
    """88 parameter bits -> the 144 interleaved bits transmitted (test/encoder use)."""
    b = np.asarray(bits88, dtype=np.uint8)
    if b.size != 88:
        raise ValueError("an IMBE frame is 88 parameter bits")
    u = []
    pos = 0
    for width in INFO_BITS:
        v = 0
        for x in b[pos : pos + width].tolist():
            v = (v << 1) | x
        u.append(v)
        pos += width
    masks = pn_masks(u[0])
    words = [golay.golay_23_12_encode(u[0])]
    for i in (1, 2, 3):
        words.append(golay.golay_23_12_encode(u[i]) ^ masks[i - 1])
    for i in (4, 5, 6):
        words.append(hamming.p25_hamming_15_11_3_encode(u[i]) ^ masks[i - 1])
    words.append(u[7])
    cw: list[int] = []
    for w, width in zip(words, WORD_BITS):
        cw.extend(_int_bits(w, width))
    return interleave.interleave(np.array(cw, dtype=np.uint8), _PERM)
