"""M17 link layer parsing: addresses, the LSF, TYPE and META (WP-E). Original code.

Written from the public M17 specification (M17-Project/M17_spec, revision 2.0.0):
  * Address encoding            appendix "Address Encoding" (base-40 alphabet, little-endian digits)
  * LSF layout                  "Link Setup Frame": DST(6) SRC(6) TYPE(2) META(14) CRC(2) = 30 bytes
  * TYPE field                  "Stream Mode / TYPE Field" and "Packet Mode LSF TYPE"
  * META (no encryption)        text blocks, GNSS position, extended callsign data
  * CRC                         polynomial 0x5935, init 0xFFFF, not reflected (fec.crc "crc16_m17")

Only metadata is interpreted here. Encrypted streams are reported, never decoded: the
scrambler and AES types are identified from TYPE and nothing in this package tries to
recover or apply a key (see docs/digital-voice-architecture.md, scope and safety policy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sigsense_api.dvoice.events import EncryptionInfo, EncryptionState
from sigsense_api.dvoice.fec import crc as fec_crc

M17_ALPHABET = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/."

ADDR_RESERVED = 0x000000000000
ADDR_EXTENDED_MIN = 0xEE6B28000000
ADDR_BROADCAST = 0xFFFFFFFFFFFF

# TYPE data types (stream mode)
DATA_RESERVED = 0
DATA_DATA = 1
DATA_VOICE = 2
DATA_VOICE_DATA = 3

# TYPE encryption types
ENC_NONE = 0
ENC_SCRAMBLER = 1
ENC_AES = 2
ENC_RESERVED = 3

# META content kind when encryption type is none (selected by the "encryption subtype" bits)
META_TEXT = 0
META_GNSS = 1
META_EXT_CALLSIGN = 2
META_RESERVED = 3

LSF_BYTES = 30
LSF_BITS = 240
META_BYTES = 14


def decode_address(address: int) -> tuple[str, str]:
    """-> (text, category). Category is reserved | standard | extended | broadcast.

    `text` is the callsign for standard addresses; "" for the others (the specification leaves
    extended/reserved addresses to applications; BROADCAST is reported as such)."""
    if address == ADDR_BROADCAST:
        return "", "broadcast"
    if address == ADDR_RESERVED:
        return "", "reserved"
    if address >= ADDR_EXTENDED_MIN:
        return "", "extended"
    chars = []
    while address:
        chars.append(M17_ALPHABET[address % 40])
        address //= 40
    return "".join(chars), "standard"


def address_label(address: int) -> str:
    """Human label: the callsign, BROADCAST, or a hex form for extended/reserved addresses."""
    text, category = decode_address(address)
    if category == "standard":
        return text
    if category == "broadcast":
        return "BROADCAST"
    return f"0x{address:012X}"


@dataclass(frozen=True)
class M17Type:
    raw: int
    stream: bool  # False = packet mode
    data_type: int  # stream mode: DATA_*; packet mode: reserved (0)
    enc_type: int  # ENC_*
    enc_subtype: int
    can: int  # channel access number 0..15
    signed: bool  # stream carries a digital signature in its last four frames


def parse_type(raw: int) -> M17Type:
    """TYPE (16 bits, big endian): bit 0 P/S, bits 2:1 data type, 4:3 encryption type,
    6:5 encryption subtype, 10:7 CAN (byte 1 bit 7 is the CAN LSB), bit 11 signed stream."""
    stream = bool(raw & 1)
    return M17Type(
        raw=raw & 0xFFFF,
        stream=stream,
        data_type=(raw >> 1) & 3 if stream else 0,
        enc_type=(raw >> 3) & 3 if stream else 0,
        enc_subtype=(raw >> 5) & 3 if stream else 0,
        can=(raw >> 7) & 0xF,
        signed=bool((raw >> 11) & 1) if stream else False,
    )


def build_type(
    *,
    stream: bool,
    data_type: int = DATA_VOICE,
    enc_type: int = ENC_NONE,
    enc_subtype: int = 0,
    can: int = 0,
    signed: bool = False,
) -> int:
    """Inverse of parse_type (used by tests and the synthetic encoder's callers)."""
    raw = (1 if stream else 0) | ((can & 0xF) << 7)
    if stream:
        raw |= (data_type & 3) << 1 | (enc_type & 3) << 3 | (enc_subtype & 3) << 5
        raw |= (1 << 11) if signed else 0
    return raw


_SCRAMBLER_BITS = {0: 8, 1: 16, 2: 24}
_AES_BITS = {0: 128, 1: 192, 2: 256}


def encryption_info(t: M17Type) -> EncryptionInfo:
    """What TYPE says about encryption. Informational only; `audio_decoded` is set by the decoder."""
    if not t.stream or t.enc_type == ENC_NONE:
        return EncryptionInfo(state=EncryptionState.CLEAR)
    if t.enc_type == ENC_SCRAMBLER:
        bits = _SCRAMBLER_BITS.get(t.enc_subtype)
        name = f"M17 scrambler ({bits}-bit LFSR)" if bits else "M17 scrambler (reserved subtype)"
        return EncryptionInfo(state=EncryptionState.ENCRYPTED, algorithm_id=ENC_SCRAMBLER, algorithm_name=name)
    if t.enc_type == ENC_AES:
        bits = _AES_BITS.get(t.enc_subtype)
        name = f"AES-{bits}-CTR" if bits else "AES-CTR (reserved key length)"
        # The 112-bit nonce travels in META: present, but only reported.
        return EncryptionInfo(
            state=EncryptionState.ENCRYPTED, algorithm_id=ENC_AES, algorithm_name=name, mi_present=True
        )
    return EncryptionInfo(state=EncryptionState.UNKNOWN, algorithm_id=ENC_RESERVED, algorithm_name="reserved")


@dataclass(frozen=True)
class Lsf:
    dst: int
    src: int
    type: M17Type
    meta: bytes  # 14 bytes
    raw: bytes  # the 30 bytes including the CRC

    @property
    def dst_label(self) -> str:
        return address_label(self.dst)

    @property
    def src_label(self) -> str:
        return address_label(self.src)


def lsf_crc(first28: bytes) -> int:
    return fec_crc.crc_compute_bytes("crc16_m17", first28)


def parse_lsf(raw: bytes) -> Optional[Lsf]:
    """30 bytes -> Lsf, or None when the CRC does not verify (never guess at a damaged LSF)."""
    if len(raw) != LSF_BYTES:
        return None
    if lsf_crc(raw[:28]) != int.from_bytes(raw[28:30], "big"):
        return None
    return Lsf(
        dst=int.from_bytes(raw[0:6], "big"),
        src=int.from_bytes(raw[6:12], "big"),
        type=parse_type(int.from_bytes(raw[12:14], "big")),
        meta=bytes(raw[14:28]),
        raw=bytes(raw),
    )


# --- META interpretation (no encryption) ---------------------------------------------------------


@dataclass
class TextAssembler:
    """Collects the up-to-four 13-byte text blocks of one message (control byte semantics per spec:
    high nibble = bitmap of blocks in the message, low nibble = which block this is; control 0 = none)."""

    blocks: dict[int, bytes] = field(default_factory=dict)
    expected: int = 0  # bitmap of blocks the message needs (OR of the high nibbles)
    seen: int = 0

    def reset(self) -> None:
        self.blocks.clear()
        self.expected = 0
        self.seen = 0

    def push(self, meta: bytes) -> Optional[str]:
        """Feed one META field; returns the complete message once every block has arrived."""
        control = meta[0]
        if control == 0:
            return None
        total, which = control >> 4, control & 0xF
        if which not in (1, 2, 4, 8) or total not in (1, 3, 7, 15):
            return None
        index = {1: 0, 2: 1, 4: 2, 8: 3}[which]
        if total != self.expected and self.expected:
            self.reset()  # a different message started
        self.expected = total
        self.seen |= which
        self.blocks[index] = bytes(meta[1:14])
        if self.seen & total == total:
            text = b"".join(self.blocks[i] for i in sorted(self.blocks) if (1 << i) & total)
            return text.decode("utf-8", errors="replace").rstrip(" \x00")
        return None


_STATION_TYPES = {0: "fixed", 1: "mobile", 2: "handheld"}
_DATA_SOURCES = {0: "M17 client", 1: "OpenRTX", 15: "other"}


def _signed(value: int, bits: int) -> int:
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def parse_gnss(meta: bytes) -> Optional[dict]:
    """GNSS position META (revision 2.0.0 layout). Fields are only reported when the validity
    flags say they are valid. Returns None when the field is empty or the position is invalid."""
    if len(meta) != META_BYTES or not any(meta):
        return None
    validity = meta[1] >> 4
    out: dict = {
        "source": _DATA_SOURCES.get(meta[0] >> 4, f"reserved({meta[0] >> 4})"),
        "station_type": _STATION_TYPES.get(meta[0] & 0xF, f"reserved({meta[0] & 0xF})"),
        "radius_m": 1 << ((meta[1] >> 1) & 7),
    }
    if validity & 0b1000:
        lat = _signed(int.from_bytes(meta[3:6], "big"), 24)
        lon = _signed(int.from_bytes(meta[6:9], "big"), 24)
        out["latitude"] = round(lat * 90.0 / 8388607, 6)
        out["longitude"] = round(lon * 180.0 / 8388607, 6)
    else:
        return None
    if validity & 0b0100:
        out["altitude_m"] = int.from_bytes(meta[9:11], "big") * 0.5 - 500.0
    if validity & 0b0010:
        out["bearing_deg"] = ((meta[1] & 1) << 8) | meta[2]
        out["speed_kmh"] = (((meta[11] << 4) | (meta[12] >> 4)) & 0xFFF) * 0.5
    return out


def parse_extended_callsigns(meta: bytes) -> list[str]:
    """Extended callsign META: up to two 6-byte callsign fields (first always present)."""
    out = []
    for offset in (0, 6):
        value = int.from_bytes(meta[offset : offset + 6], "big")
        if value == 0:
            continue
        text, category = decode_address(value)
        out.append(text if category == "standard" else address_label(value))
    return out


def meta_kind(t: M17Type) -> Optional[int]:
    """Which META interpretation applies (only when the stream is unencrypted)."""
    if not t.stream or t.enc_type != ENC_NONE:
        return None
    return t.enc_subtype
