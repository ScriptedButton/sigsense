"""Channelizer: wideband IQ -> 48 kHz complex baseband around the target (CONTRACT v1). Owner: WP-B.

Two stateful stages, both numba kernels (plain-Python fallback):

1. **Decimating FIR with the mixer folded into its taps.** Mixing the channel to DC and
   low-pass filtering is `y[m] = e^{-j w n_m} * sum_k h[k] e^{+j w k} x[n_m - k]`, so the
   full-rate stream is never rotated sample by sample: the taps carry the offset and one
   phasor multiply per OUTPUT restores phase continuity. Integer decimation D1 brings the
   rate to >= 4x the output rate. `set_offset` redesigns only the complex taps; the output
   phase accumulator is kept, so a retune is phase continuous.
2. **Rational polyphase resampler** (`up/down`) from that rate to `out_rate` whose prototype
   filter is the channel filter itself: -6 dB at `bandwidth_hz / 2`, 60 dB stop-band
   starting 0.3 * bandwidth beyond it (adjacent 12.5 kHz channels are 12.5 kHz away).

The output is bit-identical whatever the input block sizes (all state is carried, all
arithmetic is sequential float64).
"""

from __future__ import annotations

from fractions import Fraction

import numpy as np
from scipy import signal as sp_signal

from sigsense_api.dvoice.dsp._jit import njit

CHANNEL_RATE = 48_000.0  # complex baseband rate every downstream block assumes
_TWO_PI = 2.0 * np.pi
STAGE1_MIN_RATIO = 4.0  # stage 1 output rate >= 4 x out_rate


@njit
def _dec_fir(buf, n_hist, taps, dec, count, acc, omega, out):
    """Complex-tap decimating FIR over buf = history (n_hist samples) + new samples.

    Returns (n_out, new_count, new_acc). `count` is the number of input samples consumed so
    far; an output is produced whenever (count + 1) % dec == 0. Each output is
    e^{-j acc} * sum_k taps[k] * x[n - k], with acc advancing by omega * dec per output.
    """
    n_taps = taps.shape[0]
    n_new = buf.shape[0] - n_hist
    n_out = 0
    for i in range(n_new):
        g = count + i
        if (g + 1) % dec != 0:
            continue
        top = n_hist + i
        s_re = 0.0
        s_im = 0.0
        for k in range(n_taps):
            xv = buf[top - k]
            tv = taps[k]
            s_re += tv.real * xv.real - tv.imag * xv.imag
            s_im += tv.real * xv.imag + tv.imag * xv.real
        acc += omega * dec
        acc = acc - _TWO_PI * np.floor(acc / _TWO_PI)
        c = np.cos(acc)
        sn = -np.sin(acc)  # e^{-j acc}
        out[n_out] = complex(s_re * c - s_im * sn, s_re * sn + s_im * c)
        n_out += 1
    return n_out, count + n_new, acc


@njit
def _poly_resample(x, poly, up, down, next_pos, out):
    """Rational resampler on a buffer that already includes the history prefix.

    `poly` has shape (up, L): poly[p, k] is prototype tap p + k*up. Output m needs input
    index n0 = pos // up and phase p = pos % up (pos in 1/up-sample units). Returns
    (n_out, new_next_pos) with new_next_pos relative to the same buffer start.
    """
    n_in = x.shape[0]
    taps_per_phase = poly.shape[1]
    n_out = 0
    pos = next_pos
    while True:
        n0 = pos // up
        if n0 >= n_in:
            break
        p = pos - n0 * up
        s_re = 0.0
        s_im = 0.0
        for k in range(taps_per_phase):
            j = n0 - k
            if j < 0:
                break
            t = poly[p, k]
            s_re += t * x[j].real
            s_im += t * x[j].imag
        out[n_out] = complex(s_re, s_im)
        n_out += 1
        pos += down
    return n_out, pos


