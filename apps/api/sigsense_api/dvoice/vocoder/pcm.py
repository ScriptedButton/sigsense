"""PCM post-processing for decoded voice (CONTRACT v1). Owner: WP-F.

Vocoders produce 8 kHz mono int16; the audio socket and recorder take 48 kHz.
Every stage is STATEFUL across frames (a 20 ms frame is far shorter than any
filter), so frame boundaries are click-free:

  StreamingUpsampler  8 kHz -> 48 kHz, linear-phase FIR, state carried over
  SoftLimiter         keeps samples under a ceiling without hard clipping
  Ramp                short raised-cosine fade in/out at call boundaries

`PcmPostProcessor` strings them together for one call.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from scipy import signal

IN_RATE = 8000
OUT_RATE = 48000
FACTOR = OUT_RATE // IN_RATE


class StreamingUpsampler:
    """Integer-factor upsampler: zero-stuff then low-pass, filter memory kept between calls.

    The FIR is a Kaiser design with its pass-band to 3.6 kHz (speech never
    exceeds 4 kHz at 8 kHz sampling) and >60 dB image rejection; `delay`
    reports its group delay in output samples.
    """

    def __init__(self, factor: int = FACTOR, numtaps: int = 12 * FACTOR * 2 + 1) -> None:
        self.factor = factor
        cutoff = 3600.0 / (IN_RATE * factor / 2)  # fraction of the output Nyquist
        taps = signal.firwin(numtaps, cutoff, window=("kaiser", 7.0)) * factor
        self._taps = taps.astype(np.float64)
        self._zi = np.zeros(len(self._taps) - 1)
        self.delay = (len(self._taps) - 1) // 2

    def process(self, pcm8k: np.ndarray) -> np.ndarray:
        x = np.asarray(pcm8k, dtype=np.float64)
        stuffed = np.zeros(x.size * self.factor)
        stuffed[:: self.factor] = x
        y, self._zi = signal.lfilter(self._taps, [1.0], stuffed, zi=self._zi)
        return y

    def flush(self) -> np.ndarray:
        """Output still inside the filter after the last input (so a call's tail isn't cut off)."""
        tail, self._zi = signal.lfilter(self._taps, [1.0], np.zeros(len(self._taps) - 1), zi=self._zi)
        return tail

    def reset(self) -> None:
        self._zi = np.zeros(len(self._taps) - 1)


class SoftLimiter:
    """Peak limiter: fast attack inside the block, slow release between blocks.

    Output never exceeds `ceiling` (as a fraction of full scale); gain only
    moves down as far as the loudest sample needs, then recovers over `release_s`.
    """

    def __init__(self, ceiling: float = 0.89, release_s: float = 0.15, rate: int = OUT_RATE) -> None:
        self.ceiling = ceiling
        self._release = float(np.exp(-1.0 / (release_s * rate)))
        self._gain = 1.0

    def process(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            return x
        full = 32768.0 * self.ceiling
        need = np.minimum(1.0, full / np.maximum(np.abs(x), 1e-9))  # per-sample gain that fits the ceiling
        # Running minimum of what's needed, relaxing toward 1.0 at the release rate.
        gain = np.empty_like(need)
        g = self._gain
        rel = self._release
        for i in range(need.size):  # block sizes are tiny (960); a python loop is fine and exact
            g = need[i] if need[i] < g else 1.0 - (1.0 - g) * rel
            gain[i] = g
        self._gain = g
        return x * np.minimum(gain, need)

    def reset(self) -> None:
        self._gain = 1.0


class Ramp:
    """Raised-cosine fade used to start and end a call without a step."""

    def __init__(self, ms: float = 4.0, rate: int = OUT_RATE) -> None:
        n = max(2, int(rate * ms / 1000))
        self.n = n
        self._up = 0.5 - 0.5 * np.cos(np.pi * np.arange(n) / n)

    def fade_in(self, x: np.ndarray) -> np.ndarray:
        out = x.copy()
        m = min(self.n, out.size)
        out[:m] *= self._up[:m]
        return out

    def fade_out(self, x: np.ndarray) -> np.ndarray:
        out = x.copy()
        m = min(self.n, out.size)
        out[-m:] *= self._up[:m][::-1]
        return out


def to_int16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(x), -32768, 32767).astype(np.int16)


class PcmPostProcessor:
    """One call's 8 kHz -> 48 kHz chain. `push()` per frame, `finish()` once at the end."""

    def __init__(self, *, gain: float = 1.0, limiter: bool = True, declick: bool = True) -> None:
        self.gain = gain
        self._up = StreamingUpsampler()
        self._limiter: Optional[SoftLimiter] = SoftLimiter() if limiter else None
        self._ramp: Optional[Ramp] = Ramp() if declick else None
        self._started = False
        self._last = 0.0

    def push(self, pcm8k: np.ndarray) -> np.ndarray:
        y = self._up.process(pcm8k) * self.gain
        if self._limiter is not None:
            y = self._limiter.process(y)
        if self._ramp is not None and not self._started:
            y = self._ramp.fade_in(y)
        self._started = True
        if y.size:
            self._last = float(y[-1])
        return to_int16(y)

    def finish(self) -> np.ndarray:
        """The filter tail with a fade-out, so the call ends smoothly instead of mid-cycle."""
        y = self._up.flush() * self.gain
        if self._limiter is not None:
            y = self._limiter.process(y)
        if self._ramp is not None:
            y = self._ramp.fade_out(y)
        return to_int16(y)

    def reset(self) -> None:
        self._up.reset()
        if self._limiter:
            self._limiter.reset()
        self._started = False
        self._last = 0.0
