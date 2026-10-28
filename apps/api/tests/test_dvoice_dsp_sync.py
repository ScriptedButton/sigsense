"""SyncCorrelator: detection with symbol errors, polarity, block straddling, overlap merging and
false-alarm rates on random data. Owner: WP-B.

False-alarm model: on uniformly random 4-level data a word of L symbols with tolerance e matches at a
given position with probability P = sum_{k<=e} C(L,k) (3/4)^k (1/4)^(L-k) (2-level words: 1/2 per
symbol). The tests compare the measured counts with N * P (both polarities) and require the long
words (>= 20 symbols at 4 levels) to produce NO false hit in 1e6 random symbols. Short words (M17 8
symbols, NXDN 10) DO false-alarm at their configured tolerance (see the printed table); protocol
decoders must confirm every hit with the frame's own structure (NID / LICH / CRC) before acting.
"""

from __future__ import annotations

import math
import unittest

import numpy as np

from sigsense_api.dvoice.sync.correlator import SyncCorrelator
from sigsense_api.dvoice.sync.patterns import SYNC_SPECS

TWO_LEVEL = {"dstar_header", "dstar_sync"}


def noise_stream(n, rng, two_level=False):
    d = rng.integers(0, 4, n).astype(np.uint8)
    return np.where(d & 1, 3, 1).astype(np.uint8) if two_level else d


def corrupt(word, k, rng, two_level=False):
    w = word.copy()
    for p in rng.choice(len(w), k, replace=False):
        choices = [v for v in ((1, 3) if two_level else (0, 1, 2, 3)) if v != w[p]]
        w[p] = rng.choice(choices)
    return w


def scan_in_blocks(cor, x, sizes, rng=None):
    hits, i, k = [], 0, 0
    while i < len(x):
        n = int(sizes[k % len(sizes)])
        hits += cor.scan(x[i : i + n], i)
        i += n
        k += 1
    hits += cor.flush()
    return hits


def match_probability(length, errors, levels):
    q = 1.0 / levels
    return sum(math.comb(length, k) * (1 - q) ** k * q ** (length - k) for k in range(errors + 1))


