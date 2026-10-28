"""Synthetic NXDN transmissions (WP-E test encoder). Original code from NXDN TS 1-A v1.3.

Separate from protocols/nxdn: it builds frames in transmit order from structured input, the way the
specification describes them (CRC -> null/tail bits -> convolutional code -> puncturing -> interleaving ->
scrambling), using the WP-A FEC primitives that reproduce the TS 2-B conformance vectors.

Voice (VCH) blocks are random bits: the AMBE+2 voice frame layout is not public, so this encoder (like the
decoder) does not model what is inside a VCH, only where it sits in the frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from sigsense_api.dvoice.fec import crc, interleave, viterbi, whitening

FSW_SYMBOLS = (-3, +1, -3, +3, -3, -3, +3, +3, -1, +3)  # TS 1-A table 4.4-2 (0xCDF59)
POST_TAIL = (-3, +3, -3, +3, +3, -3, -3, -3, +3)  # last 9 symbols of the post field
SYMBOL_TO_DIBIT = {+3: 1, +1: 0, -1: 2, -3: 3}

FSW = np.array([SYMBOL_TO_DIBIT[s] for s in FSW_SYMBOLS], dtype=np.uint8)
FRAME_SYMBOLS = 192
BODY_SYMBOLS = 182

_G1, _G2 = viterbi.NXDN_K5_R12.polys


def _keep(matrix):
    if matrix is None:
        return None
    return tuple(int(matrix[row][col]) for col in range(len(matrix[0])) for row in range(2))


# name -> (interleave table, info bits, null bits, crc name, crc width, puncturing matrix)
CHANNEL = {
    "cac": ("nxdn_cac_outbound", 152, 3, "crc16_ccitt_false", 16, ("1111111", "1011101")),
    "long_cac": ("nxdn_cac_long", 136, 0, "crc16_ccitt_false", 16, ("1111111111111", "0110001110111")),
    "short_cac": ("nxdn_cac_short", 104, 2, "crc16_ccitt_false", 16, None),
    "sacch": ("nxdn_sacch", 26, 0, "crc6_nxdn", 6, ("111111", "110110")),
    "facch1": ("nxdn_facch1", 80, 0, "crc12_nxdn", 12, ("11", "01")),
    "udch": ("nxdn_udch", 184, 0, "crc15_nxdn", 15, ("1111111", "1011101")),
}


def int_bits(value: int, width: int) -> np.ndarray:
    return np.array([(value >> (width - 1 - i)) & 1 for i in range(width)], dtype=np.uint8)


def bytes_bits(data: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8))


def encode_channel(name: str, info: np.ndarray) -> np.ndarray:
    """Info bits -> the coded, interleaved bits of the functional channel (TS 1-A 4.5)."""
    table, n_info, nulls, crc_name, crc_width, matrix = CHANNEL[name]
    assert len(info) == n_info, (name, len(info), n_info)
    data = np.concatenate([np.asarray(info, dtype=np.uint8), np.zeros(nulls, dtype=np.uint8)])
    value = crc.crc_compute(crc_name, data)
    data = np.concatenate([data, int_bits(value, crc_width)])
    code = viterbi.ConvCode(5, (_G1, _G2), _keep(matrix), 4)
    return interleave.interleave(viterbi.conv_encode(code, data), interleave.table(table))


def bits_to_dibits(bits: np.ndarray) -> np.ndarray:
    b = np.asarray(bits, dtype=np.uint8)
    return ((b[0::2] << 1) | b[1::2]).astype(np.uint8)


def lich_symbols(rf: int, fn: int, opt: int, outbound: bool) -> np.ndarray:
    """7 LICH bits + even parity over the four MSBs; each bit is one symbol (0 -> +3, 1 -> -3)."""
    bits = [(rf >> 1) & 1, rf & 1, (fn >> 1) & 1, fn & 1, (opt >> 1) & 1, opt & 1, 1 if outbound else 0]
    parity = bits[0] ^ bits[1] ^ bits[2] ^ bits[3]
    return np.array([1 if b == 0 else 3 for b in bits + [parity]], dtype=np.uint8)  # dibit 01 = +3, 11 = -3


_SCRAMBLE = whitening.nxdn_scramble_sequence(BODY_SYMBOLS).astype(np.uint8)


def assemble(lich: np.ndarray, body: np.ndarray, post: Optional[np.ndarray] = None) -> np.ndarray:
    """FSW + scrambled (LICH + body). `post` symbols (unscrambled) replace the tail of the body when given."""
    payload = np.concatenate([lich, body]).astype(np.uint8)
    assert payload.size == BODY_SYMBOLS, payload.size
    scrambled = payload ^ (_SCRAMBLE << 1)
    if post is not None:
        scrambled[BODY_SYMBOLS - len(post) :] = post
    return np.concatenate([FSW, scrambled])


# --- layer 3 ---------------------------------------------------------------------------------------


def sr(structure: int, ran: int) -> np.ndarray:
    return int_bits(((structure & 3) << 6) | (ran & 0x3F), 8)


def l3_vcall(src: int, dst: int, *, call_type: int = 0, cipher: int = 0, key_id: int = 0, emergency: bool = False, tx_mode: int = 2, duplex: bool = False, msg_type: int = 0x01) -> bytes:
    cc = 0x80 if emergency else 0
    option = (0x10 if duplex else 0) | (tx_mode & 7)
    return bytes(
        [msg_type & 0x3F, cc, ((call_type & 7) << 5) | option, src >> 8, src & 0xFF, dst >> 8, dst & 0xFF, ((cipher & 3) << 6) | (key_id & 0x3F)]
    )


def l3_tx_rel(src: int, dst: int, *, call_type: int = 0, extended: bool = False) -> bytes:
    msg = [0x07 if extended else 0x08, 0, (call_type & 7) << 5, src >> 8, src & 0xFF, dst >> 8, dst & 0xFF]
    if extended:
        msg.append(0x80)
    return bytes(msg)


def l3_vcall_iv(iv: bytes) -> bytes:
    assert len(iv) == 8
    return bytes([0x03]) + iv


def l3_site_info(category: int, system_code: int, site_code: int) -> bytes:
    """Location ID in octets 1-3 (category 2 bits, then system and site code per table 6.5-1)."""
    sys_bits, site_bits = {0: (10, 12), 2: (14, 8), 1: (17, 5)}[category]
    value = (category << 22) | (system_code << site_bits) | site_code
    assert system_code < (1 << sys_bits) and site_code < (1 << site_bits)
    return bytes([0x18]) + value.to_bytes(3, "big") + bytes(14)


def l3_vcall_assgn(src: int, dst: int, channel: int, *, call_type: int = 0, call_timer: int = 2, emergency: bool = False, dup: bool = False) -> bytes:
    cc = 0x80 if emergency else 0
    return bytes(
        [0x05 if dup else 0x04, cc, ((call_type & 7) << 5) | 0x02, src >> 8, src & 0xFF, dst >> 8, dst & 0xFF,
         ((call_timer & 0x3F) << 2) | ((channel >> 8) & 3), channel & 0xFF]
    )


def pad(message: bytes, octets: int) -> bytes:
    return message.ljust(octets, b"\x00")


# --- frames ----------------------------------------------------------------------------------------


def sacch_bits(message72: bytes, ran: int, segment: int) -> np.ndarray:
    """SACCH of one voice frame: SR + one 18-bit quarter of the 72-bit message."""
    assert len(message72) == 9
    quarter = bytes_bits(message72)[18 * segment : 18 * segment + 18]
    structure = {0: 3, 1: 2, 2: 1, 3: 0}[segment]  # 1/4 (head) = 11 ... 4/4 (last) = 00
    return np.concatenate([sr(structure, ran), quarter])


def voice_frame(
    message72: bytes,
    segment: int,
    ran: int,
    *,
    facch1: Sequence[Optional[bytes]] = (None, None),
    rf: int = 1,
    outbound: bool = False,
    rng: Optional[np.random.Generator] = None,
    idle: bool = False,
) -> np.ndarray:
    """One voice frame: LICH, SACCH, then four VCH blocks of 72 bits or FACCH1 (144 bits) over each half.

    `facch1` = (first half, second half) L3 messages (10 octets) or None for plain voice. A random VCH
    stands in for the AMBE+2 bits."""
    rng = rng or np.random.default_rng(0)
    first, second = facch1
    # Steal flag (option bits b2 b1, table 5.2-1): 11 no steal, 10 second half is FACCH1, 01 first half is
    # FACCH1, 00 both. So b2 is 0 exactly when the first half is stolen and b1 is 0 when the second is.
    opt = ((0 if first is not None else 1) << 1) | (0 if second is not None else 1)
    fn = 0b11 if idle else 0b10
    lich = lich_symbols(rf, fn, opt, outbound)
    body = [bits_to_dibits(encode_channel("sacch", sacch_bits(message72, ran, segment)))]
    if idle:
        body.append(np.zeros(BODY_SYMBOLS - 8 - 30, dtype=np.uint8))
    else:
        for half, msg in enumerate((first, second)):
            if msg is None:
                body.append(bits_to_dibits(rng.integers(0, 2, 144).astype(np.uint8)))  # two VCH72 stand-ins
            else:
                body.append(bits_to_dibits(encode_channel("facch1", bytes_bits(pad(msg, 10)))))
    return assemble(lich, np.concatenate(body))


def cac_frame(l3: bytes, ran: int) -> np.ndarray:
    """Outbound CAC frame (control channel): SR + 18-octet layer 3 message, then E and Post fields."""
    info = np.concatenate([sr(0, ran), bytes_bits(pad(l3, 18))])
    cac = bits_to_dibits(encode_channel("cac", info))
    e_field = np.zeros(12, dtype=np.uint8)
    post = np.array([SYMBOL_TO_DIBIT[s] for s in (+3, +3, +3) + POST_TAIL], dtype=np.uint8)
    return assemble(lich_symbols(0, 0, 0, True), np.concatenate([cac, e_field, post]), post)


def long_cac_frame(l3: bytes, ran: int) -> np.ndarray:
    info = np.concatenate([sr(0, ran), bytes_bits(pad(l3, 16))])
    return assemble(lich_symbols(0, 1, 0, False), np.concatenate([bits_to_dibits(encode_channel("long_cac", info)), np.zeros(48, dtype=np.uint8)]))


def short_cac_frame(l3: bytes, ran: int) -> np.ndarray:
    info = np.concatenate([sr(0, ran), bytes_bits(pad(l3, 12))])
    return assemble(lich_symbols(0, 3, 0, False), np.concatenate([bits_to_dibits(encode_channel("short_cac", info)), np.zeros(48, dtype=np.uint8)]))


def udch_frame(l3: bytes, ran: int, *, rf: int = 1, outbound: bool = False, facch2: bool = False) -> np.ndarray:
    info = np.concatenate([sr(0, ran), bytes_bits(pad(l3, 22))])
    body = bits_to_dibits(encode_channel("udch", info))
    return assemble(lich_symbols(rf, 1, 0 if facch2 else 3, outbound), body)


PREAMBLE = np.tile(np.array([SYMBOL_TO_DIBIT[s] for s in (+3, +3, -3, -3)], dtype=np.uint8), 6)  # 24 symbols


@dataclass
class SyntheticNxdnCall:
    src: int = 1234
    dst: int = 100
    call_type: int = 0  # 0 broadcast, 1 conference, 4 individual
    ran: int = 7
    voice_frames: int = 24
    cipher: int = 0
    key_id: int = 0
    emergency: bool = False
    tx_mode: int = 2
    send_iv: bool = False
    rf: int = 1  # 1 = RTCH (trunked), 2 = RDCH (conventional)
    outbound: bool = False
    header_frames: int = 2
    tail_frames: int = 2
    send_tx_rel: bool = True
    seed: int = 1


def voice_transmission(call: SyntheticNxdnCall, *, with_preamble: bool = True) -> np.ndarray:
    """Header frames (FACCH1 VCALL), voice frames (SACCH carries the VCALL superframe), tail (FACCH1 TX_REL)."""
    rng = np.random.default_rng(call.seed)
    vcall = l3_vcall(call.src, call.dst, call_type=call.call_type, cipher=call.cipher, key_id=call.key_id,
                     emergency=call.emergency, tx_mode=call.tx_mode)
    message72 = pad(vcall, 9)
    rel = l3_tx_rel(call.src, call.dst, call_type=call.call_type)
    iv = l3_vcall_iv(bytes(range(1, 9))) if call.send_iv else None
    frames = []
    if with_preamble and call.rf == 2:
        frames.append(PREAMBLE)
    n = 0
    for i in range(call.header_frames):
        frames.append(voice_frame(message72, n % 4, call.ran, facch1=(vcall, iv or vcall), rf=call.rf, outbound=call.outbound, rng=rng))
        n += 1
    for _ in range(call.voice_frames):
        frames.append(voice_frame(message72, n % 4, call.ran, rf=call.rf, outbound=call.outbound, rng=rng))
        n += 1
    for _ in range(call.tail_frames):
        msg = rel if call.send_tx_rel else None
        frames.append(voice_frame(message72, n % 4, call.ran, facch1=(msg, msg), rf=call.rf, outbound=call.outbound, rng=rng))
        n += 1
    return np.concatenate(frames)
