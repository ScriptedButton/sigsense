"""DMR packet-data headers and blocks (metadata level).

Header layouts: ETSI TS 102 361-1 clause 9.2 (tables 9.10-9.18). Payload decoding is off
by default (`ProtocolConfig.decode_data`); addressing metadata is always reported, like
the addressing of a voice call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

DPF_NAMES = {
    0b0000: "UDT",
    0b0001: "response",
    0b0010: "unconfirmed",
    0b0011: "confirmed",
    0b1101: "short_data_defined",
    0b1110: "short_data_raw_status",
    0b1111: "proprietary",
}
SAP_NAMES = {
    0b0000: "UDT",
    0b0010: "TCP/IP header compression",
    0b0011: "UDP/IP header compression",
    0b0100: "IP",
    0b0101: "ARP",
    0b1001: "proprietary",
    0b1010: "short data",
}
# user-data octets per block, by burst data type (rate 1/2, rate 3/4)
BLOCK_OCTETS = {7: 12, 8: 18}


@dataclass(frozen=True)
class DataHeader:
    group: bool
    response_requested: bool
    dpf: int
    sap: int
    dst: int  # logical link id
    src: int
    blocks_to_follow: int
    full_message: bool
    confirmed: bool
    raw: bytes

    @property
    def dpf_name(self) -> str:
        return DPF_NAMES.get(self.dpf, f"DPF_{self.dpf:X}")

    @property
    def sap_name(self) -> str:
        return SAP_NAMES.get(self.sap, f"SAP_{self.sap:X}")


def parse_data_header(octets12: bytes) -> Optional[DataHeader]:
    """The common fields of C_HEAD/U_HEAD/SP_HEAD/R_HEAD/DD_HEAD (octets 0-8). Response and
    UDT headers use other layouts and are reported with addressing only."""
    if len(octets12) != 12:
        return None
    b = octets12
    dpf = b[0] & 0xF
    if dpf not in DPF_NAMES:
        return None
    return DataHeader(
        group=bool(b[0] & 0x80),
        response_requested=bool(b[0] & 0x40),
        dpf=dpf,
        sap=(b[1] >> 4) & 0xF,
        dst=int.from_bytes(b[2:5], "big"),
        src=int.from_bytes(b[5:8], "big"),
        blocks_to_follow=b[8] & 0x7F,
        full_message=bool(b[8] & 0x80),
        confirmed=dpf == 0b0011,
        raw=bytes(b),
    )


def build_data_header(
    *, dpf: int, sap: int, dst: int, src: int, blocks: int, group: bool = False,
    response_requested: bool = False, full_message: bool = True,
) -> bytes:
    """10 octets (without CRC) of a U_HEAD/C_HEAD-shaped header."""
    o0 = (int(group) << 7) | (int(response_requested) << 6) | (dpf & 0xF)
    o1 = (sap & 0xF) << 4
    o8 = (int(full_message) << 7) | (blocks & 0x7F)
    return bytes([o0, o1]) + dst.to_bytes(3, "big") + src.to_bytes(3, "big") + bytes([o8, 0])
