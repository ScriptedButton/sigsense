#!/usr/bin/env python3
"""Check the DMR AMBE+2 voice-frame mapping against a REAL capture.

The unit tests only prove voice_map.py is self-consistent. This script is the check against over-the-air
traffic: it decodes a capture of a DMR channel with the SigSense front end and counts how many 72-bit
voice frames pass the AMBE+2 channel code cleanly.

  * With the right mapping, frames from a decently strong signal need 0-1 Golay corrections.
  * With a wrong mapping, a frame decodes like a random word: about 5-6 corrections per frame, and almost
    no frame (about 1 in 1000) comes out clean by chance. The script prints that control next to the result.

Record with the HackRF (example: a local DMR repeater or simplex channel at 2 MS/s, a minute or two
while there is traffic), then run:

    python3 apps/api/scripts/verify_dmr_voice_map.py capture.iq --format cs8 --rate 2000000 --offset-hz 0

  --format   cs8 (HackRF int8 I/Q, e.g. `hackrf_transfer -r`), cu8 (RTL-SDR), cs16, cf32
  --rate     capture sample rate in Hz
  --offset-hz  frequency of the DMR channel relative to the capture centre (Hz)

Exit status is 0 when the mapping passes (see PASS_* below) and 1 otherwise; a capture with no DMR
voice at all reports "not enough voice frames" with status 2.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sigsense_api.dvoice.dsp.channel import Channelizer  # noqa: E402
from sigsense_api.dvoice.dsp.discriminator import FmDiscriminator  # noqa: E402
from sigsense_api.dvoice.dsp.fsk import FskSymbolRecovery  # noqa: E402
from sigsense_api.dvoice.events import Modulation  # noqa: E402
from sigsense_api.dvoice.protocols.base import ProtocolConfig  # noqa: E402
from sigsense_api.dvoice.protocols.dmr import voice_map  # noqa: E402
from sigsense_api.dvoice.protocols.dmr.decoder import DmrDecoder  # noqa: E402
from sigsense_api.dvoice.vocoder import registry  # noqa: E402

MIN_FRAMES = 30
PASS_CLEAN_FRACTION = 0.40  # >= 40% of voice frames with <= 1 Golay correction
CHANNEL_RATE = 48_000.0
CHUNK = 1 << 20


def read_iq(path: str, fmt: str, count: int | None = None, offset: int = 0) -> np.ndarray:
    """Read `count` complex samples starting at sample `offset` (None = to the end)."""
    per = {"cs8": 2, "cu8": 2, "cs16": 4, "cf32": 8}[fmt]
    with open(path, "rb") as fh:
        fh.seek(offset * per)
        n = -1 if count is None else count
        if fmt == "cs8":
            raw = np.fromfile(fh, dtype=np.int8, count=n * 2 if n > 0 else -1).astype(np.float32) / 128.0
        elif fmt == "cu8":
            raw = (np.fromfile(fh, dtype=np.uint8, count=n * 2 if n > 0 else -1).astype(np.float32) - 127.5) / 127.5
        elif fmt == "cs16":
            raw = np.fromfile(fh, dtype=np.int16, count=n * 2 if n > 0 else -1).astype(np.float32) / 32768.0
        else:
            raw = np.fromfile(fh, dtype=np.float32, count=n * 2 if n > 0 else -1)
    raw = raw[: raw.size // 2 * 2]
    return (raw[0::2] + 1j * raw[1::2]).astype(np.complex64)


def _swap_dibit_bits(bits72: np.ndarray) -> np.ndarray:
    out = bits72.copy()
    out[0::2], out[1::2] = bits72[1::2], bits72[0::2]
    return out


def analyze(iq_chunks, sample_rate: float, offset_hz: float = 0.0, seed: int = 1) -> dict:
    """Run a capture (iterable of complex64 chunks) through the DMR path and score the voice map."""
    chan = Channelizer(sample_rate, offset_hz=offset_hz, bandwidth_hz=12_500.0, out_rate=CHANNEL_RATE)
    disc = FmDiscriminator(CHANNEL_RATE)
    rec = FskSymbolRecovery(4800.0, CHANNEL_RATE, 4, Modulation.FSK4)
    frames: list[np.ndarray] = []

    def hook(raw72):
        frames.append(np.asarray(raw72, dtype=np.uint8).copy())
        return None  # no audio is needed for the check

    events: list = []
    with registry.use_test_double():
        dec = DmrDecoder(
            lambda kind, payload: events.append(kind),
            registry.get_vocoder,
            ProtocolConfig(channel="verify", extra={"ambe_channel_decode": hook}),
        )
        for chunk in iq_chunks:
            sym = rec.process(disc.process(chan.process(chunk)))
            if sym.dibits.size:
                dec.feed(sym)

    # Control: the same frames read through a shuffled permutation of the table.
    rng = np.random.default_rng(seed)
    shuffled = list(voice_map.AIR_TO_CELL)
    rng.shuffle(shuffled)
    crow = np.array([r for r, _ in shuffled], dtype=np.intp)
    ccol = np.array([c for _, c in shuffled], dtype=np.intp)

    def errs(frame72: np.ndarray, hypothesis: str) -> int:
        if hypothesis == "table":
            return voice_map.decode_air72(frame72)[1]
        if hypothesis == "dibit-bits-swapped":
            return voice_map.decode_air72(_swap_dibit_bits(frame72))[1]
        f = np.zeros((4, 24), dtype=np.uint8)
        f[crow, ccol] = frame72
        return voice_map.decode_ambe2450_frame(f)[1]

    result = {"voice_frames": len(frames), "calls_started": events.count("call_start"), "hypotheses": {}}
    for h in ("table", "dibit-bits-swapped", "control-shuffled"):
        e = np.array([errs(f, h) for f in frames]) if frames else np.zeros(0)
        result["hypotheses"][h] = {
            "mean_golay_corrections": float(e.mean()) if e.size else None,
            "clean_fraction": float((e <= 1).mean()) if e.size else None,
        }
    return result


def verdict(result: dict) -> tuple[int, str]:
    if result["voice_frames"] < MIN_FRAMES:
        return 2, f"not enough voice frames ({result['voice_frames']} < {MIN_FRAMES}): no DMR voice found in this capture"
    t = result["hypotheses"]["table"]["clean_fraction"]
    c = result["hypotheses"]["control-shuffled"]["clean_fraction"]
    if t >= PASS_CLEAN_FRACTION and t > 10 * max(c, 0.01):
        return 0, "PASS: the mapping decodes real voice frames cleanly (far above the shuffled control)"
    swapped = result["hypotheses"]["dibit-bits-swapped"]["clean_fraction"]
    if swapped >= PASS_CLEAN_FRACTION and swapped > 10 * max(c, 0.01):
        return 1, "FAIL as is, but the frames decode cleanly with the two bits of every dibit swapped: the dibit bit order is reversed in this front end"
    return 1, "FAIL: no better than the shuffled control (weak signal, wrong channel/offset, or the table is wrong)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("capture")
    ap.add_argument("--format", choices=("cs8", "cu8", "cs16", "cf32"), default="cs8")
    ap.add_argument("--rate", type=float, required=True, help="capture sample rate (Hz)")
    ap.add_argument("--offset-hz", type=float, default=0.0, help="DMR channel offset from the capture centre (Hz)")
    ap.add_argument("--seconds", type=float, default=None, help="only use the first N seconds")
    args = ap.parse_args(argv)

    total = None if args.seconds is None else int(args.seconds * args.rate)

    def chunks():
        pos = 0
        while total is None or pos < total:
            n = CHUNK if total is None else min(CHUNK, total - pos)
            iq = read_iq(args.capture, args.format, n, pos)
            if iq.size == 0:
                return
            yield iq
            pos += iq.size

    result = analyze(chunks(), args.rate, args.offset_hz)
    code, text = verdict(result)
    print(f"voice frames decoded: {result['voice_frames']}   calls started: {result['calls_started']}")
    for name, h in result["hypotheses"].items():
        if h["clean_fraction"] is None:
            continue
        print(f"  {name:20s} mean Golay corrections/frame {h['mean_golay_corrections']:5.2f}   "
              f"frames with <=1 correction {100 * h['clean_fraction']:5.1f}%")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
