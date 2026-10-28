"""FastAPI router for the digital voice engine (CONTRACT v1). Owner: WP-G.

Mounted by main.py:  app.include_router(build_router(runtime.engine, lambda: sdr.audio_source()))
(only WP-G edits main.py). Paths (prefix /dvoice):

  GET    /dvoice/status                    engine + per-channel LinkStats, HackRF feeder state, recorder, disclaimer
  GET    /dvoice/capabilities              protocols implemented (with reasons), vocoder availability + licence notes,
                                           numba/GPU info, input formats, encryption policy text, legal disclaimer text
  GET    /dvoice/config ; POST /dvoice/config   settings (merge) -> applied live; starts/stops the engine (`enabled`)
  GET    /dvoice/calls?limit&offset&since&tg&protocol&encrypted&source   call log, newest first
  GET    /dvoice/calls/{call_id}           one call (+ transcript)
  GET    /dvoice/calls/{call_id}/audio     WAV (404 none/not recorded, 403 encrypted: never recorded)
  DELETE /dvoice/calls/{call_id}
  GET|POST /dvoice/calls/export?fmt=json|csv
  GET    /dvoice/talkgroups ; PUT /dvoice/talkgroups   labels (JSON {"labels":{"id":"name"}} or CSV "id,label" lines)
  GET    /dvoice/codec                     local codec status + licence notices (build instructions for the UI)
  POST   /dvoice/ingest?channel&kind       chunked upload of IQ/discriminator samples (kind = engine.INPUT_*)
  WS     /dvoice/stream?source=hackrf|upload&channel=ch0&last_event_id=N
           text frames   -> JSON events {"v","id","type","t","channel","data"} (events.make_event + a monotonic "id";
                            reconnect with last_event_id to replay what you missed) and transcripts
                            {"type":"transcript","kind":"partial|final","call_id",...}
           binary frames -> voice audio (events.pack_audio_frame; 8 kHz mono int16; empty + end flag = end of call)
           client -> server text: {"type":"subscribe","channels":[..],"types":[..],"talkgroups":[..],"audio":true}
                                  {"type":"config","channel":..,"protocol":..,"offset_hz":..,"frequency_hz":..}
                                  {"type":"ping"}
           (source=upload) client -> server binary: [u8 kind][int16 LE samples...] with kind = engine.INPUT_*

SAFETY POLICY: encrypted calls are reported (algorithm/key ids) but never decoded, streamed, recorded or served.
There is no key-entry surface anywhere in this API.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from sigsense_api.dvoice.engine import (
    DISC16_FULL_SCALE_HZ,
    ENCRYPTION_POLICY,
    INPUT_DISC16_48K,
    INPUT_IQ16_48K,
    LEGAL_DISCLAIMER,
    MAX_CHANNELS,
    MAX_FOLLOWERS,
    DigitalVoiceEngine,
    protocol_support,
)
from sigsense_api.dvoice.runtime.manager import DvoiceRuntime, Settings, get_runtime

MAX_UPLOAD_BYTES = 1_048_576  # per message / request


class ChannelBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    protocol: str = "auto"
    offset_hz: float = 0.0
    frequency_hz: Optional[float] = None
    bandwidth_hz: float = 12_500.0
    invert: Optional[bool] = None


class FollowBody(BaseModel):
    hold_talkgroups: Optional[list[int]] = None
    skip_talkgroups: Optional[list[int]] = None
    priority_talkgroups: Optional[list[int]] = None
    hang_time_s: Optional[float] = None
    max_voice_followers: Optional[int] = None
    # `skip_encrypted` is deliberately not settable: encrypted calls are never followed or decoded.


class ConfigBody(BaseModel):
    enabled: Optional[bool] = None
    source: Optional[str] = None
    channels: Optional[list[ChannelBody]] = None
    protocols: Optional[list[str]] = None
    follow: Optional[FollowBody] = None
    record: Optional[str] = None
    audio: Optional[bool] = None
    transcribe: Optional[bool] = None
    language: Optional[str] = None
    talkgroup_labels: Optional[dict[str, str]] = None
    max_recording_gb: Optional[float] = None


class TalkgroupBody(BaseModel):
    labels: dict[str, str]


def merged_settings(current: Settings, body: ConfigBody) -> Settings:
    doc = dataclasses.asdict(current)
    patch = body.model_dump(exclude_none=True)
    follow_patch = patch.pop("follow", None)
    if "channels" in patch:
        patch["channels"] = [ChannelBody(**c).model_dump() if not isinstance(c, dict) else c for c in patch["channels"]]
    doc.update(patch)
    if follow_patch:
        doc["follow"] = {**doc["follow"], **follow_patch}
    return Settings(**doc)


def _gpu_info() -> dict[str, Any]:
    try:
        import torch  # already a dependency of the inference engine

        if torch.cuda.is_available():
            return {"available": True, "name": torch.cuda.get_device_name(0)}
        return {"available": False}
    except Exception:
        return {"available": False}


def build_router(get_engine: Callable[[], DigitalVoiceEngine], get_capture: Callable[[], Any]) -> APIRouter:
    """`get_capture()` returns the HackRF capture object (sdr.audio_source()) or None."""
    router = APIRouter(prefix="/dvoice", tags=["digital voice"])
    rt: DvoiceRuntime = get_runtime()
    rt.bind(get_capture)

    def engine() -> DigitalVoiceEngine:
        return get_engine()

    def recorder():
        eng = engine()
        return eng.recorder

    async def apply_async(settings: Settings) -> None:
        try:
            await asyncio.to_thread(rt.apply, settings)
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc

    # ------------------------------------------------------------------ status / capabilities
    @router.get("/status")
    def status() -> dict[str, Any]:
        eng = engine()
        rec = eng.recorder
        return {
            "enabled": rt.settings.enabled,
            "source": rt.settings.source,
            "engine": eng.status_dict(),
            "feeder": rt.feeder_status(),
            "clients": rt.hub.client_count,
            "last_event_id": rt.hub.last_id,
            "recording": {
                "mode": rt.settings.record,
                "audio": bool(rec is not None and rec.record_audio),
                "persistent": bool(rec is not None and rec.root is not None),
                "calls": rec.count() if rec is not None else 0,
            },
            "disclaimer": LEGAL_DISCLAIMER,
            "encryption_policy": ENCRYPTION_POLICY,
        }

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        from sigsense_api.dvoice import CONTRACT_VERSION
        from sigsense_api.dvoice.dsp._jit import HAVE_NUMBA
        from sigsense_api.dvoice.vocoder import registry
        from sigsense_api.dvoice.vocoder.notices import licence_notices

        try:
            from sigsense_api.stt import get_stt

            stt = get_stt().status()
        except Exception:
            stt = {"available": False}
        return {
            "contract_version": CONTRACT_VERSION,
            "protocols": protocol_support(),
            "vocoders": registry.capabilities(),
            "licence_notices": licence_notices(),
            "numba": HAVE_NUMBA,
            "gpu": _gpu_info(),
            "speech": stt,
            "limits": {"max_channels": MAX_CHANNELS, "max_voice_followers": MAX_FOLLOWERS},
            "inputs": {
                "iq16_48k": INPUT_IQ16_48K,
                "disc16_48k": INPUT_DISC16_48K,
                "disc16_full_scale_hz": DISC16_FULL_SCALE_HZ,
                "note": "OpenWebRX supplies demodulated audio, not IQ: digital voice is not available on that source.",
            },
            "encryption_policy": ENCRYPTION_POLICY,
            "disclaimer": LEGAL_DISCLAIMER,
        }

    @router.get("/codec")
    def codec() -> dict[str, Any]:
        from sigsense_api.dvoice.vocoder import registry
        from sigsense_api.dvoice.vocoder.local_codec import local_codec_status
        from sigsense_api.dvoice.vocoder.notices import licence_notices

        return {"status": local_codec_status(), "notices": licence_notices(), "capabilities": registry.capabilities()}

    # ------------------------------------------------------------------ config
    @router.get("/config")
    def get_config() -> dict[str, Any]:
        return dataclasses.asdict(rt.settings)

    @router.post("/config")
    async def post_config(body: ConfigBody) -> dict[str, Any]:
        try:
            settings = merged_settings(rt.settings, body)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        await apply_async(settings)
        return dataclasses.asdict(rt.settings)

    # ------------------------------------------------------------------ calls
    @router.get("/calls/export")
    @router.post("/calls/export")
    def export_calls(fmt: str = Query("json", pattern="^(json|csv)$")) -> Response:
        data = recorder().export(fmt)
        media = "application/json" if fmt == "json" else "text/csv"
        return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="calls.{fmt}"'})

    @router.get("/calls")
    def list_calls(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        since: Optional[float] = None,
        tg: Optional[int] = None,
        protocol: Optional[str] = None,
        encrypted: Optional[bool] = None,
        source: Optional[int] = None,
    ) -> dict[str, Any]:
        rec = recorder()
        items = rec.list_calls(limit, since, tg, offset=offset, protocol=protocol, encrypted=encrypted, source=source)
        total = rec.count(since, tg, protocol, encrypted, source)
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "next_offset": offset + len(items) if offset + len(items) < total else None,
        }

    @router.get("/calls/{call_id}")
    def get_call(call_id: str) -> dict[str, Any]:
        doc = recorder().get_call(call_id)
        if doc is None:
            raise HTTPException(404, "no such call")
        doc["audio_url"] = f"/dvoice/calls/{call_id}/audio" if doc.get("has_audio") else None
        return doc

    @router.get("/calls/{call_id}/audio")
    def call_audio(call_id: str) -> FileResponse:
        rec = recorder()
        doc = rec.get_call(call_id)
        if doc is None:
            raise HTTPException(404, "no such call")
        if doc.get("encryption", {}).get("state") == "encrypted":
            raise HTTPException(403, "encrypted calls are never decoded or recorded")
        path = rec.wav_path(call_id)
        if path is None:
            raise HTTPException(404, "no audio was recorded for this call (recording may be off)")
        return FileResponse(path, media_type="audio/wav", filename=f"{path.stem}.wav")

    @router.delete("/calls/{call_id}")
    def delete_call(call_id: str) -> dict[str, Any]:
        if not recorder().delete_call(call_id):
            raise HTTPException(404, "no such call")
        return {"deleted": call_id}

    # ------------------------------------------------------------------ talkgroups
    @router.get("/talkgroups")
    def talkgroups() -> dict[str, Any]:
        seen = engine().talkgroups()
        known = {(t["id"]) for t in seen}
        labels = {int(k): v for k, v in rt.settings.talkgroup_labels.items()}
        unseen = [{"id": i, "label": label, "protocol": None, "call_count": 0, "encrypted_calls": 0, "last_heard": None} for i, label in labels.items() if i not in known]
        return {"items": seen + unseen, "labels": rt.settings.talkgroup_labels}

    @router.put("/talkgroups")
    async def put_talkgroups(request: Request) -> dict[str, Any]:
        raw = (await request.body())[:MAX_UPLOAD_BYTES]
        labels: dict[str, str] = {}
        ctype = request.headers.get("content-type", "")
        try:
            if "json" in ctype:
                labels = {str(int(k)): str(v)[:80] for k, v in TalkgroupBody(**json.loads(raw)).labels.items()}
            else:  # CSV / plain text: "id,label" per line (a header line is skipped)
                for line in raw.decode("utf-8", "replace").splitlines():
                    if not line.strip() or line.lstrip().startswith("#"):
                        continue
                    ident, _, label = line.partition(",")
                    if not ident.strip().isdigit():
                        continue
                    labels[str(int(ident.strip()))] = label.strip().strip('"')[:80]
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, f"could not read the labels: {exc}") from exc
        settings = dataclasses.replace(rt.settings, talkgroup_labels=labels)
        await apply_async(settings)
        engine().set_talkgroup_labels({int(k): v for k, v in labels.items()})
        return {"count": len(labels), "labels": labels}

    # ------------------------------------------------------------------ ingest
    @router.post("/ingest")
    async def ingest(request: Request, channel: str = "ch0", kind: int = Query(INPUT_IQ16_48K)) -> dict[str, Any]:
        eng = engine()
        if not rt.settings.enabled or rt.settings.source != "upload":
            raise HTTPException(409, "set enabled=true and source='upload' (POST /dvoice/config) before sending samples")
        if kind not in (INPUT_IQ16_48K, INPUT_DISC16_48K):
            raise HTTPException(422, "kind must be 1 (IQ16 48 kHz) or 2 (discriminator16 48 kHz)")
        body = await request.body()
        if len(body) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "chunk too large; send at most 1 MiB per request")
        eng.feed_upload(kind, body, channel)
        return {"accepted_bytes": len(body)}

    # ------------------------------------------------------------------ stream
    @router.websocket("/stream")
    async def stream(
        ws: WebSocket,
        source: str = "hackrf",
        channel: str = "ch0",
        last_event_id: Optional[int] = None,
    ) -> None:
        await ws.accept()
        eng = engine()
        client, backlog = rt.hub.add_client(last_event_id)
        client.channels = None
        try:
            if source not in ("hackrf", "upload"):
                await ws.send_text(json.dumps({"type": "error", "data": {"code": "bad_source", "message": "source must be hackrf or upload"}}))
                await ws.close()
                return
            await ws.send_text(json.dumps({"v": 1, "type": "status", "channel": "*", "t": 0, "data": eng.status_dict(), "id": rt.hub.last_id}))
            for text in backlog:
                await ws.send_text(text)

            async def sender() -> None:
                while True:
                    kind, payload = await client.queue.get()
                    if kind == "text":
                        await ws.send_text(payload)
                    else:
                        await ws.send_bytes(payload)

            async def receiver() -> None:
                while True:
                    message = await ws.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("bytes") is not None:
                        data = message["bytes"]
                        if source != "upload" or not data:
                            continue
                        if len(data) > MAX_UPLOAD_BYTES:
                            await ws.send_text(json.dumps({"type": "error", "data": {"code": "chunk_too_large", "message": "send at most 1 MiB per message"}}))
                            continue
                        if not (rt.settings.enabled and rt.settings.source == "upload"):
                            await ws.send_text(json.dumps({"type": "error", "data": {"code": "engine_stopped", "message": "POST /dvoice/config with enabled=true and source='upload' first"}}))
                            await asyncio.sleep(0.5)
                            continue
                        try:
                            eng.feed_upload(data[0], data[1:], channel)
                        except ValueError as exc:
                            await ws.send_text(json.dumps({"type": "error", "data": {"code": "bad_upload", "message": str(exc)}}))
                        continue
                    text = message.get("text")
                    if not text:
                        continue
                    try:
                        msg = json.loads(text)
                    except ValueError:
                        continue
                    await handle_client_message(msg)

            async def handle_client_message(msg: dict[str, Any]) -> None:
                kind = msg.get("type")
                if kind == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
                elif kind == "subscribe":
                    client.channels = set(msg["channels"]) if msg.get("channels") else None
                    client.types = set(msg["types"]) if msg.get("types") else None
                    client.talkgroups = {int(x) for x in msg["talkgroups"]} if msg.get("talkgroups") else None
                    client.audio = bool(msg.get("audio", True))
                elif kind == "config":
                    name = str(msg.get("channel", channel))
                    chans = [dict(c) for c in rt.settings.channels]
                    target = next((c for c in chans if c["name"] == name), None)
                    if target is None:
                        await ws.send_text(json.dumps({"type": "error", "data": {"code": "unknown_channel", "message": name}}))
                        return
                    for key in ("protocol", "offset_hz", "frequency_hz", "bandwidth_hz", "invert"):
                        if key in msg:
                            target[key] = msg[key]
                    try:
                        await asyncio.to_thread(rt.apply, dataclasses.replace(rt.settings, channels=chans))
                    except (ValueError, TypeError) as exc:
                        await ws.send_text(json.dumps({"type": "error", "data": {"code": "bad_config", "message": str(exc)}}))

            tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc is not None and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                    raise exc
        except WebSocketDisconnect:
            return
        finally:
            rt.hub.remove_client(client)

    return router
