"""DMR AMBE+2 voice frame mapping: self-consistency of voice_map.py and its wiring.

These tests prove the pieces agree with the sources described in voice_map.py (permutation is a bijection,
encode and decode invert each other, the channel code behaves as the reference algorithm). They do NOT prove
the table matches real over-the-air DMR: only a capture can (apps/api/scripts/verify_dmr_voice_map.py)."""

import unittest
from unittest import mock

import numpy as np

from sigsense_api.dvoice.dsp.types import SymbolBlock
from sigsense_api.dvoice.fec import golay
from sigsense_api.dvoice.protocols.base import ProtocolConfig
from sigsense_api.dvoice.protocols.dmr import lc
from sigsense_api.dvoice.protocols.dmr import voice_map as vm
from sigsense_api.dvoice.protocols.dmr.decoder import DmrDecoder
from sigsense_api.dvoice.testing.dmr_encoder import DmrEncoder, assemble_bs_stream
from sigsense_api.dvoice.vocoder import registry

# mbelib ecc_const.h `golayGenerator` (ISC): parity word for data bit 22 .. 11 of the (23,12) code.
MBELIB_GOLAY_GENERATOR = (0x63A, 0x31D, 0x7B4, 0x3DA, 0x1ED, 0x6CC, 0x366, 0x1B3, 0x6E3, 0x54B, 0x49F, 0x475)


def reference_decode(frame: np.ndarray) -> tuple[list[int], int]:
    """A literal, loop-for-loop port of the reference channel decode (mbelib ambe3600x2450.c,
    mbe_eccAmbe3600x2450C0 / mbe_demodulateAmbe3600x2450Data / mbe_eccAmbe3600x2450Data), independent of
    voice_map's vectorised form. Error count = corrected DATA bits, as the reference counts them."""
    fr = [[int(x) for x in row] for row in frame]

    def golay2312(inp):  # in[0..22] -> (out[0..22], errs) with data in 22..11
        word = sum(inp[j] << j for j in range(23))
        data, _ = golay.golay_23_12_decode(word)
        cw = golay.golay_23_12_encode(data)
        out = [(cw >> j) & 1 for j in range(23)]
        return out, sum(1 for i in range(22, 10, -1) if out[i] != inp[i])

    # C0
    out, errs = golay2312([fr[0][j + 1] for j in range(23)])
    for j in range(23):
        fr[0][j + 1] = out[j]
    # demodulate row 1
    foo = 0
    for i in range(23, 11, -1):
        foo = (foo << 1) | fr[0][i]
    pr = [0] * 24
    pr[0] = 16 * foo
    for i in range(1, 24):
        pr[i] = (173 * pr[i - 1] + 13849) % 65536
    for i in range(1, 24):
        pr[i] //= 32768
    k = 1
    for j in range(22, -1, -1):
        fr[1][j] ^= pr[k]
        k += 1
    # C1 + copy
    d = []
    for j in range(23, 11, -1):
        d.append(fr[0][j])
    out1, errs1 = golay2312([fr[1][j] for j in range(23)])
    for j in range(22, 10, -1):
        d.append(out1[j])
    for j in range(10, -1, -1):
        d.append(fr[2][j])
    for j in range(13, -1, -1):
        d.append(fr[3][j])
    return d, errs + errs1


def random_bits(rng, n):
    return rng.integers(0, 2, n).astype(np.uint8)


class TableTests(unittest.TestCase):
    def test_permutation_is_a_bijection_over_72_cells(self):
        cells = set(vm.AIR_TO_CELL)
        self.assertEqual(len(vm.AIR_TO_CELL), 72)
        self.assertEqual(len(cells), 72)
        for row, width in enumerate(vm.ROW_WIDTHS):
            self.assertEqual(sorted(c for r, c in cells if r == row), list(range(width)), f"row {row}")
        self.assertEqual(sum(vm.ROW_WIDTHS), 72)

    def test_air_frame_round_trip(self):
        rng = np.random.default_rng(0)
        for _ in range(50):
            air = random_bits(rng, 72)
            np.testing.assert_array_equal(vm.frame_to_air72(vm.air72_to_frame(air)), air)

    def test_golay_matches_the_reference_generator_rows(self):
        for i, parity in enumerate(MBELIB_GOLAY_GENERATOR):
            cw = golay.golay_23_12_encode(1 << (11 - i))
            self.assertEqual(cw >> 11, 1 << (11 - i))
            self.assertEqual(cw & 0x7FF, parity)

    def test_nxdn_mask_is_144_bits_and_round_trips(self):
        self.assertEqual(vm.NXDN_VOICE_MASK.size, 144)
        rng = np.random.default_rng(3)
        air = random_bits(rng, 72)
        mask = vm.NXDN_VOICE_MASK[36:72]
        np.testing.assert_array_equal(
            vm.frame_to_air72(vm.air72_to_frame(air, descramble=mask), scramble=mask), air)


