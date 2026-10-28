"""P25 Phase 2 MAC PDU parsing (SACCH / FACCH / LCCH information bits). Original code.

Bit offsets are from the start of the MAC PDU information field (bit 0 = first bit after the DUID gap
removal). Header: bits 0-2 PDU type, 3-5 offset to the next 4V burst, 6-7 reserved. For the signalling
types (SIGNAL, IDLE, ACTIVE, HANGTIME) one to three structures follow from bit 8, each starting with
an 8-bit opcode and a fixed length in octets; PTT and END-PTT occupy the whole PDU.

PROVENANCE: structure layouts and the opcode/length table are numeric facts from SDRTrunk's
io.github.dsheirer.module.decode.p25.phase2.message.mac (TIA-102.BBAC-A / TIA-102.AABC-B content).
Only structures that feed call tracking or the scrambler are decoded into fields; the others are named
and skipped by length.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

PDU_SIGNAL, PDU_PTT, PDU_END_PTT, PDU_IDLE, PDU_ACTIVE, PDU_HANGTIME = 0, 1, 2, 3, 4, 6
PDU_NAMES = {0: "SIGNAL", 1: "PTT", 2: "END_PTT", 3: "IDLE", 4: "ACTIVE", 5: "RESERVED_5", 6: "HANGTIME", 7: "RESERVED_7"}

OP_NULL = 0x00
OP_GVCU = 0x01  # group voice channel user, abbreviated
OP_UUVCU = 0x02  # unit-to-unit voice channel user, abbreviated
OP_GVCU_EXT = 0x21
OP_MAC_RELEASE = 0x31
OP_GRANT_IMPLICIT = 0x40
OP_GRANT_UPDATE_IMPLICIT = 0x42
OP_NSB_IMPLICIT = 0x7B
OP_GRANT_EXPLICIT = 0xC0
OP_NSB_EXPLICIT = 0xFB

#: standard opcodes -> (name, length in octets or None when variable/unknown)
OPCODES: dict[int, tuple[str, Optional[int]]] = {
    0x00: ("NULL_INFORMATION_MESSAGE", None),
    0x01: ("GROUP_VOICE_CHANNEL_USER_ABBREVIATED", 7),
    0x02: ("UNIT_TO_UNIT_VOICE_CHANNEL_USER_ABBREVIATED", 8),
    0x03: ("TELEPHONE_INTERCONNECT_VOICE_CHANNEL_USER", 7),
    0x05: ("GROUP_VOICE_CHANNEL_GRANT_UPDATE_MULTIPLE_IMPLICIT", 16),
    0x08: ("NULL_AVOID_ZERO_BIAS", None),
    0x10: ("MULTI_FRAGMENT_CONTINUATION_MESSAGE", None),
    0x11: ("INDIRECT_GROUP_PAGING_WITHOUT_PRIORITY", None),
    0x12: ("INDIVIDUAL_PAGING_WITH_PRIORITY", None),
    0x21: ("GROUP_VOICE_CHANNEL_USER_EXTENDED", 14),
    0x22: ("UNIT_TO_UNIT_VOICE_CHANNEL_USER_EXTENDED", 15),
    0x25: ("GROUP_VOICE_CHANNEL_GRANT_UPDATE_MULTIPLE_EXPLICIT", 15),
    0x30: ("POWER_CONTROL_SIGNAL_QUALITY", 5),
    0x31: ("MAC_RELEASE", 7),
    0x40: ("GROUP_VOICE_CHANNEL_GRANT_IMPLICIT", 9),
    0x41: ("GROUP_VOICE_SERVICE_REQUEST", 7),
    0x42: ("GROUP_VOICE_CHANNEL_GRANT_UPDATE_IMPLICIT", 9),
    0x44: ("UNIT_TO_UNIT_VOICE_SERVICE_CHANNEL_GRANT_ABBREVIATED", 9),
    0x45: ("UNIT_TO_UNIT_ANSWER_REQUEST_ABBREVIATED", 10),
    0x46: ("UNIT_TO_UNIT_VOICE_CHANNEL_GRANT_UPDATE_ABBREVIATED", 9),
    0x48: ("TELEPHONE_INTERCONNECT_VOICE_CHANNEL_GRANT_IMPLICIT", 10),
    0x49: ("TELEPHONE_INTERCONNECT_VOICE_CHANNEL_GRANT_UPDATE_IMPLICIT", 10),
    0x4A: ("TELEPHONE_INTERCONNECT_ANSWER_RESPONSE", 7),
    0x4C: ("RADIO_UNIT_MONITOR_COMMAND_ABBREVIATED", 10),
    0x52: ("SNDCP_DATA_CHANNEL_REQUEST", 8),
    0x53: ("SNDCP_DATA_PAGE_RESPONSE", 9),
    0x54: ("SNDCP_DATA_CHANNEL_GRANT", 9),
    0x55: ("SNDCP_DATA_PAGE_REQUEST", 7),
    0x58: ("STATUS_UPDATE_ABBREVIATED", 10),
    0x5A: ("STATUS_QUERY_ABBREVIATED", 7),
    0x5C: ("MESSAGE_UPDATE_ABBREVIATED", 10),
    0x5D: ("RADIO_UNIT_MONITOR_COMMAND_OBSOLETE", 8),
    0x5E: ("RADIO_UNIT_MONITOR_ENHANCED_COMMAND_ABBREVIATED", 14),
    0x5F: ("CALL_ALERT_ABBREVIATED", 7),
    0x60: ("ACKNOWLEDGE_RESPONSE_FNE_ABBREVIATED", 9),
    0x61: ("QUEUED_RESPONSE", 9),
    0x64: ("EXTENDED_FUNCTION_COMMAND_ABBREVIATED", 9),
    0x67: ("DENY_RESPONSE", 9),
    0x68: ("GROUP_AFFILIATION_RESPONSE_ABBREVIATED", 10),
    0x6A: ("GROUP_AFFILIATION_QUERY_ABBREVIATED", 7),
    0x6B: ("LOCATION_REGISTRATION_RESPONSE", 10),
    0x6C: ("UNIT_REGISTRATION_RESPONSE_ABBREVIATED", 10),
    0x6D: ("UNIT_REGISTRATION_COMMAND_ABBREVIATED", 7),
    0x6F: ("DEREGISTRATION_ACKNOWLEDGE", 9),
    0x70: ("SYNCHRONIZATION_BROADCAST", 9),
    0x71: ("AUTHENTICATION_DEMAND", 18),
    0x72: ("AUTHENTICATION_FNE_RESPONSE_ABBREVIATED", 9),
    0x73: ("IDENTIFIER_UPDATE_TDMA_ABBREVIATED", 9),
    0x74: ("IDENTIFIER_UPDATE_V_UHF", 9),
    0x75: ("TIME_AND_DATE_ANNOUNCEMENT", 9),
    0x76: ("ROAMING_ADDRESS_COMMAND", 10),
    0x77: ("ROAMING_ADDRESS_UPDATE", 13),
    0x78: ("SYSTEM_SERVICE_BROADCAST", 9),
    0x79: ("SECONDARY_CONTROL_CHANNEL_BROADCAST_IMPLICIT", 9),
    0x7A: ("RFSS_STATUS_BROADCAST_IMPLICIT", 9),
    0x7B: ("NETWORK_STATUS_BROADCAST_IMPLICIT", 11),
    0x7C: ("ADJACENT_STATUS_BROADCAST_IMPLICIT", 9),
    0x7D: ("IDENTIFIER_UPDATE", 9),
    0x88: ("UNKNOWN_LCCH_OPCODE", 5),
    0x90: ("GROUP_REGROUP_VOICE_CHANNEL_USER_ABBREVIATED", 7),
    0xC0: ("GROUP_VOICE_CHANNEL_GRANT_EXPLICIT", 11),
    0xC3: ("GROUP_VOICE_CHANNEL_GRANT_UPDATE_EXPLICIT", 8),
    0xC4: ("UNIT_TO_UNIT_VOICE_SERVICE_CHANNEL_GRANT_EXTENDED_VCH", 15),
    0xC5: ("UNIT_TO_UNIT_ANSWER_REQUEST_EXTENDED", 14),
    0xC6: ("UNIT_TO_UNIT_VOICE_CHANNEL_GRANT_UPDATE_EXTENDED_VCH", 15),
    0xC7: ("UNIT_TO_UNIT_VOICE_CHANNEL_GRANT_UPDATE_EXTENDED_LCCH", 18),
    0xC8: ("TELEPHONE_INTERCONNECT_VOICE_CHANNEL_GRANT_EXPLICIT", 12),
    0xC9: ("TELEPHONE_INTERCONNECT_VOICE_CHANNEL_GRANT_UPDATE_EXPLICIT", 12),
    0xCB: ("CALL_ALERT_EXTENDED_LCCH", 18),
    0xCC: ("RADIO_UNIT_MONITOR_COMMAND_EXTENDED_VCH", 14),
    0xCD: ("RADIO_UNIT_MONITOR_COMMAND_EXTENDED_LCCH", 18),
    0xCE: ("MESSAGE_UPDATE_EXTENDED_LCCH", 18),
    0xCF: ("UNIT_TO_UNIT_VOICE_SERVICE_CHANNEL_GRANT_EXTENDED_LCCH", 18),
    0xD6: ("SNDCP_DATA_CHANNEL_ANNOUNCEMENT", 9),
    0xD8: ("STATUS_UPDATE_EXTENDED_VCH", 14),
    0xD9: ("STATUS_UPDATE_EXTENDED_LCCH", 18),
    0xDA: ("STATUS_QUERY_EXTENDED_VCH", 11),
    0xDB: ("STATUS_QUERY_EXTENDED_LCCH", 18),
    0xDC: ("MESSAGE_UPDATE_EXTENDED_VCH", 14),
    0xDE: ("RADIO_UNIT_MONITOR_ENHANCED_COMMAND_EXTENDED", 18),
    0xDF: ("CALL_ALERT_EXTENDED_VCH", 11),
    0xE0: ("ACKNOWLEDGE_RESPONSE_FNE_EXTENDED", 18),
    0xE4: ("EXTENDED_FUNCTION_COMMAND_EXTENDED_VCH", 17),
    0xE5: ("EXTENDED_FUNCTION_COMMAND_EXTENDED_LCCH", 14),
    0xE8: ("GROUP_AFFILIATION_RESPONSE_EXTENDED", 16),
    0xE9: ("SECONDARY_CONTROL_CHANNEL_BROADCAST_EXPLICIT", 8),
    0xEA: ("GROUP_AFFILIATION_QUERY_EXTENDED", 11),
    0xEC: ("UNIT_REGISTRATION_RESPONSE_EXTENDED", 13),
    0xF2: ("AUTHENTICATION_FNE_RESPONSE_EXTENDED", 16),
    0xF3: ("IDENTIFIER_UPDATE_TDMA_EXTENDED", 14),
    0xFA: ("RFSS_STATUS_BROADCAST_EXPLICIT", 11),
    0xFB: ("NETWORK_STATUS_BROADCAST_EXPLICIT", 13),
    0xFC: ("ADJACENT_STATUS_BROADCAST_EXPLICIT", 11),
    0xFE: ("ADJACENT_STATUS_BROADCAST_EXTENDED_EXPLICIT", 15),
}


#: highest information-bit index structures may use, per channel (the CRC and, on LCCH, the NAC follow)
MAX_INDEX = {"sacch": 168, "facch": 144, "lcch": 152}


def _int(bits: np.ndarray, start: int, width: int) -> int:
    v = 0
    for b in bits[start : start + width]:
        v = (v << 1) | int(b)
    return v


@dataclass
class MacStructure:
    opcode: int
    name: str
    offset: int
    length: Optional[int]
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class MacPdu:
    pdu_type: int
    voice_offset: int
    structures: list[MacStructure]
    nac: Optional[int] = None

    @property
    def type_name(self) -> str:
        return PDU_NAMES.get(self.pdu_type, "UNKNOWN")


def parse_structure(bits: np.ndarray, s: int) -> MacStructure:
    op = _int(bits, s, 8)
    name, length = OPCODES.get(op, (f"OPCODE_0x{op:02X}", None))
    f: dict[str, Any] = {}
    if op in (OP_GVCU, OP_GVCU_EXT):
        f = {"service": _int(bits, s + 8, 8), "group": _int(bits, s + 16, 16), "source": _int(bits, s + 32, 24)}
    elif op == OP_UUVCU:
        f = {"service": _int(bits, s + 8, 8), "target": _int(bits, s + 16, 24), "source": _int(bits, s + 40, 24)}
    elif op == OP_MAC_RELEASE:
        f = {"target": _int(bits, s + 16, 24), "nac": _int(bits, s + 44, 12)}
    elif op == OP_NSB_IMPLICIT:
        f = {"lra": _int(bits, s + 8, 8), "wacn": _int(bits, s + 16, 20), "system_id": _int(bits, s + 36, 12),
             "channel": (_int(bits, s + 48, 4) << 12) | _int(bits, s + 52, 12), "service_class": _int(bits, s + 64, 8),
             "nac": _int(bits, s + 76, 12)}
    elif op == OP_NSB_EXPLICIT:
        f = {"lra": _int(bits, s + 8, 8), "wacn": _int(bits, s + 16, 20), "system_id": _int(bits, s + 36, 12),
             "channel": (_int(bits, s + 48, 4) << 12) | _int(bits, s + 52, 12),
             "rx_channel": (_int(bits, s + 64, 4) << 12) | _int(bits, s + 68, 12),
             "service_class": _int(bits, s + 80, 8), "nac": _int(bits, s + 92, 12)}
    elif op == OP_GRANT_IMPLICIT:
        f = {"service": _int(bits, s + 8, 8), "channel": (_int(bits, s + 16, 4) << 12) | _int(bits, s + 20, 12),
             "group": _int(bits, s + 32, 16), "source": _int(bits, s + 48, 24)}
    elif op == OP_GRANT_EXPLICIT:
        f = {"service": _int(bits, s + 8, 8), "channel": (_int(bits, s + 16, 4) << 12) | _int(bits, s + 20, 12),
             "rx_channel": (_int(bits, s + 32, 4) << 12) | _int(bits, s + 36, 12),
             "group": _int(bits, s + 48, 16), "source": _int(bits, s + 64, 24)}
    elif op == OP_GRANT_UPDATE_IMPLICIT:
        f = {"channel": (_int(bits, s + 8, 4) << 12) | _int(bits, s + 12, 12), "group": _int(bits, s + 24, 16)}
        ch2 = (_int(bits, s + 40, 4) << 12) | _int(bits, s + 44, 12)
        g2 = _int(bits, s + 56, 16)
        if g2:
            f.update(channel2=ch2, group2=g2)
    return MacStructure(op, name, s, length, f)


def parse_mac(bits: np.ndarray, channel: str) -> MacPdu:
    """Information bits (CRC already verified) -> MacPdu."""
    pdu_type = _int(bits, 0, 3)
    voice_offset = _int(bits, 3, 3)
    nac = _int(bits, 152, 12) if channel == "lcch" else None
    if pdu_type == PDU_PTT:
        st = MacStructure(-1, "PUSH_TO_TALK", 0, None, {
            "mi": _int(bits, 8, 72).to_bytes(9, "big"), "algorithm": _int(bits, 80, 8), "key_id": _int(bits, 88, 16),
            "source": _int(bits, 104, 24), "group": _int(bits, 128, 16)})
        return MacPdu(pdu_type, voice_offset, [st], nac)
    if pdu_type == PDU_END_PTT:
        st = MacStructure(-2, "END_PUSH_TO_TALK", 0, None, {
            "nac": _int(bits, 12, 12), "source": _int(bits, 104, 24), "group": _int(bits, 128, 16)})
        return MacPdu(pdu_type, voice_offset, [st], nac)
    structures: list[MacStructure] = []
    if pdu_type in (PDU_SIGNAL, PDU_IDLE, PDU_ACTIVE, PDU_HANGTIME):
        limit = MAX_INDEX[channel]
        s = 8
        while s + 8 <= limit and len(structures) < 3:
            st = parse_structure(bits, s)
            if st.opcode == OP_NULL:
                break
            structures.append(st)
            if st.length is None or st.length <= 0:
                break  # variable or unknown length: nothing after it can be located
            s += 8 * st.length
    return MacPdu(pdu_type, voice_offset, structures, nac)
