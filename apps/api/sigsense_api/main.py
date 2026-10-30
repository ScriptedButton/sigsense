"""SigSense API: analyze image/IQ, library enroll, live SDR websocket."""

from __future__ import annotations

import os
from pathlib import Path

# Avoid OpenMP double-init crashes (faiss + torch/scipy on macOS)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, SecretStr

from sigsense_api.inference import InferenceEngine
from sigsense_api.sdr import LiveSDRService
from sigsense_api.signal_store import SignalStore
from sigsense_api.upconverters import UPCONVERTERS
from sigsense_api.feedback_routes import build_router as build_feedback_router
from sigsense_api.autofinder import router as autofinder_router
from sigsense_api.audio_cleaner import router as audio_cleaner_router
from sigsense_api.kiwi_diversity import router as kiwi_diversity_router
from sigsense_api.spectrum_bands import (
    SpectrumPayloadError,
    SpectrumSessions,
    analyze_spectrum_rows,
    decode_rows,
    session_trackers,
)
from sigsense_api.spectrum import (
    SignalTracker,
    measure_spectrum,
    observe_frame,
    public_name,
)
from sigsense_ml.ble import BLE_CHANNELS_MHZ, decode_ble_iq
from sigsense_ml.ble_capture import (
    MAX_CAPTURE_BYTES,
    dissect_capture,
    export_ble_pcap,
    tshark_capabilities,
)
from sigsense_ml.classes import SIGNAL_CLASSES, CAPTURE_ONLY_CLASSES
from sigsense_ml.preprocess import load_iq_file, iq_to_spectrogram


engine: InferenceEngine | None = None
sdr = LiveSDRService()
tracker = SignalTracker()
spectrum_sessions = SpectrumSessions()
signal_store: SignalStore | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global engine, signal_store
    # SIGSENSE_MODELS_DIR points the engine at another models folder (tests and
    # trial servers use a copy so corrections never touch the real library).
    models_override = os.environ.get("SIGSENSE_MODELS_DIR")
    engine = InferenceEngine(Path(models_override) if models_override else None)
    signal_store = SignalStore()
    if os.environ.get("SIGSENSE_STT_PRELOAD") == "1":
        # Optional: hold the speech model in VRAM from startup so the first
        # transcription starts instantly (otherwise it loads on first use, ~2 s).
        from sigsense_api.stt import get_stt

        get_stt().load_in_background()
    try:
        yield
    finally:
        dvoice_runtime.shutdown()
        sdr._close_all()
        signal_store.db.close()


app = FastAPI(title="SigSense API", version="0.1.0", lifespan=lifespan)
app.include_router(autofinder_router)
app.include_router(audio_cleaner_router)
app.include_router(kiwi_diversity_router)

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


# Digital voice (P25, DMR, NXDN, D-STAR, YSF, M17): clear voice and signalling only; encryption is
# reported, never decoded. The engine only starts when enabled via POST /dvoice/config.
from sigsense_api.dvoice.api import build_router as build_dvoice_router  # noqa: E402
from sigsense_api.dvoice.runtime import get_runtime as get_dvoice_runtime  # noqa: E402

dvoice_runtime = get_dvoice_runtime()
dvoice_runtime.bind(
    lambda: sdr.audio_source(),
    lambda: bool(sdr.state.demo),
    lambda: bool(sdr.state.ble_hop and sdr.state.ble_decode),
)
app.include_router(build_dvoice_router(dvoice_runtime.engine, lambda: sdr.audio_source()))

# Manual corrections: label a tracked signal and the model learns from it now.
app.include_router(
    build_feedback_router(
        get_engine,
        tracker,
        lambda session: session_trackers(spectrum_sessions, session),
    )
)


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
    lna_gain_db: float | None = None
    vga_gain_db: float | None = None
    amp_enable: bool | None = None
    demo: bool | None = None
    demo_class: str | None = None
    device_args: str | None = None
    ble_decode: bool | None = None
    ble_hop: bool | None = None
    upconverter: str | None = None


class BleReplayRequest(BaseModel):
    header_hex: str = Field(min_length=2, max_length=8)
    payload_hex: str = Field(min_length=2, max_length=80)
    channel: int = Field(default=37, ge=0, le=39)
    sample_rate_hz: float | None = None
    tx_gain: int = Field(default=20, ge=0, le=47)
    amp: bool = False
    repeats: int = Field(default=3, ge=1, le=20)
    dry_run: bool = False


class SaveBleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str = "broadcast"
    demo: bool = False
    packets: list[dict[str, Any]] = Field(min_length=1, max_length=10_000)


class SavedBleReplayRequest(BaseModel):
    unique: bool = True
    repeats: int = Field(default=1, ge=1, le=20)
    tx_gain: int = Field(default=10, ge=0, le=47)
    dry_run: bool = True


class SaveSignalRequest(BaseModel):
    frame_id: str
    name: str = Field(min_length=1, max_length=80)
    device_id: str | None = None


class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class NameDeviceRequest(RenameRequest):
    device_id: str = Field(min_length=1, max_length=160)


class SavedTransmitRequest(BaseModel):
    repeats: int = Field(default=1, ge=1, le=20)
    tx_gain: int = Field(default=10, ge=0, le=47)
    amp: bool = False
    dry_run: bool = False


class ScannerRequest(BaseModel):
    enabled: bool
    start_hz: float = Field(default=2402e6, ge=1e5, le=6e9)
    stop_hz: float = Field(default=2480e6, ge=1e5, le=6e9)
    step_hz: float = Field(default=2e6, gt=0, le=20e6)
    dwell_ms: int = Field(default=150, ge=50, le=5000)
    sensitivity_db: float = Field(default=10.0, ge=3, le=40)


class BleDecryptRequest(BaseModel):
    session_key_hex: SecretStr
    iv_hex: str
    header_hex: str
    ciphertext_mic_hex: str
    packet_counter: int = Field(ge=0, lt=1 << 39)
    direction: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "classes": len(get_engine().model.classes)}


@app.get("/classes")
def classes() -> dict:
    return {"classes": get_engine().model.classes, "catalog": SIGNAL_CLASSES}


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


@app.get("/ble/capabilities")
async def ble_capabilities() -> dict:
    import asyncio
    import importlib.util

    return {
        "iq_phys": ["LE1M", "LE2M"],
        "iq_connection_data": "requires CONNECT_IND or explicit access address + CRCInit",
        "automatic_rf_connection_following": False,
        "iq_coded_phy": False,
        "supplied_session_key_decryption": bool(
            importlib.util.find_spec("cryptography")
        ),
        "capture_analysis": await asyncio.to_thread(tshark_capabilities),
    }


@app.post("/analyze/ble/decrypt", response_model=None)
async def decrypt_ble_payload(body: BleDecryptRequest) -> Response:
    """Verify MIC and decrypt one observed ACL payload using supplied session material."""
    from sigsense_ml.ble_crypto import decrypt_payload

    try:
        plaintext = decrypt_payload(
            session_key=bytes.fromhex(body.session_key_hex.get_secret_value()),
            iv_on_air=bytes.fromhex(body.iv_hex),
            header=bytes.fromhex(body.header_hex),
            ciphertext_mic=bytes.fromhex(body.ciphertext_mic_hex),
            packet_counter=body.packet_counter,
            direction=body.direction,
        )
    except ImportError as exc:
        raise HTTPException(
            503, "Install sigsense-ml[ble] for supplied-key decryption"
        ) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(
        json.dumps({"plaintext_hex": plaintext.hex(), "mic_ok": True}),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/analyze/ble/capture")
async def analyze_ble_capture(
    file: UploadFile = File(...), max_packets: int = Form(2000)
) -> dict:
    """Passively dissect sniffer PCAP/PCAPNG or HCI/btsnoop captures."""
    import asyncio

    data = await file.read(MAX_CAPTURE_BYTES + 1)
    try:
        return await asyncio.to_thread(dissect_capture, data, max_packets=max_packets)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, f"Failed to analyze BLE capture: {exc}") from exc


