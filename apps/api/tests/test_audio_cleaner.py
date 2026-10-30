import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from scipy import signal

from sigsense_api.audio_cleaner import (
    RATE, CHANNELS, LiveCleaner, NeuralWorker, Processor, PROFILES, Resampler,
    SpectralReducer, optimize, pcm16, quality, rf_advice, router, save_profile, saved_profiles,
)


def fixture(seconds=6):
    rng = np.random.default_rng(753)
    t = np.arange(RATE * seconds) / RATE
    mask = ((t % 2) > .45) & ((t % 2) < 1.65)
    voice = sum(.12 / k * np.sin(2 * np.pi * (170 * k) * t + .6 * np.sin(2 * np.pi * 3 * t)) for k in range(1, 17))
    voice *= mask * (.6 + .4 * np.sin(2 * np.pi * 3.3 * t) ** 2)
    noise = rng.normal(0, .035, len(t))
    return voice.astype(np.float32), (voice + noise).astype(np.float32), mask


class CleanerDspTests(unittest.TestCase):
    def test_saved_aggressive_processing_is_revalidated_before_use(self):
        cleaner = LiveCleaner("Speech isolation")
        self.assertEqual(cleaner.profile.name, "Natural")
        self.assertEqual(cleaner.previous_profile, "Speech isolation")

    def test_overlap_add_and_resampling_are_chunk_invariant(self):
        x = np.random.default_rng(1).normal(0, .03, RATE * 3)
        for strength in (0, .65):
            a = SpectralReducer(strength).process(x)
            reducer = SpectralReducer(strength)
            b = np.concatenate([reducer.process(part) for part in np.array_split(x, 139)])
            np.testing.assert_allclose(a, b, atol=1e-12)
            if strength == 0:
                np.testing.assert_allclose(a[1024:], x[512:-512], atol=1e-12)
        for rate in (8000, 12000, 44100, 48000):
            source = np.random.default_rng(5).normal(0, .1, rate)
            a = Resampler(rate).process(source)
            resampler = Resampler(rate)
            b = np.concatenate([resampler.process(part) for part in np.array_split(source, 71)])
            np.testing.assert_allclose(a, b, atol=1e-6)
            self.assertLessEqual(abs(len(a) - RATE), 2)

    def test_anti_alias_filter_rejects_out_of_band_interference(self):
        rate = 48000
        t = np.arange(rate) / rate
        wanted = Resampler(rate).process(.2 * np.sin(2 * np.pi * 1500 * t))
        rejected = Resampler(rate).process(.2 * np.sin(2 * np.pi * 12000 * t))
        self.assertLess(np.std(rejected[1000:]) / np.std(wanted[1000:]), .01)

    def test_silence_and_destroyed_speech_cannot_win(self):
        clean, noisy, mask = fixture()
        self.assertFalse(quality(noisy, np.zeros_like(noisy), mask)["eligible"])
        destroyed = np.random.default_rng(9).normal(0, .001, len(noisy))
        self.assertFalse(quality(noisy, destroyed, mask)["eligible"])
        result = optimize(noisy, clean, "Natural", True, mask)
        self.assertIsNotNone(result["metrics"])
        self.assertTrue(result["metrics"]["eligible"])
        self.assertGreater(result["metrics"]["noiseReductionDb"], 3)
        self.assertGreaterEqual(result["metrics"]["speechRetention"], .87)

    def test_optimizer_rolls_back_a_destructive_neural_candidate(self):
        _, noisy, mask = fixture()
        result = optimize(noisy, np.zeros_like(noisy), "Speech isolation", True, mask)
        self.assertNotEqual(result["profile"], "Speech isolation")
        self.assertFalse(next(trial for trial in result["trials"] if trial["profile"] == "Speech isolation")["eligible"])
        quiet = optimize(noisy, noisy, "Natural", False, np.zeros(len(noisy), dtype=bool))
        self.assertEqual(quiet["profile"], "Natural")
        self.assertIsNone(quiet["metrics"])

    def test_stream_keeps_sample_clock_and_bounds_during_profile_changes(self):
        clean, noisy, mask = fixture(8)
        cleaner = LiveCleaner()
        out = []
        for at in range(0, len(noisy), 1600):
            result, original = cleaner.process(noisy[at:at + 1600], clean[at:at + 1600])
            out.append(result)
            self.assertEqual(len(result), len(original))
            if cleaner.due():
                cleaner.evaluate(True, mask[max(0, at + 1600 - RATE * 6):at + 1600])
        out = np.concatenate(out)
        self.assertEqual(len(out), len(noisy))
        self.assertTrue(np.isfinite(out).all())
        self.assertLessEqual(np.max(abs(out)), .951)
        self.assertGreaterEqual(cleaner.iterations, 2)
        self.assertLessEqual(len(cleaner.raw), RATE * 6)

    def test_rf_corrections_require_measured_evidence(self):
        values = np.full(129, -90.)
        values[65] = -48
        values[64], values[66] = -65, -62
        values[105] = -50
        meta = {"frequencyHz": 5950000, "gainDb": 20, "rfClipFraction": .02, "bandwidthHz": 6000,
                "spectrum": {"values": values.tolist(), "startHz": 5942000, "binHz": 125}}
        advice = rf_advice(meta)
        self.assertEqual(advice["gainDb"], 17)
        self.assertEqual(advice["bandwidthHz"], 4000)
        self.assertLessEqual(abs(advice["offsetCorrectionHz"]), 250)
        self.assertEqual(rf_advice({"frequencyHz": 5950000, "spectrum": {"values": [-90] * 100, "startHz": 5945000, "binHz": 125}}), {})

    def test_profiles_persist_privately_and_remain_bounded(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SIGSENSE_CLEANER_DIR": directory}):
            save_profile(5950000, {"profile": "Balanced", "metrics": {"eligible": True}})
            path, profiles = saved_profiles()
            self.assertEqual(profiles["5950000"]["profile"], "Balanced")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class CleanerNeuralTests(unittest.TestCase):
    def test_real_local_neural_worker_reduces_stationary_noise(self):
        async def run():
            worker = NeuralWorker()
            try:
                await worker.start()
                rng = np.random.default_rng(2)
                raw = rng.normal(0, .02, RATE * 2).astype(np.float32)
                output = np.concatenate([await worker.apply(part) for part in np.array_split(raw, 20)])
                self.assertEqual(len(output), len(raw))
                self.assertGreater(20 * np.log10(np.std(raw[RATE:]) / max(np.std(output[RATE:]), 1e-12)), 8)
            finally:
                await worker.close()
        asyncio.run(run())

    def test_websocket_pipeline_streams_pcm_and_closes_worker_on_disconnect(self):
        app = FastAPI(); app.include_router(router)
        client = TestClient(app)
        clean, noisy, mask = fixture()
        with client, patch("sigsense_api.audio_cleaner.speech_mask", side_effect=lambda audio: mask[:len(audio)]), patch("sigsense_api.audio_cleaner.save_profile"):
            with client.websocket_connect("/autofinder/cleaner/source/test-clean") as source:
                source.send_json({"frequencyHz": 5950000, "sampleRate": RATE, "source": "demo"})
                started = source.receive_json()
                self.assertTrue(started["neuralAvailable"])
                with client.websocket_connect("/autofinder/cleaner/listen/test-clean") as listener:
                    listener.receive_json()
                    for part in np.array_split(noisy[:RATE // 2], 5):
                        source.send_bytes(pcm16(part))
                        audio = listener.receive_bytes()
                        self.assertEqual(len(audio), 3200)
                # End-to-end processing keeps optimizing even without a listening browser.
                for part in np.array_split(noisy[RATE // 2:RATE * 5], 45):
                    source.send_bytes(pcm16(part))
                report, returned_audio = {}, 0
                while not report.get("iterations"):
                    message = source.receive()
                    if message.get("bytes") is not None:
                        self.assertEqual(len(message["bytes"]), 3200)
                        returned_audio += 1
                    elif message.get("text"):
                        report = json.loads(message["text"])
                self.assertGreater(returned_audio, 0)
                self.assertGreaterEqual(report["iterations"], 1)
                self.assertEqual(client.get("/autofinder/cleaner/status/test-clean").status_code, 200)
            self.assertNotIn("test-clean", CHANNELS)
            self.assertEqual(client.get("/autofinder/cleaner/status/test-clean").status_code, 404)


if __name__ == "__main__":
    unittest.main()
