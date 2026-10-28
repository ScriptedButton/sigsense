"""Differential QPSK path for P25 Phase 2 / CQPSK-LSM (CONTRACT v1). Owner: WP-B.

Per input sample (one sequential numba kernel, state carried between blocks so the output is
independent of block boundaries):

    AGC -> derotate by the tracked carrier (NCO) -> RRC matched filter
        -> cubic interpolation + Gardner timing loop (rotation-invariant: Re{(s_k - s_{k-1}) conj(s_mid)})
        -> differential phasor s_k * conj(s_{k-1}) -> phase step -> dibit + soft level

The dibit values follow the C4FM convention, so the same sync words apply: the differential
phase step per symbol is `+3pi/4 -> 1`, `+pi/4 -> 0`, `-pi/4 -> 2`, `-3pi/4 -> 3` (the step an FM
carrier would accumulate for the same symbol). The soft level is the step in units of pi/4
(nominal +/-1, +/-3). Because the data are differential, absolute carrier phase does not
matter; only the residual FREQUENCY does, so there is no Costas phase loop. Two frequency loops
share the NCO: a slow per-sample loop on the mean phase increment (balanced symbols average to
zero, so its fixed point is the carrier offset; capture range +/-fs/2) and a decision-directed
symbol-rate loop on the phase-step error for the fine residual.

A carrier offset f rotates every differential step by 2*pi*f/symbol_rate: at +/-1.5 kHz and
4800 sym/s that is +/-112 degrees, far outside the +/-45 degree decision region, which is why
the coarse loop must converge first. Acquisition takes about 50 ms; during it the output is
unreliable (decisions made while unlocked are flagged in `meta["locked"]`).
"""

from __future__ import annotations

import numpy as np

from sigsense_api.dvoice.dsp._jit import njit
from sigsense_api.dvoice.dsp.filters import rrc_taps
from sigsense_api.dvoice.dsp.types import SymbolBlock
from sigsense_api.dvoice.events import Modulation

_TWO_PI = 2.0 * np.pi

# state vector layout
CS_PHI = 0  # NCO phase (rad)
CS_W = 1  # NCO frequency (rad/sample): the tracked carrier offset
CS_AGC = 2  # running mean |x|
CS_PRE = 3  # previous derotated sample (re, im), for the coarse frequency estimate
CS_PIM = 4
CS_TN = 5  # next symbol-centre position in samples, relative to the filtered buffer start
CS_FREQ = 6  # timing integrator (fractional symbol-period error)
CS_HAVE = 7  # a previous symbol exists (differential reference)
CS_SRE = 8  # previous symbol-centre phasor (re, im)
CS_SIM = 9
CS_NSAMP = 10
CS_PM = 11  # running mean |s|^2 for timing-error normalisation
CS_LOCK = 12  # lock metric: running mean of squared phase-step error (rad^2)
CS_EVM = 13  # running mean of squared phase-step error for the SNR estimate
CS_SIZE = 16

MAX_FREQ = 0.005


@njit
def _interp_c(fb, t):
    i = int(np.floor(t))
    u = t - i
    c0 = -u * (u - 1.0) * (u - 2.0) / 6.0
    c1 = (u + 1.0) * (u - 1.0) * (u - 2.0) / 2.0
    c2 = -(u + 1.0) * u * (u - 2.0) / 2.0
    c3 = (u + 1.0) * u * (u - 1.0) / 6.0
    re = c0 * fb[i - 1].real + c1 * fb[i].real + c2 * fb[i + 1].real + c3 * fb[i + 2].real
    im = c0 * fb[i - 1].imag + c1 * fb[i].imag + c2 * fb[i + 1].imag + c3 * fb[i + 2].imag
    return re, im


