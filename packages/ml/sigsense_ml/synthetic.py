"""Synthetic IQ generators for training signal classes."""

from __future__ import annotations

import numpy as np

from sigsense_ml.classes import SIGNAL_CLASSES


def _awgn(iq: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    power = np.mean(np.abs(iq) ** 2) + 1e-12
    noise_power = power / (10 ** (snr_db / 10.0))
    noise = (
        rng.normal(0, np.sqrt(noise_power / 2), iq.shape)
        + 1j * rng.normal(0, np.sqrt(noise_power / 2), iq.shape)
    )
    return (iq + noise).astype(np.complex64)


def _tone(n: int, freq: float, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n, dtype=np.float64)
    phase = rng.uniform(0, 2 * np.pi)
    return np.exp(1j * (2 * np.pi * freq * t + phase)).astype(np.complex64)


def generate_iq(class_name: str, n: int = 8192, snr_db: float = 15.0, seed: int | None = None) -> np.ndarray:
    """Generate a short synthetic IQ burst for the given class label."""
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=np.float64)
    cf = rng.uniform(-0.15, 0.15)  # relative CF offset
    name = class_name

    if name == "Noise":
        iq = (
            rng.normal(0, 1, n) + 1j * rng.normal(0, 1, n)
        ).astype(np.complex64)
        return iq

    if name == "Impulsive":
        iq = (
            rng.normal(0, 0.2, n) + 1j * rng.normal(0, 0.2, n)
        ).astype(np.complex64)
        spikes = rng.choice(n, size=max(3, n // 200), replace=False)
        iq[spikes] += rng.normal(0, 4, len(spikes)) + 1j * rng.normal(0, 4, len(spikes))
        return _awgn(iq, snr_db, rng)

    if name == "CW":
        iq = _tone(n, cf, rng)
        return _awgn(iq, snr_db, rng)

    if name in ("AM",):
        msg = np.sin(2 * np.pi * rng.uniform(0.001, 0.01) * t)
        carrier = _tone(n, cf, rng)
        iq = ((1 + 0.7 * msg) * carrier).astype(np.complex64)
        return _awgn(iq, snr_db, rng)

    if name in ("NFM", "WFM"):
        dev = 0.02 if name == "NFM" else 0.08
        msg = np.sin(2 * np.pi * rng.uniform(0.001, 0.008) * t)
        phase = 2 * np.pi * cf * t + 2 * np.pi * dev * np.cumsum(msg)
        iq = np.exp(1j * phase).astype(np.complex64)
        return _awgn(iq, snr_db, rng)

    if name in ("USB", "LSB"):
        msg = np.sin(2 * np.pi * rng.uniform(0.002, 0.01) * t)
        analytic = sp_hilbert(msg)
        if name == "LSB":
            analytic = np.conj(analytic)
        carrier = _tone(n, cf, rng)
        iq = (analytic * carrier).astype(np.complex64)
        return _awgn(iq, snr_db + 3, rng)

    if name in ("ASK",):
        bits = rng.integers(0, 2, n // 16)
        sym = np.repeat(bits, 16)[:n].astype(np.float64)
        iq = (sym * _tone(n, cf, rng)).astype(np.complex64)
        return _awgn(iq, snr_db, rng)

    if name in ("FSK", "GFSK", "APRS", "AIS"):
        bits = rng.integers(0, 2, n // 8)
        sym = np.repeat(bits * 2 - 1, 8)[:n].astype(np.float64)
        if name == "GFSK":
            # simple smoothing
            kernel = np.exp(-0.5 * np.linspace(-2, 2, 7) ** 2)
            kernel /= kernel.sum()
            sym = np.convolve(sym, kernel, mode="same")
        dev = 0.04
        phase = 2 * np.pi * cf * t + 2 * np.pi * dev * np.cumsum(sym)
        iq = np.exp(1j * phase).astype(np.complex64)
        return _awgn(iq, snr_db, rng)

    if name in ("BPSK", "QPSK", "8PSK", "DMR", "P25", "ACARS", "GSM"):
        m = {"BPSK": 2, "QPSK": 4, "8PSK": 8}.get(name, 4)
        sps = 8
        symbols = rng.integers(0, m, n // sps)
        phases = 2 * np.pi * symbols / m
        baseband = np.repeat(np.exp(1j * phases), sps)[:n]
        iq = (baseband * _tone(n, cf, rng)).astype(np.complex64)
        return _awgn(iq, snr_db, rng)

    if name in ("QAM16", "QAM64"):
        m = 16 if name == "QAM16" else 64
        side = int(np.sqrt(m))
        sps = 8
        n_sym = n // sps
        i_idx = rng.integers(0, side, n_sym) - (side - 1) / 2
        q_idx = rng.integers(0, side, n_sym) - (side - 1) / 2
        baseband = (i_idx + 1j * q_idx) / (side / 2)
        baseband = np.repeat(baseband, sps)[:n]
        iq = (baseband * _tone(n, cf, rng)).astype(np.complex64)
        return _awgn(iq, snr_db, rng)

    if name == "Bluetooth-LE":
        from sigsense_ml.ble import generate_ble_adv_iq

        # Generate at 4 Msps then resample/crop to requested length
        packet = generate_ble_adv_iq(
            sample_rate=4e6, channel=37, snr_db=snr_db, seed=seed
        )
        if packet.size >= n:
            start = max(0, (packet.size - n) // 2)
            return packet[start : start + n]
        return np.pad(packet, (0, n - packet.size)).astype(np.complex64)

    if name in ("OFDM", "WiFi", "LTE", "Zigbee"):
        n_sub = 64
        n_sym = max(1, n // (n_sub * 2))
        frames = []
        for _ in range(n_sym):
            X = rng.normal(0, 1, n_sub) + 1j * rng.normal(0, 1, n_sub)
            X[n_sub // 2 - 2 : n_sub // 2 + 3] = 0  # null DC region
            x = np.fft.ifft(np.fft.ifftshift(X))
            cp = x[-n_sub // 4 :]
            frames.append(np.concatenate([cp, x]))
        baseband = np.concatenate(frames)[:n]
        if baseband.size < n:
            baseband = np.pad(baseband, (0, n - baseband.size))
        iq = (baseband * _tone(n, cf, rng)).astype(np.complex64)
        return _awgn(iq, snr_db, rng)

    if name == "LoRa":
        # chirp-like symbols
        bw = 0.2
        chirp_len = 256
        pieces = []
        while sum(len(p) for p in pieces) < n:
            sf_phase = np.pi * bw * (np.arange(chirp_len) / chirp_len) ** 2 * chirp_len
            if rng.random() < 0.5:
                sf_phase = -sf_phase
            pieces.append(np.exp(1j * sf_phase))
        baseband = np.concatenate(pieces)[:n]
        iq = (baseband * _tone(n, cf, rng)).astype(np.complex64)
        return _awgn(iq, snr_db, rng)

    if name in ("Pulse", "ADS-B"):
        iq = np.zeros(n, dtype=np.complex64)
        width = 16 if name == "ADS-B" else max(4, int(rng.integers(4, 40)))
        period = max(width * 4, int(rng.integers(width * 3, width * 20)))
        for start in range(rng.integers(0, period), n - width, period):
            iq[start : start + width] = _tone(width, cf, rng)
        return _awgn(iq, snr_db, rng)

    if name in ("Chirp-LFM", "FMCW"):
        bw = rng.uniform(0.1, 0.35)
        if name == "FMCW":
            sweep = 512
            pieces = []
            while sum(len(p) for p in pieces) < n:
                tt = np.arange(sweep)
                phase = 2 * np.pi * (cf * tt + 0.5 * bw * (tt / sweep) * tt)
                pieces.append(np.exp(1j * phase))
            iq = np.concatenate(pieces)[:n].astype(np.complex64)
        else:
            phase = 2 * np.pi * (cf * t + 0.5 * bw * (t / n) * t)
            iq = np.exp(1j * phase).astype(np.complex64)
        return _awgn(iq, snr_db, rng)

    # Fallback: tone in noise
    iq = _tone(n, cf, rng)
    return _awgn(iq, snr_db, rng)


def sp_hilbert(x: np.ndarray) -> np.ndarray:
    """Simple analytic signal via FFT Hilbert."""
    x = np.asarray(x, dtype=np.float64)
    n = x.size
    X = np.fft.fft(x)
    h = np.zeros(n)
    if n % 2 == 0:
        h[0] = h[n // 2] = 1
        h[1 : n // 2] = 2
    else:
        h[0] = 1
        h[1 : (n + 1) // 2] = 2
    return np.fft.ifft(X * h)


def random_class_batch(
    batch_size: int,
    n: int = 8192,
    snr_range: tuple[float, float] = (0.0, 25.0),
    seed: int | None = None,
) -> tuple[list[np.ndarray], list[int]]:
    rng = np.random.default_rng(seed)
    iqs: list[np.ndarray] = []
    labels: list[int] = []
    for i in range(batch_size):
        idx = int(rng.integers(0, len(SIGNAL_CLASSES)))
        snr = float(rng.uniform(*snr_range))
        iq = generate_iq(SIGNAL_CLASSES[idx], n=n, snr_db=snr, seed=None if seed is None else seed + i)
        iqs.append(iq)
        labels.append(idx)
    return iqs, labels
