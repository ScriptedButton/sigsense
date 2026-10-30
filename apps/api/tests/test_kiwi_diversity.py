import unittest
from unittest.mock import patch

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from scipy import signal

from sigsense_api.kiwi_diversity import (
    RATE, HOP, HISTORY, History, CoherenceFilter, DiversitySession,
    Resampler, find_alignment, router,
)


def transmission(seconds=10, seed=17):
    rng = np.random.default_rng(seed)
    count = RATE * seconds
    # Broadband, syllabically modulated fixture rather than a matchable carrier.
    voice = signal.sosfilt(signal.butter(4, [240, 3000], btype="bandpass", fs=RATE, output="sos"), rng.normal(size=count))
    t = np.arange(count) / RATE
    return (voice * .18 * (.35 + .65 * np.sin(t * 8) ** 2)).astype(np.float32)


class AlignmentTests(unittest.TestCase):
    def test_resampler_is_chunk_invariant_and_rejects_aliasing(self):
        rng = np.random.default_rng(18)
        for rate in (8000, 12000, 44100, 48000):
            samples = rng.normal(0, .1, rate)
            entire = Resampler(rate).process(samples)
            chunked = Resampler(rate)
            result = np.concatenate([chunked.process(part) for part in np.array_split(samples, 71)])
            np.testing.assert_allclose(result, entire, atol=1e-6)
            self.assertLessEqual(abs(len(result) - RATE), 2)
        t = np.arange(48000) / 48000
        alias = Resampler(48000).process(np.sin(2 * np.pi * 12000 * t))
        self.assertLess(np.std(alias[1000:]), .01)

    def test_broadband_match_with_delay_polarity_and_independent_noise(self):
        voice = transmission(6)
        rng = np.random.default_rng(8)
        reference = -.7 * voice + rng.normal(0, .008, len(voice))
        start = 17320
        local = voice[start:start + RATE * 2] + rng.normal(0, .015, RATE * 2)
        match = find_alignment(local, reference)
        self.assertIsNotNone(match)
        self.assertLessEqual(abs(match[0] - start), 4)
        self.assertGreater(match[1], .8)

    def test_unrelated_transmission_carrier_silence_and_repetition_rejected(self):
        voice = transmission(6)
        self.assertIsNone(find_alignment(voice[:RATE * 2], transmission(6, 90)))
        self.assertIsNone(find_alignment(np.zeros(RATE * 2), np.zeros(RATE * 6)))
        tone = np.sin(np.arange(RATE * 6) * 2 * np.pi * 1000 / RATE)
        self.assertIsNone(find_alignment(tone[:RATE * 2], tone))
        repeated = np.tile(voice[:RATE], 6)
        self.assertIsNone(find_alignment(repeated[:RATE * 2], repeated))

    def test_history_is_bounded_and_coordinates_survive_trimming(self):
        history = History()
        history.push(np.arange(HISTORY + 400, dtype=np.float32))
        self.assertEqual(len(history.data), HISTORY)
        self.assertEqual(history.start, 400)
        self.assertIsNone(history.get(0, 10))
        np.testing.assert_array_equal(history.get(400, 410), np.arange(400, 410))


