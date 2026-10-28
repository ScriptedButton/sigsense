"""P25 Phase 1 end to end: frame encoder -> C4FM modulator -> channel -> discriminator ->
symbol recovery -> protocol decoder (WP-C over WP-B). Skips if the front end is unavailable."""

import unittest

import numpy as np

try:  # unittest discovery puts tests/ on sys.path
    import test_dvoice_dsp_common as C
    from sigsense_api.dvoice.dsp.discriminator import FmDiscriminator
    from sigsense_api.dvoice.dsp.fsk import FskSymbolRecovery
    from sigsense_api.dvoice.testing.channel import impair
    from sigsense_api.dvoice.testing.modulator import modulate

    _FRONT_END = True
except Exception:  # pragma: no cover
    _FRONT_END = False

from sigsense_api.dvoice.events import EncryptionState, VocoderId
from sigsense_api.dvoice.protocols.p25p1.decoder import P25P1Decoder
from sigsense_api.dvoice.testing import p25p1_encoder as enc
from sigsense_api.dvoice.vocoder.base import Availability, Vocoder

NAC = 0x293


class Recorder(Vocoder):
    id = VocoderId.IMBE_7200X4400
    frames: list = []

    @classmethod
    def availability(cls):
        return Availability(True, "recorder", "test", "recorder")

    def decode(self, frame):
        Recorder.frames.append(tuple(int(b) for b in frame.bits))
        return np.zeros(160, dtype=np.int16)


def receive(dibits, snr_db, *, seed=1, **channel):
    rate, mod, levels, dev = C.NOMINAL["p25_c4fm"]
    iq = impair(modulate(dibits, rate, mod, C.FS, dev), snr_db, seed=seed, snr_bandwidth_hz=C.CHANNEL_BW, **channel)
    disc = FmDiscriminator(C.FS, bandwidth_hz=C.CHANNEL_BW)
    rec = FskSymbolRecovery(rate, C.FS, levels, mod)
    events = []
    Recorder.frames = []
    d = P25P1Decoder(lambda t, p: events.append((t, p)), lambda vid: Recorder())
    for i in range(0, len(iq), 4096):
        d.feed(rec.process(disc.process(iq[i : i + 4096])))
    return d, events


@unittest.skipUnless(_FRONT_END, "DSP front end not available")
class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.dib, self.sent = enc.build_voice_call(NAC, tgid=1234, source=0xABCDE, superframes=3, rng=np.random.default_rng(2))
        self.sent_set = {tuple(int(b) for b in s) for s in self.sent}

    def check(self, d, ev, *, min_frames=7, min_voice=50):
        st = d.stats()
        self.assertGreaterEqual(st["frames_ok"], min_frames, st)
        ends = [p for t, p in ev if t == "call_end"]
        self.assertEqual(len(ends), 1)
        self.assertEqual((ends[0].target_id, ends[0].source_id), (1234, 0xABCDE))
        self.assertEqual(ends[0].encryption.state, EncryptionState.CLEAR)
        exact = sum(1 for f in Recorder.frames if f in self.sent_set)
        self.assertGreaterEqual(exact, min_voice, f"only {exact}/54 voice frames bit-exact")

    def test_clean(self):
        d, ev = receive(self.dib, np.inf)
        self.check(d, ev, min_frames=8, min_voice=54)

    def test_snr_25_db(self):
        d, ev = receive(self.dib, 25)
        self.check(d, ev, min_frames=8, min_voice=54)

    def test_snr_20_db(self):
        d, ev = receive(self.dib, 20)
        self.check(d, ev)

    def test_carrier_offset_and_clock_error(self):
        d, ev = receive(self.dib, 28, freq_offset_hz=900.0, timing_ppm=60.0)
        self.check(d, ev)

    def test_inverted_polarity(self):
        d, ev = receive(self.dib, 28, invert=True)
        self.check(d, ev)

    def test_trunking_blocks_survive_the_air_path(self):
        ch = (1 << 12) | 0x10
        tsdu = np.concatenate((
            enc.build_tsdu(NAC, [(0x3D, enc.args_iden_up(1, 12_500, -45_000_000, 12_500, 851_006_250))]),
            enc.build_tsdu(NAC, [(0x00, enc.args_grp_v_ch_grant(0x03, ch, 321, 654))]),
        ))
        d, ev = receive(np.concatenate((self.dib[:0], tsdu, tsdu)), 26)
        grants = [p for t, p in ev if t == "trunk" and p.kind == "grant"]
        self.assertGreaterEqual(len(grants), 1)
        self.assertAlmostEqual(grants[0].frequency_hz, 851_006_250 + 0x10 * 12_500)
        self.assertEqual((grants[0].talkgroup, grants[0].source_id), (321, 654))

    def test_very_low_snr_degrades_without_false_calls(self):
        d, ev = receive(self.dib, 8)
        for t, p in ev:
            if t in ("call_start", "call_end"):
                self.assertIn(p.target_id, (None, 1234))


if __name__ == "__main__":
    unittest.main()
