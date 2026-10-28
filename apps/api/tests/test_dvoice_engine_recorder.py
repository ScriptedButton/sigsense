"""CallRecorder: WAV + sidecar + sqlite call log, retention, safety (WP-G)."""

from __future__ import annotations

import csv
import io
import json
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

import numpy as np

from sigsense_api.dvoice.events import CallKind, EncryptionState, Protocol
from sigsense_api.dvoice.recorder import CallRecorder, safe_name

from test_dvoice_engine_common import make_call


def pcm(seconds: float = 1.0, freq: float = 440.0) -> np.ndarray:
    t = np.arange(int(8000 * seconds)) / 8000.0
    return (6000 * np.sin(2 * np.pi * freq * t)).astype(np.int16)


class RecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def record(self, rec: CallRecorder, call_id: str, seconds: float = 1.0, **kw):
        call = make_call(call_id, **kw)
        rec.on_call(call)
        audio = pcm(seconds)
        for i in range(0, audio.size, 160):
            rec.on_voice(call_id, audio[i : i + 160])
        call.ended_at = time.time()
        call.end_reason = "terminator"
        call.voice_frames = audio.size // 160
        rec.on_call(call)
        return call

    def test_wav_roundtrip_and_sidecar(self):
        rec = CallRecorder(self.root)
        self.addCleanup(rec.close)
        self.record(rec, "dmr-ch0-0-1", 2.0, target=42)
        path = rec.wav_path("dmr-ch0-0-1")
        self.assertIsNotNone(path)
        with wave.open(str(path)) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()), (1, 2, 8000, 16000))
            self.assertTrue(np.array_equal(np.frombuffer(w.readframes(160), dtype="<i2"), pcm(2.0)[:160]))
        sidecar = json.loads(path.with_suffix(".json").read_text())
        self.assertEqual(sidecar["target_id"], 42)
        self.assertTrue(sidecar["audio_file"].endswith(".wav"))
        self.assertTrue(rec.list_calls()[0]["has_audio"])

    def test_log_only_mode_writes_no_audio(self):
        rec = CallRecorder(self.root, record_audio=False)
        self.addCleanup(rec.close)
        self.record(rec, "x-1")
        self.assertIsNone(rec.wav_path("x-1"))
        self.assertEqual(list(self.root.rglob("*.wav")), [])
        self.assertTrue((self.root / "index.sqlite").exists())
        self.assertEqual(len(rec.list_calls()), 1)

    def test_memory_mode_touches_no_disk(self):
        rec = CallRecorder(None)
        self.addCleanup(rec.close)
        self.record(rec, "mem-1")
        self.assertEqual(len(rec.list_calls()), 1)
        self.assertIsNone(rec.wav_path("mem-1"))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_memory_log_is_bounded(self):
        rec = CallRecorder(None, max_memory_calls=20)
        self.addCleanup(rec.close)
        for i in range(60):
            call = make_call(f"c-{i}")
            call.started_at = 1000.0 + i
            rec.on_call(call)
        self.assertLessEqual(rec.count(), 20)
        self.assertEqual(rec.list_calls(1)[0]["call_id"], "c-59")  # newest kept

    def test_persistence_across_reopen(self):
        rec = CallRecorder(self.root)
        self.record(rec, "persist-1", 0.5)
        rec.close()
        again = CallRecorder(self.root)
        self.addCleanup(again.close)
        self.assertEqual(again.list_calls()[0]["call_id"], "persist-1")
        self.assertIsNotNone(again.wav_path("persist-1"))

    def test_filters_pagination_and_counts(self):
        rec = CallRecorder(None)
        self.addCleanup(rec.close)
        for i in range(12):
            call = make_call(f"f-{i}", target=100 + i % 3, source=500 + i)
            call.started_at = 2000.0 + i
            call.protocol = Protocol.DMR if i % 2 else Protocol.P25P1
            if i == 5:
                call.encryption.state = EncryptionState.ENCRYPTED
            rec.on_call(call)
        self.assertEqual(rec.count(), 12)
        self.assertEqual(rec.count(talkgroup=100), 4)
        self.assertEqual(rec.count(protocol="dmr"), 6)
        self.assertEqual(rec.count(encrypted=True), 1)
        self.assertEqual(rec.count(source=505), 1)
        page1 = [c["call_id"] for c in rec.list_calls(5, offset=0)]
        page2 = [c["call_id"] for c in rec.list_calls(5, offset=5)]
        self.assertEqual(page1, ["f-11", "f-10", "f-9", "f-8", "f-7"])
        self.assertEqual(page2, ["f-6", "f-5", "f-4", "f-3", "f-2"])
        self.assertEqual([c["call_id"] for c in rec.list_calls(100, since=2010.0)], ["f-11", "f-10"])

    def test_retention_deletes_the_oldest_audio_first(self):
        rec = CallRecorder(self.root, max_bytes=60_000)  # about 3.7 s of 8 kHz audio
        self.addCleanup(rec.close)
        for i in range(4):
            call = make_call(f"old-{i}")
            call.started_at = 3000.0 + i
            rec.on_call(call)
            rec.on_voice(call.call_id, pcm(2.0))
            call.ended_at = 3000.0 + i + 2
            rec.on_call(call)
        kept = [i for i in range(4) if rec.wav_path(f"old-{i}") is not None]
        self.assertEqual(kept, [3], "only the newest recording fits the cap")
        self.assertEqual(rec.count(), 4, "metadata is kept even when audio is trimmed")
        self.assertFalse(rec.list_calls(10)[-1]["has_audio"])

    def test_transcript_is_attached_and_exported(self):
        rec = CallRecorder(self.root)
        self.addCleanup(rec.close)
        self.record(rec, "tx-1", 0.5)
        rec.on_transcript("tx-1", "unit twelve responding")
        rec.on_transcript("tx-1", "ten four")
        self.assertEqual(rec.get_call("tx-1")["transcript"], "unit twelve responding ten four")
        sidecar = json.loads(rec.wav_path("tx-1").with_suffix(".json").read_text())
        self.assertIn("ten four", sidecar["transcript"])
        rows = list(csv.DictReader(io.StringIO(rec.export("csv").decode())))
        self.assertEqual(rows[0]["transcript"], "unit twelve responding ten four")
        self.assertEqual(rows[0]["has_audio"], "1")

    def test_export_formats(self):
        rec = CallRecorder(None)
        self.addCleanup(rec.close)
        call = make_call('weird,"id"\n1')
        call.meta["talkgroup_label"] = 'Fire, "North"'
        rec.on_call(call)
        rows = list(csv.reader(io.StringIO(rec.export("csv").decode())))
        self.assertEqual(rows[1][0], 'weird,"id"\n1')
        self.assertEqual(rows[1][5], 'Fire, "North"')
        self.assertEqual(json.loads(rec.export("json"))[0]["target_id"], 100)
        with self.assertRaises(ValueError):
            rec.export("xml")

    def test_delete_removes_files_and_row(self):
        rec = CallRecorder(self.root)
        self.addCleanup(rec.close)
        self.record(rec, "del-1", 0.5)
        path = rec.wav_path("del-1")
        self.assertTrue(rec.delete_call("del-1"))
        self.assertFalse(path.exists())
        self.assertFalse(path.with_suffix(".json").exists())
        self.assertFalse(rec.delete_call("del-1"))
        self.assertIsNone(rec.get_call("del-1"))

    def test_hostile_call_ids_stay_inside_the_root(self):
        rec = CallRecorder(self.root)
        self.addCleanup(rec.close)
        for cid in ("../../escape", "..", "a/b/c", "x\\y", "\x00bad", "." * 5, "é" * 200):
            call = make_call(cid)
            rec.on_call(call)
            rec.on_voice(cid, pcm(0.1))
            call.ended_at = time.time()
            rec.on_call(call)
        for path in self.root.rglob("*"):
            resolved = path.resolve()
            self.assertTrue(resolved == self.root.resolve() or self.root.resolve() in resolved.parents, f"{path} escaped the root")
        self.assertFalse((self.root.parent / "escape.wav").exists())
        for hostile in ("../../x", "..", "a/b", "x\\y", "\x00z"):
            name = safe_name(hostile)
            self.assertNotRegex(name, r"[/\\\x00]")
            self.assertFalse(name.startswith("."), name)
            self.assertTrue(name)
        self.assertLessEqual(len(safe_name("é" * 500)), 120)

    def test_wav_path_never_serves_outside_the_root(self):
        rec = CallRecorder(self.root)
        self.addCleanup(rec.close)
        self.record(rec, "ok-1", 0.2)
        rec._db.execute("UPDATE calls SET audio_path = ? WHERE call_id = ?", ("../../../etc/passwd", "ok-1"))
        self.assertIsNone(rec.wav_path("ok-1"))

    def test_audio_for_an_unfinished_call_is_not_served(self):
        rec = CallRecorder(self.root)
        self.addCleanup(rec.close)
        call = make_call("open-1")
        rec.on_call(call)
        rec.on_voice("open-1", pcm(0.3))
        self.assertIsNone(rec.wav_path("open-1"), "still being written")
        call.ended_at = time.time()
        rec.on_call(call)
        self.assertIsNotNone(rec.wav_path("open-1"))

    def test_close_finalises_open_wavs(self):
        rec = CallRecorder(self.root)
        call = make_call("crash-1")
        rec.on_call(call)
        rec.on_voice("crash-1", pcm(0.5))
        rec.close()
        path = next(self.root.rglob("crash-1.wav"))
        with wave.open(str(path)) as w:
            self.assertEqual(w.getnframes(), 4000)
        rec.close()  # twice is fine
        rec.on_call(call)  # and writes after close are ignored, not errors

    def test_late_encryption_deletes_audio_already_written(self):
        rec = CallRecorder(self.root)
        self.addCleanup(rec.close)
        call = make_call("late-1")
        rec.on_call(call)
        rec.on_voice("late-1", pcm(0.3))
        call.encryption.state = EncryptionState.ENCRYPTED
        rec.on_call(call)
        rec.on_voice("late-1", pcm(0.3))  # ignored from here on
        call.ended_at = time.time()
        rec.on_call(call)
        self.assertEqual(list(self.root.rglob("late-1*")), [])
        self.assertIsNone(rec.wav_path("late-1"))
        self.assertFalse(rec.get_call("late-1")["has_audio"])

    def test_thread_safety_under_concurrent_calls(self):
        rec = CallRecorder(self.root)
        self.addCleanup(rec.close)
        errors = []

        def worker(n: int) -> None:
            try:
                for k in range(6):
                    self.record(rec, f"t{n}-{k}", 0.2)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(rec.count(), 36)
        self.assertEqual(len(list(self.root.rglob("*.wav"))), 36)


if __name__ == "__main__":
    unittest.main()
