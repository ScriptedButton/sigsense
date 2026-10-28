"""Front-end loopback tests: symbols -> modulator -> impairments -> discriminator/CQPSK -> dibits. Owner: WP-B.

What is checked, and against what:
* BER/SER vs SNR against closed-form theory (FM-discriminator noise integral for 4-level FSK,
  Proakis' DQPSK formula with Marcum Q for CQPSK); the measured rates must lie within a factor
  of the theory, which is the implementation loss (printed as a table in the test log).
* Clean-signal operation (zero errors) for every modulation / symbol rate in the architecture:
  P25 C4FM 4800, DMR-style 4FSK 4800, NXDN48 2400, D-STAR GMSK 4800, CQPSK 4800 and 6000.
* The tolerance list of docs/digital-voice-architecture.md section 6.3: carrier offset +/-1.5 kHz,
  symbol clock +/-100 ppm, polarity inversion, +/-20% deviation, DC offset.
* Block-size independence: the same signal cut at random boundaries gives bit-identical symbols.

SNR is measured in a 12.5 kHz channel (see test_dvoice_dsp_common). NOTE on the contract's
"zero symbol errors at >= 15 dB": no FM-discriminator receiver can do that at 15 dB in a
12.5 kHz channel (theory gives ~3% symbol errors there, which is what we measure, +/- 20%);
zero errors needs roughly 24+ dB. These tests therefore assert agreement with THEORY.
"""

from __future__ import annotations

import unittest

import numpy as np

from sigsense_api.dvoice.dsp.cqpsk import CqpskSymbolRecovery
from sigsense_api.dvoice.dsp.discriminator import FmDiscriminator
from sigsense_api.dvoice.dsp.filters import gaussian_taps, rrc_taps
from sigsense_api.dvoice.dsp.fsk import FskSymbolRecovery
from sigsense_api.dvoice.events import Modulation
from sigsense_api.dvoice.testing.channel import impair
from sigsense_api.dvoice.testing.modulator import align_and_ber, modulate

try:  # unittest discovery puts tests/ on sys.path
    import test_dvoice_dsp_common as C
except ImportError:  # pragma: no cover
    from tests import test_dvoice_dsp_common as C


def _ber(tx, rx, skip=2500):
    return align_and_ber(tx, rx, search=200, skip=skip, length=len(tx) - skip - 300)


class CleanLoopbackTests(unittest.TestCase):
    def _check(self, name, n=20000):
        rate, mod, levels, dev = C.NOMINAL[name]
        tx = C.random_dibits(n, 3, levels)
        iq = C.make_iq(tx, name)
        rx, blk = C.fsk_receive(iq, rate, mod, levels)
        r = _ber(tx, rx)
        self.assertEqual(r["ser"], 0.0, f"{name}: {r}")
        return blk

    def test_p25_c4fm_4800(self):
        blk = self._check("p25_c4fm")
        self.assertAlmostEqual(blk.meta["level"], 1800.0, delta=120.0)  # tracked outer level, Hz

    def test_dmr_4fsk_4800(self):
        blk = self._check("dmr_4fsk")
        self.assertAlmostEqual(blk.meta["level"], 1944.0, delta=130.0)

    def test_nxdn48_2400(self):
        self._check("nxdn48")

    def test_dstar_gmsk_two_level(self):
        rate, mod, levels, dev = C.NOMINAL["dstar_gmsk"]
        tx = C.random_dibits(20000, 4, 2)
        rx, blk = C.fsk_receive(C.make_iq(tx, "dstar_gmsk"), rate, mod, levels)
        self.assertEqual(set(np.unique(rx).tolist()) - {1, 3}, set())  # 2-level uses only dibits 1 and 3
        self.assertEqual(_ber(tx, rx)["ser"], 0.0)

    def test_cqpsk_4800_and_6000(self):
        for rate in (4800.0, 6000.0):
            tx = C.random_dibits(20000, 5)
            rx = CqpskSymbolRecovery(rate).process(modulate(tx, rate, Modulation.CQPSK)).dibits
            self.assertEqual(_ber(tx, rx, 1500)["ser"], 0.0, rate)

    def test_gardner_option_also_decodes_clean(self):
        tx = C.random_dibits(20000, 6)
        iq = C.make_iq(tx, "p25_c4fm")
        rx, _ = C.fsk_receive(iq, 4800.0, Modulation.C4FM, 4, timing="gardner")
        self.assertEqual(_ber(tx, rx)["ser"], 0.0)

    def test_soft_values_have_nominal_levels(self):
        tx = C.random_dibits(8000, 7)
        _, blk = C.fsk_receive(C.make_iq(tx, "p25_c4fm"), 4800.0, Modulation.C4FM, 4)
        rec = FskSymbolRecovery(4800.0)
        blk = rec.process(FmDiscriminator(C.FS).process(C.make_iq(tx, "p25_c4fm")))
        soft = blk.soft[2000:]
        d = blk.dibits[2000:]
        for dib, level in ((1, 3.0), (0, 1.0), (2, -1.0), (3, -3.0)):
            self.assertAlmostEqual(float(np.median(soft[d == dib])), level, delta=0.35, msg=f"dibit {dib}")