@njit
def _run(x, zb, n_zhist, fb, n_carry, cs, taps, sps, kp, ki, k_coarse, k_dd, out_d, out_v, out_lock):
    """Process `x` sample by sample. `zb` = derotated history (n_zhist) + room for x; `fb` =
    carried filtered samples (n_carry) + room for x. Returns (symbols written, n_valid)."""
    n_taps = taps.shape[0]
    n_out = 0
    max_out = out_d.shape[0]
    for i in range(x.shape[0]):
        # ---- AGC + derotation -------------------------------------------------------------
        mag = np.sqrt(x[i].real * x[i].real + x[i].imag * x[i].imag)
        cs[CS_AGC] += 0.002 * (mag - cs[CS_AGC])
        g = 1.0 / max(cs[CS_AGC], 1e-9)
        c = np.cos(cs[CS_PHI])
        s = -np.sin(cs[CS_PHI])
        yr = x[i].real * g
        yi = x[i].imag * g
        zr = yr * c - yi * s
        zi = yr * s + yi * c
        zb[n_zhist + i] = complex(zr, zi)
        # coarse frequency estimate: mean phase increment of the derotated signal
        dr = zr * cs[CS_PRE] + zi * cs[CS_PIM]
        di = zi * cs[CS_PRE] - zr * cs[CS_PIM]
        if cs[CS_NSAMP] > 0.0:
            cs[CS_W] += k_coarse * np.arctan2(di, dr)
        cs[CS_PRE] = zr
        cs[CS_PIM] = zi
        cs[CS_PHI] += cs[CS_W]
        cs[CS_PHI] -= _TWO_PI * np.floor(cs[CS_PHI] / _TWO_PI)
        cs[CS_NSAMP] += 1.0
        # ---- matched filter -----------------------------------------------------------------
        top = n_zhist + i
        s_re = 0.0
        s_im = 0.0
        for k in range(n_taps):
            zv = zb[top - k]
            s_re += taps[k] * zv.real
            s_im += taps[k] * zv.imag
        fb[n_carry + i] = complex(s_re, s_im)
        n_valid = n_carry + i + 1
        # ---- symbols that are now fully interpolable ----------------------------------------
        while n_out < max_out:
            tn = cs[CS_TN]
            period = sps * (1.0 + cs[CS_FREQ])
            idx = int(np.floor(tn))
            if idx + 2 > n_valid - 1:
                break
            tm = tn - 0.5 * period
            if int(np.floor(tm)) - 1 < 0:
                cs[CS_TN] = tn + period
                continue
            yre, yim = _interp_c(fb, tn)
            mre, mim = _interp_c(fb, tm)
            pw = yre * yre + yim * yim
            cs[CS_PM] += 0.01 * (pw - cs[CS_PM])
            if cs[CS_HAVE] != 0.0:
                # differential detection
                ar = yre * cs[CS_SRE] + yim * cs[CS_SIM]
                ai = yim * cs[CS_SRE] - yre * cs[CS_SIM]
                dphi = np.arctan2(ai, ar)
                v = dphi * (4.0 / np.pi)
                if v > 2.0:
                    d = 1
                    ideal = 0.75 * np.pi
                elif v > 0.0:
                    d = 0
                    ideal = 0.25 * np.pi
                elif v > -2.0:
                    d = 2
                    ideal = -0.25 * np.pi
                else:
                    d = 3
                    ideal = -0.75 * np.pi
                err = dphi - ideal
                err -= _TWO_PI * np.floor((err + np.pi) / _TWO_PI)
                out_d[n_out] = d
                out_v[n_out] = v
                cs[CS_EVM] += 0.01 * (err * err - cs[CS_EVM])
                cs[CS_LOCK] += 0.02 * (err * err - cs[CS_LOCK])
                out_lock[n_out] = 1.0 if cs[CS_LOCK] < 0.25 else 0.0
                n_out += 1
                # fine frequency: residual phase step per symbol -> rad per sample
                cs[CS_W] += k_dd * err / sps
                # Gardner timing error (rotation invariant), normalised by the signal power
                gr = (yre - cs[CS_SRE]) * mre + (yim - cs[CS_SIM]) * mim
                e = gr / max(cs[CS_PM], 1e-9) / 2.0
                if e > 2.0:
                    e = 2.0
                elif e < -2.0:
                    e = -2.0
                cs[CS_FREQ] -= ki * e
                if cs[CS_FREQ] > MAX_FREQ:
                    cs[CS_FREQ] = MAX_FREQ
                elif cs[CS_FREQ] < -MAX_FREQ:
                    cs[CS_FREQ] = -MAX_FREQ
                cs[CS_TN] = tn + period - sps * kp * e
            else:
                cs[CS_TN] = tn + period
            cs[CS_SRE] = yre
            cs[CS_SIM] = yim
            cs[CS_HAVE] = 1.0
    return n_out, n_valid


class CqpskSymbolRecovery:
    """AGC -> carrier tracking -> RRC -> Gardner timing -> differential phasor -> phase symbols.

    Input: complex64 baseband at `sample_rate`. Output: `SymbolBlock` (modulation=CQPSK) whose
    dibits use the same value convention as the C4FM path so P25 sync words match.
    P25 Phase 1 CQPSK: 4800 sym/s (10 SPS @48k); P25 Phase 2: 6000 sym/s (8 SPS @48k).
    """

    def __init__(
        self,
        symbol_rate: float,
        sample_rate: float = 48_000.0,
        loop_bw: float = 0.006,
        rolloff: float = 0.2,
    ) -> None:
        self.symbol_rate = float(symbol_rate)
        self.sample_rate = float(sample_rate)
        self.sps = self.sample_rate / self.symbol_rate
        self._taps = rrc_taps(self.sps, rolloff, 8)
        self.delay_samples = (len(self._taps) - 1) / 2.0
        zeta = 0.707
        theta = loop_bw / (zeta + 1.0 / (4.0 * zeta))
        den = 1.0 + 2.0 * zeta * theta + theta * theta
        self._kp = 4.0 * zeta * theta / den
        self._ki = 4.0 * theta * theta / den
        self._k_coarse = 2.0e-4  # per-sample mean-phase-increment loop (time constant ~100 ms)
        self._k_dd = 4.0e-3  # decision-directed residual loop, per symbol
        self._inverted = False
        self.reset()

    def reset(self) -> None:
        self._zhist = np.zeros(len(self._taps) - 1, dtype=np.complex128)
        self._fbuf = np.zeros(0, dtype=np.complex128)
        self._cs = np.zeros(CS_SIZE, dtype=np.float64)
        self._cs[CS_AGC] = 1.0
        self._cs[CS_PM] = 1.0
        self._cs[CS_TN] = 2.0 * self.sps
        self._cs[CS_LOCK] = 2.0  # start "unlocked"
        self._n_symbols = 0

    def set_polarity_inverted(self, inverted: bool) -> None:
        self._inverted = bool(inverted)

    @property
    def freq_offset_hz(self) -> float:
        """Tracked carrier offset (Hz); feed to `FrequencyTracker` / `Channelizer.set_offset`."""
        return float(self._cs[CS_W]) * self.sample_rate / _TWO_PI

    def process(self, iq: np.ndarray) -> SymbolBlock:
        x = np.ascontiguousarray(iq, dtype=np.complex128)
        first_index = self._n_symbols
        if x.size == 0:
            return self._block(np.zeros(0, np.uint8), np.zeros(0), np.zeros(0), first_index)
        n_zh = self._zhist.size
        zb = np.concatenate((self._zhist, np.zeros(x.size, dtype=np.complex128)))
        n_carry = self._fbuf.size
        fb = np.concatenate((self._fbuf, np.zeros(x.size, dtype=np.complex128)))
        max_out = int(x.size / self.sps * 1.05) + 8
        out_d = np.empty(max_out, dtype=np.int64)
        out_v = np.empty(max_out, dtype=np.float64)
        out_lock = np.empty(max_out, dtype=np.float64)
        n, n_valid = _run(
            x, zb, n_zh, fb, n_carry, self._cs, self._taps, self.sps, self._kp, self._ki,
            self._k_coarse, self._k_dd, out_d, out_v, out_lock,
        )
        self._zhist = zb[-(len(self._taps) - 1) :].copy()
        keep_from = min(max(0, int(np.floor(self._cs[CS_TN] - self.sps * 1.6)) - 2), n_valid)
        self._cs[CS_TN] -= keep_from
        self._fbuf = fb[keep_from:n_valid].copy()
        self._n_symbols += n
        return self._block(out_d[:n].astype(np.uint8), out_v[:n], out_lock[:n], first_index)

    def _block(self, dibits: np.ndarray, soft: np.ndarray, lock: np.ndarray, first_index: int) -> SymbolBlock:
        soft32 = soft.astype(np.float32)
        if self._inverted:
            dibits = (dibits ^ 2).astype(np.uint8)
            soft32 = -soft32
        evm = float(self._cs[CS_EVM])
        # phase-step error variance (rad^2) against the symbol-step power (mean step^2 = 5*(pi/4)^2)
        snr = 10.0 * np.log10(max(5.0 * (np.pi / 4.0) ** 2, 1e-12) / max(evm, 1e-12))
        return SymbolBlock(
            dibits=dibits,
            soft=soft32,
            symbol_rate=self.symbol_rate,
            first_index=first_index,
            t0=first_index / self.symbol_rate,
            modulation=Modulation.CQPSK,
            polarity_inverted=self._inverted,
            meta={
                "freq_error_hz": self.freq_offset_hz,
                "level": float(np.pi / 4.0),
                "snr_db": float(snr),
                "locked": bool(lock.size and lock[-1] > 0.5),
                "timing_ppm": float(self._cs[CS_FREQ]) * 1e6,
            },
        )
