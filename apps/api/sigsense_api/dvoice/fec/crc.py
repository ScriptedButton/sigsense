"""CRC engine and named CRCs (CONTRACT v1). Owner: WP-A.

`crc_compute` / `crc_check` are real (generic bitwise engine). WP-A adds every
spec CRC to CRC_SPECS from the air-interface documents, each with a known-answer
test. Only specs whose parameters are standard (check value over b"123456789")
are pre-filled here; protocol-specific ones (P25 TSBK/PDU/LC, DMR CRC-9 data
block, NXDN CRC-6/7/12/15/16, M17 LSF, ...) are WP-A's to add.

Bit order: `bits` is a uint8 array of 0/1, MSB-first transmission order.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CrcSpec:
    width: int
    poly: int
    init: int = 0
    xorout: int = 0
    refin: bool = False
    refout: bool = False
    note: str = ""


CRC_SPECS: dict[str, CrcSpec] = {
    # Standard catalogue CRCs (check value of b"123456789" noted for the test).
    "crc16_ccitt_false": CrcSpec(16, 0x1021, 0xFFFF, 0x0000, False, False, "check 0x29B1"),
    "crc16_genibus": CrcSpec(16, 0x1021, 0xFFFF, 0xFFFF, False, False, "check 0xD64E"),
    "crc16_x25": CrcSpec(16, 0x1021, 0xFFFF, 0xFFFF, True, True, "D-STAR header; check 0x906E"),
    # --- From the air-interface specs (clause numbers in the notes) -----------------
    # CRC-CCITT with an all-ones inversion polynomial and zero initial remainder:
    # P25 data header CRC (TIA-102.BAAA-A 6.2) and DMR (ETSI TS 102 361-1 B.3.8).
    "crc16_p25_dmr_header": CrcSpec(16, 0x1021, 0x0000, 0xFFFF, False, False, "TIA-102.BAAA-A 6.2; ETSI B.3.8"),
    # Packet CRC-32, GM(x) = x^32+x^26+x^23+x^22+x^16+x^12+x^11+x^10+x^8+x^7+x^5+x^4+x^2+x+1,
    # inverted: P25 (TIA-102.BAAA-A 6.3.3). The DMR variant (B.3.9) has no inversion and
    # reorders octets: use crc32_dmr_message().
    "crc32_p25_packet": CrcSpec(32, 0x04C11DB7, 0x0, 0xFFFFFFFF, False, False, "TIA-102.BAAA-A 6.3.3"),
    "crc32_dmr_raw": CrcSpec(32, 0x04C11DB7, 0x0, 0x0, False, False, "ETSI B.3.9 polynomial, octets as given"),
    # CRC-9 for confirmed data blocks, G9 = x^9+x^6+x^4+x^3+1, inverted (both specs).
    "crc9_p25_dmr": CrcSpec(9, 0x059, 0x0, 0x1FF, False, False, "TIA-102.BAAA-A 6.4.2; ETSI B.3.10"),
    # P25 Phase 2 MAC CRC-12 (FACCH 144 bits, SACCH 168 bits), g = x^12+x^11+x^7+x^4+x^2+x+1, zero
    # initial remainder, inverted. The LCCH CRC-16 is crc16_p25_dmr_header (CCITT, zero init, inverted).
    # Checked bit-for-bit against SDRTrunk's published per-bit tables (see protocols/p25p2/framing.py).
    "crc12_p25p2": CrcSpec(12, 0x897, 0x0, 0xFFF, False, False, "P25 Phase 2 FACCH/SACCH"),
    # NXDN TS 1-A v1.3 section 4.5.5: shift register preset to all ones, no output inversion
    # stated; polynomials from sections 4.5.1-4.5.2 (16-bit is CRC-CCITT = crc16_ccitt_false).
    "crc6_nxdn": CrcSpec(6, 0x27, 0x3F, 0x0, False, False, "NXDN SACCH: x^6+x^5+x^2+x+1"),
    "crc12_nxdn": CrcSpec(12, 0x80F, 0xFFF, 0x0, False, False, "NXDN FACCH1: x^12+x^11+x^3+x^2+x+1"),
    "crc15_nxdn": CrcSpec(15, 0x4CC5, 0x7FFF, 0x0, False, False, "NXDN UDCH/FACCH2: x^15+x^14+x^11+x^10+x^7+x^6+x^2+1"),
    # M17 (spec section 2.6): non-reflected, init 0xFFFF; test vectors "" FFFF, "A" 206E, "123456789" 772B.
    "crc16_m17": CrcSpec(16, 0x5935, 0xFFFF, 0x0000, False, False, "M17 LSF/stream; check 0x772B"),
    # DMR short CRCs (ETSI B.3.7, B.3.13): no inversion, zero initial remainder.
    "crc8_dmr_shortlc": CrcSpec(8, 0x07, 0x0, 0x0, False, False, "ETSI B.3.7, G8 = x^8+x^2+x+1"),
    "crc7_dmr_rc": CrcSpec(7, 0x27, 0x0, 0x0, False, False, "ETSI B.3.13, G7 = x^7+x^5+x^2+x+1"),
}

# ETSI TS 102 361-1 Table B.21: Data Type CRC masks (XORed onto the transmitted CRC).
DMR_CRC_MASKS_16: dict[str, int] = {
    "pi_header": 0x6969,
    "csbk": 0xA5A5,
    "mbc_header": 0xAAAA,
    "data_header": 0xCCCC,
    "unified_single_block_data": 0x3333,
}
DMR_CRC_MASKS_OTHER: dict[str, int] = {
    "voice_lc_header_24": 0x969696,
    "terminator_with_lc_24": 0x999999,
    "rate_1_2_data_continuation_crc9": 0x0F0,
    "rate_3_4_data_continuation_crc9": 0x1FF,
    "rate_1_data_continuation_crc9": 0x10F,
    "reverse_channel_crc7": 0x7A,
}


def bytes_to_bits(data: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8)).astype(np.uint8)


def crc_compute(name: str, bits: np.ndarray) -> int:
    """CRC of `bits` (0/1, MSB-first) under CRC_SPECS[name]."""
    spec = CRC_SPECS[name]
    top = 1 << (spec.width - 1)
    mask = (1 << spec.width) - 1
    reg = spec.init & mask
    stream = np.asarray(bits, dtype=np.uint8)
    if spec.refin:
        # Reflected input: each byte's bits arrive LSB first.
        pad = (-len(stream)) % 8
        if pad:
            stream = np.concatenate([stream, np.zeros(pad, dtype=np.uint8)])
        stream = stream.reshape(-1, 8)[:, ::-1].reshape(-1)
        if pad:
            stream = stream[: len(stream) - pad]
    for bit in stream:
        feedback = ((reg & top) != 0) ^ bool(bit)
        reg = (reg << 1) & mask
        if feedback:
            reg ^= spec.poly
    if spec.refout:
        reg = int(f"{reg:0{spec.width}b}"[::-1], 2)
    return (reg ^ spec.xorout) & mask


def crc_check(name: str, bits_with_crc: np.ndarray) -> bool:
    """True when the trailing `width` bits equal the CRC of the leading bits."""
    spec = CRC_SPECS[name]
    bits = np.asarray(bits_with_crc, dtype=np.uint8)
    body, tail = bits[: -spec.width], bits[-spec.width :]
    received = int("".join(str(int(b)) for b in tail), 2)
    return crc_compute(name, body) == received


def crc_apply_mask(crc_value: int, mask: int) -> int:
    """XOR a data-type CRC mask onto a CRC value (apply before FEC on transmit, and
    again after FEC decoding on receive, ETSI TS 102 361-1 B.3.12)."""
    return crc_value ^ mask


def crc_compute_bytes(name: str, data: bytes) -> int:
    """CRC of whole octets (MSB first), same result as crc_compute(name, bytes_to_bits(data))."""
    return crc_compute(name, bytes_to_bits(data))


def dmr_checksum5(lc_bytes: bytes) -> int:
    """5-bit checksum of the 72-bit embedded LC (ETSI B.3.11): sum of the 9 octets mod 31."""
    if len(lc_bytes) != 9:
        raise ValueError("embedded LC is 9 octets")
    return sum(lc_bytes) % 31


def crc32_dmr_message(data: bytes) -> bytes:
    """Message CRC of a DMR data packet (ETSI B.3.9): the user data is taken as 16-bit
    words fed least-significant octet first (i.e. every pair of octets swapped), no
    inversion, zero initial remainder; the four CRC octets come out least significant
    first. Returns those four octets in transmission order. `data` must have an even length."""
    if len(data) % 2:
        raise ValueError("data must be whole 16-bit words")
    swapped = bytearray(data)
    swapped[0::2], swapped[1::2] = data[1::2], data[0::2]
    value = crc_compute_bytes("crc32_dmr_raw", bytes(swapped))
    return value.to_bytes(4, "little")