class ChannelCodeTests(unittest.TestCase):
    def test_encode_decode_round_trip_clean(self):
        rng = np.random.default_rng(1)
        for _ in range(300):
            bits = random_bits(rng, 49)
            out, errs = vm.decode_air72(vm.encode_air72(bits))
            np.testing.assert_array_equal(out, bits)
            self.assertEqual(errs, 0)

    def test_protected_rows_correct_up_to_three_errors_each(self):
        rng = np.random.default_rng(2)
        prot = [i for i, (r, _) in enumerate(vm.AIR_TO_CELL) if r in (0, 1)]
        row_of = {i: vm.AIR_TO_CELL[i][0] for i in prot}
        for _ in range(200):
            bits = random_bits(rng, 49)
            air = vm.encode_air72(bits)
            # up to 3 errors in the Golay codeword of each row (row 0 col 0 is the unused parity bit)
            for row in (0, 1):
                idx = [i for i in prot if row_of[i] == row and vm.AIR_TO_CELL[i] != (0, 0)]
                for i in rng.choice(idx, size=int(rng.integers(0, 4)), replace=False):
                    air[i] ^= 1
            out, errs = vm.decode_air72(air)
            np.testing.assert_array_equal(out, bits)
            self.assertLessEqual(errs, 6)

    def test_unprotected_rows_pass_errors_through(self):
        rng = np.random.default_rng(4)
        bits = random_bits(rng, 49)
        air = vm.encode_air72(bits)
        for i, (r, c) in enumerate(vm.AIR_TO_CELL):
            if r in (2, 3):
                a = air.copy()
                a[i] ^= 1
                out, errs = vm.decode_air72(a)
                self.assertEqual(int((out != bits).sum()), 1)
                self.assertEqual(errs, 0)

    def test_agrees_with_a_literal_port_of_the_reference_on_random_and_noisy_frames(self):
        rng = np.random.default_rng(5)
        for n in range(1500):
            if n % 3 == 0:  # arbitrary noise frame
                air = random_bits(rng, 72)
            else:  # a valid frame with a few channel errors
                air = vm.encode_air72(random_bits(rng, 49))
                for i in rng.choice(72, size=int(rng.integers(0, 8)), replace=False):
                    air[i] ^= 1
            frame = vm.air72_to_frame(air)
            ref_bits, ref_data_errs = reference_decode(frame)
            out, errs = vm.decode_ambe2450_frame(frame)
            self.assertEqual(list(out), ref_bits)
            self.assertGreaterEqual(errs, ref_data_errs)  # ours also counts corrected parity bits


