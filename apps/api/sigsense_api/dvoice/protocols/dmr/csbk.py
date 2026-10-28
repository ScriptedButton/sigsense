"""DMR control signalling blocks (CSBK) and multi-block control.

Layouts: ETSI TS 102 361-1 clauses 7.2, 7.4, 9.1.8; TS 102 361-2 clause 7.1.2 (Tier II);
TS 102 361-4 clause 7.1.1 (Tier III channel grants, C_ALOHA, C_BCAST). Opcodes whose
layout is not in those texts are reported by number and left undecoded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from sigsense_api.dvoice.fec import bptc, crc
from sigsense_api.dvoice.protocols.dmr import constants as C
from sigsense_api.dvoice.protocols.dmr.bits import bits_to_bytes, bytes_to_bits

CSBKO_NAMES = {
    0b000100: "UU_V_Req",
    0b000101: "UU_Ans_Rsp",
    0b000111: "CT_CSBK",
    0b100110: "NACK_Rsp",
    0b111000: "BS_Dwn_Act",
    0b111101: "Pre_CSBK",
    # Tier III (TS 102 361-4)
    0b011001: "C_ALOHA",
    0b011100: "C_AHOY",
    0b101000: "C_BCAST",
    0b110000: "PV_GRANT",
    0b110001: "TV_GRANT",
    0b110010: "BTV_GRANT",
    0b110011: "PD_GRANT",
    0b110100: "TD_GRANT",
    0b110101: "PV_GRANT_DX",
    0b110110: "PD_GRANT_DX",
    0b111001: "MOVE_C",  # Move TSCC (table 7.17)
    0b100000: "TS_ACK_RSP",  # table 7.23
}


@dataclass(frozen=True)
class Csbk:
    lb: int
    pf: int
    csbko: int
    fid: int
    data: bytes  # octets 2..9 (8 bytes)

    @property
    def name(self) -> str:
        base = CSBKO_NAMES.get(self.csbko, f"CSBKO_{self.csbko:02X}")
        return base if self.fid == 0 else f"MFID_{self.fid:02X}/{base}"

    def to_bytes(self) -> bytes:
        return bytes([((self.lb & 1) << 7) | ((self.pf & 1) << 6) | (self.csbko & 0x3F), self.fid & 0xFF]) + self.data


def parse_csbk(octets10: bytes) -> Csbk:
    if len(octets10) != 10:
        raise ValueError("a CSBK is 10 octets before its CRC")
    return Csbk((octets10[0] >> 7) & 1, (octets10[0] >> 6) & 1, octets10[0] & 0x3F, octets10[1], bytes(octets10[2:10]))


def _crc_ok_masked(octets12: bytes, mask: Optional[int]) -> bool:
    got = int.from_bytes(octets12[10:12], "big") ^ (mask or 0)
    return crc.crc_compute("crc16_p25_dmr_header", bytes_to_bits(octets12[:10])) == got


def decode_block(info196: np.ndarray, data_type: int) -> Optional[tuple[bytes, int]]:
    """BPTC(196,96) -> 12 octets after verifying the header CRC with the data-type mask.

    Used for CSBK, MBC header, data header, PI header, unified single block."""
    mask = C.CRC16_MASKS.get(C.DataType(data_type))
    res = bptc.bptc_196_96_decode(info196)
    if not res.ok:
        return None
    octets = bits_to_bytes(res.data)
    if not _crc_ok_masked(octets, mask):
        return None
    return octets, res.corrected


def encode_block(octets10: bytes, data_type: int) -> np.ndarray:
    mask = C.CRC16_MASKS[C.DataType(data_type)]
    value = crc.crc_compute("crc16_p25_dmr_header", bytes_to_bits(octets10)) ^ mask
    return bptc.bptc_196_96_encode(bytes_to_bits(bytes(octets10) + value.to_bytes(2, "big")))


def decode_csbk_block(info196: np.ndarray) -> Optional[tuple[Csbk, int]]:
    out = decode_block(info196, C.DataType.CSBK)
    return None if out is None else (parse_csbk(out[0][:10]), out[1])


def encode_csbk_block(csbk: Csbk) -> np.ndarray:
    return encode_block(csbk.to_bytes(), C.DataType.CSBK)


# ---- decoded views --------------------------------------------------------------------


@dataclass
class CsbkInfo:
    name: str
    fields: dict[str, Any] = field(default_factory=dict)
    # Tier III classification used by the decoder to emit system/trunk events
    system: Optional[dict[str, Any]] = None
    grant: Optional[dict[str, Any]] = None


def _u24(b: bytes) -> int:
    return int.from_bytes(b, "big")


def syscode_fields(code16: int) -> dict[str, int]:
    """C_SYScode: MODEL(2) | NET | SITE | PAR(2), NET+SITE = 12 bits (TS 102 361-4 table 6.5)."""
    model = (code16 >> 14) & 3
    net_bits, site_bits = {0: (9, 3), 1: (7, 5), 2: (4, 8), 3: (2, 10)}[model]
    body = (code16 >> 2) & 0xFFF
    return {
        "model": model,
        "net": body >> site_bits,
        "site": body & ((1 << site_bits) - 1),
        "par": code16 & 3,
        "net_bits": net_bits,
        "site_bits": site_bits,
    }


def describe_csbk(c: Csbk) -> CsbkInfo:
    info = CsbkInfo(c.name, {"csbko": c.csbko, "fid": c.fid, "lb": c.lb})
    if c.fid != 0:
        info.fields["manufacturer_specific"] = True
        return info
    d = c.data
    op = c.csbko
    if op == 0b000100:  # UU_V_Req: SO, reserved, target, source
        info.fields.update(service_options=d[0], target=_u24(d[2:5]), source=_u24(d[5:8]))
    elif op == 0b000101:  # UU_Ans_Rsp: SO, answer, target, source
        info.fields.update(service_options=d[0], answer=d[1], target=_u24(d[2:5]), source=_u24(d[5:8]))
    elif op == 0b111000:  # BS_Dwn_Act: reserved16, BS address, source
        info.fields.update(bs_address=_u24(d[2:5]), source=_u24(d[5:8]))
    elif op == 0b111101:  # Pre_CSBK
        info.fields.update(
            data_follows=bool(d[0] & 0x80), group=bool(d[0] & 0x40), blocks_to_follow=d[1],
            target=_u24(d[2:5]), source=_u24(d[5:8]),
        )
    elif op == 0b011001:  # C_ALOHA (table 7.19)
        # octet 2: R1 TSCCAS1 SiteSync1 Version3 Offset1 Active1 | 16 bits: Mask5 SF2 NRand4 Reg1 Backoff4
        w = (d[1] << 8) | d[2]
        syscode = (d[3] << 8) | d[4]
        info.fields.update(
            tsccas=bool(d[0] & 0x40), site_sync=bool(d[0] & 0x20), version=(d[0] >> 2) & 7,
            offset_timing=bool(d[0] & 2), active_connection=bool(d[0] & 1),
            mask=(w >> 11) & 0x1F, service_function=(w >> 9) & 3, nrand_wait=(w >> 5) & 0xF,
            reg=bool((w >> 4) & 1), backoff=w & 0xF, syscode=syscode, ms_address=_u24(d[5:8]),
        )
        info.fields["syscode_fields"] = syscode_fields(syscode)
        info.system = {"syscode": syscode, **syscode_fields(syscode)}
    elif op == 0b101000:  # C_BCAST (table 7.20): ann5 parms1(14) reg1 backoff4 syscode16 parms2(24)
        value = int.from_bytes(d, "big")
        syscode = (value >> 24) & 0xFFFF
        info.fields.update(
            announcement_type=(value >> 59) & 0x1F, reg=bool((value >> 44) & 1),
            backoff=(value >> 40) & 0xF, syscode=syscode,
        )
        info.fields["syscode_fields"] = syscode_fields(syscode)
        info.system = {"syscode": syscode, **syscode_fields(syscode)}
    elif op in (0b110000, 0b110001, 0b110010, 0b110101):  # voice grants (tables 7.9-7.12)
        value = int.from_bytes(d[:2], "big")  # LPCN12 | LCN1 | b1 | emergency1 | offset1
        lpcn = value >> 4
        lcn = (value >> 3) & 1
        flag_a = (value >> 2) & 1  # late entry (TV/BTV) or reserved (PV)
        emergency = bool((value >> 1) & 1)
        offset = bool(value & 1)
        kind = {0b110000: "private", 0b110001: "group", 0b110010: "broadcast", 0b110101: "private_duplex"}[op]
        info.fields.update(
            logical_channel=lpcn, tdma_slot=lcn, emergency=emergency, offset_timing=offset,
            target=_u24(d[2:5]), source=_u24(d[5:8]), call=kind,
        )
        if op != 0b110000 and op != 0b110101:
            info.fields["late_entry"] = bool(flag_a)
        info.grant = dict(info.fields)
    elif op in (0b110011, 0b110100, 0b110110):  # data grants (tables 7.13-7.15)
        value = int.from_bytes(d[:2], "big")
        info.fields.update(
            logical_channel=value >> 4, tdma_slot=(value >> 3) & 1, hi_rate=bool((value >> 2) & 1),
            emergency=bool((value >> 1) & 1), offset_timing=bool(value & 1),
            target=_u24(d[2:5]), source=_u24(d[5:8]), call="data",
        )
        info.grant = dict(info.fields)
    return info


# ---- multi-block control (TS 102 361-1 clause 7.4) ---------------------------------------


class MbcAssembler:
    """Header (CRC-protected, mask AAAA) + 0-2 intermediate blocks + a last block with a CRC-16.

    The last block's CRC is checked over every continuation block except the CRC itself
    (spec text: "performed on all MBC blocks except the header block"). That span is read
    literally from the prose; no worked example was available to confirm it."""

    def __init__(self) -> None:
        self.header: Optional[Csbk] = None
        self.blocks: list[bytes] = []  # 12 octets each (continuations)

    def reset(self) -> None:
        self.header, self.blocks = None, []

    def add_header(self, octets12: bytes) -> None:
        self.header = parse_csbk(octets12[:10])
        self.blocks = []

    def add_continuation(self, octets12: bytes) -> Optional[tuple[Csbk, bytes]]:
        if self.header is None:
            return None
        self.blocks.append(bytes(octets12))
        last = bool(octets12[0] & 0x80)
        if len(self.blocks) > 2 and not last:
            self.reset()
            return None
        if not last:
            return None
        body = b"".join(self.blocks[:-1]) + self.blocks[-1][:10]
        got = int.from_bytes(self.blocks[-1][10:12], "big")
        header, self.header, self.blocks = self.header, None, []
        if crc.crc_compute("crc16_p25_dmr_header", bytes_to_bits(body)) != got:
            return None
        return header, body
