"""Loader for a USER-INSTALLED vocoder library (CONTRACT v1). Owner: WP-F.

SigSense ships no IMBE/AMBE/AMBE+2 implementation. If you have a vocoder
library you are entitled to use, point SigSense at a small adapter exposing the
C ABI below and it will route voice frames through it. The adapter can wrap
whatever you have (an mbelib-style library, a hardware driver, ...); that
wrapper and the codec behind it are yours, and so are any licence or patent
questions they raise.

C ABI, version 1 (all functions `extern "C"`, cdecl):

    int         sigsense_vocoder_abi_version(void);                 /* must return 1 */
    void*       sigsense_vocoder_create(int codec);                 /* NULL if `codec` unsupported */
    void        sigsense_vocoder_destroy(void* h);
    int         sigsense_vocoder_decode(void* h,
                                        const uint8_t* bits, int nbits,   /* one byte per bit, 0 or 1 */
                                        int16_t* pcm, int pcm_cap);       /* 8 kHz mono */
                                        /* returns samples written (160 per 20 ms frame), or < 0 on error */
    int         sigsense_vocoder_conceal(void* h, int16_t* pcm, int pcm_cap);   /* OPTIONAL: lost-frame fill */
    const char* sigsense_vocoder_describe(void);                                /* OPTIONAL: name/version text */

`codec` values: 1 = IMBE 7200x4400 (P25 Phase 1), 2 = IMBE 7100x4400,
3 = AMBE 3600x2400 (D-STAR), 4 = AMBE+2 3600x2450 (DMR, NXDN, YSF, P25 Phase 2).
`bits` are the FEC-decoded parameter bits SigSense's protocol layer produced
(88 for IMBE, 49 for AMBE/AMBE+2); the adapter does any codec-specific
re-ordering. One handle = one call/slot (synthesis state lives in the handle).
A handle is used by one thread at a time (SigSense serialises calls per handle).

Library selection (SigSense never searches system library paths, so it cannot
pick up an unrelated library by accident):
    IMBE : $SIGSENSE_MBE_LIB   then $SIGSENSE_VOCODER_LIB
    AMBE : $SIGSENSE_AMBE_LIB  then $SIGSENSE_VOCODER_LIB
    else : the library the build helper (scripts/build_mbe_adapter.py) installed
           at <data home>/codecs/libsigsense_mbe.so, if it is there (see
           local_codec.py). That directory is written only by that helper, after
           the user confirmed its warning.
Loading a library runs its initialisation code with this process's privileges:
only point these at files you built or trust.
"""

from __future__ import annotations

import ctypes
import os
import threading
from typing import Optional

import numpy as np

from sigsense_api.dvoice.events import VocoderId
from sigsense_api.dvoice.vocoder.base import FRAME_SAMPLES, Availability, Vocoder, VocoderFrame

ABI_VERSION = 1
CODEC_IDS: dict[VocoderId, int] = {
    VocoderId.IMBE_7200X4400: 1,
    VocoderId.IMBE_7100X4400: 2,
    VocoderId.AMBE_3600X2400: 3,
    VocoderId.AMBE_PLUS2_3600X2450: 4,
}

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[Optional[ctypes.CDLL], str]] = {}


DEFAULT_LOCATION_LABEL = "default install location"


def configured_path(env_names: tuple[str, ...]) -> tuple[str, str]:
    """(path, source) for the first variable that is set, else the helper-installed
    library if present, else ("", ""). The source is an environment variable name
    or DEFAULT_LOCATION_LABEL. An environment variable always wins."""
    for name in env_names:
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    from sigsense_api.dvoice.vocoder import local_codec  # lazy: local_codec imports this module lazily too

    default = local_codec.default_library_path()
    if default.is_file():
        return str(default), DEFAULT_LOCATION_LABEL
    return "", ""


def _bind(lib: ctypes.CDLL) -> ctypes.CDLL:
    lib.sigsense_vocoder_abi_version.argtypes = []
    lib.sigsense_vocoder_abi_version.restype = ctypes.c_int
    lib.sigsense_vocoder_create.argtypes = [ctypes.c_int]
    lib.sigsense_vocoder_create.restype = ctypes.c_void_p
    lib.sigsense_vocoder_destroy.argtypes = [ctypes.c_void_p]
    lib.sigsense_vocoder_destroy.restype = None
    lib.sigsense_vocoder_decode.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int16),
        ctypes.c_int,
    ]
    lib.sigsense_vocoder_decode.restype = ctypes.c_int
    try:  # optional
        lib.sigsense_vocoder_conceal.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16), ctypes.c_int]
        lib.sigsense_vocoder_conceal.restype = ctypes.c_int
    except AttributeError:
        pass
    try:  # optional
        lib.sigsense_vocoder_describe.argtypes = []
        lib.sigsense_vocoder_describe.restype = ctypes.c_char_p
    except AttributeError:
        pass
    return lib


