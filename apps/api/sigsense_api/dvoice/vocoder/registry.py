"""Vocoder registry (CONTRACT v1). Owner: WP-F.

Backends register themselves on import (see backends/__init__.py). Each codec id
has an ordered list of backends (lower `priority` first, then registration
order); `get_vocoder` returns the first one whose `availability()` says it can
run, or an UnavailableVocoder that produces silence so the engine degrades to
"calls and metadata without audio".

Availability checks can dlopen libraries or touch serial ports, so results are
cached for a few seconds (`refresh()` drops the cache, e.g. after the user sets
an environment variable or plugs in hardware).
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

from sigsense_api.dvoice.events import VocoderId
from sigsense_api.dvoice.vocoder.base import Availability, UnavailableVocoder, Vocoder

AVAILABILITY_TTL_S = 5.0

_LOCK = threading.RLock()
_BACKENDS: dict[VocoderId, list[type[Vocoder]]] = {}
_CACHE: dict[type[Vocoder], tuple[float, Availability]] = {}
_clock: Callable[[], float] = time.monotonic


def register(cls: type[Vocoder]) -> type[Vocoder]:
    """Class decorator: add `cls` to its codec's backend list (idempotent)."""
    with _LOCK:
        backends = _BACKENDS.setdefault(cls.id, [])
        if cls not in backends:
            backends.append(cls)
            backends.sort(key=lambda b: b.priority)  # stable: registration order breaks ties
    return cls


def unregister(cls: type[Vocoder]) -> None:
    with _LOCK:
        backends = _BACKENDS.get(cls.id, [])
        if cls in backends:
            backends.remove(cls)
        _CACHE.pop(cls, None)


def refresh() -> None:
    """Forget cached availability results."""
    with _LOCK:
        _CACHE.clear()


def _availability(cls: type[Vocoder]) -> Availability:
    now = _clock()
    with _LOCK:
        hit = _CACHE.get(cls)
        if hit and now - hit[0] < AVAILABILITY_TTL_S:
            return hit[1]
    try:
        avail = cls.availability()
    except Exception as exc:  # a broken probe must never take the engine down
        avail = Availability(False, f"availability check failed: {type(exc).__name__}: {exc}", backend=cls.__name__)
    with _LOCK:
        _CACHE[cls] = (now, avail)
    return avail


def backends_for(vocoder_id: VocoderId) -> list[type[Vocoder]]:
    with _LOCK:
        return list(_BACKENDS.get(vocoder_id, []))


def get_vocoder(vocoder_id: VocoderId, prefer: Optional[str] = None) -> Vocoder:
    """A fresh instance of the best available backend, or an UnavailableVocoder."""
    why = Availability(False, "no backend registered")
    for cls in backends_for(vocoder_id):
        avail = _availability(cls)
        if not avail.available:
            why = avail
            continue
        if prefer is not None and prefer not in avail.backend:
            continue
        try:
            return cls()
        except Exception as exc:  # e.g. native library refused to create a decoder
            why = Availability(False, f"{avail.backend or cls.__name__} failed to start: {exc}", avail.licence_note, avail.backend)
    return UnavailableVocoder(vocoder_id, why)


def capabilities() -> dict[str, dict]:
    """vocoder id -> {available, reason, licence_note, backend} for /dvoice/capabilities."""
    out: dict[str, dict] = {}
    for vid in VocoderId:
        backends = backends_for(vid)
        if not backends:
            out[vid.value] = {
                "available": False,
                "reason": "no backend registered",
                "licence_note": "",
                "backend": "",
            }
            continue
        results = [_availability(b) for b in backends]
        best = next((a for a in results if a.available), None)
        if best is None:
            # Nothing can run: say why for every route, so the user sees what to configure.
            reasons = list(dict.fromkeys(a.reason for a in results if a.reason))
            note = next((a.licence_note for a in results if a.licence_note), "")
            out[vid.value] = {
                "available": False,
                "reason": " | ".join(reasons),
                "licence_note": note,
                "backend": results[0].backend,
            }
            continue
        out[vid.value] = {
            "available": True,
            "reason": best.reason,
            "licence_note": best.licence_note,
            "backend": best.backend,
        }
    return out


def all_availabilities() -> list[tuple[type[Vocoder], Availability]]:
    """Every registered backend with its current availability (for the notices/UI)."""
    with _LOCK:
        classes = [c for vid in VocoderId for c in _BACKENDS.get(vid, [])]
    return [(c, _availability(c)) for c in classes]


@contextmanager
def use_test_double(ids: Optional[list[VocoderId]] = None) -> Iterator[None]:
    """Temporarily put the deterministic ToneVocoder first for `ids` (default: all).

    For engine/protocol integration tests: lets the whole pipeline run end to
    end with no real codec, and with no licence or patent question.
    """
    from sigsense_api.dvoice.vocoder.backends.tone import tone_backend_for

    added = [tone_backend_for(vid) for vid in (ids or list(VocoderId))]
    for cls in added:
        register(cls)
    refresh()
    try:
        yield
    finally:
        for cls in added:
            unregister(cls)
        refresh()


# Importing the backends package registers every backend (must stay last: the
# backend modules import `register` from here).
from sigsense_api.dvoice.vocoder import backends as _backends  # noqa: E402,F401
