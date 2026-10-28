"""The user's locally built IMBE/AMBE adapter: where it lives, what it is, whether it works.

SigSense does not ship a voice codec. `apps/api/scripts/build_mbe_adapter.py`
builds one on the user's own machine, after a warning and an explicit
confirmation, into `<data home>/codecs/libsigsense_mbe.so` and records a
`manifest.json` next to it. This module is the read side of that: the backend
loader falls back to the default location when no environment variable is set,
and the UI asks `local_codec_status()` what to show.

Nothing here downloads or builds anything.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from sigsense_api.dvoice.events import VocoderId

HOME_ENV = "SIGSENSE_CODEC_HOME"
LIB_NAME = "libsigsense_mbe.so"
MANIFEST_NAME = "manifest.json"
LAST_ERROR_NAME = "last_error.txt"

BUILD_COMMAND = "python3 apps/api/scripts/build_mbe_adapter.py"

WARNING = (
    "The IMBE/AMBE voice codecs may be covered by patents, and the codec source the build downloads is "
    "someone else's project with its own licence. Check the law where you live before you download, build or "
    "use it. SigSense does not ship it, and the locally built library is for your own use: do not redistribute "
    "it. This is not legal advice."
)


def data_home() -> Path:
    """Root for everything the helper writes. Overridable so tests never touch the real home."""
    override = os.environ.get(HOME_ENV, "").strip()
    return Path(override).expanduser() if override else Path.home() / ".local" / "share" / "sigsense"


def codec_dir() -> Path:
    return data_home() / "codecs"


def build_dir() -> Path:
    return data_home() / "codec-build"


def default_library_path() -> Path:
    return codec_dir() / LIB_NAME


def read_manifest() -> Optional[dict[str, Any]]:
    try:
        return json.loads((codec_dir() / MANIFEST_NAME).read_text())
    except (OSError, ValueError):
        return None


def read_last_error() -> str:
    try:
        return (codec_dir() / LAST_ERROR_NAME).read_text().strip()
    except OSError:
        return ""


def local_codec_status() -> dict[str, Any]:
    """JSON-serialisable status for the UI and /dvoice/capabilities."""
    # Imported here: external.py asks this module for the default path, so a top-level import would loop.
    from sigsense_api.dvoice.vocoder.backends import external

    imbe_path, imbe_src = external.configured_path(("SIGSENSE_MBE_LIB", "SIGSENSE_VOCODER_LIB"))
    ambe_path, ambe_src = external.configured_path(("SIGSENSE_AMBE_LIB", "SIGSENSE_VOCODER_LIB"))
    path = imbe_path or ambe_path
    source = imbe_src or ambe_src
    default = default_library_path()

    codecs: dict[str, bool] = {v.value: False for v in external.CODEC_IDS}
    description = ""
    error = ""
    for p in {imbe_path, ambe_path} - {""}:
        lib, err = external.load_adapter(p)
        if lib is None:
            error = err
            continue
        description = description or external.describe(lib)
        for vocoder_id, codec in external.CODEC_IDS.items():
            probe = lib.sigsense_vocoder_create(codec)
            if probe:
                lib.sigsense_vocoder_destroy(probe)
                codecs[vocoder_id.value] = True
    if not error and not path:
        error = read_last_error()

    return {
        "installed": any(codecs.values()),
        "path": path or None,
        "source": source or None,  # an environment variable name, or "default install location"
        "default_path": str(default),
        "default_present": default.exists(),
        "library": description,
        "manifest": read_manifest(),
        "codecs": codecs,
        "protocols_with_audio": _protocols(codecs),
        "last_error": error,
        "build_command": BUILD_COMMAND + " --i-understand",
        "uninstall_command": BUILD_COMMAND + " --uninstall",
        "warning": WARNING,
    }


def _protocols(codecs: dict[str, bool]) -> list[str]:
    protocols = []
    if codecs.get(VocoderId.IMBE_7200X4400.value):
        protocols.append("P25 Phase 1")
    if codecs.get(VocoderId.AMBE_PLUS2_3600X2450.value):
        protocols += ["DMR", "NXDN", "YSF", "P25 Phase 2"]
    if codecs.get(VocoderId.AMBE_3600X2400.value):
        protocols.append("D-STAR")
    return protocols
