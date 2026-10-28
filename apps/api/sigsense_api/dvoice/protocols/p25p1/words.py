"""P25 Phase 1 embedded words: Link Control, Encryption Sync, Header, LSD (TIA-102.BAAA-A 5.2-5.6). Original code.

All words are Reed-Solomon codes over GF(2^6) (6-bit hex bits, systematic: the
information hex bits come first), then wrapped in an inner code:

  LC   72 bits -> 12 hex bits -> RS(24,12,13) -> 24 x Hamming(10,6,3)  = 240 bits (inside an LDU1)
  ES   96 bits -> 16 hex bits -> RS(24,16,9)  -> 24 x Hamming(10,6,3)  = 240 bits (inside an LDU2)
  HDR 120 bits -> 20 hex bits -> RS(36,20,17) -> 36 x Golay(18,6,8)   = 648 bits (HDU)
  TLC  LC as above, RS(24,12,13) then 12 x Golay(24,12,8)             = 288 bits (TDULC)
  LSD  8 bits -> (16,8,5) shortened cyclic code, g(x) = x^8+x^5+x^4+x^3+1

Encryption is only ever REPORTED here (ALGID/KID/MI); nothing in this package
handles keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Optional

import numpy as np

from sigsense_api.dvoice.fec import golay, hamming, reed_solomon

# --- helpers -----------------------------------------------------------------


def _bits_to_hex(bits: np.ndarray) -> list[int]:
    b = np.asarray(bits, dtype=np.uint8).reshape(-1, 6)
    out = []
    for row in b.tolist():
        v = 0
        for x in row:
            v = (v << 1) | x
        out.append(v)
    return out


def _hex_to_bits(hexbits: list[int]) -> np.ndarray:
    return np.array([(h >> (5 - k)) & 1 for h in hexbits for k in range(6)], dtype=np.uint8)


def _int(bits: np.ndarray, start: int, n: int) -> int:
    v = 0
    for x in np.asarray(bits[start : start + n]).tolist():
        v = (v << 1) | x
    return v


def _int_bits(value: int, width: int) -> list[int]:
    return [(value >> (width - 1 - k)) & 1 for k in range(width)]


@dataclass
class WordResult:
    data: np.ndarray  # information bits (72 / 96 / 120), uint8
    ok: bool
    corrected: int  # inner-code bit corrections + RS symbol corrections
    erasures: int = 0  # inner words that failed and were handed to the RS code as erasures


def _rs_decode(hexbits: list[int], rs: reed_solomon.ReedSolomon, k: int, erasures: list[int], inner_fixed: int) -> WordResult:
    # Beyond the RS capability erasures cannot help; let the decoder decide.
    res = None
    try:
        res = rs.decode(hexbits, erasures=erasures or None)
    except Exception:  # defensive: malformed input must never raise out of a decoder
        res = None
    if res is None:
        return WordResult(np.zeros(6 * k, dtype=np.uint8), False, inner_fixed, len(erasures))
    cw, nerr = res
    return WordResult(_hex_to_bits([int(x) for x in cw[:k]]), True, inner_fixed + int(nerr), len(erasures))


# --- (10,6,3) Hamming-protected words: LC (LDU1) and ES (LDU2) ---------------


def _decode_hamming_words(bits240: np.ndarray) -> tuple[list[int], list[int], int]:
    hexbits: list[int] = []
    erasures: list[int] = []
    fixed = 0
    b = np.asarray(bits240, dtype=np.uint8).reshape(24, 10)
    for i, row in enumerate(b.tolist()):
        w = 0
        for x in row:
            w = (w << 1) | x
        r = hamming.hamming_10_6_3_decode(w)
        if r is None:
            hexbits.append(0)
            erasures.append(i)
        else:
            hexbits.append(r[0])
            fixed += r[1]
    return hexbits, erasures, fixed


def decode_lc_hamming(bits240: np.ndarray) -> WordResult:
    hexbits, erasures, fixed = _decode_hamming_words(bits240)
    return _rs_decode(hexbits, reed_solomon.rs_p25_24_12_13(), 12, erasures, fixed)


def decode_es(bits240: np.ndarray) -> WordResult:
    hexbits, erasures, fixed = _decode_hamming_words(bits240)
    return _rs_decode(hexbits, reed_solomon.rs_p25_24_16_9(), 16, erasures, fixed)


def _encode_hamming_words(hexbits: list[int]) -> np.ndarray:
    out: list[int] = []
    for h in hexbits:
        out.extend(_int_bits(hamming.hamming_10_6_3_encode(h), 10))
    return np.array(out, dtype=np.uint8)


def encode_lc_hamming(bits72: np.ndarray) -> np.ndarray:
    cw = reed_solomon.rs_p25_24_12_13().encode(_bits_to_hex(bits72))
    return _encode_hamming_words([int(x) for x in cw])


def encode_es(bits96: np.ndarray) -> np.ndarray:
    cw = reed_solomon.rs_p25_24_16_9().encode(_bits_to_hex(bits96))
    return _encode_hamming_words([int(x) for x in cw])


# --- header word (HDU) -------------------------------------------------------


def decode_header(bits648: np.ndarray) -> WordResult:
    b = np.asarray(bits648, dtype=np.uint8).reshape(36, 18)
    hexbits: list[int] = []
    erasures: list[int] = []
    fixed = 0
    for i, row in enumerate(b.tolist()):
        w = 0
        for x in row:
            w = (w << 1) | x
        r = golay.golay_18_6_decode(w)
        if r is None:
            hexbits.append(0)
            erasures.append(i)
        else:
            hexbits.append(r[0])
            fixed += r[1]
    return _rs_decode(hexbits, reed_solomon.rs_p25_36_20_17(), 20, erasures, fixed)


def encode_header(bits120: np.ndarray) -> np.ndarray:
    cw = reed_solomon.rs_p25_36_20_17().encode(_bits_to_hex(bits120))
    out: list[int] = []
    for h in cw:
        out.extend(_int_bits(golay.golay_18_6_encode(int(h)), 18))
    return np.array(out, dtype=np.uint8)


# --- terminator link control (TDULC): Golay(24,12) pairs ---------------------


def decode_lc_golay(bits288: np.ndarray) -> WordResult:
    b = np.asarray(bits288, dtype=np.uint8).reshape(12, 24)
    hexbits: list[int] = []
    erasures: list[int] = []
    fixed = 0
    for i, row in enumerate(b.tolist()):
        w = 0
        for x in row:
            w = (w << 1) | x
        r = golay.golay_24_12_decode(w)
        if r is None:
            hexbits.extend([0, 0])
            erasures.extend([2 * i, 2 * i + 1])
        else:
            hexbits.extend([(r[0] >> 6) & 0x3F, r[0] & 0x3F])
            fixed += r[1]
    return _rs_decode(hexbits, reed_solomon.rs_p25_24_12_13(), 12, erasures, fixed)


def encode_lc_golay(bits72: np.ndarray) -> np.ndarray:
    cw = [int(x) for x in reed_solomon.rs_p25_24_12_13().encode(_bits_to_hex(bits72))]
    out: list[int] = []
    for i in range(0, 24, 2):
        out.extend(_int_bits(golay.golay_24_12_encode((cw[i] << 6) | cw[i + 1]), 24))
    return np.array(out, dtype=np.uint8)


# --- low speed data: (16,8,5) shortened cyclic code --------------------------

_LSD_GEN = 0b100111001  # x^8 + x^5 + x^4 + x^3 + 1


def _lsd_parity(data8: int) -> int:
    reg = data8 << 8
    for bit in range(15, 7, -1):
        if reg & (1 << bit):
            reg ^= _LSD_GEN << (bit - 8)
    return reg & 0xFF


def lsd_encode(data8: int) -> int:
    return ((data8 & 0xFF) << 8) | _lsd_parity(data8 & 0xFF)


def _build_lsd_table() -> dict[int, int]:
    table: dict[int, int] = {0: 0}
    for weight in (1, 2):
        for positions in combinations(range(16), weight):
            err = 0
            for p in positions:
                err |= 1 << p
            syn = _lsd_parity(err >> 8) ^ (err & 0xFF)
            table.setdefault(syn, err)
    return table


_LSD_TABLE = _build_lsd_table()


def lsd_decode(word16: int) -> Optional[tuple[int, int]]:
    """-> (data octet, corrected bits) or None. Corrects up to 2 errors (distance 5)."""
    syn = _lsd_parity((word16 >> 8) & 0xFF) ^ (word16 & 0xFF)
    err = _LSD_TABLE.get(syn)
    if err is None:
        return None
    fixed = bin(err).count("1")
    return ((word16 ^ err) >> 8) & 0xFF, fixed


# --- field parsing -----------------------------------------------------------

ALGID_CLEAR = 0x80
_ALGORITHM_NAMES = {
    0x80: "Unencrypted",
    0x81: "DES-OFB",
    0x84: "AES-256",
    0xAA: "ADP",
}

MFID_STANDARD = (0x00, 0x01)

LCO_GROUP_VOICE = 0x00
LCO_UNIT_VOICE = 0x03
LCO_CALL_TERMINATION = 0x0F


def algorithm_name(algid: int) -> str:
    return _ALGORITHM_NAMES.get(algid, f"algorithm 0x{algid:02X}")


@dataclass
class ServiceOptions:
    raw: int
    emergency: bool
    protected: bool  # encrypted call as flagged by the system
    duplex: bool
    packet_mode: bool
    priority: int

    @staticmethod
    def parse(octet: int) -> "ServiceOptions":
        return ServiceOptions(
            raw=octet & 0xFF,
            emergency=bool(octet & 0x80),
            protected=bool(octet & 0x40),
            duplex=bool(octet & 0x20),
            packet_mode=bool(octet & 0x10),
            priority=octet & 0x07,
        )


@dataclass
class LinkControl:
    lco: int
    mfid: int
    protected_lc: bool  # P bit: the LC info is itself encrypted (fields are then meaningless)
    standard: bool
    kind: str  # "group" | "private" | "termination" | "other"
    service: Optional[ServiceOptions] = None
    target_id: Optional[int] = None  # talkgroup or destination unit
    source_id: Optional[int] = None
    raw: str = ""  # 9 octets, hex
    fields: dict[str, Any] = field(default_factory=dict)


def parse_lc(bits72: np.ndarray) -> LinkControl:
    """72-bit LC -> LinkControl. Only formats confirmed by the supplied spec are decoded
    (group voice 0x00, unit-to-unit 0x03, termination 0x0F); others keep their raw octets."""
    b = np.asarray(bits72, dtype=np.uint8)
    first = _int(b, 0, 8)
    lco = first & 0x3F
    protected = bool(first & 0x80)
    mfid = _int(b, 8, 8)
    raw = bytes(_int(b, 8 * i, 8) for i in range(9)).hex()
    lc = LinkControl(lco=lco, mfid=mfid, protected_lc=protected, standard=mfid in MFID_STANDARD, kind="other", raw=raw)
    if protected or not lc.standard:
        return lc
    if lco == LCO_GROUP_VOICE:
        lc.kind = "group"
        lc.service = ServiceOptions.parse(_int(b, 16, 8))
        lc.target_id = _int(b, 32, 16)
        lc.source_id = _int(b, 48, 24)
    elif lco == LCO_UNIT_VOICE:
        lc.kind = "private"
        lc.service = ServiceOptions.parse(_int(b, 16, 8))
        lc.target_id = _int(b, 24, 24)
        lc.source_id = _int(b, 48, 24)
    elif lco == LCO_CALL_TERMINATION:
        lc.kind = "termination"
    return lc


def build_lc(
    *,
    lco: int,
    mfid: int = 0,
    service_options: int = 0,
    target_id: int = 0,
    source_id: int = 0,
    protected: bool = False,
) -> np.ndarray:
    """Test/encoder helper: fields -> 72 LC bits (formats parsed by `parse_lc`)."""
    bits: list[int] = []
    bits += _int_bits(((0x80 if protected else 0) | (lco & 0x3F)), 8)
    bits += _int_bits(mfid, 8)
    if lco == LCO_GROUP_VOICE:
        bits += _int_bits(service_options, 8) + _int_bits(0, 8) + _int_bits(target_id, 16) + _int_bits(source_id, 24)
    elif lco == LCO_UNIT_VOICE:
        bits += _int_bits(service_options, 8) + _int_bits(target_id, 24) + _int_bits(source_id, 24)
    else:
        bits += [0] * 56
    return np.array(bits, dtype=np.uint8)


@dataclass
class EncryptionSync:
    mi: bytes  # 9 octets (72 bits)
    algid: int
    kid: int

    @property
    def clear(self) -> bool:
        return self.algid == ALGID_CLEAR


def parse_es(bits96: np.ndarray) -> EncryptionSync:
    b = np.asarray(bits96, dtype=np.uint8)
    return EncryptionSync(
        mi=bytes(_int(b, 8 * i, 8) for i in range(9)), algid=_int(b, 72, 8), kid=_int(b, 80, 16)
    )


def build_es(*, mi: bytes = bytes(9), algid: int = ALGID_CLEAR, kid: int = 0) -> np.ndarray:
    bits: list[int] = []
    for o in mi:
        bits += _int_bits(o, 8)
    return np.array(bits + _int_bits(algid, 8) + _int_bits(kid, 16), dtype=np.uint8)


@dataclass
class HeaderWord:
    mi: bytes
    mfid: int
    algid: int
    kid: int
    tgid: int

    @property
    def clear(self) -> bool:
        return self.algid == ALGID_CLEAR


def parse_header(bits120: np.ndarray) -> HeaderWord:
    b = np.asarray(bits120, dtype=np.uint8)
    return HeaderWord(
        mi=bytes(_int(b, 8 * i, 8) for i in range(9)),
        mfid=_int(b, 72, 8),
        algid=_int(b, 80, 8),
        kid=_int(b, 88, 16),
        tgid=_int(b, 104, 16),
    )


def build_header(*, mi: bytes = bytes(9), mfid: int = 0, algid: int = ALGID_CLEAR, kid: int = 0, tgid: int = 0) -> np.ndarray:
    bits: list[int] = []
    for o in mi:
        bits += _int_bits(o, 8)
    return np.array(bits + _int_bits(mfid, 8) + _int_bits(algid, 8) + _int_bits(kid, 16) + _int_bits(tgid, 16), dtype=np.uint8)
