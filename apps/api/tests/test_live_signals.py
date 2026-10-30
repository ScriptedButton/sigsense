"""Spectrum measurements, identity boundaries, storage and non-emitting TX checks."""

from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from sigsense_api.signal_store import SignalStore
from sigsense_api.spectrum import (
    SignalTracker,
    measure_spectrum,
    observe_frame,
    public_name,
)
from sigsense_api.sdr import LiveSDRService


class SpectrumTests(unittest.TestCase):
    def test_two_tones_have_correct_frequency_and_power(self):
        fs = 4e6
        center = 100e6
        n = np.arange(16384)
        iq = np.exp(2j * np.pi * 100 * fs / 1024 * n / fs) + 0.5 * np.exp(
            -2j * np.pi * 160 * fs / 1024 * n / fs
        )
        rng = np.random.default_rng(42)
        iq += 0.001 * (rng.normal(size=len(iq)) + 1j * rng.normal(size=len(iq)))
        result = measure_spectrum(iq, fs, center)
        bands = result["bands"]
        self.assertEqual(len(bands), 2)
        self.assertAlmostEqual(
            bands[0]["peak_freq_hz"], center + 100 * fs / 1024, delta=fs / 1024
        )
        self.assertAlmostEqual(bands[0]["power_dbfs"], 0, places=2)
        self.assertAlmostEqual(bands[1]["power_dbfs"], -6.0206, places=2)

    def test_empty_and_nonfinite_iq_rejected(self):
        for iq in [[], np.full(64, np.nan)]:
            with self.assertRaises(ValueError):
                measure_spectrum(iq, 4e6, 100e6)


class TrackerTests(unittest.TestCase):
    def observation(self, **changes):
        return dict(
            identity_type="rf_occupancy",
            low_hz=99,
            high_hz=101,
            center_freq_hz=100,
            bandwidth_hz=2,
            label="FSK",
            confidence=0.9,
            power_dbfs=-20,
            **changes
        )

    def test_rf_duplicates_are_temporary_not_physical_identities(self):
        tracker = SignalTracker()
        a = tracker.observe(self.observation(), now=1)
        b = tracker.observe(self.observation(), now=2)
        self.assertEqual(a["id"], b["id"])
        c = tracker.observe({**self.observation(), "label": "AM"}, now=3)
        self.assertEqual(a["id"], c["id"])
        d = tracker.observe(self.observation(), now=20)
        self.assertNotEqual(a["id"], d["id"])
        self.assertEqual(b["observations"], 2)

    def test_ble_address_tracks_across_channel_and_retains_name(self):
        tracker = SignalTracker()
        a = tracker.observe(
            self.observation(
                device_id="ble:AB:random",
                channel=37,
                new_packets=2,
                discovered_name="Lamp",
            ),
            now=1,
        )
        b = tracker.observe(
            self.observation(
                device_id="ble:AB:random",
                channel=39,
                new_packets=3,
                discovered_name=None,
            ),
            now=2,
        )
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(b["channels"], [37, 39])
        self.assertEqual(b["packet_count"], 5)
        self.assertEqual(b["discovered_name"], "Lamp")

    def test_public_name_prefers_saved_then_discovered_then_fallback(self):
        occupancy = {"id": "rf:1", "identity_type": "rf_occupancy"}
        ble = {
            "id": "ble:AA:random",
            "identity_type": "ble_address",
            "device_address": "AA",
            "discovered_name": "Lamp",
        }
        self.assertEqual(public_name(occupancy, {}), "Unidentified emitter")
        self.assertEqual(public_name(ble, {}), "Lamp")
        self.assertEqual(public_name(ble, {"ble:AA:random": "Desk lamp"}), "Desk lamp")
        self.assertEqual(
            public_name({**ble, "discovered_name": None}, {}),
            "AA",
        )

    def test_connected_data_uses_address_type_from_connection_not_ll_header(self):
        tracker = SignalTracker()
        context = {
            "access_address": "0x12345678",
            "advertiser_address": "AA:BB:CC:DD:EE:FF",
            "advertiser_address_type": "random",
        }
        packet = {
            "crc_ok": True,
            "packet_kind": "data",
            "adv_address": None,
            "access_address": "0x12345678",
            "header_hex": "0100",
            "pdu_type": 1,
            "channel": 3,
            "ad_structures": [],
            "phy": "LE1M",
            "rssi_dbfs": 0,
        }
        frame = {
            "frame_id": "a",
            "iq": np.zeros(64),
            "tuner": {"center_freq_hz": 2410e6, "sample_rate_hz": 4e6},
            "spectrum": {"bands": []},
            "ble": {"connections": [context], "packets": [packet]},
        }
        tracks = observe_frame(frame, None, tracker)
        self.assertEqual(tracks[0]["id"], "ble:AA:BB:CC:DD:EE:FF:random")
        self.assertEqual(tracks[0]["power_dbfs"], 0)


