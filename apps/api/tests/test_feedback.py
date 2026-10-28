"""Manual corrections: storage, persistence, undo, decisions and the HTTP handlers.

Every test works on a COPY of the models folder; the real library is never touched.
"""

import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
from fastapi import HTTPException

from sigsense_api import learning
from sigsense_api.feedback import FeedbackError, canonical_label
from sigsense_api.feedback_routes import FeedbackRequest, build_router
from sigsense_api.inference import DEFAULT_MODELS, InferenceEngine
from sigsense_api.spectrum import (
    CAPTURE_MAX_TRACKS,
    SignalTracker,
    band_observation,
)
from sigsense_ml.preprocess import iq_to_spectrogram
from sigsense_ml.synthetic import generate_iq


def models_copy(root: Path) -> Path:
    models = root / "models"
    models.mkdir()
    for name in ("signal_embedding.pt", "library.faiss", "library.sqlite"):
        shutil.copy2(DEFAULT_MODELS / name, models / name)
    return models


def spec_of(name: str, seed: int, snr: float = 15.0) -> np.ndarray:
    return iq_to_spectrogram(generate_iq(name, n=16384, snr_db=snr, seed=seed))


class FeedbackCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sigsense-fb-"))
        self.models = models_copy(self.tmp)
        self.engine = InferenceEngine(models_dir=self.models)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def predict(self, spec):
        return self.engine.analyze_spectrogram(spec)


