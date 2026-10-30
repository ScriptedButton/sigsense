"""Bounded, versioned receiver-to-worker audio protocol; never carries radio commands."""
import numpy as np

VERSION = 1
RATE = 16000
BLOCK = 1600
# Fixed timeline across remote models, reconnects and the local fallback.
DELAY = 1600
FORMAT = "f32le"
HELLO = {"version": VERSION, "sampleRate": RATE, "blockSamples": BLOCK, "format": FORMAT}


def valid_token(token: str) -> bool:
    return (32 <= len(token) <= 512 and token.isascii()
            and not any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in token)
            and not token.startswith("REPLACE_"))


def decode_audio(data: bytes) -> np.ndarray:
    if not isinstance(data, bytes) or len(data) != BLOCK * 4:
        raise ValueError("Expected exactly 100 ms of 16 kHz mono float32 audio")
    audio = np.frombuffer(data, dtype="<f4").copy()
    if not np.isfinite(audio).all() or np.max(abs(audio), initial=0) > 4:
        raise ValueError("Invalid audio samples")
    return audio


class Delay:
    def __init__(self, samples: int):
        self.pending = np.zeros(samples, dtype=np.float32)

    def apply(self, audio):
        combined = np.concatenate((self.pending, audio)).astype(np.float32)
        self.pending = combined[len(audio):]
        return combined[:len(audio)]
