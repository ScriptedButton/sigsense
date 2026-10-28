"""P25 Phase 2 (two-slot TDMA) framing: geometry, ISCH, DUID, scrambling, RS/CRC wrappers. Original code.

PROVENANCE. TIA-102.BBAC is not publicly readable. Every layout constant below (slot geometry, DUID
positions and parity codes, the I-ISCH generator, the SACCH/FACCH/LCCH symbol maps, voice and ESS offsets,
scrambler offsets) was taken as a numeric fact from the open-source SDRTrunk decoder
(io.github.dsheirer.module.decode.p25.phase2.*), which decodes live systems. Two facts were additionally
checked here: the MAC CRC-12 and CRC-16 reproduce SDRTrunk's published per-bit tables exactly (see the
tests), and the I-ISCH code has minimum distance 16. The tests prove the pieces agree with each other, not
that they match a given transmitter; that needs a capture.

GEOMETRY (all counts are bits unless noted; symbols are dibits)
    fragment   = 4 slots = 720 dibits = 120 ms
    slot       = 180 dibits = [ 40-bit ISCH | 320-bit body ]
    slot A, B  : I-ISCH (information) for timeslot 1 and 2
    slot C, D  : S-ISCH = the 20-dibit frame sync word (sync2 = start of C, and of D)
    superframe = 3 fragments = 12 slots (360 ms); ultraframe = 4 superframes
    fragment 0,1,2 is announced by the I-ISCH "ISCH sequence" field; the third ("final") swaps which
    logical channel owns slots C and D.

LOGICAL CHANNEL / SCRAMBLER INDEX
    slot index s (0..11) = 4 * fragment + position (A=0, B=1, C=2, D=3).
    channel of a slot: A -> 0, B -> 1, C -> 0 (1 if final fragment), D -> 1 (0 if final).
    scrambling segment s = sequence[20 + 360 s : 340 + 360 s] (320 bits), applied to the body.
    The eight DUID bits are never scrambled; the decoder reads them before descrambling.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import numpy as np

from sigsense_api.dvoice.fec import crc, reed_solomon
from sigsense_api.dvoice.fec.whitening import p25p2_scramble_sequence

SLOT_DIBITS = 180
ISCH_DIBITS = 20
ISCH_BITS = 40
BODY_BITS = 320
FRAGMENT_DIBITS = 720
SLOTS_PER_FRAGMENT = 4
FRAGMENTS_PER_SUPERFRAME = 3
SLOTS_PER_SUPERFRAME = 12
SYNC_DIBITS = 20
# Dibit offsets (from the fragment start) of the two S-ISCH sync words.
SYNC_OFFSETS = (360, 540)

# Body bit positions of the eight DUID bits (4 data + 4 parity, each pair spread across the body).
DUID_BITS = (0, 1, 74, 75, 244, 245, 318, 319)


class Duid(enum.IntEnum):
    VOICE_4 = 0
    SACCH_S = 3
    VOICE_2 = 6
    FACCH_S = 9
    DATCH_S = 10
    SACCH_U = 12
    LCCH_U = 13
    FACCH_U = 15


SCRAMBLED_DUIDS = frozenset({Duid.VOICE_4, Duid.VOICE_2, Duid.SACCH_S, Duid.FACCH_S, Duid.DATCH_S})

# 4-bit DUID -> 8-bit word with its parity nibble: an extended Hamming (8,4,4) code.
DUID_CODEWORDS = (0x00, 0x17, 0x2E, 0x39, 0x4B, 0x5C, 0x65, 0x72, 0x8D, 0x9A, 0xA3, 0xB4, 0xC6, 0xD1, 0xE8, 0xFF)
_DUID_NP = np.array(DUID_CODEWORDS, dtype=np.uint8)
_POPCOUNT8 = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def read_duid(body_bits: np.ndarray) -> tuple[Optional[int], int]:
    """(duid nibble or None, bit errors). One bit error is corrected; two are detected, not guessed."""
    word = 0
    for p in DUID_BITS:
        word = (word << 1) | int(body_bits[p])
    dist = _POPCOUNT8[_DUID_NP ^ word]
    best = int(dist.argmin())
    if int(dist[best]) <= 1:
        return best, int(dist[best])
    return None, int(dist[best])


def write_duid(body_bits: np.ndarray, duid: int) -> None:
    word = DUID_CODEWORDS[duid]
    for i, p in enumerate(DUID_BITS):
        body_bits[p] = (word >> (7 - i)) & 1


# --------------------------------------------------------------------------------- I-ISCH

# 9 message bits (MSB first) -> 40-bit word: XOR of one generator word per set bit, then a constant.
ISCH_GENERATOR = (0x8816CE36D7, 0x201DFD4F64, 0x100F4B1758, 0x0C00DED18E, 0x020807F7FF,
                  0x09048D9B72, 0x009DA3A171, 0x0058CBAA4E, 0x00343D8597)
ISCH_OFFSET = 0x184229D461


class LchType(enum.IntEnum):
    VCH = 0
    DCH = 1
    RESERVED = 2
    LCCH = 3


@dataclass(frozen=True)
class Isch:
    lch_type: int
    channel: int  # 0 or 1 (timeslot 1 / 2)
    seq: int  # fragment within the superframe, 0..2 (2 = final)
    flag: int  # VCH: inbound SACCH in use; LCCH: dual-slot control; DCH: data 0/1
    superframe: int  # superframe within the ultraframe, 0..3
    errors: int = 0

    @property
    def message(self) -> int:
        return (self.lch_type << 7) | (self.channel << 5) | (self.seq << 3) | (self.flag << 2) | self.superframe

    @property
    def final(self) -> bool:
        return self.seq == 2


def isch_codeword(message9: int) -> int:
    word = ISCH_OFFSET
    for i in range(9):
        if (message9 >> (8 - i)) & 1:
            word ^= ISCH_GENERATOR[i]
    return word


def encode_isch(isch: Isch) -> int:
    return isch_codeword(isch.message)


@lru_cache(maxsize=1)
def _isch_book() -> tuple[np.ndarray, np.ndarray]:
    """Valid I-ISCH words (channel 0/1, sequence 0..2) and their 9-bit messages."""
    msgs = [m for m in range(512) if ((m >> 5) & 3) < 2 and ((m >> 3) & 3) != 3]
    words = np.array([isch_codeword(m) for m in msgs], dtype=np.uint64)
    return words, np.array(msgs, dtype=np.uint16)


def _popcount64(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.uint64)
    out = np.zeros(x.shape, dtype=np.uint8)
    for shift in range(0, 40, 8):
        out += _POPCOUNT8[((x >> np.uint64(shift)) & np.uint64(0xFF)).astype(np.uint8)]
    return out


# The code has minimum distance 16: up to 7 errors are corrected, never more.
ISCH_MAX_ERRORS = 7


def decode_isch(bits40: np.ndarray, channel: int) -> Optional[Isch]:
    """Nearest valid word for the expected logical channel (0 or 1), or None beyond 7 bit errors."""
    word = 0
    for b in np.asarray(bits40, dtype=np.uint8):
        word = (word << 1) | int(b)
    words, msgs = _isch_book()
    keep = ((msgs >> 5) & 3) == channel
    dist = _popcount64(words[keep] ^ np.uint64(word))
    i = int(dist.argmin())
    if int(dist[i]) > ISCH_MAX_ERRORS:
        return None
    m = int(msgs[keep][i])
    return Isch(m >> 7, (m >> 5) & 3, (m >> 3) & 3, (m >> 2) & 1, m & 3, int(dist[i]))


# --------------------------------------------------------------------------------- slot layout

def slot_channel(position: int, final_fragment: bool) -> int:
    """Logical channel (0/1) of slot position A..D (0..3) in a fragment."""
    if position < 2:
        return position
    return (position - 2) ^ 1 if final_fragment else position - 2


def channel_ordinal(fragment_seq: int, position: int) -> int:
    """Position 0..5 of a slot within its channel's share of the superframe (4V,4V,4V,4V,2V,SACCH)."""
    return 2 * fragment_seq + (1 if position >= 2 else 0)


