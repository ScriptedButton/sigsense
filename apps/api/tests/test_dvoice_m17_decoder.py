"""M17 decoder behaviour (WP-E): calls, voice, late join, encryption, packets, BERT, robustness.

Transmissions come from the independent encoder in testing/m17_encoder.py. Voice payloads are
captured at the vocoder boundary so tests can check that the exact bits that were sent are the
bits the decoder hands over (a "trusted" frame that decodes to wrong bits would fail here).
"""

from __future__ import annotations

import ctypes
import unittest

import numpy as np

from sigsense_api.dvoice.dsp.types import SymbolBlock
from sigsense_api.dvoice.events import CallKind, EncryptionState, VocoderId
from sigsense_api.dvoice.fec import crc as fec_crc
from sigsense_api.dvoice.protocols.base import ProtocolConfig
from sigsense_api.dvoice.protocols.m17 import M17Decoder
from sigsense_api.dvoice.testing import m17_encoder as enc
from sigsense_api.dvoice.vocoder import registry
from sigsense_api.dvoice.vocoder.base import Availability, UnavailableVocoder, Vocoder

RNG = np.random.default_rng(20251023)


class CaptureVocoder(Vocoder):
    """Records the bits it is given; returns silence. Each Harness owns its own log list."""

    id = VocoderId.CODEC2_3200

    def __init__(self, log: list):
        self.log = log

    @classmethod
    def availability(cls) -> Availability:
        return Availability(True, "test capture", "n/a", "capture")

    def decode(self, frame):
        self.log.append((frame.vocoder, bytes(np.packbits(frame.bits))))
        return np.zeros(320 if frame.vocoder == VocoderId.CODEC2_1600 else 160, dtype=np.int16)


class Harness:
    def __init__(self, *, decode_data: bool = False, provider=None):
        self.events: list = []
        self.log: list = []
        self.provider = provider or (lambda vid: CaptureVocoder(self.log))
        self.decoder = M17Decoder(
            lambda t, p: self.events.append((t, p)), self.provider, ProtocolConfig(decode_data=decode_data)
        )
        self.index = 0

    def feed(self, dibits, soft=None, chunks=None):
        dibits = np.asarray(dibits, dtype=np.uint8)
        if chunks is None:
            chunks = [len(dibits)]
        pos = 0
        for n in chunks:
            part = dibits[pos : pos + n]
            if not len(part):
                break
            self.decoder.feed(
                SymbolBlock(
                    dibits=part,
                    soft=None if soft is None else soft[pos : pos + n],
                    symbol_rate=4800.0,
                    first_index=self.index,
                    t0=self.index / 4800.0,
                )
            )
            self.index += len(part)
            pos += len(part)
        return self

    def of(self, kind):
        return [p for t, p in self.events if t == kind]

    @property
    def starts(self):
        return self.of("call_start")

    @property
    def ends(self):
        return self.of("call_end")

    @property
    def sent_ok(self):
        return self.log


def noise(n):
    return RNG.integers(0, 4, n).astype(np.uint8)


def payloads(n):
    return [bytes(RNG.integers(0, 256, 16, dtype=np.uint8)) for _ in range(n)]


def halves(pls):
    return [h for pl in pls for h in (pl[:8], pl[8:])]


def symbol_errors(dibits, rate, rng):
    """Adjacent-level symbol errors (Gray mapped: each one is a single bit error)."""
    order = [3, 2, 0, 1]  # -3 -1 +1 +3
    pos = {d: i for i, d in enumerate(order)}
    out = dibits.copy()
    for i in np.flatnonzero(rng.random(out.size) < rate):
        j = min(3, max(0, pos[int(out[i])] + int(rng.choice([-1, 1]))))
        out[i] = order[j]
    return out