def load_adapter(path: str) -> tuple[Optional[ctypes.CDLL], str]:
    """Open and validate an adapter library. Returns (library or None, error text). Cached per path."""
    with _LOCK:
        if path in _CACHE:
            return _CACHE[path]
        try:
            lib = _bind(ctypes.CDLL(path))
            version = lib.sigsense_vocoder_abi_version()
            if version != ABI_VERSION:
                result: tuple[Optional[ctypes.CDLL], str] = (None, f"adapter ABI version {version}, need {ABI_VERSION}")
            else:
                result = (lib, "")
        except (OSError, AttributeError) as exc:
            result = (None, f"cannot use {path} as a SigSense vocoder adapter: {exc}")
        _CACHE[path] = result
        return result


def forget_adapters() -> None:
    """Drop cached load results (tests, or after the user rebuilds the adapter)."""
    with _LOCK:
        _CACHE.clear()


def describe(lib: ctypes.CDLL) -> str:
    fn = getattr(lib, "sigsense_vocoder_describe", None)
    if fn is None:
        return ""
    try:
        text = fn()
        return text.decode("utf-8", "replace") if text else ""
    except Exception:
        return ""


def external_availability(
    vocoder_id: VocoderId, env_names: tuple[str, ...], what: str, licence_note: str
) -> Availability:
    path, env = configured_path(env_names)
    if not path:
        return Availability(
            False,
            f"not configured: build the local codec with scripts/build_mbe_adapter.py (see "
            f"docs/digital-voice-codec-setup.md), or set {' or '.join('$' + n for n in env_names)} to an adapter "
            f"library for {what} that you installed (SigSense does not ship one).",
            licence_note,
            f"{what} (not configured)",
        )
    lib, err = load_adapter(path)
    if lib is None:
        return Availability(False, err, licence_note, f"{what} adapter ({path})")
    probe = lib.sigsense_vocoder_create(CODEC_IDS[vocoder_id])
    if not probe:
        return Availability(False, f"{path} does not support {vocoder_id.value}", licence_note, f"{what} adapter ({path})")
    lib.sigsense_vocoder_destroy(probe)
    detail = describe(lib)
    return Availability(
        True,
        f"using {path} via " + (env if env == DEFAULT_LOCATION_LABEL else f"${env}") + (f" ({detail})" if detail else ""),
        licence_note,
        f"{what} adapter ({path})",
    )


class ExternalVocoder(Vocoder):
    """One call/slot's decoder handle inside a user-installed adapter."""

    ENV_NAMES: tuple[str, ...] = ()
    WHAT = "vocoder"
    LICENCE_NOTE = ""

    def __init__(self) -> None:
        path, _ = configured_path(self.ENV_NAMES)
        lib, err = load_adapter(path) if path else (None, "not configured")
        if lib is None:
            raise RuntimeError(err)
        self._lib = lib
        self._lock = threading.Lock()
        self._handle = lib.sigsense_vocoder_create(CODEC_IDS[self.id])
        if not self._handle:
            raise RuntimeError(f"adapter refused codec {self.id.value}")
        self._last = np.zeros(FRAME_SAMPLES, dtype=np.int16)

    @classmethod
    def availability(cls) -> Availability:
        return external_availability(cls.id, cls.ENV_NAMES, cls.WHAT, cls.LICENCE_NOTE)

    def _call_decode(self, bits: np.ndarray) -> Optional[np.ndarray]:
        data = (ctypes.c_ubyte * bits.size).from_buffer_copy((bits & 1).astype(np.uint8).tobytes())
        out = (ctypes.c_int16 * FRAME_SAMPLES)()
        with self._lock:
            if not self._handle:
                return None
            n = self._lib.sigsense_vocoder_decode(self._handle, data, bits.size, out, FRAME_SAMPLES)
        if n < 0:
            return None
        pcm = np.frombuffer(out, dtype=np.int16).copy()
        pcm[n:] = 0  # an adapter that wrote fewer samples leaves silence, never stale memory
        return pcm

    def _call_conceal(self) -> Optional[np.ndarray]:
        fn = getattr(self._lib, "sigsense_vocoder_conceal", None)
        if fn is None:
            return None
        out = (ctypes.c_int16 * FRAME_SAMPLES)()
        with self._lock:
            if not self._handle:
                return None
            n = fn(self._handle, out, FRAME_SAMPLES)
        if n < 0:
            return None
        pcm = np.frombuffer(out, dtype=np.int16).copy()
        pcm[n:] = 0
        return pcm

    def decode(self, frame: VocoderFrame) -> np.ndarray:
        pcm = self._call_conceal() if frame.erased else self._call_decode(frame.bits)
        if pcm is None:
            return np.zeros(FRAME_SAMPLES, dtype=np.int16)  # error or lost with no adapter concealment
        self._last = pcm
        return pcm

    def reset(self) -> None:
        with self._lock:
            if self._handle:
                self._lib.sigsense_vocoder_destroy(self._handle)
            self._handle = self._lib.sigsense_vocoder_create(CODEC_IDS[self.id])

    def close(self) -> None:
        with self._lock:
            if self._handle:
                self._lib.sigsense_vocoder_destroy(self._handle)
                self._handle = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