class StoreTests(FeedbackCase):
    def test_a_correction_changes_the_next_prediction_and_undo_restores_it(self):
        spec = spec_of("NFM", 3)
        before = self.predict(spec)
        target = "WFM" if before.label != "WFM" else "AM"
        item = self.engine.feedback.add(spec, target, wrong_label=before.label)
        after = self.predict(spec)
        self.assertEqual(after.label, target)
        self.assertEqual(after.learned["examples"], 1)
        self.assertTrue(self.engine.feedback.remove(item["id"]))
        restored = self.predict(spec)
        self.assertEqual(restored.label, before.label)
        self.assertIsNone(restored.learned)

    def test_an_unrelated_signal_is_left_alone(self):
        other = spec_of("OFDM", 2)
        before = self.predict(other)
        self.engine.feedback.add(spec_of("NFM", 1), "WFM")
        after = self.predict(other)
        self.assertIsNone(after.learned)
        self.assertEqual(after.label, before.label)
        self.assertAlmostEqual(after.confidence, before.confidence, places=5)

    def test_corrections_persist_across_restarts(self):
        spec = spec_of("NFM", 5)
        self.engine.feedback.add(spec, "WFM", note="broadcast")
        reloaded = InferenceEngine(models_dir=self.models)
        listing = reloaded.feedback.list()
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["counts"], {"WFM": 1})
        self.assertEqual(reloaded.analyze_spectrogram(spec).label, "WFM")

    def test_custom_labels_work_and_list_as_custom(self):
        spec = spec_of("DMR", 7)
        self.engine.feedback.add(spec, "  Local   trunk  ")
        self.assertEqual(self.predict(spec).label, "Local trunk")
        self.assertEqual(self.engine.feedback.custom_labels(), ["Local trunk"])
        self.assertIn("Local trunk", self.engine.class_names())

    def test_labels_are_normalised_and_validated(self):
        known = ["WFM", "NFM"]
        self.assertEqual(canonical_label("wfm", known), "WFM")
        self.assertEqual(canonical_label("__noise__", known), "Noise")
        for bad in ("", "   ", "x" * 80, "unknown", "bad\x01label"):
            with self.assertRaises(FeedbackError):
                canonical_label(bad, known)

    def test_the_library_gets_a_user_prototype_and_loses_it_on_undo(self):
        spec = spec_of("NFM", 9)
        count = lambda: len(self.engine.library.source_vectors("user")[1])  # noqa: E731
        builtin = len(self.engine.library.source_vectors("builtin")[1])
        item = self.engine.feedback.add(spec, "WFM")
        self.assertEqual(count(), 1)
        self.engine.feedback.remove(item["id"])
        self.assertEqual(count(), 0)
        self.assertEqual(len(self.engine.library.source_vectors("builtin")[1]), builtin)

    def test_removing_by_label_and_clear(self):
        store = self.engine.feedback
        for seed in (1, 2):
            store.add(spec_of("NFM", seed), "WFM")
        store.add(spec_of("DMR", 3), "Mine")
        self.assertEqual(store.remove_label("wfm"), 2)
        self.assertEqual(store.list()["counts"], {"Mine": 1})
        self.assertEqual(store.clear(), 1)
        self.assertEqual(store.list()["total"], 0)

    def test_the_newest_of_two_contradicting_corrections_wins(self):
        spec = spec_of("NFM", 11)
        self.engine.feedback.add(spec, "WFM")
        second = self.engine.feedback.add(spec, "AM")
        self.assertEqual(self.predict(spec).label, "AM")
        self.engine.feedback.remove(second["id"])
        self.assertEqual(self.predict(spec).label, "WFM")

    def test_a_backup_of_the_library_is_kept_before_the_first_write(self):
        self.engine.feedback.add(spec_of("NFM", 1), "WFM")
        backup = self.models / "user_labels" / "backup"
        self.assertTrue((backup / "library.sqlite").is_file())

    def test_export_then_import_into_a_fresh_install(self):
        spec = spec_of("NFM", 13)
        self.engine.feedback.add(spec, "WFM")
        self.engine.feedback.add(spec_of("DMR", 14), "Local trunk")
        blob = self.engine.feedback.export_zip()
        other_root = Path(tempfile.mkdtemp(prefix="sigsense-fb2-"))
        self.addCleanup(shutil.rmtree, other_root, True)
        fresh = InferenceEngine(models_dir=models_copy(other_root))
        self.assertEqual(fresh.feedback.import_zip(blob), 2)
        self.assertEqual(fresh.feedback.list()["counts"], {"WFM": 1, "Local trunk": 1})
        self.assertEqual(fresh.analyze_spectrogram(spec).label, "WFM")
        # importing twice adds nothing
        self.assertEqual(fresh.feedback.import_zip(blob), 0)
        with self.assertRaises(FeedbackError):
            fresh.feedback.import_zip(b"not a zip")

    def test_embeddings_are_recomputed_when_the_model_changes(self):
        spec = spec_of("NFM", 15)
        self.engine.feedback.add(spec, "WFM")
        index = self.models / "user_labels" / "index.json"
        data = json.loads(index.read_text())
        data["model_fp"] = "some-older-weights"
        index.write_text(json.dumps(data))
        reloaded = InferenceEngine(models_dir=self.models)
        self.assertEqual(reloaded.analyze_spectrogram(spec).label, "WFM")
        self.assertNotEqual(json.loads(index.read_text())["model_fp"], "some-older-weights")

    def test_a_correction_is_stored_quickly(self):
        import time

        spec = spec_of("NFM", 17)
        started = time.perf_counter()
        self.engine.feedback.add(spec, "WFM")
        self.assertLess(time.perf_counter() - started, 2.0)


