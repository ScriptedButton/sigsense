"""Systematic binary linear block codes with syndrome decoding (private). Original code.

A word is a Python int, MSB = first transmitted bit. For a systematic code
[ data (k bits) | parity (r bits) ], `parity_rows[i]` is the r-bit parity of a
message with only data bit i set (i = 0 is the MSB / first data bit), exactly
the right-hand block of a generator matrix written as [I | P].
"""

from __future__ import annotations

from itertools import combinations
from typing import Optional, Sequence


class SystematicCode:
    def __init__(self, k: int, r: int, parity_rows: Sequence[int], t: int, name: str = "") -> None:
        if len(parity_rows) != k:
            raise ValueError("one parity row per data bit")
        self.k, self.r, self.n = k, r, k + r
        self.rows = [int(p) & ((1 << r) - 1) for p in parity_rows]
        self.t = t
        self.name = name
        # Parity contribution of every data byte-slice, for fast encode.
        self._table = self._build_encode_tables()
        self._syndrome_to_error = self._build_syndrome_table()

    # ---- encoding -------------------------------------------------------
    def _build_encode_tables(self):
        # 4-bit lookup per nibble of data keeps encode O(k/4).
        tables = []
        for base in range(0, self.k, 4):
            width = min(4, self.k - base)
            table = [0] * (1 << width)
            for v in range(1 << width):
                p = 0
                for j in range(width):
                    if (v >> (width - 1 - j)) & 1:
                        p ^= self.rows[base + j]
                table[v] = p
            tables.append((width, table))
        return tables

    def parity_of(self, data: int) -> int:
        p = 0
        shift = self.k
        for width, table in self._table:
            shift -= width
            p ^= table[(data >> shift) & ((1 << width) - 1)]
        return p

    def encode(self, data: int) -> int:
        data &= (1 << self.k) - 1
        return (data << self.r) | self.parity_of(data)

    def syndrome(self, word: int) -> int:
        data = (word >> self.r) & ((1 << self.k) - 1)
        return self.parity_of(data) ^ (word & ((1 << self.r) - 1))

    # ---- decoding -------------------------------------------------------
    def _build_syndrome_table(self) -> dict[int, tuple[int, int]]:
        """syndrome -> (error mask, weight) for every pattern of weight <= t."""
        table: dict[int, tuple[int, int]] = {0: (0, 0)}
        for weight in range(1, self.t + 1):
            for positions in combinations(range(self.n), weight):
                mask = 0
                for pos in positions:
                    mask |= 1 << (self.n - 1 - pos)
                s = self.syndrome(mask)
                if s in table:
                    raise ValueError(f"{self.name}: patterns of weight <= {self.t} are ambiguous")
                table[s] = (mask, weight)
        return table

    def decode(self, word: int) -> Optional[tuple[int, int]]:
        """(data, corrected) when within the correction radius, else None."""
        word &= (1 << self.n) - 1
        hit = self._syndrome_to_error.get(self.syndrome(word))
        if hit is None:
            return None
        mask, weight = hit
        return ((word ^ mask) >> self.r) & ((1 << self.k) - 1), weight

    def codewords(self):
        for data in range(1 << self.k):
            yield self.encode(data)


def cyclic_parity_rows(k: int, r: int, generator: int) -> list[int]:
    """Parity rows of the systematic cyclic code with generator polynomial `generator`
    (degree r): row i = x^(r + k-1-i) mod g(x)."""
    from sigsense_api.dvoice.fec._gf import gf2_poly_mod

    return [gf2_poly_mod(1 << (r + k - 1 - i), generator) for i in range(k)]


def rows_from_matrix(matrix: Sequence[Sequence[int]], k: int) -> list[int]:
    """Parity rows from a generator matrix written [I_k | P] (list of 0/1 rows)."""
    out = []
    for i, row in enumerate(matrix):
        if len(row) <= k:
            raise ValueError("matrix has no parity part")
        if list(row[:k]) != [1 if j == i else 0 for j in range(k)]:
            raise ValueError("matrix is not systematic [I|P]")
        p = 0
        for bit in row[k:]:
            p = (p << 1) | int(bit)
        out.append(p)
    return out
