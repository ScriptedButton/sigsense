"""Per-call vocoder sessions (CONTRACT v1). Owner: WP-F.

The engine (WP-G) talks to this module, not to backends:

    pool = SessionPool()
    out = pool.push(("ch0", call_id, slot), frame)   # -> VocoderOutput (8 kHz and 48 kHz PCM)
    tail = pool.end(("ch0", call_id, slot))          # -> final fade-out samples

One session = one call (and, for TDMA, one slot): it owns a fresh backend
instance, so synthesis state never leaks between calls or slots; it applies the
loss-concealment policy; and it carries the stateful 48 kHz post-processing
chain. When no backend can run, `push()` still returns well-formed silence with
`available=False` and a status string, so the engine keeps reporting the call.

Everything is thread-safe: each session has a lock (the engine may feed a
call from a worker thread while another thread ends it) and the pool has its own.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Hashable, Optional

import numpy as np

from sigsense_api.dvoice.events import VocoderId
from sigsense_api.dvoice.vocoder import registry
from sigsense_api.dvoice.vocoder.base import FRAME_SAMPLES, UnavailableVocoder, Vocoder, VocoderFrame
from sigsense_api.dvoice.vocoder.pcm import PcmPostProcessor

STATUS_OK = "ok"
STATUS_CONCEALED = "concealed"
STATUS_MUTED = "muted"
STATUS_UNAVAILABLE = "vocoder_unavailable"


@dataclass
class ConcealmentPolicy:
    """What to do when a frame is lost or too damaged to trust.

    A lost frame repeats the previous good one, quieter each time
    (`decay` per repeat), for up to `repeat_limit` frames; after that, silence
    until a good frame arrives. A frame whose FEC corrected more than
    `max_fec_errors` bits is treated as lost (`None` = never).
    """

    repeat_limit: int = 3
    decay: float = 0.6
    max_fec_errors: Optional[int] = None


@dataclass
class VocoderOutput:
    pcm8k: np.ndarray  # int16, a multiple of 160 samples (silence when unavailable)
    pcm48k: np.ndarray  # int16, 6x the samples
    available: bool
    status: str = STATUS_OK
    concealed: bool = False


@dataclass
class SessionStats:
    frames: int = 0
    good: int = 0
    concealed: int = 0
    muted: int = 0
    bad: int = 0
    fec_errors: int = 0


class VocoderSession:
    def __init__(
        self,
        vocoder_id: VocoderId,
        *,
        backend: Optional[Vocoder] = None,
        policy: Optional[ConcealmentPolicy] = None,
        post: Optional[PcmPostProcessor] = None,
        gain: float = 1.0,
    ) -> None:
        self.vocoder_id = vocoder_id
        self.policy = policy or ConcealmentPolicy()
        self._backend: Vocoder = backend if backend is not None else registry.get_vocoder(vocoder_id)
        self._post = post if post is not None else PcmPostProcessor(gain=gain)
        self._lock = threading.Lock()
        self._last_good: Optional[np.ndarray] = None
        self._repeats = 0
        self._closed = False
        self.stats = SessionStats()
        self.created = time.monotonic()

    @property
    def available(self) -> bool:
        return not isinstance(self._backend, UnavailableVocoder)

    @property
    def backend_name(self) -> str:
        return type(self._backend).__name__

    def _silence(self) -> np.ndarray:
        return np.zeros(getattr(self._backend, "samples_per_decode", FRAME_SAMPLES), dtype=np.int16)

    def _finish(self, pcm8k: np.ndarray, status: str, concealed: bool = False) -> VocoderOutput:
        return VocoderOutput(pcm8k, self._post.push(pcm8k), self.available, status, concealed)

    def push(self, frame: VocoderFrame) -> VocoderOutput:
        with self._lock:
            if self._closed:
                raise RuntimeError("session is closed")
            self.stats.frames += 1
            self.stats.fec_errors += max(0, frame.fec_errors)
            if not self.available:
                return self._finish(self._silence(), STATUS_UNAVAILABLE)

            limit = self.policy.max_fec_errors
            lost = frame.erased or (limit is not None and frame.fec_errors > limit)
            if not lost:
                try:
                    pcm = np.asarray(self._backend.decode(frame), dtype=np.int16)
                except Exception:  # a backend fault must not kill the call: treat as a lost frame
                    self.stats.bad += 1
                    lost, pcm = True, None
                else:
                    self._last_good, self._repeats = pcm, 0
                    self.stats.good += 1
                    return self._finish(pcm, STATUS_OK)

            # Lost or damaged: repeat the last good frame, fading, then mute.
            if self._last_good is not None and self._repeats < self.policy.repeat_limit:
                self._repeats += 1
                scale = self.policy.decay**self._repeats
                pcm = np.round(self._last_good.astype(np.float64) * scale).astype(np.int16)
                self.stats.concealed += 1
                return self._finish(pcm, STATUS_CONCEALED, concealed=True)
            self.stats.muted += 1
            return self._finish(self._silence(), STATUS_MUTED, concealed=True)

    def close(self) -> np.ndarray:
        """End the call. Returns the 48 kHz fade-out tail (int16); the session is unusable afterwards."""
        with self._lock:
            if self._closed:
                return np.zeros(0, dtype=np.int16)
            self._closed = True
            tail = self._post.finish()
            self._backend.close()
            return tail

    def reset(self) -> None:
        """Start a new call on the same session: clears synthesis, concealment and filter state."""
        with self._lock:
            self._backend.reset()
            self._post.reset()
            self._last_good, self._repeats = None, 0


@dataclass
class SessionPool:
    """Sessions keyed by (channel, call, slot) or any hashable the engine chooses.

    Bounded: beyond `max_sessions` the least recently used session is ended, and
    sessions idle for more than `idle_s` are reaped on access, so a call whose
    terminator was never heard cannot leak a native codec handle forever.
    """

    max_sessions: int = 64
    idle_s: float = 30.0
    policy: ConcealmentPolicy = field(default_factory=ConcealmentPolicy)
    clock: Callable[[], float] = time.monotonic
    factory: Optional[Callable[[VocoderId], Vocoder]] = None
    _sessions: "OrderedDict[Hashable, tuple[VocoderSession, float]]" = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _reap(self, now: float) -> list[VocoderSession]:
        dead = [k for k, (_, seen) in self._sessions.items() if now - seen > self.idle_s]
        out = [self._sessions.pop(k)[0] for k in dead]
        while len(self._sessions) > self.max_sessions:
            out.append(self._sessions.popitem(last=False)[1][0])
        return out

    def session(self, key: Hashable, vocoder_id: VocoderId) -> VocoderSession:
        now = self.clock()
        with self._lock:
            hit = self._sessions.get(key)
            if hit is not None and hit[0].vocoder_id == vocoder_id:
                self._sessions[key] = (hit[0], now)
                self._sessions.move_to_end(key)
                evicted = self._reap(now)
                session = hit[0]
            else:
                if hit is not None:  # same key, different codec: a new call took over the slot
                    evicted = [hit[0]]
                else:
                    evicted = []
                backend = self.factory(vocoder_id) if self.factory else None
                session = VocoderSession(vocoder_id, backend=backend, policy=self.policy)
                self._sessions[key] = (session, now)
                evicted += self._reap(now)
        for old in evicted:
            old.close()
        return session

    def push(self, key: Hashable, frame: VocoderFrame) -> VocoderOutput:
        return self.session(key, frame.vocoder).push(frame)

    def end(self, key: Hashable) -> np.ndarray:
        with self._lock:
            hit = self._sessions.pop(key, None)
        return hit[0].close() if hit else np.zeros(0, dtype=np.int16)

    def close_all(self) -> None:
        with self._lock:
            sessions = [s for s, _ in self._sessions.values()]
            self._sessions.clear()
        for s in sessions:
            s.close()

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