class DetectionTests(unittest.TestCase):
    def test_every_spec_found_with_up_to_max_errors_both_polarities_and_split_blocks(self):
        rng = np.random.default_rng(1)
        for spec in SYNC_SPECS.values():
            two = spec.name in TWO_LEVEL
            for inverted in (False, True):
                base = np.array(spec.inverted_values if inverted else spec.values, dtype=np.uint8)
                if two and inverted:
                    base = np.array(spec.inverted_values, dtype=np.uint8)
                for errors in range(spec.max_errors + 1):
                    for trial in range(3):
                        pos = int(rng.integers(200, 600))
                        x = noise_stream(1000, rng, two)
                        # 4-level words sit in clean (non-matching) surroundings: use another random run
                        x[pos : pos + len(base)] = corrupt(base, errors, rng, two) if errors else base
                        cor = SyncCorrelator([spec])
                        hits = scan_in_blocks(cor, x, rng.integers(5, 97, 32))
                        good = [h for h in hits if h.index == pos]
                        self.assertEqual(len(good), 1, (spec.name, inverted, errors, hits))
                        self.assertEqual((good[0].errors, good[0].inverted, good[0].spec), (errors, inverted, spec.name))

    def test_word_straddling_every_block_boundary(self):
        for name in ("p25p1", "dmr_bs_voice", "ysf_fsw", "m17_lsf", "nxdn_fsw"):
            spec = SYNC_SPECS[name]
            word = np.array(spec.values, dtype=np.uint8)
            for cut in range(1, len(word)):
                x = np.concatenate((np.zeros(40, np.uint8), word, np.zeros(60, np.uint8)))
                cor = SyncCorrelator([spec], max_errors=0)
                first = cor.scan(x[: 40 + cut], 0)
                second = cor.scan(x[40 + cut :], 40 + cut)
                hits = first + second + cor.flush()
                self.assertEqual([(h.index, h.errors) for h in hits], [(40, 0)], (name, cut))

    def test_inverted_polarity_flag_and_all_specs_together(self):
        rng = np.random.default_rng(2)
        cor = SyncCorrelator(SYNC_SPECS.values())
        spec = SYNC_SPECS["p25p1"]
        x = noise_stream(3000, rng)
        x[500:524] = spec.values
        x[1500:1524] = spec.inverted_values
        hits = [h for h in scan_in_blocks(cor, x, [333]) if h.spec == "p25p1"]
        self.assertEqual([(h.index, h.errors, h.inverted) for h in hits], [(500, 0, False), (1500, 0, True)])

    def test_overlapping_candidates_report_only_the_best(self):
        # All four M17 words are 8 symbols; a clean LSF word has 1-error look-alikes of other words
        # nearby only by coincidence, so build the stream in silence (dibit 0): exactly one hit.
        spec_names = ("m17_lsf", "m17_stream", "m17_bert", "m17_packet")
        cor = SyncCorrelator([SYNC_SPECS[n] for n in spec_names])
        x = np.zeros(200, np.uint8)
        x[80:88] = SYNC_SPECS["m17_lsf"].values
        hits = scan_in_blocks(cor, x, [50])
        self.assertEqual([(h.spec, h.index, h.errors) for h in hits], [("m17_lsf", 80, 0)])

    def test_hit_is_held_until_better_overlap_is_excluded_and_flush_releases_it(self):
        spec = SYNC_SPECS["p25p1"]
        cor = SyncCorrelator([spec])
        x = np.zeros(100, np.uint8)
        x[70:94] = spec.values
        early = cor.scan(x[:96], 0)  # the word ended 2 symbols ago: still pending
        self.assertEqual(early, [])
        self.assertEqual([(h.index, h.errors) for h in cor.flush()], [(70, 0)])

    def test_gap_in_the_stream_prevents_phantom_matches(self):
        spec = SYNC_SPECS["p25p1"]
        word = np.array(spec.values, dtype=np.uint8)
        cor = SyncCorrelator([spec], max_errors=0)
        a = cor.scan(np.concatenate((np.zeros(30, np.uint8), word[:12])), 0)
        b = cor.scan(np.concatenate((word[12:], np.zeros(80, np.uint8))), 500)  # jump: 12 + 12 must NOT join
        self.assertEqual(a + b + cor.flush(), [])

    def test_reset_and_empty_input(self):
        cor = SyncCorrelator([SYNC_SPECS["p25p1"]])
        self.assertEqual(cor.scan(np.zeros(0, np.uint8), 0), [])
        cor.reset()
        self.assertEqual(cor.flush(), [])


class FalseAlarmTests(unittest.TestCase):
    N = 1_000_000

    def _count(self, spec, max_errors, rng):
        two = spec.name in TWO_LEVEL
        cor = SyncCorrelator([spec], max_errors=max_errors)
        return len(scan_in_blocks(cor, noise_stream(self.N, rng, two), [65536]))

    def test_false_alarm_table_matches_the_binomial_model(self):
        rng = np.random.default_rng(7)
        print(f"\nfalse alarms on {self.N:,} random symbols (both polarities) | spec  len  tol | measured | expected")
        for spec in SYNC_SPECS.values():
            two = spec.name in TWO_LEVEL
            levels = 2 if two else 4
            for tol in sorted({0, spec.max_errors}):
                measured = self._count(spec, tol, rng)
                expected = 2 * self.N * match_probability(len(spec.dibits), tol, levels)
                print(f"   {spec.name:14s} {len(spec.dibits):3d}  {tol}  | {measured:8d} | {expected:10.2f}")
                # Poisson-ish bound; overlapping hits merge so the measured count is <= the model
                self.assertLessEqual(measured, 1.5 * expected + 6 * math.sqrt(expected) + 5, (spec.name, tol))

    def test_long_words_never_false_alarm_at_their_tolerance(self):
        """>= 20-symbol 4-level words: expected false alarms per 1e6 symbols are < 0.1."""
        rng = np.random.default_rng(8)
        for name in ("p25p1", "p25p2", "dmr_bs_voice", "dmr_bs_data", "dmr_ms_voice", "dmr_ms_data", "ysf_fsw"):
            spec = SYNC_SPECS[name]
            self.assertEqual(self._count(spec, spec.max_errors, rng), 0, name)


if __name__ == "__main__":
    unittest.main()
