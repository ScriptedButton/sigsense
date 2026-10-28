"""Streaming demodulator: HackRF IQ -> 48 kHz mono int16 audio.

Every stage keeps its state across calls (NCO phase, FIR histories, the FM
discriminator's previous sample, the resampler's fractional position), so the
audio is continuous however the IQ happens to be chunked.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import signal

AUDIO_RATE = 48_000
MODES = ("WBFM", "NBFM", "AM", "USB", "LSB", "CW")
CW_PITCH_HZ = 700.0

_IR_WBFM = 250_000.0
_IR_NARROW = 50_000.0
# Largest rate the first (boxcar) decimation stage may hand to the FIRs.
_BOXCAR_TARGET_RATE = 2.5e6
_MAX_STAGE = 8
_NCO_BLOCK = 1024

_AUDIO_CUTOFF = {"WBFM": 15_000.0, "NBFM": 4_000.0, "AM": 5_000.0}
_DEFAULT_CUTOFF = 4_000.0
_DEVIATION = {"WBFM": 75_000.0, "NBFM": 5_000.0}
_DEEMPHASIS_TAU = 75e-6
_OUT_GAIN = 0.8 * 32767.0
# Slow level tracking for AM / SSB / CW.
_AM_TC_S = 0.5
_PEAK_DECAY_S = 2.0
_PEAK_FLOOR = 3e-3
_GAIN_BLOCK_S = 0.01


def _is_smooth(n: int) -> bool:
    for p in (2, 3, 5, 7):
        while n % p == 0:
            n //= p
    return n == 1


def choose_decimation(fs: float, target_rate: float) -> int:
    """Integer decimation with only small prime factors, near fs / target."""
    target = max(fs / target_rate, 1.0)
    lo = max(1, int(round(target * 0.7)))
    hi = max(lo, int(round(target * 1.4)))
    best, best_err = max(1, int(round(target))), math.inf
    for d in range(lo, hi + 1):
        if not _is_smooth(d):
            continue
        err = abs(math.log(d / target))
        if err < best_err:
            best, best_err = d, err
    return best


def _divisors(n: int) -> list[int]:
    return [d for d in range(1, n + 1) if n % d == 0]


def _split_stages(n: int) -> list[int]:
    """Group prime factors of n into stages of at most _MAX_STAGE."""
    primes: list[int] = []
    m, p = n, 2
    while m > 1:
        while m % p == 0:
            primes.append(p)
            m //= p
        p += 1
    stages: list[int] = []
    for prime in sorted(primes, reverse=True):
        for i, stage in enumerate(stages):
            if stage * prime <= _MAX_STAGE:
                stages[i] = stage * prime
                break
        else:
            stages.append(prime)
    return sorted(stages, reverse=True)


class _DecimatingFir:
    """Stateful FIR + integer decimation for complex input."""

    def __init__(self, taps: np.ndarray, factor: int):
        self.factor = factor
        self.taps = np.asarray(taps, dtype=np.complex64)
        # Correlation form: y[n] = sum_t window[n + t] * taps[T - 1 - t].
        self._rev = self.taps[::-1].copy()
        self._len = len(self.taps)
        self._tail = np.zeros(self._len - 1, dtype=np.complex64)
        self._phase = 0

    def process(self, x: np.ndarray) -> np.ndarray:
        buf = np.concatenate((self._tail, x)) if len(x) else self._tail
        valid = len(buf) - self._len + 1
        if valid <= 0:
            self._tail = buf
            return np.zeros(0, dtype=np.complex64)
        idx = np.arange(self._phase, valid, self.factor)
        if len(idx):
            windows = np.lib.stride_tricks.sliding_window_view(buf, self._len)
            # einsum, not matmul: BLAS would spin up worker threads for these
            # small products and burn several cores for no speed-up.
            out = np.einsum("ij,j->i", windows[idx], self._rev).astype(np.complex64)
            self._phase = int(idx[-1]) + self.factor - valid
        else:
            out = np.zeros(0, dtype=np.complex64)
            self._phase -= valid
        self._tail = buf[valid:].copy()
        return out


class _Lfilter:
    """Stateful lfilter wrapper (FIR or one-pole IIR)."""

    def __init__(self, b, a=(1.0,), dtype=np.float32):
        self.b = np.asarray(b)
        self.a = np.asarray(a)
        order = max(len(self.a), len(self.b)) - 1
        self.zi = np.zeros(order, dtype=np.result_type(self.b.dtype, dtype))

    def process(self, x: np.ndarray) -> np.ndarray:
        if len(x) == 0:
            return x
        y, self.zi = signal.lfilter(self.b, self.a, x, zi=self.zi)
        return y


class Demodulator:
    def __init__(
        self,
        sample_rate: float,
        mode: str = "NBFM",
        bandwidth_hz: float = 12_500.0,
        offset_hz: float = 0.0,
    ):
        self.fs = float(sample_rate)
        self._phase = 0.0  # NCO phase, radians
        self.mode = ""
        self.bandwidth_hz = 0.0
        self.offset_hz = 0.0
        self._build(mode, bandwidth_hz)
        self.offset_hz = self._clamp_offset(offset_hz)

    # -- configuration ------------------------------------------------------

    def _clamp_offset(self, offset_hz: float) -> float:
        limit = 0.48 * self.fs
        return float(min(max(offset_hz, -limit), limit))

    def reconfigure(
        self,
        mode: str | None = None,
        bandwidth_hz: float | None = None,
        offset_hz: float | None = None,
    ) -> None:
        """Offset-only changes keep all state; mode/bandwidth rebuild filters
        (NCO phase is still preserved)."""
        mode = mode if mode is not None else self.mode
        bandwidth_hz = (
            bandwidth_hz if bandwidth_hz is not None else self.bandwidth_hz
        )
        if mode != self.mode or abs(bandwidth_hz - self.bandwidth_hz) > 1e-6:
            self._build(mode, bandwidth_hz)
        if offset_hz is not None:
            self.offset_hz = self._clamp_offset(offset_hz)

    def _build(self, mode: str, bandwidth_hz: float) -> None:
        mode = mode.upper()
        if mode not in MODES:
            raise ValueError(f"Unknown mode {mode!r}")
        self.mode = mode
        target_ir = _IR_WBFM if mode == "WBFM" else _IR_NARROW
        total = choose_decimation(self.fs, target_ir)
        self.ir = self.fs / total

        bw = float(bandwidth_hz)
        if mode in ("USB", "LSB"):
            bw = min(max(bw, 300.0), 0.9 * self.ir)
        elif mode == "CW":
            bw = min(max(bw, 50.0), 2_000.0)
        else:
            bw = min(max(bw, 1_000.0), 0.9 * self.ir)
        self.bandwidth_hz = float(bandwidth_hz)

        # Cheap boxcar first stage, then FIR stages of <= 8 each.
        want = max(1, int(self.fs / _BOXCAR_TARGET_RATE))
        self._boxcar = max(d for d in _divisors(total) if d <= want)
        self._boxcar_carry = np.zeros(0, dtype=np.complex64)
        rate = self.fs / self._boxcar
        self._stages: list[_DecimatingFir] = []
        for factor in _split_stages(total // self._boxcar):
            numtaps = 16 * factor + 1
            taps = signal.firwin(numtaps, 0.9 / factor)
            self._stages.append(_DecimatingFir(taps, factor))
            rate /= factor

        # Channel filter at the intermediate rate.
        if mode == "USB":
            half, centre = bw / 2, bw / 2
        elif mode == "LSB":
            half, centre = bw / 2, -bw / 2
        elif mode == "CW":
            half, centre = bw / 2, CW_PITCH_HZ
        else:
            half, centre = bw / 2, 0.0
        numtaps = int(min(max(8 * self.ir / max(bw, 1.0), 65), 513)) | 1
        proto = signal.firwin(numtaps, half, fs=self.ir)
        n = np.arange(numtaps) - (numtaps - 1) / 2
        taps = (proto * np.exp(2j * np.pi * centre * n / self.ir)).astype(
            np.complex64
        )
        self._channel = _Lfilter(taps, dtype=np.complex64)

        # Demodulator state.
        self._prev = np.complex64(0)
        self._have_prev = False
        # AM carrier estimate: per-sample one-pole, so chunking cannot matter.
        alpha_dc = math.exp(-1.0 / (self.ir * _AM_TC_S))
        self._am_alpha = alpha_dc
        self._am_lp = _Lfilter([1.0 - alpha_dc], [1.0, -alpha_dc])
        self._am_n = 0  # samples seen
        self._am_sum = 0.0
        self._am_warmup = int(self.ir * _AM_TC_S)
        # SSB/CW level: peak tracked per fixed block with a ramped gain.
        self._blk = max(1, int(self.ir * _GAIN_BLOCK_S))
        self._peak_decay = math.exp(-self._blk / (self.ir * _PEAK_DECAY_S))
        self._peak = _PEAK_FLOOR
        self._gain: float | None = None  # set from the first block
        self._ssb_carry = np.zeros(0, dtype=np.float32)
        if mode == "WBFM":
            alpha = math.exp(-1.0 / (self.ir * _DEEMPHASIS_TAU))
            self._deemph = _Lfilter([1.0 - alpha], [1.0, -alpha])
        else:
            self._deemph = None

        cutoff = _AUDIO_CUTOFF.get(mode, _DEFAULT_CUTOFF)
        cutoff = min(cutoff, 0.45 * self.ir, 0.45 * AUDIO_RATE)
        audio_taps = int(min(max(4 * self.ir / cutoff, 65), 301)) | 1
        self._audio_lp = _Lfilter(
            signal.firwin(audio_taps, cutoff, fs=self.ir).astype(np.float32)
        )

        # Linear resampler (ir -> 48 kHz) with fractional position carried.
        self._step = self.ir / AUDIO_RATE
        self._rs_pos = 0.0
        self._rs_carry = np.zeros(1, dtype=np.float32)

    # -- stages -------------------------------------------------------------

    def _mix(self, iq: np.ndarray) -> np.ndarray:
        shift = self.offset_hz - (CW_PITCH_HZ if self.mode == "CW" else 0.0)
        w = 2.0 * math.pi * shift / self.fs
        count = len(iq)
        blocks = -(-count // _NCO_BLOCK)
        # Per-sample exp() is the cost at 20 MS/s; a table of one block times a
        # per-block phasor needs only a complex multiply per sample.
        table = np.exp(-1j * w * np.arange(_NCO_BLOCK))
        starts = np.exp(-1j * (self._phase + w * _NCO_BLOCK * np.arange(blocks)))
        lo = (starts[:, None] * table[None, :]).ravel()[:count]
        self._phase = (self._phase + w * count) % (2.0 * math.pi)
        return iq * lo.astype(np.complex64)

    def _boxcar_decimate(self, x: np.ndarray) -> np.ndarray:
        b = self._boxcar
        if b == 1:
            return x
        if len(self._boxcar_carry):
            x = np.concatenate((self._boxcar_carry, x))
        usable = (len(x) // b) * b
        self._boxcar_carry = x[usable:].copy()
        if usable == 0:
            return np.zeros(0, dtype=np.complex64)
        return (x[:usable].reshape(-1, b).sum(axis=1) / b).astype(np.complex64)

    def _fm(self, x: np.ndarray) -> np.ndarray:
        if len(x) == 0:
            return np.zeros(0, dtype=np.float32)
        prev = self._prev if self._have_prev else x[0]
        ext = np.concatenate(([prev], x))
        self._prev, self._have_prev = x[-1], True
        angle = np.angle(ext[1:] * np.conj(ext[:-1]))
        out = angle * (self.ir / (2.0 * math.pi * _DEVIATION[self.mode]))
        out = out.astype(np.float32)
        if self._deemph is not None:
            out = self._deemph.process(out).astype(np.float32)
        return out

    def _am(self, x: np.ndarray) -> np.ndarray:
        if len(x) == 0:
            return np.zeros(0, dtype=np.float32)
        env = np.abs(x).astype(np.float64)
        # Carrier level: running mean while warming up (a one-pole started at
        # zero would take ~tc to settle), then a one-pole. Per-sample either
        # way, so the result does not depend on how the IQ was chunked.
        head_len = min(len(env), max(0, self._am_warmup - self._am_n))
        parts = []
        if head_len:
            head = env[:head_len]
            counts = self._am_n + np.arange(1, head_len + 1)
            parts.append((self._am_sum + np.cumsum(head)) / counts)
            self._am_sum += float(head.sum())
            self._am_n += head_len
            if self._am_n >= self._am_warmup:
                self._am_lp.zi[:] = self._am_alpha * parts[-1][-1]
        if head_len < len(env):
            tail = env[head_len:]
            parts.append(self._am_lp.process(tail))
            self._am_n += len(tail)
        carrier = np.maximum(np.concatenate(parts), 1e-6)
        return np.clip((env - carrier) / carrier, -3.0, 3.0).astype(np.float32)

    def _ssb(self, x: np.ndarray) -> np.ndarray:
        if len(x) == 0:
            return np.zeros(0, dtype=np.float32)
        # The channel filter passes one sideband, so x is analytic: the real
        # part carries half the amplitude.
        buf = np.concatenate((self._ssb_carry, (2.0 * x.real).astype(np.float32)))
        blk = self._blk
        count = len(buf) // blk
        self._ssb_carry = buf[count * blk :].copy()
        if count == 0:
            return np.zeros(0, dtype=np.float32)
        ramp = (np.arange(blk, dtype=np.float32) + 1.0) / blk
        out = np.empty(count * blk, dtype=np.float32)
        for k in range(count):
            seg = buf[k * blk : (k + 1) * blk]
            peak = float(np.abs(seg).max())
            self._peak = max(peak, max(self._peak * self._peak_decay, _PEAK_FLOOR))
            target = 0.5 / self._peak
            if self._gain is None:
                self._gain = target  # no ramp from an arbitrary start level
            out[k * blk : (k + 1) * blk] = seg * (
                self._gain + (target - self._gain) * ramp
            )
            self._gain = target
        return out

    def _resample(self, x: np.ndarray) -> np.ndarray:
        ext = np.concatenate((self._rs_carry, x))
        last = len(ext) - 1
        if last <= 0 or self._rs_pos >= last:
            self._rs_carry = ext[-1:]
            self._rs_pos = max(0.0, self._rs_pos - last)
            return np.zeros(0, dtype=np.float32)
        count = int(math.ceil((last - self._rs_pos) / self._step))
        pos = self._rs_pos + self._step * np.arange(count)
        i0 = pos.astype(np.int64)
        frac = (pos - i0).astype(np.float32)
        out = ext[i0] * (1.0 - frac) + ext[i0 + 1] * frac
        self._rs_pos = self._rs_pos + self._step * count - last
        self._rs_carry = ext[-1:]
        return out.astype(np.float32)

    # -- public ---------------------------------------------------------------

    def process(self, iq: np.ndarray) -> np.ndarray:
        """Complex IQ at the HackRF rate -> int16 mono PCM at 48 kHz."""
        iq = np.asarray(iq, dtype=np.complex64).ravel()
        if iq.size == 0:
            return np.zeros(0, dtype="<i2")
        x = self._boxcar_decimate(self._mix(iq))
        for stage in self._stages:
            x = stage.process(x)
        x = self._channel.process(x).astype(np.complex64)
        if self.mode in ("WBFM", "NBFM"):
            audio = self._fm(x)
        elif self.mode == "AM":
            audio = self._am(x)
        else:
            audio = self._ssb(x)
        audio = self._audio_lp.process(audio)
        audio = self._resample(audio.astype(np.float32))
        return np.clip(audio * _OUT_GAIN, -32768, 32767).astype("<i2")
