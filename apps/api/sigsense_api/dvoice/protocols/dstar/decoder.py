"""WP-E (CONTRACT v1). Owner: dstar0."""

from __future__ import annotations

from sigsense_api.dvoice.dsp.types import SymbolBlock
from sigsense_api.dvoice.events import Modulation, Protocol
from sigsense_api.dvoice.protocols.base import ProtocolDecoder


class DstarDecoder(ProtocolDecoder):
    protocol = Protocol.DSTAR
    symbol_rate = 4800.0
    modulation = Modulation.GMSK
    sync_specs = ("dstar_header", "dstar_sync")
    timeslots = 1

    def feed(self, block: SymbolBlock) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError
