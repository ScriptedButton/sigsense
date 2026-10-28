"""Band finding + CNN identification from browser spectrum rows.

The browser (RTL-SDR, rtl_tcp, OpenWebRX, demo) has no IQ the backend can see,
only the spectrum rows it already draws. This turns a short block of those rows
into the same occupancy tracks the HackRF path produces, so one ML pipeline
(CNN + embedding + open-set library + band plan) drives the spectrum view.

Approximation versus IQ-trained inputs: the CNN was trained on STFT images of
IQ (iq_to_spectrogram). Here each band crop is cut from already-averaged FFT
rows, so the time axis is the display's frame cadence rather than an STFT hop,
and the frequency axis is the browser FFT's resolution. The crop is then run
through the same normalize_spectrogram (log, 5-99 percentile, SPEC_SIZE-square), which
is affine-invariant in dB, so absolute level and gain do not matter.
"""

from __future__ import annotations

import base64
import threading
import time
import uuid

import numpy as np
from scipy import signal
from sigsense_ml.preprocess import normalize_spectrogram

from sigsense_api.spectrum import (
    BURST_HOLD_S,
    WIDE_BIN_HZ,
    WIDE_BIN_SENSITIVITY_DB,
    SignalTracker,
    band_observation,
    find_bands,
    public_name,
)

MAX_ROWS = 256
MAX_BINS = 16384
MAX_BANDS = 6
MAX_SESSIONS = 16
SESSION_IDLE_S = 120.0
# Time percentile used for detection: a push-to-talk burst present in only a
# few rows still shows, while steady noise keeps a stable floor.
DETECT_PERCENTILE = 85.0
APPROXIMATION = (
    "Band crops come from averaged FFT rows, not IQ STFTs; time resolution is "
    "the display frame rate."
)


class SpectrumPayloadError(ValueError):
    pass


def decode_rows(data_b64: str, rows: int, bins: int, db_min: float, db_max: float):
    """Quantised uint8 rows (row-major, ascending frequency) back to dB."""
    if not (1 <= rows <= MAX_ROWS and 16 <= bins <= MAX_BINS):
        raise SpectrumPayloadError("rows/bins out of range")
    if not (np.isfinite(db_min) and np.isfinite(db_max)) or db_max <= db_min:
        raise SpectrumPayloadError("invalid dB range")
    try:
        raw = np.frombuffer(base64.b64decode(data_b64, validate=True), dtype=np.uint8)
    except Exception as exc:  # binascii.Error and friends
        raise SpectrumPayloadError("data is not valid base64") from exc
    if raw.size != rows * bins:
        raise SpectrumPayloadError(
            f"expected {rows * bins} samples, got {raw.size}"
        )
    return (
        raw.reshape(rows, bins).astype(np.float32) / 255.0 * (db_max - db_min) + db_min
    )


def _detection_spectrum(rows_db: np.ndarray) -> np.ndarray:
    if rows_db.shape[0] < 4:
        return rows_db.max(axis=0)
    return np.percentile(rows_db, DETECT_PERCENTILE, axis=0).astype(np.float32)


def _noise_model(rows_db: np.ndarray, floor_db: float):
    """Mean level and spread of the quiet part of the spectrum."""
    quiet = rows_db[rows_db < floor_db + 6.0]
    if quiet.size < 16:
        return floor_db, 1.0
    return float(np.median(quiet)), float(max(0.5, quiet.std()))


def band_crop(
    rows_db: np.ndarray,
    lo_bin: int,
    hi_bin: int,
    floor_db: float | None = None,
    margin: float = 0.5,
):
    """The CNN's SPEC_SIZE-square view of one band.

    The CNN learned from whole-capture images where a signal fills only part of
    the span, so it reads width relative to the span. A tight crop made every
    band fill the image and the labels collapsed (0/112 synthetic hits vs 28
    for the IQ path); the full span scored best (13/112), but then every band in
    the window would get the same image. So keep the full span and replace the
    other bands with noise at the measured floor, isolating this band.
    Rows are time (oldest first), columns frequency; the image is frequency down,
    time across, like the STFT the CNN was trained on.
    """
    rows_db = np.asarray(rows_db, dtype=np.float32)
    bins = rows_db.shape[1]
    if floor_db is not None:
        mean, std = _noise_model(rows_db, floor_db)
        width = hi_bin - lo_bin + 1
        pad = max(2, int(round(width * margin)))
        keep_lo, keep_hi = max(0, lo_bin - pad), min(bins, hi_bin + pad + 1)
        masked = rows_db.copy()
        rng = np.random.default_rng(0)
        for lo, hi in ((0, keep_lo), (keep_hi, bins)):
            if hi > lo:
                masked[:, lo:hi] = mean + std * rng.standard_normal(
                    (rows_db.shape[0], hi - lo)
                ).astype(np.float32)
        rows_db = masked
    magnitude = np.power(10.0, rows_db.T.astype(np.float64) / 20.0)
    return normalize_spectrogram(magnitude), bins


