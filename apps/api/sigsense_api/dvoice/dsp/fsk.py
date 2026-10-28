"""4FSK / C4FM / GMSK symbol recovery from discriminator samples (CONTRACT v1). Owner: WP-B.

Pipeline per channel (all float64, one sequential numba kernel, state carried between blocks so
the symbols are identical whatever the block boundaries):

    discriminator samples (Hz) -> matched filter (RRC / Gaussian) -> cubic-Lagrange interpolator
        -> timing-error detector (Gardner, or Mueller-Muller decision-directed)
        -> PI loop filter steering the symbol clock -> adaptive slicer -> dibits + soft levels

The loop filter has a proportional path (phase) and an integral path (symbol-clock offset in
parts per one; +/-0.5% range), so a constant clock error such as +/-100 ppm is absorbed in the
integrator. Carrier offset appears as DC at the discriminator output and is removed by the
slicer's tracked centre (reported as `freq_error_hz`).
"""

from __future__ import annotations

import numpy as np

from sigsense_api.dvoice.dsp._jit import njit
from sigsense_api.dvoice.dsp.filters import gaussian_taps, rrc_taps
from sigsense_api.dvoice.dsp.slicer import (
    ST_A,
    ST_C,
    ST_ERR2,
    ST_LOCKED,
    AdaptiveSlicer,
    fit_sync,
    fit_sync_raw,
    new_state,
    slicer_step,
)
from sigsense_api.dvoice.dsp.types import SymbolBlock
from sigsense_api.dvoice.events import Modulation

# timing state layout
TS_TN = 0  # next symbol-centre position in samples, relative to the start of the filtered buffer
TS_YPREV = 1  # previous symbol-centre value (input units)
TS_FREQ = 2  # integrator: fractional symbol-period error
TS_HAVE = 3  # 1 once y_prev is valid
TS_DPREV = 4  # previous decided level (input units), for Mueller-Muller
TS_SIZE = 6

TED_GARDNER = 0
TED_MUELLER = 1
MAX_FREQ = 0.005  # +/-5000 ppm clock error


@njit
def _fir_real(buf, n_hist, taps, out):
    n_taps = taps.shape[0]
    for i in range(out.shape[0]):
        top = n_hist + i
        s = 0.0
        for k in range(n_taps):
            s += taps[k] * buf[top - k]
        out[i] = s


@njit
def _interp(f, t):
    """4-point cubic Lagrange at fractional index t (needs f[i-1..i+2])."""
    i = int(np.floor(t))
    u = t - i
    xm1 = f[i - 1]
    x0 = f[i]
    x1 = f[i + 1]
    x2 = f[i + 2]
    return (
        -u * (u - 1.0) * (u - 2.0) / 6.0 * xm1
        + (u + 1.0) * (u - 1.0) * (u - 2.0) / 2.0 * x0
        - (u + 1.0) * u * (u - 2.0) / 2.0 * x1
        + (u + 1.0) * u * (u - 1.0) / 6.0 * x2
    )


@njit
def _recover(f, n_valid, ts, sl, sps, ted, kp, ki, two_level, out_d, out_v, out_raw):
    """Produce symbols from the filtered buffer `f[:n_valid]`. Returns the number of symbols."""
    n_out = 0
    max_out = out_d.shape[0]
    while n_out < max_out:
        tn = ts[TS_TN]
        period = sps * (1.0 + ts[TS_FREQ])
        i = int(np.floor(tn))
        if i + 2 > n_valid - 1:
            break
        tm = tn - 0.5 * period
        if int(np.floor(tm)) - 1 < 0:
            # not enough history behind the mid-sample yet: skip ahead one period
            ts[TS_TN] = tn + period
            continue
        y = _interp(f, tn)
        ym = _interp(f, tm)
        c = sl[ST_C]
        a = sl[ST_A]
        d, v = slicer_step(y, sl, two_level)
        out_d[n_out] = d
        out_v[n_out] = v
        out_raw[n_out] = y
        n_out += 1
        if ts[TS_HAVE] != 0.0 and sl[ST_LOCKED] != 0.0:
            scale = 9.0 / (a * a)
            if ted == 0:  # Gardner: (y_k - y_{k-1}) * y_mid, all centred
                e = (y - ts[TS_YPREV]) * (ym - c) * scale / 2.7
            else:  # Mueller-Muller: y_{k-1} * dec_k - y_k * dec_{k-1}
                dec = c + a * (3.0 if v > 2.0 else (1.0 if v > 0.0 else (-1.0 if v > -2.0 else -3.0))) / 3.0
                if two_level:
                    dec = c + a * (1.0 if v > 0.0 else -1.0)
                e = ((ts[TS_YPREV] - c) * (dec - c) - (y - c) * (ts[TS_DPREV] - c)) * scale / 12.0
            if e > 2.0:
                e = 2.0
            elif e < -2.0:
                e = -2.0
            ts[TS_FREQ] -= ki * e  # same direction as the proportional term: early (e < 0) -> longer period
            if ts[TS_FREQ] > MAX_FREQ:
                ts[TS_FREQ] = MAX_FREQ
            elif ts[TS_FREQ] < -MAX_FREQ:
                ts[TS_FREQ] = -MAX_FREQ
            ts[TS_TN] = tn + period - sps * kp * e
        else:
            # until the slicer has a level estimate the timing error is meaningless: free-run
            ts[TS_TN] = tn + period
        # decided level for the next Mueller-Muller step
        if two_level:
            ts[TS_DPREV] = c + a * (1.0 if v > 0.0 else -1.0)
        else:
            ts[TS_DPREV] = c + a * (3.0 if v > 2.0 else (1.0 if v > 0.0 else (-1.0 if v > -2.0 else -3.0))) / 3.0
        ts[TS_YPREV] = y
        ts[TS_HAVE] = 1.0
    return n_out


