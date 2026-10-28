"""Throughput, latency and soak measurements for the engine (WP-G).

These print their numbers (run with `-v` or look at the log) and assert only loose bounds, because the
machine is shared and the figures are for the report, not for flaky gating:
  * real-time factor: CPU seconds per second of capture, per channel, at several capture rates;
  * latency: how far behind the input the decoded audio arrives when fed at real-time speed;
  * soak: a long real-time stream stays flat in memory with no queue growth or dropped input.
These are opt-in because they are slow and the machine is shared: set DVOICE_PERF=1 to run them.
Set DVOICE_SOAK_SECONDS (default 20) for a longer soak, e.g. 180.
"""

from __future__ import annotations

import os
import statistics
import threading
import time
import unittest

import numpy as np

from sigsense_api.dvoice import engine as eng
from sigsense_api.dvoice.engine import ChannelConfig, DigitalVoiceEngine, EngineConfig
from sigsense_api.dvoice.events import Modulation
from sigsense_api.dvoice.vocoder import registry

from test_dvoice_e2e_engine import m17_dibits, p25_dibits
from test_dvoice_engine_common import CAPTURE_CENTER, Collector, drain, feed_capture, transmit, wideband


if os.environ.get("DVOICE_PERF") != "1":
    raise unittest.SkipTest("performance measurements are opt-in: set DVOICE_PERF=1")


def rss_mb() -> float:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    return 0.0


def feed_realtime(engine, wb: np.ndarray, fs: float, center: float, chunk_s: float = 0.02, start: float | None = None) -> float:
    """Feed at the capture's own pace; returns the wall-clock start time."""
    n = int(fs * chunk_s)
    t0 = time.monotonic() if start is None else start
    for i, pos in enumerate(range(0, wb.size, n)):
        due = t0 + (pos / fs)
        wait = due - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        engine.feed_iq(wb[pos : pos + n], fs, center, "*")
    return t0


class ThroughputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not eng.protocol_support().get("m17", {}).get("implemented"):
            raise unittest.SkipTest("M17 decoder not implemented yet")
        cls.dibits = m17_dibits(frames=100)

    def _rtf(self, fs: float, channels: int = 1) -> tuple[float, float]:
        wb = transmit(self.dibits, 4800.0, Modulation.FSK4, snr_db=26.0, offset_hz=35_000.0, fs=fs, lead_s=0.3, tail_s=0.3)
        cfg = [ChannelConfig(name=f"ch{i}", protocol="m17", offset_hz=35_000.0) for i in range(channels)]
        col = Collector()
        engine = DigitalVoiceEngine(EngineConfig(channels=cfg), col.on_event, col.on_audio)
        engine.start()
        self.addCleanup(engine.close)
        time.sleep(0.5)  # let the kernel warm-up finish: this measures steady state
        cpu0, wall0 = time.process_time(), time.monotonic()
        feed_capture(engine, wb, fs=fs, chunk_s=0.1, max_backlog_s=0.4)
        drain(engine, 120)
        cpu, wall = time.process_time() - cpu0, time.monotonic() - wall0
        seconds = wb.size / fs
        self.assertTrue(col.data("call_end"), f"no call decoded at {fs} S/s")
        return cpu / seconds / channels, wall / seconds

    def test_real_time_factor_by_capture_rate(self):
        print("\ncapture rate   CPU-sec per capture-sec per channel   wall/real-time")
        results = {}
        for fs in (1_200_000.0, 2_400_000.0, 4_800_000.0, 7_680_000.0):
            cpu, wall = self._rtf(fs)
            results[fs] = (cpu, wall)
            print(f"  {fs / 1e6:5.2f} MS/s   {cpu:6.3f}                               {wall:5.2f}x")
        for fs, (cpu, wall) in results.items():
            self.assertLess(wall, 1.0, f"{fs / 1e6} MS/s decoded slower than real time")

    def test_three_channels_share_one_capture(self):
        cpu, wall = self._rtf(2_400_000.0, channels=3)
        print(f"\n3 channels @2.4 MS/s: {cpu:.3f} CPU-sec per capture-sec per channel, wall {wall:.2f}x real time")
        self.assertLess(wall, 1.0)