def find_spectrum_bands(
    rows_db: np.ndarray,
    center_freq_hz: float,
    sample_rate_hz: float,
    sensitivity_db: float = 10.0,
):
    """Occupied bands (with bin edges) in a block of spectrum rows."""
    bins = rows_db.shape[1]
    bin_hz = sample_rate_hz / bins
    # Frequency ascending, DC bin at index bins/2 (an fftshifted layout).
    freq = (np.arange(bins) - bins / 2.0) * bin_hz + center_freq_hz
    det = _detection_spectrum(rows_db)
    wide = bin_hz > WIDE_BIN_HZ
    sens = min(sensitivity_db, WIDE_BIN_SENSITIVITY_DB) if wide else sensitivity_db
    smooth = det if wide or bins < 7 else signal.savgol_filter(det, 7, 2)
    bands, floor = find_bands(det, smooth, freq, bin_hz, sens, wide, MAX_BANDS)
    for band in bands:
        lo = int(np.searchsorted(freq, band["low_hz"] + bin_hz / 2.0 - 1e-6))
        hi = int(np.searchsorted(freq, band["high_hz"] - bin_hz / 2.0 + 1e-6)) - 1
        band["_lo_bin"] = max(0, min(bins - 1, lo))
        band["_hi_bin"] = max(0, min(bins - 1, max(lo, hi)))
    return bands, floor, bin_hz


class SpectrumSessions:
    """One SignalTracker per browser session, bounded and idle-expired."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}

    def get(self, session: str) -> dict:
        now = time.monotonic()
        with self._lock:
            for key in [
                k for k, v in self._sessions.items() if now - v["used"] > SESSION_IDLE_S
            ]:
                self._sessions.pop(key)
            entry = self._sessions.get(session)
            if entry is None:
                if len(self._sessions) >= MAX_SESSIONS:
                    oldest = min(self._sessions, key=lambda k: self._sessions[k]["used"])
                    self._sessions.pop(oldest)
                entry = {
                    "tracker": SignalTracker(),
                    "busy": threading.Lock(),
                    "used": now,
                }
                self._sessions[session] = entry
            entry["used"] = now
            return entry


def session_trackers(sessions: SpectrumSessions, session: str | None) -> list:
    """Trackers to search for a track: one session's, or all of them."""
    with sessions._lock:
        if session is not None:
            entry = sessions._sessions.get(session)
            return [entry["tracker"]] if entry else []
        return [v["tracker"] for v in sessions._sessions.values()]


def analyze_spectrum_rows(
    engine,
    entry: dict,
    rows_db: np.ndarray,
    center_freq_hz: float,
    sample_rate_hz: float,
    sensitivity_db: float = 10.0,
    now: float | None = None,
) -> dict:
    """Classify the occupied bands in `rows_db` and update the session tracker."""
    started = time.perf_counter()
    now = time.time() if now is None else now
    tracker: SignalTracker = entry["tracker"]
    frame_id = uuid.uuid4().hex
    bands, floor, bin_hz = find_spectrum_bands(
        rows_db, center_freq_hz, sample_rate_hz, sensitivity_db
    )
    observations = []
    for band in bands:
        spec, crop_bins = band_crop(
            rows_db, band["_lo_bin"], band["_hi_bin"], floor_db=floor
        )
        clean = {k: v for k, v in band.items() if not k.startswith("_")}
        prediction = engine.analyze_spectrogram(
            spec,
            sample_rate=crop_bins * bin_hz,
            center_freq=band["center_freq_hz"],
        )
        observations.append(
            tracker.observe(
                band_observation(
                    clean,
                    prediction,
                    frame_id=frame_id,
                    evidence="CNN on spectrum rows",
                ),
                now=now,
            )
        )
    held = []
    for track in tracker.snapshot():
        age = now - track["last_seen"]
        if track.get("identity_type") != "rf_occupancy" or age > BURST_HOLD_S:
            continue
        held.append(
            {
                **track,
                "name": public_name(track, {}),
                "age_s": age,
                "stale": age > 2.5,
            }
        )
    seen = {t["id"] for t in observations}
    return {
        "type": "spectrum_bands",
        "frame_id": frame_id,
        "center_freq_hz": float(center_freq_hz),
        "sample_rate_hz": float(sample_rate_hz),
        "bin_hz": float(bin_hz),
        "rows": int(rows_db.shape[0]),
        "bins": int(rows_db.shape[1]),
        "noise_floor_db": float(floor),
        "bands": [
            {k: v for k, v in b.items() if not k.startswith("_")} for b in bands
        ],
        "observations": [t["id"] for t in observations],
        "tracks": held,
        "active_ids": sorted(seen),
        "approximation": APPROXIMATION,
        "latency_ms": (time.perf_counter() - started) * 1000.0,
    }