class FskSymbolRecovery:
    """Matched filter -> timing recovery (Gardner / Mueller-Muller, selectable) -> levels.

    Input: float32 discriminator samples at `sample_rate` (Hz deviation).
    Output: `SymbolBlock` with soft levels (nominal -3..+3, sign = polarity) and dibits
    from the adaptive slicer, re-centred whenever a sync word is found
    (`recalibrate_from_sync`). Tolerates +/-1.5 kHz carrier offset (DC at the discriminator)
    and +/-100 ppm symbol-clock error.

    `timing` selects the detector. The default is Mueller-Muller (decision directed): on 4-level
    data it has far less self-noise than Gardner, so at 20 dB in a 12.5 kHz channel it makes
    ~0.2% symbol errors against 0.3-0.5% for Gardner, and it does not care about the loop
    bandwidth (Gardner unlocks above ~0.004). `loop_bw` is the timing loop's noise bandwidth
    as a fraction of the symbol rate.
    """

    def __init__(
        self,
        symbol_rate: float,
        sample_rate: float = 48_000.0,
        levels: int = 4,
        modulation: Modulation = Modulation.C4FM,
        timing: str = "mueller",
        loop_bw: float = 0.003,
        rolloff: float = 0.2,
    ) -> None:
        if timing not in ("gardner", "mueller"):
            raise ValueError("timing must be 'gardner' or 'mueller'")
        if levels not in (2, 4):
            raise ValueError("levels must be 2 or 4")
        self.symbol_rate = float(symbol_rate)
        self.sample_rate = float(sample_rate)
        self.sps = self.sample_rate / self.symbol_rate
        self.levels = levels
        self.modulation = modulation
        self.ted = TED_GARDNER if timing == "gardner" else TED_MUELLER
        if modulation == Modulation.GMSK:
            self._taps = gaussian_taps(self.sps, 0.5, 4)
        else:
            self._taps = rrc_taps(self.sps, rolloff, 8)
        self.delay_samples = (len(self._taps) - 1) / 2.0
        # The matched filter has unit ENERGY (so the symbol levels pass with unit gain) but a DC gain
        # of sum(taps) ~ 3 for RRC: a carrier offset (DC at the discriminator) is amplified by that
        # much relative to the levels, so the slicer's centre must be divided by it to be in Hz.
        self._dc_gain = float(np.sum(self._taps))
        # Second-order loop gains (zeta 0.707) on the normalised timing error. The detector
        # gain depends on the pulse shape and level; these constants were tuned on the
        # loopback simulations in tests/test_dvoice_dsp_symbols.py.
        zeta = 0.707
        theta = loop_bw / (zeta + 1.0 / (4.0 * zeta))
        den = 1.0 + 2.0 * zeta * theta + theta * theta
        self._kp = 4.0 * zeta * theta / den
        self._ki = 4.0 * theta * theta / den
        self._inverted = False
        self.reset()

    # ---- state -------------------------------------------------------------------------
    def reset(self) -> None:
        self._hist = np.zeros(len(self._taps) - 1, dtype=np.float64)
        self._fbuf = np.zeros(0, dtype=np.float64)  # filtered samples kept for interpolation
        self._ts = np.zeros(TS_SIZE, dtype=np.float64)
        self._ts[TS_TN] = 2.0 * self.sps  # first centre; the loop pulls it in
        self._sl = new_state()
        self._n_symbols = 0
        self._raw = np.zeros(0, dtype=np.float64)  # recent raw symbol values, for recalibrate_at
        self._raw_first = 0  # absolute symbol index of self._raw[0]

    @property
    def freq_error_hz(self) -> float:
        """Carrier offset the slicer currently sees, in Hz at the discriminator input."""
        return float(self._sl[ST_C]) / self._dc_gain

    def set_polarity_inverted(self, inverted: bool) -> None:
        """Output the inverse polarity from now on (engine calls this on an inverted SyncHit)."""
        self._inverted = bool(inverted)

    # ---- sync feedback -----------------------------------------------------------------
    def recalibrate_from_sync(self, symbol_levels: np.ndarray, expected_dibits: np.ndarray) -> None:
        """Re-centre the slicer thresholds and gain from a recognised sync word.

        `symbol_levels` are the `soft` values the slicer produced for the sync word (nominal
        -3..+3, as in `SymbolBlock.soft`, i.e. BEFORE polarity un-inversion) and
        `expected_dibits` the word's true dibits in that same (received) polarity."""
        fit_sync(self._sl, symbol_levels, expected_dibits, self.levels == 2)

    def recalibrate_at(self, first_index: int, expected_dibits: np.ndarray) -> bool:
        """Exact re-centre from the raw values of the sync word that started at absolute symbol
        index `first_index` (`SyncHit.index`), in the polarity it was RECEIVED (undo any
        `set_polarity_inverted` first: pass `expected_dibits ^ 2` for an inverted hit).
        Returns False if those symbols are no longer buffered (last 8192) or carry no level
        information. Preferred over `recalibrate_from_sync`, which assumes the slicer state is
        unchanged since the word went by."""
        n = len(expected_dibits)
        lo = first_index - self._raw_first
        if lo < 0 or lo + n > len(self._raw):
            return False
        return fit_sync_raw(self._sl, self._raw[lo : lo + n], expected_dibits, self.levels == 2)

    # ---- processing --------------------------------------------------------------------
    def process(self, disc: np.ndarray) -> SymbolBlock:
        x = np.ascontiguousarray(disc, dtype=np.float64)
        first_index = self._n_symbols
        if x.size == 0:
            return self._block(np.zeros(0, np.uint8), np.zeros(0, np.float64), first_index)
        joined = np.concatenate((self._hist, x))
        filt = np.empty(x.size, dtype=np.float64)
        _fir_real(joined, self._hist.size, self._taps, filt)
        self._hist = joined[-(len(self._taps) - 1) :].copy()

        f = np.concatenate((self._fbuf, filt))
        max_out = int(x.size / self.sps * 1.05) + 8
        out_d = np.empty(max_out, dtype=np.int64)
        out_v = np.empty(max_out, dtype=np.float64)
        out_raw = np.empty(max_out, dtype=np.float64)
        n = _recover(
            f, f.size, self._ts, self._sl, self.sps, self.ted, self._kp, self._ki, self.levels == 2, out_d, out_v, out_raw
        )
        # keep what the next interpolation can still need: one period + the cubic's reach
        # never trim more than exists: with tiny blocks the pointer would otherwise run ahead of the data
        keep_from = min(max(0, int(np.floor(self._ts[TS_TN] - self.sps * 1.6)) - 2), f.size)
        self._ts[TS_TN] -= keep_from
        self._fbuf = f[keep_from:].copy()
        self._n_symbols += n
        self._raw = np.concatenate((self._raw, out_raw[:n]))
        if len(self._raw) > 8192:
            drop = len(self._raw) - 8192
            self._raw = self._raw[drop:]
            self._raw_first += drop
        return self._block(out_d[:n].astype(np.uint8), out_v[:n], first_index)

    def _block(self, dibits: np.ndarray, soft: np.ndarray, first_index: int) -> SymbolBlock:
        soft32 = soft.astype(np.float32)
        if self._inverted:
            dibits = (dibits ^ 2).astype(np.uint8)
            soft32 = -soft32
        c, a = float(self._sl[ST_C]), float(self._sl[ST_A])
        err2 = float(self._sl[ST_ERR2])
        # EVM-style SNR of the decision error against the mean level power (mean e^2 = 5 for 4-level, 9 for 2-level)
        mean_e2 = 9.0 if self.levels == 2 else 5.0
        snr = 10.0 * np.log10(max(mean_e2 * (a / 3.0) ** 2, 1e-12) / max(err2, 1e-12))
        return SymbolBlock(
            dibits=dibits,
            soft=soft32,
            symbol_rate=self.symbol_rate,
            first_index=first_index,
            t0=first_index / self.symbol_rate,
            modulation=self.modulation,
            polarity_inverted=self._inverted,
            meta={"freq_error_hz": c / self._dc_gain, "level": a, "snr_db": float(snr), "timing_ppm": float(self._ts[TS_FREQ]) * 1e6},
        )
