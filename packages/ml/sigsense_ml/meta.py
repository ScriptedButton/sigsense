"""Estimate coarse RF metadata from spectrograms / IQ."""

from __future__ import annotations

import numpy as np
from scipy import signal as sp_signal


def estimate_rf_meta(
    iq: np.ndarray | None = None,
    spectrogram: np.ndarray | None = None,
    *,
    sample_rate: float | None = None,
    center_freq: float | None = None,
) -> dict:
    """Estimate bandwidth, relative center, duration, and duty cycle heuristics."""
    meta: dict = {
        "sample_rate_hz": sample_rate,
        "center_freq_hz": center_freq,
        "bandwidth_hz": None,
        "relative_center_hz": None,
        "duration_s": None,
        "duty_cycle": None,
    }

    if iq is not None:
        iq = np.asarray(iq, dtype=np.complex64).ravel()
        if sample_rate and sample_rate > 0:
            meta["duration_s"] = float(iq.size / sample_rate)
        # Occupied bandwidth via spectral density
        n_fft = min(2048, max(256, int(2 ** np.floor(np.log2(max(iq.size, 256))))))
        freqs, psd = sp_signal.welch(
            iq,
            fs=sample_rate or 1.0,
            nperseg=n_fft,
            return_onesided=False,
        )
        freqs = np.fft.fftshift(freqs)
        psd = np.fft.fftshift(psd)
        power = psd / (psd.sum() + 1e-12)
        cdf = np.cumsum(power)
        lo = freqs[np.searchsorted(cdf, 0.05)]
        hi = freqs[np.searchsorted(cdf, 0.95)]
        meta["bandwidth_hz"] = float(abs(hi - lo)) if sample_rate else float(abs(hi - lo))
        meta["relative_center_hz"] = float((lo + hi) / 2.0)
        # Duty: fraction of time energy above threshold
        env = np.abs(iq)
        thr = np.percentile(env, 70)
        meta["duty_cycle"] = float((env > thr).mean())
        return meta

    if spectrogram is not None:
        spec = np.asarray(spectrogram, dtype=np.float32)
        # Frequency axis = rows (after our STFT shift convention: mid = CF)
        freq_profile = spec.mean(axis=1)
        time_profile = spec.mean(axis=0)
        thr_f = np.percentile(freq_profile, 60)
        active_f = np.where(freq_profile > thr_f)[0]
        if active_f.size:
            span = (active_f[-1] - active_f[0] + 1) / spec.shape[0]
            mid = (active_f[0] + active_f[-1]) / 2.0
            rel = (mid / spec.shape[0]) - 0.5
            if sample_rate:
                meta["bandwidth_hz"] = float(span * sample_rate)
                meta["relative_center_hz"] = float(rel * sample_rate)
            else:
                meta["bandwidth_hz"] = float(span)  # fraction of Nyquist span
                meta["relative_center_hz"] = float(rel)
        thr_t = np.percentile(time_profile, 55)
        meta["duty_cycle"] = float((time_profile > thr_t).mean())
    return meta
