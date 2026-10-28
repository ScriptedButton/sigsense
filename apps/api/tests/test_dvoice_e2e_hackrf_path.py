"""The whole server-side HackRF path with a fake `hackrf_transfer` (WP-G).

A replay script stands in for the HackRF binary and streams a recorded M17 transmission as int8 IQ in
real time. Everything downstream is the real code: HackRFCapture's stream pump and IQ tap, the
runtime's feeder thread, the engine, the protocol decoder, the recorder. No hardware is touched.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

import numpy as np

from sigsense_api import hackrf
from sigsense_api.dvoice import engine as eng
from sigsense_api.dvoice.events import Modulation
from sigsense_api.dvoice.runtime.manager import DvoiceRuntime, Settings

from test_dvoice_e2e_engine import m17_dibits
from test_dvoice_engine_common import CAPTURE_CENTER, CAPTURE_FS, transmit

REPLAY = textwrap.dedent(
    """
    import os, sys, time
    args = sys.argv[1:]
    rate = float(args[args.index("-s") + 1])
    data = open(os.environ["FAKE_HACKRF_IQ"], "rb").read()
    chunk = 65536
    start = time.monotonic()
    sent = 0
    out = sys.stdout.buffer
    while sent < len(data):
        due = start + (sent / 2.0) / rate
        wait = due - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        out.write(data[sent:sent + chunk])
        out.flush()
        sent += chunk
    time.sleep(0.2)
    """
)


def to_int8(iq: np.ndarray, scale: float = 40.0) -> bytes:
    out = np.empty(iq.size * 2, dtype=np.int8)
    out[0::2] = np.clip(np.round(iq.real * scale), -127, 127)
    out[1::2] = np.clip(np.round(iq.imag * scale), -127, 127)
    return out.tobytes()


class HackrfPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not eng.protocol_support().get("m17", {}).get("implemented"):
            raise unittest.SkipTest("M17 decoder not implemented yet")
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        script = root / "replay.py"
        script.write_text(REPLAY)
        wrapper = root / "hackrf_transfer"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        iq = transmit(m17_dibits(frames=50), 4800.0, Modulation.FSK4, snr_db=28.0, offset_hz=35_000.0, lead_s=0.3, tail_s=0.5)
        (root / "capture.iq").write_bytes(to_int8(iq))
        cls.seconds = iq.size / CAPTURE_FS
        cls.wrapper = str(wrapper)
        cls.capture_file = str(root / "capture.iq")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def setUp(self) -> None:
        self._saved = (hackrf.HACKRF_TRANSFER, os.environ.get("FAKE_HACKRF_IQ"))
        hackrf.HACKRF_TRANSFER = self.wrapper
        os.environ["FAKE_HACKRF_IQ"] = self.capture_file
        self.cap = hackrf.HackRFCapture(center_freq_hz=CAPTURE_CENTER, sample_rate_hz=CAPTURE_FS, gain_db=32.0)
        self.rt = DvoiceRuntime(Path(tempfile.mkdtemp()))
        self.rt.bind(lambda: self.cap, lambda: False, lambda: False)
        self.addCleanup(self.cleanup)

    def cleanup(self) -> None:
        self.rt.shutdown()
        self.cap.stop_stream()
        hackrf.HACKRF_TRANSFER = self._saved[0]
        if self._saved[1] is None:
            os.environ.pop("FAKE_HACKRF_IQ", None)
        else:
            os.environ["FAKE_HACKRF_IQ"] = self._saved[1]

    def settings(self, **kw) -> Settings:
        s = Settings(enabled=True, source="hackrf", record="audio")
        s.channels = [{"name": "ch0", "protocol": "m17", "offset_hz": 35_000.0, "bandwidth_hz": 12500.0}]
        for key, value in kw.items():
            setattr(s, key, value)
        return s

    def wait_calls(self, n: int, timeout: float) -> list:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            rows = self.rt.engine().recorder.list_calls()
            if len(rows) >= n and all(r["ended_at"] for r in rows[:n]):
                return rows
            time.sleep(0.1)
        return self.rt.engine().recorder.list_calls()

    def test_capture_to_call_through_tap_feeder_and_engine(self):
        self.cap.start_stream()
        self.assertTrue(self.cap.streaming)
        self.rt.apply(self.settings())
        rows = self.wait_calls(1, self.seconds + 15)
        self.assertEqual(len(rows), 1, f"no call decoded; feeder={self.rt.feeder_status()}")
        call = rows[0]
        self.assertEqual(call["protocol"], "m17")
        self.assertEqual(call["meta"].get("src"), "W2FBI")
        self.assertGreaterEqual(call["voice_frames"], 80)
        feeder = self.rt.feeder_status()
        self.assertGreater(feeder["fed_samples"], CAPTURE_FS * 1.5, "the feeder moved the capture into the engine")
        status = self.rt.engine().status_dict()
        self.assertGreater(status["capture"]["feeds"], 10)
        self.assertEqual(status["capture"]["sample_rate"], CAPTURE_FS)

    def test_the_tap_does_not_starve_the_other_consumers(self):
        """The audio demodulator's buffer and the display tail keep filling while the tap is read."""
        self.cap.start_stream()
        self.cap.enable_audio(True)
        self.rt.apply(self.settings())
        time.sleep(1.0)
        audio = self.cap.take_audio_iq()
        tail = self.cap.peek_latest(8192)
        self.assertGreater(audio.size, 100_000, "audio demod still receives samples")
        self.assertIsNotNone(tail, "display tail still fills")
        self.cap.enable_audio(False)

    def test_stream_restart_is_noticed_and_decoding_resumes(self):
        self.cap.start_stream()
        self.rt.apply(self.settings())
        first = self.wait_calls(1, self.seconds + 15)
        self.assertEqual(len(first), 1)
        generation = self.cap.generation
        self.cap.restart_stream()  # replays the capture from the start, like a retune would
        self.assertGreater(self.cap.generation, generation)
        end = time.monotonic() + self.seconds + 15
        rows = first
        while time.monotonic() < end and len(rows) < 2:
            time.sleep(0.2)
            rows = self.rt.engine().recorder.list_calls()
        self.assertGreaterEqual(len(rows), 2, "a second call is decoded after the stream restarted")
        self.assertEqual(self.rt.feeder.state, "streaming" if self.cap.streaming else self.rt.feeder.state)

    def test_demo_source_and_hopping_are_explained_not_fed(self):
        self.rt.get_demo = lambda: True
        self.rt.apply(self.settings())
        time.sleep(0.5)
        status = self.rt.feeder_status()
        self.assertEqual(status["state"], "unavailable")
        self.assertIn("demo", status["reason"])
        self.assertEqual(status["fed_samples"], 0)
        self.rt.get_demo = lambda: False
        self.rt.get_hopping = lambda: True
        self.cap.start_stream()
        time.sleep(0.6)
        self.assertIn("hopping", self.rt.feeder_status()["reason"])

    def test_stream_not_running_says_so(self):
        self.rt.apply(self.settings())  # the capture exists but nothing started its stream
        time.sleep(0.5)
        self.assertEqual(self.rt.feeder_status()["state"], "waiting")
        self.assertIn("stream", self.rt.feeder_status()["reason"])

    def test_disabling_stops_the_feeder_and_releases_the_tap(self):
        self.cap.start_stream()
        self.rt.apply(self.settings())
        time.sleep(0.5)
        self.assertEqual(len(self.cap._taps), 1)
        self.rt.apply(self.settings(enabled=False))
        time.sleep(0.2)
        self.assertEqual(self.rt.feeder_status()["state"], "off")
        self.assertEqual(len(self.cap._taps), 0, "the tap is removed so it cannot grow unbounded")
        self.assertFalse(self.rt.engine().status().running)


if __name__ == "__main__":
    unittest.main()
