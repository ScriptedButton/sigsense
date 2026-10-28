"""Interleavers (CONTRACT v1). Owner: WP-A. Original code.

Generic helpers plus the named permutation tables that are defined in the
public specifications I could read (see _specdata for sources).

Convention: out[i] = in[table[i]]  (deinterleave); interleave is the inverse.

Provided tables
  'dmr_bptc196'            ETSI TS 102 361-1 B.1.1: matrix bit n <- received bit (181*n mod 196)
  'dmr_trellis_dibit'      ETSI B.2.4 Table B.9/B.10 (98 dibits), received order -> encoder-output order
  'p25_trellis_dibit'      TIA-102.BAAA-A Table 7-4 (same permutation as DMR's)
  'imbe7200x4400_frame'    TIA-102.BAAA-A Table 5-1: 144 received bits -> codeword order
                           [c0 (23) c1 (23) c2 (23) c3 (23) c4 (15) c5 (15) c6 (15) c7 (7)], MSB first
  'p25_ldu_status'         864-dibit LDU: info dibits first, then the 24 status dibits
                           (one status dibit after every 35 info dibits, TIA-102.BAAA-A 8.2)
  'm17_payload'            M17 spec appendix F: QPP pi(x) = (45x + 92x^2) mod 368, 368 bits
  'dstar_header'           JARL D-STAR spec AP3: the 660-bit header matrix (24 rows, written down
                           the columns, read along the rows)
  'nxdn_cac_outbound' (25x12), 'nxdn_cac_long' (21x12), 'nxdn_cac_short' (21x12),
  'nxdn_sacch' (5x12), 'nxdn_facch1' (9x16), 'nxdn_udch' (29x12)
                           NXDN TS 1-A 4.5: the coded block is written as `rows` rows of `width`
                           bits and read by columns. Verified bit-exact against the TS 2-B
                           conformance vectors (see the tests).

NOT provided (not available in any public document I could read; asking for them
returns NotImplementedError naming the missing source rather than a guess):
  ambe_plus2_3600x2450_frame, ambe3600x2400_frame, dmr_voice_burst  (DVSI AMBE+2 frame format)
  nxdn_voice, dstar_voice, ysf_voice                               (WP-E: confirm with each spec)
"""

from __future__ import annotations

import numpy as np

from sigsense_api.dvoice.fec import _specdata as spec

_IMBE_WORD_BITS = (23, 23, 23, 23, 15, 15, 15, 7)  # c_0 .. c_7
_IMBE_OFFSETS = tuple(int(x) for x in np.cumsum((0,) + _IMBE_WORD_BITS[:-1]))


def _imbe_table() -> np.ndarray:
    """P[j] = received bit index holding codeword-order bit j (bit 1 of symbol s is 2s, bit 0 is 2s+1)."""
    perm = np.full(144, -1, dtype=np.int64)
    for symbol, (bit1, bit0) in enumerate(spec.P25_IMBE_INTERLEAVE):
        for rx_index, (word, bit) in ((2 * symbol, bit1), (2 * symbol + 1, bit0)):
            j = _IMBE_OFFSETS[word] + (_IMBE_WORD_BITS[word] - 1 - bit)
            if perm[j] != -1:
                raise ValueError("IMBE interleave table maps two bits to one position")
            perm[j] = rx_index
    if (perm < 0).any():
        raise ValueError("IMBE interleave table leaves a codeword bit unplaced")
    return perm


def _status_table(n_dibits: int = 864, period: int = 35) -> np.ndarray:
    status = [i for i in range(n_dibits) if i % (period + 1) == period]
    info = [i for i in range(n_dibits) if i % (period + 1) != period]
    return np.array(info + status, dtype=np.int64)


def _trellis_table() -> np.ndarray:
    # Spec: transmitted dibit i carries encoder-output dibit T[i]. Deinterleave perm P with
    # enc[j] = rx[P[j]] is the inverse of T.
    t = np.array(spec.TRELLIS_INTERLEAVE, dtype=np.int64)
    inverse = np.empty_like(t)
    inverse[t] = np.arange(t.size)
    return inverse


def block_permutation(rows: int, cols: int) -> np.ndarray:
    """Row-write / column-read block interleaver permutation (a building block for the tables)."""
    return np.arange(rows * cols).reshape(rows, cols).T.reshape(-1)


def _qpp_368() -> np.ndarray:
    x = np.arange(368, dtype=np.int64)
    return (45 * x + 92 * x * x) % 368


def _dstar_header_table() -> np.ndarray:
    """Receive-side permutation for the 660-bit header interleave (JARL spec AP3)."""
    n, rows = 660, 24
    # transmit order = row-major walk of a matrix whose entry (r, c) is original index r + 24 c
    order = [r + rows * c for r in range(rows) for c in range((n - r + rows - 1) // rows)]
    if sorted(order) != list(range(n)):
        raise ValueError("D-STAR header matrix does not cover 660 bits")
    inverse = np.empty(n, dtype=np.int64)
    inverse[np.array(order)] = np.arange(n)
    return inverse


def _nxdn_table(rows: int, width: int) -> np.ndarray:
    """Receive-side table of the NXDN write-rows / read-columns block interleaver."""
    return block_permutation(width, rows)


TABLES: dict[str, np.ndarray] = {
    "dmr_bptc196": (np.arange(196) * 181) % 196,
    "dmr_trellis_dibit": _trellis_table(),
    "p25_trellis_dibit": _trellis_table(),
    "imbe7200x4400_frame": _imbe_table(),
    "p25_ldu_status": _status_table(),
    "m17_payload": _qpp_368(),
    "dstar_header": _dstar_header_table(),
    "nxdn_cac_outbound": _nxdn_table(25, 12),
    "nxdn_cac_long": _nxdn_table(21, 12),
    "nxdn_cac_short": _nxdn_table(21, 12),
    "nxdn_sacch": _nxdn_table(5, 12),
    "nxdn_facch1": _nxdn_table(9, 16),
    "nxdn_udch": _nxdn_table(29, 12),
}

_UNAVAILABLE = {
    "ambe_plus2_3600x2450_frame": "the AMBE+2 3600x2450 frame format is DVSI-proprietary; no public spec was readable",
    "ambe3600x2400_frame": "the AMBE 3600x2400 frame format is DVSI-proprietary; no public spec was readable",
    "dmr_voice_burst": "DMR voice-burst bit order is the AMBE+2 frame format (DVSI); not in ETSI TS 102 361-1",
    "nxdn_voice": "the NXDN VCH frame bit order is the vocoder (AMBE+2) frame format; not in TS 1-A text",
    "dstar_voice": "D-STAR voice frames carry the AMBE bits as-is (no public interleave table found)",
    "ysf_voice": "no public specification of the Yaesu System Fusion air interface was found",
}


def table(name: str) -> np.ndarray:
    """Named permutation table (see the module docstring for the list and conventions)."""
    try:
        return TABLES[name]
    except KeyError:
        reason = _UNAVAILABLE.get(name)
        if reason is None:
            raise KeyError(f"unknown interleave table {name!r}") from None
        raise NotImplementedError(f"interleave table {name!r} not provided: {reason}") from None


def deinterleave(bits: np.ndarray, perm: np.ndarray) -> np.ndarray:
    return np.asarray(bits)[np.asarray(perm)]


def interleave(bits: np.ndarray, perm: np.ndarray) -> np.ndarray:
    out = np.empty_like(np.asarray(bits))
    out[np.asarray(perm)] = np.asarray(bits)
    return out


