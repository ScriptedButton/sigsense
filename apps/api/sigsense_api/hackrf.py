"""Native HackRF capture via `hackrf_transfer` / `hackrf_info` (HackRF Pro friendly)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field

import numpy as np

HACKRF_TRANSFER = shutil.which("hackrf_transfer")
HACKRF_INFO = shutil.which("hackrf_info")

# Classic One "supported" band in hackrf_transfer; Pro operates 100 kHz–6 GHz
# and needs ``-F`` below 1 MHz (0–7250 MHz is forceable).
_CLASSIC_FREQ_LO_HZ = 1_000_000
_CLASSIC_FREQ_HI_HZ = 6_000_000_000


def hackrf_available() -> bool:
    return bool(HACKRF_TRANSFER and HACKRF_INFO)


def _force_freq_args(freq_hz: float) -> list[str]:
    """``-F`` when outside the classic 1 MHz–6 GHz CLI range (HackRF Pro MW/HF)."""
    hz = float(freq_hz)
    if hz < _CLASSIC_FREQ_LO_HZ or hz > _CLASSIC_FREQ_HI_HZ:
        return ["-F"]
    return []


def list_hackrf_devices() -> list[dict]:
    if not HACKRF_INFO:
        return []
    try:
        out = subprocess.check_output([HACKRF_INFO], text=True, stderr=subprocess.STDOUT)
    except Exception as exc:
        return [{"driver": "hackrf", "label": f"hackrf_info error: {exc}", "available": False}]

    devices: list[dict] = []
    serial = None
    board = None
    for line in out.splitlines():
        if "Serial number:" in line:
            serial = line.split(":", 1)[1].strip()
        if "Board ID Number:" in line:
            board = line.strip()
        if "Found HackRF" in line or (serial and "Firmware Version:" in line):
            pass
        m = re.search(r"HackRF Pro|HackRF One|Jawbreaker", line)
        if m and serial:
            devices.append(
                {
                    "driver": "hackrf",
                    "label": f"{m.group(0)} {serial[-8:]}",
                    "serial": serial,
                    "board": board or "",
                    "available": True,
                }
            )
            serial = None
    if not devices and "Found HackRF" in out:
        devices.append(
            {
                "driver": "hackrf",
                "label": "HackRF (detected)",
                "serial": serial or "",
                "available": True,
            }
        )
    return devices


def _split_gain(gain_db: float) -> tuple[int, int, int]:
    """Map total gain-ish slider to amp + LNA (0-40/8) + VGA (0-62/2).

    btle_rx typical: ``-g 16 -l 16`` (VGA/LNA) with amp off.
    """
    g = max(0.0, float(gain_db))
    # Keep amp off at modest gains — amp often hurts HackRF BLE front-end
    amp = 1 if g >= 40 else 0
    lna = int(min(40, max(0, round(min(g * 0.5, 40) / 8) * 8)))
    vga = int(min(62, max(0, round(max(0, g - lna) / 2) * 2)))
    if abs(g - 32) < 1:
        # Preset near btle_rx -g 16 -l 16
        amp, lna, vga = 0, 16, 16
    return amp, lna, vga


@dataclass
class HackRFCapture:
    """One-shot or streaming capture helper."""

    # RF frequency at the antenna; the HackRF itself tunes to tune_hz.
    center_freq_hz: float
    sample_rate_hz: float
    gain_db: float = 40.0
    serial: str | None = None
    # Explicit front-end stages; None derives them from gain_db.
    lna_db: int | None = None
    vga_db: int | None = None
    amp: bool | None = None
    # Upconverter local oscillator added to the HackRF tuning (0 = direct).
    lo_offset_hz: float = 0.0
    _proc: subprocess.Popen | None = None
    _stream_key: tuple | None = None
    _stream_failed: bool = False
    _ring: bytearray = field(default_factory=bytearray, repr=False)
    _tail: bytearray = field(default_factory=bytearray, repr=False)
    # Independent buffer for the audio demodulator; only filled while enabled.
    _audio: bytearray = field(default_factory=bytearray, repr=False)
    audio_enabled: bool = False
    # Increments on every stream (re)start so consumers can reset DSP state.
    generation: int = 0
    _cond: threading.Condition = field(
        default_factory=threading.Condition, repr=False
    )
    # Independent IQ subscribers (digital voice). Each has its own bounded buffer,
    # so taking from one never consumes samples another (or the display) needs.
    _taps: list = field(default_factory=list, repr=False)

    @property
    def tune_hz(self) -> float:
        return self.center_freq_hz + self.lo_offset_hz

    def _gain_stages(self) -> tuple[int, int, int]:
        amp, lna, vga = _split_gain(self.gain_db)
        if self.lna_db is not None:
            lna = int(self.lna_db)
        if self.vga_db is not None:
            vga = int(self.vga_db)
        if self.amp is not None:
            amp = 1 if self.amp else 0
        return amp, lna, vga

    def capture_samples(self, n: int) -> np.ndarray:
        """Capture n complex samples via hackrf_transfer (int8 IQ to temp file)."""
        if not HACKRF_TRANSFER:
            raise RuntimeError("hackrf_transfer not found — brew install hackrf")
        amp, lna, vga = self._gain_stages()
        fd, path = tempfile.mkstemp(suffix=".iq")
        os.close(fd)
        try:
            cmd = [
                HACKRF_TRANSFER,
                "-r",
                path,
                "-f",
                str(int(self.tune_hz)),
                "-s",
                str(int(self.sample_rate_hz)),
                "-n",
                str(int(n)),
                "-l",
                str(lna),
                "-g",
                str(vga),
                "-a",
                str(amp),
                # Match btle_rx: SAMPLE_PER_SYMBOL*1e6/2 baseband (2 MHz at 4 MS/s)
                "-b",
                str(int(min(max(1.75e6, self.sample_rate_hz / 2), 5e6))),
                *_force_freq_args(self.tune_hz),
            ]
            if self.serial:
                cmd.extend(["-d", self.serial])
            proc = subprocess.run(cmd, capture_output=True, check=False)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            if size < 16:
                err = (proc.stderr or b"").decode("utf-8", errors="replace")
                if "busy" in err.lower():
                    raise RuntimeError(
                        "HackRF is busy: another program or API instance is "
                        "using it. Close it (or restart the API) and try again."
                    )
                raise RuntimeError(
                    f"hackrf_transfer produced no IQ data: {err[-400:]}"
                )
            raw = open(path, "rb").read()
            return int8_iq_to_complex(raw)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _tuning_key(self) -> tuple:
        return (
            int(self.tune_hz),
            int(self.sample_rate_hz),
            round(float(self.gain_db), 1),
            self._gain_stages(),
            self.serial,
        )

    def _stream_cmd(self) -> list[str]:
        amp, lna, vga = self._gain_stages()
        cmd = [
            HACKRF_TRANSFER,
            "-r",
            "-",
            "-f",
            str(int(self.tune_hz)),
            "-s",
            str(int(self.sample_rate_hz)),
            "-l",
            str(lna),
            "-g",
            str(vga),
            "-a",
            str(amp),
            "-b",
            str(int(min(max(1.75e6, self.sample_rate_hz / 2), 5e6))),
            *_force_freq_args(self.tune_hz),
        ]
        if self.serial:
            cmd.extend(["-d", self.serial])
        return cmd

    def start_stream(self) -> None:
        """Keep one hackrf_transfer running and buffer its newest samples.

        Launching a process per read costs a large fraction of a second (USB
        open, tune, start-up), which caps the display at a couple of rows per
        second. A persistent stream only restarts when tuning changes. If the
        stream never produces data, reads fall back to one-shot captures.
        """
        self.stop_stream()
        if not HACKRF_TRANSFER or self._stream_failed:
            return
        proc = subprocess.Popen(
            self._stream_cmd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        with self._cond:
            self._ring.clear()
            self._tail.clear()
            self._audio.clear()
            self.generation += 1
            for tap in self._taps:
                tap._discontinuity(self.generation)
        self._proc = proc
        self._stream_key = self._tuning_key()
        threading.Thread(target=self._pump, args=(proc,), daemon=True).start()

    def _pump(self, proc: subprocess.Popen) -> None:
        cap = STREAM_RING_BYTES
        carry = b""
        while True:
            try:
                chunk = proc.stdout.read(1 << 18) if proc.stdout else b""
            except (ValueError, OSError):
                break  # pipe closed by stop_stream
            if not chunk:
                break
            # A short pipe read can end mid I/Q pair; hold the odd byte back so
            # every ring stays pair-aligned.
            chunk = carry + chunk
            if len(chunk) & 1:
                carry, chunk = chunk[-1:], chunk[:-1]
            else:
                carry = b""
            if not chunk:
                continue
            with self._cond:
                if proc is not self._proc:
                    break
                self._ring.extend(chunk)
                # Newest samples for the display, independent of decoding.
                self._tail.extend(chunk)
                extra = len(self._tail) - STREAM_TAIL_BYTES
                if extra > 0:
                    del self._tail[: extra + (extra & 1)]
                over = len(self._ring) - cap
                if over > 0:
                    # Drop whole I/Q pairs so alignment survives.
                    del self._ring[: over + (over & 1)]
                for tap in self._taps:
                    tap._feed(chunk)
                if self.audio_enabled:
                    self._audio.extend(chunk)
                    # About one second of IQ at most (2 bytes per sample).
                    audio_over = len(self._audio) - int(2 * self.sample_rate_hz)
                    if audio_over > 0:
                        del self._audio[: audio_over + (audio_over & 1)]
                self._cond.notify_all()
        with self._cond:
            self._cond.notify_all()

    def restart_stream(self) -> None:
        """Apply new tuning now, so the first read after a retune isn't the one
        that pays for the restart."""
        self.stop_stream()
        self.start_stream()

    def stop_stream(self) -> None:
        proc, self._proc = self._proc, None
        self._stream_key = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=0.5)
        except Exception:
            # A retune must not wait on a slow shutdown; the device frees on exit.
            try:
                proc.kill()
                proc.wait(timeout=1)
            except Exception:
                pass
        if proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass
        with self._cond:
            self._ring.clear()
            self._tail.clear()
            self._audio.clear()
            self._cond.notify_all()

    def add_tap(self, max_seconds: float = 1.0) -> "IqTap":
        """A non-consuming IQ subscription with its own ring (about `max_seconds` of samples).

        Used by the digital-voice engine. Does not touch the audio demodulator's
        buffer or the display tail, and is cleared (generation bumped) whenever the
        stream restarts, so consumers know to reset their DSP state.
        """
        tap = IqTap(self, max_seconds)
        with self._cond:
            self._taps.append(tap)
            tap.generation = self.generation
        return tap

    def remove_tap(self, tap: "IqTap") -> None:
        with self._cond:
            if tap in self._taps:
                self._taps.remove(tap)

    def enable_audio(self, enabled: bool) -> None:
        """Start or stop buffering IQ for the demodulator."""
        with self._cond:
            self.audio_enabled = bool(enabled)
            self._audio.clear()

    def take_audio_iq(self, max_samples: int | None = None) -> np.ndarray:
        """Everything buffered for audio since the last call (non-blocking).

        A backlog beyond ~250 ms is trimmed from the old end so audio stays
        close to real time; `max_samples` caps the return size the same way.
        """
        limit = int(0.25 * self.sample_rate_hz)
        if max_samples is not None:
            limit = min(limit, int(max_samples))
        with self._cond:
            if not self._audio:
                return np.zeros(0, dtype=np.complex64)
            # Only whole I/Q pairs, counted from the front; a trailing odd byte
            # stays buffered so I and Q never swap after a short pipe read.
            whole = len(self._audio) & ~1
            start = max(0, whole - 2 * max(limit, 1))
            raw = bytes(self._audio[start:whole])
            del self._audio[:whole]
        return int8_iq_to_complex(raw)

    @property
    def streaming(self) -> bool:
        proc = self._proc
        return (
            proc is not None
            and proc.poll() is None
            and self._stream_key == self._tuning_key()
        )

    def peek_latest(self, n: int) -> np.ndarray | None:
        """Newest n samples without consuming them (display only)."""
        if not self.streaming:
            return None
        with self._cond:
            if len(self._tail) < 2 * n:
                return None
            raw = bytes(self._tail[-2 * n :])
        return int8_iq_to_complex(raw)

    def read_stream(self, n: int) -> np.ndarray:
        """Next n contiguous samples; retunes by restarting the stream."""
        if self._stream_failed or not (
            self._proc is not None
            and self._proc.poll() is None
            and self._stream_key == self._tuning_key()
        ):
            self.start_stream()
        if self._proc is None:
            return self.capture_samples(n)
        need = 2 * n
        deadline = time.monotonic() + 3.0
        with self._cond:
            while len(self._ring) < need:
                if self._proc is None or self._proc.poll() is not None:
                    break
                if time.monotonic() >= deadline:
                    break
                self._cond.wait(0.1)
            if len(self._ring) < need:
                got_nothing = len(self._ring) == 0
                short = True
            else:
                short = False
                got_nothing = False
                if len(self._ring) > 3 * need:
                    # Consumer fell behind: skip ahead rather than lag.
                    del self._ring[: len(self._ring) - need]
                raw = bytes(self._ring[:need])
                del self._ring[:need]
        if short:
            if got_nothing:
                self._stream_failed = True
            self.stop_stream()
            return self.capture_samples(n)
        return int8_iq_to_complex(raw)


STREAM_RING_BYTES = 8 * 1024 * 1024
STREAM_TAIL_BYTES = 512 * 1024  # 256k samples: 8 blocks of the largest display FFT


class IqTap:
    """One subscriber's view of the HackRF stream (see HackRFCapture.add_tap)."""

    def __init__(self, capture: "HackRFCapture", max_seconds: float) -> None:
        self.capture = capture
        self.max_seconds = float(max_seconds)
        self.generation = 0
        self.dropped_samples = 0
        self._buf = bytearray()
        self._closed = False

    @property
    def max_bytes(self) -> int:
        # 2 bytes per complex sample; at least one second's worth of a small rate
        return int(2 * max(1.0, self.capture.sample_rate_hz) * self.max_seconds)

    # called from the capture's pump thread with the capture lock held
    def _feed(self, chunk: bytes) -> None:
        if self._closed:
            return
        self._buf.extend(chunk)
        over = len(self._buf) - self.max_bytes
        if over > 0:
            drop = over + (over & 1)  # whole I/Q pairs only
            del self._buf[:drop]
            self.dropped_samples += drop // 2

    def _discontinuity(self, generation: int) -> None:
        self._buf.clear()
        self.generation = generation

    def take(self, max_samples: int | None = None) -> tuple[np.ndarray, int]:
        """Everything buffered (up to `max_samples`, oldest first) and the stream generation."""
        with self.capture._cond:
            whole = len(self._buf) & ~1
            if max_samples is not None:
                whole = min(whole, 2 * int(max_samples))
            raw = bytes(self._buf[:whole])
            del self._buf[:whole]
            generation = self.generation
        return (int8_iq_to_complex(raw) if raw else np.zeros(0, dtype=np.complex64)), generation

    def close(self) -> None:
        self._closed = True
        self.capture.remove_tap(self)


