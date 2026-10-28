"""Sync-word search (CONTRACT v1). Owner: WP-B.

Streaming exact-dibit search with a symbol-error tolerance, both polarities:

* a spec matches at position p when at most `max_errors` of its symbols differ from the
  received dibits (or from the inverted-polarity word, reported with `inverted=True`);
* matches that straddle block boundaries are found (the last `len-1` dibits are kept);
* overlapping candidate hits are merged: the one with the fewest errors wins (ties go to the
  longer word, then the earlier position), and a hit is only reported once `max_len` further
  symbols have been seen, so a better overlapping candidate in the next block can still win.
  `flush()` releases whatever is pending at end of stream.

Exact-dibit matching is deliberately the default. Sign-only matching tolerates inner/outer
level errors but is hopeless against random data at useful tolerances (a 24-symbol word with
4 allowed errors would false-alarm about once per 1300 symbols); see the false-alarm table in
tests/test_dvoice_dsp_sync.py. Because short words (M17, NXDN) still false-alarm often at
their configured tolerance, protocol decoders must confirm every hit with the frame's own
structure (NID/LICH/CRC) before acting on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np

from sigsense_api.dvoice.dsp._jit import njit
from sigsense_api.dvoice.events import Protocol
from sigsense_api.dvoice.sync.patterns import SyncSpec


@dataclass
class SyncHit:
    spec: str  # SyncSpec.name
    protocol: Protocol
    index: int  # absolute symbol index of the first symbol of the sync word
    errors: int
    inverted: bool


@njit
def _scan(stream, pattern, max_errors, out_pos, out_err):
    """Positions where `pattern` matches `stream` with <= max_errors mismatches."""
    n = stream.shape[0]
    m = pattern.shape[0]
    count = 0
    for p in range(n - m + 1):
        err = 0
        for k in range(m):
            if stream[p + k] != pattern[k]:
                err += 1
                if err > max_errors:
                    break
        if err <= max_errors and count < out_pos.shape[0]:
            out_pos[count] = p
            out_err[count] = err
            count += 1
    return count


class SyncCorrelator:
    """Streaming search of a dibit stream for any of a set of sync words (both polarities).

    Handles matches that straddle block boundaries (keeps a tail), reports the best of
    overlapping hits, and is cheap enough for 6000 sym/s x ~8 specs on one core.

    `max_errors` overrides every spec's tolerance (e.g. 0 for a strict re-check once locked).
    """

    def __init__(self, specs: Iterable[SyncSpec], max_errors: Optional[int] = None) -> None:
        self.specs = list(specs)
        if not self.specs:
            raise ValueError("at least one sync spec is required")
        self._words: list[tuple[SyncSpec, bool, np.ndarray, int]] = []
        for spec in self.specs:
            tol = spec.max_errors if max_errors is None else max_errors
            self._words.append((spec, False, np.array(spec.values, dtype=np.uint8), tol))
            self._words.append((spec, True, np.array(spec.inverted_values, dtype=np.uint8), tol))
        self._max_len = max(len(w[2]) for w in self._words)
        self.reset()

    def reset(self) -> None:
        self._tail = np.zeros(0, dtype=np.uint8)
        self._tail_start = 0  # absolute index of _tail[0]
        self._next_index = 0  # absolute index of the next unseen symbol
        self._pending: list[SyncHit] = []

    def _merge(self, hits: list[SyncHit]) -> list[SyncHit]:
        """Keep the best of every group of overlapping hits (fewest errors, then longest word)."""
        lengths = {s.name: len(s.dibits) for s in self.specs}
        ordered = sorted(hits, key=lambda h: (h.errors, -lengths[h.spec], h.index, h.inverted))
        kept: list[SyncHit] = []
        for h in ordered:
            lo, hi = h.index, h.index + lengths[h.spec]
            if all(hi <= k.index or lo >= k.index + lengths[k.spec] for k in kept):
                kept.append(h)
        return sorted(kept, key=lambda h: h.index)

    def scan(self, dibits: np.ndarray, first_index: int) -> list[SyncHit]:
        x = np.ascontiguousarray(dibits, dtype=np.uint8)
        if first_index != self._next_index:
            # a gap or restart in the stream: matches cannot span it
            self._tail = np.zeros(0, dtype=np.uint8)
            self._tail_start = first_index
            self._pending = []
        stream = np.concatenate((self._tail, x))
        base = self._tail_start
        found: list[SyncHit] = list(self._pending)
        pos = np.empty(max(len(stream), 1), dtype=np.int64)
        err = np.empty(max(len(stream), 1), dtype=np.int64)
        old = len(self._tail)  # windows must include at least one new symbol
        for spec, inverted, pattern, tol in self._words:
            if len(stream) < len(pattern):
                continue
            n = _scan(stream, pattern, tol, pos, err)
            for j in range(n):
                p = int(pos[j])
                if p + len(pattern) <= old:
                    continue  # fully inside the previous tail: already examined
                found.append(SyncHit(spec.name, spec.protocol, base + p, int(err[j]), inverted))
        self._next_index = first_index + len(x)
        keep = self._max_len - 1
        self._tail = stream[-keep:].copy() if keep > 0 else np.zeros(0, dtype=np.uint8)
        self._tail_start = base + len(stream) - len(self._tail)
        merged = self._merge(found)
        # release hits that no later (overlapping) candidate can beat: those whose word ended at
        # least `max_len` symbols ago
        horizon = self._next_index - self._max_len
        done = [h for h in merged if h.index + len(self._spec(h.spec).dibits) <= horizon]
        self._pending = [h for h in merged if h not in done]
        return done

    def flush(self) -> list[SyncHit]:
        """Release the hits still waiting for a possible better overlapping candidate."""
        out, self._pending = self._merge(self._pending), []
        return out

    def _spec(self, name: str) -> SyncSpec:
        return next(s for s in self.specs if s.name == name)
