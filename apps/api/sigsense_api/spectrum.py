"""Measured spectrum, occupancy detection and conservative observation tracking."""

from __future__ import annotations

import time
import uuid
import threading

import numpy as np
from scipy import signal
from sigsense_ml.preprocess import iq_to_spectrogram


# Above this bin width a 7-tap smoother spans more than a narrowband channel
# (12.5 kHz NBFM), so it would flatten exactly what we want to find.
WIDE_BIN_HZ = 2_000.0
# Detection margin used when bins are wide: a narrow carrier shares its power
# with fewer bins' worth of noise, but is still short of the usual 10 dB.
WIDE_BIN_SENSITIVITY_DB = 6.0
# Occupancy tracks re-match (and keep their identity) for this long after a
# burst, so push-to-talk transmissions accumulate on one track.
BURST_HOLD_S = 10.0
# With wide bins, band edges are trimmed to this far below the peak.
SKIRT_TRIM_DB = 25.0
# A modulated wideband emission (broadcast FM, TV, DAB, LTE) ripples and dips
# across its width. Runs inside one coarse occupied region are one signal.
COARSE_SMOOTH_HZ = 20_000.0
WIDEBAND_MIN_HZ = 75_000.0
WIDEBAND_MIN_OCCUPANCY = 0.55


def merge_wideband_runs(runs, smooth, active, floor, sensitivity_db, resolution):
    """Join fragments of one wideband emission into a single run.

    A coarse (power-averaged over ~20 kHz) occupancy map finds regions at least
    75 kHz wide. Narrow runs whose centre falls inside a region merge into one
    run spanning them, but only when the fragments really fill the region
    (>= 55% occupied): a row of separate 12.5 kHz carriers does not.
    """
    if len(runs) < 2:
        return runs
    n = len(smooth)
    win = int(round(COARSE_SMOOTH_HZ / resolution))
    if win < 3:
        return runs  # bins too wide for a coarse map to mean anything
    power = 10.0 ** (np.asarray(smooth, dtype=float) / 10.0)
    coarse = 10.0 * np.log10(
        np.convolve(power, np.ones(win) / win, mode="same") + 1e-30
    )
    on = coarse > floor + 0.7 * float(sensitivity_db)
    edges = np.diff(np.r_[False, on, False].astype(int))
    regions = [
        (a, b)
        for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))
        if (b - a) * resolution >= WIDEBAND_MIN_HZ
    ]
    if not regions:
        return runs
    merged = []
    used = [False] * len(runs)
    for a, b in regions:
        inside = [
            i for i, (s0, e0) in enumerate(runs) if a <= (s0 + e0) // 2 < b
        ]
        if len(inside) < 2:
            continue
        lo = min(runs[i][0] for i in inside)
        hi = max(runs[i][1] for i in inside)
        occupied = float(np.count_nonzero(active[lo:hi])) / max(1, hi - lo)
        if (hi - lo) * resolution < WIDEBAND_MIN_HZ or occupied < WIDEBAND_MIN_OCCUPANCY:
            continue
        for i in inside:
            used[i] = True
        merged.append((lo, hi))
    if not merged:
        return runs
    out = [r for i, r in enumerate(runs) if not used[i]] + merged
    return sorted(out)


def find_bands(db, smooth, freq, resolution, sensitivity_db, wide_bins, max_bands=8):
    """Occupied bands in a frequency-ordered dB spectrum.

    `smooth` decides occupancy (it may equal `db`); `db` locates peaks and
    band edges. Returns (bands sorted by power, noise floor in dB).
    """
    floor = float(np.percentile(smooth, 20))
    active = smooth > floor + float(sensitivity_db)
    # Fill only tiny spectral gaps; keep separate occupied bands separate.
    for i in range(1, len(active) - 1):
        if active[i - 1] and active[i + 1]:
            active[i] = True
    changes = np.diff(np.r_[False, active, False].astype(int))
    runs = list(zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)))
    runs = merge_wideband_runs(runs, smooth, active, floor, sensitivity_db, resolution)
    bands = []
    for start, end in runs:
        peak = start + int(np.argmax(db[start:end]))
        if db[peak] < floor + sensitivity_db:
            continue
        if wide_bins:
            # A strong narrow carrier's window skirt stays above a low
            # threshold for many bins; measure occupancy against the peak.
            keep = db[start:end] >= db[peak] - SKIRT_TRIM_DB
            lo = hi = peak - start
            while lo > 0 and keep[lo - 1]:
                lo -= 1
            while hi < len(keep) - 1 and keep[hi + 1]:
                hi += 1
            start, end = start + lo, start + hi + 1
        bands.append(
            {
                "center_freq_hz": float((freq[start] + freq[end - 1]) / 2),
                "bandwidth_hz": float(max(resolution, (end - start) * resolution)),
                "low_hz": float(freq[start] - resolution / 2),
                "high_hz": float(freq[end - 1] + resolution / 2),
                "peak_freq_hz": float(freq[peak]),
                "power_dbfs": float(db[peak]),
                "snr_db": float(db[peak] - floor),
            }
        )
    bands = sorted(bands, key=lambda x: x["power_dbfs"], reverse=True)[:max_bands]
    return bands, floor


