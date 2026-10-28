"""P25 Phase 1 IMBE 7200x4400 via an EXTERNAL library (CONTRACT v1). Owner: WP-F.

Route for IMBE: load an adapter library the USER installed (see external.py for
the C ABI and the environment variables) rather than vendoring one. SigSense
contains no IMBE synthesis code and bundles no vocoder binary. The existing
browser WASM build (apps/web/src/lib/sdr/p25/vendor/mbe) is a separate,
pre-existing artifact whose licence/patent handling is a documented decision
for the user, not something this module changes.
"""

from __future__ import annotations

from sigsense_api.dvoice.events import VocoderId
from sigsense_api.dvoice.vocoder.backends.external import ExternalVocoder
from sigsense_api.dvoice.vocoder.registry import register

LICENCE_NOTE = (
    "IMBE (P25 Phase 1) comes from a library you installed; SigSense ships none. "
    "mbelib-style IMBE decoders carry a patent notice (\"may be covered by patents\"); "
    "whether use is permitted where you are is your decision, and this is not legal advice."
)


@register
class ExternalImbe(ExternalVocoder):
    id = VocoderId.IMBE_7200X4400
    bits_per_frame = 88
    ENV_NAMES = ("SIGSENSE_MBE_LIB", "SIGSENSE_VOCODER_LIB")
    WHAT = "IMBE"
    LICENCE_NOTE = LICENCE_NOTE


@register
class ExternalImbe7100(ExternalVocoder):
    """IMBE 7100x4400 (same parameters in a different bit order; the adapter converts)."""

    id = VocoderId.IMBE_7100X4400
    bits_per_frame = 88
    ENV_NAMES = ("SIGSENSE_MBE_LIB", "SIGSENSE_VOCODER_LIB")
    WHAT = "IMBE 7100x4400"
    LICENCE_NOTE = LICENCE_NOTE
