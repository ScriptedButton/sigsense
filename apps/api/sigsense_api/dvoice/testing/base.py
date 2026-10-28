"""Encoder / modulator interfaces for test generation (CONTRACT v1). Owner: WP-B (modulator), protocol WPs (encoders)."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from sigsense_api.dvoice.events import Modulation


@dataclass
class SyntheticCall:
    """What an encoder is asked to produce; the test later asserts the decoder reports the same."""

    protocol: str
    target_id: int = 1
    source_id: int = 1234
    slot: Optional[int] = None
    voice_frames: int = 18
    encrypted: bool = False
    algorithm_id: Optional[int] = None
    extra: dict[str, Any] = field(default_factory=dict)


class FrameEncoder(abc.ABC):
    @abc.abstractmethod
    def encode_call(self, call: SyntheticCall) -> np.ndarray:
        """-> uint8 dibits (values 0..3) for a complete transmission incl. sync, preamble and terminator."""


def modulate(
    dibits: np.ndarray,
    symbol_rate: float,
    modulation: Modulation,
    sample_rate: float = 48_000.0,
    deviation_hz: Optional[float] = None,
) -> np.ndarray:
    """dibits -> complex64 baseband at `sample_rate` (shaped per the protocol). Owner: WP-B."""
    from sigsense_api.dvoice.testing.modulator import modulate as _modulate

    return _modulate(dibits, symbol_rate, modulation, sample_rate, deviation_hz)


def impair(
    iq: np.ndarray,
    snr_db: float,
    freq_offset_hz: float = 0.0,
    timing_ppm: float = 0.0,
    invert: bool = False,
    seed: int = 0,
) -> np.ndarray:
    """Apply channel impairments for BER sweeps. Owner: WP-B."""
    from sigsense_api.dvoice.testing.channel import impair as _impair

    return _impair(iq, snr_db, freq_offset_hz, timing_ppm, invert, seed)
