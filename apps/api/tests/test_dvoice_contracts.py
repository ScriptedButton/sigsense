"""Contract tests for the digital voice package (docs/digital-voice-architecture.md).

These pin the INTERFACES the work packages code against. They deliberately do
not assert that stubs raise NotImplementedError, so a work package can implement
a function without touching this file; they fail only on contract drift
(renamed fields, changed signatures, missing modules, wrong sync words...).
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import pkgutil
import re
import unittest
from pathlib import Path

import numpy as np

import sigsense_api.dvoice as dvoice
from sigsense_api.dvoice import events
from sigsense_api.dvoice.events import (
    AUDIO_HEADER,
    Call,
    CallKind,
    EncryptionInfo,
    EncryptionState,
    ErrorEvent,
    LinkStats,
    Modulation,
    Protocol,
    SyncEvent,
    SystemInfo,
    Talkgroup,
    TrunkEvent,
    VocoderId,
    VoiceFrame,
    make_event,
    pack_audio_frame,
    unpack_audio_frame,
)

WEB_TYPES = Path(__file__).resolve().parents[2] / "web/src/lib/sdr/dvoice/types.ts"


def ts_fields(type_name: str) -> list[str]:
    text = WEB_TYPES.read_text()
    match = re.search(r"export type %s = \{(.*?)\n\};" % re.escape(type_name), text, re.S)
    assert match, f"{type_name} missing from types.ts"
    return re.findall(r"^\s{2}([a-z_0-9]+)\??:", match.group(1), re.M)


class PackageTests(unittest.TestCase):
    def test_every_module_imports(self):
        names = [m.name for m in pkgutil.walk_packages(dvoice.__path__, "sigsense_api.dvoice.")]
        self.assertGreater(len(names), 30)
        for name in names:
            importlib.import_module(name)

    def test_contract_version(self):
        self.assertEqual(dvoice.CONTRACT_VERSION, 1)

    def test_no_key_recovery_surface(self):
        banned = re.compile(r"crack|brute|keyrecover|key_recover|bruteforce", re.I)
        for path in Path(dvoice.__file__).parent.rglob("*.py"):
            self.assertIsNone(banned.search(path.name), path.name)
        fields = {f.name for f in dataclasses.fields(EncryptionInfo)}
        self.assertFalse({"key", "key_bytes", "keystream", "secret"} & fields)


class EventContractTests(unittest.TestCase):
    PAIRS = {
        "DvEncryptionInfo": EncryptionInfo,
        "DvSystemInfo": SystemInfo,
        "DvTalkgroup": Talkgroup,
        "DvCall": Call,
        "DvLinkStats": LinkStats,
        "DvSyncEvent": SyncEvent,
        "DvTrunkEvent": TrunkEvent,
        "DvError": ErrorEvent,
    }

    def test_typescript_mirror_matches_python_fields(self):
        for ts_name, cls in self.PAIRS.items():
            py = [f.name for f in dataclasses.fields(cls)]
            self.assertEqual(sorted(py), sorted(ts_fields(ts_name)), ts_name)

    def test_protocol_values_mirrored(self):
        text = WEB_TYPES.read_text()
        block = re.search(r"export type DvProtocol =(.*?);", text, re.S).group(1)
        self.assertEqual(sorted(re.findall(r'"([a-z0-9]+)"', block)), sorted(p.value for p in Protocol))

    def test_envelope(self):
        event = make_event("call_start", Call(call_id="c1", protocol=Protocol.DMR), channel="ch1", t=1.5)
        self.assertEqual(event["v"], 1)
        self.assertEqual(event["t"], 1500)
        self.assertEqual(event["channel"], "ch1")
        self.assertEqual(event["data"]["protocol"], "dmr")
        self.assertEqual(event["data"]["kind"], CallKind.UNKNOWN.value)
        self.assertEqual(event["data"]["encryption"]["state"], EncryptionState.UNKNOWN.value)
        with self.assertRaises(ValueError):
            make_event("nope", {})

    def test_voice_pcm_is_not_json(self):
        frame = VoiceFrame(call_id="c", seq=1, vocoder=VocoderId.IMBE_7200X4400, pcm=np.zeros(160, np.int16))
        self.assertNotIn("pcm", events.to_jsonable(frame))

    def test_audio_frame_round_trip(self):
        pcm = (np.arange(160) - 80).astype(np.int16)
        data = pack_audio_frame(41, 7, pcm, slot=1, end_of_call=True, had_errors=True)
        self.assertEqual(len(data), AUDIO_HEADER.size + 320)
        self.assertEqual(AUDIO_HEADER.size, 16)
        meta, out = unpack_audio_frame(data)
        self.assertEqual((meta["call_num"], meta["seq"], meta["slot"]), (41, 7, 1))
        self.assertTrue(meta["end_of_call"] and meta["had_errors"])
        np.testing.assert_array_equal(out, pcm)
        self.assertIsNone(unpack_audio_frame(pack_audio_frame(1, 1, pcm))[0]["slot"])
        with self.assertRaises(ValueError):
            unpack_audio_frame(b"\x00" * 32)
        with self.assertRaises(ValueError):
            unpack_audio_frame(data[:-2])


class FecContractTests(unittest.TestCase):
    SIGNATURES = {
        "crc": {"crc_compute": ["name", "bits"], "crc_check": ["name", "bits_with_crc"]},
        "golay": {
            "golay_23_12_encode": ["data12"],
            "golay_23_12_decode": ["word23"],
            "golay_24_12_encode": ["data12"],
            "golay_24_12_decode": ["word24"],
            "golay_18_6_decode": ["word18"],
            "golay_20_8_encode": ["data8"],
            "golay_20_8_decode": ["word20"],
        },
        "hamming": {
            f"hamming_{code}_{kind}": ["word%s" % code.split("_")[0]] if kind == "decode" else ["data%s" % code.split("_")[1]]
            for code in ("7_4", "10_6_3", "13_9_3", "15_11_3", "16_11_4", "17_12_3")
            for kind in ("encode", "decode")
        },
        "bch": {
            "p25_nid_encode": ["nac12", "duid4"],
            "p25_nid_decode": ["word64"],
            "qr_16_7_6_encode": ["data7"],
            "qr_16_7_6_decode": ["word16"],
            "bch_15_7_5_decode": ["word15"],
        },
        "bptc": {
            "bptc_196_96_decode": ["bits196"],
            "bptc_196_96_encode": ["bits96"],
            "embedded_lc_decode": ["bits128"],
            "embedded_lc_encode": ["bits72"],
        },
        "viterbi": {"conv_encode": ["code", "bits"], "viterbi_decode": ["code", "received", "soft"]},
        "trellis": {
            "p25_trellis_half_decode": ["dibits"],
            "p25_trellis_half_encode": ["data_bits"],
            "p25_trellis_three_quarter_decode": ["dibits"],
            "p25_trellis_three_quarter_encode": ["data_bits"],
            "dmr_trellis_three_quarter_decode": ["dibits"],
            "dmr_trellis_three_quarter_encode": ["data_bits"],
        },
        "interleave": {
            "table": ["name"],
            "deinterleave": ["bits", "perm"],
            "interleave": ["bits", "perm"],
            "block_permutation": ["rows", "cols"],
        },
        "whitening": {
            "lfsr_sequence": ["length", "taps", "seed", "width"],
            "p25p2_scramble_sequence": ["wacn", "system_id", "nac", "length"],
            "nxdn_scramble_sequence": ["length"],
            "m17_randomizer": ["length"],
            "dstar_scrambler": ["length"],
            "ysf_whitening": ["length"],
        },
    }

    def test_signatures(self):
        for module_name, funcs in self.SIGNATURES.items():
            module = importlib.import_module(f"sigsense_api.dvoice.fec.{module_name}")
            for func_name, params in funcs.items():
                fn = getattr(module, func_name)
                self.assertEqual(list(inspect.signature(fn).parameters), params, f"{module_name}.{func_name}")

    def test_reed_solomon_surface(self):
        from sigsense_api.dvoice.fec import reed_solomon as rs

        self.assertEqual(
            list(inspect.signature(rs.ReedSolomon.__init__).parameters),
            ["self", "m", "prim_poly", "n", "k", "fcr", "prim"],
        )
        for name in ("rs_p25_24_12_13", "rs_p25_24_16_9", "rs_p25_36_20_17", "rs_dmr_12_9_4"):
            self.assertTrue(callable(getattr(rs, name)))
        for name in ("encode", "decode"):
            self.assertTrue(callable(getattr(rs.ReedSolomon, name)))

    def test_crc_catalogue_check_values(self):
        from sigsense_api.dvoice.fec import crc

        bits = crc.bytes_to_bits(b"123456789")
        for name, expected in (("crc16_ccitt_false", 0x29B1), ("crc16_genibus", 0xD64E), ("crc16_x25", 0x906E)):
            self.assertEqual(crc.crc_compute(name, bits), expected, name)
        body = crc.bytes_to_bits(b"sigsense")
        value = crc.crc_compute("crc16_ccitt_false", body)
        framed = np.concatenate([body, np.array([int(c) for c in f"{value:016b}"], np.uint8)])
        self.assertTrue(crc.crc_check("crc16_ccitt_false", framed))
        framed[3] ^= 1
        self.assertFalse(crc.crc_check("crc16_ccitt_false", framed))

    def test_interleave_helpers(self):
        from sigsense_api.dvoice.fec import interleave

        perm = interleave.block_permutation(5, 7)
        x = np.arange(35)
        np.testing.assert_array_equal(interleave.interleave(interleave.deinterleave(x, perm), perm), x)

    def test_lfsr_is_deterministic_and_balanced_enough(self):
        from sigsense_api.dvoice.fec import whitening

        a = whitening.lfsr_sequence(511, (9, 5), 0x1FF, 9)
        np.testing.assert_array_equal(a, whitening.lfsr_sequence(511, (9, 5), 0x1FF, 9))
        self.assertEqual(int(a.sum()), 256)  # maximal-length 9-bit LFSR: 256 ones per 511

    def test_fec_result(self):
        from sigsense_api.dvoice.fec.types import FecResult

        self.assertEqual([f.name for f in dataclasses.fields(FecResult)], ["data", "corrected", "ok"])


class SyncContractTests(unittest.TestCase):
    HEX = {
        "p25p1": "0x5575F5FF77FF",
        "dmr_bs_voice": "0x755FD7DF75F7",
        "dmr_bs_data": "0xDFF57D75DF5D",
        "dmr_ms_voice": "0x7F7D5DD57DFD",
        "dmr_ms_data": "0xD5D7F77FD757",
        "ysf_fsw": "0xD471C9634D",
        "m17_lsf": "0x55F7",
        "m17_stream": "0xFF5D",
        "m17_bert": "0xDF55",
        "m17_packet": "0x75FF",
    }
    # Sign-only strings published by the reference architecture (1 = positive symbol, 3 = negative).
    REFERENCE_SIGNS = {
        "p25p1": "111113113311333313133333",
        "dmr_bs_voice": "131111333113313313113313",
        "ysf_fsw": "31111311313113131131",
        "m17_lsf": "11113313",
        "nxdn_fsw": "3131331131",
    }

    def test_hex_forms(self):
        from sigsense_api.dvoice.sync.patterns import SYNC_SPECS, dibits_to_hex

        for name, expected in self.HEX.items():
            self.assertEqual(dibits_to_hex(SYNC_SPECS[name].dibits), expected, name)

    def test_sign_forms_match_reference_table(self):
        from sigsense_api.dvoice.sync.patterns import SYNC_SPECS

        for name, expected in self.REFERENCE_SIGNS.items():
            signs = "".join("1" if v == 0 else "3" for v in SYNC_SPECS[name].signs)
            self.assertEqual(signs, expected, name)

    def test_inversion_and_lengths(self):
        from sigsense_api.dvoice.sync.patterns import SYNC_SPECS

        for spec in SYNC_SPECS.values():
            self.assertTrue(all(0 <= v <= 3 for v in spec.values), spec.name)
            self.assertEqual([v ^ 2 for v in spec.values], list(spec.inverted_values))
        self.assertEqual(len(SYNC_SPECS["p25p1"].dibits), 24)
        self.assertEqual(len(SYNC_SPECS["p25p2"].dibits), 20)
        self.assertEqual(len(SYNC_SPECS["nxdn_fsw"].dibits), 10)
        self.assertEqual(len(SYNC_SPECS["m17_lsf"].dibits), 8)


class ProtocolContractTests(unittest.TestCase):
    EXPECTED = {
        "p25p1": (4800.0, 1, Modulation.C4FM),
        "p25p2": (6000.0, 2, Modulation.CQPSK),
        "dmr": (4800.0, 2, Modulation.FSK4),
        "nxdn48": (2400.0, 1, Modulation.FSK4),
        "nxdn96": (4800.0, 1, Modulation.FSK4),
        "dstar": (4800.0, 1, Modulation.GMSK),
        "ysf": (4800.0, 1, Modulation.C4FM),
        "m17": (4800.0, 1, Modulation.FSK4),
    }

    def test_registry_and_attributes(self):
        from sigsense_api.dvoice.protocols import all_decoders
        from sigsense_api.dvoice.protocols.base import ProtocolDecoder
        from sigsense_api.dvoice.sync.patterns import SYNC_SPECS

        decoders = all_decoders()
        self.assertEqual(set(decoders), set(self.EXPECTED))
        for key, (rate, slots, modulation) in self.EXPECTED.items():
            cls = decoders[key]
            self.assertTrue(issubclass(cls, ProtocolDecoder), key)
            self.assertEqual((cls.symbol_rate, cls.timeslots, cls.modulation), (rate, slots, modulation), key)
            self.assertTrue(cls.sync_specs and all(s in SYNC_SPECS for s in cls.sync_specs), key)
            self.assertEqual(
                list(inspect.signature(cls.__init__).parameters), ["self", "emit", "vocoders", "config"], key
            )
            for method in ("feed", "reset", "stats"):
                self.assertTrue(callable(getattr(cls, method)), f"{key}.{method}")

    def test_symbol_block_fields(self):
        from sigsense_api.dvoice.dsp.types import SymbolBlock

        self.assertEqual(
            [f.name for f in dataclasses.fields(SymbolBlock)],
            ["dibits", "soft", "symbol_rate", "first_index", "t0", "modulation", "polarity_inverted", "meta"],
        )


class VocoderContractTests(unittest.TestCase):
    def test_registry_covers_every_id(self):
        from sigsense_api.dvoice.vocoder import registry

        caps = registry.capabilities()
        self.assertEqual(set(caps), {v.value for v in VocoderId})
        for entry in caps.values():
            self.assertEqual(set(entry), {"available", "reason", "licence_note", "backend"})

    def test_unavailable_vocoder_is_silent_not_fatal(self):
        from sigsense_api.dvoice.vocoder import base, registry

        vocoder = registry.get_vocoder(VocoderId.CODEC2_3200)
        frame = base.VocoderFrame(VocoderId.CODEC2_3200, np.zeros(49, np.uint8))
        pcm = vocoder.decode(frame)
        self.assertEqual((pcm.dtype, pcm.shape), (np.dtype("int16"), (base.FRAME_SAMPLES,)))

    def test_backend_classes(self):
        from sigsense_api.dvoice.vocoder.backends import ambe_external, codec2, imbe_external
        from sigsense_api.dvoice.vocoder.base import Vocoder

        for cls in (
            imbe_external.ExternalImbe,
            ambe_external.ExternalAmbePlus2,
            ambe_external.ExternalAmbe,
            ambe_external.Dvsi3000,
            codec2.Codec2Vocoder,
        ):
            self.assertTrue(issubclass(cls, Vocoder), cls.__name__)
            self.assertIsInstance(cls.id, VocoderId)
            self.assertTrue(callable(cls.availability))


class EngineContractTests(unittest.TestCase):
    def test_engine_surface(self):
        from sigsense_api.dvoice import engine

        self.assertEqual((engine.INPUT_IQ16_48K, engine.INPUT_DISC16_48K), (1, 2))
        methods = {
            "start": ["self"],
            "stop": ["self"],
            "configure": ["self", "config"],
            "feed_iq": ["self", "iq", "sample_rate", "center_hz", "channel"],
            "feed_upload": ["self", "kind", "payload", "channel"],
            "status": ["self"],
            "subscribe_audio": ["self", "callback"],
        }
        for name, params in methods.items():
            self.assertEqual(list(inspect.signature(getattr(engine.DigitalVoiceEngine, name)).parameters), params, name)
        self.assertEqual(
            list(inspect.signature(engine.DigitalVoiceEngine.__init__).parameters),
            ["self", "config", "on_event", "on_audio"],
        )

    def test_default_config_policy(self):
        from sigsense_api.dvoice.engine import EngineConfig

        config = EngineConfig()
        self.assertTrue(config.play_unencrypted_only)
        self.assertTrue(config.follow.skip_encrypted)
        self.assertEqual(len(config.protocols), 8)
        self.assertIsNone(config.record_dir)  # recording is opt-in

    def test_recorder_api_and_trunking(self):
        from sigsense_api.dvoice import api, recorder
        from sigsense_api.dvoice.trunking import base, dmr, p25

        for name in ("on_call", "on_voice", "list_calls", "wav_path", "export"):
            self.assertTrue(callable(getattr(recorder.CallRecorder, name)))
        self.assertEqual(list(inspect.signature(api.build_router).parameters), ["get_engine", "get_capture"])
        self.assertTrue(issubclass(p25.P25TrunkController, base.TrunkController))
        self.assertTrue(issubclass(dmr.DmrTrunkController, base.TrunkController))
        self.assertTrue(base.FollowPolicy().skip_encrypted)


if __name__ == "__main__":
    unittest.main()