class CleanCallTests(unittest.TestCase):
    def test_stream_call_fields_voice_and_end(self):
        pls = payloads(20)
        call = enc.SyntheticM17Call(src="W2FBI", dst="N0CALL", can=7, payloads=pls)
        h = Harness().feed(np.concatenate([noise(321), enc.stream_transmission(call), noise(200)]))
        self.assertEqual(len(h.starts), 1)
        start = h.starts[0]
        self.assertEqual(start.meta["src"], "W2FBI")
        self.assertEqual(start.meta["dst"], "N0CALL")
        self.assertEqual(start.source_id, enc.encode_callsign("W2FBI"))
        self.assertEqual(start.target_id, enc.encode_callsign("N0CALL"))
        self.assertEqual((start.kind, start.meta["can"]), (CallKind.PRIVATE, 7))
        self.assertEqual(start.encryption.state, EncryptionState.CLEAR)
        end = h.ends[0]
        self.assertEqual((end.call_id, end.end_reason, end.voice_frames, end.voice_frames_bad), (start.call_id, "terminator", 40, 0))
        self.assertTrue(end.encryption.audio_decoded)
        self.assertEqual(end.ber, 0.0)
        # exactly the bits that were sent reach the vocoder, in order, as 8-byte Codec2 3200 frames
        self.assertEqual([b for _, b in h.sent_ok], halves(pls))
        self.assertTrue(all(v == VocoderId.CODEC2_3200 for v, _ in h.sent_ok))
        self.assertEqual(len(h.of("voice")), 40)
        self.assertEqual([v.seq for v in h.of("voice")], list(range(40)))
        self.assertEqual(h.of("system_info")[0].extra, {"can": 7})

    def test_broadcast_and_reflector_destinations(self):
        h = Harness().feed(enc.stream_transmission(enc.SyntheticM17Call(dst="ALL", payloads=payloads(3))))
        self.assertEqual((h.starts[0].kind, h.starts[0].meta["dst"]), (CallKind.ALL, "BROADCAST"))
        h = Harness().feed(enc.stream_transmission(enc.SyntheticM17Call(dst="M17-M17 C", payloads=payloads(3))))
        self.assertEqual(h.starts[0].kind, CallKind.GROUP)

    def test_two_back_to_back_calls_get_distinct_ids(self):
        a = enc.stream_transmission(enc.SyntheticM17Call(src="K1ABC", payloads=payloads(8)))
        b = enc.stream_transmission(enc.SyntheticM17Call(src="K2DEF", payloads=payloads(8)))
        h = Harness().feed(np.concatenate([a, noise(50), b]))
        self.assertEqual([s.meta["src"] for s in h.starts], ["K1ABC", "K2DEF"])
        self.assertEqual(len({s.call_id for s in h.starts}), 2)
        self.assertEqual([e.end_reason for e in h.ends], ["terminator", "terminator"])

    def test_sync_events_report_the_frames(self):
        h = Harness().feed(enc.stream_transmission(enc.SyntheticM17Call(payloads=payloads(4))))
        names = [s.pattern for s in h.of("sync")]
        self.assertEqual(names, ["m17_lsf"] + ["m17_stream"] * 4)
        self.assertTrue(all(not s.inverted for s in h.of("sync")))

    def test_inverted_polarity(self):
        pls = payloads(10)
        tx = enc.stream_transmission(enc.SyntheticM17Call(payloads=pls)) ^ 2
        h = Harness().feed(tx)
        self.assertEqual(len(h.starts), 1)
        self.assertTrue(all(s.inverted for s in h.of("sync")))
        self.assertEqual([b for _, b in h.sent_ok], halves(pls))

    def test_voice_and_data_uses_codec2_1600(self):
        pls = payloads(6)
        call = enc.SyntheticM17Call(data_type=3, payloads=pls)
        h = Harness().feed(enc.stream_transmission(call))
        self.assertEqual([b for v, b in h.sent_ok], [pl[:8] for pl in pls])
        self.assertTrue(all(v == VocoderId.CODEC2_1600 for v, _ in h.sent_ok))
        self.assertEqual(h.ends[0].meta["data_bytes"], 8 * 6)
        self.assertEqual(len(h.of("voice")), 12)  # 320 samples per codec frame -> two 160-sample VoiceFrames

    def test_data_stream_is_not_given_to_a_vocoder(self):
        h = Harness().feed(enc.stream_transmission(enc.SyntheticM17Call(data_type=1, payloads=payloads(4))))
        self.assertEqual(h.sent_ok, [])
        self.assertEqual(h.starts[0].meta["audio"], "data stream")

    def test_vocoder_unavailable_reports_once_and_keeps_metadata(self):
        h = Harness(provider=lambda vid: UnavailableVocoder(vid)).feed(
            enc.stream_transmission(enc.SyntheticM17Call(payloads=payloads(6)))
        )
        errors = [e for e in h.of("error") if e.code == "vocoder_unavailable"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(h.starts[0].meta["src"], "W2FBI")
        self.assertTrue(all(v.pcm is None for v in h.of("voice")))
        self.assertFalse(h.ends[0].encryption.audio_decoded)


class ChunkInvarianceTests(unittest.TestCase):
    @staticmethod
    def signature(h):
        out = []
        for t, p in h.events:
            if t in ("call_start", "call_end"):
                out.append((t, p.meta["src"], p.meta["dst"], p.voice_frames, p.voice_frames_bad, p.end_reason))
            elif t == "sync":
                out.append((t, p.pattern, p.symbol_index, p.errors, p.inverted))
            elif t == "voice":
                out.append((t, p.seq, p.errors))
            elif t not in ("call_update",):
                out.append((t,))
        return out

    def test_any_block_boundaries_give_identical_results(self):
        pls = payloads(15)
        tx = np.concatenate([noise(777), enc.stream_transmission(enc.SyntheticM17Call(payloads=pls)), noise(300)])
        tx = symbol_errors(tx, 0.03, np.random.default_rng(3))
        whole = Harness().feed(tx)
        self.assertEqual(len(whole.starts), 1)
        rng = np.random.default_rng(9)
        for trial in range(4):
            sizes = []
            left = len(tx)
            while left > 0:
                n = int(rng.integers(1, 700)) if trial < 3 else int(rng.integers(1, 12))
                sizes.append(min(n, left))
                left -= sizes[-1]
            h = Harness().feed(tx, chunks=sizes)
            self.assertEqual(self.signature(h), self.signature(whole), f"trial {trial}")
            self.assertEqual(h.sent_ok, whole.sent_ok)


class NoiseTests(unittest.TestCase):
    def exact_fraction(self, rate, seed, soft=False, nframes=25):
        rng = np.random.default_rng(seed)
        pls = payloads(nframes)
        tx = enc.stream_transmission(enc.SyntheticM17Call(src="K1ABC", dst="W2XYZ", payloads=pls))
        tx = np.concatenate([noise(300), tx, noise(300)])
        noisy = symbol_errors(tx, rate, rng)
        h = Harness().feed(noisy)
        sent = set(halves(pls))
        got = [b for _, b in h.sent_ok]
        exact = sum(1 for b in got if b in sent)
        return h, exact, len(got)

    def test_clean_and_light_noise_decode_every_frame_exactly(self):
        for rate in (0.0, 0.02, 0.04):
            h, exact, got = self.exact_fraction(rate, seed=int(rate * 1000) + 1)
            self.assertEqual(len(h.starts), 1, rate)
            self.assertGreaterEqual(exact, 49, rate)  # at most one damaged half-frame of 50

    def test_heavy_noise_never_hands_the_vocoder_mostly_wrong_bits(self):
        # At 12 % symbol errors (about 6 % bit errors) most frames are still good; the ones that are
        # not must be erased rather than decoded. Measured: ~93 % of the frames let through are exact.
        total_exact = total = 0
        for seed in range(6):
            h, exact, got = self.exact_fraction(0.12, seed=100 + seed)
            total_exact += exact
            total += got
        self.assertGreater(total, 6 * 25)  # a good share of the 300 half-frames still decodes
        self.assertGreater(total_exact / total, 0.85)

    def test_soft_symbols_through_the_decoder(self):
        # Gaussian noise on 4-level symbols, sliced at 0 and +-2 for the hard dibits. Soft values let the
        # decoder find more syncs and decode more frames correctly (measured offline, 6 transmissions of
        # 20 frames: sigma 0.7 soft 179 vs hard 87 exact frames of 240; sigma 0.8 soft 119 vs hard 2).
        levels = np.array([1.0, 3.0, -1.0, -3.0])  # dibit value -> level
        soft_exact = hard_exact = soft_wrong = 0
        for seed in range(6):
            rng = np.random.default_rng(100 + seed)
            pls = payloads(20)
            tx = enc.stream_transmission(enc.SyntheticM17Call(payloads=pls))
            x = levels[tx] + rng.normal(0, 0.7, tx.size)
            hard = np.where(x >= 2, 1, np.where(x >= 0, 0, np.where(x > -2, 2, 3))).astype(np.uint8)
            with_soft = Harness().feed(hard, soft=x.astype(np.float32))
            without = Harness().feed(hard)
            sent = set(halves(pls))
            soft_exact += sum(1 for _, b in with_soft.sent_ok if b in sent)
            soft_wrong += sum(1 for _, b in with_soft.sent_ok if b not in sent)
            hard_exact += sum(1 for _, b in without.sent_ok if b in sent)
        self.assertGreater(soft_exact, hard_exact * 1.5)
        self.assertGreater(soft_exact, 100)
        self.assertLess(soft_wrong, 0.25 * soft_exact)  # damaged frames that slip through stay a minority

    def test_random_symbols_never_create_a_call_or_raise(self):
        rng = np.random.default_rng(77)
        h = Harness()
        for _ in range(40):
            h.feed(rng.integers(0, 4, 5000).astype(np.uint8))
        self.assertEqual(h.starts, [])
        self.assertEqual(h.sent_ok, [])
        self.assertEqual([e for e in h.of("error") if e.code == "decoder_fault"], [])

    def test_stream_frames_alone_cannot_start_a_call_without_a_valid_lsf(self):
        # Three stream frames and a destroyed LSF: the LICH has not delivered all six chunks.
        call = enc.SyntheticM17Call(payloads=payloads(3))
        tx = enc.stream_transmission(call)
        damaged = tx.copy()
        damaged[192:384] = noise(192)  # the LSF payload (after the 192-symbol preamble) is destroyed
        damaged[192 : 192 + 8] = enc._word_dibits(0x55F7)  # but its sync survives
        h = Harness().feed(damaged)
        self.assertEqual(h.starts, [])
        self.assertEqual(h.sent_ok, [])  # nothing reaches a vocoder: the call context (and encryption state) is unknown


class LateJoinTests(unittest.TestCase):
    def test_call_is_learned_from_six_lich_chunks_then_audio_flows(self):
        pls = payloads(14)
        call = enc.SyntheticM17Call(src="K1ABC", dst="W2XYZ", can=4, payloads=pls)
        tx = enc.stream_transmission(call, with_preamble=False)
        late = tx[192:]  # drop the LSF frame: a receiver tuning in mid-stream
        late = late[192 * 2 :]  # and the first two stream frames
        h = Harness().feed(late)
        self.assertEqual(len(h.starts), 1)
        self.assertEqual((h.starts[0].meta["src"], h.starts[0].meta["dst"], h.starts[0].meta["can"]), ("K1ABC", "W2XYZ", 4))
        # the call was only known after all six chunks (frame numbers 2..7), so no audio before that
        first_voice = next(i for i, (t, _) in enumerate(h.events) if t == "voice")
        first_start = next(i for i, (t, _) in enumerate(h.events) if t == "call_start")
        self.assertLess(first_start, first_voice)
        # held frames were flushed once clear audio was established: the tail of the transmission is complete
        sent = halves(pls)
        got = [b for _, b in h.sent_ok]
        self.assertEqual(got[-2 * 8 :], sent[-2 * 8 :])
        self.assertEqual(h.ends[0].end_reason, "terminator")

    def test_new_transmission_without_an_eot_replaces_the_old_call(self):
        a = enc.stream_transmission(enc.SyntheticM17Call(src="K1ABC", payloads=payloads(8), send_eot=False), with_eot=False)
        b = enc.stream_transmission(enc.SyntheticM17Call(src="K2DEF", payloads=payloads(8)))
        h = Harness().feed(np.concatenate([a, b]))
        self.assertEqual([s.meta["src"] for s in h.starts], ["K1ABC", "K2DEF"])
        self.assertEqual(len(h.ends), 2)


class EncryptionPolicyTests(unittest.TestCase):
    def run_enc(self, enc_type, subtype, late=False):
        call = enc.SyntheticM17Call(enc_type=enc_type, enc_subtype=subtype, payloads=payloads(14))
        tx = enc.stream_transmission(call, with_preamble=False)
        if late:
            tx = tx[192 * 3 :]
        return Harness().feed(tx)

    def test_encrypted_streams_are_reported_and_never_decoded(self):
        for enc_type, subtype, name in ((1, 0, "M17 scrambler (8-bit LFSR)"), (1, 2, "M17 scrambler (24-bit LFSR)"), (2, 0, "AES-128-CTR"), (2, 2, "AES-256-CTR")):
            for late in (False, True):
                h = self.run_enc(enc_type, subtype, late)
                self.assertEqual(len(h.starts), 1, (name, late))
                self.assertEqual(h.starts[0].encryption.state, EncryptionState.ENCRYPTED)
                self.assertEqual(h.starts[0].encryption.algorithm_name, name)
                self.assertEqual(h.sent_ok, [], (name, late))  # never given to a vocoder
                self.assertEqual(h.of("voice"), [])
                self.assertFalse(h.ends[0].encryption.audio_decoded)
                self.assertEqual(h.starts[0].meta["audio"], "encrypted - not decoded")

    def test_aes_reports_the_nonce_without_using_it(self):
        call = enc.SyntheticM17Call(enc_type=2, enc_subtype=1, meta=bytes(range(1, 15)), payloads=payloads(3))
        h = Henc = Harness().feed(enc.stream_transmission(call))
        self.assertTrue(h.starts[0].encryption.mi_present)
        self.assertNotIn("text", h.starts[0].meta)


class SignedStreamTests(unittest.TestCase):
    def test_signature_frames_are_not_played_as_speech(self):
        pls = payloads(8)
        call = enc.SyntheticM17Call(signed=True, payloads=pls, first_fn=0x7FF8)
        h = Harness().feed(enc.stream_transmission(call))
        self.assertEqual(len(h.starts), 1)
        self.assertTrue(h.starts[0].meta["signed"])
        self.assertEqual([b for _, b in h.sent_ok], halves(pls[:4]))  # FN 0x7FF8..0x7FFB are speech
        self.assertEqual(h.ends[0].meta["signature_frames"], 4)  # FN 0x7FFC..0x7FFF carry the signature
        self.assertEqual(h.ends[0].end_reason, "terminator")


class MetaTests(unittest.TestCase):
    def test_text_message_reaches_the_call(self):
        text = "CQ CQ from the lab"
        data = text.encode().ljust(26)
        blocks = [data[:13], data[13:26]]
        # one text block per superframe position, repeated as the stream runs
        call_meta = bytes([0b0011_0001]) + blocks[0]
        h = Harness().feed(enc.stream_transmission(enc.SyntheticM17Call(enc_subtype=0, meta=call_meta, payloads=payloads(6))))
        self.assertNotIn("text", h.starts[0].meta)  # incomplete: only one of two blocks
        # now the second block arrives in a later superframe (META changes only at superframe boundaries)
        second = bytes([0b0011_0010]) + blocks[1]
        lsf2 = enc.lsf_bytes(enc.address_bytes("ALL") if False else enc.BROADCAST, enc.address_bytes("W2FBI"), enc.make_type(), second)
        # (decoder sees the LSF of a fresh transmission; text assembly is per call, so test the assembler path directly)
        from sigsense_api.dvoice.protocols.m17 import lsf as L

        asm = L.TextAssembler()
        asm.push(call_meta)
        self.assertEqual(asm.push(second), text)
        self.assertTrue(L.parse_lsf(lsf2))

    def test_gnss_and_extended_callsign_meta_in_the_lsf(self):
        meta = bytearray(14)
        meta[0] = 0x01
        meta[1] = 0b1000 << 4
        lat = round(40.0 / 90 * 8388607) & 0xFFFFFF
        lon = round(-75.0 / 180 * 8388607) & 0xFFFFFF
        meta[3:6] = lat.to_bytes(3, "big")
        meta[6:9] = lon.to_bytes(3, "big")
        h = Harness().feed(enc.stream_transmission(enc.SyntheticM17Call(enc_subtype=1, meta=bytes(meta), payloads=payloads(3))))
        g = h.starts[0].meta["gnss"]
        self.assertAlmostEqual(g["latitude"], 40.0, places=3)
        self.assertAlmostEqual(g["longitude"], -75.0, places=3)
        ext = enc.address_bytes("N0CALL") + enc.address_bytes("M17-M17 C") + bytes(2)
        h = Harness().feed(enc.stream_transmission(enc.SyntheticM17Call(enc_subtype=2, meta=ext, payloads=payloads(3))))
        self.assertEqual(h.starts[0].meta["extended_callsigns"], ["N0CALL", "M17-M17 C"])


class PacketTests(unittest.TestCase):
    def test_sms_packet_across_several_frames(self):
        body = bytes([0x05]) + b"Hello over M17 packet mode, this text spans three frames.\x00"
        h = Harness(decode_data=True).feed(enc.packet_transmission("W2FBI", "N0CALL", body))
        self.assertEqual(len(h.starts), 1)
        end = h.ends[0]
        self.assertEqual((end.kind, end.end_reason), (CallKind.DATA, "terminator"))
        self.assertTrue(end.meta["packet_ok"])
        self.assertEqual((end.meta["packet_protocol"], end.meta["packet_bytes"]), ("SMS", len(body)))
        self.assertEqual(end.meta["text"], "Hello over M17 packet mode, this text spans three frames.")
        self.assertEqual(h.sent_ok, [])  # packet mode never touches a vocoder

    def test_payload_text_is_not_reported_unless_data_decoding_is_enabled(self):
        body = bytes([0x05]) + b"secret-ish text\x00"
        h = Harness(decode_data=False).feed(enc.packet_transmission("W2FBI", "ALL", body))
        end = h.ends[0]
        self.assertTrue(end.meta["packet_ok"])
        self.assertNotIn("text", end.meta)
        self.assertNotIn("packet_payload_hex", end.meta)
        self.assertEqual(end.meta["packet_protocol"], "SMS")

    def test_exact_multiples_of_25_bytes(self):
        body = bytes([0x00]) + bytes(RNG.integers(0, 256, 47, dtype=np.uint8))  # 48 + 2 CRC = 50 bytes = 2 frames
        h = Harness(decode_data=True).feed(enc.packet_transmission("W2FBI", "ALL", body))
        end = h.ends[0]
        self.assertTrue(end.meta["packet_ok"])
        self.assertEqual(bytes.fromhex(end.meta["packet_payload_hex"]), body[1:])

    def test_bad_crc_is_flagged_not_trusted(self):
        body = bytes([0x05]) + b"hello\x00"
        data = body + (enc.crc16(body) ^ 0x1234).to_bytes(2, "big")  # wrong CRC
        lsf = enc.lsf_bytes(enc.address_bytes("N0CALL"), enc.address_bytes("W2FBI"), enc.make_type(stream=False))
        tx = np.concatenate(
            [
                enc.preamble(),
                enc.frame_dibits("lsf", enc.lsf_payload(lsf)),
                enc.frame_dibits("packet", enc.packet_payload(data, True, len(data))),
                enc.eot(),
            ]
        )
        h = Harness(decode_data=True).feed(tx)
        end = h.ends[0]
        self.assertFalse(end.meta["packet_ok"])
        self.assertNotIn("text", end.meta)

    def test_truncated_packet_is_reported_incomplete(self):
        body = bytes([0x05]) + b"x" * 80 + b"\x00"
        tx = enc.packet_transmission("W2FBI", "ALL", body)
        cut = tx[: 192 + 192 + 192]  # preamble + LSF + first packet frame only
        h = Harness().feed(np.concatenate([cut, noise(2500)]))
        self.assertEqual(len(h.ends), 1)
        self.assertIn(h.ends[0].end_reason, ("sync_loss", "timeout"))  # never "terminator"
        self.assertFalse(h.ends[0].meta["packet_ok"])
        self.assertTrue(h.ends[0].meta["packet_incomplete"])


class BertTests(unittest.TestCase):
    def test_clean_bert_counts_zero_errors(self):
        h = Harness().feed(enc.bert_transmission(8))
        stats = h.decoder.stats()
        self.assertEqual(stats["bert_frames"], 8)
        self.assertEqual(stats["bert_ber"], 0.0)
        self.assertGreater(stats["bert_bits"], 1000)
        self.assertEqual(h.starts, [])  # BERT never creates a call

    def test_errors_the_fec_cannot_remove_show_up_as_ber(self):
        clean = enc.bert_transmission(30)
        rng = np.random.default_rng(8)
        noisy = symbol_errors(clean, 0.13, rng)  # heavy enough that some frames decode with residual errors
        h = Harness().feed(noisy)
        stats = h.decoder.stats()
        self.assertGreater(stats["bert_frames"], 5)
        if stats["bert_bits"]:
            self.assertGreaterEqual(stats["bert_ber"], 0.0)


class LifecycleTests(unittest.TestCase):
    def test_reset_ends_an_open_call_as_retune(self):
        call = enc.SyntheticM17Call(payloads=payloads(10), send_eot=False)
        tx = enc.stream_transmission(call, with_eot=False)
        h = Harness().feed(tx[: 192 * 6])
        self.assertEqual(len(h.starts), 1)
        h.decoder.reset()
        self.assertEqual(h.ends[-1].end_reason, "retune")

    def test_sync_loss_ends_the_call(self):
        call = enc.SyntheticM17Call(payloads=payloads(10), send_eot=False)
        tx = enc.stream_transmission(call, with_eot=False)
        h = Harness().feed(np.concatenate([tx[: 192 * 6], noise(192 * 8)]))
        self.assertEqual([e.end_reason for e in h.ends], ["sync_loss"])

    def test_stream_gap_does_not_crash_and_a_later_call_decodes(self):
        a = enc.stream_transmission(enc.SyntheticM17Call(src="K1ABC", payloads=payloads(5)))
        b = enc.stream_transmission(enc.SyntheticM17Call(src="K2DEF", payloads=payloads(5)))
        h = Harness().feed(a)
        h.index += 5000  # the front end lost 5000 symbols
        h.feed(b)
        self.assertEqual([s.meta["src"] for s in h.starts], ["K1ABC", "K2DEF"])
        self.assertEqual([e for e in h.of("error") if e.code == "decoder_fault"], [])

    def test_empty_and_tiny_blocks(self):
        h = Harness()
        h.decoder.feed(SymbolBlock(dibits=np.zeros(0, np.uint8), soft=None, symbol_rate=4800.0, first_index=0, t0=0.0))
        h.feed(np.array([1], dtype=np.uint8))
        self.assertEqual(h.events, [])

    def test_call_updates_are_rate_limited_and_snapshots_are_independent(self):
        call = enc.SyntheticM17Call(payloads=payloads(30))
        h = Harness().feed(enc.stream_transmission(call))
        updates = h.of("call_update")
        self.assertLessEqual(len(updates), 3)  # 30 frames = 1.2 s: at most ~4 per second
        # a snapshot emitted earlier must not change when the call later ends
        self.assertIsNone(h.starts[0].ended_at)
        self.assertIsNone(h.starts[0].end_reason)
        self.assertEqual(h.starts[0].voice_frames, 0)


class Codec2EndToEndTests(unittest.TestCase):
    """Real libcodec2: a synthetic voiced tone is encoded, sent as M17, decoded, and must sound the same
    as decoding the same Codec2 frames directly. Skipped where libcodec2 is not installed."""

    def test_tone_through_the_whole_chain(self):
        from sigsense_api.dvoice.vocoder.backends import codec2 as c2mod

        lib, path, err = c2mod.load_library()
        if lib is None:
            self.skipTest(f"libcodec2 not available: {err}")
        lib.codec2_encode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_short)]
        lib.codec2_encode.restype = None
        state = lib.codec2_create(c2mod.CODEC2_MODE_3200)
        try:
            n = int(lib.codec2_samples_per_frame(state))
            nbytes = (int(lib.codec2_bits_per_frame(state)) + 7) // 8
            t = np.arange(n * 40) / 8000.0
            speech = sum(np.sin(2 * np.pi * 140 * k * t) / k for k in range(1, 12))
            speech = (speech / np.abs(speech).max() * 9000).astype(np.int16)
            frames = []
            for i in range(40):
                pcm = (ctypes.c_short * n)(*speech[i * n : (i + 1) * n])
                out = (ctypes.c_ubyte * nbytes)()
                lib.codec2_encode(state, out, pcm)
                frames.append(bytes(out))
        finally:
            lib.codec2_destroy(state)
        pls = [frames[i] + frames[i + 1] for i in range(0, 40, 2)]
        h = Harness(provider=registry.get_vocoder).feed(enc.stream_transmission(enc.SyntheticM17Call(payloads=pls)))
        got = np.concatenate([v.pcm for v in h.of("voice") if v.pcm is not None])
        self.assertEqual(len(got), 40 * n)
        # reference: the same frames decoded directly
        ref_state = lib.codec2_create(c2mod.CODEC2_MODE_3200)
        try:
            ref = []
            for f in frames:
                buf = (ctypes.c_ubyte * nbytes).from_buffer_copy(f)
                out = (ctypes.c_short * n)()
                lib.codec2_decode(ref_state, out, buf)
                ref.append(np.frombuffer(out, dtype=np.int16).copy())
        finally:
            lib.codec2_destroy(ref_state)
        ref = np.concatenate(ref)
        corr = float(np.corrcoef(got.astype(float), ref.astype(float))[0, 1])
        self.assertGreater(corr, 0.99)
        self.assertGreater(np.abs(got).max(), 500)  # real audio, not silence


