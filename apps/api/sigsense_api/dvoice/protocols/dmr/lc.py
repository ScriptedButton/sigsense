"""DMR full link control: voice/terminator LC bursts, service options, talker alias, GPS.

ETSI TS 102 361-1 clauses 7.1, 9.1.6, B.3.6, B.3.12; ETSI TS 102 361-2 clause 7.1.1.
The scope policy applies: privacy/encryption is REPORTED (service-option privacy bit,
manufacturer feature-set ids, PI headers). Nothing here handles keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from sigsense_api.dvoice.events import CallKind
from sigsense_api.dvoice.fec import bptc
from sigsense_api.dvoice.fec import reed_solomon as rs
from sigsense_api.dvoice.protocols.dmr import constants as C
from sigsense_api.dvoice.protocols.dmr.bits import bits_to_bytes, bytes_to_bits

FLCO_GRP_V_CH_USR = 0b000000
FLCO_UU_V_CH_USR = 0b000011
FLCO_TA_HEADER = 0b000100
FLCO_TA_BLOCK1, FLCO_TA_BLOCK2, FLCO_TA_BLOCK3 = 0b000101, 0b000110, 0b000111
FLCO_GPS_INFO = 0b001000

FLCO_NAMES = {
    FLCO_GRP_V_CH_USR: "Grp_V_Ch_Usr",
    FLCO_UU_V_CH_USR: "UU_V_Ch_Usr",
    FLCO_TA_HEADER: "Talker_Alias_hdr",
    FLCO_TA_BLOCK1: "Talker_Alias_blk1",
    FLCO_TA_BLOCK2: "Talker_Alias_blk2",
    FLCO_TA_BLOCK3: "Talker_Alias_blk3",
    FLCO_GPS_INFO: "GPS_Info",
}


@dataclass(frozen=True)
class FullLc:
    pf: int
    flco: int
    fid: int
    data: bytes  # octets 2..8
    raw: bytes  # all nine octets

    @property
    def name(self) -> str:
        return FLCO_NAMES.get(self.flco, f"FLCO_{self.flco:02X}") if self.fid == 0 else f"MFID_{self.fid:02X}/FLCO_{self.flco:02X}"


def parse_full_lc(lc9: bytes) -> FullLc:
    if len(lc9) != 9:
        raise ValueError("a full LC is 9 octets")
    return FullLc((lc9[0] >> 7) & 1, lc9[0] & 0x3F, lc9[1], bytes(lc9[2:9]), bytes(lc9))


def build_full_lc(flco: int, fid: int, data: bytes, pf: int = 0) -> bytes:
    if len(data) != 7:
        raise ValueError("LC data is 7 octets")
    return bytes([((pf & 1) << 7) | (flco & 0x3F), fid & 0xFF]) + bytes(data)


@dataclass(frozen=True)
class ServiceOptions:
    emergency: bool
    privacy: bool
    broadcast: bool
    ovcm: bool
    priority: int

    @property
    def raw(self) -> int:
        return (
            (int(self.emergency) << 7) | (int(self.privacy) << 6) | (int(self.broadcast) << 3)
            | (int(self.ovcm) << 2) | (self.priority & 3)
        )


def parse_service_options(octet: int) -> ServiceOptions:
    """Table 7.11 (TS 102 361-2)."""
    return ServiceOptions(bool(octet & 0x80), bool(octet & 0x40), bool(octet & 0x08), bool(octet & 0x04), octet & 3)


@dataclass(frozen=True)
class VoiceUser:
    kind: CallKind
    service: ServiceOptions
    target: int
    source: int


def parse_voice_user(lc: FullLc) -> Optional[VoiceUser]:
    """Grp_V_Ch_Usr / UU_V_Ch_Usr (standard feature set only): SO, target, source."""
    if lc.fid != 0 or lc.flco not in (FLCO_GRP_V_CH_USR, FLCO_UU_V_CH_USR):
        return None
    d = lc.data
    kind = CallKind.GROUP if lc.flco == FLCO_GRP_V_CH_USR else CallKind.PRIVATE
    return VoiceUser(kind, parse_service_options(d[0]), int.from_bytes(d[1:4], "big"), int.from_bytes(d[4:7], "big"))


# ---- LC header / terminator bursts: BPTC(196,96) over 72 LC bits + masked RS(12,9) -------

_RS = rs.rs_dmr_12_9_4()


def _mask_bytes(mask24: int) -> bytes:
    return mask24.to_bytes(3, "big")


def decode_lc_burst(info196: np.ndarray, data_type: int) -> Optional[tuple[FullLc, int]]:
    """196 info bits of a Voice LC Header / Terminator with LC -> (LC, corrected) or None.

    BPTC decode, then the data-type mask is removed from the three RS parity octets and
    RS(12,9) must succeed. A BPTC that does not converge is rejected."""
    mask = C.LC_PARITY_MASKS.get(C.DataType(data_type))
    if mask is None:
        raise ValueError("not an LC burst")
    res = bptc.bptc_196_96_decode(info196)
    if not res.ok:
        return None
    octets = bits_to_bytes(res.data)
    parity = bytes(a ^ b for a, b in zip(octets[9:12], _mask_bytes(mask)))
    fixed = _RS.decode(list(octets[:9]) + list(parity))
    if fixed is None:
        return None
    codeword, rs_fixed = fixed
    return parse_full_lc(bytes(int(x) for x in codeword[:9])), res.corrected + int(rs_fixed)


def encode_lc_burst(lc9: bytes, data_type: int) -> np.ndarray:
    mask = C.LC_PARITY_MASKS[C.DataType(data_type)]
    codeword = _RS.encode(list(lc9))
    parity = bytes(int(a) ^ b for a, b in zip(codeword[9:12], _mask_bytes(mask)))
    return bptc.bptc_196_96_encode(bytes_to_bits(bytes(lc9) + parity))


# ---- talker alias (TS 102 361-2 clause 7.1.1.4/5, tables 7.4-7.5, 7.25-7.26) -----------

_TA_FORMATS = {0: "7-bit", 1: "ISO-8859-1", 2: "UTF-8", 3: "UTF-16BE"}


class TalkerAliasAssembler:
    """Collects the header and up to three block LCs of one call."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._format: Optional[int] = None
        self._length = 0
        self._bits = {0: None, 1: None, 2: None, 3: None}

    def add(self, lc: FullLc) -> Optional[str]:
        """Returns the alias once every block it needs has arrived."""
        if lc.fid != 0:
            return None
        bits = bytes_to_bits(lc.data)  # 56 bits
        if lc.flco == FLCO_TA_HEADER:
            self._format = int(bits[0]) << 1 | int(bits[1])
            self._length = int("".join(str(int(b)) for b in bits[2:7]), 2)
            self._bits[0] = bits[7:]  # 49 bits
        elif lc.flco in (FLCO_TA_BLOCK1, FLCO_TA_BLOCK2, FLCO_TA_BLOCK3):
            self._bits[lc.flco - FLCO_TA_BLOCK1 + 1] = bits
        else:
            return None
        return self._try_decode()

    def _needed_blocks(self) -> int:
        if self._format is None:
            return 4
        # Length is characters for 7-bit data and octets for the 8-bit/Unicode formats (TS 102 361-2
        # clause 7.2.19 says "in bytes"; table 7.26 says "in characters": the byte reading is used).
        per_unit = 7 if self._format == 0 else 8
        total = self._length * per_unit
        have = 49 if self._format == 0 else 48  # the header's MSB is reserved for 8/16-bit formats
        extra = max(0, total - have)
        return 1 + (extra + 55) // 56

    def _try_decode(self) -> Optional[str]:
        if self._format is None or self._bits[0] is None:
            return None
        need = self._needed_blocks()
        if need > 4 or any(self._bits[i] is None for i in range(need)):
            return None
        head = self._bits[0] if self._format == 0 else self._bits[0][1:]
        stream = np.concatenate([head] + [self._bits[i] for i in range(1, need)])
        try:
            if self._format == 0:
                n = self._length * 7
                groups = stream[:n].reshape(-1, 7)
                text = "".join(chr(int("".join(str(int(b)) for b in g), 2)) for g in groups)
            else:
                raw = bits_to_bytes(stream[: (len(stream) // 8) * 8])[: self._length]
                text = raw.decode({1: "latin-1", 2: "utf-8", 3: "utf-16-be"}[self._format], errors="replace")
        except ValueError:
            return None
        return text.rstrip("\x00").strip()

    @property
    def format_name(self) -> Optional[str]:
        return _TA_FORMATS.get(self._format) if self._format is not None else None


def encode_talker_alias(text: str, fmt: int = 0) -> list[bytes]:
    """Text -> [header LC data, block LC data...] (7 octets each) for the test encoder."""
    if fmt == 0:
        bits = np.concatenate([np.array([(ord(ch) >> (6 - i)) & 1 for i in range(7)], dtype=np.uint8) for ch in text])
        head_cap = 49
    else:
        raw = text.encode({1: "latin-1", 2: "utf-8", 3: "utf-16-be"}[fmt])
        bits = np.concatenate([np.zeros(1, np.uint8), bytes_to_bits(raw)])  # reserved MSB first
        head_cap = 49
    length = len(text) if fmt == 0 else len(raw)
    stream = np.concatenate([bits, np.zeros(max(0, head_cap + 3 * 56 - bits.size), np.uint8)])
    header = np.concatenate([np.array([(fmt >> 1) & 1, fmt & 1], np.uint8),
                             np.array([(length >> (4 - i)) & 1 for i in range(5)], np.uint8), stream[:49]])
    blocks = [bits_to_bytes(stream[49 + 56 * i : 49 + 56 * (i + 1)]) for i in range(3)]
    return [bits_to_bytes(header)] + blocks


def parse_gps(lc: FullLc) -> Optional[dict]:
    """GPS Info LC (table 7.3): position error, longitude (25 bit), latitude (24 bit), two's complement."""
    if lc.fid != 0 or lc.flco != FLCO_GPS_INFO:
        return None
    value = int.from_bytes(lc.data, "big")  # 56 bits: 4 reserved | 3 error | 25 lon | 24 lat
    lat_raw = value & 0xFFFFFF
    lon_raw = (value >> 24) & 0x1FFFFFF
    err = (value >> 49) & 7
    if lat_raw & 0x800000:
        lat_raw -= 1 << 24
    if lon_raw & 0x1000000:
        lon_raw -= 1 << 25
    return {
        "position_error_code": err,
        "latitude": lat_raw * 180.0 / (1 << 24),
        "longitude": lon_raw * 360.0 / (1 << 25),
    }