class BerVsTheoryTests(unittest.TestCase):
    """Measured error rates against closed-form theory (the implementation loss table)."""

    def test_c4fm_symbol_error_rate_tracks_discriminator_theory(self):
        n = 120000
        tx = C.random_dibits(n, 21)
        iq = C.make_iq(tx, "p25_c4fm")
        lpf = FmDiscriminator(C.FS, bandwidth_hz=C.CHANNEL_BW)._taps
        rx_taps = rrc_taps(10.0, 0.2, 8)
        rows = []
        for snr in (16, 18, 20, 22):
            y = impair(iq, snr, seed=7, snr_bandwidth_hz=C.CHANNEL_BW)
            rx, _ = C.fsk_receive(y, 4800.0, Modulation.C4FM, 4)
            meas = _ber(tx, rx, 3000)["ser"]
            theory = C.fsk4_ser_theory(snr, 1800.0, rx_taps, lpf)
            rows.append((snr, meas, theory))
            # within [0.6x, 3x] of theory (implementation loss <~ 0.6 dB); statistics: >= 100 errors expected
            self.assertLess(meas, 3.0 * theory + 2e-4, (snr, meas, theory))
            self.assertGreater(meas, 0.6 * theory, (snr, meas, theory))
        print("\nC4FM 4800, SNR in 12.5 kHz | measured SER | theory SER | ratio")
        for snr, m, t in rows:
            print(f"   {snr:3d} dB             | {m:11.5f} | {t:10.5f} | {m / t:5.2f}")

    def test_cqpsk_bit_error_rate_tracks_dqpsk_theory(self):
        n = 150000
        tx = C.random_dibits(n, 22)
        rows = []
        for rate in (4800.0, 6000.0):
            iq = modulate(tx, rate, Modulation.CQPSK)
            for snr in (4, 6, 8):
                y = impair(iq, snr, seed=7, snr_bandwidth_hz=C.CHANNEL_BW)
                rx = CqpskSymbolRecovery(rate).process(y).dibits
                meas = _ber(tx, rx, 3000)["ber"]
                theory = C.dqpsk_ber_theory(snr, rate)
                rows.append((rate, snr, meas, theory))
                self.assertLess(meas, 2.5 * theory, (rate, snr, meas, theory))
                self.assertGreater(meas, 0.5 * theory, (rate, snr, meas, theory))
        print("\nCQPSK, SNR in 12.5 kHz | rate | measured BER | theory BER | ratio")
        for rate, snr, m, t in rows:
            print(f"   {snr:3d} dB            | {rate:5.0f} | {m:.5f}      | {t:.5f}    | {m / t:4.2f}")

    def test_gmsk_error_rate_is_sane(self):
        """GMSK (BT 0.5) has inherent ISI, so only a loose check: error-free at 26 dB, <25% at 14 dB."""
        rate, mod, levels, _ = C.NOMINAL["dstar_gmsk"]
        tx = C.random_dibits(60000, 23, 2)
        iq = C.make_iq(tx, "dstar_gmsk")
        for snr, lo_hi in ((26, (0.0, 0.001)), (14, (0.0, 0.25))):
            y = impair(iq, snr, seed=7, snr_bandwidth_hz=C.CHANNEL_BW)
            rx, _ = C.fsk_receive(y, rate, mod, levels)
            ser = _ber(tx, rx, 3000)["ser"]
            self.assertLessEqual(ser, lo_hi[1], (snr, ser))

    def test_operates_at_12_to_15_db(self):
        """The doc's '12-15 dB operation' for 4-level protocols: it must hold lock and decode with a
        bounded error rate (FEC territory) rather than fail outright."""
        tx = C.random_dibits(60000, 24)
        for name, ceiling in (("p25_c4fm", 0.20), ("dmr_4fsk", 0.20)):
            rate, mod, levels, _ = C.NOMINAL[name]
            iq = C.make_iq(tx, name, snr_db=15.0)
            rx, _ = C.fsk_receive(iq, rate, mod, levels)
            self.assertLess(_ber(tx, rx, 3000)["ser"], ceiling, name)
        iq = impair(modulate(tx, 4800.0, Modulation.CQPSK), 12.0, seed=2, snr_bandwidth_hz=C.CHANNEL_BW)
        rx = CqpskSymbolRecovery(4800.0).process(iq).dibits
        self.assertLess(_ber(tx, rx, 3000)["ser"], 0.01)  # CQPSK locks at 12 dB (zero errors in practice)


