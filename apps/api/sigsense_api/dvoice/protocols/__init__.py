"""Protocol decoders (CONTRACT v1). Each subpackage exports `DECODERS`."""

from __future__ import annotations

import importlib

PROTOCOL_PACKAGES = ("p25p1", "p25p2", "dmr", "nxdn", "dstar", "ysf", "m17")


def all_decoders() -> dict[str, type]:
    """Map protocol value (e.g. 'dmr') -> decoder class, over every subpackage."""
    found: dict[str, type] = {}
    for name in PROTOCOL_PACKAGES:
        module = importlib.import_module(f"sigsense_api.dvoice.protocols.{name}")
        for cls in module.DECODERS:
            found[cls.protocol.value] = cls
    return found