def int8_iq_to_complex(raw: bytes) -> np.ndarray:
    interleaved = np.frombuffer(raw, dtype=np.int8)
    if len(interleaved) % 2:
        interleaved = interleaved[:-1]
    i = interleaved[0::2].astype(np.float32) / 128.0
    q = interleaved[1::2].astype(np.float32) / 128.0
    return (i + 1j * q).astype(np.complex64)


def complex_to_int8_iq(iq: np.ndarray) -> bytes:
    """Pack complex64 IQ to HackRF int8 interleaved I/Q."""
    iq = np.asarray(iq, dtype=np.complex64).ravel()
    peak = float(np.max(np.abs(iq))) + 1e-12
    scale = 127.0 / peak
    i = np.clip(np.real(iq) * scale, -128, 127).astype(np.int8)
    q = np.clip(np.imag(iq) * scale, -128, 127).astype(np.int8)
    interleaved = np.empty(i.size * 2, dtype=np.int8)
    interleaved[0::2] = i
    interleaved[1::2] = q
    return interleaved.tobytes()


def transmit_iq(
    iq: np.ndarray,
    *,
    center_freq_hz: float,
    sample_rate_hz: float,
    tx_gain: int = 20,
    amp: bool = False,
    serial: str | None = None,
    repeat: bool = False,
) -> dict:
    """Transmit baseband IQ via ``hackrf_transfer -t`` (blocks until done)."""
    if not HACKRF_TRANSFER:
        raise RuntimeError("hackrf_transfer not found — brew install hackrf")
    raw = complex_to_int8_iq(iq)
    n_samp = len(raw) // 2
    if n_samp < 16:
        raise ValueError("IQ too short to transmit")
    fd, path = tempfile.mkstemp(suffix=".iq")
    try:
        os.write(fd, raw)
        os.close(fd)
        cmd = [
            HACKRF_TRANSFER,
            "-t",
            path,
            "-f",
            str(int(center_freq_hz)),
            "-s",
            str(int(sample_rate_hz)),
            "-n",
            str(int(n_samp)),
            "-x",
            str(int(max(0, min(47, tx_gain)))),
            "-a",
            "1" if amp else "0",
            "-b",
            str(int(min(max(1.75e6, sample_rate_hz / 2), 5e6))),
            *_force_freq_args(center_freq_hz),
        ]
        if serial:
            cmd.extend(["-d", serial])
        if repeat:
            cmd.append("-R")
        proc = subprocess.run(cmd, capture_output=True, check=False)
        err = (proc.stderr or b"").decode("utf-8", errors="replace")
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(
                f"hackrf_transfer TX failed (code {proc.returncode}): "
                f"{(err or out)[-500:]}"
            )
        return {
            "ok": True,
            "samples": n_samp,
            "center_freq_hz": float(center_freq_hz),
            "sample_rate_hz": float(sample_rate_hz),
            "tx_gain": int(tx_gain),
            "amp": bool(amp),
            "log": (err or out)[-300:],
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