@lru_cache(maxsize=16)
def _segments(wacn: int, system_id: int, nac: int) -> np.ndarray:
    seq = p25p2_scramble_sequence(wacn, system_id, nac, 4320)
    return np.stack([seq[20 + 360 * s : 340 + 360 * s] for s in range(SLOTS_PER_SUPERFRAME)])


def scramble_segment(params: tuple[int, int, int], slot_index: int) -> np.ndarray:
    """The 320 scrambling bits of slot `slot_index` (0..11) for (WACN, system id, NAC)."""
    return _segments(*params)[slot_index]


# Voice and encryption-sync fields of the (descrambled) body.
VOICE_BITS = 72
VOICE4_OFFSETS = (2, 76, 172, 246)
VOICE2_OFFSETS = (2, 76)
ESS_B = (148, 172)
ESS_A = ((148, 244), (246, 318))


def voice_frames(body: np.ndarray, duid: int) -> list[np.ndarray]:
    offs = VOICE4_OFFSETS if duid == Duid.VOICE_4 else VOICE2_OFFSETS
    return [body[o : o + VOICE_BITS] for o in offs]


def ess_b_bits(body: np.ndarray) -> np.ndarray:
    return body[ESS_B[0] : ESS_B[1]]


def ess_a_bits(body: np.ndarray) -> np.ndarray:
    return np.concatenate([body[a:b] for a, b in ESS_A])


