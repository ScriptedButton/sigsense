"""Front-end output types (CONTRACT v1). Owner: WP-B (architect-defined).

Dibit convention (all supported protocols, identical to the DSD family so the
public sync words apply verbatim): 4-level symbols map
    +3 -> 01 (value 1)    +1 -> 00 (value 0)    -1 -> 10 (value 2)    -3 -> 11 (value 3)
so a polarity inversion flips the symbol sign and maps dibit d -> d ^ 0b10.
2-level (D-STAR GMSK) streams use values {1 (=+1), 3 (=-1)} only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from sigsense_api.dvoice.events import Modulation


@dataclass
class SymbolBlock:
    """A contiguous run of recovered symbols from one channel."""

    dibits: np.ndarray  # uint8, values 0..3 per the convention above
    soft: Optional[np.ndarray]  # float32, same length: normalized level, nominal -3..+3 (sign = polarity)
    symbol_rate: float  # symbols/second
    first_index: int  # absolute symbol index of dibits[0] since the channel started
    t0: float  # stream time (seconds) of the first symbol
    modulation: Modulation = Modulation.C4FM
    polarity_inverted: bool = False  # front end already un-inverted? NO: reports it was seen
    meta: dict[str, Any] = field(default_factory=dict)  # snr_db, freq_error_hz, level, ...