class Channelizer:
    def __init__(
        self,
        sample_rate_in: float,
        offset_hz: float = 0.0,
        bandwidth_hz: float = 12_500.0,
        out_rate: float = CHANNEL_RATE,
    ) -> None:
        if sample_rate_in < out_rate:
            raise ValueError("sample_rate_in must be >= out_rate")
        self.sample_rate_in = float(sample_rate_in)
        self.bandwidth_hz = float(bandwidth_hz)
        self.out_rate = float(out_rate)
        self._offset_hz = 0.0

        # ---- stage 1: integer decimation to >= 4 x out_rate -------------------------------
        self.dec = max(1, int(self.sample_rate_in // (STAGE1_MIN_RATIO * self.out_rate)))
        self.rate1 = self.sample_rate_in / self.dec
        if self.dec > 1:
            guard = self.bandwidth_hz / 2.0 + 3_000.0  # aliasing must not reach the channel + margin
            f_pass = guard
            f_stop = self.rate1 - guard
            self._proto1 = self._design(f_pass, f_stop, self.sample_rate_in, 60.0)
        else:
            self._proto1 = np.ones(1)
        self._n_taps1 = len(self._proto1)

        # ---- stage 2: rational resampler carrying the channel filter ----------------------
        ratio = Fraction(self.out_rate * self.dec / self.sample_rate_in).limit_denominator(4096)
        self.up, self.down = ratio.numerator, ratio.denominator
        # up/down == out_rate / rate1 (exactly for the usual 48k-multiples, to ~1e-7 otherwise)
        self.rate_error_ppm = abs(self.up / self.down * self.rate1 / self.out_rate - 1.0) * 1e6
        f_pass2 = self.bandwidth_hz / 2.0 * 0.9
        f_stop2 = self.bandwidth_hz / 2.0 * (1.0 + 0.3 * 2.0)
        f_stop2 = min(f_stop2, self.out_rate - self.bandwidth_hz / 2.0)
        proto2 = self._design(f_pass2, f_stop2, self.rate1 * self.up, 60.0) * self.up
        n2 = int(np.ceil(len(proto2) / self.up))
        padded = np.zeros(n2 * self.up)
        padded[: len(proto2)] = proto2
        self._poly = np.ascontiguousarray(padded.reshape(n2, self.up).T)  # (up, L)
        self._n_taps2 = n2
        self.group_delay_s = ((self._n_taps1 - 1) / 2.0) / self.sample_rate_in + ((len(proto2) - 1) / 2.0) / (
            self.rate1 * self.up
        )

        self._taps1c = np.zeros(self._n_taps1, dtype=np.complex128)
        self.reset()
        self.set_offset(offset_hz)

    @staticmethod
    def _design(f_pass: float, f_stop: float, fs: float, atten_db: float) -> np.ndarray:
        """Kaiser low-pass, -6 dB midway between pass and stop edges, unit DC gain."""
        width = max(f_stop - f_pass, fs * 1e-5)
        n, beta = sp_signal.kaiserord(atten_db, width / (fs / 2.0))
        n = int(n) | 1
        return sp_signal.firwin(n, (f_pass + f_stop) / 2.0, window=("kaiser", beta), fs=fs)

    def set_offset(self, offset_hz: float) -> None:
        """Retune the NCO without resetting filter state (phase continuous)."""
        if abs(offset_hz) > self.sample_rate_in / 2.0:
            raise ValueError("offset outside the input bandwidth")
        self._offset_hz = float(offset_hz)
        self._omega = _TWO_PI * offset_hz / self.sample_rate_in
        k = np.arange(self._n_taps1) - (self._n_taps1 - 1) / 2.0  # centre the modulation on the filter
        self._taps1c = (self._proto1 * np.exp(1j * self._omega * k)).astype(np.complex128)
        # Centred modulation shifts the carrier phase by a constant; absorbed by the receiver.

    @property
    def offset_hz(self) -> float:
        return self._offset_hz

    def reset(self) -> None:
        self._hist1 = np.zeros(self._n_taps1 - 1, dtype=np.complex128)
        self._count1 = 0
        self._acc1 = 0.0
        self._hist2 = np.zeros(self._n_taps2 - 1, dtype=np.complex128)
        self._pos2 = (self._n_taps2 - 1) * self.up  # first output lands on the first new sample

    def process(self, iq: np.ndarray) -> np.ndarray:
        """complex64 IQ at sample_rate_in -> complex64 at out_rate."""
        x = np.ascontiguousarray(iq, dtype=np.complex128)
        if x.size == 0:
            return np.zeros(0, dtype=np.complex64)
        if self.dec > 1:
            out1 = np.empty(x.size // self.dec + 2, dtype=np.complex128)
            joined = np.concatenate((self._hist1, x))
            n1, self._count1, self._acc1 = _dec_fir(
                joined, self._hist1.shape[0], self._taps1c, self.dec, self._count1, self._acc1, self._omega, out1
            )
            self._hist1 = joined[-(self._n_taps1 - 1) :].copy()
            s1 = out1[:n1]
        else:
            # No decimation: mix directly (phase accumulator carried across blocks).
            n = np.arange(x.size)
            phase = self._acc1 + self._omega * (n + 1)
            s1 = x * np.exp(-1j * phase)
            self._acc1 = float(np.fmod(self._acc1 + self._omega * x.size, _TWO_PI))
            self._count1 += x.size
        buf = np.concatenate((self._hist2, s1))
        out2 = np.empty(int(len(buf) * self.up // self.down) + 4, dtype=np.complex128)
        n2, new_pos = _poly_resample(buf, self._poly, self.up, self.down, self._pos2, out2)
        keep = self._n_taps2 - 1
        drop = len(buf) - keep  # == len(s1): the history window slides forward by the new samples
        self._pos2 = new_pos - drop * self.up
        self._hist2 = buf[drop:].copy()
        return out2[:n2].astype(np.complex64)