def measure_spectrum(iq, sample_rate, center_freq, fft_size=1024, sensitivity_db=10.0):
    iq = np.asarray(iq, dtype=np.complex64).ravel()
    if len(iq) < 16 or not np.isfinite(iq).all():
        raise ValueError("Spectrum requires finite IQ samples")
    size = min(fft_size, 2 ** int(np.floor(np.log2(len(iq)))))
    # Average a bounded number of windows across the capture, not just its start.
    starts = np.linspace(0, len(iq) - size, min(24, max(1, len(iq) // size)), dtype=int)
    window = np.hanning(size)
    blocks = np.stack([iq[s : s + size] for s in starts])
    block_power = np.abs(np.fft.fft(blocks * window, axis=1)) ** 2 / (window.sum() ** 2)
    power = block_power.mean(axis=0)
    db = 10 * np.log10(np.fft.fftshift(power) + 1e-16)
    freq = np.fft.fftshift(np.fft.fftfreq(size, 1 / sample_rate)) + center_freq
    resolution = sample_rate / size
    wide_bins = resolution > WIDE_BIN_HZ
    # Keep the caller's margin for fine-resolution captures; relax it when
    # bins are wide enough that a narrow signal only fills a few of them.
    sensitivity_db = (
        min(float(sensitivity_db), WIDE_BIN_SENSITIVITY_DB)
        if wide_bins
        else float(sensitivity_db)
    )
    smooth = db if wide_bins or size < 7 else signal.savgol_filter(db, 7, 2)
    peak_hold = 10 * np.log10(np.fft.fftshift(block_power.max(axis=0)) + 1e-16)
    bands, floor = find_bands(db, smooth, freq, resolution, sensitivity_db, wide_bins)
    return {
        "center_freq_hz": float(center_freq),
        "sample_rate_hz": float(sample_rate),
        "fft_size": size,
        "start_hz": float(freq[0]),
        "bin_hz": float(resolution),
        "power_dbfs": db.astype(float).tolist(),
        "peak_hold_dbfs": peak_hold.astype(float).tolist(),
        "noise_floor_dbfs": floor,
        "resolution_hz": float(resolution),
        "threshold_dbfs": floor + sensitivity_db,
        "units": "dBFS/bin (Hann coherent-gain normalized)",
        "bands": bands,
        "timestamp": time.time(),
    }


def _channel_table():
    rows = []
    for i in range(7):  # GMRS/FRS 1-7, 12.5 kHz channels
        rows.append(("GMRS", i + 1, 462.5625e6 + i * 25e3, {"shared": "FRS"}))
    for i in range(7):  # GMRS/FRS 8-14
        rows.append(("GMRS", i + 8, 467.5625e6 + i * 25e3, {"shared": "FRS"}))
    for i in range(8):  # GMRS 15-22, 25 kHz channels
        rows.append(("GMRS", i + 15, 462.550e6 + i * 25e3, {}))
    for i in range(8):  # GMRS repeater inputs paired with 15-22
        rows.append(
            ("GMRS", i + 15, 467.550e6 + i * 25e3, {"repeater_input": True})
        )
    for i, mhz in enumerate((151.820, 151.880, 151.940, 154.570, 154.600)):
        rows.append(("MURS", i + 1, mhz * 1e6, {}))
    return rows


_CHANNELS = _channel_table()
_BAND_PLAN_TOLERANCE_HZ = 6_250.0
_BAND_PLAN_MAX_BANDWIDTH_HZ = 30_000.0


def band_plan_for(freq_hz, bandwidth_hz=None):
    """Licensed-service channel that a narrow signal sits on, if any.

    A frequency allocation only: it says where a service is permitted, never
    which transmitter is on the air.
    """
    if bandwidth_hz is not None and bandwidth_hz >= _BAND_PLAN_MAX_BANDWIDTH_HZ:
        return None
    best = None
    for service, channel, hz, extra in _CHANNELS:
        distance = abs(freq_hz - hz)
        if distance <= _BAND_PLAN_TOLERANCE_HZ and (best is None or distance < best[0]):
            best = (distance, service, channel, hz, extra)
    if best is None:
        return None
    _, service, channel, hz, extra = best
    return {
        "service": service,
        "channel": channel,
        "frequency_hz": float(hz),
        "label": f"{service} channel {channel} ({hz / 1e6:.4f} MHz)",
        **extra,
    }


def extract_band(iq, sample_rate, center_freq, band):
    """Mix, low-pass and decimate one occupied band for narrowband classification."""
    offset = band["center_freq_hz"] - center_freq
    width = max(band["bandwidth_hz"] * 1.5, sample_rate / 128)
    mixed = iq * np.exp(-2j * np.pi * offset * np.arange(len(iq)) / sample_rate)
    decimation = max(1, min(32, int(sample_rate / (width * 2))))
    if decimation > 1:
        mixed = signal.resample_poly(mixed, 1, decimation)
    if len(mixed) > 16384:
        start = (len(mixed) - 16384) // 2
        mixed = mixed[start : start + 16384]
    return np.asarray(mixed, dtype=np.complex64), sample_rate / decimation


# What a correction needs from a track: the exact model input and embedding
# behind its label. Bounded by count and age; a few hundred 128 KB arrays.
CAPTURE_MAX_TRACKS = 160
CAPTURE_PER_TRACK = 2
CAPTURE_TTL_S = 900.0


class SignalTracker:
    """BLE identities are explicit; undecoded RF bands are temporary occupancy tracks.

    Frequency or CNN similarity alone never establishes a physical device identity.
    """

    def __init__(self):
        self.tracks = {}
        self.lock = threading.RLock()
        # track id -> newest-last list of captures (see band_observation)
        self.captures = {}

    def _store_capture(self, track_id, capture, now):
        capture = {**capture, "t": now}
        kept = self.captures.pop(track_id, [])[-(CAPTURE_PER_TRACK - 1) :]
        self.captures[track_id] = kept + [capture]
        for key in [
            k
            for k, v in self.captures.items()
            if now - v[-1]["t"] > CAPTURE_TTL_S or k not in self.tracks
        ]:
            self.captures.pop(key, None)
        while len(self.captures) > CAPTURE_MAX_TRACKS:
            self.captures.pop(next(iter(self.captures)))

    def capture_for(self, track_id):
        """The newest model input kept for a track, or None."""
        with self.lock:
            kept = self.captures.get(track_id)
            return dict(kept[-1]) if kept else None

    def pin_label(self, track_id, label, now=None):
        """The user's label for this track stands for as long as it lives."""
        now = time.time() if now is None else now
        with self.lock:
            track = self.tracks.get(track_id)
            if track is None:
                return None
            track.update(
                pinned_label=label,
                label=label,
                confidence=1.0,
                evidence="Corrected by you",
                user_corrected=True,
            )
            return dict(track)

    def unpin(self, track_id):
        with self.lock:
            track = self.tracks.get(track_id)
            if track is not None and track.pop("pinned_label", None) is not None:
                track.pop("user_corrected", None)

    def relabel(self, track_id, label, confidence, learned=None, alternatives=None):
        """Update a track's label from a re-evaluated capture (not a pin)."""
        with self.lock:
            track = self.tracks.get(track_id)
            if track is None or track.get("pinned_label"):
                return None
            track.update(label=label, confidence=confidence, learned=learned)
            if alternatives is not None:
                track["alternatives"] = alternatives
            return dict(track)

    def observe(self, observation, now=None):
        now = time.time() if now is None else now
        observation = dict(observation)
        capture = observation.pop("_capture", None)
        with self.lock:
            identity = observation.get("device_id")
            track = self.tracks.get(identity) if identity else None
            if identity is None:
                candidates = []
                for old in self.tracks.values():
                    if (
                        old["identity_type"] != "rf_occupancy"
                        or now - old["last_seen"] > BURST_HOLD_S
                    ):
                        continue
                    overlap = max(
                        0,
                        min(old["high_hz"], observation["high_hz"])
                        - max(old["low_hz"], observation["low_hz"]),
                    )
                    ratio = overlap / max(
                        1, min(old["bandwidth_hz"], observation["bandwidth_hz"])
                    )
                    # Do not merge distinct confident classifications just because
                    # they occupy the same frequency at different times.
                    # (A track the user has corrected always takes its own signal.)
                    incompatible = (
                        not old.get("pinned_label")
                        and now - old["last_seen"] > 2.5
                        and old["confidence"] > 0.7
                        and observation["confidence"] > 0.7
                        and old["label"] != observation["label"]
                    )
                    if ratio > 0.65 and not incompatible:
                        candidates.append((ratio, old))
                if candidates:
                    track = max(candidates, key=lambda item: item[0])[1]
                identity = track["id"] if track else "rf:" + uuid.uuid4().hex[:16]
            if track is None:
                track = {
                    "id": identity,
                    "first_seen": now,
                    "observations": 0,
                    "packet_count": 0,
                    "identity_type": observation.get("identity_type", "rf_occupancy"),
                    "history": [],
                    "channels": [],
                    "label": observation["label"],
                    "confidence": observation["confidence"],
                }
                self.tracks[identity] = track
            count = track["observations"] + 1
            track.update(
                {
                    k: v
                    for k, v in observation.items()
                    if k != "device_id" and v is not None
                }
            )
            track.update(last_seen=now, observations=count)
            if "learned" in observation:
                track["learned"] = observation["learned"]  # None clears a stale badge
            if track.get("pinned_label"):
                track.update(
                    label=track["pinned_label"],
                    confidence=1.0,
                    evidence="Corrected by you",
                )
            if capture is not None:
                self._store_capture(identity, capture, now)
            track["packet_count"] = track.get("packet_count", 0) + observation.get(
                "new_packets", 0
            )
            channel = observation.get("channel")
            if channel is not None:
                track["channels"] = sorted(set(track["channels"]) | {channel})
            track["history"] = (track["history"] + [observation["power_dbfs"]])[-32:]
            if len(self.tracks) > 500:
                oldest = min(self.tracks, key=lambda k: self.tracks[k]["last_seen"])
                if oldest != identity:
                    self.tracks.pop(oldest)
            return dict(track)

    def snapshot(self):
        with self.lock:
            return [
                dict(t)
                for t in sorted(
                    self.tracks.values(), key=lambda t: t["last_seen"], reverse=True
                )[:100]
            ]


def public_name(track: dict, names: dict) -> str:
    """Saved name, then a discovered BLE name, then a stable fallback label."""
    return (
        names.get(track["id"])
        or track.get("discovered_name")
        or (
            "Unidentified emitter"
            if track.get("identity_type") == "rf_occupancy"
            else track.get("device_address") or track["id"]
        )
    )


def band_observation(band, prediction, *, frame_id, evidence="CNN on isolated band"):
    """Tracker observation for one classified band: CNN result plus band plan.

    The CNN label stands; the band plan is a separate, frequency-only hint.
    """
    plan = band_plan_for(band["center_freq_hz"], band["bandwidth_hz"]) or (
        band_plan_for(band["peak_freq_hz"], band["bandwidth_hz"])
    )
    note = "Occupancy track; transmitter identity unverified"
    if plan:
        evidence += f" · on {plan['label']}"
        note += "; channel from frequency allocation only"
        if prediction.is_unknown or prediction.confidence < 0.5:
            plan = {**plan, "likely_mode": "NFM"}
    learned = getattr(prediction, "learned", None)
    if learned:
        evidence += f" · learned from {learned['examples']} of your corrections"
    spec = getattr(prediction, "spec", None)
    capture = None
    if spec is not None:
        capture = {
            "spec": np.asarray(spec, dtype=np.float16),
            "embedding": np.asarray(prediction.embedding, dtype=np.float32),
            "cnn_probs": np.asarray(prediction.cnn_probs, dtype=np.float32),
            "label": prediction.label,
            "cnn_label": prediction.cnn_label,
        }
    return {
        **band,
        "_capture": capture,
        "learned": learned,
        "label": prediction.label,
        "confidence": prediction.confidence,
        "alternatives": prediction.alternatives,
        "neighbors": [
            {k: v for k, v in n.items() if k != "embedding"}
            for n in (getattr(prediction, "neighbors", None) or [])[:3]
        ],
        "evidence": evidence,
        "band_plan": plan,
        "identity_type": "rf_occupancy",
        "identity_note": note,
        "new_packets": 0,
        "frame_id": frame_id,
    }


def observe_frame(frame, engine, tracker):
    iq = frame["iq"]
    tuner = frame["tuner"]
    center = tuner["center_freq_hz"]
    rate = tuner["sample_rate_hz"]
    spectrum = frame["spectrum"]
    ble_packets = (frame.get("ble") or {}).get("packets", [])
    groups = {}
    contexts = {
        c["access_address"]: c for c in (frame.get("ble") or {}).get("connections", [])
    }
    for packet in ble_packets:
        if not packet.get("crc_ok"):
            continue
        address = packet.get("adv_address")
        if not address and packet.get("packet_kind") == "data":
            address = contexts.get(packet["access_address"], {}).get(
                "advertiser_address"
            )
        header = bytes.fromhex(packet["header_hex"])
        if packet.get("packet_kind") == "data":
            address_type = contexts.get(packet["access_address"], {}).get(
                "advertiser_address_type", "unknown"
            )
        else:
            address_type = (
                "random"
                if header[0] & (0x80 if packet["pdu_type"] in (3, 5) else 0x40)
                else "public"
            )
        identity = (
            f"ble:{address}:{address_type}"
            if address
            else f'ble-link:{packet["access_address"]}'
        )
        groups.setdefault(identity, []).append(packet)
    active = []
    for identity, packets in groups.items():
        packet = packets[-1]
        from sigsense_ml.ble import BLE_CHANNELS_MHZ

        frequency = BLE_CHANNELS_MHZ.get(packet["channel"], center / 1e6) * 1e6
        width = 2e6 if packet.get("phy") == "LE2M" else 1e6
        names = [
            a["detail"]
            for p in packets
            for a in p.get("ad_structures") or []
            if a["type"] in (8, 9)
        ]
        active.append(
            tracker.observe(
                {
                    "device_id": identity,
                    "identity_type": (
                        "ble_address" if identity.startswith("ble:") else "ble_link"
                    ),
                    "device_address": packet.get("adv_address")
                    or contexts.get(packet["access_address"], {}).get(
                        "advertiser_address"
                    ),
                    "discovered_name": names[-1] if names else None,
                    "center_freq_hz": frequency,
                    "bandwidth_hz": width,
                    "low_hz": frequency - width / 2,
                    "high_hz": frequency + width / 2,
                    "power_dbfs": max(
                        p["rssi_dbfs"] if p.get("rssi_dbfs") is not None else -120
                        for p in packets
                    ),
                    "snr_db": None,
                    "label": "Bluetooth-LE",
                    "confidence": 1.0,
                    "evidence": "CRC-validated BLE PDU",
                    "identity_note": "Address identity; rotating private addresses are not linked",
                    "channel": packet["channel"],
                    "new_packets": len(packets),
                    "frame_id": frame["frame_id"],
                }
            )
        )
    for band in spectrum["bands"][:5]:
        if any(
            band["low_hz"] < t["high_hz"] and band["high_hz"] > t["low_hz"]
            for t in active
        ):
            continue
        narrow, narrow_rate = extract_band(iq, rate, center, band)
        prediction = engine.analyze_spectrogram(
            iq_to_spectrogram(narrow),
            iq=narrow,
            sample_rate=narrow_rate,
            center_freq=band["center_freq_hz"],
        )
        families = getattr(engine, "live_families", None)
        evidence = "CNN on isolated band"
        if families is not None:
            fft = min(4096, len(iq))
            window = np.hanning(fft)
            blocks = iq[:len(iq) // fft * fft].reshape(-1, fft)
            power = np.abs(np.fft.fftshift(np.fft.fft(blocks * window, axis=1), axes=1)) ** 2 / window.sum() ** 2
            rows = 10 * np.log10(power + 1e-20)
            bin_hz = rate / fft
            lo = max(0, int(np.floor((band["low_hz"] - center) / bin_hz + fft / 2)))
            hi = min(fft - 1, int(np.ceil((band["high_hz"] - center) / bin_hz + fft / 2)))
            prediction = families.refine(prediction, rows, lo, hi, bin_hz)
            evidence = "RF family model on raw IQ"
        active.append(
            tracker.observe(
                band_observation(band, prediction, frame_id=frame["frame_id"], evidence=evidence)
            )
        )
    return active