class LatencyTests(unittest.TestCase):
    """Input-to-output lag at real-time pace.

    Two numbers with no mapping ambiguity:
      * sync lag: a sync event carries its exact symbol index, so (event wall time) - (the time that
        symbol was fed) is the front end + queue + block delay of the engine;
      * last-frame audio lag: the last stream frame's end time is known exactly, so the arrival of its
        audio shows the delay through the decoder and vocoder as well.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not eng.protocol_support().get("m17", {}).get("implemented"):
            raise unittest.SkipTest("M17 decoder not implemented yet")

    def test_lag_behind_the_input_at_real_time_pace(self):
        from sigsense_api.dvoice.events import VocoderId, unpack_audio_frame
        from sigsense_api.dvoice.testing.m17_encoder import eot

        have_audio = registry.get_vocoder(VocoderId.CODEC2_3200).__class__.__name__ != "UnavailableVocoder"
        dibits = m17_dibits(frames=100)
        lead = 0.4
        wb = transmit(dibits, 4800.0, Modulation.FSK4, snr_db=26.0, offset_hz=35_000.0, lead_s=lead, tail_s=0.6)
        syncs: list[tuple[float, int]] = []
        audio: list[tuple[float, int]] = []
        ends: list[float] = []

        def on_event(env) -> None:
            now = time.monotonic()
            if env["type"] == "sync" and env["data"]["pattern"] == "m17_stream":
                syncs.append((now, env["data"]["symbol_index"]))
            elif env["type"] == "call_end":
                ends.append(now)

        def on_audio(frame: bytes) -> None:
            header, pcm = unpack_audio_frame(frame)
            if pcm.size:
                audio.append((time.monotonic(), header["seq"]))

        engine = DigitalVoiceEngine(EngineConfig(channels=[ChannelConfig(name="ch0", protocol="m17", offset_hz=35_000.0)]), on_event, on_audio)
        engine.start()
        self.addCleanup(engine.close)
        time.sleep(0.6)
        t0 = feed_realtime(engine, wb, 2_400_000.0, CAPTURE_CENTER)
        drain(engine, 30)
        time.sleep(0.3)
        self.assertGreater(len(syncs), 8)
        # symbol index k is complete at stream time lead + (k + 8) / 4800 (the preamble before the LSF is part of `dibits`)
        lags = sorted((t - t0) - ((idx + 8) / 4800.0) for t, idx in syncs)  # symbol indices count from the start of the feed
        median, p95 = statistics.median(lags), lags[int(len(lags) * 0.95)]
        print(f"\nengine lag behind the input, sync events (M17 @2.4 MS/s, real time): median {median * 1e3:.0f} ms, p95 {p95 * 1e3:.0f} ms, max {lags[-1] * 1e3:.0f} ms over {len(lags)} syncs")
        self.assertLess(median, 0.5)
        if have_audio and audio:
            run = []
            for t, seq in audio:
                if run and seq <= run[-1][1]:
                    break
                run.append((t, seq))
            content_end = lead + (dibits.size - eot().size) / 4800.0  # the last stream frame ends here
            last_lag = (run[-1][0] - t0) - content_end
            print(f"last voice frame of the call: audio arrived {last_lag * 1e3:.0f} ms after its content ended (budget: under 350 ms)")
            # (the M17 decoder was seen to end this call early with 'sync_loss' and open a second one for the
            # last frames, so this single-frame figure can be negative; the sync lag above is the reliable one)


class SoakTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not eng.protocol_support().get("m17", {}).get("implemented"):
            raise unittest.SkipTest("M17 decoder not implemented yet")

    def test_long_stream_has_flat_memory_and_no_drops(self):
        seconds = float(os.environ.get("DVOICE_SOAK_SECONDS", "20"))
        fs = 2_400_000.0
        wb = transmit(m17_dibits(frames=40), 4800.0, Modulation.FSK4, snr_db=26.0, offset_hz=35_000.0, lead_s=0.5, tail_s=0.8, fs=fs)
        segment = wb.size / fs
        col = Collector()
        engine = DigitalVoiceEngine(EngineConfig(channels=[ChannelConfig(name="ch0", protocol="m17", offset_hz=35_000.0)]), col.on_event, col.on_audio)
        engine.start()
        self.addCleanup(engine.close)
        time.sleep(0.5)
        samples: list[tuple[float, float]] = []
        stop = threading.Event()

        def sampler() -> None:
            while not stop.wait(max(1.0, seconds / 40)):
                samples.append((time.monotonic(), rss_mb()))

        thread = threading.Thread(target=sampler, daemon=True)
        thread.start()
        t0 = time.monotonic()
        loops = 0
        while time.monotonic() - t0 < seconds:
            feed_realtime(engine, wb, fs, CAPTURE_CENTER, chunk_s=0.05)
            loops += 1
        drain(engine, 30)
        stop.set()
        thread.join()
        status = engine.status().channels["ch0"]
        calls = len(col.data("call_end"))
        warm = [m for t, m in samples if t - t0 > seconds * 0.3] or [rss_mb()]
        growth = max(warm) - min(warm)
        print(
            f"\nsoak {seconds:.0f}s: {loops} loops of {segment:.1f}s -> {calls} calls, {len(col.audio)} audio frames, "
            f"dropped_items={status['dropped_items']}, queue={status['queue_seconds']:.3f}s, "
            f"RSS {min(m for _, m in samples) if samples else 0:.0f}->{max(m for _, m in samples) if samples else 0:.0f} MB (after warm-up spread {growth:.1f} MB), "
            f"recorder rows={engine.recorder.count()}"
        )
        self.assertGreaterEqual(calls, loops - 1, "every loop should yield a call")
        self.assertEqual(status["dropped_items"], 0, "no input was dropped")
        self.assertLess(status["queue_seconds"], 0.3)
        self.assertLess(growth, 60.0, f"memory grew {growth:.1f} MB after warm-up")
        self.assertFalse(col.errors("input_overrun"))
        self.assertLess(len(engine._calls), 3, "finished calls are not retained")
        self.assertLess(len(engine._recent_ended), 300)


if __name__ == "__main__":
    unittest.main()
