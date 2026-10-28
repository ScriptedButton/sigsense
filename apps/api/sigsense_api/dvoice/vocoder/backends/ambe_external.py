"""AMBE+2 / AMBE via an EXTERNAL library or DVSI hardware (CONTRACT v1). Owner: WP-F.

AMBE/AMBE+2 are the patent-exposed codecs (see the architecture doc, section 7).
Routes, safest first:
  1. DVSI AMBE-3000 class USB vocoder: the chip is licensed by DVSI, so the
     user's hardware carries the rights. In this release `Dvsi3000` is only an
     interface stub (see below); it never reports itself available.
  2. A user-installed adapter library (see external.py for the ABI): the same
     'we do not ship it' stance as IMBE.
No backend in this module is usable out of the box. With neither route
configured, DMR/NXDN/YSF/P25-P2/D-STAR calls are decoded and reported, and audio
is reported as unavailable (the engine says so; nothing crashes).
"""

from __future__ import annotations

import os
from typing import ClassVar

import numpy as np

from sigsense_api.dvoice.events import VocoderId
from sigsense_api.dvoice.vocoder.backends.external import ExternalVocoder
from sigsense_api.dvoice.vocoder.base import Availability, Vocoder, VocoderFrame
from sigsense_api.dvoice.vocoder.registry import register

AMBE_LICENCE_NOTE = (
    "AMBE/AMBE+2 are patent-exposed codecs (for example US 8,359,197, listed active until about 2028). "
    "SigSense ships no AMBE implementation and enables none by default. Audio is only produced through a "
    "library or DVSI hardware you supply, and the legal responsibility for using it is yours "
    "(not legal advice)."
)


@register
class ExternalAmbePlus2(ExternalVocoder):
    id = VocoderId.AMBE_PLUS2_3600X2450
    bits_per_frame = 49
    ENV_NAMES = ("SIGSENSE_AMBE_LIB", "SIGSENSE_VOCODER_LIB")
    WHAT = "AMBE+2"
    LICENCE_NOTE = AMBE_LICENCE_NOTE


@register
class ExternalAmbe(ExternalVocoder):
    id = VocoderId.AMBE_3600X2400
    bits_per_frame = 49
    ENV_NAMES = ("SIGSENSE_AMBE_LIB", "SIGSENSE_VOCODER_LIB")
    WHAT = "AMBE"
    LICENCE_NOTE = AMBE_LICENCE_NOTE


ENV_DVSI_PORT = "SIGSENSE_DVSI_PORT"


@register
class Dvsi3000(Vocoder):
    """Hardware AMBE-3000 over a serial port ($SIGSENSE_DVSI_PORT): INTERFACE STUB ONLY.

    The packet protocol is not implemented here: neither the architecture doc
    nor any public, redistributable source this project may use defines it, and
    we do not write drivers from vendor-confidential material. When DVSI's
    protocol documentation is available to the project, implement
    `decode()` over a pyserial transport (the DVSI chip does its own AMBE
    decoding of the raw FEC-coded frame, so `VocoderFrame.raw_bits` is what the
    protocol layer must keep for this backend). Until then this class only
    reports, truthfully, why it cannot run.
    """

    id: ClassVar[VocoderId] = VocoderId.AMBE_PLUS2_3600X2450
    priority: ClassVar[int] = 40  # would be preferred over a library once implemented

    @classmethod
    def availability(cls) -> Availability:
        port = os.environ.get(ENV_DVSI_PORT, "").strip()
        why = (
            f"${ENV_DVSI_PORT}={port} is set, but " if port else f"${ENV_DVSI_PORT} is not set; and "
        ) + "the DVSI AMBE-3000 serial driver is not implemented in this release (interface stub only)."
        return Availability(False, why, AMBE_LICENCE_NOTE, "DVSI AMBE-3000 (not implemented)")

    def decode(self, frame: VocoderFrame) -> np.ndarray:
        raise NotImplementedError("Dvsi3000 is an interface stub; check availability() first")