# --------------------------------------------------------------------------------- MAC channels

_NON_DUID = [i for i in range(BODY_BITS) if i not in DUID_BITS]  # 312 positions, 52 hex bits
# SACCH and LCCH use every non-DUID bit: 30 information + 22 parity hex bits.
HEX_POSITIONS_SACCH = np.array(_NON_DUID, dtype=np.int64)
# FACCH leaves bits 138..179 out (another field lives there): 26 information + 19 parity hex bits.
HEX_POSITIONS_FACCH = np.array([i for i in _NON_DUID if not 138 <= i < 180], dtype=np.int64)


@dataclass(frozen=True)
class MacChannel:
    name: str
    positions: np.ndarray
    info_hex: int
    parity_hex: int
    info_bits: int  # information bits including the CRC
    crc_name: str
    crc_cover: int  # the CRC protects the first crc_cover bits; the rest of info_bits is the CRC


SACCH = MacChannel("sacch", HEX_POSITIONS_SACCH, 30, 22, 180, "crc12_p25p2", 168)
FACCH = MacChannel("facch", HEX_POSITIONS_FACCH, 26, 19, 156, "crc12_p25p2", 144)
LCCH = MacChannel("lcch", HEX_POSITIONS_SACCH, 30, 22, 180, "crc16_p25_dmr_header", 164)

RS_N, RS_K = 63, 35


@lru_cache(maxsize=1)
def _rs() -> reed_solomon.ReedSolomon:
    # RS(63,35,29) over GF(2^6), x^6+x+1, roots alpha^1..alpha^28; the MAC and ESS words are shortened and
    # punctured pieces of it (index 0 = highest degree, parity last, as fec/reed_solomon documents).
    return reed_solomon.ReedSolomon(6, 0x43, RS_N, RS_K, fcr=1, prim=1)


def _to_hex(bits: np.ndarray) -> list[int]:
    b = np.asarray(bits, dtype=np.uint8).reshape(-1, 6)
    return [int(v) for v in (b * np.array([32, 16, 8, 4, 2, 1], dtype=np.uint8)).sum(axis=1)]


def _from_hex(values) -> np.ndarray:
    return np.array([[(int(v) >> s) & 1 for s in (5, 4, 3, 2, 1, 0)] for v in values], dtype=np.uint8).reshape(-1)


def rs_decode_shortened(info: list[int], parity: list[int], erased_info: tuple[int, ...] = ()) -> Optional[tuple[list[int], int]]:
    """Decode a shortened/punctured piece of RS(63,35): `info` (<= 35 symbols, right-aligned in the message
    part) then `parity` (<= 28 symbols, the rest punctured = erased). Returns (info, symbols corrected)."""
    rs = _rs()
    cw = [0] * RS_N
    pad = RS_K - len(info)
    cw[pad : pad + len(info)] = info
    cw[RS_K : RS_K + len(parity)] = parity
    erasures = [pad + i for i in erased_info] + list(range(RS_K + len(parity), RS_N))
    res = rs.decode(cw, erasures)
    if res is None:
        return None
    word, _ = res
    # count only repaired symbols that were actually received (punctured/erased ones are always "changed")
    received = [pad + i for i in range(len(info)) if i not in erased_info] + list(range(RS_K, RS_K + len(parity)))
    fixed = sum(1 for i in received if int(word[i]) != cw[i])
    return [int(v) for v in word[pad : pad + len(info)]], fixed


