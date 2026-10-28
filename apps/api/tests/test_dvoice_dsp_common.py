"""Shared helpers for the WP-B front-end tests (no tests live here). Owner: WP-B.

SNR convention used everywhere in these tests: signal power over noise power measured inside a
12.5 kHz channel (`snr_bandwidth_hz=12500`), i.e. 5.8 dB better than the same noise spread over
the whole 48 kHz band. `Es/N0 = SNR * 12500 / symbol_rate`.
"""

from __future__ import annotations

import numpy as np
from scipy import special, stats

from sigsense_api.dvoice.dsp.afc import FrequencyTracker
from sigsense_api.dvoice.dsp.channel import Channelizer
from sigsense_api.dvoice.dsp.cqpsk import CqpskSymbolRecovery
from sigsense_api.dvoice.dsp.discriminator import FmDiscriminator
from sigsense_api.dvoice.dsp.fsk import FskSymbolRecovery
from sigsense_api.dvoice.events import Modulation
from sigsense_api.dvoice.testing.channel import impair
from sigsense_api.dvoice.testing.modulator import align_and_ber, modulate

FS = 48_000.0
CHANNEL_BW = 12_500.0

NOMINAL = {  # protocol-style parameters used across the tests: (symbol_rate, modulation, levels, deviation_hz)
    "p25_c4fm": (4800.0, Modulation.C4FM, 4, 1800.0),
    "dmr_4fsk": (4800.0, Modulation.FSK4, 4, 1944.0),
    "nxdn48": (2400.0, Modulation.FSK4, 4, 1050.0),
    "dstar_gmsk": (4800.0, Modulation.GMSK, 2, 1200.0),
}


def random_dibits(n: int, seed: int = 1, levels: int = 4) -> np.ndarray:
    rng = np.random.default_rng(seed)
    d = rng.integers(0, 4, n).astype(np.uint8)
    if levels == 2:
        d = np.where(d & 1, 3, 1).astype(np.uint8)  # only values 1 (+) and 3 (-)
    return d


def q_func(x):
    return 0.5 * special.erfc(np.asarray(x) / np.sqrt(2.0))


def process_in_blocks(obj, x, sizes):
    """Feed `x` to `obj.process` in the given (cycled) block sizes; concatenate dibits/soft."""
    dibits, soft, i, k = [], [], 0, 0
    while i < len(x):
        n = int(sizes[k % len(sizes)])
        blk = obj.process(x[i : i + n])
        dibits.append(blk.dibits)
        soft.append(blk.soft)
        i += n
        k += 1
    return np.concatenate(dibits), np.concatenate(soft)


def fsk_receive(iq, rate, modulation, levels, timing="mueller", bandwidth=CHANNEL_BW, block=4096):
    """Discriminator (+ optional pre-filter) + FSK symbol recovery; returns (dibits, last block)."""
    disc = FmDiscriminator(FS, bandwidth_hz=bandwidth)
    rec = FskSymbolRecovery(rate, FS, levels, modulation, timing)
    out, last = [], None
    for i in range(0, len(iq), block):
        last = rec.process(disc.process(iq[i : i + block]))
        out.append(last.dibits)
    return np.concatenate(out), last


def fsk_receive_with_afc(iq, rate, modulation, levels, block=2400, limit_hz=2500.0, bandwidth=CHANNEL_BW):
    """Closed loop: Channelizer (retuned from the slicer's centre) -> discriminator -> recovery."""
    chan = Channelizer(FS, 0.0, bandwidth, FS)
    disc = FmDiscriminator(FS)
    rec = FskSymbolRecovery(rate, FS, levels, modulation)
    tracker = FrequencyTracker(limit_hz=limit_hz, bandwidth=0.2)
    out = []
    for i in range(0, len(iq), block):
        blk = rec.process(disc.process(chan.process(iq[i : i + block])))
        out.append(blk.dibits)
        chan.set_offset(tracker.update(blk.meta["freq_error_hz"]))
    return np.concatenate(out), tracker.correction_hz


def fm_noise_sigma_hz(snr_db, rx_taps, lpf_taps, snr_bw=CHANNEL_BW, fs=FS):
    """Std-dev (Hz) of the discriminator noise after the pre-filter and matched filter.

    Small-noise FM theory: frequency noise PSD = |2 sin(pi f/fs)|^2 (fs/2pi)^2 * N0 |L(f)|^2 / (2 A^2),
    with N0/A^2 = 1 / (SNR * snr_bw) for unit signal power, then shaped by |H_rx(f)|^2."""
    n0 = 1.0 / (10.0 ** (snr_db / 10.0) * snr_bw)
    nfft = 16384
    f = np.fft.fftshift(np.fft.fftfreq(nfft, 1.0 / fs))
    h = np.fft.fftshift(np.fft.fft(rx_taps, nfft))
    lp = np.fft.fftshift(np.fft.fft(lpf_taps, nfft)) if lpf_taps is not None else 1.0
    disc = (2.0 * np.sin(np.pi * f / fs)) ** 2 * (fs / (2.0 * np.pi)) ** 2
    psd = disc * n0 * np.abs(lp) ** 2 / 2.0 * np.abs(h) ** 2
    return float(np.sqrt(np.trapezoid(psd, f)))


def fsk4_ser_theory(snr_db, deviation_hz, rx_taps, lpf_taps):
    """Symbol error rate of 4-level FSK through the discriminator: 1.5 Q(d / sigma), d = dev/3."""
    return float(1.5 * q_func((deviation_hz / 3.0) / fm_noise_sigma_hz(snr_db, rx_taps, lpf_taps)))


def dqpsk_ber_theory(snr_db, symbol_rate, snr_bw=CHANNEL_BW):
    """Gray-coded DQPSK with ideal differential detection (Proakis): Q1(a,b) - 0.5 I0(ab) e^{-(a^2+b^2)/2}."""
    gb = 10.0 ** (snr_db / 10.0) * snr_bw / symbol_rate / 2.0  # Eb/N0
    a = np.sqrt(2.0 * gb * (1.0 - 1.0 / np.sqrt(2.0)))
    b = np.sqrt(2.0 * gb * (1.0 + 1.0 / np.sqrt(2.0)))
    marcum = stats.ncx2.sf(b * b, 2, a * a)  # Marcum Q1(a, b)
    return float(marcum - 0.5 * special.i0e(a * b) * np.exp(-((a - b) ** 2) / 2.0))


def make_iq(dibits, name, snr_db=np.inf, **impairments):
    rate, modulation, _levels, dev = NOMINAL[name]
    iq = modulate(dibits, rate, modulation, FS, dev)
    if snr_db is np.inf and not impairments:
        return iq
    return impair(iq, snr_db, seed=impairments.pop("seed", 11), snr_bandwidth_hz=CHANNEL_BW, **impairments)


__all__ = [n for n in dir() if not n.startswith("_")]
