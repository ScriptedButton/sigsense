"""SigSense API: analyze image/IQ, library enroll, live SDR websocket."""

from __future__ import annotations

import os

# Avoid OpenMP double-init crashes (faiss + torch/scipy on macOS)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sigsense_api.inference import InferenceEngine
from sigsense_api.sdr import LiveSDRService
from sigsense_ml.ble import BLE_CHANNELS_MHZ, decode_ble_iq
from sigsense_ml.classes import SIGNAL_CLASSES
from sigsense_ml.preprocess import load_iq_file


engine: InferenceEngine | None = None
sdr = LiveSDRService()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global engine
    engine = InferenceEngine()
    yield


app = FastAPI(title="SigSense API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_engine() -> InferenceEngine:
    if engine is None:
        raise HTTPException(503, "Inference engine not ready")
    return engine


class AnalyzeResponse(BaseModel):
    label: str
    confidence: float
    is_unknown: bool
    alternatives: list[dict[str, Any]]
    neighbors: list[dict[str, Any]]
    meta: dict[str, Any]
    embedding: list[float] | None = None


class EnrollRequest(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    embedding: list[float]
    notes: str = ""


class EnrollResponse(BaseModel):
    id: int
    label: str


class TunerConfig(BaseModel):
    center_freq_hz: float | None = None
    sample_rate_hz: float | None = None
    gain_db: float | None = None
    demo: bool | None = None
    demo_class: str | None = None
    device_args: str | None = None
    ble_decode: bool | None = None
    ble_hop: bool | None = None


class BleReplayRequest(BaseModel):
    header_hex: str = Field(min_length=2, max_length=8)
    payload_hex: str = Field(min_length=2, max_length=80)
    channel: int = Field(default=37, ge=0, le=39)
    sample_rate_hz: float | None = None
    tx_gain: int = Field(default=20, ge=0, le=47)
    amp: bool = False
    repeats: int = Field(default=3, ge=1, le=20)
    dry_run: bool = False


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "classes": len(SIGNAL_CLASSES)}


@app.get("/classes")
def classes() -> dict:
    return {"classes": SIGNAL_CLASSES}


@app.post("/analyze/image", response_model=AnalyzeResponse)
async def analyze_image(
    file: UploadFile = File(...),
    include_embedding: bool = Form(False),
) -> AnalyzeResponse:
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    try:
        result = get_engine().analyze_image(data)
    except Exception as exc:
        raise HTTPException(400, f"Failed to analyze image: {exc}") from exc
    return AnalyzeResponse(
        label=result.label,
        confidence=result.confidence,
        is_unknown=result.is_unknown,
        alternatives=result.alternatives,
        neighbors=result.neighbors,
        meta=result.meta,
        embedding=result.embedding if include_embedding else None,
    )


@app.post("/analyze/iq", response_model=AnalyzeResponse)
async def analyze_iq(
    file: UploadFile = File(...),
    dtype: str = Form("auto"),
    sample_rate: float | None = Form(None),
    center_freq: float | None = Form(None),
    include_embedding: bool = Form(False),
) -> AnalyzeResponse:
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    try:
        result = get_engine().analyze_iq(
            data,
            dtype=dtype,
            sample_rate=sample_rate,
            center_freq=center_freq,
            filename=file.filename,
        )
    except Exception as exc:
        raise HTTPException(400, f"Failed to analyze IQ: {exc}") from exc
    return AnalyzeResponse(
        label=result.label,
        confidence=result.confidence,
        is_unknown=result.is_unknown,
        alternatives=result.alternatives,
        neighbors=result.neighbors,
        meta=result.meta,
        embedding=result.embedding if include_embedding else None,
    )


@app.get("/library")
def library() -> dict:
    return {"items": get_engine().list_library()}


@app.post("/library/enroll", response_model=EnrollResponse)
def enroll(body: EnrollRequest) -> EnrollResponse:
    pid = get_engine().enroll(body.embedding, body.label, notes=body.notes)
    return EnrollResponse(id=pid, label=body.label)


@app.get("/sdr/devices")
def sdr_devices() -> dict:
    return {"devices": sdr.list_devices()}


@app.post("/analyze/ble")
async def analyze_ble(
    file: UploadFile = File(...),
    dtype: str = Form("auto"),
    sample_rate: float | None = Form(None),
    center_freq: float | None = Form(2402e6),
    channel: int | None = Form(None),
) -> dict:
    """Demodulate BLE 1M GFSK and frame advertising PDUs from an IQ/WAV capture."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    try:
        iq, wav_rate = load_iq_file(
            data, dtype=dtype, sample_rate=sample_rate, filename=file.filename
        )
        rate = sample_rate if sample_rate is not None else wav_rate
        if rate is None:
            rate = 4e6
        result = decode_ble_iq(
            iq,
            sample_rate=float(rate),
            center_freq_hz=center_freq,
            channel=channel,
        )
    except Exception as exc:
        raise HTTPException(400, f"Failed to decode BLE: {exc}") from exc
    return result


@app.post("/ble/replay")
async def ble_replay(body: BleReplayRequest) -> dict:
    """Modulate a captured ADV PDU and TX via HackRF (or dry-run IQ build)."""
    if body.channel not in BLE_CHANNELS_MHZ:
        raise HTTPException(400, f"Unsupported BLE channel {body.channel}")
    try:
        return await sdr.replay_ble_packet(
            header_hex=body.header_hex,
            payload_hex=body.payload_hex,
            channel=body.channel,
            sample_rate_hz=body.sample_rate_hz,
            tx_gain=body.tx_gain,
            amp=body.amp,
            repeats=body.repeats,
            dry_run=body.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Replay failed: {exc}") from exc


@app.get("/sdr/presets/ble")
def ble_preset() -> dict:
    """HackRF-oriented BLE advertising channel presets."""
    return {
        "device_args": "driver=hackrf",
        "sample_rate_hz": 4e6,
        "gain_db": 32.0,
        "ble_decode": True,
        "ble_hop": True,
        "demo": False,
        "demo_class": "Bluetooth-LE",
        "channels": [
            {"name": "ADV 37", "channel": 37, "center_freq_hz": 2402e6},
            {"name": "ADV 38", "channel": 38, "center_freq_hz": 2426e6},
            {"name": "ADV 39", "channel": 39, "center_freq_hz": 2480e6},
        ],
    }


@app.get("/sdr/tuner")
def sdr_tuner() -> dict:
    st = sdr.state
    return {
        "center_freq_hz": st.center_freq_hz,
        "sample_rate_hz": st.sample_rate_hz,
        "gain_db": st.gain_db,
        "demo": st.demo,
        "demo_class": st.demo_class,
        "device_args": st.device_args,
        "ble_decode": st.ble_decode,
        "ble_hop": st.ble_hop,
        "backend": st.backend,
    }


@app.post("/sdr/tuner")
async def sdr_configure(body: TunerConfig) -> dict:
    st = await sdr.configure(
        center_freq_hz=body.center_freq_hz,
        sample_rate_hz=body.sample_rate_hz,
        gain_db=body.gain_db,
        demo=body.demo,
        demo_class=body.demo_class,
        device_args=body.device_args,
        ble_decode=body.ble_decode,
        ble_hop=body.ble_hop,
    )
    return {
        "center_freq_hz": st.center_freq_hz,
        "sample_rate_hz": st.sample_rate_hz,
        "gain_db": st.gain_db,
        "demo": st.demo,
        "demo_class": st.demo_class,
        "device_args": st.device_args,
        "ble_decode": st.ble_decode,
        "ble_hop": st.ble_hop,
        "backend": st.backend,
    }


@app.websocket("/live")
async def live_ws(ws: WebSocket) -> None:
    await ws.accept()
    eng = get_engine()
    try:
        while True:
            # Allow client to send tuner updates interleaved
            try:
                msg = await asyncio_wait_message(ws, timeout=0.01)
                if msg:
                    data = json.loads(msg)
                    if data.get("type") == "tune":
                        await sdr.configure(
                            center_freq_hz=data.get("center_freq_hz"),
                            sample_rate_hz=data.get("sample_rate_hz"),
                            gain_db=data.get("gain_db"),
                            demo=data.get("demo"),
                            demo_class=data.get("demo_class"),
                            device_args=data.get("device_args"),
                            ble_decode=data.get("ble_decode"),
                            ble_hop=data.get("ble_hop"),
                        )
            except Exception:
                pass

            frame = await sdr.next_frame()
            result = eng.analyze_spectrogram(
                frame["spectrogram"],
                iq=frame["iq"],
                sample_rate=frame["tuner"]["sample_rate_hz"],
                center_freq=frame["tuner"]["center_freq_hz"],
            )
            await ws.send_json(
                {
                    "type": "frame",
                    "preview": frame["preview"],
                    "tuner": frame["tuner"],
                    "prediction": {
                        "label": result.label,
                        "confidence": result.confidence,
                        "is_unknown": result.is_unknown,
                        "alternatives": result.alternatives[:3],
                        "neighbors": result.neighbors[:3],
                        "meta": result.meta,
                    },
                    "ble": frame.get("ble"),
                }
            )
            await asyncio_sleep(0.15)
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass


async def asyncio_sleep(sec: float) -> None:
    import asyncio

    await asyncio.sleep(sec)


async def asyncio_wait_message(ws: WebSocket, timeout: float) -> str | None:
    import asyncio

    try:
        return await asyncio.wait_for(ws.receive_text(), timeout=timeout)
    except asyncio.TimeoutError:
        return None
