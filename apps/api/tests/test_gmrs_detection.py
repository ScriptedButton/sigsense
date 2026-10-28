"""Narrowband detection, burst hold and band-plan hints for GMRS-style signals."""

import unittest

import numpy as np

from sigsense_api.spectrum import (
    BURST_HOLD_S,
    SignalTracker,
    band_plan_for,
    measure_spectrum,
    observe_frame,
)

RATE = 4e6
CENTER = 463.0e6
GMRS_1 = 462.5625e6


def nbfm_capture(freq_hz, n=65536, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(n) / RATE
    # 1 kHz tone, 2.5 kHz deviation: modulation index 2.5 rad (a typical NBFM voice peak)
    phase = 2 * np.pi * (freq_hz - CENTER) * t + 2.5 * (-np.cos(2 * np.pi * 1000 * t))
    iq = 0.3 * np.exp(1j * phase)
    iq += 0.01 * (rng.normal(size=n) + 1j * rng.normal(size=n))
    return iq.astype(np.complex64)


class NarrowbandTests(unittest.TestCase):
    def test_nbfm_burst_is_a_narrow_band_at_the_right_frequency(self):
        result = measure_spectrum(nbfm_capture(GMRS_1), RATE, CENTER)
        self.assertTrue(result["bands"])
        band = result["bands"][0]
        self.assertLess(band["bandwidth_hz"], 40e3)
        self.assertAlmostEqual(band["center_freq_hz"], GMRS_1, delta=8e3)
        self.assertEqual(len(result["peak_hold_dbfs"]), len(result["power_dbfs"]))

    def test_fine_resolution_keeps_requested_sensitivity(self):
        result = measure_spectrum(
            nbfm_capture(GMRS_1, n=1 << 18), 0.25e6, 462.55e6, fft_size=1024,
            sensitivity_db=10.0,
        )
        self.assertAlmostEqual(
            result["threshold_dbfs"] - result["noise_floor_dbfs"], 10.0, places=6
        )

    def test_band_plan_maps_gmrs_channels_only_for_narrow_signals(self):
        plan = band_plan_for(GMRS_1 + 2e3, 12.5e3)
        self.assertEqual((plan["service"], plan["channel"]), ("GMRS", 1))
        self.assertIsNone(band_plan_for(GMRS_1, 200e3))
        self.assertIsNone(band_plan_for(100e6, 12.5e3))
        self.assertEqual(band_plan_for(462.725e6, 20e3)["channel"], 22)

    def test_observe_frame_attaches_band_plan_without_touching_label(self):
        class Prediction:
            label, confidence, is_unknown, alternatives = "Noise", 0.2, True, []

        class Engine:
            def analyze_spectrogram(self, *a, **k):
                return Prediction()

        iq = nbfm_capture(GMRS_1)
        frame = {
            "iq": iq,
            "frame_id": "f",
            "tuner": {"center_freq_hz": CENTER, "sample_rate_hz": RATE},
            "spectrum": measure_spectrum(iq, RATE, CENTER),
        }
        tracks = observe_frame(frame, Engine(), SignalTracker())
        planned = [t for t in tracks if t.get("band_plan")]
        self.assertTrue(planned)
        track = planned[0]
        self.assertEqual(track["band_plan"]["channel"], 1)
        self.assertEqual(track["band_plan"]["service"], "GMRS")
        self.assertEqual(track["band_plan"]["likely_mode"], "NFM")
        self.assertEqual(track["label"], "Noise")
        self.assertIn("GMRS channel 1", track["evidence"])


class BurstHoldTests(unittest.TestCase):
    def obs(self, power=-40.0):
        return {
            "center_freq_hz": GMRS_1, "bandwidth_hz": 12.5e3,
            "low_hz": GMRS_1 - 6e3, "high_hz": GMRS_1 + 6e3,
            "power_dbfs": power, "label": "NFM", "confidence": 0.6,
            "identity_type": "rf_occupancy", "new_packets": 0,
        }

    def test_bursts_within_hold_window_share_a_track(self):
        tracker = SignalTracker()
        first = tracker.observe(self.obs(), now=100.0)
        second = tracker.observe(self.obs(-42.0), now=100.0 + BURST_HOLD_S - 1)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["observations"], 2)
        self.assertEqual(len(tracker.snapshot()), 1)

    def test_burst_after_hold_window_starts_a_new_track(self):
        tracker = SignalTracker()
        first = tracker.observe(self.obs(), now=100.0)
        later = tracker.observe(self.obs(), now=100.0 + BURST_HOLD_S + 1)
        self.assertNotEqual(first["id"], later["id"])


if __name__ == "__main__":
    unittest.main()


class WidebandMergeTests(unittest.TestCase):
    def _wfm_bands(self):
        rng = np.random.default_rng(1)
        fs, n = 1.024e6, 1 << 18
        t = np.arange(n) / fs
        m = (
            0.5 * np.sin(2 * np.pi * 400 * t)
            + 0.3 * np.sin(2 * np.pi * 3100 * t) * np.sin(2 * np.pi * 38e3 * t)
            + 0.1 * np.sin(2 * np.pi * 19e3 * t)
            + 0.04 * np.sin(2 * np.pi * 57e3 * t)
        )
        iq = np.exp(2j * np.pi * 75e3 * np.cumsum(m) / fs)
        iq = iq + 0.001 * (rng.normal(size=n) + 1j * rng.normal(size=n))
        return measure_spectrum(iq, fs, 93.9e6, fft_size=2048)["bands"]

    def test_broadcast_fm_is_one_band_not_fragments(self):
        bands = self._wfm_bands()
        self.assertEqual(len(bands), 1)
        self.assertGreater(bands[0]["bandwidth_hz"], 150e3)
        self.assertAlmostEqual(bands[0]["center_freq_hz"], 93.9e6, delta=20e3)

    def test_separate_narrow_carriers_stay_separate(self):
        rng = np.random.default_rng(2)
        fs, n = 2e6, 1 << 18
        t = np.arange(n) / fs
        iq = 0.2 * sum(
            np.exp(2j * np.pi * f * t) * (1 + 0.5 * np.sin(2 * np.pi * 1000 * t))
            for f in (-100e3, -75e3, -50e3, -25e3, 0)
        )
        iq = iq + 0.002 * (rng.normal(size=n) + 1j * rng.normal(size=n))
        bands = measure_spectrum(iq, fs, 462.6e6, fft_size=4096)["bands"]
        self.assertEqual(len(bands), 5)
