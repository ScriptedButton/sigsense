"""Vocoder layer interface (CONTRACT v1). Owner: WP-F.

Voice frames leave the protocol decoders as `VocoderFrame`s (FEC already
applied by the protocol layer) and come back as 160-sample int16 PCM at 8 kHz.

LICENCE / PATENT STATUS IS PART OF THE CONTRACT: every backend reports
`availability()` with a human-readable `licence_note`, the UI shows it, and a
backend that cannot be shipped or loaded reports available=False so the engine
degrades to "calls and metadata without audio" (never a crash, never silent
failure). See docs/digital-voice-architecture.md section 7.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import ClassVar, Optional

import numpy as np

from sigsense_api.dvoice.events import Protocol, VocoderId


@dataclass(frozen=True)
class Availability:
    available: bool
    reason: str = ""  # why not, or which library/device is in use
    licence_note: str = ""  # shown in the UI; never empty for a shipped backend
    backend: str = ""  # e.g. "libmbe (user installed)", "DVSI AMBE-3000 (serial)"


@dataclass
class VocoderFrame:
    vocoder: VocoderId
    bits: np.ndarray  # uint8 0/1 hard bits, FEC-decoded parameter bits for the codec (e.g. 88 for IMBE, 49 for AMBE+2)
    protocol: Protocol = Protocol.UNKNOWN
    soft: Optional[np.ndarray] = None  # per-bit reliabilities 0..255 when the front end has them
    fec_errors: int = 0  # errors the protocol layer corrected in this frame's FEC words
    erased: bool = False  # frame declared lost (repeat/mute logic applies)
    # Raw (pre-FEC) bits for backends that do their own FEC (hardware vocoders), if the protocol layer kept them.
    raw_bits: Optional[np.ndarray] = None


FRAME_SAMPLES = 160  # 20 ms @ 8 kHz
SAMPLE_RATE = 8000


def pack_bits(bits: np.ndarray) -> bytes:
    """0/1 hard bits (MSB first) -> bytes, zero-padded on the right to a whole byte."""
    return np.packbits(np.asarray(bits, dtype=np.uint8) & 1).tobytes()


class Vocoder(abc.ABC):
    id: ClassVar[VocoderId]
    # Lower runs first when several backends serve one codec (the registry sorts on it).
    priority: ClassVar[int] = 50
    # decode() returns this many samples per VocoderFrame (a whole number of 20 ms frames;
    # Codec2 1600 spans 40 ms per codec frame, so it returns 320).
    samples_per_decode: ClassVar[int] = FRAME_SAMPLES
    # Parameter bits a VocoderFrame must carry for this codec (None = backend decides).
    bits_per_frame: ClassVar[Optional[int]] = None

    @classmethod
    @abc.abstractmethod
    def availability(cls) -> Availability:
        """Cheap, side-effect free: can this backend run on this machine, and under what terms?"""

    @abc.abstractmethod
    def decode(self, frame: VocoderFrame) -> np.ndarray:
        """-> int16 array of FRAME_SAMPLES samples @ SAMPLE_RATE. Stateful per instance (one instance per call/slot)."""

    def reset(self) -> None:
        """Clear synthesis state between calls."""

    def close(self) -> None:
        """Release native resources. Safe to call twice; instances are not reused after close()."""


@dataclass
class UnavailableVocoder(Vocoder):
    """Returned by the registry when a backend can't run: reports why, produces silence."""

    vocoder_id: VocoderId
    why: Availability = field(default_factory=lambda: Availability(False, "not implemented"))

    id: ClassVar[VocoderId] = VocoderId.IMBE_7200X4400  # placeholder; see `vocoder_id`

    @classmethod
    def availability(cls) -> Availability:
        return Availability(False, "unavailable")

    def decode(self, frame: VocoderFrame) -> np.ndarray:
        return np.zeros(FRAME_SAMPLES, dtype=np.int16)
