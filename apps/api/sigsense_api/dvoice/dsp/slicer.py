"""Adaptive slicer (CONTRACT v1). Owner: WP-B.

Maps raw symbol values (any unit, e.g. Hz of deviation) to dibits and to normalised
"soft" levels (nominal -3..+3) using a centre `c` and an outer-level amplitude `a`:

    v = 3 * (x - c) / a        4-level thresholds at v = 0, +/-2;  2-level: threshold v = 0

State (`st`, float64) follows the signal:
* blind start: robust percentile estimates over the first symbols (preambles are often
  outer-symbol-only, so this is only a starting point);
* then slow moment estimates keep c (mean) and a (mean absolute deviation) on the signal
  (handle +/-20% deviation error and carrier/DC offset, which land on `c`);
* `update_from_sync` replaces both by a least-squares fit to a recognised sync word, which
  has known levels, so a sync word re-centres the slicer exactly.
"""

from __future__ import annotations

import numpy as np

from sigsense_api.dvoice.dsp._jit import njit

# --- state layout --------------------------------------------------------------------------
ST_C = 0  # centre (input units)
ST_A = 1  # outer-level amplitude (input units): the +/-3 symbols sit at c +/- a
ST_HI = 2  # (unused; kept so the layout is stable)
ST_LO = 3
ST_COUNT = 4  # symbols seen
ST_LOCKED = 5  # 1 once c/a came from a sync fit or the warm-up ended
ST_ERR2 = 6  # running mean of squared decision error (input units^2), for SNR estimates
ST_BUF = 8  # first WARMUP_SYMBOLS raw values (for the robust blind start)
WARMUP_SYMBOLS = 48
ST_SIZE = ST_BUF + WARMUP_SYMBOLS
MU_C = 0.002  # steady-state step for the centre (moment based, ~500 symbols; see fsk.py notes)
MU_A = 0.002  # step for the moment-based amplitude tracker (~500 symbols)

# dibit value for each decided level index: v>2 -> +3 -> 1, 0<v<=2 -> +1 -> 0, -2<v<=0 -> -1 -> 2, v<=-2 -> -3 -> 3
_LEVEL_E = np.array([3.0, 1.0, -1.0, -3.0])
_DIBIT_OF_LEVEL_IDX = np.array([1, 0, 2, 3], dtype=np.int64)


def new_state() -> np.ndarray:
    st = np.zeros(ST_SIZE, dtype=np.float64)
    st[ST_A] = 1.0
    return st


@njit
def _blind_estimate(st, n):
    """Robust (c, a) from the first `n` raw values: c from the 10th/90th percentiles, a from
    the 75th percentile of |x - c| (for a balanced 4-level stream the top half of |x - c| are the
    outer symbols). Click noise and filter start-up spikes only touch the extreme tail."""
    vals = np.sort(st[ST_BUF : ST_BUF + n])
    lo = vals[int(0.1 * (n - 1))]
    hi = vals[int(0.9 * (n - 1))]
    c = 0.5 * (lo + hi)
    dev = np.sort(np.abs(st[ST_BUF : ST_BUF + n] - c))
    a = dev[int(0.75 * (n - 1))]
    if a < 1e-9:
        a = 1e-9
    return c, a


@njit
def slicer_step(x, st, two_level):
    """Decide one symbol and adapt. Returns (dibit, soft v). Mutates `st`."""
    n = st[ST_COUNT]
    ni = int(n)
    if ni < WARMUP_SYMBOLS:
        st[ST_BUF + ni] = x
        if ni >= 2:
            c0, a0 = _blind_estimate(st, ni + 1)
            st[ST_C] = c0
            st[ST_A] = a0
        else:
            st[ST_C] = x
            st[ST_A] = max(abs(x), 1.0)
    elif st[ST_LOCKED] == 0.0:
        st[ST_LOCKED] = 1.0
    c = st[ST_C]
    a = st[ST_A]
    v = 3.0 * (x - c) / a
    if two_level:
        if v > 0.0:
            d = 1
            e = 3.0
        else:
            d = 3
            e = -3.0
    else:
        if v > 2.0:
            d = 1
            e = 3.0
        elif v > 0.0:
            d = 0
            e = 1.0
        elif v > -2.0:
            d = 2
            e = -1.0
        else:
            d = 3
            e = -3.0
    err = x - (c + a * e / 3.0)
    st[ST_ERR2] += 0.01 * (err * err - st[ST_ERR2])
    if st[ST_LOCKED] != 0.0:
        # Both level parameters follow MOMENTS of the symbol values, never the decisions. For
        # equiprobable levels E[x] = c and E|x - c| = 2a/3 (4-level) or a (2-level). Decision-directed
        # tracking has false fixed points (a at 2x the true level, or c shifted by a fraction of the
        # level spacing, with every decision still "consistent"), which a start-up transient or a
        # carrier-offset step can land in. Moments have no such trap. They are biased by unbalanced
        # data (outer-only sync words), hence the slow steady-state rate; a recognised sync word
        # re-fits both exactly (`fit_sync`).
        k = n - WARMUP_SYMBOLS + 24.0
        mu_c = MU_C if MU_C > 1.0 / k else 1.0 / k
        mu_a = MU_A if MU_A > 1.0 / k else 1.0 / k
        dx = x - c
        lim = 1.5 * a
        if dx > lim:
            dx = lim
        elif dx < -lim:
            dx = -lim
        st[ST_C] = c + mu_c * dx
        dev = abs(x - c)
        if dev > lim:
            dev = lim
        a_moment = dev if two_level else 1.5 * dev
        a_new = a + mu_a * (a_moment - a)
        st[ST_A] = a_new if a_new > 1e-9 else 1e-9
    st[ST_COUNT] = n + 1.0
    return d, v


