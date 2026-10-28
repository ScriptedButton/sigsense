"""DMR air-interface constants (ETSI TS 102 361-1 V2.5.1). Original code.

Burst layout (clause 6, annex E), in transmit order, 264 bits = 132 dibits:

    voice burst : VS(215..108) 108 | EMB 8 | embedded 32 | EMB 8 | VS(107..0) 108
                  (burst A carries the 48-bit voice SYNC in the centre instead)
    data burst  : info 98 | slot type 10 | SYNC or embedded 48 | slot type 10 | info 98

On the outbound (BS) channel every 30 ms slot is a 24-bit CACH followed by the burst.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

import numpy as np

BURST_BITS = 264
BURST_DIBITS = 132
CACH_BITS = 24
CACH_DIBITS = 12
SLOT_DIBITS = 144  # 30 ms at 4800 symbols/s
SYNC_BITS = 48
SYNC_DIBITS = 24
SYNC_DIBIT_OFFSET = 54  # first sync dibit inside the burst (bit 108)
SUPERFRAME_BURSTS = 6  # voice bursts A..F
VOICE_FRAME_BITS = 72  # one vocoder frame
VOCODER_SOCKET_BITS = 216  # 3 frames per voice burst

# Bit positions inside the 264-bit burst.
VOICE_A = slice(0, 108)
VOICE_B = slice(156, 264)
CENTER = slice(108, 156)
EMB_FIRST = slice(108, 116)
EMBEDDED = slice(116, 148)
EMB_LAST = slice(148, 156)
DATA_INFO_A = slice(0, 98)
DATA_INFO_B = slice(166, 264)
SLOT_TYPE_A = slice(98, 108)
SLOT_TYPE_B = slice(156, 166)

# CACH bit positions in transmit order (figure B.9): AT, P16-14, TC, P13-11, LS1, P10-8, LS0,
# P7, H2, P6-4, H1, P3-1, H0, P0.
CACH_TACT_POSITIONS = (0, 4, 8, 12, 14, 18, 22)  # AT, TC, LS1, LS0, H2, H1, H0
CACH_PAYLOAD_POSITIONS = (1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 15, 16, 17, 19, 20, 21, 23)  # P16..P0


class DataType(enum.IntEnum):
    """Table 9.22."""

    PI_HEADER = 0
    VOICE_LC_HEADER = 1
    TERMINATOR_WITH_LC = 2
    CSBK = 3
    MBC_HEADER = 4
    MBC_CONTINUATION = 5
    DATA_HEADER = 6
    RATE_12_DATA = 7
    RATE_34_DATA = 8
    IDLE = 9
    RATE_1_DATA = 10
    UNIFIED_SINGLE_BLOCK = 11


class Lcss(enum.IntEnum):
    """Table 9.20."""

    SINGLE = 0
    FIRST = 1
    LAST = 2
    CONTINUATION = 3


# Data Type CRC masks (table B.21) and the 24-bit RS parity masks of LC headers/terminators.
CRC16_MASKS = {
    DataType.PI_HEADER: 0x6969,
    DataType.CSBK: 0xA5A5,
    DataType.MBC_HEADER: 0xAAAA,
    DataType.DATA_HEADER: 0xCCCC,
    DataType.UNIFIED_SINGLE_BLOCK: 0x3333,
}
LC_PARITY_MASKS = {
    DataType.VOICE_LC_HEADER: 0x969696,
    DataType.TERMINATOR_WITH_LC: 0x999999,
}
CRC9_MASKS = {DataType.RATE_12_DATA: 0x0F0, DataType.RATE_34_DATA: 0x1FF, DataType.RATE_1_DATA: 0x10F}


@dataclass(frozen=True)
class SyncWord:
    """One frame-sync pattern (table 9.2)."""

    name: str
    hex48: str
    source: str  # "bs" | "ms" | "rc" | "dm1" | "dm2"
    traffic: str  # "voice" | "data" | "rc"
    slot: Optional[int]  # direct-mode slot (0/1) when the pattern identifies it

    @property
    def bits(self) -> np.ndarray:
        value = int(self.hex48, 16)
        return np.array([(value >> (47 - i)) & 1 for i in range(48)], dtype=np.uint8)

    @property
    def dibits(self) -> np.ndarray:
        b = self.bits
        return ((b[0::2] << 1) | b[1::2]).astype(np.uint8)


SYNC_WORDS: tuple[SyncWord, ...] = (
    SyncWord("bs_voice", "755FD7DF75F7", "bs", "voice", None),
    SyncWord("bs_data", "DFF57D75DF5D", "bs", "data", None),
    SyncWord("ms_voice", "7F7D5DD57DFD", "ms", "voice", None),
    SyncWord("ms_data", "D5D7F77FD757", "ms", "data", None),
    SyncWord("rc", "77D55F7DFD77", "rc", "rc", None),
    SyncWord("dm1_voice", "5D577F7757FF", "dm1", "voice", 0),
    SyncWord("dm1_data", "F7FDD5DDFD55", "dm1", "data", 0),
    SyncWord("dm2_voice", "7DFFD5F55D5F", "dm2", "voice", 1),
    SyncWord("dm2_data", "D7557F5FF7F5", "dm2", "data", 1),
)
SYNC_BY_NAME = {s.name: s for s in SYNC_WORDS}


# The Idle message information bits I(95)..I(0) (ETSI TS 102 361-1 table D.2, annex D.2) and the
# 13 x 15 BPTC matrix they encode to (figure D.1), both read mechanically from the specification text.
IDLE_INFO_HEX = "ff83df1732094ed1e7cd8a91"
IDLE_BPTC_MATRIX = ('000111111110100', '100000111101101', '111110001011101', '110011001000101', '000100101000111', '111011010001101', '111100111110010', '001101100011101', '010100100010101', '010011100100011', '101100111101100', '001000010000100', '010010101110110')
