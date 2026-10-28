"""Scramblers / whiteners (CONTRACT v1). Owner: WP-A. Original code.

`lfsr_sequence` is real and generic. Named sequences come from each air
interface's public spec; they are keyed XOR streams that every decoder applies to
the bits/symbols after FEC. (Whitening, not encryption: parameters are public.)
Sequences that no readable public document defines raise NotImplementedError naming
the gap instead of guessing (the P25 Phase 2 one is taken from an open-source decoder, see its docstring).
"""

from __future__ import annotations

import numpy as np


def lfsr_sequence(length: int, taps: tuple[int, ...], seed: int, width: int) -> np.ndarray:
    """Fibonacci LFSR as a plain recurrence: out[n] = XOR of out[n - t] for t in `taps`.

    `seed` holds the `width` bits that precede the output: its MSB is out[-width]
    and its LSB is out[-1]. With taps (9, 5) and width 9 this is the primitive
    polynomial x^9 + x^4 + 1 (period 511). Spec-specific scramblers (NXDN, P25 P2,
    M17, D-STAR, YSF) map their published register layouts onto this definition.
    """
    history = [(seed >> (width - 1 - i)) & 1 for i in range(width)]
    out = np.empty(length, dtype=np.uint8)
    for n in range(length):
        bit = 0
        for t in taps:
            bit ^= history[-t]
        history.append(bit)
        out[n] = bit
    return out


def p25p2_scramble_sequence(wacn: int, system_id: int, nac: int, length: int = 4320) -> np.ndarray:
    """P25 Phase 2 TDMA scrambling sequence from WACN/SysID/NAC (TIA-102.BBAC 7.2.5).

    A 44-bit Galois-style register G(x) = x^44 + x^40 + x^35 + x^29 + x^24 + x^10 + 1 is loaded with
    WACN(20) | SystemID(12) | NAC(12), MSB first (all ones when the seed is zero). Each step outputs the
    top bit, and shifts in the XOR of register bits 43, 33, 19, 14, 8 and 3. The first 4320 bits cover one
    12-slot superframe; slot s uses bits [20 + 360*s, 340 + 360*s).

    PROVENANCE: TIA-102.BBAC is not publicly readable. The register layout, taps and the 20-bit offset were
    taken from the open-source SDRTrunk decoder (io.github.dsheirer.module.decode.p25.phase2.timeslot,
    LinearFeedbackShiftRegister / ScramblingSequence, which cites 7.2.5 and decodes live systems).
    Only the numeric facts were used. The unit tests check the register is maximal-length-like and
    self-consistent; they cannot prove it against the air without a capture.
    """
    if length < 0:
        raise ValueError("length must be non-negative")
    mask = (1 << 44) - 1
    reg = ((wacn & 0xFFFFF) << 24) | ((system_id & 0xFFF) << 12) | (nac & 0xFFF)
    if reg == 0:
        reg = mask
    out = np.empty(length, dtype=np.uint8)
    for n in range(length):
        top = (reg >> 43) & 1
        fb = top ^ ((reg >> 33) & 1) ^ ((reg >> 19) & 1) ^ ((reg >> 14) & 1) ^ ((reg >> 8) & 1) ^ ((reg >> 3) & 1)
        reg = ((reg << 1) & mask) | fb
        out[n] = top
    return out


# NXDN TS 1-A v1.3 section 4.6: PN code x^9 + x^4 + 1, register S8..S0 preset to 0 1 1 1 0 0 1 0 0.
# The output is S0 and the register shifts toward S0; the first nine outputs are therefore S0..S8
# of the preset, and afterwards out[n+9] = out[n] ^ out[n+4]. Verified symbol-by-symbol against
# the NXDN TS 2-B conformance vectors (LICH and CAC "after dibit" vs "after scrambling").
_NXDN_PRESET_OUTPUT = (0, 0, 1, 0, 0, 1, 1, 1, 0)


def nxdn_scramble_sequence(length: int) -> np.ndarray:
    """NXDN scrambler stream S0, S1, ... (one value per 4-level SYMBOL, restarted every frame).

    A symbol is scrambled by sign inversion when the stream value is 1, i.e. for a dibit
    d (01 = +3, 00 = +1, 10 = -1, 11 = -3): d ^ (s << 1). The frame sync word is not
    scrambled, so the LICH starts at S0."""
    out = np.empty(length, dtype=np.uint8)
    out[: min(length, 9)] = _NXDN_PRESET_OUTPUT[: min(length, 9)]
    for n in range(9, length):
        out[n] = out[n - 9] ^ out[n - 5]  # out[n] = out[(n-9)] ^ out[(n-9)+4]
    return out


# M17 specification Table B.1: the 46-byte (368-bit) randomizer. Bits are taken MSB first and
# the sequence restarts after byte 45.
_M17_RANDOMIZER_BYTES = bytes([
    0xD6, 0xB5, 0xE2, 0x30, 0x82, 0xFF, 0x84, 0x62, 0xBA, 0x4E, 0x96, 0x90,
    0xD8, 0x98, 0xDD, 0x5D, 0x0C, 0xC8, 0x52, 0x43, 0x91, 0x1D, 0xF8, 0x6E,
    0x68, 0x2F, 0x35, 0xDA, 0x14, 0xEA, 0xCD, 0x76, 0x19, 0x8D, 0xD5, 0x80,
    0xD1, 0x33, 0x87, 0x13, 0x57, 0x18, 0x2D, 0x29, 0x78, 0xC3,
])


def m17_randomizer(length: int) -> np.ndarray:
    """M17 fixed randomizer sequence (M17 specification section 1.4.4, Table B.1)."""
    bits = np.unpackbits(np.frombuffer(_M17_RANDOMIZER_BYTES, dtype=np.uint8))
    return np.resize(bits, length).astype(np.uint8)


def dstar_scrambler(length: int) -> np.ndarray:
    """D-STAR scrambler S(x) = x^7 + x^4 + 1, initialised to all ones (JARL D-STAR spec AP1.1).

    Returns the bit stream in transmission order (D-STAR sends each byte LSB first), so
    out[n] = out[n-7] ^ out[n-4] with the seven bits before the output all ones. Applied by
    XOR to the 660 error-corrected header bits (and to the voice/data frames except the
    sync and last frame; AP1.2-1.3). UNVERIFIED against a worked example: the register tap
    and shift direction are read from the spec's recurrence, not from a figure."""
    return lfsr_sequence(length, (7, 4), 0x7F, 7)


def ysf_whitening(length: int) -> np.ndarray:
    raise NotImplementedError(
        "no public specification of the Yaesu System Fusion air interface was readable; "
        "the whitening sequence cannot be implemented without guessing."
    )
