"""NXDN Layer 3 messages (WP-E). Original code from NXDN TS 1-A v1.3 section 6.

Only the messages that carry call identity and system identity are decoded; everything else is
named (so the UI can show that it was seen) and otherwise ignored. Formats are the octet tables
of section 6.4 (verified against the printed figures for VCALL, VCALL_ASSGN, SITE_INFO):

  octet 0   F1 F2 | message type (6 bits)
  VCALL / VCALL_ASSGN / VCALL_ASSGN_DUP:
    1 CC option (bit 7 emergency, bit 6 inter-system, bit 5 priority paging)
    2 call type (bits 7-5) | voice call option (bits 4-0: duplex bit 4, transmission mode bits 2-0)
    3-4 source unit ID   5-6 destination group or unit ID
    VCALL: 7 cipher type (bits 7-6) | key ID (bits 5-0)
    VCALL_ASSGN: 7 call timer (bits 7-2) | channel high bits (1-0), 8 channel low bits
  TX_REL / TX_REL_EX: 1 CC option, 2 call type (bits 7-5), 3-4 source, 5-6 destination
  VCALL_IV: octets 1-8 initialization vector (reported, never used)
  SITE_INFO: octets 1-3 location ID (category 2 bits, system code, site code)

Messages are only trusted when the channel's CRC passed; this module just interprets octets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sigsense_api.dvoice.events import CallKind

# Message types on the traffic channels (RTCH/RDCH), TS 1-A table 6.4-12
RTCH_TYPES = {
    0x01: "VCALL",
    0x02: "VCALL_REC_REQ",
    0x03: "VCALL_IV",
    0x04: "VCALL_ASSGN",
    0x05: "VCALL_ASSGN_DUP",
    0x07: "TX_REL_EX",
    0x08: "TX_REL",
    0x09: "DCALL_HEADER",
    0x0A: "DCALL_REC_REQ",
    0x0B: "DCALL_USER_DATA",
    0x0C: "DCALL_ACK",
    0x0D: "DCALL_ASSGN_DUP",
    0x0E: "DCALL_ASSGN",
    0x0F: "HEAD_DLY",
    0x10: "IDLE",
    0x11: "DISC",
    0x19: "SRV_INFO",
    0x1A: "CCH_INFO",
    0x1B: "ADJ_SITE_INFO",
    0x1C: "FAIL_STAT_INFO",
    0x28: "AUTH_INQ_REQ",
    0x29: "AUTH_INQ_RESP",
    0x30: "STAT_INQ_REQ",
    0x31: "STAT_INQ_RESP",
    0x32: "STAT_REQ",
    0x33: "STAT_RESP",
    0x34: "REM_CON_REQ",
    0x35: "REM_CON_RESP",
    0x38: "SDCALL_REQ_HEADER",
    0x39: "SDCALL_REQ_USER_DATA",
    0x3A: "SDCALL_IV",
    0x3B: "SDCALL_RESP",
    0x3F: "PROP_FORM",
}

# Outbound control channel (RCCH, base station -> units)
RCCH_OUT_TYPES = {
    0x01: "VCALL_RESP",
    0x02: "VCALL_REC_REQ",
    0x03: "VCALL_CONN_RESP",
    0x04: "VCALL_ASSGN",
    0x05: "VCALL_ASSGN_DUP",
    0x09: "DCALL_RESP",
    0x0A: "DCALL_REC_REQ",
    0x0D: "DCALL_ASSGN_DUP",
    0x0E: "DCALL_ASSGN",
    0x10: "IDLE",
    0x11: "DISC",
    0x18: "SITE_INFO",
    0x19: "SRV_INFO",
    0x1A: "CCH_INFO",
    0x1B: "ADJ_SITE_INFO",
    0x20: "REG_RESP",
    0x22: "REG_C_RESP",
    0x23: "REG_COMM",
    0x24: "GRP_REG_RESP",
    0x28: "AUTH_INQ_REQ",
    0x30: "STAT_INQ_REQ",
    0x31: "STAT_INQ_RESP",
    0x32: "STAT_REQ",
    0x33: "STAT_RESP",
    0x34: "REM_CON_REQ",
    0x35: "REM_CON_RESP",
    0x38: "SDCALL_REQ_HEADER",
    0x39: "SDCALL_REQ_USER_DATA",
    0x3A: "SDCALL_IV",
    0x3B: "SDCALL_RESP",
    0x3F: "PROP_FORM",
}

RCCH_IN_TYPES = {
    0x01: "VCALL_REQ",
    0x02: "VCALL_REC_RESP",
    0x03: "VCALL_CONN_REQ",
    0x09: "DCALL_REQ",
    0x0A: "DCALL_REC_RESP",
    0x11: "DISC_REQ",
    0x20: "REG_REQ",
    0x22: "REG_C_REQ",
    0x24: "GRP_REG_REQ",
    0x29: "AUTH_INQ_RESP",
    0x3F: "PROP_FORM",
}

TABLES = {"rtch": RTCH_TYPES, "rcch_out": RCCH_OUT_TYPES, "rcch_in": RCCH_IN_TYPES}

# Call type (3 bits), table 6.5-8
CALL_TYPES = {
    0: "broadcast",
    1: "conference",
    2: "unspecified",
    4: "individual",
    6: "interconnect",
    7: "speed dial",
}

CIPHER_TYPES = {0: "none", 1: "scramble", 2: "DES", 3: "AES"}
TX_MODES = {0: "4800 bps / EHR", 2: "9600 bps / EHR", 3: "9600 bps / EFR"}

UNIT_ALL = 0xFFFF


def call_kind(call_type: int) -> CallKind:
    if call_type in (0, 1):
        return CallKind.GROUP
    if call_type == 4:
        return CallKind.PRIVATE
    if call_type in (6, 7):
        return CallKind.TELEPHONE
    return CallKind.UNKNOWN


@dataclass
class L3Message:
    msg_type: int
    name: str
    fields: dict = field(default_factory=dict)
    octets: bytes = b""


def _u16(octets: bytes, i: int) -> int:
    return (octets[i] << 8) | octets[i + 1]


def _cc_option(octet: int) -> dict:
    return {"emergency": bool(octet & 0x80), "inter_system": bool(octet & 0x40), "priority_paging": bool(octet & 0x20)}


def location_id(value: int) -> dict:
    """24-bit location ID: category (2 bits) then system code and site code (table 6.5-1)."""
    category = (value >> 22) & 3
    if category == 0b00:  # global: system code 10 bits, site code 12 bits
        name, system, site = "global", (value >> 12) & 0x3FF, value & 0xFFF
    elif category == 0b10:  # regional: 14 and 8 bits
        name, system, site = "regional", (value >> 8) & 0x3FFF, value & 0xFF
    elif category == 0b01:  # local: 17 and 5 bits
        name, system, site = "local", (value >> 5) & 0x1FFFF, value & 0x1F
    else:
        name, system, site = "reserved", (value >> 12) & 0x3FF, value & 0xFFF
    return {"category": name, "system_code": system, "site_code": site}


def parse_l3(octets: bytes, channel: str = "rtch") -> Optional[L3Message]:
    """Interpret one layer 3 message. `channel`: rtch (traffic), rcch_out or rcch_in (control)."""
    if len(octets) < 1:
        return None
    msg_type = octets[0] & 0x3F
    name = TABLES[channel].get(msg_type, f"MSG_0x{msg_type:02X}")
    fields: dict = {}
    n = len(octets)
    if name in ("VCALL", "VCALL_ASSGN", "VCALL_ASSGN_DUP", "DCALL_HEADER", "DCALL_ASSGN", "DCALL_ASSGN_DUP") and n >= 8:
        fields.update(_cc_option(octets[1]))
        call_type = octets[2] >> 5
        fields["call_type"] = call_type
        fields["call_type_name"] = CALL_TYPES.get(call_type, f"reserved({call_type})")
        option = octets[2] & 0x1F
        fields["duplex"] = bool(option & 0x10)
        mode = option & 7
        fields["tx_mode"] = mode
        fields["tx_mode_name"] = TX_MODES.get(mode, f"reserved({mode})")
        fields["source_id"] = _u16(octets, 3)
        fields["dest_id"] = _u16(octets, 5)
        if name in ("VCALL", "DCALL_HEADER"):
            fields["cipher_type"] = octets[7] >> 6
            fields["cipher_name"] = CIPHER_TYPES[octets[7] >> 6]
            fields["key_id"] = octets[7] & 0x3F
        elif n >= 9:
            fields["call_timer"] = octets[7] >> 2
            fields["channel"] = ((octets[7] & 3) << 8) | octets[8]
    elif name in ("TX_REL", "TX_REL_EX") and n >= 7:
        fields.update(_cc_option(octets[1]))
        fields["call_type"] = octets[2] >> 5
        fields["call_type_name"] = CALL_TYPES.get(octets[2] >> 5, "reserved")
        fields["source_id"] = _u16(octets, 3)
        fields["dest_id"] = _u16(octets, 5)
    elif name == "VCALL_IV" and n >= 9:
        fields["iv_present"] = any(octets[1:9])
    elif name == "SITE_INFO" and n >= 4:
        fields.update(location_id((octets[1] << 16) | (octets[2] << 8) | octets[3]))
    return L3Message(msg_type, name, fields, bytes(octets))
