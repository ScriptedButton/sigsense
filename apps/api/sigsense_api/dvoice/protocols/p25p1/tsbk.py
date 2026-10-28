"""P25 trunking signalling blocks (TSBK) and data packet headers. Original code.

A TSBK is 96 bits inside one trellis-coded rate-1/2 block (BAAA-A section 7):

    LB(1) P(1) opcode(6) MFID(8) arguments(64) CRC-16(16)

The CRC is the header CRC of BAAA-A 6.2 (CRC-CCITT plus inversion), checked by
the caller via fec.crc 'crc16_p25_dmr_header'.

SOURCE NOTE: the opcode formats below follow TIA-102.AABC as commonly published.
That document is not part of the BAAA-A copy this module was written from, so
the argument layouts are NOT verified against a primary text here; every layout
sums to the 64 argument bits, and the encoder in testing/p25p1_encoder.py shares
them, so the tests prove round-trip consistency but not conformance. Opcodes whose
layout is not confidently known are returned as raw hex under `fields["raw_args"]`
(name "unparsed").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

ARGS = 16  # first argument bit within the 96-bit block
STANDARD_MFIDS = (0x00, 0x01)

OP_GRP_V_CH_GRANT = 0x00
OP_GRP_V_CH_GRANT_UPDT = 0x02
OP_GRP_V_CH_GRANT_UPDT_EXP = 0x03
OP_UU_V_CH_GRANT = 0x04
OP_UU_V_CH_GRANT_UPDT = 0x06
OP_IDEN_UP_VU = 0x34
OP_RFSS_STS_BCST = 0x3A
OP_NET_STS_BCST = 0x3B
OP_ADJ_STS_BCST = 0x3C
OP_IDEN_UP = 0x3D

OPCODE_NAMES = {
    OP_GRP_V_CH_GRANT: "GRP_V_CH_GRANT",
    OP_GRP_V_CH_GRANT_UPDT: "GRP_V_CH_GRANT_UPDT",
    OP_GRP_V_CH_GRANT_UPDT_EXP: "GRP_V_CH_GRANT_UPDT_EXP",
    OP_UU_V_CH_GRANT: "UU_V_CH_GRANT",
    OP_UU_V_CH_GRANT_UPDT: "UU_V_CH_GRANT_UPDT",
    OP_IDEN_UP_VU: "IDEN_UP_VU",
    OP_RFSS_STS_BCST: "RFSS_STS_BCST",
    OP_NET_STS_BCST: "NET_STS_BCST",
    OP_ADJ_STS_BCST: "ADJ_STS_BCST",
    OP_IDEN_UP: "IDEN_UP",
}


def _int(bits: np.ndarray, start: int, n: int) -> int:
    v = 0
    for x in np.asarray(bits[start : start + n]).tolist():
        v = (v << 1) | x
    return v


@dataclass
class Tsbk:
    opcode: int
    mfid: int
    last_block: bool
    protected: bool
    name: str
    standard: bool
    fields: dict[str, Any] = field(default_factory=dict)
    raw: str = ""  # 12 octets, hex


def _split_channel(channel: int) -> dict[str, int]:
    return {"channel": channel, "iden": channel >> 12, "number": channel & 0xFFF}


def parse_tsbk(bits96: np.ndarray) -> Tsbk:
    b = np.asarray(bits96, dtype=np.uint8)
    first = _int(b, 0, 8)
    opcode = first & 0x3F
    mfid = _int(b, 8, 8)
    raw = bytes(_int(b, 8 * i, 8) for i in range(12)).hex()
    t = Tsbk(
        opcode=opcode,
        mfid=mfid,
        last_block=bool(first & 0x80),
        protected=bool(first & 0x40),
        name="unparsed",
        standard=mfid in STANDARD_MFIDS,
        raw=raw,
    )
    a = ARGS
    f: dict[str, Any] = {}
    if not t.standard:
        f["raw_args"] = raw[4:20]
    elif opcode == OP_GRP_V_CH_GRANT:
        f = {"service_options": _int(b, a, 8), **_split_channel(_int(b, a + 8, 16)),
             "group": _int(b, a + 24, 16), "source": _int(b, a + 40, 24)}
    elif opcode == OP_GRP_V_CH_GRANT_UPDT:
        f = {"updates": [
            {**_split_channel(_int(b, a, 16)), "group": _int(b, a + 16, 16)},
            {**_split_channel(_int(b, a + 32, 16)), "group": _int(b, a + 48, 16)},
        ]}
    elif opcode == OP_GRP_V_CH_GRANT_UPDT_EXP:
        f = {"service_options": _int(b, a, 8), "tx": _split_channel(_int(b, a + 16, 16)),
             "rx": _split_channel(_int(b, a + 32, 16)), "group": _int(b, a + 48, 16)}
        f.update(_split_channel(f["tx"]["channel"]))
    elif opcode in (OP_UU_V_CH_GRANT, OP_UU_V_CH_GRANT_UPDT):
        f = {**_split_channel(_int(b, a, 16)), "target": _int(b, a + 16, 24), "source": _int(b, a + 40, 24)}
    elif opcode == OP_RFSS_STS_BCST:
        f = {"lra": _int(b, a, 8), "system_id": _int(b, a + 12, 12), "rfss_id": _int(b, a + 24, 8),
             "site_id": _int(b, a + 32, 8), **_split_channel(_int(b, a + 40, 16)), "service_class": _int(b, a + 56, 8)}
    elif opcode == OP_NET_STS_BCST:
        f = {"lra": _int(b, a, 8), "wacn": _int(b, a + 8, 20), "system_id": _int(b, a + 28, 12),
             **_split_channel(_int(b, a + 40, 16)), "service_class": _int(b, a + 56, 8)}
    elif opcode == OP_ADJ_STS_BCST:
        f = {"lra": _int(b, a, 8), "cfva": _int(b, a + 8, 4), "system_id": _int(b, a + 12, 12),
             "rfss_id": _int(b, a + 24, 8), "site_id": _int(b, a + 32, 8),
             **_split_channel(_int(b, a + 40, 16)), "service_class": _int(b, a + 56, 8)}
    elif opcode == OP_IDEN_UP:
        toff = _int(b, a + 13, 9)
        f = {"iden": _int(b, a, 4), "bandwidth_hz": _int(b, a + 4, 9) * 125,
             "tx_offset_hz": (toff & 0xFF) * 250_000 * (1 if toff & 0x100 else -1),
             "spacing_hz": _int(b, a + 22, 10) * 125, "base_hz": _int(b, a + 32, 32) * 5}
    elif opcode == OP_IDEN_UP_VU:
        f = {"iden": _int(b, a, 4), "bandwidth_code": _int(b, a + 4, 4), "tx_offset_raw": _int(b, a + 8, 14),
             "spacing_hz": _int(b, a + 22, 10) * 125, "base_hz": _int(b, a + 32, 32) * 5}
    else:
        f["raw_args"] = raw[4:20]
    if opcode in OPCODE_NAMES and t.standard:
        t.name = OPCODE_NAMES[opcode]
    t.fields = f
    return t


def build_tsbk_bits(opcode: int, args64: int, *, mfid: int = 0, last_block: bool = True, protected: bool = False) -> np.ndarray:
    """First 80 bits of a TSBK (the CRC is appended by the caller)."""
    head = ((0x80 if last_block else 0) | (0x40 if protected else 0) | (opcode & 0x3F))
    bits = [(head >> (7 - k)) & 1 for k in range(8)] + [(mfid >> (7 - k)) & 1 for k in range(8)]
    bits += [(args64 >> (63 - k)) & 1 for k in range(64)]
    return np.array(bits, dtype=np.uint8)


# --- packet data header (BAAA-A 6.2 - 6.6) ---------------------------------------

FORMAT_RESPONSE = 0b00011
FORMAT_UNCONFIRMED = 0b10101
FORMAT_CONFIRMED = 0b10110


@dataclass
class PduHeader:
    format: int
    confirmed_requested: bool  # A/N bit
    outbound: bool  # I/O bit
    sap: int
    mfid: int
    llid: int
    blocks_to_follow: int
    pad_octets: int
    kind: str  # "confirmed" | "unconfirmed" | "response" | "other"

    @property
    def block_octets(self) -> int:
        """User octets per data block (12 unconfirmed, 16 confirmed)."""
        return 16 if self.kind == "confirmed" else 12


def parse_pdu_header(bits96: np.ndarray) -> PduHeader:
    b = np.asarray(bits96, dtype=np.uint8)
    fmt = _int(b, 3, 5)
    kind = {FORMAT_CONFIRMED: "confirmed", FORMAT_UNCONFIRMED: "unconfirmed", FORMAT_RESPONSE: "response"}.get(fmt, "other")
    return PduHeader(
        format=fmt,
        confirmed_requested=bool(b[1]),
        outbound=bool(b[2]),
        sap=_int(b, 10, 6),
        mfid=_int(b, 16, 8),
        llid=_int(b, 24, 24),
        blocks_to_follow=_int(b, 49, 7),
        pad_octets=_int(b, 59, 5),
        kind=kind,
    )
