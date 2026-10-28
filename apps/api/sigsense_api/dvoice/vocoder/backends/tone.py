"""Deterministic test-double vocoder (CONTRACT v1). Owner: WP-F.

Turns frame bits into a phase-continuous tone: the pitch comes from the bits and
the loudness from how many are set. It is NOT a codec: it never decodes speech
and implements no part of any real vocoder. It exists so engine and protocol
tests can run end to end (frame in, PCM out) with predictable audio and no
licensing or patent question. It is registered only inside
`registry.use_test_double()`.
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from sigsense_api.dvoice.events import VocoderId
from sigsense_api.dvoice.vocoder.base import FRAME_SAMPLES, SAMPLE_RATE, Availability, Vocoder, VocoderFrame

LICENCE_NOTE = "Test double: synthesises tones from frame bits. Not a codec; contains no third-party vocoder code."


def tone_params(bits: np.ndarray) -> tuple[float, float]:
    """(frequency Hz, amplitude 0..1) a frame maps to: 300..1800 Hz in 25 Hz steps."""
    b = np.asarray(bits, dtype=np.uint8) & 1
    pitch_bits = b[:6] if b.size >= 6 else np.pad(b, (0, 6 - b.size))
    index = int(pitch_bits @ (1 << np.arange(5, -1, -1)))  # 0..63
    freq = 300.0 + 25.0 * index
    amp = 0.1 + 0.6 * (float(b.sum()) / max(1, b.size))
    return freq, amp


class ToneVocoder(Vocoder):
    id: ClassVar[VocoderId] = VocoderId.IMBE_7200X4400
    priority: ClassVar[int] = 0

    def __init__(self) -> None:
        self._phase = 0.0

    @classmethod
    def availability(cls) -> Availability:
        return Availability(True, "deterministic test double", LICENCE_NOTE, "tone test double")

    def decode(self, frame: VocoderFrame) -> np.ndarray:
        if frame.erased:
            self._phase = 0.0
            return np.zeros(FRAME_SAMPLES, dtype=np.int16)
        freq, amp = tone_params(frame.bits)
        n = np.arange(FRAME_SAMPLES)
        angle = self._phase + 2 * np.pi * freq * n / SAMPLE_RATE
        self._phase = float((self._phase + 2 * np.pi * freq * FRAME_SAMPLES / SAMPLE_RATE) % (2 * np.pi))
        return np.round(np.sin(angle) * amp * 32767 * 0.5).astype(np.int16)

    def reset(self) -> None:
        self._phase = 0.0


_FOR_ID: dict[VocoderId, type[ToneVocoder]] = {}


def tone_backend_for(vocoder_id: VocoderId) -> type[ToneVocoder]:
    """A ToneVocoder subclass serving `vocoder_id` (one per id, created once)."""
    cls = _FOR_ID.get(vocoder_id)
    if cls is None:
        cls = type(f"ToneVocoder_{vocoder_id.value}", (ToneVocoder,), {"id": vocoder_id})
        _FOR_ID[vocoder_id] = cls
    return cls