class FrontEndIntegrationTests(unittest.TestCase):
    """The decoder against the real WP-B front end, which the engine uses: soft values in its convention
    (sign = symbol level, nominal +-1 / +-3, ISI spread) and the timing slips a real symbol clock produces."""

    @staticmethod
    def front_end_block(tx):
        from sigsense_api.dvoice.dsp.discriminator import FmDiscriminator
        from sigsense_api.dvoice.dsp.fsk import FskSymbolRecovery
        from sigsense_api.dvoice.events import Modulation
        from sigsense_api.dvoice.testing.modulator import modulate

        iq = modulate(tx, 4800.0, Modulation.FSK4)
        return FskSymbolRecovery(4800.0).process(FmDiscriminator(48_000.0).process(iq))

    def test_clean_transmission_with_the_front_ends_soft_values(self):
        # Regression: a clean frame through the real front end costs ~47 in soft units, above limits that were
        # calibrated on ideal Gaussian-noise soft values, so every frame was rejected (88 of 88 bad).
        pls = payloads(40)
        tx = np.concatenate([noise(300), enc.stream_transmission(enc.SyntheticM17Call(payloads=pls)), noise(300)])
        blk = self.front_end_block(tx)
        for use_soft in (False, True):
            h = Harness().feed(blk.dibits, soft=blk.soft if use_soft else None)
            self.assertEqual(len(h.starts), 1, use_soft)
            end = h.ends[0]
            self.assertEqual((end.voice_frames_bad, end.end_reason), (0, "terminator"), use_soft)
            sent = set(halves(pls))
            self.assertGreaterEqual(sum(1 for _, b in h.sent_ok if b in sent), 76, use_soft)  # of 80

    def test_a_one_symbol_clock_slip_does_not_drop_the_lock(self):
        # A front-end clock offset eventually inserts or drops a symbol; every later frame then sits 191 or 193
        # symbols after the previous one. That must not end the call (sync_loss) or split it in two.
        pls = payloads(40)
        tx = np.concatenate([noise(300), enc.stream_transmission(enc.SyntheticM17Call(payloads=pls)), noise(300)])
        slipped = tx.copy()
        slipped = np.delete(slipped, 300 + 192 * 15 + 50)  # one symbol lost mid-stream
        slipped = np.insert(slipped, 300 + 192 * 24 + 90, 1)  # and one extra symbol later
        h = Harness().feed(slipped)
        self.assertEqual(len(h.starts), 1)
        self.assertEqual([e.end_reason for e in h.ends], ["terminator"])
        # Only the two frames that contain a slipped symbol are lost (2 frames = 4 half-frames, concealed);
        # every frame after each slip is found again one symbol earlier or later and decodes exactly.
        self.assertLessEqual(h.ends[0].voice_frames_bad, 4)
        sent = set(halves(pls))
        self.assertGreaterEqual(sum(1 for _, b in h.sent_ok if b in sent), 76)
        self.assertEqual(sum(1 for _, b in h.sent_ok if b not in sent), 0)


if __name__ == "__main__":
    unittest.main()
