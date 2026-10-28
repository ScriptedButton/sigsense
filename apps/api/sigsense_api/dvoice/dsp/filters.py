"""Filter design helpers for the front end and the test modulators. Owner: WP-B.

All taps are float64, odd length, linear phase, unit DC gain unless noted.
"""

from __future__ import annotations

import numpy as np
from scipy import signal as sp_signal


def rrc_taps(sps: float, alpha: float = 0.2, span: int = 8) -> np.ndarray:
    """Root-raised-cosine pulse, `span` symbols long, normalised to unit energy
    (so a cascade Tx-RRC * Rx-RRC has a raised-cosine response with unit peak
    after dividing by `sps`... see `rc_peak` in the tests, not assumed here)."""
    n = int(round(span * sps))
    if n % 2 == 0:
        n += 1
    t = (np.arange(n) - (n - 1) / 2.0) / sps  # in symbol periods
    h = np.zeros(n)
    for i, ti in enumerate(t):
        if abs(ti) < 1e-12:
            h[i] = 1.0 - alpha + 4.0 * alpha / np.pi
        elif alpha > 0 and abs(abs(ti) - 1.0 / (4.0 * alpha)) < 1e-9:
            h[i] = (alpha / np.sqrt(2.0)) * (
                (1 + 2 / np.pi) * np.sin(np.pi / (4 * alpha)) + (1 - 2 / np.pi) * np.cos(np.pi / (4 * alpha))
            )
        else:
            num = np.sin(np.pi * ti * (1 - alpha)) + 4 * alpha * ti * np.cos(np.pi * ti * (1 + alpha))
            den = np.pi * ti * (1 - (4 * alpha * ti) ** 2)
            h[i] = num / den
    return h / np.sqrt(np.sum(h * h))


def gaussian_taps(sps: float, bt: float = 0.5, span: int = 4) -> np.ndarray:
    """Gaussian frequency-pulse filter (GMSK), unit DC gain."""
    n = int(round(span * sps))
    if n % 2 == 0:
        n += 1
    t = (np.arange(n) - (n - 1) / 2.0) / sps
    sigma = np.sqrt(np.log(2.0)) / (2.0 * np.pi * bt)
    h = np.exp(-(t * t) / (2.0 * sigma * sigma))
    return h / h.sum()


def lowpass_taps(cutoff_hz: float, sample_rate: float, transition_hz: float, atten_db: float = 60.0) -> np.ndarray:
    """Kaiser-window low-pass, unit DC gain. `cutoff_hz` is the -6 dB one-sided edge."""
    transition_hz = max(transition_hz, sample_rate * 1e-4)
    n, beta = sp_signal.kaiserord(atten_db, transition_hz / (sample_rate / 2.0))
    n = int(n) | 1
    return sp_signal.firwin(n, cutoff_hz, window=("kaiser", beta), fs=sample_rate)


def half_symbol_integrator(sps: float) -> np.ndarray:
    """Boxcar over one symbol (integrate-and-dump), unit DC gain."""
    n = max(1, int(round(sps)))
    return np.ones(n) / n
