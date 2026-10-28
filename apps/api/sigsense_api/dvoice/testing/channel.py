"""Channel impairments for synthetic captures. Owner: WP-B.

`impair()` applies, in this order: polarity inversion, carrier offset + drift, symbol-clock
(timing) error, static multipath, Rayleigh fading, DC offset, then AWGN last so the SNR is
relative to the signal as the receiver sees it.

SNR convention: `snr_db` is signal power over noise power inside `snr_bandwidth_hz`
(default: the whole sample-rate band, i.e. noise power over `sample_rate`). A 12.5 kHz
channel at 48 kHz has +5.8 dB more SNR than the same noise measured over 48 kHz.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np


def signal_power(iq: np.ndarray) -> float:
    """Mean power over the active part (ignores silent padding)."""
    p = np.abs(iq) ** 2
    if p.size == 0:
        return 0.0
    active = p > 0.1 * p.mean()
    return float(p[active].mean()) if active.any() else float(p.mean())


def resample_ppm(iq: np.ndarray, ppm: float) -> np.ndarray:
    """Read the input `ppm` parts-per-million faster (positive) or slower, 4-point Lagrange."""
    if ppm == 0.0 or iq.size < 8:
        return iq
    n_out = int((iq.size - 4) / (1.0 + ppm * 1e-6))
    t = np.arange(n_out) * (1.0 + ppm * 1e-6) + 1.0
    i0 = np.floor(t).astype(np.int64)
    f = t - i0
    xm1, x0, x1, x2 = iq[i0 - 1], iq[i0], iq[i0 + 1], iq[i0 + 2]
    c0 = -f * (f - 1) * (f - 2) / 6.0
    c1 = (f + 1) * (f - 1) * (f - 2) / 2.0
    c2 = -(f + 1) * f * (f - 2) / 2.0
    c3 = (f + 1) * f * (f - 1) / 6.0
    return (c0 * xm1 + c1 * x0 + c2 * x1 + c3 * x2).astype(iq.dtype)


def rayleigh_fading(n: int, sample_rate: float, doppler_hz: float, seed: int = 0, paths: int = 24) -> np.ndarray:
    """Flat Rayleigh gain (Jakes sum of sinusoids), unit mean power."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / sample_rate
    angle = rng.uniform(0, 2 * np.pi, paths)
    doppler = doppler_hz * np.cos(rng.uniform(0, 2 * np.pi, paths))
    phase = rng.uniform(0, 2 * np.pi, paths)
    g = np.zeros(n, dtype=np.complex128)
    for a, d, p in zip(angle, doppler, phase):
        g += np.exp(1j * (2 * np.pi * d * t + p + a))
    return g / np.sqrt(paths)


def multipath(iq: np.ndarray, sample_rate: float, taps: Sequence[tuple[float, complex]]) -> np.ndarray:
    """Static echoes: (delay_seconds, complex_gain) pairs; the direct path is added at gain 1."""
    out = iq.astype(np.complex128).copy()
    for delay_s, gain in taps:
        d = int(round(delay_s * sample_rate))
        if 0 < d < iq.size:
            out[d:] += gain * iq[: iq.size - d]
    return out.astype(iq.dtype)


def impair(
    iq: np.ndarray,
    snr_db: float,
    freq_offset_hz: float = 0.0,
    timing_ppm: float = 0.0,
    invert: bool = False,
    seed: int = 0,
    *,
    sample_rate: float = 48_000.0,
    snr_bandwidth_hz: Optional[float] = None,
    drift_hz_per_s: float = 0.0,
    dc_offset: complex = 0j,
    multipath_taps: Optional[Sequence[tuple[float, complex]]] = None,
    fading_doppler_hz: float = 0.0,
) -> np.ndarray:
    """Apply channel impairments for BER sweeps (see module docstring for order/SNR convention)."""
    x = np.asarray(iq, dtype=np.complex128)
    p_sig = signal_power(x)
    if invert:
        x = np.conj(x)  # flips frequency sign: FSK polarity and DQPSK rotation both invert
    n = np.arange(x.size)
    if freq_offset_hz or drift_hz_per_s:
        t = n / sample_rate
        x = x * np.exp(2j * np.pi * (freq_offset_hz * t + 0.5 * drift_hz_per_s * t * t))
    if timing_ppm:
        x = resample_ppm(x, timing_ppm)
    if multipath_taps:
        x = multipath(x, sample_rate, multipath_taps)
    if fading_doppler_hz > 0:
        x = x * rayleigh_fading(x.size, sample_rate, fading_doppler_hz, seed + 17)
    if dc_offset:
        x = x + dc_offset * np.sqrt(p_sig)
    if np.isfinite(snr_db):
        bw = snr_bandwidth_hz or sample_rate
        noise_power = p_sig / (10.0 ** (snr_db / 10.0)) * (sample_rate / bw)
        rng = np.random.default_rng(seed)
        sigma = np.sqrt(noise_power / 2.0)
        x = x + sigma * (rng.standard_normal(x.size) + 1j * rng.standard_normal(x.size))
    return x.astype(np.complex64)
