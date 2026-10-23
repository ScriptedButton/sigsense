"""IQ STFT and waterfall image normalization to shared 256x256 spectrograms."""

from __future__ import annotations

import io
from pathlib import Path
from typing import BinaryIO

import numpy as np
from PIL import Image
from scipy import signal as sp_signal
from scipy.io import wavfile

from sigsense_ml.classes import SPECTROGRAM_SIZE

SPEC_SIZE = SPECTROGRAM_SIZE


def _is_wav_bytes(raw: bytes) -> bool:
    return len(raw) >= 12 and raw[0:4] == b"RIFF" and raw[8:12] == b"WAVE"


def _pcm_channels_to_iq(data: np.ndarray) -> np.ndarray:
    """Convert WAV PCM (stereo L/R = I/Q) to complex64 baseband."""
    arr = np.asarray(data)
    if arr.ndim == 1:
        raise ValueError(
            "WAV IQ requires stereo (L=I, R=Q). Got mono — use a stereo IQ WAV."
        )
    if arr.ndim != 2 or arr.shape[1] < 2:
        raise ValueError(
            f"WAV IQ requires at least 2 channels (I/Q), got shape {arr.shape}"
        )

    i = arr[:, 0].astype(np.float64)
    q = arr[:, 1].astype(np.float64)

    if arr.dtype == np.uint8:
        i = (i - 127.5) / 127.5
        q = (q - 127.5) / 127.5
    elif np.issubdtype(arr.dtype, np.integer):
        # Left-justified signed PCM from scipy — scale by type max
        max_abs = float(np.iinfo(arr.dtype).max) or 1.0
        i = i / max_abs
        q = q / max_abs
    # float32/float64 WAV already in [-1, 1]

    return (i + 1j * q).astype(np.complex64)


def load_wav_iq(
    source: str | Path | bytes | BinaryIO,
    *,
    sample_rate: float | None = None,
) -> tuple[np.ndarray, float | None]:
    """Load stereo WAV IQ (L=I, R=Q) as used by SDR#, HDSDR, Gqrx exports, etc."""
    if isinstance(source, (str, Path)):
        rate, data = wavfile.read(str(source))
    elif isinstance(source, bytes):
        rate, data = wavfile.read(io.BytesIO(source))
    else:
        # File-like: scipy needs a seekable buffer
        raw = source.read() if hasattr(source, "read") else bytes(source)
        rate, data = wavfile.read(io.BytesIO(raw))

    iq = _pcm_channels_to_iq(data)
    return iq, float(sample_rate if sample_rate is not None else rate)


def load_iq_file(
    source: str | Path | bytes | BinaryIO,
    *,
    dtype: str = "auto",
    sample_rate: float | None = None,
    filename: str | None = None,
) -> tuple[np.ndarray, float | None]:
    """Load IQ samples from WAV, raw complex, or numpy buffer.

    ``dtype``:
      - ``auto``: detect WAV via magic/extension, else complex64
      - ``wav``: force stereo WAV IQ (L=I, R=Q)
      - ``complex64`` / ``int16`` / ``uint8``: raw interleaved IQ
    """
    path_suffix = ""
    if isinstance(source, (str, Path)):
        path = Path(source)
        path_suffix = path.suffix.lower()
        if path_suffix == ".npy":
            iq = np.load(path)
            return np.asarray(iq, dtype=np.complex64), sample_rate
        if dtype in ("auto", "wav") and path_suffix in (".wav", ".wave"):
            return load_wav_iq(path, sample_rate=sample_rate)
        raw = path.read_bytes()
    elif isinstance(source, bytes):
        raw = source
    else:
        raw = source.read()

    name = (filename or "").lower()
    force_wav = dtype == "wav" or (
        dtype == "auto"
        and (_is_wav_bytes(raw) or name.endswith((".wav", ".wave")))
    )
    if force_wav:
        return load_wav_iq(raw, sample_rate=sample_rate)

    resolved = "complex64" if dtype == "auto" else dtype

    if resolved == "complex64":
        iq = np.frombuffer(raw, dtype=np.complex64)
    elif resolved == "int16":
        interleaved = np.frombuffer(raw, dtype=np.int16)
        if len(interleaved) % 2:
            interleaved = interleaved[:-1]
        i = interleaved[0::2].astype(np.float32)
        q = interleaved[1::2].astype(np.float32)
        iq = (i + 1j * q) / 32768.0
    elif resolved == "uint8":
        interleaved = np.frombuffer(raw, dtype=np.uint8)
        if len(interleaved) % 2:
            interleaved = interleaved[:-1]
        i = (interleaved[0::2].astype(np.float32) - 127.5) / 127.5
        q = (interleaved[1::2].astype(np.float32) - 127.5) / 127.5
        iq = i + 1j * q
    else:
        raise ValueError(f"Unsupported dtype: {dtype}")

    return np.asarray(iq, dtype=np.complex64), sample_rate


def iq_to_spectrogram(
    iq: np.ndarray,
    *,
    n_fft: int = 512,
    hop_length: int | None = None,
    size: int = SPEC_SIZE,
) -> np.ndarray:
    """Convert complex IQ to a log-magnitude spectrogram of shape (size, size)."""
    iq = np.asarray(iq, dtype=np.complex64).ravel()
    if iq.size < n_fft:
        pad = n_fft - iq.size
        iq = np.pad(iq, (0, pad))

    hop = hop_length or max(1, n_fft // 4)
    _freqs, _times, zxx = sp_signal.stft(
        iq,
        nperseg=n_fft,
        noverlap=n_fft - hop,
        return_onesided=False,
        boundary=None,
        padded=False,
    )
    # FFT-shift frequency axis so DC is centered (waterfall-like)
    mag = np.abs(np.fft.fftshift(zxx, axes=0))
    return normalize_spectrogram(mag, size=size)


def normalize_spectrogram(mag: np.ndarray, *, size: int = SPEC_SIZE) -> np.ndarray:
    """Log-scale and resize magnitude spectrogram to (size, size) float32 in [0, 1]."""
    mag = np.asarray(mag, dtype=np.float64)
    mag = np.maximum(mag, 1e-12)
    log_mag = np.log10(mag)
    # Robust percentile normalization
    lo, hi = np.percentile(log_mag, [5, 99])
    if hi <= lo:
        hi = lo + 1e-6
    norm = np.clip((log_mag - lo) / (hi - lo), 0.0, 1.0)

    img = Image.fromarray((norm * 255).astype(np.uint8), mode="L")
    img = img.resize((size, size), Image.Resampling.BILINEAR)
    out = np.asarray(img, dtype=np.float32) / 255.0
    return out


def _to_grayscale_power(img: Image.Image) -> np.ndarray:
    """Convert waterfall screenshot to approximate log-power grayscale."""
    rgba = img.convert("RGBA")
    arr = np.asarray(rgba, dtype=np.float32)
    rgb = arr[..., :3]
    alpha = arr[..., 3:4] / 255.0
    # Composite on dark background (typical SDR UI)
    rgb = rgb * alpha + (1.0 - alpha) * 20.0

    # Heuristic: trim uniform UI chrome margins
    gray = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    # Prefer luminance of hot colormap: boost max channel relative to mean
    vmax = rgb.max(axis=-1)
    vmean = rgb.mean(axis=-1) + 1e-6
    heat = np.clip((vmax - vmean) / 128.0 + gray / 255.0, 0.0, 1.0)

    # Crop near-empty borders
    mask = heat > 0.08
    if mask.any():
        rows = np.where(mask.any(axis=1))[0]
        cols = np.where(mask.any(axis=0))[0]
        if len(rows) > 8 and len(cols) > 8:
            r0, r1 = rows[0], rows[-1] + 1
            c0, c1 = cols[0], cols[-1] + 1
            # Keep a bit of margin but discard extreme chrome
            heat = heat[r0:r1, c0:c1]

    return heat


def waterfall_image_to_spectrogram(
    source: str | Path | bytes | BinaryIO | Image.Image,
    *,
    size: int = SPEC_SIZE,
) -> np.ndarray:
    """Normalize a waterfall PNG/JPG (or screenshot) to shared spectrogram tensor."""
    if isinstance(source, Image.Image):
        img = source
    elif isinstance(source, (str, Path)):
        img = Image.open(source)
    elif isinstance(source, bytes):
        img = Image.open(io.BytesIO(source))
    else:
        img = Image.open(io.BytesIO(source.read()))

    heat = _to_grayscale_power(img)
    # Treat as already log-ish display; still apply robust normalize+resize
    return normalize_spectrogram(np.maximum(heat, 1e-6), size=size)


def spectrogram_to_tensor(spec: np.ndarray) -> np.ndarray:
    """Return NCHW-ready float32 array with shape (1, 1, H, W)."""
    spec = np.asarray(spec, dtype=np.float32)
    if spec.ndim != 2:
        raise ValueError(f"Expected 2D spectrogram, got shape {spec.shape}")
    return spec[np.newaxis, np.newaxis, ...]
