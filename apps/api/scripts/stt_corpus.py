"""Build a radio-realistic speech test corpus.

    python apps/api/scripts/stt_corpus.py --out DIR [--voices 2] [--limit N]

Synthesises radio-style phrases (callsigns, numbers, NATO alphabet, GMRS
chatter) with Kokoro TTS, then pushes them through a simulated narrowband FM
link and the repo's own demodulator, so the audio the speech model sees has the
same character as live radio:

  speech -> 300-3400 Hz channel audio -> NBFM at 2 MS/s (+100 kHz offset)
         -> AWGN at a chosen in-channel SNR -> sigsense_api.demod (NBFM, 12.5 kHz)
         -> 48 kHz int16

For each SNR it writes
  clips/<snr>/<id>.wav          one transmission each (for offline model scoring)
  stream_<snr>.wav + .json      the same clips back to back with the carrier OFF
                                between them, so the demodulator outputs open-
                                squelch hiss in the gaps (what the HackRF path
                                actually delivers), with ground-truth timings.
Requires: kokoro-onnx, soundfile, and the cached speaches-ai/Kokoro-82M-v1.0-ONNX.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sigsense_api.demod import Demodulator  # noqa: E402

FS_RF = 2_000_000
OFFSET_HZ = 100_000
DEVIATION_HZ = 2_500.0
CHANNEL_BW = 12_500.0

# Spoken-form text: what is said is what is scored.
PHRASES = [
    "kilo alpha bravo one two three four this is kilo alpha bravo five six seven eight over",
    "roger that ten four we are moving to channel fifteen",
    "alpha bravo charlie delta echo foxtrot",
    "this is whiskey quebec xray yankee six five seven calling base come in",
    "copy that we will be there in about twenty minutes over",
    "all units be advised the road is closed at mile marker forty two",
    "base to unit seven what is your location over",
    "negative i am at the north gate waiting for the truck",
    "radio check one two three how do you read me",
    "i read you loud and clear over",
    "we have a flat tire on highway nine requesting assistance",
    "stand by one moment while i check the schedule",
    "unit twelve is clear and returning to base",
    "please repeat your last transmission you were breaking up",
    "i say again the meeting is at fourteen thirty",
    "go ahead with your traffic",
    "tango uniform victor whiskey x ray yankee zulu",
    "juliet kilo lima mike november oscar papa quebec romeo sierra",
    "the package will arrive at four fifteen on dock three",
    "roger wilco out",
    "this is november two niner six requesting a radio check",
    "weather looks clear with winds from the west at ten knots",
    "copy ten four we are monitoring channel twenty",
    "can you send someone to the front entrance",
    "all stations all stations this is a test of the emergency network",
    "kilo lima nine eight seven is on frequency",
    "we need two more people at gate three over",
    "affirmative proceed to the rendezvous point",
    "break break priority traffic for unit four",
    "thank you for the assist have a good night",
]
VOICES = ["am_adam", "af_heart", "bf_emma", "bm_george"]
SNRS = [10, 15, 25]


def kokoro():
    from kokoro_onnx import Kokoro

    base = glob.glob(os.path.expanduser("~/.cache/huggingface/hub/models--speaches-ai--Kokoro-82M-v1.0-ONNX/snapshots/*/"))[0]
    return Kokoro(base + "model.onnx", base + "voices.bin")


def channel_audio(x24k: np.ndarray) -> np.ndarray:
    """Speech as a narrowband radio would carry it: 300-3400 Hz, mild compression."""
    sos = signal.butter(4, [300, 3400], btype="bandpass", fs=24000, output="sos")
    y = signal.sosfilt(sos, x24k)
    y = np.tanh(1.8 * y / (np.max(np.abs(y)) + 1e-9)) / np.tanh(1.8)  # mic AGC/limiter
    return (0.9 * y).astype(np.float64)


def fm_link(audio24k: np.ndarray, carrier_on: np.ndarray, snr_db: float, rng) -> np.ndarray:
    """FM-modulate at FS_RF and add noise so SNR in a 12.5 kHz channel is `snr_db`.

    `carrier_on` is a boolean mask at the audio rate: the carrier is keyed
    (PTT), and between transmissions only noise reaches the receiver.
    """
    audio_rf = signal.resample_poly(audio24k, 250, 3)  # 24 kHz -> 2 MS/s
    index = np.minimum(np.arange(len(audio_rf)) * 24000 // FS_RF, len(carrier_on) - 1)
    mask_rf = carrier_on[index]
    phase = 2 * np.pi * (OFFSET_HZ * np.arange(len(audio_rf)) / FS_RF + DEVIATION_HZ * np.cumsum(audio_rf) / FS_RF)
    carrier = np.exp(1j * phase) * mask_rf
    # Noise power in the channel bandwidth -> total noise power across FS_RF.
    noise_total = 10 ** (-snr_db / 10) * (FS_RF / CHANNEL_BW)
    noise = np.sqrt(noise_total / 2) * (rng.normal(size=len(carrier)) + 1j * rng.normal(size=len(carrier)))
    return (carrier + noise).astype(np.complex64)


def demod48k(iq: np.ndarray) -> np.ndarray:
    d = Demodulator(FS_RF, mode="NBFM", bandwidth_hz=CHANNEL_BW, offset_hz=OFFSET_HZ)
    out = []
    for i in range(0, len(iq), 262144):
        out.append(d.process(iq[i : i + 262144]))
    return np.concatenate(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--voices", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--stream-clips", type=int, default=14)
    ap.add_argument("--stream-start", type=int, default=0, help="first phrase index in the stream (for a held-out set)")
    ap.add_argument("--skip-clips", action="store_true", help="only build the continuous streams")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    out = Path(args.out)
    tts = kokoro()

    items = []
    voices = VOICES[: args.voices]
    for pi, text in enumerate(PHRASES):
        voice = voices[pi % len(voices)]
        items.append((f"p{pi:02d}_{voice}", text, voice))
    if args.limit:
        items = items[: args.limit]

    speech = {}
    for cid, text, voice in items:
        samples, sr = tts.create(text, voice=voice, speed=1.0, lang="en-us")
        speech[cid] = (text, signal.resample_poly(samples, 24000, sr) if sr != 24000 else samples)
        print("tts", cid, round(len(speech[cid][1]) / 24000, 1), "s", flush=True)

    meta = {"phrases": {cid: text for cid, (text, _) in speech.items()}, "snrs": SNRS, "clips": {}, "streams": {}}
    for snr in SNRS:
        rng = np.random.default_rng(1000 + snr + args.seed)
        (out / "clips" / str(snr)).mkdir(parents=True, exist_ok=True)
        for cid, (text, x) in ([] if args.skip_clips else speech.items()):
            pad = np.zeros(int(0.25 * 24000))
            audio = np.concatenate([pad, channel_audio(x), pad])
            iq = fm_link(audio, np.ones(len(audio), bool), snr, rng)
            pcm = demod48k(iq)
            sf.write(out / "clips" / str(snr) / f"{cid}.wav", pcm, 48000, subtype="PCM_16")
        # Continuous stream: carrier keyed only during transmissions.
        ids = list(speech)[args.stream_start : args.stream_start + args.stream_clips]
        parts, mask, timeline, t = [], [], [], 0.0
        gap = np.zeros(int(1.6 * 24000))
        parts.append(gap); mask.append(np.zeros(len(gap), bool)); t += 1.6
        for cid in ids:
            text, x = speech[cid]
            ca = channel_audio(x)
            lead = np.zeros(int(0.15 * 24000)); tail = np.zeros(int(0.2 * 24000))
            seg = np.concatenate([lead, ca, tail])
            parts.append(seg); mask.append(np.ones(len(seg), bool))
            timeline.append({"id": cid, "text": text, "speech_start": round(t + 0.15, 2), "speech_end": round(t + 0.15 + len(ca) / 24000, 2)})
            t += len(seg) / 24000
            g = np.zeros(int(rng.uniform(1.4, 2.4) * 24000))
            parts.append(g); mask.append(np.zeros(len(g), bool)); t += len(g) / 24000
        audio = np.concatenate(parts)
        iq = fm_link(audio, np.concatenate(mask), snr, rng)
        pcm = demod48k(iq)
        sf.write(out / f"stream_{snr}.wav", pcm, 48000, subtype="PCM_16")
        meta["streams"][str(snr)] = {"file": f"stream_{snr}.wav", "timeline": timeline, "duration_s": round(len(pcm) / 48000, 2)}
        print("built SNR", snr, "stream", meta["streams"][str(snr)]["duration_s"], "s", flush=True)
    meta["clips"] = {cid: text for cid, (text, _) in speech.items()}
    (out / "corpus.json").write_text(json.dumps(meta, indent=1))
    print("done", out)


if __name__ == "__main__":
    main()