class WiringTests(unittest.TestCase):
    def _call(self, voice_bits_fn, superframes=2):
        enc = DmrEncoder(cc=5, voice_bits=voice_bits_fn)
        lc9 = lc.build_full_lc(0, 0, bytes([0]) + (4001).to_bytes(3, "big") + (777).to_bytes(3, "big"))
        bursts = enc.voice_call(lc9, superframes=superframes)
        return assemble_bs_stream(bursts, [enc.idle_burst()] * len(bursts), enc, lead_idle=3, tail_idle=10)

    def _run(self, stream, extra=None):
        events = []
        with registry.use_test_double():
            dec = DmrDecoder(lambda t, p: events.append((t, p)), registry.get_vocoder,
                             ProtocolConfig(channel="vm", extra=dict(extra or {})))
            for i in range(0, stream.size, 211):
                dec.feed(SymbolBlock(stream[i : i + 211], None, 4800.0, i, i / 4800.0))
        return dec, events

    def test_encoder_to_decoder_recovers_every_parameter_frame_with_zero_golay_errors(self):
        rng = np.random.default_rng(7)
        sent = {}

        def voice_bits(burst, frame):
            bits = random_bits(rng, 49)
            sent[(burst, frame)] = bits
            return vm.encode_air72(bits)

        stream = self._call(voice_bits)
        seen = []
        real = vm.decode_air72

        def spy(raw):
            out, errs = real(raw)
            seen.append((np.asarray(out).copy(), errs))
            return out, errs

        with mock.patch.object(vm, "decode_air72", spy):
            dec, events = self._run(stream)  # no hook configured: the default voice map is used
        sent_list = [v for _, v in sorted(sent.items())]
        self.assertEqual(len(seen), len(sent_list))
        for (out, errs), bits in zip(seen, sent_list):
            np.testing.assert_array_equal(out, bits)
            self.assertEqual(errs, 0)
        voice = [p for t, p in events if t == "voice"]
        self.assertEqual(len(voice), len(sent_list))
        self.assertTrue(all(v.errors == 0 for v in voice))

    def test_wrong_permutation_gives_many_golay_errors(self):
        """Negative control: the same frames read through a shuffled table decode to noise."""
        rng = np.random.default_rng(9)
        right, wrong = [], []
        shuffled = list(vm.AIR_TO_CELL)
        np.random.default_rng(11).shuffle(shuffled)
        rows = np.array([r for r, _ in shuffled], dtype=np.intp)
        cols = np.array([c for _, c in shuffled], dtype=np.intp)
        for _ in range(200):
            air = vm.encode_air72(random_bits(rng, 49))
            right.append(vm.decode_air72(air)[1])
            frame = np.zeros((4, 24), dtype=np.uint8)
            frame[rows, cols] = air
            wrong.append(vm.decode_ambe2450_frame(frame)[1])
        self.assertEqual(max(right), 0)
        self.assertGreater(float(np.mean(wrong)), 4.0)  # about 5.7 expected for random words
        self.assertLess(float(np.mean([w <= 1 for w in wrong])), 0.02)

    def test_explicit_hook_and_raw_frame_mode_still_take_precedence(self):
        stream = self._call(lambda b, f: np.zeros(72, np.uint8), superframes=1)
        calls = []
        _, ev = self._run(stream, {"ambe_channel_decode": lambda r: (calls.append(1) or r[:49], 0)})
        self.assertGreater(len(calls), 0)
        with mock.patch.object(vm, "decode_air72", side_effect=AssertionError("must not run")):
            dec, ev = self._run(stream, {"ambe_raw_frames": True})
        self.assertGreater(len([1 for t, p in ev if t == "voice"]), 0)


if __name__ == "__main__":
    unittest.main()


class VerifyScriptTests(unittest.TestCase):
    """The capture-verification script, run on a synthetic 'capture' (proves the script works; it cannot
    prove the table matches real air - that needs a real recording)."""

    def test_script_passes_the_right_table_and_ranks_the_shuffled_control_far_below(self):
        import importlib.util
        import pathlib

        from sigsense_api.dvoice.events import Modulation
        from sigsense_api.dvoice.testing.channel import impair
        from sigsense_api.dvoice.testing.modulator import modulate

        path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "verify_dmr_voice_map.py"
        spec = importlib.util.spec_from_file_location("verify_dmr_voice_map", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        rng = np.random.default_rng(21)
        enc = DmrEncoder(cc=2, voice_bits=lambda b, f: vm.encode_air72(random_bits(rng, 49)))
        lc9 = lc.build_full_lc(0, 0, bytes([0]) + (9).to_bytes(3, "big") + (8).to_bytes(3, "big"))
        bursts = enc.voice_call(lc9, superframes=8)
        stream = assemble_bs_stream(bursts, [enc.idle_burst()] * len(bursts), enc, lead_idle=3, tail_idle=10)
        iq = modulate(stream, 4800.0, Modulation.FSK4, 48_000.0, 1944.0)
        iq = impair(iq, 30.0, sample_rate=48_000.0, snr_bandwidth_hz=12_500.0, seed=3).astype(np.complex64)
        result = mod.analyze((iq[i : i + 50_000] for i in range(0, iq.size, 50_000)), 48_000.0)
        self.assertGreaterEqual(result["voice_frames"], 100)
        code, text = mod.verdict(result)
        self.assertEqual(code, 0, text)
        self.assertGreater(result["hypotheses"]["table"]["clean_fraction"], 0.9)
        self.assertLess(result["hypotheses"]["control-shuffled"]["clean_fraction"], 0.05)
        self.assertEqual(mod.verdict({"voice_frames": 3, "hypotheses": {}})[0], 2)
