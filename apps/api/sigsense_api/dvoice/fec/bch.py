"""BCH / QR block codes (CONTRACT v1). Owner: WP-A. Original code.

* P25 NID: BCH(63,16,23) over GF(2^6) (primitive polynomial x^6+x+1, the field
  TIA-102.BAAA-A uses for its Reed-Solomon codes) with the generator polynomial
  printed in 8.5.2, plus a 64th bit. That bit is NOT an overall parity of the word:
  in the spec's generator matrix (8.5.2) and Table 8-4 it equals S1 xor S0 of the
  data-unit ID, independent of the NAC (re-derived from all 16 matrix rows in the
  tests). Decoded algebraically (syndromes, Berlekamp-Massey, Chien search): up to
  11 errors are corrected.
* QR(16,7,6): ETSI TS 102 361-1 clause B.3.2 generator matrix.
* BCH(15,7,5): the standard narrow-sense code, generator x^8+x^7+x^6+x^4+1 (its use
  in NXDN/D-STAR fields is NOT confirmed here; WP-E must confirm against its spec).
"""

from __future__ import annotations

from typing import Optional

from sigsense_api.dvoice.fec import _specdata as spec
from sigsense_api.dvoice.fec._gf import GaloisField, gf2_poly_mod
from sigsense_api.dvoice.fec._linear import SystematicCode, cyclic_parity_rows, rows_from_matrix

_GF64 = GaloisField(6, 0x43)
_NID_G = int(spec.P25_NID_GENERATOR_OCTAL, 8)  # degree 47
_NID_N, _NID_K, _NID_T = 63, 16, 11


def _nid_last_bit(duid4: int) -> int:
    """64th NID bit as defined by the spec's generator matrix: S1 xor S0."""
    return ((duid4 >> 1) ^ duid4) & 1


def p25_nid_encode(nac12: int, duid4: int) -> int:
    """64-bit P25 NID: BCH(63,16,23) over (NAC<<4 | DUID) plus the 64th bit (S1 xor S0)."""
    data = ((nac12 & 0xFFF) << 4) | (duid4 & 0xF)
    parity47 = gf2_poly_mod(data << 47, _NID_G)
    word63 = (data << 47) | parity47
    return (word63 << 1) | _nid_last_bit(duid4 & 0xF)


def _syndromes(word63: int) -> list[int]:
    """S_j = r(alpha^j), j = 1..2t, r(x) with the first transmitted bit as x^62."""
    gf = _GF64
    synd = []
    for j in range(1, 2 * _NID_T + 1):
        acc = 0
        for i in range(_NID_N):
            if (word63 >> i) & 1:
                acc ^= gf.exp[(j * i) % gf.order]
        synd.append(acc)
    return synd


def _berlekamp_massey(synd: list[int]) -> list[int]:
    """Error-locator polynomial coefficients, lowest degree first."""
    gf = _GF64
    c = [1]
    b = [1]
    ell, m, bb = 0, 1, 1
    for n in range(len(synd)):
        d = synd[n]
        for i in range(1, ell + 1):
            if i < len(c):
                d ^= gf.mul(c[i], synd[n - i])
        if d == 0:
            m += 1
            continue
        coef = gf.div(d, bb)
        t_poly = c[:]
        shifted = [0] * m + [gf.mul(coef, x) for x in b]
        if len(c) < len(shifted):
            c = c + [0] * (len(shifted) - len(c))
        for i, v in enumerate(shifted):
            c[i] ^= v
        if 2 * ell <= n:
            ell = n + 1 - ell
            b = t_poly
            bb = d
            m = 1
        else:
            m += 1
    return c[: ell + 1] if len(c) >= ell + 1 else c + [0] * (ell + 1 - len(c))


def p25_nid_decode(word64: int) -> Optional[tuple[int, int, int]]:
    """Decode a received 64-bit NID. Returns (nac12, duid4, corrected) or None.

    Corrects up to 11 bit errors in the 63 BCH bits. The 64th bit must agree with
    the decoded data-unit ID (it counts as one more error when it does not), and
    totals above 11 are rejected, which catches part of the 12+-error patterns
    that would otherwise decode to a wrong codeword."""
    word64 &= (1 << 64) - 1
    received_last = word64 & 1
    word63 = word64 >> 1
    if gf2_poly_mod(word63, _NID_G) == 0:
        # A codeword: only the 64th bit can be wrong.
        data = word63 >> 47
        errors = 0 if _nid_last_bit(data & 0xF) == received_last else 1
        return (data >> 4, data & 0xF, errors)
    synd = _syndromes(word63)
    if not any(synd):
        return None  # cannot happen for a non-codeword; defensive
    locator = _berlekamp_massey(synd)
    degree = len(locator) - 1
    while degree > 0 and locator[degree] == 0:
        degree -= 1
    if degree == 0 or degree > _NID_T:
        return None
    gf = _GF64
    corrected = word63
    roots = 0
    # Chien search: position i (x^i) is in error when Lambda(alpha^-i) == 0.
    for i in range(_NID_N):
        acc = 0
        for d in range(degree + 1):
            if locator[d]:
                acc ^= gf.exp[(gf.log[locator[d]] + d * (gf.order - i)) % gf.order]
        if acc == 0:
            corrected ^= 1 << i
            roots += 1
    if roots != degree or gf2_poly_mod(corrected, _NID_G) != 0:
        return None
    data = corrected >> 47
    total = roots + (0 if _nid_last_bit(data & 0xF) == received_last else 1)
    if total > _NID_T:
        return None  # 12+ errors: reject rather than risk a miscorrection
    return (data >> 4, data & 0xF, total)


_QR = SystematicCode(7, 9, rows_from_matrix(spec.DMR_QR_16_7_6, 7), t=2, name="qr16_7_6")


def qr_16_7_6_encode(data7: int) -> int:
    """Quadratic-residue (16,7,6) used by DMR embedded signalling / CACH-adjacent fields."""
    return _QR.encode(data7)


def qr_16_7_6_decode(word16: int) -> Optional[tuple[int, int]]:
    """Corrects up to 2 errors, detects 3."""
    return _QR.decode(word16)


_BCH157 = SystematicCode(7, 8, cyclic_parity_rows(7, 8, 0x1D1), t=2, name="bch15_7_5")


def bch_15_7_5_encode(data7: int) -> int:
    return _BCH157.encode(data7)


def bch_15_7_5_decode(word15: int) -> Optional[tuple[int, int]]:
    """BCH(15,7,5) used by NXDN LICH / D-STAR fields where applicable (WP-E confirms)."""
    return _BCH157.decode(word15)