def rs_encode_shortened(info: list[int], parity_len: int) -> list[int]:
    pad = RS_K - len(info)
    cw = _rs().encode([0] * pad + list(info))
    return [int(v) for v in cw[RS_K : RS_K + parity_len]]


@dataclass
class MacWord:
    bits: np.ndarray  # information bits incl. CRC (channel.info_bits)
    crc_ok: bool
    corrected: int  # hex-bit symbols the RS decoder repaired


def decode_mac(body: np.ndarray, channel: MacChannel) -> Optional[MacWord]:
    """Descrambled body -> MAC information bits (RS error-corrected, CRC checked), None when RS fails."""
    hexbits = _to_hex(body[channel.positions])
    res = rs_decode_shortened(hexbits[: channel.info_hex], hexbits[channel.info_hex :])
    if res is None:
        return None
    info, fixed = res
    bits = _from_hex(info)
    ok = crc.crc_compute(channel.crc_name, bits[: channel.crc_cover]) == int("".join(map(str, bits[channel.crc_cover : channel.info_bits])), 2)
    return MacWord(bits, ok, fixed)


def encode_mac(info_bits: np.ndarray, channel: MacChannel, body: np.ndarray) -> None:
    """Write `info_bits` (payload without CRC, `crc_cover` long) into `body` for `channel` (CRC and parity added)."""
    payload = np.asarray(info_bits, dtype=np.uint8)
    if payload.size != channel.crc_cover:
        raise ValueError(f"{channel.name} carries {channel.crc_cover} payload bits")
    width = channel.info_bits - channel.crc_cover
    value = crc.crc_compute(channel.crc_name, payload)
    crc_bits = np.array([(value >> (width - 1 - i)) & 1 for i in range(width)], dtype=np.uint8)
    info = _to_hex(np.concatenate([payload, crc_bits]))
    parity = rs_encode_shortened(info, channel.parity_hex)
    body[channel.positions] = _from_hex(info + parity)


# --------------------------------------------------------------------------------- ESS

ESS_B_HEX = 4  # hex bits per 4V burst; four bursts carry the 16 information hex bits
ESS_A_HEX = 28  # the 2V burst carries all the parity


@dataclass(frozen=True)
class Ess:
    algorithm: int
    key_id: int
    mi: bytes  # 9 octets
    corrected: int


def decode_ess(b_parts: list[Optional[np.ndarray]], a_part: Optional[np.ndarray]) -> Optional[Ess]:
    """b_parts: four 24-bit ESS-B pieces (None when missed, order B1..B4); a_part: 168-bit ESS-A."""
    if a_part is None or len(b_parts) != 4 or all(p is None for p in b_parts):
        return None
    info: list[int] = []
    erased: list[int] = []
    for i, part in enumerate(b_parts):
        if part is None:
            erased.extend(range(i * ESS_B_HEX, (i + 1) * ESS_B_HEX))
            info.extend([0] * ESS_B_HEX)
        else:
            info.extend(_to_hex(part))
    res = rs_decode_shortened(info, _to_hex(a_part), tuple(erased))
    if res is None:
        return None
    bits = _from_hex(res[0])
    value = int("".join(map(str, bits)), 2)
    return Ess((value >> 88) & 0xFF, (value >> 72) & 0xFFFF, (value & ((1 << 72) - 1)).to_bytes(9, "big"), res[1])


def encode_ess(algorithm: int, key_id: int, mi: bytes) -> tuple[list[np.ndarray], np.ndarray]:
    """-> (four 24-bit ESS-B pieces, 168-bit ESS-A)."""
    if len(mi) != 9:
        raise ValueError("message indicator is 9 octets")
    value = (algorithm << 88) | (key_id << 72) | int.from_bytes(mi, "big")
    bits = np.array([(value >> (95 - i)) & 1 for i in range(96)], dtype=np.uint8)
    info = _to_hex(bits)
    parity = rs_encode_shortened(info, ESS_A_HEX)
    return [bits[24 * i : 24 * i + 24] for i in range(4)], _from_hex(parity)