class DecisionTests(unittest.TestCase):
    """The blend itself, without a CNN."""

    def unit(self, v):
        v = np.asarray(v, dtype=np.float32)
        return v / np.linalg.norm(v)

    def test_nothing_learned_means_the_cnn_is_untouched(self):
        learner = learning.UserLearner()
        probs = np.array([0.7, 0.3], dtype=np.float32)
        d = learner.apply(probs, ["A", "B"], self.unit([1, 0, 0]))
        self.assertIsNone(d.learned)
        np.testing.assert_allclose(d.probs, probs)

    def test_a_close_example_overrules_the_cnn_and_a_far_one_does_not(self):
        learner = learning.UserLearner()
        learner.set_prototypes(
            [{"label": "WFM", "embeddings": [self.unit([1, 0.05, 0])], "competitor": None}]
        )
        cnn = np.array([0.8, 0.2], dtype=np.float32)  # says "5G-NR"
        near = learner.apply(cnn, ["5G-NR", "AM"], self.unit([1, 0.1, 0]))
        self.assertEqual(near.labels[int(np.argmax(near.probs))], "WFM")
        far = learner.apply(cnn, ["5G-NR", "AM"], self.unit([0, 1, 0]))
        self.assertEqual(far.labels[int(np.argmax(far.probs))], "5G-NR")
        self.assertIsNone(far.learned)

    def test_the_corrected_from_label_is_pushed_down(self):
        learner = learning.UserLearner()
        learner.set_prototypes(
            [
                {
                    "label": "WFM",
                    "embeddings": [self.unit([1, 0, 0])],
                    "wrong_label": "5G-NR",
                    "competitor": None,
                }
            ]
        )
        d = learner.apply(
            np.array([0.9, 0.1], dtype=np.float32), ["5G-NR", "AM"], self.unit([1, 0, 0])
        )
        by = dict(zip(d.labels, d.probs))
        self.assertLess(by["5G-NR"], 0.02)

    def test_a_crowded_neighbourhood_needs_a_closer_match(self):
        learner = learning.UserLearner()
        base = {"label": "WFM", "embeddings": [self.unit([1, 0, 0])]}
        learner.set_prototypes([{**base, "competitor": None}])
        loose = learner.apply(
            np.array([1.0, 0.0], dtype=np.float32), ["X", "WFM"], self.unit([1, 0.75, 0])
        )
        learner.set_prototypes([{**base, "competitor": 0.97}])
        strict = learner.apply(
            np.array([1.0, 0.0], dtype=np.float32), ["X", "WFM"], self.unit([1, 0.75, 0])
        )
        self.assertGreater(dict(zip(loose.labels, loose.probs))["WFM"],
                           dict(zip(strict.labels, strict.probs))["WFM"])


class TrackerCaptureTests(FeedbackCase):
    def observe(self, tracker, band, spec, frame="f"):
        pred = self.predict(spec)
        return tracker.observe(band_observation(band, pred, frame_id=frame))

    BAND = {
        "center_freq_hz": 93.9e6,
        "bandwidth_hz": 200e3,
        "low_hz": 93.8e6,
        "high_hz": 94.0e6,
        "peak_freq_hz": 93.9e6,
        "power_dbfs": -40.0,
        "snr_db": 30.0,
    }

    def test_a_track_keeps_what_the_model_saw_and_it_stays_out_of_the_track(self):
        tracker = SignalTracker()
        track = self.observe(tracker, self.BAND, spec_of("NFM", 1))
        self.assertNotIn("_capture", track)
        self.assertNotIn("spec", track)
        cap = tracker.capture_for(track["id"])
        self.assertEqual(cap["spec"].shape, (256, 256))
        self.assertEqual(cap["embedding"].shape, (128,))
        json.dumps(track, default=str)  # serialisable

    def test_captures_are_bounded(self):
        tracker = SignalTracker()
        spec = spec_of("NFM", 1)
        for i in range(CAPTURE_MAX_TRACKS + 20):
            band = {**self.BAND, "center_freq_hz": 90e6 + i * 1e6,
                    "low_hz": 90e6 + i * 1e6 - 1e5, "high_hz": 90e6 + i * 1e6 + 1e5}
            self.observe(tracker, band, spec)
        self.assertLessEqual(len(tracker.captures), CAPTURE_MAX_TRACKS)

    def test_pinning_holds_the_label_and_unpinning_releases_it(self):
        tracker = SignalTracker()
        track = self.observe(tracker, self.BAND, spec_of("NFM", 1))
        pinned = tracker.pin_label(track["id"], "WFM")
        self.assertEqual(pinned["label"], "WFM")
        again = self.observe(tracker, self.BAND, spec_of("NFM", 2), frame="g")
        self.assertEqual(again["label"], "WFM")
        self.assertEqual(again["evidence"], "Corrected by you")
        tracker.unpin(track["id"])
        self.assertNotIn("pinned_label", tracker.tracks[track["id"]])


