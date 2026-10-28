#!/usr/bin/env python3
"""Dev-only mock of the digital voice API, for building and testing the web UI.

    cd apps/api && ../../.venv/bin/python scripts/dvoice_mock_server.py --port 8021 [--mock]

Serves the whole real SigSense API (so the app behaves normally) and, with --mock, replaces
the real /dvoice router with a scripted one that mirrors its response shapes
(apps/api/sigsense_api/dvoice/api.py): a P25 group call, two DMR slots, an encrypted call, an
emergency call and the lighter protocols, binary 8 kHz tone audio, transcripts. Without --mock
it runs the real engine when one is mounted (and mounts the mock only if there is none).

Environment: MOCK_VOCODER=1 pretends the local codec is installed; MOCK_FEEDER=unavailable|waiting
shows the feeder problem banners; MOCK_SPEED scales call pacing. No hardware is touched.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import math
import os
import random
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import PlainTextResponse, Response  # noqa: E402

from sigsense_api.dvoice.events import make_event, pack_audio_frame  # noqa: E402

DISCLAIMER = (
    "Monitoring is legal for unencrypted public-safety and amateur traffic in many places but not all. "
    "You are responsible for complying with the laws that apply to you. Encrypted transmissions are not decoded."
)
ENCRYPTION_POLICY = (
    "Clear voice and signalling are decoded. Encryption is detected and reported (algorithm and key ID) "
    "but never decoded, and no keys are handled."
)
SPEED = float(os.environ.get("MOCK_SPEED", "1.0"))
RATE = 8000
FRAME = 160
IMPLEMENTED = {"p25p1", "dmr", "m17"}
PROTOCOLS = ["p25p1", "p25p2", "dmr", "nxdn48", "nxdn96", "dstar", "ysf", "m17"]

# (protocol, kind, slot, target, source, freq_mhz, tone_hz, seconds, encrypted, emergency, phrase)
SCENARIOS = [
    ("p25p1", "group", None, 4101, 1204567, 851.0125, 440, 7.0, False, False, "Unit twelve, respond to Main Street for a welfare check."),
    ("dmr", "group", 0, 9, 3120001, 462.6375, 523, 5.0, False, False, "Copy that, we are clear of the loading dock."),
    ("dmr", "group", 1, 91, 3120055, 462.6375, 659, 4.0, False, False, "Base to mobile two, radio check."),
    ("p25p1", "group", None, 4120, 1204999, 851.5125, 0, 4.5, True, False, ""),
    ("p25p1", "group", None, 4199, 1200042, 851.0125, 392, 6.0, False, True, "Emergency, officer needs assistance at Fifth and Oak."),
    ("m17", "private", None, 884201, 884777, 438.8, 587, 4.5, False, False, "CQ CQ, this is a test of the M17 digital voice mode."),
    ("p25p1", "group", None, 4101, 1204571, 851.0125, 494, 4.0, False, False, "Ten four, en route."),
]
ENC = {"state": "encrypted", "algorithm_id": 0x84, "algorithm_name": "AES-256", "key_id": 17, "mi_present": True,
       "audio_decoded": False}
CLEAR = {"state": "clear", "algorithm_id": None, "algorithm_name": None, "key_id": None, "mi_present": False,
         "audio_decoded": True}


class Mock:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.clients: set[WebSocket] = set()
        self.counter = 1000
        self.event_id = 0
        self.task: asyncio.Task | None = None
        self.vocoder_ok = os.environ.get("MOCK_VOCODER") == "1"
        self.feeder = os.environ.get("MOCK_FEEDER", "streaming")
        self.settings = {
            "enabled": False, "source": "hackrf",
            "channels": [{"name": "ch0", "protocol": "auto", "offset_hz": 0.0, "bandwidth_hz": 12500.0}],
            "protocols": PROTOCOLS,
            "follow": {"hold_talkgroups": [], "skip_talkgroups": [], "priority_talkgroups": [], "skip_encrypted": True,
                       "hang_time_s": 2.0, "max_voice_followers": 1},
            "record": "off", "audio": True, "transcribe": False, "language": "en", "talkgroup_labels": {},
            "max_recording_gb": 5.0,
        }
        self._seed_history()

    def _call(self, sc, started: float, ended: float | None) -> dict:
        proto, kind, slot, tg, src, mhz, tone, secs, enc, emerg, phrase = sc
        self.counter += 1
        frames = int(max(0, ((ended or started + secs) - started)) * 50)
        return {
            "call_id": f"{proto}-ch0-{'x' if slot is None else slot}-{self.counter}",
            "protocol": proto, "slot": slot, "kind": kind, "target_id": tg or None, "source_id": src or None,
            "frequency_hz": mhz * 1e6, "channel": "ch0", "started_at": started, "updated_at": ended or started,
            "ended_at": ended, "encryption": dict(ENC if enc else CLEAR), "emergency": emerg,
            "priority": 1 if emerg else None, "voice_frames": 0 if enc else frames,
            "voice_frames_bad": 0 if enc else int(frames * random.random() * 0.04),
            "ber": None if enc else round(random.random() * 0.02, 4), "end_reason": "terminator" if ended else None,
            "meta": {"call_num": self.counter, "tone_hz": tone, "phrase": phrase},
            "has_audio": bool(ended) and not enc, "transcript": phrase if (ended and not enc) else None,
        }

    def _seed_history(self) -> None:
        now = time.time()
        for i in range(36):
            sc = random.Random(i).choice(SCENARIOS)
            start = now - 90 - i * 55 - random.random() * 20
            self.calls.append(self._call(sc, start, start + sc[7]))
        self.calls.sort(key=lambda c: -c["started_at"])

    def numbered(self, type_: str, data, channel: str = "ch0") -> dict:
        self.event_id += 1
        event = make_event(type_, data, channel=channel)
        event["id"] = self.event_id
        return event

    async def send_text(self, payload: dict) -> None:
        text = json.dumps(payload)
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                self.clients.discard(ws)

    async def send_bytes(self, data: bytes) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_bytes(data)
            except Exception:
                self.clients.discard(ws)

    def link(self, state: str, proto: str | None) -> dict:
        return {
            "channel": "ch0", "protocol": proto, "state": state,
            "sync_ratio": round(0.93 + random.random() * 0.06, 3) if state != "searching" else 0.0,
            "ber": round(random.random() * 0.012, 4) if state != "searching" else None,
            "fec_corrected": random.randint(0, 40), "fec_failed": random.randint(0, 2),
            "frames_ok": random.randint(500, 900), "frames_bad": random.randint(0, 12),
            "symbol_rate": 4800.0, "snr_db": round(14 + random.random() * 8, 1),
            "freq_error_hz": round(random.uniform(-80, 80), 1),
            "level": round(0.4 + random.random() * 0.3, 2) if state == "voice" else 0.2, "polarity_inverted": False,
        }

    def engine_status(self) -> dict:
        return {
            "running": self.settings["enabled"],
            "channels": {"ch0": {"state": "synced", "protocol": "p25p1", "mode": "auto", "decoder": "ok", "decoder_note": "",
                                 "offset_hz": 0.0, "cpu_fraction": 0.07, "queue_seconds": 0.02, "dropped_items": 0}},
            "vocoders": {}, "contract_version": 1, "capture": {}, "active_calls": 0, "followers": {}, "metrics": {}, "trunk": {},
        }

    async def run_call(self, sc) -> None:
        proto, _k, slot, tg, src, mhz, tone, secs, enc, emerg, phrase = sc
        secs = secs / SPEED
        call = self._call(sc, time.time(), None)
        self.calls.insert(0, call)
        await self.send_text(self.numbered("call_start", call))
        call_num = call["meta"]["call_num"]
        end = time.time() + secs
        seq = 0
        phase = 0.0
        words = phrase.split()
        while time.time() < end:
            call["updated_at"] = time.time()
            call["voice_frames"] = 0 if enc else call["voice_frames"] + 1
            if seq % 12 == 0:
                await self.send_text(self.numbered("call_update", call))
            if not enc and words and seq % 40 == 20 and self.settings["transcribe"]:
                shown = " ".join(words[: max(1, int(len(words) * (time.time() - call["started_at"]) / secs))])
                await self.send_text({"type": "transcript", "kind": "partial", "call_id": call["call_id"], "text": shown})
            if not enc and tone:
                t = (np.arange(FRAME) + phase) / RATE
                pcm = (np.sin(2 * math.pi * tone * t) * 0.35 * 32767 * (0.8 + 0.2 * math.sin(seq / 9))).astype("<i2")
                phase += FRAME
                await self.send_bytes(pack_audio_frame(call_num, seq, pcm, slot=slot, sample_rate=RATE))
            seq += 1
            await asyncio.sleep(0.02)
        call.update(ended_at=time.time(), updated_at=time.time(), end_reason="terminator")
        if not enc:
            call["has_audio"] = self.settings["record"] == "audio"
            call["transcript"] = phrase if self.settings["transcribe"] else None
            await self.send_bytes(pack_audio_frame(call_num, seq, np.zeros(0, dtype="<i2"), slot=slot, sample_rate=RATE, end_of_call=True))
            if self.settings["transcribe"] and phrase:
                await self.send_text({"type": "transcript", "kind": "final", "call_id": call["call_id"], "text": phrase})
        await self.send_text(self.numbered("call_end", call))

    async def scenario_loop(self) -> None:
        i = 0
        while True:
            await asyncio.sleep(0.5)
            if not self.clients or not self.settings["enabled"]:
                continue
            await self.send_text(self.numbered("system_info", {
                "protocol": "p25p1", "wacn": 0xBEE00, "system_id": 0x3A1, "nac": 0x293, "rfss_id": 1, "site_id": 4,
                "color_code": None, "ran": None, "network_id": None, "name": "Mock County P25",
                "control_channel_hz": 851.0125e6, "extra": {"bandplan": "851.0125 / 12.5 kHz"},
            }))
            await self.send_text(self.numbered("system_info", {
                "protocol": "dmr", "wacn": None, "system_id": None, "nac": None, "rfss_id": None, "site_id": None,
                "color_code": 1, "ran": None, "network_id": 7, "name": "Mock DMR", "control_channel_hz": None,
                "extra": {"slots": 2},
            }))
            sc = SCENARIOS[i % len(SCENARIOS)]
            i += 1
            tasks = [asyncio.create_task(self.run_call(sc))]
            if sc[0] == "dmr" and sc[2] == 0:  # DMR slot 1 and 2 overlap
                tasks.append(asyncio.create_task(self.run_call(SCENARIOS[i % len(SCENARIOS)])))
                i += 1
            end = time.time() + sc[7] / SPEED + 1
            while time.time() < end:
                await self.send_text(self.numbered("link_stats", self.link("voice", sc[0])))
                await asyncio.sleep(0.5)
            await asyncio.gather(*tasks, return_exceptions=True)
            for _ in range(4):
                await self.send_text(self.numbered("link_stats", self.link("synced", "p25p1")))
                await asyncio.sleep(0.5)


mock = Mock()
router = APIRouter(prefix="/dvoice")


def notices() -> dict:
    from sigsense_api.dvoice.vocoder.notices import licence_notices

    n = licence_notices()
    if mock.vocoder_ok:
        lc = n["local_codec"]
        lc.update(installed=True, path="/home/user/.local/share/sigsense/codecs/libsigsense_mbe.so",
                  codecs={k: True for k in lc["codecs"]},
                  protocols_with_audio=["P25 Phase 1", "DMR", "NXDN", "YSF", "P25 Phase 2"])
    return n


def vocoders() -> dict:
    from sigsense_api.dvoice.vocoder import registry

    caps = registry.capabilities()
    if mock.vocoder_ok:
        caps = {k: {**v, "available": True} for k, v in caps.items()}
    return caps


@router.get("/status")
def status() -> dict:
    feeder = {"state": "off", "reason": "", "fed_samples": 0}
    if mock.settings["enabled"]:
        reasons = {
            "unavailable": "no HackRF capture is open; start the HackRF receiver first",
            "waiting": "the HackRF stream is not running; connect the receiver (it only streams while connected)",
        }
        feeder = {"state": mock.feeder, "reason": reasons.get(mock.feeder, ""), "fed_samples": 123456}
    return {"enabled": mock.settings["enabled"], "source": mock.settings["source"], "engine": mock.engine_status(),
            "feeder": feeder, "clients": len(mock.clients), "last_event_id": mock.event_id,
            "recording": {"mode": mock.settings["record"], "audio": mock.settings["record"] == "audio",
                          "persistent": False, "calls": len(mock.calls)},
            "disclaimer": DISCLAIMER, "encryption_policy": ENCRYPTION_POLICY}


@router.get("/capabilities")
def capabilities() -> dict:
    return {
        "contract_version": 1,
        "protocols": {p: {"implemented": p in IMPLEMENTED, "reason": "" if p in IMPLEMENTED else "decoder not implemented yet",
                          "symbol_rate": 4800.0, "modulation": "fsk4", "timeslots": 2 if p in ("dmr", "p25p2") else 1}
                      for p in PROTOCOLS},
        "vocoders": vocoders(), "licence_notices": notices(), "numba": True, "gpu": {"available": False},
        "speech": {"available": True, "ready": True}, "limits": {"max_channels": 4, "max_voice_followers": 2},
        "inputs": {"note": "OpenWebRX supplies demodulated audio, not IQ: digital voice is not available on that source."},
        "encryption_policy": ENCRYPTION_POLICY, "disclaimer": DISCLAIMER,
    }


@router.get("/codec")
def codec() -> dict:
    n = notices()
    return {"status": n["local_codec"], "notices": n, "capabilities": vocoders()}


@router.get("/config")
def get_config() -> dict:
    return mock.settings


@router.post("/config")
async def post_config(request: Request) -> dict:
    body = await request.json()
    if "record" in body and body["record"] not in ("off", "log", "audio"):
        raise HTTPException(422, "record must be off, log or audio")
    for key, value in body.items():
        if key == "follow" and isinstance(value, dict):
            mock.settings["follow"].update({k: v for k, v in value.items() if k != "skip_encrypted"})
        elif key in mock.settings and value is not None:
            mock.settings[key] = value
    return mock.settings


@router.get("/calls")
def calls(limit: int = 100, offset: int = 0) -> dict:
    items = mock.calls[offset : offset + limit]
    nxt = offset + len(items)
    return {"items": items, "total": len(mock.calls), "limit": limit, "offset": offset,
            "next_offset": nxt if nxt < len(mock.calls) else None}


@router.get("/calls/export")
def export(fmt: str = "json") -> Response:
    if fmt == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["call_id", "protocol", "target_id", "source_id", "started_at"])
        for c in mock.calls:
            w.writerow([c["call_id"], c["protocol"], c["target_id"], c["source_id"], c["started_at"]])
        return PlainTextResponse(out.getvalue(), media_type="text/csv")
    return Response(json.dumps(mock.calls), media_type="application/json")


@router.get("/calls/{call_id}")
def get_call(call_id: str) -> dict:
    call = next((c for c in mock.calls if c["call_id"] == call_id), None)
    if not call:
        raise HTTPException(404, "no such call")
    return call


@router.delete("/calls/{call_id}")
def delete_call(call_id: str) -> dict:
    before = len(mock.calls)
    mock.calls = [c for c in mock.calls if c["call_id"] != call_id]
    if len(mock.calls) == before:
        raise HTTPException(404, "no such call")
    return {"deleted": call_id}


@router.get("/calls/{call_id}/audio")
def call_audio(call_id: str) -> Response:
    call = next((c for c in mock.calls if c["call_id"] == call_id), None)
    if not call:
        raise HTTPException(404, "no such call")
    if call["encryption"]["state"] == "encrypted":
        raise HTTPException(403, "encrypted calls are never decoded or recorded")
    secs = max(1.0, (call["ended_at"] or time.time()) - call["started_at"])
    t = np.arange(int(secs * RATE)) / RATE
    pcm = (np.sin(2 * math.pi * (call["meta"].get("tone_hz") or 440) * t) * 0.35 * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return Response(buf.getvalue(), media_type="audio/wav")


@router.get("/talkgroups")
def get_talkgroups() -> dict:
    return {"items": [], "labels": mock.settings["talkgroup_labels"]}


@router.put("/talkgroups")
async def put_talkgroups(request: Request) -> dict:
    body = await request.json()
    mock.settings["talkgroup_labels"] = body.get("labels", {})
    return {"count": len(mock.settings["talkgroup_labels"]), "labels": mock.settings["talkgroup_labels"]}


@router.websocket("/stream")
async def stream(ws: WebSocket, source: str = "hackrf", channel: str = "ch0", last_event_id: int | None = None) -> None:
    await ws.accept()
    mock.clients.add(ws)
    if mock.task is None:  # lifespan-managed apps ignore on_event hooks, so start lazily
        mock.task = asyncio.create_task(mock.scenario_loop())
    await ws.send_text(json.dumps({**make_event("status", mock.engine_status(), channel="*"), "id": mock.event_id}))
    try:
        while True:
            text = await ws.receive_text()
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if msg.get("type") == "config" and msg.get("protocol"):
                mock.settings["channels"][0]["protocol"] = msg["protocol"]
            elif msg.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        mock.clients.discard(ws)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8021)
    parser.add_argument("--mock", action="store_true", help="replace the real /dvoice router with the scripted mock")
    args = parser.parse_args()

    from sigsense_api.main import app

    def is_dvoice(route) -> bool:
        """True for a /dvoice route, including a router that FastAPI wrapped on include."""
        if getattr(route, "path", "").startswith("/dvoice"):
            return True
        original = getattr(route, "original_router", None)
        return original is not None and (
            getattr(original, "prefix", "") == "/dvoice"
            or any(getattr(r, "path", "").startswith("/dvoice") for r in getattr(original, "routes", []))
        )

    real = any(is_dvoice(r) for r in app.routes)
    if real and not args.mock:
        print("The real /dvoice router is mounted; running the real engine. Use --mock for the scripted one.", flush=True)
    else:
        if real:
            app.router.routes[:] = [r for r in app.router.routes if not is_dvoice(r)]
        app.include_router(router)
        print(f"Mock /dvoice mounted (vocoder installed: {mock.vocoder_ok}, feeder: {mock.feeder}).", flush=True)

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