class StorageTests(unittest.TestCase):
    def test_exact_duplicate_persistence_rename_and_checksum(self):
        with TemporaryDirectory() as root:
            store = SignalStore(root)
            iq = np.arange(64, dtype=np.float32).astype(np.complex64)
            metadata = {
                "center_freq_hz": 2402e6,
                "sample_rate_hz": 4e6,
                "device_id": "ble:test",
            }
            record = store.save(iq, metadata, "First capture")
            duplicate = store.save(iq.copy(), metadata, "Duplicate")
            self.assertEqual(record["id"], duplicate["id"])
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(len(store.list_recordings()), 1)
            store.name_device("ble:test", "Desk lamp")
            store.rename(record["id"], "Renamed")
            np.testing.assert_array_equal(store.load(record["id"])[0], iq)
            store.db.close()
            store = SignalStore(root)
            loaded = store.get(record["id"])
            self.assertEqual(loaded["name"], "Renamed")
            self.assertEqual(loaded["device_name"], "Desk lamp")
            np.save(store.path(record["id"]), iq + 1)
            with self.assertRaisesRegex(ValueError, "checksum"):
                store.load(record["id"])
            with self.assertRaises(KeyError):
                store.path("../../outside")
            store.db.close()


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_scanner_and_frame_recovery(self):
        service = LiveSDRService()
        await service.configure(demo=True, ble_decode=False, ble_hop=False)
        await service.configure_scanner(
            enabled=True, start_hz=100e6, stop_hz=104e6, step_hz=2e6, dwell_ms=50
        )
        frame = await service.next_frame()
        self.assertEqual(frame["scanner"]["steps"], 3)
        self.assertIs(service.captured_frame(frame["frame_id"]), frame)
        self.assertTrue(any(v is None for v in frame["sweep"]["power_dbfs"]))
        service._scan_changed -= 1
        frame2 = await service.next_frame()
        self.assertEqual(frame2["tuner"]["center_freq_hz"], 102e6)
        await service.configure(center_freq_hz=100e6)
        self.assertFalse(service.scan["enabled"])
        with self.assertRaises(ValueError):
            await service.configure_scanner(
                enabled=True, start_hz=1e6, stop_hz=6e9, step_hz=1
            )
        with self.assertRaises(ValueError):
            await service.configure(sample_rate_hz=float("nan"))

    async def test_dry_run_never_calls_transmitter(self):
        service = LiveSDRService()
        with patch(
            "sigsense_api.sdr.transmit_iq",
            side_effect=AssertionError("Must not transmit"),
        ):
            result = await service.transmit_saved(
                np.ones(100, dtype=np.complex64),
                {"center_freq_hz": 2402e6, "sample_rate_hz": 4e6},
                dry_run=True,
            )
        self.assertFalse(result["transmitted"])
        self.assertTrue(result["dry_run"])
        with self.assertRaises(ValueError):
            await service.transmit_saved(
                np.ones(100),
                {"center_freq_hz": 2402e6, "sample_rate_hz": 4e6},
                repeats=21,
                dry_run=True,
            )


if __name__ == "__main__":
    unittest.main()
