"""Shared FEC result type (CONTRACT v1). Owner: WP-A."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class FecResult:
    """Outcome of decoding one FEC block.

    data:      decoded information bits, uint8 array of 0/1 (length per function).
    corrected: number of bit errors corrected (0 for a clean block).
    ok:        False when the block was uncorrectable / failed its check; `data`
               is then best-effort and must not be trusted.
    """

    data: np.ndarray
    corrected: int = 0
    ok: bool = True
