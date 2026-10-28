"""Speed of the front end per channel (real-time factor = CPU seconds per second of signal). Owner: WP-B.

The architecture budget is <= 0.15 of one core per channel. Thresholds here are deliberately
generous (shared CI machines) so they catch order-of-magnitude regressions; the measured numbers are
printed for the report. Numba compilation happens before timing (warm-up call)."""

from __future__ import annotations

import time
import unittest

import numpy as np

from sigsense_api.dvoice.dsp._jit import HAVE_NUMBA
from sigsense_api.dvoice.dsp.autodetect import AutoDetector
from sigsense_api.dvoice.dsp.channel import Channelizer
from sigsense_api.dvoice.dsp.cqpsk import CqpskSymbolRecovery
from sigsense_api.dvoice.dsp.discriminator import FmDiscriminator
from sigsense_api.dvoice.dsp.fsk import FskSymbolRecovery
from sigsense_api.dvoice.events import Modulation
from sigsense_api.dvoice.sync.correlator import SyncCorrelator
from sigsense_api.dvoice.sync.patterns import SYNC_SPECS
from sigsense_api.dvoice.testing.channel import impair
from sigsense_api.dvoice.testing.modulator import modulate

SECONDS = 4.0


def timed(fn, *args, repeat=3):
    fn(*args)  # warm-up / compile
    best = float("inf")
    for _ in range(repeat):
        t = time.perf_counter()
        fn(*args)
        best = min(best, time.perf_counter() - t)
    return best


@unittest.skipUnless(HAVE_NUMBA, "numba not available: pure-Python kernels are not real time")
class RealTimeFactorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = np.random.default_rng(1)
        n4800 = int(4800 * SECONDS)
        cls.d4800 = rng.integers(0, 4, n4800).astype(np.uint8)
        cls.iq_c4fm = impair(modulate(cls.d4800, 4800.0, Modulation.C4FM), 22.0, 300.0, seed=1, snr_bandwidth_hz=12500.0)
        cls.iq_cqpsk4800 = impair(modulate(cls.d4800, 4800.0, Modulation.CQPSK), 20.0, -400.0, seed=1, snr_bandwidth_hz=12500.0)
        d6000 = rng.integers(0, 4, int(6000 * SECONDS)).astype(np.uint8)
        cls.iq_cqpsk6000 = impair(modulate(d6000, 6000.0, Modulation.CQPSK), 20.0, 0.0, seed=1, snr_bandwidth_hz=12500.0)
        cls.rows: list[tuple[str, float]] = []

    @classmethod
    def tearDownClass(cls):
        print("\nreal-time factor per channel (fraction of one core), best of 3")
        for name, rtf in cls.rows:
            print(f"   {name:48s} {rtf:7.4f}")

    def _rtf(self, name, seconds_of_signal, fn, *args):
        rtf = timed(fn, *args) / seconds_of_signal
        self.rows.append((name, rtf))
        return rtf

    def test_fsk_path_4800(self):
        def run():
            disc = FmDiscriminator(48000.0, bandwidth_hz=12500.0)
            FskSymbolRecovery(4800.0).process(disc.process(self.iq_c4fm))

        self.assertLess(self._rtf("FM discriminator + C4FM recovery, 4800 baud", SECONDS, run), 0.15)

    def test_cqpsk_paths(self):
        self.assertLess(self._rtf("CQPSK recovery, 4800 baud", SECONDS, lambda: CqpskSymbolRecovery(4800.0).process(self.iq_cqpsk4800)), 0.15)
        self.assertLess(self._rtf("CQPSK recovery, 6000 baud (P25 Phase 2)", SECONDS, lambda: CqpskSymbolRecovery(6000.0).process(self.iq_cqpsk6000)), 0.15)

    def test_sync_correlator_all_specs(self):
        def run():
            SyncCorrelator(SYNC_SPECS.values()).scan(self.d4800, 0)

        self.assertLess(self._rtf("SyncCorrelator, all 14 specs x 2 polarities", SECONDS, run), 0.15)

    def test_autodetector_five_chains(self):
        def run():
            AutoDetector().feed(self.iq_c4fm)

        self.assertLess(self._rtf("AutoDetector (5 candidate chains + correlators)", SECONDS, run), 0.15)

    def test_channelizer_by_capture_rate(self):
        limits = {2_400_000.0: 0.15, 4_000_000.0: 0.15, 10_000_000.0: 0.35, 20_000_000.0: 0.70}
        for fs, limit in limits.items():
            x = (np.random.default_rng(2).standard_normal(int(fs * 0.5)) + 1j * np.random.default_rng(3).standard_normal(int(fs * 0.5))).astype(np.complex64)
            ch = Channelizer(fs, 0.21 * fs, 12500.0)
            rtf = self._rtf(f"Channelizer {fs / 1e6:g} MS/s -> 48 kHz", 0.5, lambda: ch.process(x))
            self.assertLess(rtf, limit, fs)


if __name__ == "__main__":
    unittest.main()
