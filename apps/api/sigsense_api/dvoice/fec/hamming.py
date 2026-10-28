"""Hamming codes used by DMR/P25/NXDN/YSF (CONTRACT v1). Owner: WP-A. Original code.

Generator matrices come from the specifications (ETSI TS 102 361-1 Tables
B.13-B.17 for the DMR family, TIA-102.BAAA-A Table 5-4 for P25). Note the DMR
(15,11,3) and the P25 (15,11,3) are different parity assignments of the same
cyclic code: `hamming_15_11_3_*` is the DMR/BPTC one, `p25_hamming_15_11_3_*`
the P25 one.

Words are Python ints (MSB first). Decoders return `(data, corrected)` where
corrected is 0 or 1, or None when the syndrome is not a single-bit one (the
extended code (16,11,4) therefore detects every double error).
"""

from __future__ import annotations

from typing import Optional

from sigsense_api.dvoice.fec import _specdata as spec
from sigsense_api.dvoice.fec._linear import SystematicCode, rows_from_matrix


def _from_matrix(matrix, k, name):
    return SystematicCode(k, len(matrix[0]) - k, rows_from_matrix(matrix, k), t=1, name=name)


_H74 = _from_matrix(spec.DMR_HAMMING_7_4_3, 4, "hamming7_4")
_H106 = _from_matrix(spec.P25_HAMMING_10_6_3, 6, "p25_hamming10_6")
_H139 = _from_matrix(spec.DMR_HAMMING_13_9_3, 9, "hamming13_9")
_H1511 = _from_matrix(spec.DMR_HAMMING_15_11_3, 11, "hamming15_11")
_P25H1511 = _from_matrix(spec.P25_HAMMING_15_11_3, 11, "p25_hamming15_11")
_H1611 = _from_matrix(spec.DMR_HAMMING_16_11_4, 11, "hamming16_11")
_H1712 = _from_matrix(spec.DMR_HAMMING_17_12_3, 12, "hamming17_12")


def hamming_7_4_encode(data4: int) -> int:
    return _H74.encode(data4)


def hamming_7_4_decode(word7: int) -> Optional[tuple[int, int]]:
    return _H74.decode(word7)


def hamming_10_6_3_encode(data6: int) -> int:
    """P25 shortened Hamming (10,6,3) (TIA-102.BAAA-A Table 5-4)."""
    return _H106.encode(data6)


def hamming_10_6_3_decode(word10: int) -> Optional[tuple[int, int]]:
    return _H106.decode(word10)


def hamming_13_9_3_encode(data9: int) -> int:
    """DMR BPTC 196,96 column code."""
    return _H139.encode(data9)


def hamming_13_9_3_decode(word13: int) -> Optional[tuple[int, int]]:
    return _H139.decode(word13)


def hamming_15_11_3_encode(data11: int) -> int:
    """DMR BPTC 196,96 row code."""
    return _H1511.encode(data11)


def hamming_15_11_3_decode(word15: int) -> Optional[tuple[int, int]]:
    return _H1511.decode(word15)


def p25_hamming_15_11_3_encode(data11: int) -> int:
    """P25 voice-frame Hamming (15,11,3) (TIA-102.BAAA-A Table 5-4)."""
    return _P25H1511.encode(data11)


def p25_hamming_15_11_3_decode(word15: int) -> Optional[tuple[int, int]]:
    return _P25H1511.decode(word15)


def hamming_16_11_4_encode(data11: int) -> int:
    """Extended Hamming(16,11,4): DMR embedded-signalling rows."""
    return _H1611.encode(data11)


def hamming_16_11_4_decode(word16: int) -> Optional[tuple[int, int]]:
    return _H1611.decode(word16)


def hamming_17_12_3_encode(data12: int) -> int:
    """DMR reverse-channel / RC burst code (ETSI Table B.13)."""
    return _H1712.encode(data12)


def hamming_17_12_3_decode(word17: int) -> Optional[tuple[int, int]]:
    return _H1712.decode(word17)
