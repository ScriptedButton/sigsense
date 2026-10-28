"""Protocol decoder interface (CONTRACT v1). Owner: architect (WP-G consumes).

A decoder is a state machine fed `SymbolBlock`s from the DSP front end. It finds
frames (using `sync_specs`), runs FEC/CRC (via dvoice.fec), parses link-control /
signalling into the typed events of events.py, and hands vocoder frames to the
vocoder layer. It never touches audio output, sockets or files: it only calls
`emit(event_type, payload)`.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Optional

from sigsense_api.dvoice.dsp.types import SymbolBlock
from sigsense_api.dvoice.events import Modulation, Protocol, VoiceFrame
from sigsense_api.dvoice.vocoder.base import Vocoder
from sigsense_api.dvoice.events import VocoderId

# emit("call_start", Call(...)); emit("voice", VoiceFrame(...)); types per events.EVENT_TYPES + "voice"
EmitFn = Callable[[str, Any], None]
VocoderProvider = Callable[[VocoderId], Vocoder]


@dataclass
class ProtocolConfig:
    """Per-decoder options set by the engine from EngineConfig."""

    channel: str = "ch0"
    frequency_hz: Optional[float] = None
    talkgroup_labels: dict[int, str] = field(default_factory=dict)
    decode_data: bool = False  # data/PDU payload decoding (off by default)
    hang_time_s: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)


class ProtocolDecoder(abc.ABC):
    protocol: ClassVar[Protocol]
    symbol_rate: ClassVar[float]
    modulation: ClassVar[Modulation]
    sync_specs: ClassVar[tuple[str, ...]]  # keys of sync.patterns.SYNC_SPECS this decoder wants
    timeslots: ClassVar[int] = 1

    def __init__(
        self,
        emit: EmitFn,
        vocoders: VocoderProvider,
        config: Optional[ProtocolConfig] = None,
    ) -> None:
        self.emit = emit
        self.vocoders = vocoders
        self.config = config or ProtocolConfig()

    @abc.abstractmethod
    def feed(self, block: SymbolBlock) -> None:
        """Consume symbols; emit events. Must be safe for arbitrary block sizes/boundaries."""

    @abc.abstractmethod
    def reset(self) -> None:
        """Drop all per-channel state (retune, source change)."""

    def stats(self) -> dict[str, Any]:
        """Counters for LinkStats: frames_ok, frames_bad, fec_corrected, fec_failed, sync_ratio."""
        return {}
