"""Reed-Solomon codes over GF(2^m) (CONTRACT v1). Owner: WP-A. Original code.

One generic implementation (syndromes, errors-and-erasures Berlekamp-Massey via
modified syndromes, Chien search, Forney) plus named instances per air
interface. Symbols are ints (P25: 6-bit hex-bits; DMR: bytes).

Conventions: generator g(x) = prod_{i=0}^{R-1} (x - beta^(fcr+i)) with beta =
alpha^prim, R = n-k. Codewords are systematic: k message symbols first, R parity
symbols last; index 0 is the highest-degree coefficient. Shortened codes are
handled by treating the missing leading symbols as zero.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from sigsense_api.dvoice.fec._gf import GaloisField


class ReedSolomon:
    """RS(n, k) over GF(2^m) with primitive polynomial `prim_poly`, first root `fcr`."""

    def __init__(self, m: int, prim_poly: int, n: int, k: int, fcr: int = 1, prim: int = 1) -> None:
        self.m, self.prim_poly, self.n, self.k, self.fcr, self.prim = m, prim_poly, n, k, fcr, prim
        if not 0 < k < n <= (1 << m) - 1:
            raise ValueError("need 0 < k < n <= 2^m - 1")
        self.gf = GaloisField(m, prim_poly)
        self.nroots = n - k
        gf = self.gf
        # g(x), low degree first.
        g = [1]
        for i in range(self.nroots):
            root = gf.pow_alpha(prim * (fcr + i))
            nxt = [0] * (len(g) + 1)
            for j, c in enumerate(g):
                nxt[j + 1] ^= c               # x * g
                nxt[j] ^= gf.mul(c, root)     # root * g  (char 2: minus == plus)
            g = nxt
        self.generator = g

    # ---- encoding -------------------------------------------------------
    def encode(self, message: Sequence[int]) -> np.ndarray:
        """k message symbols -> n-symbol systematic codeword (message first, parity last)."""
        msg = [int(x) for x in message]
        if len(msg) != self.k:
            raise ValueError(f"need {self.k} message symbols")
        gf, r = self.gf, self.nroots
        # LFSR division of m(x) x^R by g(x): parity register, highest degree first.
        reg = [0] * r
        g = self.generator
        for sym in msg:
            feedback = sym ^ reg[0]
            reg = reg[1:] + [0]
            if feedback:
                for j in range(r):
                    reg[j] ^= gf.mul(feedback, g[r - 1 - j])
        return np.array(msg + reg, dtype=np.int64)

    # ---- decoding -------------------------------------------------------
    def _syndromes(self, word: list[int]) -> list[int]:
        gf = self.gf
        out = []
        for j in range(self.nroots):
            x = gf.pow_alpha(self.prim * (self.fcr + j))
            acc = 0
            for sym in word:  # Horner, highest degree first
                acc = gf.mul(acc, x) ^ sym
            out.append(acc)
        return out

    def decode(
        self, codeword: Sequence[int], erasures: Optional[Sequence[int]] = None
    ) -> Optional[tuple[np.ndarray, int]]:
        """Returns (corrected_codeword, symbols_corrected) or None if uncorrectable.

        `erasures` are indices (0 = first symbol) known to be unreliable; up to R of
        them are allowed, and 2*errors + erasures <= R overall. symbols_corrected is
        the number of positions whose value changed."""
        gf, r, n = self.gf, self.nroots, self.n
        word = [int(x) for x in codeword]
        if len(word) != n:
            raise ValueError(f"need {n} symbols")
        if any(not 0 <= x <= gf.order for x in word):
            raise ValueError("symbol out of range")
        eras = sorted(set(int(e) for e in (erasures or [])))
        if any(not 0 <= e < n for e in eras):
            raise ValueError("erasure index out of range")
        if len(eras) > r:
            return None
        synd = self._syndromes(word)
        if not any(synd):
            return np.array(word, dtype=np.int64), 0

        beta_pow = lambda p: gf.pow_alpha(self.prim * p)  # beta^p
        # Erasure locator Gamma(x) = prod (1 + X_e x), low degree first.
        gamma = [1]
        for e in eras:
            x_e = beta_pow(n - 1 - e)
            nxt = [0] * (len(gamma) + 1)
            for j, c in enumerate(gamma):
                nxt[j] ^= c
                nxt[j + 1] ^= gf.mul(c, x_e)
            gamma = nxt
        ne = len(eras)
        # Modified (Forney) syndromes T_i = sum_j Gamma_j S_{i-j}, i = ne..R-1.
        modified = []
        for i in range(ne, r):
            acc = 0
            for j in range(ne + 1):
                acc ^= gf.mul(gamma[j], synd[i - j])
            modified.append(acc)
        sigma = self._berlekamp_massey(modified)
        nu = len(sigma) - 1
        if 2 * nu + ne > r:
            return None
        # Total locator Psi = sigma * Gamma.
        psi = [0] * (len(sigma) + len(gamma) - 1)
        for i, a in enumerate(sigma):
            if a:
                for j, b in enumerate(gamma):
                    psi[i + j] ^= gf.mul(a, b)
        deg = len(psi) - 1
        while deg > 0 and psi[deg] == 0:
            deg -= 1
        psi = psi[: deg + 1]
        # Chien search over the n valid positions: root at X^-1.
        positions = []
        for idx in range(n):
            x_inv = beta_pow(-(n - 1 - idx))
            acc = 0
            xp = 1
            for c in psi:
                acc ^= gf.mul(c, xp)
                xp = gf.mul(xp, x_inv)
            if acc == 0:
                positions.append(idx)
        if len(positions) != deg:
            return None
        # Evaluator Omega = S(x) Psi(x) mod x^R.
        omega = [0] * r
        for i, a in enumerate(psi):
            if a:
                for j in range(r - i):
                    omega[i + j] ^= gf.mul(a, synd[j])
        corrected = list(word)
        for idx in positions:
            x = beta_pow(n - 1 - idx)
            x_inv = gf.inv(x)
            num = 0
            xp = 1
            for c in omega:
                num ^= gf.mul(c, xp)
                xp = gf.mul(xp, x_inv)
            # Psi'(x) in char 2: only odd-degree terms survive.
            den = 0
            for i in range(1, len(psi), 2):
                den ^= gf.mul(psi[i], self._pow(x_inv, i - 1))
            if den == 0:
                return None
            # Forney with first root `fcr`: magnitude = X^(1 - fcr) * Omega(X^-1) / Psi'(X^-1).
            magnitude = gf.mul(gf.div(num, den), beta_pow((n - 1 - idx) * (1 - self.fcr)))
            corrected[idx] ^= magnitude
        if any(self._syndromes(corrected)):
            return None
        changed = sum(1 for a, b in zip(word, corrected) if a != b)
        return np.array(corrected, dtype=np.int64), changed

    def _pow(self, x: int, e: int) -> int:
        gf = self.gf
        if e == 0:
            return 1
        if x == 0:
            return 0
        return gf.exp[(gf.log[x] * e) % gf.order]

    def _berlekamp_massey(self, synd: list[int]) -> list[int]:
        """Locator polynomial (low degree first) for the syndrome sequence."""
        gf = self.gf
        c, b = [1], [1]
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
            previous = c[:]
            shifted = [0] * m + [gf.mul(coef, x) for x in b]
            if len(c) < len(shifted):
                c = c + [0] * (len(shifted) - len(c))
            for i, v in enumerate(shifted):
                c[i] ^= v
            if 2 * ell <= n:
                ell = n + 1 - ell
                b = previous
                bb = d
                m = 1
            else:
                m += 1
        c = c[: ell + 1] + [0] * max(0, ell + 1 - len(c))
        return c


def rs_p25_24_12_13() -> ReedSolomon:
    """P25 link control word: RS(24,12,13) over GF(2^6) (TIA-102.BAAA-A 5.9)."""
    return ReedSolomon(6, 0x43, 24, 12, fcr=1, prim=1)


def rs_p25_24_16_9() -> ReedSolomon:
    """P25 encryption sync word: RS(24,16,9) over GF(2^6)."""
    return ReedSolomon(6, 0x43, 24, 16, fcr=1, prim=1)


def rs_p25_36_20_17() -> ReedSolomon:
    """P25 header data unit: RS(36,20,17) over GF(2^6)."""
    return ReedSolomon(6, 0x43, 36, 20, fcr=1, prim=1)


def rs_dmr_12_9_4() -> ReedSolomon:
    """DMR link control / terminator: RS(12,9,4) over GF(2^8), g=(x+a)(x+a^2)(x+a^3)
    (ETSI TS 102 361-1 B.3.6; the data-type CRC mask is applied by WP-D)."""
    return ReedSolomon(8, 0x11D, 12, 9, fcr=1, prim=1)
