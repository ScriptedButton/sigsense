"""DMR through the real front end: modulate -> channel -> discriminator -> symbols -> decoder (WP-D x WP-B)."""

import unittest

import numpy as np

from sigsense_api.dvoice.dsp.discriminator import FmDiscriminator
from sigsense_api.dvoice.dsp.fsk import FskSymbolRecovery
from sigsense_api.dvoice.events import Modulation
from sigsense_api.dvoice.protocols.base import ProtocolConfig
from sigsense_api.dvoice.protocols.dmr import lc
from sigsense_api.dvoice.protocols.dmr.decoder import DmrDecoder
from sigsense_api.dvoice.testing.channel import impair
from sigsense_api.dvoice.testing.dmr_encoder import DmrEncoder, assemble_bs_stream
from sigsense_api.dvoice.testing.modulator import modulate
from sigsense_api.dvoice.vocoder import registry

FS = 48_000.0
RATE = 4800.0
DEVIATION = 1944.0  # outer-symbol deviation of DMR 4FSK


def _stream(superframes=4):
    enc = DmrEncoder(cc=7)
    lc9 = lc.build_full_lc(0, 0, bytes([0x00]) + (2001).to_bytes(3, "big") + (31337).to_bytes(3, "big"))
    bursts = enc.voice_call(lc9, superframes=superframes)
    return assemble_bs_stream(bursts, [enc.idle_burst()] * len(bursts), enc, lead_idle=3, tail_idle=10)


def decode_through_front_end(dibits, snr_db, freq_offset_hz=0.0, timing_ppm=0.0, invert=False, seed=1, block=4096):
    iq = modulate(dibits, RATE, Modulation.FSK4, FS, DEVIATION)
    iq = impair(iq, snr_db, freq_offset_hz=freq_offset_hz, timing_ppm=timing_ppm, invert=invert, seed=seed,
                sample_rate=FS, snr_bandwidth_hz=12_500.0)
    disc, rec = FmDiscriminator(FS), FskSymbolRecovery(RATE, FS, 4, Modulation.FSK4)
    events = []
    with registry.use_test_double():
        dec = DmrDecoder(lambda t, p: events.append((t, p)), registry.get_vocoder,
                         ProtocolConfig(channel="e2e", extra={"ambe_channel_decode": lambda r: (r[:49], 0)}))
        for i in range(0, iq.size, block):
            sym = rec.process(disc.process(iq[i : i + block]))
            if sym.dibits.size:
                dec.feed(sym)
    return dec, events


def calls(events, kind):
    return [p for t, p in events if t == kind]


class FrontEndChainTests(unittest.TestCase):
    def test_clean_channel(self):
        dec, ev = decode_through_front_end(_stream(), snr_db=40.0)
        c = calls(ev, "call_start")
        self.assertEqual(len(c), 1)
        self.assertEqual((c[0].target_id, c[0].source_id), (2001, 31337))
        self.assertEqual(calls(ev, "call_end")[0].voice_frames, 72)
        self.assertEqual(dec.stats()["color_code"], 7)

    def test_noisy_offset_and_clock_error(self):
        # 18 dB in 12.5 kHz, +600 Hz carrier offset, +40 ppm symbol clock.
        dec, ev = decode_through_front_end(_stream(), snr_db=18.0, freq_offset_hz=600.0, timing_ppm=40.0)
        c = calls(ev, "call_start")
        self.assertEqual(len(c), 1, dec.stats())
        self.assertEqual((c[0].target_id, c[0].source_id), (2001, 31337))
        ended = calls(ev, "call_end")
        self.assertGreaterEqual(ended[0].voice_frames, 60)

    def test_inverted_carrier(self):
        _, ev = decode_through_front_end(_stream(), snr_db=30.0, invert=True)
        self.assertEqual(len(calls(ev, "call_start")), 1)

    def test_pure_noise_makes_no_calls(self):
        rng = np.random.default_rng(3)
        iq = (rng.standard_normal(480_000) + 1j * rng.standard_normal(480_000)).astype(np.complex64)
        disc, rec = FmDiscriminator(FS), FskSymbolRecovery(RATE, FS, 4, Modulation.FSK4)
        events = []
        dec = DmrDecoder(lambda t, p: events.append((t, p)), registry.get_vocoder, ProtocolConfig(channel="n"))
        for i in range(0, iq.size, 4096):
            sym = rec.process(disc.process(iq[i : i + 4096]))
            if sym.dibits.size:
                dec.feed(sym)
        self.assertEqual(calls(events, "call_start"), [])


if __name__ == "__main__":
    unittest.main()