class ToleranceTests(unittest.TestCase):
    """Section 6.3 tolerances at a healthy 26 dB SNR, so any error is a front-end defect, not noise."""

    SNR = 26.0
    N = 40000

    def _fsk(self, name="p25_c4fm", dev_scale=1.0, afc=False, **imp):
        rate, mod, levels, dev = C.NOMINAL[name]
        tx = C.random_dibits(self.N, 31, levels)
        iq = modulate(tx, rate, mod, C.FS, dev * dev_scale)
        y = impair(iq, self.SNR, seed=5, snr_bandwidth_hz=C.CHANNEL_BW, **imp)
        if afc:
            rx, corr = C.fsk_receive_with_afc(y, rate, mod, levels)
        else:
            rx, _ = C.fsk_receive(y, rate, mod, levels)
        return _ber(tx, rx, 4000)

    def test_symbol_clock_plus_minus_100_ppm(self):
        for ppm in (100.0, -100.0, 300.0, -300.0):
            self.assertLess(self._fsk(timing_ppm=ppm)["ser"], 5e-4, ppm)

    def test_polarity_inversion_is_reported_by_alignment(self):
        r = self._fsk(invert=True)
        self.assertTrue(r["inverted"])
        self.assertLess(r["ser"], 5e-4)

    def test_deviation_error_plus_minus_20_percent(self):
        for scale in (0.8, 1.2):
            self.assertLess(self._fsk(dev_scale=scale)["ser"], 1e-3, scale)

    def test_dc_offset(self):
        # a 5% (-26 dB) complex DC term, the size of a typical SDR leakage spike
        self.assertLess(self._fsk(dc_offset=0.05 + 0.03j)["ser"], 1e-3)

    def test_carrier_offset_pm_1500_hz_with_afc(self):
        """Closed loop: the slicer's tracked centre retunes the channelizer (FrequencyTracker)."""
        for off in (1500.0, -1500.0, 800.0):
            r = self._fsk(afc=True, freq_offset_hz=off)
            self.assertLess(r["ser"], 5e-4, off)

    def test_carrier_offset_open_loop_small(self):
        """Without AFC the slicer centre alone absorbs a few hundred Hz."""
        self.assertLess(self._fsk(freq_offset_hz=400.0)["ser"], 5e-4)

    def test_cqpsk_capture_range_and_clock(self):
        for rate in (4800.0, 6000.0):
            tx = C.random_dibits(self.N, 32)
            iq = modulate(tx, rate, Modulation.CQPSK)
            for kw in (
                dict(freq_offset_hz=1500.0),
                dict(freq_offset_hz=-1500.0),
                dict(freq_offset_hz=-1200.0, timing_ppm=100.0),
                dict(timing_ppm=-100.0),
                dict(invert=True),
                dict(dc_offset=0.05 + 0.03j),
            ):
                y = impair(iq, self.SNR, seed=5, snr_bandwidth_hz=C.CHANNEL_BW, **kw)
                rec = CqpskSymbolRecovery(rate)
                r = _ber(tx, rec.process(y).dibits, 3000)
                self.assertLess(r["ser"], 5e-4, (rate, kw, r))
                if kw.get("invert"):
                    self.assertTrue(r["inverted"])


