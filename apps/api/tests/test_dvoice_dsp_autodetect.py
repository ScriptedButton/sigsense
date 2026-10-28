"""AutoDetector: pick the protocol/modulation from sync hits on a short buffer. Owner: WP-B.

Synthetic streams: random payload with the protocol's real sync word every `period` symbols,
modulated as the protocol is (with 22 dB SNR in 12.5 kHz and a 600 Hz carrier offset) and fed in
0.1 s blocks. The top hypothesis must name the protocol and modulation within 1.5 s."""

from __future__ import annotations

import unittest

import numpy as np

from sigsense_api.dvoice.dsp.autodetect import AutoDetector
from sigsense_api.dvoice.events import Modulation, Protocol
from sigsense_api.dvoice.sync.patterns import SYNC_SPECS
from sigsense_api.dvoice.testing.channel import impair
from sigsense_api.dvoice.testing.modulator import modulate

# name, sync spec names (cycled), symbol_rate, modulation to TRANSMIT, frame period (symbols), expected
CASES = [
    ("P25 Phase 1 C4FM", ["p25p1"], 4800.0, Modulation.C4FM, 864, (Protocol.P25P1, Modulation.C4FM)),
    ("P25 Phase 1 CQPSK", ["p25p1"], 4800.0, Modulation.CQPSK, 864, (Protocol.P25P1, Modulation.CQPSK)),
    ("P25 Phase 2", ["p25p2"], 6000.0, Modulation.CQPSK, 720, (Protocol.P25P2, Modulation.CQPSK)),
    ("DMR", ["dmr_bs_voice", "dmr_bs_data"], 4800.0, Modulation.FSK4, 144, (Protocol.DMR, Modulation.FSK4)),
    ("NXDN96", ["nxdn_fsw"], 4800.0, Modulation.FSK4, 192, (Protocol.NXDN96, Modulation.FSK4)),
    ("NXDN48", ["nxdn_fsw"], 2400.0, Modulation.FSK4, 192, (Protocol.NXDN48, Modulation.FSK4)),
    ("YSF", ["ysf_fsw"], 4800.0, Modulation.C4FM, 480, (Protocol.YSF, Modulation.C4FM)),
    ("M17", ["m17_stream"], 4800.0, Modulation.FSK4, 192, (Protocol.M17, Modulation.FSK4)),
    ("D-STAR", ["dstar_header"], 4800.0, Modulation.GMSK, 96, (Protocol.DSTAR, Modulation.GMSK)),
]

DEVIATION = {Modulation.C4FM: 1800.0, Modulation.FSK4: 1944.0, Modulation.GMSK: 1200.0}


def build_stream(spec_names, period, seconds, rate, two_level, seed):
    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    d = rng.integers(0, 4, n).astype(np.uint8)
    if two_level:
        d = np.where(d & 1, 3, 1).astype(np.uint8)
    for k, pos in enumerate(range(100, n - 40, period)):
        spec = SYNC_SPECS[spec_names[k % len(spec_names)]]
        d[pos : pos + len(spec.dibits)] = spec.values
    return d


class AutoDetectTests(unittest.TestCase):
    def test_each_protocol_is_the_top_hypothesis(self):
        table = []
        for name, specs, rate, mod, period, expected in CASES:
            two = mod == Modulation.GMSK
            d = build_stream(specs, period, 3.0, rate, two, seed=len(name))
            iq = modulate(d, rate, mod, 48_000.0, DEVIATION.get(mod))
            iq = impair(iq, 22.0, 600.0, 0.0, False, seed=2, snr_bandwidth_hz=12_500.0)
            det = AutoDetector()
            best = None
            for i in range(0, len(iq), 4800):
                hyps = det.feed(iq[i : i + 4800])
                if i >= 48_000 * 1.5 and hyps:  # judge after 1.5 s
                    best = hyps[0]
                    table.append((name, best.protocol.value, best.modulation.value, best.score, hyps[1].score if len(hyps) > 1 else 0.0))
                    break
            self.assertIsNotNone(best, name)
            self.assertEqual((best.protocol, best.modulation), expected, f"{name}: {best}")
            self.assertEqual(best.symbol_rate, rate, name)
            self.assertGreater(best.score, 0.6, f"{name}: {best}")
        print("\nauto-detect after 1.5 s at 22 dB (12.5 kHz) | detected | modulation | score | runner-up score")
        for row in table:
            print(f"   {row[0]:18s} | {row[1]:7s} | {row[2]:6s} | {row[3]:.3f} | {row[4]:.3f}")

    def test_noise_only_yields_nothing_confident(self):
        rng = np.random.default_rng(9)
        iq = (rng.standard_normal(48_000 * 3) + 1j * rng.standard_normal(48_000 * 3)).astype(np.complex64)
        det = AutoDetector()
        top = 0.0
        for i in range(0, len(iq), 4800):
            hyps = det.feed(iq[i : i + 4800])
            top = max(top, hyps[0].score if hyps else 0.0)
        self.assertLess(top, 0.5)

    def test_inverted_polarity_is_still_detected(self):
        d = build_stream(["p25p1"], 864, 3.0, 4800.0, False, seed=1)
        iq = impair(modulate(d, 4800.0, Modulation.C4FM, 48_000.0, 1800.0), 24.0, 0.0, 0.0, True, seed=2, snr_bandwidth_hz=12_500.0)
        det = AutoDetector()
        hyps = []
        for i in range(0, len(iq), 4800):
            hyps = det.feed(iq[i : i + 4800])
        self.assertEqual(hyps[0].protocol, Protocol.P25P1)

    def test_candidate_filter_and_reset(self):
        d = build_stream(["dmr_bs_voice"], 144, 2.0, 4800.0, False, seed=3)
        iq = modulate(d, 4800.0, Modulation.FSK4, 48_000.0, 1944.0)
        det = AutoDetector([Protocol.P25P1])  # DMR not a candidate: must not be reported
        out = []
        for i in range(0, len(iq), 4800):
            out = det.feed(iq[i : i + 4800])
        self.assertTrue(all(h.protocol == Protocol.P25P1 for h in out))
        det2 = AutoDetector([Protocol.DMR])
        for i in range(0, len(iq), 4800):
            out = det2.feed(iq[i : i + 4800])
        self.assertEqual(out[0].protocol, Protocol.DMR)
        det2.reset()
        self.assertEqual(det2.feed(np.zeros(0, np.complex64)), [])


if __name__ == "__main__":
    unittest.main()
