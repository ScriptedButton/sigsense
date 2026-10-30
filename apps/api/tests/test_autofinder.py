import io
import wave
import numpy as np
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sigsense_api.autofinder import assess_wav, router


def wav_bytes(seconds=2, rate=48000, amplitude=.1):
    data = io.BytesIO()
    with wave.open(data, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        signal = amplitude * np.sin(2 * np.pi * 500 * np.arange(int(seconds * rate)) / rate)
        wav.writeframes((signal * 32767).astype("<i2").tobytes())
    return data.getvalue()


def test_assessment_requires_neural_activity_not_audio_energy():
    assert assess_wav(wav_bytes(), lambda audio: [])['speechLikely'] is False
    result = assess_wav(wav_bytes(), lambda audio: [{"start": 0, "end": 16000}])
    assert result["speechLikely"] is True
    assert result["speechRatio"] == .5
    assert result["intelligibilityConfirmed"] is False
    assert assess_wav(wav_bytes(amplitude=0), lambda audio: [{"start": 0, "end": len(audio)}])["speechLikely"] is False


def test_capture_validation():
    for data in [b"", b"not wav", b"x" * 600001, wav_bytes(seconds=.5), wav_bytes(seconds=7)]:
        with unittest.TestCase().assertRaises(ValueError):
            assess_wav(data, lambda audio: [])


def test_api_enforces_actual_body_limit_and_invalid_wav():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.post("/autofinder/assess", content=b"x" * 600001).status_code == 413
    assert client.post("/autofinder/assess", content=b"invalid").status_code == 422


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(fn) for fn in [test_assessment_requires_neural_activity_not_audio_energy, test_capture_validation, test_api_enforces_actual_body_limit_and_invalid_wav])
