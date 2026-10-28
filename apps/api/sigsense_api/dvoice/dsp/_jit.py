"""numba wrapper with a pure-Python fallback. Owner: WP-B.

Kernels are written as plain loops over numpy arrays so they run either way:
compiled with numba (the default, cached on disk) or as ordinary Python when
numba is missing or `SIGSENSE_DVOICE_NUMBA=0` (used by the numba on/off parity
test; the Python path is correct but slow, so keep inputs small there).
"""

from __future__ import annotations

import os

try:  # pragma: no cover - exercised by whichever path the environment takes
    if os.environ.get("SIGSENSE_DVOICE_NUMBA", "1") == "0":
        raise ImportError("numba disabled by SIGSENSE_DVOICE_NUMBA=0")
    from numba import njit as _numba_njit

    HAVE_NUMBA = True

    def njit(*args, **kwargs):
        kwargs.setdefault("cache", True)
        kwargs.setdefault("nogil", True)
        if len(args) == 1 and callable(args[0]):
            return _numba_njit(**kwargs)(args[0])
        return _numba_njit(*args, **kwargs)

except Exception:  # numba missing/disabled -> plain Python
    HAVE_NUMBA = False

    def njit(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda f: f
