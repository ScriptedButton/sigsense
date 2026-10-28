"""Symbols -> shaped baseband IQ for synthetic captures. Owner: WP-B.

Dibit convention (dsp/types.py): value 1 -> +3, 0 -> +1, 2 -> -1, 3 -> -3 (4-level);
2-level (D-STAR) uses values 1 (+1) and 3 (-1).

Models (original implementations from the public air-interface descriptions):
* C4FM / FSK4: frequency-pulse shaped with a root-raised-cosine (alpha 0.2 by default)
  so a matched RRC after the discriminator gives a Nyquist (ISI-free) overall response.
  `deviation_hz` is the frequency of the OUTER symbols (+/-3); inner symbols sit at 1/3.
* GMSK: +/-1 rectangular frequency pulses through a Gaussian filter (BT 0.5);
  `deviation_hz` is the peak deviation (symbol_rate/4 for MSK-index-0.5).
* CQPSK: differential QPSK with phase steps {+3pi/4, +pi/4, -pi/4, -3pi/4} for the dibits
  {1, 0, 2, 3}: the same step an FM carrier would accumulate, so the dibit values line up
  with the C4FM path and the same sync words apply (P25 Phase 1 LSM / Phase 2 H-DQPSK).
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from sigsense_api.dvoice.dsp.filters import gaussian_taps, rrc_taps
from sigsense_api.dvoice.events import Modulation

# dibit value -> nominal level (+3, +1, -1, -3)
LEVEL_OF_DIBIT = np.array([1.0, 3.0, -1.0, -3.0])
# dibit value -> differential phase step (radians)
PHASE_STEP_OF_DIBIT = np.array([np.pi / 4, 3 * np.pi / 4, -np.pi / 4, -3 * np.pi / 4])

DEFAULT_DEVIATION_FACTOR = {
    Modulation.C4FM: 0.375,  # 1800 Hz at 4800 baud (P25 C4FM)
    Modulation.FSK4: 0.405,  # 1944 Hz at 4800 baud (DMR-style 4FSK)
    Modulation.GMSK: 0.25,  # 1200 Hz at 4800 baud
}


def default_deviation(symbol_rate: float, modulation: Modulation) -> float:
    return DEFAULT_DEVIATION_FACTOR.get(modulation, 0.0) * symbol_rate


def _impulse_train(values: np.ndarray, sps: float, tail: int) -> np.ndarray:
    n = len(values)
    positions = np.round(np.arange(n) * sps).astype(np.int64)
    out = np.zeros(int(positions[-1]) + 1 + tail, dtype=values.dtype)
    out[positions] = values
    return out


def modulate(
    dibits: np.ndarray,
    symbol_rate: float,
    modulation: Modulation,
    sample_rate: float = 48_000.0,
    deviation_hz: Optional[float] = None,
    *,
    alpha: float = 0.2,
    bt: float = 0.5,
    span: int = 8,
) -> np.ndarray:
    """dibits -> complex64 baseband at `sample_rate`, unit mean power.

    The output is delayed by the shaping filter's group delay ((taps-1)/2 samples);
    symbol k is centred at sample `round(k * sps) + (taps - 1) // 2`.
    """
    dibits = np.asarray(dibits, dtype=np.uint8).ravel()
    if dibits.size == 0:
        return np.zeros(0, dtype=np.complex64)
    sps = sample_rate / symbol_rate

    if modulation in (Modulation.C4FM, Modulation.FSK4):
        dev = default_deviation(symbol_rate, modulation) if deviation_hz is None else deviation_hz
        values = LEVEL_OF_DIBIT[dibits] * (dev / 3.0)
        taps = rrc_taps(sps, alpha, span)
        freq = np.convolve(_impulse_train(values, sps, len(taps)), taps)
        phase = 2.0 * np.pi * np.cumsum(freq) / sample_rate
        return np.exp(1j * phase).astype(np.complex64)

    if modulation == Modulation.GMSK:
        dev = default_deviation(symbol_rate, modulation) if deviation_hz is None else deviation_hz
        # 2-level: dibit value 1 -> +1, value 3 -> -1 (anything else follows the sign bit)
        values = np.where((dibits & 2) == 0, 1.0, -1.0)
        n = int(round(len(values) * sps))
        held = np.repeat(values, int(round(sps)))[:n] if abs(sps - round(sps)) < 1e-9 else values[
            np.minimum((np.arange(n) / sps).astype(np.int64), len(values) - 1)
        ]
        taps = gaussian_taps(sps, bt, 4)
        freq = np.convolve(held, taps) * dev
        phase = 2.0 * np.pi * np.cumsum(freq) / sample_rate
        return np.exp(1j * phase).astype(np.complex64)

    if modulation == Modulation.CQPSK:
        phases = np.cumsum(PHASE_STEP_OF_DIBIT[dibits])
        symbols = np.exp(1j * phases)
        taps = rrc_taps(sps, alpha, span)
        iq = np.convolve(_impulse_train(symbols, sps, len(taps)), taps)
        iq /= np.sqrt(np.mean(np.abs(iq) ** 2)) + 1e-12
        return iq.astype(np.complex64)

    raise ValueError(f"unsupported modulation {modulation!r}")


def group_delay_samples(symbol_rate: float, modulation: Modulation, sample_rate: float = 48_000.0, span: int = 8) -> int:
    """Samples between the start of the output and the first symbol's centre."""
    sps = sample_rate / symbol_rate
    if modulation == Modulation.GMSK:
        return (len(gaussian_taps(sps, 0.5, 4)) - 1) // 2
    return (len(rrc_taps(sps, 0.2, span)) - 1) // 2


def align_and_ber(tx: np.ndarray, rx: np.ndarray, search: int = 400, skip: int = 400, length: Optional[int] = None) -> dict:
    """Compare a recovered dibit stream with what was sent, finding the (unknown) stream lag.

    Compares rx[skip : skip + length] with tx at every lag in +/-`search` and keeps the best;
    also tries the inverted polarity (d ^ 2). Returns symbol/bit error rates, the lag
    (rx index minus tx index) and whether the winner was inverted. `skip` drops the
    acquisition transient."""
    tx = np.asarray(tx, dtype=np.uint8)
    rx = np.asarray(rx, dtype=np.uint8)
    n = min(len(rx) - skip, len(tx) - skip - search) if length is None else length
    if n <= 16:
        return {"ser": 1.0, "ber": 1.0, "lag": 0, "inverted": False, "n": 0}
    seg = rx[skip : skip + n]
    best = None
    pop = np.array([bin(i).count("1") for i in range(4)], dtype=np.int64)
    for inverted in (False, True):
        cand = (seg ^ 2) if inverted else seg
        for lag in range(-search, search + 1):
            start = skip - lag
            if start < 0 or start + n > len(tx):
                continue
            diff = cand != tx[start : start + n]
            ser = float(diff.mean())
            if best is None or ser < best[0]:
                bit_errors = int(pop[cand[diff] ^ tx[start : start + n][diff]].sum())
                best = (ser, bit_errors / (2.0 * n), lag, inverted)
    return {"ser": best[0], "ber": best[1], "lag": best[2], "inverted": best[3], "n": n}
