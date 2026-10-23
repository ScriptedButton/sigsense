"""Native HackRF capture via `hackrf_transfer` / `hackrf_info` (HackRF Pro friendly)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

import numpy as np

HACKRF_TRANSFER = shutil.which("hackrf_transfer")
HACKRF_INFO = shutil.which("hackrf_info")


def hackrf_available() -> bool:
    return bool(HACKRF_TRANSFER and HACKRF_INFO)


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

    center_freq_hz: float
    sample_rate_hz: float
    gain_db: float = 40.0
    serial: str | None = None
    _proc: subprocess.Popen | None = None

    def capture_samples(self, n: int) -> np.ndarray:
        """Capture n complex samples via hackrf_transfer (int8 IQ to temp file)."""
        if not HACKRF_TRANSFER:
            raise RuntimeError("hackrf_transfer not found — brew install hackrf")
        amp, lna, vga = _split_gain(self.gain_db)
        fd, path = tempfile.mkstemp(suffix=".iq")
        os.close(fd)
        try:
            cmd = [
                HACKRF_TRANSFER,
                "-r",
                path,
                "-f",
                str(int(self.center_freq_hz)),
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

    def start_stream(self) -> None:
        # File-based one-shots are more reliable with HackRF Pro than stdout pipes.
        self.stop_stream()

    def stop_stream(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=2)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None

    def read_stream(self, n: int) -> np.ndarray:
        """Capture n complex samples (one-shot file transfer)."""
        return self.capture_samples(n)


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