class FilterTests(unittest.TestCase):
    def test_no_reference_is_aligned_local_passthrough(self):
        x = transmission(3)
        processor = CoherenceFilter()
        results = [processor.process(hop) for hop in np.array_split(x[:len(x) // HOP * HOP], len(x) // HOP)]
        clean = np.concatenate([a for a, _ in results])
        raw = np.concatenate([b for _, b in results])
        np.testing.assert_allclose(clean, raw, atol=1e-12)
        np.testing.assert_allclose(raw[HOP:], x[:len(raw) - HOP], atol=1e-12)

    def test_local_only_whistle_reduced_without_remote_insertion(self):
        voice = transmission(10)
        rng = np.random.default_rng(22)
        t = np.arange(len(voice)) / RATE
        local = voice + .09 * np.sin(2 * np.pi * 2300 * t) + rng.normal(0, .008, len(t))
        reference = -.8 * voice + rng.normal(0, .006, len(t))
        processor = CoherenceFilter()
        chunks = [processor.process(local[i:i + HOP], reference[i:i + HOP])
                  for i in range(0, len(local) - HOP + 1, HOP)]
        clean = np.concatenate([a for a, _ in chunks])
        raw = np.concatenate([b for _, b in chunks])
        target = np.r_[np.zeros(HOP), voice][:len(clean)]
        skip = RATE * 2
        before = np.mean((raw[skip:] - target[skip:]) ** 2)
        after = np.mean((clean[skip:] - target[skip:]) ** 2)
        improvement = 10 * np.log10(before / after)
        self.assertGreater(improvement, 4)
        # All-zero local input MUST remain zero regardless of the reference.
        silent = CoherenceFilter()
        for i in range(0, RATE - HOP, HOP):
            output, _ = silent.process(np.zeros(HOP), reference[i:i + HOP])
            np.testing.assert_array_equal(output, np.zeros(HOP))

    def test_loss_of_reference_restores_passthrough(self):
        x = transmission(3)
        processor = CoherenceFilter()
        for i in range(0, RATE, HOP):
            processor.process(x[i:i + HOP], x[i:i + HOP])
        for i in range(RATE, RATE + HOP * 3, HOP):
            clean, raw = processor.process(x[i:i + HOP], None)
        np.testing.assert_allclose(clean, raw, atol=1e-12)

    def test_unrelated_reference_disables_instantaneous_suppression(self):
        x, unrelated = transmission(5), transmission(5, 91)
        processor = CoherenceFilter()
        for i in range(0, RATE * 4 - HOP, HOP):
            processor.process(x[i:i + HOP], unrelated[i:i + HOP])
        self.assertLess(processor.reduction_db, .01)


class SessionTests(unittest.TestCase):
    def test_realistic_48k_local_and_12k_kiwi_streams(self):
        voice = transmission(10)
        local = signal.resample_poly(voice, 3, 1).astype(np.float32)
        reference = signal.resample_poly(voice, 3, 4).astype(np.float32)
        session = DiversitySession(48000, 12000)
        output = []
        for index in range(500):
            output.extend(session.feed(0, local[index * 960:(index + 1) * 960]))
            output.extend(session.feed(1, reference[index * 240:(index + 1) * 240]))
        self.assertTrue(session.locked, session.status())
        self.assertGreater(session.confidence, .55)
        self.assertLessEqual(abs(len(output) * HOP - RATE * 8), HOP)
        self.assertTrue(np.isfinite(np.concatenate([x[0] for x in output])).all())

    def test_stream_alignment_drift_and_mismatch_fallback(self):
        voice = transmission(14)
        rng = np.random.default_rng(99)
        # Reference delayed 210ms; 80ppm independent sample clock difference.
        indexes = np.arange(len(voice))
        reference = np.interp((indexes - 3360) * (1 - 80e-6), indexes, voice, left=0)
        reference += rng.normal(0, .004, len(reference))
        local = voice + rng.normal(0, .006, len(voice))
        session = DiversitySession(RATE, RATE)
        output = []
        for start in range(0, RATE * 11, 800):
            output.extend(session.feed(0, local[start:start + 800]))
            output.extend(session.feed(1, reference[start:start + 800]))
        self.assertTrue(session.locked, session.status())
        self.assertGreater(session.confidence, .6)
        self.assertAlmostEqual(session.offset, 3360 + 80e-6 * session.anchor, delta=12)
        self.assertAlmostEqual(session.drift * 1e6, 80, delta=40)
        self.assertEqual(len(output) * HOP, (RATE * 9 // HOP) * HOP)
        # Retuned/unrelated reference must clear alignment, not force suppression.
        unrelated = transmission(4, 500)
        for offset in range(0, RATE * 3, 800):
            session.feed(0, local[RATE * 11 + offset:RATE * 11 + offset + 800])
            session.feed(1, unrelated[offset:offset + 800])
        self.assertFalse(session.locked)
        self.assertEqual(session.status()["state"], "local-only")

    def test_stream_loss_revokes_match(self):
        session = DiversitySession(RATE, RATE)
        session.locked = True
        session.last_seen[1] -= 5
        session.feed(0, np.ones(800, dtype=np.float32))
        self.assertFalse(session.locked)


class SocketTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_configuration_pcm_and_interleaved_output(self):
        with self.client.websocket_connect("/kiwi-diversity") as ws:
            ws.send_json({"type": "configure", "localRate": RATE, "referenceRate": 12000})
            self.assertEqual(ws.receive_json()["sampleRate"], RATE)
            packet = np.full(8000, 600, dtype="<i2").tobytes()
            for _ in range(6):
                ws.send_bytes(b"\0" + packet)
            # First status is emitted before buffered audio becomes available.
            message = ws.receive()
            if message.get("text"):
                message = ws.receive()
            output = message["bytes"]
            self.assertEqual(len(output) % 4, 0)
            pairs = np.frombuffer(output, dtype="<i2").reshape(-1, 2)
            np.testing.assert_array_equal(pairs[:, 0], pairs[:, 1])
            self.assertEqual(np.max(pairs[:, 1]), 600, "fallback must preserve the original PCM amplitude")

    def test_invalid_config_and_packets_are_rejected(self):
        for config in ({"type": "configure", "localRate": 999999, "referenceRate": RATE},
                       {"type": "configure", "localRate": True, "referenceRate": RATE}, []):
            with self.client.websocket_connect("/kiwi-diversity") as ws:
                ws.send_json(config)
                self.assertEqual(ws.receive_json()["type"], "error")
        for packet in (b"bad", b"\0\0", b"\2\0\0", b"\0" + bytes(RATE * 2 + 2)):
            with self.client.websocket_connect("/kiwi-diversity") as ws:
                ws.send_json({"type": "configure", "localRate": RATE, "referenceRate": RATE})
                ws.receive_json()
                ws.send_bytes(packet)
                self.assertEqual(ws.receive_json()["type"], "error")

    def test_session_limit_releases_without_touching_other_sessions(self):
        with patch("sigsense_api.kiwi_diversity._sessions", {object(), object()}):
            with self.client.websocket_connect("/kiwi-diversity") as ws:
                self.assertEqual(ws.receive_json()["type"], "error")

    def test_wrong_message_types_fail_cleanly_and_release_session(self):
        with self.client.websocket_connect("/kiwi-diversity") as ws:
            ws.send_bytes(b"not json")
            self.assertEqual(ws.receive_json()["type"], "error")
        with self.client.websocket_connect("/kiwi-diversity") as ws:
            ws.send_json({"type": "configure", "localRate": RATE, "referenceRate": RATE})
            ws.receive_json()
            ws.send_text("unexpected")
            self.assertEqual(ws.receive_json()["type"], "error")
        with self.client.websocket_connect("/kiwi-diversity") as ws:
            ws.send_json({"type": "configure", "localRate": RATE, "referenceRate": RATE})
            self.assertEqual(ws.receive_json()["type"], "status")


if __name__ == "__main__":
    unittest.main()