class SyncRecalibrationTests(unittest.TestCase):
    def test_recalibrate_at_restores_corrupted_levels_from_buffered_raw_values(self):
        """Corrupt the slicer (as a bad burst start would) and re-fit it from a recognised sync word."""
        from sigsense_api.dvoice.dsp.slicer import ST_A, ST_C
        from sigsense_api.dvoice.sync.patterns import SYNC_SPECS

        word = np.array(SYNC_SPECS["p25p1"].values, dtype=np.uint8)
        tx = C.random_dibits(2500, 3)
        tx[1000:1024] = word
        iq = modulate(tx, 4800.0, Modulation.C4FM, C.FS, 1800.0)
        iq = (iq * np.exp(2j * np.pi * 300.0 * np.arange(len(iq)) / C.FS)).astype(np.complex64)  # 300 Hz carrier offset
        disc, rec = FmDiscriminator(C.FS), FskSymbolRecovery(4800.0)
        blk = rec.process(disc.process(iq))
        lag = align_and_ber(tx, blk.dibits, search=60, skip=300, length=1500)["lag"]
        self.assertEqual(blk.first_index, 0)
        a_true = float(rec._sl[ST_A])
        self.assertAlmostEqual(a_true, 1800.0, delta=90.0)
        # reported in true Hz (the moment estimator averages ~250 symbols, so it jitters by ~+/-40 Hz)
        self.assertAlmostEqual(rec.freq_error_hz, 300.0, delta=70.0)
        self.assertEqual(blk.meta["freq_error_hz"], rec.freq_error_hz)
        rec._sl[ST_C], rec._sl[ST_A] = -400.0, 2600.0  # badly wrong
        self.assertTrue(rec.recalibrate_at(1000 + lag, word))
        self.assertAlmostEqual(float(rec._sl[ST_A]), 1800.0, delta=60.0)
        self.assertAlmostEqual(rec.freq_error_hz, 300.0, delta=40.0)  # exact fit from 24 symbols
        # a word that is no longer buffered or carries no level information is refused
        self.assertFalse(rec.recalibrate_at(10**9, word))
        self.assertFalse(rec.recalibrate_at(1000 + lag, np.ones(24, np.uint8)))

    def test_recalibrate_from_sync_with_unchanged_state_is_a_noop_fit(self):
        """The soft-value variant is correct when the slicer state has not moved since the word."""
        from sigsense_api.dvoice.dsp.slicer import ST_A, ST_C

        tx = C.random_dibits(600, 4)
        word = np.array([1, 1, 3, 3, 1, 3, 3, 1, 1, 3, 1, 3], dtype=np.uint8)
        tx[500:512] = word
        disc, rec = FmDiscriminator(C.FS), FskSymbolRecovery(4800.0)
        blk = rec.process(disc.process(modulate(tx, 4800.0, Modulation.C4FM, C.FS, 1800.0)))
        lag = align_and_ber(tx, blk.dibits, search=60, skip=150, length=300)["lag"]
        a_before, c_before = float(rec._sl[ST_A]), float(rec._sl[ST_C])
        rec.recalibrate_from_sync(blk.soft[500 + lag : 512 + lag], word)
        self.assertAlmostEqual(float(rec._sl[ST_A]), a_before, delta=0.12 * a_before)
        self.assertAlmostEqual(float(rec._sl[ST_C]), c_before, delta=0.12 * a_before / 3)