@app.post("/analyze/ble", response_model=None)
async def analyze_ble(
    file: UploadFile = File(...),
    dtype: str = Form("auto"),
    sample_rate: float | None = Form(None),
    center_freq: float | None = Form(2402e6),
    channel: int | None = Form(None),
    phy: str = Form("auto"),
    connection_contexts: str = Form("[]"),
    output_format: str = Form("json"),
) -> Any:
    """Decode captured-channel LE1M/LE2M advertising and known-connection PDUs."""
    import asyncio

    data = await file.read(MAX_CAPTURE_BYTES + 1)
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_CAPTURE_BYTES:
        raise HTTPException(413, "IQ upload exceeds 32 MiB")
    if output_format not in {"json", "pcap"}:
        raise HTTPException(400, "output_format must be json or pcap")
    try:
        iq, wav_rate = load_iq_file(
            data, dtype=dtype, sample_rate=sample_rate, filename=file.filename
        )
        rate = sample_rate if sample_rate is not None else wav_rate
        if rate is None:
            rate = 4e6
        result = await asyncio.to_thread(
            decode_ble_iq,
            iq,
            sample_rate=float(rate),
            center_freq_hz=center_freq,
            channel=channel,
            phy=phy,
            connections=json.loads(connection_contexts),
        )
        if output_format == "pcap":
            return Response(
                export_ble_pcap(result["packets"]),
                media_type="application/vnd.tcpdump.pcap",
                headers={
                    "Content-Disposition": 'attachment; filename="sigsense-ble.pcap"'
                },
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


@app.get("/ble/saved")
def list_saved_ble() -> dict:
    store = get_signal_store()
    return {"items": store.list_ble(), "device_names": store.names()}


@app.post("/ble/saved")
async def save_ble_messages(body: SaveBleRequest) -> dict:
    import asyncio
    try:
        return await asyncio.to_thread(get_signal_store().save_ble,
            body.packets, body.name, body.kind, body.demo)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/ble/saved/{identity}")
def get_saved_ble(identity: str) -> dict:
    try:
        return get_signal_store().get_ble(identity)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.patch("/ble/saved/{identity}")
def rename_saved_ble(identity: str, body: RenameRequest) -> dict:
    try:
        return get_signal_store().rename_ble(identity, body.name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/ble/saved/{identity}/replay")
async def replay_saved_ble(identity: str, body: SavedBleReplayRequest) -> dict:
    import asyncio
    from sigsense_api.ble_library import prepare_replay
    completed = 0
    try:
        record = await asyncio.to_thread(get_signal_store().get_ble, identity)
        blocks, details = await asyncio.to_thread(prepare_replay, record["packets"],
            unique=body.unique, repeats=body.repeats)
        if body.dry_run:
            return {**details, "dry_run": True, "transmitted": False}
        for iq, metadata in blocks:
            await sdr.transmit_saved(iq, metadata, tx_gain=body.tx_gain)
            completed += 1
        return {**details, "dry_run": False, "transmitted": True}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"Replay stopped after {completed} channel blocks: {exc}") from exc


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


class SpectrumBandsRequest(BaseModel):
    """A short block of the browser's spectrum rows (oldest first)."""

    session: str = Field(min_length=1, max_length=64)
    center_freq_hz: float = Field(gt=0, lt=1e11)
    sample_rate_hz: float = Field(gt=0, lt=1e10)
    rows: int = Field(ge=1, le=256)
    bins: int = Field(ge=16, le=16384)
    db_min: float
    db_max: float
    # base64 uint8, rows*bins, row-major, ascending frequency, linear in dB
    data: str = Field(max_length=6_000_000)
    sensitivity_db: float = Field(default=10.0, ge=3, le=40)


@app.post("/analyze/spectrum-bands")
async def analyze_spectrum_bands(body: SpectrumBandsRequest) -> dict:
    """Identify occupied bands in browser spectrum rows with the backend CNN.

    One request per session is processed at a time; a request that arrives
    while the previous one is still running gets {"busy": true} and is dropped.
    """
    import asyncio

    eng = get_engine()
    try:
        rows_db = decode_rows(
            body.data, body.rows, body.bins, body.db_min, body.db_max
        )
    except SpectrumPayloadError as exc:
        raise HTTPException(400, str(exc)) from exc
    entry = spectrum_sessions.get(body.session)
    if not entry["busy"].acquire(blocking=False):
        return {"type": "spectrum_bands", "busy": True}

    def work() -> dict:
        try:
            return analyze_spectrum_rows(
                eng,
                entry,
                rows_db,
                body.center_freq_hz,
                body.sample_rate_hz,
                body.sensitivity_db,
            )
        finally:
            entry["busy"].release()

    return await asyncio.to_thread(work)


class IqBandsRequest(BaseModel):
    session: str = Field(min_length=1, max_length=128)
    center_freq_hz: float = Field(ge=0, lt=1e12)
    sample_rate_hz: float = Field(gt=0, lt=1e10)
    data: str = Field(max_length=400_000)
    sensitivity_db: float = Field(default=10.0, ge=3, le=40)


@app.post("/analyze/iq-bands")
async def analyze_iq_bands(body: IqBandsRequest) -> dict:
    """Identify browser receiver IQ before display averaging destroys its detail."""
    import asyncio
    import base64
    try:
        raw = base64.b64decode(body.data, validate=True)
    except Exception as exc:
        raise HTTPException(400, "data is not valid base64") from exc
    if len(raw) % 4 or not 4096 * 4 <= len(raw) <= 65536 * 4:
        raise HTTPException(400, "expected 4096–65536 interleaved int16 IQ samples")
    pairs = np.frombuffer(raw, dtype="<i2").astype(np.float32).reshape(-1, 2) / 32767.0
    iq = pairs[:, 0] + 1j * pairs[:, 1]
    eng = get_engine()
    entry = spectrum_sessions.get(body.session)
    if not entry["busy"].acquire(blocking=False):
        return {"type": "spectrum_bands", "busy": True}

    def work():
        try:
            fft = min(4096, len(iq))
            window = np.hanning(fft)
            blocks = iq[:len(iq) // fft * fft].reshape(-1, fft)
            power = np.abs(np.fft.fftshift(np.fft.fft(blocks * window, axis=1), axes=1)) ** 2 / window.sum() ** 2
            rows = (10 * np.log10(power + 1e-20)).astype(np.float32)
            result = analyze_spectrum_rows(eng, entry, rows, body.center_freq_hz, body.sample_rate_hz, body.sensitivity_db)
            result["approximation"] = "RF modulation families from raw receiver IQ; exact protocols require decoder evidence."
            for track in result["tracks"]:
                track["evidence"] = track.get("evidence", "").replace("on spectrum rows", "on raw IQ")
            return result
        finally:
            entry["busy"].release()
    return await asyncio.to_thread(work)


def tuner_payload(st) -> dict:
    return {
        "center_freq_hz": st.center_freq_hz,
        "sample_rate_hz": st.sample_rate_hz,
        "gain_db": st.gain_db,
        "lna_gain_db": st.lna_gain_db,
        "vga_gain_db": st.vga_gain_db,
        "amp_enable": st.amp_enable,
        "demo": st.demo,
        "demo_class": st.demo_class,
        "device_args": st.device_args,
        "ble_decode": st.ble_decode,
        "ble_hop": st.ble_hop,
        "backend": st.backend,
        "upconverter": st.upconverter,
        "lo_offset_hz": st.lo_offset_hz,
    }


@app.get("/sdr/upconverters")
def sdr_upconverters() -> dict:
    return {"items": [u.to_dict() for u in UPCONVERTERS.values()]}


@app.get("/sdr/tuner")
def sdr_tuner() -> dict:
    return tuner_payload(sdr.state)


@app.post("/sdr/tuner")
async def sdr_configure(body: TunerConfig) -> dict:
    if body.demo_class in CAPTURE_ONLY_CLASSES:
        raise HTTPException(
            400,
            f"{body.demo_class} requires captured IQ; no demo generator is available",
        )
    try:
        st = await sdr.configure(
            center_freq_hz=body.center_freq_hz,
            sample_rate_hz=body.sample_rate_hz,
            gain_db=body.gain_db,
            lna_gain_db=body.lna_gain_db,
            vga_gain_db=body.vga_gain_db,
            amp_enable=body.amp_enable,
            demo=body.demo,
            demo_class=body.demo_class,
            device_args=body.device_args,
            ble_decode=body.ble_decode,
            ble_hop=body.ble_hop,
            upconverter=body.upconverter,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return tuner_payload(st)


def get_signal_store() -> SignalStore:
    if signal_store is None:
        raise HTTPException(503, "Capture store is not ready")
    return signal_store


def named_tracks(tracks):
    names = get_signal_store().names()
    return [{**track, "name": public_name(track, names)} for track in tracks]


def public_tracks():
    return named_tracks(tracker.snapshot())


@app.get("/signals")
def saved_signals() -> dict:
    items = get_signal_store().list_recordings()
    return {"items": items, "bytes": sum(item["bytes"] for item in items)}


@app.get("/devices")
def observed_devices() -> dict:
    return {"items": public_tracks(), "names": get_signal_store().names()}


@app.post("/devices/name")
def name_device(body: NameDeviceRequest) -> dict:
    try:
        return get_signal_store().name_device(body.device_id, body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/signals/save")
async def save_signal(body: SaveSignalRequest) -> dict:
    import asyncio

    try:
        frame = sdr.captured_frame(body.frame_id)
        tracks = frame.get("observations", [])
        selected = next((t for t in tracks if t["id"] == body.device_id), None)
        if body.device_id and selected is None:
            raise ValueError("Selected signal is not in this capture frame")
        metadata = {
            "frame_id": body.frame_id,
            "device_id": body.device_id,
            "center_freq_hz": frame["tuner"]["center_freq_hz"],
            "sample_rate_hz": frame["tuner"]["sample_rate_hz"],
            "gain_db": frame["tuner"]["gain_db"],
            "demo": frame["tuner"]["demo"],
            "backend": frame["tuner"]["backend"],
            "classification": selected or frame.get("classification"),
            "content": "Full captured IQ window; may contain more than one signal",
        }
        return await asyncio.to_thread(
            get_signal_store().save, frame["iq"], metadata, body.name
        )
    except KeyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.patch("/signals/{identity}")
def rename_signal(identity: str, body: RenameRequest) -> dict:
    try:
        return get_signal_store().rename(identity, body.name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/signals/{identity}/iq")
def download_signal(identity: str) -> FileResponse:
    try:
        path = get_signal_store().path(identity)
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=f"sigsense-{identity}.npy",
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/signals/{identity}/transmit")
async def transmit_signal(identity: str, body: SavedTransmitRequest) -> dict:
    import asyncio

    try:
        iq, metadata = await asyncio.to_thread(get_signal_store().load, identity)
        return await sdr.transmit_saved(
            iq,
            metadata,
            repeats=body.repeats,
            tx_gain=body.tx_gain,
            amp=body.amp,
            dry_run=body.dry_run,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"Transmit failed: {exc}") from exc


@app.post("/sdr/scanner")
async def configure_scanner(body: ScannerRequest) -> dict:
    sdr.sensitivity_db = body.sensitivity_db
    try:
        return await sdr.configure_scanner(
            enabled=body.enabled,
            start_hz=body.start_hz,
            stop_hz=body.stop_hz,
            step_hz=body.step_hz,
            dwell_ms=body.dwell_ms,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.websocket("/signals/{identity}/playback")
async def playback_signal(ws: WebSocket, identity: str) -> None:
    import asyncio

    try:
        iq, metadata = await asyncio.to_thread(get_signal_store().load, identity)
    except (KeyError, ValueError):
        await ws.close(code=4404)
        return
    await ws.accept()
    try:
        step = max(1024, int(metadata["sample_rate_hz"] * 0.05))
        repeat = ws.query_params.get("repeat", "true") == "true"
        while True:
            for offset in range(0, len(iq), step):
                block = iq[offset : offset + step]
                if len(block) < 16:
                    continue
                spec = iq_to_spectrogram(block)
                prediction = await asyncio.to_thread(
                    get_engine().analyze_spectrogram,
                    spec,
                    iq=block,
                    sample_rate=metadata["sample_rate_hz"],
                    center_freq=metadata["center_freq_hz"],
                )
                spectrum = measure_spectrum(
                    block, metadata["sample_rate_hz"], metadata["center_freq_hz"]
                )
                await ws.send_json(
                    {
                        "type": "frame",
                        "playback": True,
                        "recording": metadata,
                        "progress": min(1.0, (offset + len(block)) / len(iq)),
                        "spectrum": spectrum,
                        "tracks": [],
                        "observations": [],
                        "tuner": {**metadata, "backend": "recording"},
                        "prediction": {
                            "label": prediction.label,
                            "confidence": prediction.confidence,
                            "is_unknown": prediction.is_unknown,
                            "alternatives": prediction.alternatives,
                            "neighbors": prediction.neighbors,
                            "meta": prediction.meta,
                        },
                    }
                )
                await asyncio.sleep(max(0.04, len(block) / metadata["sample_rate_hz"]))
            if not repeat:
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        try:
            await ws.close()
        except RuntimeError:
            pass


_live_clients = [0]


@app.websocket("/live")
async def live_ws(ws: WebSocket) -> None:
    import asyncio

    _live_clients[0] += 1

    await ws.accept()
    eng = get_engine()
    send_lock = asyncio.Lock()
    # Analysis (ML + tracking) runs behind capture so the spectrum keeps
    # flowing. A small bound applies backpressure instead of growing latency.
    pending: asyncio.Queue = asyncio.Queue(maxsize=4)

    async def send(payload: dict) -> None:
        async with send_lock:
            await ws.send_json(payload)

    last_analysis_log = [0.0]

    async def analyze_worker() -> None:
        while True:
            frame = await pending.get()
            try:
                result = await asyncio.to_thread(
                    eng.analyze_spectrogram,
                    frame["spectrogram"],
                    iq=frame["iq"],
                    sample_rate=frame["tuner"]["sample_rate_hz"],
                    center_freq=frame["tuner"]["center_freq_hz"],
                )
                observations = named_tracks(
                    await asyncio.to_thread(observe_frame, frame, eng, tracker)
                )
            except Exception as exc:
                # One bad frame (flat spectrum, odd capture) must not end the
                # stream: send it without ML results and keep going.
                now = time.monotonic()
                if now - last_analysis_log[0] > 5:
                    last_analysis_log[0] = now
                    logging.getLogger("uvicorn.error").exception("frame analysis failed")
                await send(
                    {
                        "type": "frame",
                        "frame_id": frame["frame_id"],
                        "spectrum": frame["spectrum"],
                        "sweep": frame["sweep"],
                        "scanner": frame["scanner"],
                        "tracks": public_tracks(),
                        "observations": [],
                        "preview": frame["preview"],
                        "tuner": frame["tuner"],
                        "prediction": {
                            "label": "unknown",
                            "confidence": 0.0,
                            "is_unknown": True,
                            "alternatives": [],
                            "neighbors": [],
                            "meta": {},
                        },
                        "ble": frame.get("ble"),
                        "analysis_error": str(exc) or type(exc).__name__,
                    }
                )
                continue
            frame["observations"] = observations
            frame["classification"] = {
                "label": result.label,
                "confidence": result.confidence,
            }
            await send(
                {
                    "type": "frame",
                    "frame_id": frame["frame_id"],
                    "spectrum": frame["spectrum"],
                    "sweep": frame["sweep"],
                    "scanner": frame["scanner"],
                    "tracks": public_tracks(),
                    "observations": observations,
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

    async def spectrum_ticker() -> None:
        # Streams the newest samples to the display independently of decode
        # and classification, so the waterfall keeps its own cadence.
        while True:
            row = await asyncio.to_thread(sdr.peek_spectrum)
            if row is not None:
                await send(row)
            await asyncio.sleep(0.03)

    worker = asyncio.create_task(analyze_worker())
    ticker = asyncio.create_task(spectrum_ticker())
    try:
        while True:
            if worker.done():
                worker.result()
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
                            lna_gain_db=data.get("lna_gain_db"),
                            vga_gain_db=data.get("vga_gain_db"),
                            amp_enable=data.get("amp_enable"),
                            demo=data.get("demo"),
                            demo_class=data.get("demo_class"),
                            device_args=data.get("device_args"),
                            ble_decode=data.get("ble_decode"),
                            ble_hop=data.get("ble_hop"),
                            upconverter=data.get("upconverter"),
                        )
            except Exception:
                pass

            frame = await sdr.next_frame(
                on_spectrum=None if sdr.can_peek() else send
            )
            await pending.put(frame)
            await asyncio.sleep(0.02)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        # Tell the browser why instead of closing silently; an unexplained
        # close looks like the receiver "flashing" and not starting.
        logging.getLogger("uvicorn.error").exception("/live stream failed")
        try:
            await ws.send_json({"type": "error", "message": str(exc) or type(exc).__name__})
            await ws.close()
        except Exception:
            pass
    finally:
        worker.cancel()
        ticker.cancel()
        _live_clients[0] -= 1
        if _live_clients[0] <= 0:
            # Free the radio so another program (or the next connect) can open it.
            await asyncio.to_thread(sdr.release_stream)


@app.get("/transcribe/status")
def transcribe_status() -> dict:
    """Speech-to-text model state, so the UI can pick the server engine."""
    from sigsense_api.stt import get_stt

    return get_stt().status()


@app.post("/transcribe/load")
def transcribe_load() -> dict:
    """Start loading the model in the background (first load takes a few seconds)."""
    from sigsense_api.stt import get_stt

    service = get_stt()
    service.load_in_background()
    return service.status()


@app.websocket("/transcribe")
async def transcribe_ws(ws: WebSocket) -> None:
    """Live transcription of client-supplied audio.

    Client: JSON {"type":"config", language?, prompt?, sample_rate?, source_label?,
    frequency_hz?, mode?, endpoint_ms?, ...}, then binary frames of little-endian
    int16 mono PCM at `sample_rate` (default 16000), any chunk size. Optional
    {"type":"meta", frequency_hz?, mode?, source_label?} updates the tag and
    {"type":"flush"} ends the utterance in progress.
    Server: {"type":"status","state":"loading|ready|error"} then
    {"type":"partial"|"final","id","text","start_s","end_s","words"?,"confidence",
    "language","latency_ms",...}; finals with "dropped": true retract a partial.
    """
    import asyncio

    from sigsense_api.stt import SessionConfig, StreamingSession, get_stt

    await ws.accept()
    service = get_stt()
    if not service.package_available():
        await ws.send_json({"type": "status", "state": "error", "message": "faster-whisper is not installed on the server"})
        await ws.close()
        return

    loop = asyncio.get_running_loop()
    events: asyncio.Queue = asyncio.Queue()
    session: StreamingSession | None = None

    def emit(event: dict) -> None:  # called from the decode thread
        loop.call_soon_threadsafe(events.put_nowait, event)

    async def sender() -> None:
        while True:
            await ws.send_json(await events.get())

    async def load_model() -> None:
        if service.status()["ready"]:
            await ws.send_json({"type": "status", "state": "ready", **service.status()})
            return
        await ws.send_json({"type": "status", "state": "loading"})
        try:
            await asyncio.to_thread(service.ensure_loaded)
            await ws.send_json({"type": "status", "state": "ready", **service.status()})
        except Exception as exc:
            await ws.send_json({"type": "status", "state": "error", "message": str(exc)})
            raise

    sender_task = asyncio.create_task(sender())
    try:
        await load_model()
        session = StreamingSession(service, SessionConfig(), emit)
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                session.feed_pcm16(message["bytes"])
                continue
            try:
                data = json.loads(message.get("text") or "{}")
            except ValueError:
                continue
            kind = data.get("type")
            if kind == "config":
                session.close()
                session = StreamingSession(service, SessionConfig.from_message(data), emit)
            elif kind == "meta":
                session.update_meta(**{k: data.get(k) for k in ("source_label", "frequency_hz", "mode")})
            elif kind == "flush":
                session.flush()
            elif kind == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:
        logging.getLogger("uvicorn.error").exception("/transcribe failed")
    finally:
        if session is not None:
            session.close()
        sender_task.cancel()


@app.websocket("/live/audio")
async def live_audio_ws(ws: WebSocket) -> None:
    """Demodulated HackRF audio: binary little-endian int16 mono @ 48 kHz.

    The client sends {"type":"config","mode","bandwidth_hz","offset_hz"} (offset
    is tuned frequency minus the HackRF centre). Needs the /live stream running,
    because that is what keeps the HackRF streaming.
    """
    import asyncio

    from sigsense_api.demod import MODES, Demodulator

    await ws.accept()

    async def unavailable(reason: str) -> None:
        try:
            await ws.send_json({"type": "unavailable", "reason": reason})
            await ws.close()
        except Exception:
            pass

    if sdr.state.demo:
        await unavailable("Demo source has no RF audio")
        return
    if sdr.audio_source() is None:
        await unavailable("HackRF is not open")
        return

    config: dict | None = None
    demod: Demodulator | None = None
    cap = None
    key = None
    idle_since: float | None = None
    # Opt-in transcript tap: the demodulated audio feeds the speech model on the
    # server, and transcript events ride back on this same socket as JSON text.
    tap = None
    tap_events: asyncio.Queue = asyncio.Queue()
    tap_loop = asyncio.get_running_loop()
    try:
        while True:
            try:
                msg = await asyncio_wait_message(ws, timeout=0.01)
            except WebSocketDisconnect:
                return
            while not tap_events.empty():
                await ws.send_json(tap_events.get_nowait())
            if msg:
                try:
                    data = json.loads(msg)
                except ValueError:
                    data = {}
                if data.get("type") == "transcribe":
                    from sigsense_api.stt import SessionConfig, StreamingSession, get_stt

                    service = get_stt()
                    if tap is not None:
                        tap.close()
                        tap = None
                    if data.get("enabled", True):
                        if not service.package_available():
                            await ws.send_json({"type": "transcript_status", "state": "error", "message": "faster-whisper is not installed on the server"})
                        else:
                            cfg = SessionConfig.from_message({**data, "sample_rate": 48000})
                            tap = StreamingSession(
                                service, cfg,
                                lambda ev: tap_loop.call_soon_threadsafe(tap_events.put_nowait, ev),
                            )
                            await ws.send_json({"type": "transcript_status", "state": "loading" if not service.status()["ready"] else "ready"})
                            if not service.status()["ready"]:
                                async def warm() -> None:
                                    try:
                                        await asyncio.to_thread(service.ensure_loaded)
                                        await ws.send_json({"type": "transcript_status", "state": "ready"})
                                    except Exception as exc:
                                        await ws.send_json({"type": "transcript_status", "state": "error", "message": str(exc)})
                                asyncio.create_task(warm())
                    else:
                        await ws.send_json({"type": "transcript_status", "state": "off"})
                elif data.get("type") == "transcribe_meta" and tap is not None:
                    tap.update_meta(**{k: data.get(k) for k in ("source_label", "frequency_hz", "mode")})
                if data.get("type") == "config":
                    mode = str(data.get("mode", "NBFM")).upper()
                    try:
                        bw = float(data.get("bandwidth_hz", 12500))
                        off = float(data.get("offset_hz", 0))
                    except (TypeError, ValueError):
                        bw, off = float("nan"), float("nan")
                    if mode in MODES and np.isfinite(bw) and np.isfinite(off):
                        config = {"mode": mode, "bandwidth_hz": bw, "offset_hz": off}
                        if demod is not None:
                            demod.reconfigure(mode, bw, off)

            # configure() swaps the capture object, so re-read it every pass.
            current = sdr.audio_source()
            if current is not cap:
                if cap is not None:
                    cap.enable_audio(False)
                cap = current
                key = None
                if cap is not None:
                    cap.enable_audio(True)
            if cap is None or sdr.state.demo or not cap.streaming:
                now = asyncio.get_running_loop().time()
                idle_since = idle_since if idle_since is not None else now
                if now - idle_since > 5.0:
                    await unavailable("HackRF stream is not running")
                    return
                await asyncio.sleep(0.04)
                continue
            idle_since = None

            new_key = (cap.generation, float(cap.sample_rate_hz))
            if new_key != key:
                key = new_key
                demod = None
            samples = cap.take_audio_iq()
            if config is None:
                await asyncio.sleep(0.04)
                continue
            if demod is None:
                demod = Demodulator(cap.sample_rate_hz, **config)
            if samples.size:
                pcm = await asyncio.to_thread(demod.process, samples)
                if pcm.size:
                    await ws.send_bytes(pcm.tobytes())
                    if tap is not None:
                        # Sent audio is 48 kHz int16; the session resamples to 16 kHz.
                        tap.feed(pcm.astype(np.float32) / 32768.0)
            await asyncio.sleep(0.04)
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass
    finally:
        if tap is not None:
            tap.close()
        if cap is not None:
            cap.enable_audio(False)


async def asyncio_sleep(sec: float) -> None:
    import asyncio

    await asyncio.sleep(sec)


async def asyncio_wait_message(ws: WebSocket, timeout: float) -> str | None:
    import asyncio

    try:
        return await asyncio.wait_for(ws.receive_text(), timeout=timeout)
    except asyncio.TimeoutError:
        return None
