"""Bounded, local neural speech assessment for the autonomous receiver (not ASR)."""
from __future__ import annotations

import io
import threading
import wave
from functools import lru_cache

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from scipy.signal import resample_poly
from math import gcd

router = APIRouter(prefix="/autofinder")
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(2)
MAX_BYTES = 600_000


@lru_cache(maxsize=1)
def neural_detector():
    from faster_whisper.vad import get_speech_timestamps, get_vad_model, VadOptions
    get_vad_model()  # bundled local ONNX, no speech-model download or external API
    return get_speech_timestamps, VadOptions


def assess_wav(data: bytes, detector=None) -> dict:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Expected a WAV capture no larger than 600 kB")
    try:
        with wave.open(io.BytesIO(data)) as wav:
            rate, frames = wav.getframerate(), wav.getnframes()
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
                raise ValueError("Expected mono PCM16 WAV")
            if not 8000 <= rate <= 48000 or not rate <= frames <= rate * 6:
                raise ValueError("Expected 1–6 seconds of 8–48 kHz audio")
            raw = wav.readframes(frames)
            if len(raw) != frames * 2:
                raise ValueError("Truncated audio")
    except (wave.Error, EOFError) as exc:
        raise ValueError("Invalid WAV capture") from exc
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    rms = float(np.sqrt(np.mean(samples ** 2)))
    clipped = float(np.mean(np.abs(samples) >= .999))
    factor = gcd(rate, 16000)
    samples = resample_poly(samples, 16000 // factor, rate // factor)
    if detector is None:
        detect, options = neural_detector()
        with _lock:
            segments = detect(samples, vad_options=options(threshold=.55, min_speech_duration_ms=250, min_silence_duration_ms=200, speech_pad_ms=0))
    else:
        segments = detector(samples)
    speech_samples = sum(max(0, min(len(samples), int(s["end"])) - max(0, int(s["start"]))) for s in segments)
    ratio = min(1., speech_samples / len(samples))
    return {"model": "Silero VAD (local ONNX)", "speechRatio": round(ratio, 4),
            "speechLikely": ratio >= .18 and rms >= .0005 and clipped < .01,
            "durationMs": frames * 1000 / rate, "rms": rms, "clippedFraction": clipped,
            "intelligibilityConfirmed": False}


@router.get("/status")
def status():
    try:
        neural_detector()
        return {"available": True, "model": "Silero VAD (local ONNX)", "transcription": False}
    except Exception as exc:
        raise HTTPException(503, "Local neural speech detector is unavailable") from exc


@router.post("/assess")
async def assess(request: Request):
    import asyncio
    # Stream the request with an actual byte cap; Content-Length is not trusted.
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "Audio capture exceeds 600 kB")
    try:
        if not _slots.acquire(blocking=False):
            raise HTTPException(429, "Speech assessment is busy; try again shortly")
        try:
            return await asyncio.to_thread(assess_wav, bytes(data))
        finally:
            _slots.release()
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "Local neural speech assessment failed") from exc
