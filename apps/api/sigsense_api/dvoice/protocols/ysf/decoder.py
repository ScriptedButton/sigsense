"""WP-E (CONTRACT v1). Owner: ysf0."""

from __future__ import annotations

from sigsense_api.dvoice.dsp.types import SymbolBlock
from sigsense_api.dvoice.events import Modulation, Protocol
from sigsense_api.dvoice.protocols.base import ProtocolDecoder


class YsfDecoder(ProtocolDecoder):
    protocol = Protocol.YSF
    symbol_rate = 4800.0
    modulation = Modulation.C4FM
    sync_specs = ("ysf_fsw",)
    timeslots = 1

    def feed(self, block: SymbolBlock) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError
