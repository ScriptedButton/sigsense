"""Licence and patent notice data for the UI and /dvoice/capabilities (CONTRACT v1). Owner: WP-F.

`licence_notices()` is the single source of truth for what the UI tells users
about each audio backend: whether it is active on this machine, who supplied it,
under what licence, and the patent position as understood by the project. It
is data (JSON-serialisable dicts), not prose baked into the UI.
"""

from __future__ import annotations

from typing import Any

from sigsense_api.dvoice.vocoder import local_codec, registry

# What is true of the build regardless of which backends are active.
POLICY: dict[str, Any] = {
    "ships_ambe": False,
    "ships_imbe_in_server": False,
    "ships_proprietary_vocoder_binaries": False,
    "encryption": "Encrypted calls are detected and reported; SigSense never attempts to decrypt, recover keys or accept keys.",
    "legal": "Not legal advice. Monitoring rules differ by country and by service; you are responsible for complying with yours.",
    "browser_p25_wasm": (
        "The existing browser P25 build vendors an IMBE decoder (WASM) separately from this server layer; "
        "see apps/web/src/lib/sdr/p25/vendor/mbe/PATENT-NOTICE.md."
    ),
}

# Facts per backend class name that availability() alone does not carry.
_META: dict[str, dict[str, str]] = {
    "Codec2Vocoder": {"licence": "LGPL-2.1 (library installed on this machine)", "supplier": "system libcodec2", "codec": "Codec2 3200 (M17)"},
    "Codec2Vocoder1600": {"licence": "LGPL-2.1 (library installed on this machine)", "supplier": "system libcodec2", "codec": "Codec2 1600 (M17)"},
    "ExternalImbe": {"licence": "set by the library you supply", "supplier": "you (local build or $SIGSENSE_MBE_LIB)", "codec": "IMBE 7200x4400 (P25 Phase 1)"},
    "ExternalImbe7100": {"licence": "set by the library you supply", "supplier": "you (local build or $SIGSENSE_MBE_LIB)", "codec": "IMBE 7100x4400"},
    "ExternalAmbePlus2": {"licence": "set by the library you supply", "supplier": "you (local build or $SIGSENSE_AMBE_LIB)", "codec": "AMBE+2 3600x2450 (DMR, NXDN, YSF, P25 Phase 2)"},
    "ExternalAmbe": {"licence": "set by the library you supply", "supplier": "you (local build or $SIGSENSE_AMBE_LIB)", "codec": "AMBE 3600x2400 (D-STAR)"},
    "Dvsi3000": {"licence": "DVSI hardware (licensed silicon)", "supplier": "your DVSI AMBE-3000 device", "codec": "AMBE / AMBE+2"},
}


def licence_notices() -> dict[str, Any]:
    backends = []
    for cls, avail in registry.all_availabilities():
        meta = _META.get(cls.__name__, {})
        backends.append(
            {
                "name": cls.__name__,
                "vocoder": cls.id.value,
                "codec": meta.get("codec", cls.id.value),
                "active": avail.available,
                "backend": avail.backend,
                "reason": avail.reason,
                "licence": meta.get("licence", ""),
                "supplied_by": meta.get("supplier", ""),
                "shipped_with_sigsense": False,
                "licence_note": avail.licence_note,
            }
        )
    return {"backends": backends, "policy": POLICY, "local_codec": local_codec.local_codec_status()}
