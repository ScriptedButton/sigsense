"""WP-F: registry, availability and the 'nothing available' path."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import numpy as np

from sigsense_api.dvoice.events import VocoderId
from sigsense_api.dvoice.vocoder import notices, registry
from sigsense_api.dvoice.vocoder.backends import external
from sigsense_api.dvoice.vocoder.backends.ambe_external import Dvsi3000
from sigsense_api.dvoice.vocoder.base import FRAME_SAMPLES, Availability, UnavailableVocoder, Vocoder, VocoderFrame

LIB_ENVS = ("SIGSENSE_MBE_LIB", "SIGSENSE_AMBE_LIB", "SIGSENSE_VOCODER_LIB", "SIGSENSE_DVSI_PORT")


def clean_env():
    env = {k: v for k, v in os.environ.items() if k not in LIB_ENVS}
    env["SIGSENSE_CODEC_HOME"] = "/nonexistent-sigsense-home"  # ignore any library the user really built
    return mock.patch.dict(os.environ, env, clear=True)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        registry.refresh()
        external.forget_adapters()

    def tearDown(self):
        registry.refresh()
        external.forget_adapters()

    def test_every_id_has_the_four_documented_fields(self):
        caps = registry.capabilities()
        self.assertEqual(set(caps), {v.value for v in VocoderId})
        for entry in caps.values():
            self.assertEqual(set(entry), {"available", "reason", "licence_note", "backend"})

    def test_patent_exposed_codecs_are_off_by_default(self):
        with clean_env():
            registry.refresh()
            caps = registry.capabilities()
            for vid in (VocoderId.IMBE_7200X4400, VocoderId.AMBE_3600X2400, VocoderId.AMBE_PLUS2_3600X2450):
                self.assertFalse(caps[vid.value]["available"], vid)
                self.assertTrue(caps[vid.value]["licence_note"], vid)
                self.assertIn("not", caps[vid.value]["reason"].lower())
            vocoder = registry.get_vocoder(VocoderId.AMBE_PLUS2_3600X2450)
            self.assertIsInstance(vocoder, UnavailableVocoder)
            pcm = vocoder.decode(VocoderFrame(VocoderId.AMBE_PLUS2_3600X2450, np.zeros(49, np.uint8)))
            self.assertEqual((pcm.dtype, pcm.shape), (np.dtype("int16"), (FRAME_SAMPLES,)))
            self.assertFalse(pcm.any())

    def test_ambe_plus2_reason_lists_every_route(self):
        with clean_env():
            registry.refresh()
            reason = registry.capabilities()[VocoderId.AMBE_PLUS2_3600X2450.value]["reason"]
            self.assertIn("SIGSENSE_AMBE_LIB", reason)
            self.assertIn("DVSI", reason)

    def test_dvsi_backend_is_an_honest_stub(self):
        with clean_env():
            avail = Dvsi3000.availability()
            self.assertFalse(avail.available)
            self.assertIn("not implemented", avail.reason)
        with mock.patch.dict(os.environ, {"SIGSENSE_DVSI_PORT": "/dev/ttyUSB0"}):
            self.assertFalse(Dvsi3000.availability().available)  # configuring a port must not make it claim to work
        with self.assertRaises(NotImplementedError):
            Dvsi3000().decode(VocoderFrame(VocoderId.AMBE_PLUS2_3600X2450, np.zeros(49, np.uint8)))

    def test_broken_probe_never_propagates(self):
        class Broken(Vocoder):
            id = VocoderId.IMBE_7100X4400

            @classmethod
            def availability(cls):
                raise RuntimeError("boom")

            def decode(self, frame):
                raise AssertionError("must not be selected")

        registry.register(Broken)
        try:
            registry.refresh()
            self.assertIsInstance(registry.get_vocoder(VocoderId.IMBE_7100X4400), UnavailableVocoder)
            entry = registry.capabilities()[VocoderId.IMBE_7100X4400.value]
            self.assertFalse(entry["available"])
            self.assertIn("boom", entry["reason"])
        finally:
            registry.unregister(Broken)

    def test_availability_is_cached_then_refreshable(self):
        calls = []

        class Counting(Vocoder):
            id = VocoderId.IMBE_7100X4400

            @classmethod
            def availability(cls):
                calls.append(1)
                return Availability(True, "x", "note", "counting")

            def decode(self, frame):
                return np.zeros(FRAME_SAMPLES, np.int16)

        registry.register(Counting)
        try:
            registry.refresh()
            registry.get_vocoder(VocoderId.IMBE_7100X4400)
            registry.get_vocoder(VocoderId.IMBE_7100X4400)
            self.assertEqual(len(calls), 1)
            registry.refresh()
            registry.get_vocoder(VocoderId.IMBE_7100X4400)
            self.assertEqual(len(calls), 2)
        finally:
            registry.unregister(Counting)

    def test_priority_orders_backends(self):
        class Late(Vocoder):
            id = VocoderId.IMBE_7100X4400
            priority = 90

            @classmethod
            def availability(cls):
                return Availability(True, "", "n", "late")

            def decode(self, frame):
                return np.zeros(FRAME_SAMPLES, np.int16)

        class Early(Late):
            priority = 10

            @classmethod
            def availability(cls):
                return Availability(True, "", "n", "early")

        registry.register(Late)
        registry.register(Early)
        try:
            registry.refresh()
            self.assertEqual(registry.backends_for(VocoderId.IMBE_7100X4400)[0], Early)
            self.assertIs(type(registry.get_vocoder(VocoderId.IMBE_7100X4400)), Early)
            self.assertIs(type(registry.get_vocoder(VocoderId.IMBE_7100X4400, prefer="late")), Late)
        finally:
            registry.unregister(Late)
            registry.unregister(Early)

    def test_test_double_is_scoped(self):
        with clean_env():
            registry.refresh()
            self.assertIsInstance(registry.get_vocoder(VocoderId.IMBE_7200X4400), UnavailableVocoder)
            with registry.use_test_double([VocoderId.IMBE_7200X4400]):
                vocoder = registry.get_vocoder(VocoderId.IMBE_7200X4400)
                self.assertNotIsInstance(vocoder, UnavailableVocoder)
                self.assertIn("test double", registry.capabilities()["imbe_7200x4400"]["backend"])
            self.assertIsInstance(registry.get_vocoder(VocoderId.IMBE_7200X4400), UnavailableVocoder)


class NoticeTests(unittest.TestCase):
    def test_notices_are_json_and_state_the_policy(self):
        data = notices.licence_notices()
        json.dumps(data)  # must serialise as-is
        self.assertFalse(data["policy"]["ships_ambe"])
        self.assertIn("never attempts to decrypt", data["policy"]["encryption"])
        self.assertTrue(data["backends"])
        for b in data["backends"]:
            self.assertFalse(b["shipped_with_sigsense"], b["name"])
            self.assertTrue(b["licence_note"], b["name"])
            self.assertTrue(b["supplied_by"] and b["licence"], b["name"])

    def test_test_double_never_listed_by_default(self):
        names = {b["name"] for b in notices.licence_notices()["backends"]}
        self.assertFalse(any(n.startswith("ToneVocoder") for n in names))


if __name__ == "__main__":
    unittest.main()
