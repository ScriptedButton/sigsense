"""FM discriminator (CONTRACT v1). Owner: WP-B."""

from __future__ import annotations

from typing import Optional

import numpy as np

from sigsense_api.dvoice.dsp._jit import njit
from sigsense_api.dvoice.dsp.filters import lowpass_taps


@njit
def _fir_complex_real(buf, n_hist, taps, out):
    """Real-tap FIR over complex samples; buf = history + new, out has len(new) entries."""
    n_taps = taps.shape[0]
    for i in range(out.shape[0]):
        top = n_hist + i
        s_re = 0.0
        s_im = 0.0
        for k in range(n_taps):
            t = taps[k]
            s_re += t * buf[top - k].real
            s_im += t * buf[top - k].imag
        out[i] = complex(s_re, s_im)


class FmDiscriminator:
    """Instantaneous-frequency estimate from 48 kHz complex baseband.

    Output is float32 in units of Hz of deviation (so +/-1800 Hz, +/-600 Hz are the
    P25/DMR 4-level targets). Stateful across blocks (previous sample carried).

    `bandwidth_hz`: optionally low-pass the complex input first (a linear-phase FIR, total
    bandwidth `bandwidth_hz`). Use it when the input has not already been channel-filtered
    (e.g. the 48 kHz upload path): FM discriminator noise grows with the noise bandwidth, so
    an un-filtered 48 kHz input costs about 6 dB against a 12.5 kHz channel.
    """

    def __init__(self, sample_rate: float = 48_000.0, bandwidth_hz: Optional[float] = None) -> None:
        self.sample_rate = float(sample_rate)
        self.bandwidth_hz = bandwidth_hz
        if bandwidth_hz:
            self._taps = lowpass_taps(bandwidth_hz / 2.0, sample_rate, bandwidth_hz * 0.3, 50.0)
        else:
            self._taps = None
        self.reset()

    @property
    def delay_samples(self) -> float:
        return 0.0 if self._taps is None else (len(self._taps) - 1) / 2.0

    def reset(self) -> None:
        self._primed = False
        self._prev = None  # last (filtered) complex sample
        self._hist = None if self._taps is None else np.zeros(len(self._taps) - 1, dtype=np.complex128)

    def process(self, iq: np.ndarray) -> np.ndarray:
        x = np.ascontiguousarray(iq, dtype=np.complex128)
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)
        if self._taps is not None:
            if self._primed is False:
                # Start the filter as if the carrier had always been there, instead of from
                # zeros: the zero-history ramp makes the discriminator emit huge start-up spikes.
                self._hist = np.full(len(self._taps) - 1, x[0], dtype=np.complex128)
                self._primed = True
            joined = np.concatenate((self._hist, x))
            y = np.empty(x.size, dtype=np.complex128)
            _fir_complex_real(joined, self._hist.size, self._taps, y)
            self._hist = joined[-(len(self._taps) - 1) :].copy()
            x = y
        prev = x[0] if self._prev is None else self._prev
        shifted = np.concatenate(([prev], x[:-1]))
        out = np.angle(x * np.conj(shifted)) * (self.sample_rate / (2.0 * np.pi))
        self._prev = x[-1]
        return out.astype(np.float32)
