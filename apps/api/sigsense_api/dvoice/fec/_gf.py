"""GF(2^m) arithmetic tables (private to the FEC package). Original code.

Elements are Python ints / numpy ints whose bits are the polynomial coefficients
in alpha; `prim_poly` includes the x^m term (e.g. 0x43 = x^6 + x + 1).
"""

from __future__ import annotations

import numpy as np


class GaloisField:
    def __init__(self, m: int, prim_poly: int) -> None:
        self.m = m
        self.size = 1 << m
        self.order = self.size - 1  # multiplicative group order
        self.prim_poly = prim_poly
        exp = [0] * (2 * self.order)
        log = [0] * self.size
        x = 1
        for i in range(self.order):
            exp[i] = x
            log[x] = i
            x <<= 1
            if x & self.size:
                x ^= prim_poly
        if x != 1:
            raise ValueError(f"0x{prim_poly:x} is not primitive for GF(2^{m})")
        for i in range(self.order, 2 * self.order):
            exp[i] = exp[i - self.order]
        self.exp = exp
        self.log = log
        self.exp_np = np.array(exp, dtype=np.int64)
        self.log_np = np.array(log, dtype=np.int64)

    def mul(self, a: int, b: int) -> int:
        if a == 0 or b == 0:
            return 0
        return self.exp[self.log[a] + self.log[b]]

    def div(self, a: int, b: int) -> int:
        if b == 0:
            raise ZeroDivisionError
        if a == 0:
            return 0
        return self.exp[self.log[a] - self.log[b] + self.order]

    def inv(self, a: int) -> int:
        if a == 0:
            raise ZeroDivisionError
        return self.exp[self.order - self.log[a]]

    def pow_alpha(self, e: int) -> int:
        return self.exp[e % self.order]

    def poly_eval(self, coeffs_high_first, x: int) -> int:
        """Horner evaluation; coefficients highest degree first."""
        acc = 0
        for c in coeffs_high_first:
            acc = self.mul(acc, x) ^ int(c)
        return acc


def gf2_poly_mod(value: int, poly: int) -> int:
    """Remainder of `value` (as a GF(2) polynomial) modulo `poly`."""
    deg = poly.bit_length() - 1
    while value.bit_length() > deg:
        value ^= poly << (value.bit_length() - 1 - deg)
    return value


def parity(x: int) -> int:
    return bin(x).count("1") & 1