class ChunkInvarianceTests(unittest.TestCase):
    def test_fsk_symbols_identical_for_any_block_boundaries(self):
        tx = C.random_dibits(6000, 41)
        iq = impair(C.make_iq(tx, "p25_c4fm"), 20.0, 300.0, 40.0, seed=3, snr_bandwidth_hz=C.CHANNEL_BW)
        disc = FmDiscriminator(C.FS, bandwidth_hz=C.CHANNEL_BW).process(iq)
        ref_d, ref_s = C.process_in_blocks(FskSymbolRecovery(4800.0), disc, [len(disc)])
        rng = np.random.default_rng(0)
        for _ in range(4):
            sizes = rng.integers(1, 3000, 64)
            d, s = C.process_in_blocks(FskSymbolRecovery(4800.0), disc, sizes)
            np.testing.assert_array_equal(d, ref_d)
            np.testing.assert_array_equal(s, ref_s)  # bit-identical soft values, not just dibits
        # and one-sample-at-a-time
        d1, s1 = C.process_in_blocks(FskSymbolRecovery(4800.0), disc[:2500], [1])
        d2, s2 = C.process_in_blocks(FskSymbolRecovery(4800.0), disc[:2500], [2500])
        np.testing.assert_array_equal(d1, d2)
        np.testing.assert_array_equal(s1, s2)

    def test_cqpsk_symbols_identical_for_any_block_boundaries(self):
        tx = C.random_dibits(5000, 42)
        iq = impair(modulate(tx, 6000.0, Modulation.CQPSK), 18.0, -700.0, 30.0, seed=3, snr_bandwidth_hz=C.CHANNEL_BW)
        ref_d, ref_s = C.process_in_blocks(CqpskSymbolRecovery(6000.0), iq, [len(iq)])
        rng = np.random.default_rng(1)
        for _ in range(4):
            d, s = C.process_in_blocks(CqpskSymbolRecovery(6000.0), iq, rng.integers(1, 2500, 64))
            np.testing.assert_array_equal(d, ref_d)
            np.testing.assert_array_equal(s, ref_s)

    def test_discriminator_is_block_invariant(self):
        iq = (np.random.default_rng(2).standard_normal(5000) + 1j * np.random.default_rng(3).standard_normal(5000)).astype(np.complex64)
        for bw in (None, 12500.0):
            whole = FmDiscriminator(C.FS, bandwidth_hz=bw).process(iq)
            d = FmDiscriminator(C.FS, bandwidth_hz=bw)
            parts = np.concatenate([d.process(iq[i : i + 777]) for i in range(0, 5000, 777)])
            np.testing.assert_allclose(parts, whole, rtol=0, atol=1e-3)  # Hz; float32 output, same math

    def test_slicer_reset_gives_identical_output(self):
        tx = C.random_dibits(3000, 43)
        disc = FmDiscriminator(C.FS).process(C.make_iq(tx, "p25_c4fm"))
        rec = FskSymbolRecovery(4800.0)
        a = rec.process(disc)
        rec.reset()
        b = rec.process(disc)
        np.testing.assert_array_equal(a.dibits, b.dibits)


if __name__ == "__main__":
    unittest.main()
