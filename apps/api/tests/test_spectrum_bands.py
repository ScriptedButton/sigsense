"""Backend identification of bands in browser spectrum rows."""

import asyncio
import base64
import time
import unittest

import numpy as np
from fastapi import HTTPException

from sigsense_ml.preprocess import SPEC_SIZE
from sigsense_api import main
from sigsense_api.inference import InferenceEngine
from sigsense_api.spectrum import BURST_HOLD_S
from sigsense_api.spectrum_bands import (
    SpectrumPayloadError,
    SpectrumSessions,
    analyze_spectrum_rows,
    decode_rows,
    find_spectrum_bands,
)

RATE = 1_024_000.0
BINS = 2048
CENTER = 462_600_000.0
BIN_HZ = RATE / BINS


def noise_rows(rows=40, seed=0):
    rng = np.random.default_rng(seed)
    return (-80.0 + 1.5 * rng.standard_normal((rows, BINS))).astype(np.float32)


def add_ridge(rows_db, freq_hz, width_hz=12_500.0, level_db=22.0, duty=1.0, seed=1):
    """A narrowband-FM-like ridge: flat top, wobbling edges, keyed by `duty`."""
    rng = np.random.default_rng(seed)
    centre_bin = int(round((freq_hz - CENTER) / BIN_HZ + BINS / 2))
    half = int(round(width_hz / BIN_HZ / 2))
    on = rng.random(rows_db.shape[0]) < duty
    for r in np.flatnonzero(on):
        wobble = int(rng.integers(-2, 3))
        lo, hi = centre_bin - half + wobble, centre_bin + half + wobble
        rows_db[r, lo:hi] += level_db + 2.0 * rng.standard_normal(hi - lo)
    return rows_db


def quantise(rows_db, db_min=-100.0, db_max=-20.0):
    q = np.clip((rows_db - db_min) / (db_max - db_min), 0, 1)
    raw = np.round(q * 255).astype(np.uint8)
    return base64.b64encode(raw.tobytes()).decode()


class SpectrumBandsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = InferenceEngine()

    def test_decode_round_trip_and_validation(self):
        rows = noise_rows(4)
        back = decode_rows(quantise(rows), 4, BINS, -100.0, -20.0)
        self.assertLess(float(np.abs(back - rows).max()), 80.0 / 255 / 2 + 1e-3)
        with self.assertRaises(SpectrumPayloadError):
            decode_rows("not base64!!", 4, BINS, -100.0, -20.0)
        with self.assertRaises(SpectrumPayloadError):
            decode_rows(quantise(rows), 5, BINS, -100.0, -20.0)
        with self.assertRaises(SpectrumPayloadError):
            decode_rows(quantise(rows), 4, BINS, -20.0, -100.0)

    def test_wideband_noise_yields_no_bands(self):
        for seed in range(5):
            bands, _floor, _bin = find_spectrum_bands(
                noise_rows(seed=seed), CENTER, RATE
            )
            self.assertEqual(bands, [], f"seed {seed}")

    def test_short_push_to_talk_burst_is_not_lost_in_history(self):
        for start in (0, 22, 45):
            rows = noise_rows(48)
            rows[start:start + 3] = add_ridge(
                rows[start:start + 3], 462_562_500.0
            )
            bands, _, _ = find_spectrum_bands(rows, CENTER, RATE)
            self.assertEqual(len(bands), 1, f"burst at row {start}")
            self.assertLess(abs(bands[0]["center_freq_hz"] - 462_562_500), 5000)

    def test_short_window_detection_rejects_noise_at_high_sensitivity(self):
        for seed in range(20):
            bands, _, _ = find_spectrum_bands(
                noise_rows(48, seed=seed), CENTER, RATE, sensitivity_db=6
            )
            self.assertEqual(bands, [], f"seed {seed}")

    def test_short_window_detection_rejects_unsmoothed_fft_noise(self):
        # Periodogram noise has much larger variance than the smoothed display
        # noise above. A maximum of individual rows would create false bands.
        for seed in range(10):
            rng = np.random.default_rng(seed)
            rows = (-80 + 10 * np.log10(rng.exponential(size=(48, 8192)))).astype(np.float32)
            bands, _, _ = find_spectrum_bands(
                rows, CENTER, 2_048_000, sensitivity_db=6
            )
            self.assertEqual(bands, [], f"seed {seed}")

    def test_gmrs_ridge_found_and_tagged(self):
        rows = add_ridge(noise_rows(), 462_562_500.0)
        entry = SpectrumSessions().get("t")
        result = analyze_spectrum_rows(self.engine, entry, rows, CENTER, RATE)
        self.assertEqual(len(result["bands"]), 1)
        band = result["bands"][0]
        self.assertLess(band["bandwidth_hz"], 40_000)
        self.assertLess(abs(band["center_freq_hz"] - 462_562_500.0), 10_000)
        track = next(t for t in result["tracks"] if t["id"] in result["active_ids"])
        self.assertEqual(track["band_plan"]["service"], "GMRS")
        self.assertEqual(track["band_plan"]["channel"], 1)
        self.assertEqual(track["evidence"].split(" · ")[0], "RF family model on spectrum rows")
        self.assertIn("label", track)  # CNN label is reported as-is, never overwritten
        self.assertEqual(result["bins"], BINS)

    def test_row_path_keeps_cnn_fidelity_when_family_model_is_unavailable(self):
        """Tight band crops collapsed labels to 0/8; full-span masking keeps most."""
        from sigsense_ml.synthetic import generate_iq

        from unittest.mock import patch

        fft, hits = 256, 0
        for seed in range(8):
            iq = generate_iq("NFM", n=8192, snr_db=15, seed=seed)
            w = np.hanning(fft)
            blocks = iq.reshape(-1, fft)
            power = np.abs(np.fft.fftshift(np.fft.fft(blocks * w, axis=1), axes=1)) ** 2
            rows = (10 * np.log10(power / w.sum() ** 2 + 1e-16)).astype(np.float32)
            entry = SpectrumSessions().get(f"nfm{seed}")
            with patch.object(self.engine, "live_families", None):
                result = analyze_spectrum_rows(self.engine, entry, rows, 100e6, 1.024e6)
            if result["tracks"] and result["tracks"][0]["label"] == "NFM":
                hits += 1
        self.assertGreaterEqual(hits, 4, f"only {hits}/8 NFM bursts identified")

    def test_band_crop_isolates_one_band(self):
        from sigsense_api.spectrum_bands import band_crop

        rows = add_ridge(add_ridge(noise_rows(32), 462_562_500.0), 462_637_500.0)
        bands, floor, _ = find_spectrum_bands(rows, CENTER, RATE)
        self.assertEqual(len(bands), 2)
        lo, hi = sorted(bands, key=lambda b: b["center_freq_hz"])
        image_lo, _ = band_crop(rows, lo["_lo_bin"], lo["_hi_bin"], floor_db=floor)
        image_hi, _ = band_crop(rows, hi["_lo_bin"], hi["_hi_bin"], floor_db=floor)
        self.assertEqual(image_lo.shape, (SPEC_SIZE, SPEC_SIZE))
        self.assertGreater(float(np.abs(image_lo - image_hi).mean()), 0.01)

    def test_burst_hold_keeps_track_after_silence(self):
        sessions = SpectrumSessions()
        entry = sessions.get("burst")
        t0 = 1_000_000.0
        busy = add_ridge(noise_rows(), 462_562_500.0, duty=0.3)
        first = analyze_spectrum_rows(self.engine, entry, busy, CENTER, RATE, now=t0)
        self.assertEqual(len(first["tracks"]), 1)
        quiet = analyze_spectrum_rows(
            self.engine, entry, noise_rows(seed=3), CENTER, RATE, now=t0 + 6
        )
        self.assertEqual(quiet["active_ids"], [])
        self.assertEqual([t["id"] for t in quiet["tracks"]], [first["tracks"][0]["id"]])
        self.assertTrue(quiet["tracks"][0]["stale"])
        gone = analyze_spectrum_rows(
            self.engine, entry, noise_rows(seed=4), CENTER, RATE, now=t0 + BURST_HOLD_S + 1
        )
        self.assertEqual(gone["tracks"], [])

    def test_sessions_are_independent_and_bounded(self):
        sessions = SpectrumSessions()
        a, b = sessions.get("a"), sessions.get("b")
        self.assertIsNot(a["tracker"], b["tracker"])
        for i in range(40):
            sessions.get(f"s{i}")
        self.assertLessEqual(len(sessions._sessions), 16)

    def test_latency_budget(self):
        rows = add_ridge(add_ridge(noise_rows(48), 462_562_500.0), 462_637_500.0)
        entry = SpectrumSessions().get("lat")
        analyze_spectrum_rows(self.engine, entry, rows, CENTER, RATE)  # warm up
        start = time.perf_counter()
        result = analyze_spectrum_rows(self.engine, entry, rows, CENTER, RATE)
        elapsed = (time.perf_counter() - start) * 1000
        print(f"\nspectrum-bands latency: {elapsed:.0f} ms for {len(result['bands'])} bands")
        self.assertLess(elapsed, 1500)

    def test_endpoint_drops_overlapping_request_and_rejects_bad_data(self):
        main.engine = self.engine
        body = main.SpectrumBandsRequest(
            session="ep",
            center_freq_hz=CENTER,
            sample_rate_hz=RATE,
            rows=40,
            bins=BINS,
            db_min=-100.0,
            db_max=-20.0,
            data=quantise(add_ridge(noise_rows(), 462_562_500.0)),
        )
        ok = asyncio.run(main.analyze_spectrum_bands(body))
        self.assertEqual(ok["type"], "spectrum_bands")
        self.assertNotIn("busy", ok)
        entry = main.spectrum_sessions.get("ep")
        self.assertTrue(entry["busy"].acquire(blocking=False))
        try:
            dropped = asyncio.run(main.analyze_spectrum_bands(body))
        finally:
            entry["busy"].release()
        self.assertEqual(dropped, {"type": "spectrum_bands", "busy": True})
        bad = body.model_copy(update={"data": "AAAA"})
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(main.analyze_spectrum_bands(bad))
        self.assertEqual(caught.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
