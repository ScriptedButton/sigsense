"""NXDN channel coding, receive side (WP-E). Original code from NXDN TS 1-A v1.3 section 4.5.

For every functional channel the receive chain is
    deinterleave (the coded block was written as `rows` rows and read by columns)
    -> depuncture + Viterbi (K=5, rate 1/2, G1 = 1 + D^3 + D^4, G2 = 1 + D + D^2 + D^4)
    -> check the CRC over (information + null bits)
The WP-A library reproduces the TS 2-B conformance vectors for these chains bit for bit
(tests/test_dvoice_fec_viterbi.py), so the same primitives are used here.

Information bit counts (from the vectors and the formats of TS 1-A 6.2-6.3):
  CAC 152 = SR 8 + 144 (L3 message of 18 octets)      Long CAC 136 = SR 8 + 128
  Short CAC 104 = SR 8 + 96                           SACCH 26 = SR 8 + 18 (one quarter of 72)
  FACCH1 80 (10 octets, no SR)                        UDCH / FACCH2 184 = SR 8 + 176 (22 octets)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from sigsense_api.dvoice.fec import crc, interleave, viterbi
from sigsense_api.dvoice.protocols.nxdn.bits import bits_to_int


def _keep(matrix: Optional[tuple[str, str]]) -> Optional[tuple[int, ...]]:
    """Puncturing matrix rows (G1, G2) -> the linear keep-mask over the alternating G1,G2 stream."""
    if matrix is None:
        return None
    return tuple(int(matrix[row][col]) for col in range(len(matrix[0])) for row in range(2))


_POLYS = viterbi.NXDN_K5_R12.polys


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    table: str  # fec.interleave table name
    info_bits: int  # information bits (including SR where the channel has one)
    nulls: int  # null bits inserted before the CRC
    crc_name: str
    crc_bits: int
    coded_bits: int
    has_sr: bool
    code: viterbi.ConvCode
    # Viterbi path metrics above which a CRC pass is not believed. Hard decisions: the metric is a channel
    # error count; soft decisions: a soft cost (sum of (1 - expected x confidence)/2), larger for the same
    # noise. Measured on random input (0.1 percentile; p50): hard cac 27 (33), long 20 (25), short 29 (35),
    # sacch 4 (8), facch1 9 (13), udch 32 (38); soft cac 49 (56), long 40 (45), short 48 (52), sacch 9 (12),
    # facch1 20 (24), udch 60 (65). Strong CRCs (12-16 bits) almost never pass random input, so their limits
    # are generous; SACCH has a 6-bit CRC (1.4 % of random input passes it) and is the only channel the
    # limits really protect. `max_*` apply while SCANNING for a frame; `locked_*` once the decoder is locked on
    # the frame timing (the evidence is then the established lock plus a whole-superframe consistency check).
    max_metric: int
    max_metric_soft: int
    locked_metric: int = 0
    locked_metric_soft: int = 0


CHANNELS: dict[str, ChannelSpec] = {
    "cac": ChannelSpec(
        "cac", "nxdn_cac_outbound", 152, 3, "crc16_ccitt_false", 16, 300, True,
        viterbi.ConvCode(5, _POLYS, _keep(("1111111", "1011101")), 4), 40, 70, 60, 110,
    ),
    "long_cac": ChannelSpec(
        "long_cac", "nxdn_cac_long", 136, 0, "crc16_ccitt_false", 16, 252, True,
        viterbi.ConvCode(5, _POLYS, _keep(("1111111111111", "0110001110111")), 4), 32, 60, 50, 90,
    ),
    "short_cac": ChannelSpec(
        "short_cac", "nxdn_cac_short", 104, 2, "crc16_ccitt_false", 16, 252, True,
        viterbi.ConvCode(5, _POLYS, None, 4), 40, 70, 60, 110,
    ),
    "sacch": ChannelSpec(
        "sacch", "nxdn_sacch", 26, 0, "crc6_nxdn", 6, 60, True,
        viterbi.ConvCode(5, _POLYS, _keep(("111111", "110110")), 4), 4, 9, 9, 15,
    ),
    "facch1": ChannelSpec(
        "facch1", "nxdn_facch1", 80, 0, "crc12_nxdn", 12, 144, False,
        viterbi.ConvCode(5, _POLYS, _keep(("11", "01")), 4), 20, 38, 30, 55,
    ),
    "udch": ChannelSpec(
        "udch", "nxdn_udch", 184, 0, "crc15_nxdn", 15, 348, True,
        viterbi.ConvCode(5, _POLYS, _keep(("1111111", "1011101")), 4), 50, 95, 70, 130,
    ),
}


@dataclass
class ChannelResult:
    spec: ChannelSpec
    info: np.ndarray  # information bits (SR first where present); not trustworthy unless `ok`
    crc_ok: bool
    metric: int  # Viterbi path metric (estimated channel errors, or soft cost when soft input was used)
    soft: bool = False

    def accepts(self, locked: bool) -> bool:
        """CRC passed and the path metric is plausible (looser limits once the frame timing is locked)."""
        if locked:
            limit = self.spec.locked_metric_soft if self.soft else self.spec.locked_metric
        else:
            limit = self.spec.max_metric_soft if self.soft else self.spec.max_metric
        return self.crc_ok and self.metric <= limit

    @property
    def ok(self) -> bool:
        return self.accepts(False)

    @property
    def sr(self) -> Optional[int]:
        return bits_to_int(self.info[:8]) if self.spec.has_sr else None

    @property
    def l3_bits(self) -> np.ndarray:
        return self.info[8:] if self.spec.has_sr else self.info


def decode_channel(
    name: str, bits: np.ndarray, soft: Optional[np.ndarray] = None
) -> ChannelResult:
    """Decode the received (descrambled) coded bits of one functional channel."""
    spec = CHANNELS[name]
    if len(bits) != spec.coded_bits:
        raise ValueError(f"{name}: expected {spec.coded_bits} coded bits, got {len(bits)}")
    perm = interleave.table(spec.table)
    rx = interleave.deinterleave(bits, perm)
    rx_soft = None if soft is None else interleave.deinterleave(soft, perm)
    result = viterbi.viterbi_decode(spec.code, rx, rx_soft)
    n = spec.info_bits + spec.nulls + spec.crc_bits
    data = result.data[:n]
    crc_ok = data.size == n and crc.crc_check(spec.crc_name, data)
    return ChannelResult(spec, data[: spec.info_bits], bool(crc_ok), int(result.corrected), soft is not None)
