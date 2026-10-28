"""Test harness: synthetic spec-based capture generation (CONTRACT v1).

Each protocol WP ships a FRAME ENCODER next to its decoder tests so the decoder
is validated against bit-exact synthetic frames, not only against itself:

  testing/modulator.py        (WP-B)  dibits -> 4FSK/C4FM/GMSK/CQPSK IQ at 48 kHz, + channel impairments
  testing/channel.py          (WP-B)  AWGN at a target SNR, carrier/timing offset, fading, polarity inversion
  testing/p25p1_encoder.py    (WP-C)  full LDU1/LDU2/HDU/TDU/TSBK frame builders (uses fec encoders)
  testing/p25p2_encoder.py    (WP-C2)
  testing/dmr_encoder.py      (WP-D)  voice superframe + embedded LC + CSBK/data builders
  testing/nxdn_encoder.py, dstar_encoder.py, ysf_encoder.py, m17_encoder.py   (WP-E)
  testing/vocoder_vectors.py  (WP-F)  known-answer frames if a backend is present (auto-skip otherwise)

Real-capture tests are optional and gated: files under apps/api/tests/data/dvoice/
(not committed) are used when present, otherwise the test is skipped.
"""