@njit
def _slice_array(values, st, two_level, out_d, out_v):
    for i in range(values.shape[0]):
        d, v = slicer_step(values[i], st, two_level)
        out_d[i] = d
        out_v[i] = v


def fit_sync(st: np.ndarray, soft: np.ndarray, expected_dibits: np.ndarray, two_level: bool) -> bool:
    """Least-squares re-centre from a recognised sync word.

    `soft` are the normalised values the slicer produced for the word (nominal -3..+3, as in
    `SymbolBlock.soft`); `expected_dibits` are the word's true dibits. Returns False when the
    word carries no level information (e.g. a single repeated symbol)."""
    soft = np.asarray(soft, dtype=np.float64)
    e = np.where(np.asarray(expected_dibits) == 1, 3.0, np.where(np.asarray(expected_dibits) == 0, 1.0,
                 np.where(np.asarray(expected_dibits) == 2, -1.0, -3.0)))
    if two_level:
        e = np.where((np.asarray(expected_dibits) & 2) == 0, 3.0, -3.0)
    if len(soft) < 4 or np.var(e) < 1e-9:
        return False
    beta = float(np.cov(soft, e, bias=True)[0, 1] / np.var(e))
    alpha = float(soft.mean() - beta * e.mean())
    if not np.isfinite(beta) or beta < 0.2 or beta > 5.0:
        return False
    a = st[ST_A]
    st[ST_C] += a / 3.0 * alpha
    st[ST_A] = max(a * beta, 1e-9)
    st[ST_HI] = st[ST_C] + st[ST_A]
    st[ST_LO] = st[ST_C] - st[ST_A]
    st[ST_LOCKED] = 1.0
    return True


def fit_sync_raw(st: np.ndarray, raw: np.ndarray, expected_dibits: np.ndarray, two_level: bool) -> bool:
    """Exact least-squares re-centre from the RAW symbol values of a recognised sync word
    (x = c + (a/3) e). Unlike `fit_sync` it does not depend on the slicer state that was in
    effect when the word went by, so it is correct however long ago the word was processed."""
    x = np.asarray(raw, dtype=np.float64)
    d = np.asarray(expected_dibits)
    e = np.where((d & 2) == 0, 3.0, -3.0) if two_level else np.where(d == 1, 3.0, np.where(d == 0, 1.0, np.where(d == 2, -1.0, -3.0)))
    if len(x) < 4 or len(x) != len(e) or np.var(e) < 1e-9:
        return False
    slope = float(np.cov(x, e, bias=True)[0, 1] / np.var(e))  # = a / 3
    c = float(x.mean() - slope * e.mean())
    if not np.isfinite(slope) or slope <= 0:
        return False
    st[ST_C] = c
    st[ST_A] = max(3.0 * slope, 1e-9)
    st[ST_HI] = c + st[ST_A]
    st[ST_LO] = c - st[ST_A]
    st[ST_LOCKED] = 1.0
    return True


class AdaptiveSlicer:
    """Maps normalized soft levels to dibits using thresholds that follow the signal.

    `slice` returns (dibits uint8, soft float32). Thresholds update from sync
    words (`update_from_sync`) and slowly from decision-directed statistics, so
    deviation errors and DC offsets do not accumulate BER.
    """

    def __init__(self, levels: int = 4) -> None:
        if levels not in (2, 4):
            raise ValueError("levels must be 2 or 4")
        self.levels = levels
        self.st = new_state()

    @property
    def center(self) -> float:
        return float(self.st[ST_C])

    @property
    def amplitude(self) -> float:
        return float(self.st[ST_A])

    def reset(self) -> None:
        self.st = new_state()

    def update_from_sync(self, levels: np.ndarray, expected_dibits: np.ndarray) -> None:
        fit_sync(self.st, levels, expected_dibits, self.levels == 2)

    def slice(self, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x = np.ascontiguousarray(values, dtype=np.float64)
        d = np.empty(x.size, dtype=np.uint8)
        v = np.empty(x.size, dtype=np.float64)
        _slice_array(x, self.st, self.levels == 2, d, v)
        return d, v.astype(np.float32)
