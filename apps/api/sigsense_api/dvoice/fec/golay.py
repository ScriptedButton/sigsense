"""Golay codes (CONTRACT v1). Owner: WP-A. Original code.

The (24,12,8) extended Golay code and its shortenings are built from the
systematic generator matrices printed in the air-interface specifications
(TIA-102.BAAA-A Table 5-3; ETSI TS 102 361-1 Table B.11). The (23,12,7) code is
the same code without the overall-parity bit (the least-significant parity bit
of each row is "not used" for it, per TIA-102.BAAA-A 5.7); tests cross-check it
against the generator polynomial g(x) = x^11+x^10+x^6+x^5+x^4+x^2+1.

Words are Python ints, MSB = first transmitted bit. Decoders return
`(data, corrected_bits)` or `None` when the word is uncorrectable.
"""

from __future__ import annotations

from typing import Optional

from sigsense_api.dvoice.fec import _specdata as spec
from sigsense_api.dvoice.fec._linear import SystematicCode, rows_from_matrix

# (24,12,8): corrects 3, detects 4 (every weight-4 pattern has a syndrome that no
# weight <= 3 pattern has, because d = 8).
_G24 = SystematicCode(12, 12, spec.P25_GOLAY_24_12_PARITY, t=3, name="golay24")
# (23,12,7): perfect code, drop the overall-parity bit (LSB of each parity row).
_G23 = SystematicCode(12, 11, [p >> 1 for p in spec.P25_GOLAY_24_12_PARITY], t=3, name="golay23")
# (18,6,8): the last six rows of the (24,12,8) code (leading six data bits deleted).
_G18 = SystematicCode(6, 12, spec.P25_GOLAY_18_6_PARITY, t=3, name="golay18")
# (20,8,7) for the DMR slot type.
_G20 = SystematicCode(8, 12, rows_from_matrix(spec.DMR_GOLAY_20_8, 8), t=3, name="golay20")


def golay_23_12_encode(data12: int) -> int:
    """12 data bits -> 23-bit Golay(23,12,7) codeword (g(x)=x^11+x^10+x^6+x^5+x^4+x^2+1)."""
    return _G23.encode(data12)


def golay_23_12_decode(word23: int) -> Optional[tuple[int, int]]:
    """Correct up to 3 errors; returns (data12, corrected) or None.

    The code is perfect (every word is within 3 of a codeword), so a result is
    always produced; heavier error patterns are silently miscorrected, as with
    any perfect code. Use the extended (24,12) decoder where detection matters."""
    return _G23.decode(word23)


def golay_24_12_encode(data12: int) -> int:
    """Extended Golay(24,12,8) (adds overall parity). Used by P25 HDU/LDU/TDULC link control."""
    return _G24.encode(data12)


def golay_24_12_decode(word24: int) -> Optional[tuple[int, int]]:
    """Correct up to 3 errors, detect 4; returns (data12, corrected) or None."""
    return _G24.decode(word24)


def golay_18_6_decode(word18: int) -> Optional[tuple[int, int]]:
    """Shortened Golay(18,6,8) used in P25 header words (TIA-102.BAAA-A 5.7).

    Corrects up to 3 errors, detects 4."""
    return _G18.decode(word18)


def golay_18_6_encode(data6: int) -> int:
    """Encoder for the shortened (18,6,8) code (additive helper for synthetic frames)."""
    return _G18.encode(data6)


def golay_20_8_encode(data8: int) -> int:
    """DMR slot-type word, Golay(20,8,7) (ETSI TS 102 361-1 clause B.3.1)."""
    return _G20.encode(data8)


def golay_20_8_decode(word20: int) -> Optional[tuple[int, int]]:
    """Corrects up to 3 errors; heavier patterns give None when detectable."""
    return _G20.decode(word20)
