"""M17 voice: Codec2 (CONTRACT v1). Owner: WP-F (shared with WP-E for M17 framing).

Codec2 is open: the library is LGPL-2.1 and its authors state no known patent
encumbrance. SigSense does not bundle it. This backend loads a libcodec2 the
user (or their distribution) installed, through ctypes:

  1. $SIGSENSE_CODEC2_LIB (explicit path), else
  2. ctypes.util.find_library("codec2") and the usual sonames.

Frame contract: one `VocoderFrame` carries the parameter bits of ONE Codec2
frame as 0/1 values, MSB first (the order Codec2's byte API uses):
  * 3200 bps: 64 bits -> 160 samples (20 ms)
  * 1600 bps: 64 bits -> 320 samples (40 ms); `samples_per_decode` is 320
An M17 stream frame carries TWO 3200-bps codec frames (128 payload bits); the
M17 protocol layer must hand them to the vocoder as two 64-bit frames.
A frame with the wrong number of bits is treated as lost (silence), not an error.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import threading
from typing import ClassVar, Optional

import numpy as np

from sigsense_api.dvoice.events import VocoderId
from sigsense_api.dvoice.vocoder.base import FRAME_SAMPLES, Availability, Vocoder, VocoderFrame, pack_bits
from sigsense_api.dvoice.vocoder.registry import register

ENV_LIB = "SIGSENSE_CODEC2_LIB"
CODEC2_MODE_3200 = 0
CODEC2_MODE_1600 = 2

LICENCE_NOTE = (
    "Codec2 (LGPL-2.1; no known patent encumbrance per its authors). "
    "Loaded from a libcodec2 installed on this machine; SigSense does not bundle it."
)

_LIB_LOCK = threading.Lock()
_LIB: Optional[ctypes.CDLL] = None
_LIB_PATH = ""
_LIB_ERROR = ""
_LIB_TRIED_FOR = object()  # env value the cached result belongs to


def _candidates() -> list[str]:
    explicit = os.environ.get(ENV_LIB, "").strip()
    if explicit:
        return [explicit]  # an explicit path is authoritative: don't silently use another library
    found = [ctypes.util.find_library("codec2")]
    return [c for c in found + ["libcodec2.so.1.2", "libcodec2.so.1", "libcodec2.so", "libcodec2.dylib"] if c]


def _bind(lib: ctypes.CDLL) -> ctypes.CDLL:
    lib.codec2_create.argtypes = [ctypes.c_int]
    lib.codec2_create.restype = ctypes.c_void_p
    lib.codec2_destroy.argtypes = [ctypes.c_void_p]
    lib.codec2_destroy.restype = None
    lib.codec2_bits_per_frame.argtypes = [ctypes.c_void_p]
    lib.codec2_bits_per_frame.restype = ctypes.c_int
    lib.codec2_samples_per_frame.argtypes = [ctypes.c_void_p]
    lib.codec2_samples_per_frame.restype = ctypes.c_int
    lib.codec2_decode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_short), ctypes.POINTER(ctypes.c_ubyte)]
    lib.codec2_decode.restype = None
    return lib


def load_library() -> tuple[Optional[ctypes.CDLL], str, str]:
    """(library or None, path/soname used, error text). Cached per $SIGSENSE_CODEC2_LIB value."""
    global _LIB, _LIB_PATH, _LIB_ERROR, _LIB_TRIED_FOR
    key = os.environ.get(ENV_LIB, "")
    with _LIB_LOCK:
        if _LIB_TRIED_FOR == key:
            return _LIB, _LIB_PATH, _LIB_ERROR
        _LIB, _LIB_PATH, _LIB_ERROR, _LIB_TRIED_FOR = None, "", "", key
        errors = []
        for name in _candidates():
            try:
                _LIB = _bind(ctypes.CDLL(name))
                _LIB_PATH = name
                break
            except (OSError, AttributeError) as exc:
                errors.append(f"{name}: {exc}")
        if _LIB is None:
            _LIB_ERROR = "; ".join(errors) or "libcodec2 not found"
        return _LIB, _LIB_PATH, _LIB_ERROR


class _Codec2Base(Vocoder):
    MODE: ClassVar[int]

    def __init__(self) -> None:
        lib, path, err = load_library()
        if lib is None:
            raise RuntimeError(f"libcodec2 unavailable: {err}")
        self._lib = lib
        self._lock = threading.Lock()
        self._state = lib.codec2_create(self.MODE)
        if not self._state:
            raise RuntimeError(f"libcodec2 refused mode {self.MODE} (disabled at build time?)")
        self._bits = int(lib.codec2_bits_per_frame(self._state))
        self._samples = int(lib.codec2_samples_per_frame(self._state))

    @classmethod
    def availability(cls) -> Availability:
        lib, path, err = load_library()
        if lib is None:
            return Availability(
                False,
                f"libcodec2 not found ({err}). Install it (e.g. apt install libcodec2-1.2) or set {ENV_LIB}.",
                LICENCE_NOTE,
                "libcodec2 (not found)",
            )
        state = lib.codec2_create(cls.MODE)
        if not state:
            return Availability(False, f"{path} was built without this Codec2 mode", LICENCE_NOTE, f"libcodec2 ({path})")
        lib.codec2_destroy(state)
        return Availability(True, f"using {path}", LICENCE_NOTE, f"libcodec2 ({path})")

    def decode(self, frame: VocoderFrame) -> np.ndarray:
        silent = np.zeros(self._samples, dtype=np.int16)
        if self._state is None or frame.erased or frame.bits.size != self._bits:
            return silent
        packed = pack_bits(frame.bits)
        out = (ctypes.c_short * self._samples)()
        buf = (ctypes.c_ubyte * len(packed)).from_buffer_copy(packed)
        with self._lock:
            if self._state is None:
                return silent
            self._lib.codec2_decode(self._state, out, buf)
        return np.frombuffer(out, dtype=np.int16).copy()

    def reset(self) -> None:
        """Codec2 keeps synthesis memory; a fresh state is the clean reset between calls."""
        with self._lock:
            if self._state is not None:
                self._lib.codec2_destroy(self._state)
            self._state = self._lib.codec2_create(self.MODE)

    def close(self) -> None:
        with self._lock:
            if self._state is not None:
                self._lib.codec2_destroy(self._state)
                self._state = None

    def __del__(self) -> None:  # best effort; close() is the supported path
        try:
            self.close()
        except Exception:
            pass


@register
class Codec2Vocoder(_Codec2Base):
    id = VocoderId.CODEC2_3200
    MODE = CODEC2_MODE_3200
    bits_per_frame = 64
    samples_per_decode = FRAME_SAMPLES


@register
class Codec2Vocoder1600(_Codec2Base):
    id = VocoderId.CODEC2_1600
    MODE = CODEC2_MODE_1600
    bits_per_frame = 64
    samples_per_decode = 2 * FRAME_SAMPLES