class RouteTests(FeedbackCase):
    def setUp(self):
        super().setUp()
        self.tracker = SignalTracker()
        self.router = build_router(lambda: self.engine, self.tracker, lambda s: [])
        self.routes = {
            (route.path, tuple(sorted(route.methods))): route.endpoint
            for route in self.router.routes
        }

    def call(self, path, method, *args, **kwargs):
        return self.routes[(path, (method,))](*args, **kwargs)

    def track(self, spec):
        band = TrackerCaptureTests.BAND
        pred = self.predict(spec)
        return self.tracker.observe(band_observation(band, pred, frame_id="f"))

    def test_submit_updates_the_track_and_the_next_prediction(self):
        spec = spec_of("NFM", 21)
        track = self.track(spec)
        out = self.call(
            "/classify/feedback", "POST",
            FeedbackRequest(track_id=track["id"], label="WFM", note="x"),
        )
        self.assertEqual(out["track"]["label"], "WFM")
        self.assertEqual(out["prediction"]["label"], "WFM")
        self.assertEqual(out["prediction"]["was"], track["label"])
        self.assertEqual(out["counts"], {"WFM": 1})
        self.assertEqual(self.predict(spec).label, "WFM")

    def test_an_unknown_track_is_a_404_and_a_bad_label_a_400(self):
        with self.assertRaises(HTTPException) as e:
            self.call("/classify/feedback", "POST", FeedbackRequest(track_id="nope", label="WFM"))
        self.assertEqual(e.exception.status_code, 404)
        track = self.track(spec_of("NFM", 22))
        with self.assertRaises(HTTPException) as e:
            self.call("/classify/feedback", "POST", FeedbackRequest(track_id=track["id"], label="unknown"))
        self.assertEqual(e.exception.status_code, 400)

    def test_a_track_without_a_capture_is_a_422(self):
        self.tracker.observe({
            **TrackerCaptureTests.BAND, "device_id": "ble:AA:public", "label": "Bluetooth-LE",
            "confidence": 1.0, "identity_type": "ble_address", "frame_id": "f",
        })
        with self.assertRaises(HTTPException) as e:
            self.call("/classify/feedback", "POST", FeedbackRequest(track_id="ble:AA:public", label="WFM"))
        self.assertEqual(e.exception.status_code, 422)

    def test_delete_releases_the_pin_and_the_label(self):
        spec = spec_of("NFM", 23)
        track = self.track(spec)
        out = self.call("/classify/feedback", "POST", FeedbackRequest(track_id=track["id"], label="WFM"))
        self.call("/classify/feedback/{example_id}", "DELETE", out["item"]["id"])
        self.assertNotIn("pinned_label", self.tracker.tracks[track["id"]])
        self.assertEqual(self.engine.feedback.list()["total"], 0)

    def test_other_tracks_follow_a_correction_immediately(self):
        a = self.track(spec_of("NFM", 24))
        band2 = {**TrackerCaptureTests.BAND, "center_freq_hz": 99e6, "low_hz": 98.9e6, "high_hz": 99.1e6}
        spec_b = spec_of("NFM", 24, snr=14.0)
        b = self.tracker.observe(band_observation(band2, self.predict(spec_b), frame_id="f"))
        self.assertNotEqual(a["id"], b["id"])
        out = self.call("/classify/feedback", "POST", FeedbackRequest(track_id=a["id"], label="WFM"))
        self.assertEqual(self.tracker.tracks[b["id"]]["label"], "WFM")
        # only tracks whose label actually changed are counted
        self.assertEqual(out["refreshed"], 1 if b["label"] != "WFM" else 0)

    def test_list_thumbnail_export_and_import(self):
        track = self.track(spec_of("NFM", 25))
        out = self.call("/classify/feedback", "POST", FeedbackRequest(track_id=track["id"], label="WFM"))
        listing = self.call("/classify/feedback", "GET")
        self.assertEqual(listing["total"], 1)
        png = self.call("/classify/feedback/{example_id}/thumbnail.png", "GET", out["item"]["id"])
        self.assertTrue(png.body.startswith(b"\x89PNG"))
        exported = self.call("/classify/feedback/export", "GET")
        self.assertIn("index.json", zipfile.ZipFile(io.BytesIO(exported.body)).namelist())
        classes = self.call("/classify/classes", "GET")
        self.assertIn("WFM", classes["classes"])
        self.assertEqual(classes["noise_label"], "Noise")
        removed = self.call("/classify/feedback", "DELETE", label="WFM")
        self.assertEqual(removed["removed"], 1)
        with self.assertRaises(HTTPException):
            self.call("/classify/feedback", "DELETE")


if __name__ == "__main__":
    unittest.main()
