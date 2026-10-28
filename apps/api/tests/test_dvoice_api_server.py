"""The digital voice REST + WebSocket API against a real uvicorn server (WP-G).

starlette's TestClient needs httpx, which is not installed here, so these tests start uvicorn on a
spare local port (8020 by default, NEVER the app's own 8000) and use urllib + the websockets client.
Each test class gets a fresh runtime with its own data directory; nothing touches the user's data.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any, Optional

import numpy as np

try:
    import uvicorn
    import websockets
except Exception as exc:  # pragma: no cover
    raise unittest.SkipTest(f"uvicorn/websockets not available: {exc}")

from fastapi import FastAPI

from sigsense_api.dvoice import engine as eng
from sigsense_api.dvoice.api import build_router
from sigsense_api.dvoice.events import Modulation, unpack_audio_frame
from sigsense_api.dvoice.runtime.manager import DvoiceRuntime, set_runtime
from sigsense_api.dvoice.testing.channel import impair
from sigsense_api.dvoice.testing.modulator import modulate
from sigsense_api.dvoice.vocoder import registry
from sigsense_api.dvoice.events import VocoderId

from test_dvoice_e2e_engine import m17_dibits


def free_port(preferred: int = 8020) -> int:
    for port in (preferred, *range(preferred + 1, preferred + 40)):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port")


def call(method: str, url: str, body: Any = None, headers: Optional[dict] = None, raw: bool = False):
    data = None
    h = dict(headers or {})
    if body is not None and not isinstance(body, (bytes, bytearray)):
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    elif body is not None:
        data = bytes(body)
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read()
            return resp.status, (payload if raw else (json.loads(payload) if payload else None)), dict(resp.headers)
    except urllib.error.HTTPError as err:
        payload = err.read()
        try:
            return err.code, json.loads(payload), dict(err.headers)
        except ValueError:
            return err.code, payload, dict(err.headers)


def upload_payload(dibits: np.ndarray, snr_db: float = 28.0, seed: int = 1) -> bytes:
    """A 48 kHz IQ16 upload for a single-channel M17 transmission (noise in the lead-in/tail like a real receiver)."""
    bb = modulate(dibits, 4800.0, Modulation.FSK4)
    rng = np.random.default_rng(seed)
    pad = lambda n: np.zeros(n, np.complex64)
    padded = np.concatenate([pad(9_600), bb, pad(19_200)])
    sig_power = float(np.mean(np.abs(bb) ** 2))
    sigma = np.sqrt(sig_power / (10 ** (snr_db / 10.0)) * (48_000 / 12_500) / 2.0)
    noisy = padded + sigma * (rng.standard_normal(padded.size) + 1j * rng.standard_normal(padded.size))
    out = np.empty(noisy.size * 2, dtype="<i2")
    out[0::2] = np.clip(noisy.real * 12000, -32768, 32767)
    out[1::2] = np.clip(noisy.imag * 12000, -32768, 32767)
    return out.tobytes()


class ServerCase(unittest.TestCase):
    """Starts uvicorn + a runtime once per class."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.rt = DvoiceRuntime(Path(cls.tmp.name))
        cls.capture = None
        cls.rt.bind(lambda: cls.capture, lambda: False, lambda: False)
        set_runtime(cls.rt)
        app = FastAPI()
        app.include_router(build_router(cls.rt.engine, lambda: cls.capture))
        cls.port = free_port()
        cls.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=cls.port, log_level="warning"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        end = time.monotonic() + 15
        while not cls.server.started and time.monotonic() < end:
            time.sleep(0.05)
        assert cls.server.started, "uvicorn did not start"
        cls.http = f"http://127.0.0.1:{cls.port}/dvoice"
        cls.ws = f"ws://127.0.0.1:{cls.port}/dvoice/stream"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.rt.shutdown()
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        set_runtime(None)
        cls.tmp.cleanup()

    def get(self, path: str, **kw):
        return call("GET", self.http + path, **kw)

    def post(self, path: str, body: Any = None, **kw):
        return call("POST", self.http + path, body, **kw)

    def enable_upload(self, protocol: str = "m17", record: str = "off", **extra) -> None:
        status, body, _ = self.post(
            "/config",
            {"enabled": True, "source": "upload", "channels": [{"name": "ch0", "protocol": protocol}], "record": record, **extra},
        )
        self.assertEqual(status, 200, body)

    def disable(self) -> None:
        self.post("/config", {"enabled": False})


class StatusAndConfigTests(ServerCase):
    def test_status_and_capabilities(self):
        status, body, _ = self.get("/status")
        self.assertEqual(status, 200)
        self.assertIn("disclaimer", body)
        self.assertIn("encryption_policy", body)
        self.assertIn("never", body["encryption_policy"].lower())
        self.assertFalse(body["enabled"])
        status, caps, _ = self.get("/capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(set(caps["protocols"]), {"p25p1", "p25p2", "dmr", "nxdn48", "nxdn96", "dstar", "ysf", "m17"})
        self.assertTrue(caps["protocols"]["m17"]["implemented"])
        for entry in caps["protocols"].values():
            if not entry["implemented"]:
                self.assertTrue(entry["reason"], "an unimplemented protocol must say why")
        self.assertIn("ambe_plus2_3600x2450", caps["vocoders"])
        self.assertIn("backends", caps["licence_notices"])
        self.assertEqual(caps["inputs"]["iq16_48k"], 1)
        self.assertIn("never", caps["disclaimer"].lower() + caps["encryption_policy"].lower())
        self.assertIn("numba", caps)

    def test_config_validation_and_policy_cannot_be_relaxed(self):
        status, body, _ = self.post("/config", {"record": "everything"})
        self.assertEqual(status, 422)
        status, body, _ = self.post("/config", {"channels": [{"name": "a", "protocol": "bogus"}]})
        self.assertEqual(status, 422)
        status, body, _ = self.post("/config", {"follow": {"max_voice_followers": 99}})
        self.assertEqual(status, 422)
        status, body, _ = self.post("/config", {"follow": {"skip_encrypted": False}})  # not a settable field: ignored, not applied
        self.assertEqual(status, 200)
        self.assertTrue(body["follow"]["skip_encrypted"])
        status, body, _ = self.get("/config")
        self.assertEqual(status, 200)
        self.assertEqual(body["channels"][0]["name"], "ch0")

    def test_enable_disable_cycle_and_persistence(self):
        self.enable_upload()
        status, body, _ = self.get("/status")
        self.assertTrue(body["enabled"])
        self.assertTrue(body["engine"]["running"])
        self.assertIn("ch0", body["engine"]["channels"])
        self.disable()
        status, body, _ = self.get("/status")
        self.assertFalse(body["engine"]["running"])
        # settings are saved but a restart never auto-starts decoding
        again = DvoiceRuntime(Path(self.tmp.name))
        self.assertFalse(again.settings.enabled)
        self.assertEqual(again.settings.channels[0]["protocol"], "m17")

    def test_codec_endpoint(self):
        status, body, _ = self.get("/codec")
        self.assertEqual(status, 200)
        self.assertIn("status", body)
        self.assertIn("notices", body)
        self.assertIn("policy", body["notices"])

    def test_ingest_requires_upload_mode(self):
        self.disable()
        status, body, _ = self.post("/ingest?channel=ch0&kind=1", b"\0" * 400, headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(status, 409)
        self.enable_upload()
        status, body, _ = self.post("/ingest?channel=ch0&kind=9", b"\0" * 400, headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(status, 422)
        status, body, _ = self.post("/ingest?channel=ch0&kind=1", b"\0" * 400, headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(status, 200)
        self.assertEqual(body["accepted_bytes"], 400)
        status, body, _ = self.post("/ingest?channel=ch0&kind=1", b"\0" * (eng_limit() + 10), headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(status, 413)
        self.disable()


def eng_limit() -> int:
    from sigsense_api.dvoice.api import MAX_UPLOAD_BYTES

    return MAX_UPLOAD_BYTES


class StreamTests(ServerCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if not eng.protocol_support().get("m17", {}).get("implemented"):
            raise unittest.SkipTest("M17 decoder not implemented yet")
        cls.dibits = m17_dibits(frames=40)
        cls.payload = upload_payload(cls.dibits)

    async def _stream(self, *, subscribe: Optional[dict] = None, last_event_id: Optional[int] = None, payload: Optional[bytes] = None, wait: float = 6.0):
        url = self.ws + "?source=upload&channel=ch0" + (f"&last_event_id={last_event_id}" if last_event_id is not None else "")
        texts: list[dict] = []
        binaries: list[bytes] = []
        async with websockets.connect(url, max_size=None) as sock:
            if subscribe is not None:
                await sock.send(json.dumps({"type": "subscribe", **subscribe}))

            async def reader() -> None:
                try:
                    async for message in sock:
                        (binaries if isinstance(message, bytes) else texts).append(message if isinstance(message, bytes) else json.loads(message))
                except websockets.ConnectionClosed:
                    pass

            task = asyncio.create_task(reader())
            if payload is not None:
                step = 4 * 2400  # 50 ms of IQ16
                for i in range(0, len(payload), step):
                    await sock.send(bytes([1]) + payload[i : i + step])
                    await asyncio.sleep(0.012)
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline and not any(t.get("type") == "call_end" for t in texts):
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.3)
            task.cancel()
        return texts, binaries

    def test_upload_stream_produces_events_and_audio(self):
        self.enable_upload()
        texts, binaries = asyncio.run(self._stream(payload=self.payload))
        self.disable()
        self.assertEqual(texts[0]["type"], "status", "the first message is a status snapshot")
        types = [t["type"] for t in texts]
        self.assertIn("call_start", types)
        self.assertIn("call_end", types)
        ids = [t["id"] for t in texts if "id" in t and t["type"] not in ("status",)]
        self.assertEqual(ids, sorted(ids), "event ids increase")
        end = next(t for t in texts if t["type"] == "call_end")["data"]
        self.assertEqual(end["meta"].get("src"), "W2FBI")
        if registry.get_vocoder(VocoderId.CODEC2_3200).__class__.__name__ != "UnavailableVocoder":
            frames = [unpack_audio_frame(b) for b in binaries]
            pcm = [(h, p) for h, p in frames if p.size]
            self.assertGreater(len(pcm), 20)
            self.assertEqual({h["call_num"] for h, _ in pcm}, {end["meta"]["call_num"]})
            self.assertTrue(frames[-1][0]["end_of_call"])

    def test_subscribe_filters_audio_and_event_types(self):
        self.enable_upload()
        texts, binaries = asyncio.run(self._stream(subscribe={"audio": False, "types": ["call_end"]}, payload=self.payload))
        self.disable()
        self.assertEqual(binaries, [], "audio:false must stop the binary frames")
        self.assertTrue({t["type"] for t in texts[1:]} <= {"call_end", "transcript"}, {t["type"] for t in texts})
        self.assertTrue(any(t["type"] == "call_end" for t in texts))

    def test_reconnect_replays_missed_events(self):
        self.enable_upload()
        first, _ = asyncio.run(self._stream(payload=self.payload))
        last_id = max(t["id"] for t in first if t["type"] != "status")
        starts = [t["id"] for t in first if t["type"] == "call_start"]
        again, _ = asyncio.run(self._stream(last_event_id=starts[0] - 1, wait=0.3))
        self.disable()
        replayed = [t for t in again if t["type"] == "call_start"]
        self.assertEqual([t["id"] for t in replayed], starts, "events after last_event_id are replayed")
        nothing, _ = asyncio.run(self._stream(last_event_id=last_id, wait=0.3))
        self.assertEqual([t for t in nothing if t["type"] in ("call_start", "call_end")], [])

    def test_upload_is_refused_until_the_engine_runs(self):
        self.disable()

        async def go():
            async with websockets.connect(self.ws + "?source=upload", max_size=None) as sock:
                await sock.recv()  # status
                await sock.send(bytes([1]) + b"\0" * 400)
                return json.loads(await asyncio.wait_for(sock.recv(), 5))

        msg = asyncio.run(go())
        self.assertEqual(msg["type"], "error")
        self.assertEqual(msg["data"]["code"], "engine_stopped")

    def test_bad_source_and_oversize_chunk(self):
        self.enable_upload()

        async def go():
            async with websockets.connect(self.ws + "?source=upload", max_size=None) as sock:
                await sock.recv()
                await sock.send(bytes([1]) + b"\0" * (eng_limit() + 8))
                return json.loads(await asyncio.wait_for(sock.recv(), 5))

        self.assertEqual(asyncio.run(go())["data"]["code"], "chunk_too_large")

        async def bad():
            async with websockets.connect(self.ws + "?source=bogus", max_size=None) as sock:
                return json.loads(await asyncio.wait_for(sock.recv(), 5))  # the error is the first and last message

        self.assertEqual(asyncio.run(bad())["data"]["code"], "bad_source")
        self.disable()

    def test_live_channel_config_over_the_socket(self):
        self.enable_upload(protocol="auto")

        async def go():
            async with websockets.connect(self.ws + "?source=upload", max_size=None) as sock:
                await sock.recv()
                await sock.send(json.dumps({"type": "config", "channel": "ch0", "protocol": "m17"}))
                await sock.send(json.dumps({"type": "config", "channel": "nope", "protocol": "m17"}))
                await sock.send(json.dumps({"type": "ping"}))
                seen = []
                for _ in range(4):
                    try:
                        seen.append(json.loads(await asyncio.wait_for(sock.recv(), 1.0)))
                    except asyncio.TimeoutError:
                        break
                return seen

        seen = asyncio.run(go())
        self.assertTrue(any(m.get("type") == "pong" for m in seen))
        self.assertTrue(any(m.get("type") == "error" and m["data"]["code"] == "unknown_channel" for m in seen))
        status, body, _ = self.get("/config")
        self.assertEqual(body["channels"][0]["protocol"], "m17")
        self.disable()


class CallLogAndRecordingTests(ServerCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if not eng.protocol_support().get("m17", {}).get("implemented"):
            raise unittest.SkipTest("M17 decoder not implemented yet")
        cls.clear = upload_payload(m17_dibits(frames=40), seed=11)
        cls.encrypted = upload_payload(m17_dibits(frames=40, encrypted=True), seed=12)

    def _send(self, payload: bytes) -> None:
        for i in range(0, len(payload), 4 * 2400):
            status, _, _ = self.post("/ingest?channel=ch0&kind=1", payload[i : i + 4 * 2400], headers={"Content-Type": "application/octet-stream"})
            self.assertEqual(status, 200)
            time.sleep(0.01)
        end = time.monotonic() + 15
        while time.monotonic() < end:
            _, body, _ = self.get("/status")
            if body["engine"]["active_calls"] == 0 and body["recording"]["calls"] and body["engine"]["channels"]["ch0"]["queue_seconds"] < 0.01:
                break
            time.sleep(0.1)
        time.sleep(0.4)

    def test_recorded_call_is_listed_exported_and_served_as_wav(self):
        self.enable_upload(record="audio")
        self._send(self.clear)
        status, listing, _ = self.get("/calls")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(listing["total"], 1)
        item = listing["items"][0]
        call_id = item["call_id"]
        self.assertEqual(item["protocol"], "m17")
        status, detail, _ = self.get(f"/calls/{call_id}")
        self.assertEqual(status, 200)
        if registry.get_vocoder(VocoderId.CODEC2_3200).__class__.__name__ != "UnavailableVocoder":
            self.assertTrue(detail["has_audio"])
            status, raw, headers = self.get(f"/calls/{call_id}/audio", raw=True)
            self.assertEqual(status, 200)
            self.assertEqual({k.lower(): v for k, v in headers.items()}["content-type"], "audio/wav")
            with wave.open(io.BytesIO(raw)) as w:
                self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 8000))
                self.assertGreater(w.getnframes(), 8000, "more than a second of audio")
        status, raw, headers = self.get("/calls/export?fmt=csv", raw=True)
        rows = list(csv.reader(io.StringIO(raw.decode())))
        self.assertEqual(rows[0][0], "call_id")
        self.assertTrue(any(r[0] == call_id for r in rows[1:]))
        status, exported, _ = self.get("/calls/export?fmt=json")
        self.assertTrue(any(c["call_id"] == call_id for c in exported))
        status, again, _ = call("POST", self.http + "/calls/export?fmt=json")
        self.assertEqual(status, 200)
        status, filtered, _ = self.get("/calls?protocol=m17&encrypted=false&limit=1")
        self.assertEqual(len(filtered["items"]), 1)
        status, none, _ = self.get("/calls?tg=424242")
        self.assertEqual(none["total"], 0)
        status, body, _ = call("DELETE", f"{self.http}/calls/{call_id}")
        self.assertEqual(status, 200)
        self.assertEqual(self.get(f"/calls/{call_id}")[0], 404)
        self.disable()

    def test_encrypted_call_is_logged_but_audio_is_refused(self):
        self.enable_upload(record="audio")
        before = self.get("/calls?encrypted=true")[1]["total"]
        self._send(self.encrypted)
        status, listing, _ = self.get("/calls?encrypted=true")
        self.assertGreater(listing["total"], before, "the encrypted call is logged as metadata")
        call_id = listing["items"][0]["call_id"]
        self.assertEqual(listing["items"][0]["encryption"]["state"], "encrypted")
        self.assertFalse(listing["items"][0]["has_audio"])
        status, body, _ = self.get(f"/calls/{call_id}/audio")
        self.assertEqual(status, 403)
        self.assertEqual(list((Path(self.tmp.name) / "calls").rglob(f"{call_id}.wav")), [])
        self.disable()

    def test_missing_call_and_unknown_audio(self):
        self.assertEqual(self.get("/calls/no-such-call")[0], 404)
        self.assertEqual(self.get("/calls/no-such-call/audio")[0], 404)
        self.assertEqual(call("DELETE", f"{self.http}/calls/no-such-call")[0], 404)

    def test_path_traversal_in_a_call_id_is_harmless(self):
        status, body, _ = self.get("/calls/..%2F..%2Fetc%2Fpasswd/audio")
        self.assertIn(status, (404, 422))

    def test_talkgroup_labels_json_and_csv(self):
        status, body, _ = call("PUT", self.http + "/talkgroups", {"labels": {"9": "Dispatch", "17": "Fire"}})
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 2)
        status, body, _ = call("PUT", self.http + "/talkgroups", b"id,label\n# comment\n300,Ops\n301,\"Tac 1\"\nbad,line\n", headers={"Content-Type": "text/csv"})
        self.assertEqual(body["labels"], {"300": "Ops", "301": "Tac 1"})
        status, body, _ = self.get("/talkgroups")
        self.assertTrue({"Ops", "Tac 1"} <= {t["label"] for t in body["items"]})
        status, body, _ = call("PUT", self.http + "/talkgroups", b"{not json", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 422)
        call("PUT", self.http + "/talkgroups", {"labels": {}})


class RuntimeUnitTests(unittest.TestCase):
    def test_hub_event_ring_and_filters(self):
        from sigsense_api.dvoice.runtime.manager import EventHub, _Client

        hub = EventHub()
        for i in range(700):
            hub.publish_event({"v": 1, "type": "call_start", "t": 0, "channel": "ch0", "data": {"meta": {"call_num": i}, "target_id": i % 3}})
        self.assertEqual(hub.last_id, 700)
        self.assertLessEqual(len(hub._ring), 500)
        loop = asyncio.new_event_loop()
        client = _Client(loop)
        client.talkgroups = {1}
        self.assertTrue(client.wants_event({"type": "call_start", "channel": "ch0", "data": {"target_id": 1}}))
        self.assertFalse(client.wants_event({"type": "call_start", "channel": "ch0", "data": {"target_id": 2}}))
        client.channels = {"other"}
        self.assertFalse(client.wants_event({"type": "error", "channel": "ch0", "data": {}}))
        self.assertTrue(client.wants_event({"type": "status", "channel": "*", "data": {}}))
        loop.close()

    def test_client_queue_drops_oldest_when_full(self):
        from sigsense_api.dvoice.runtime import manager

        loop = asyncio.new_event_loop()
        client = manager._Client(loop)
        client.queue = asyncio.Queue(maxsize=3)
        for i in range(6):
            client.push(("text", str(i)))
        self.assertEqual([client.queue.get_nowait()[1] for _ in range(3)], ["3", "4", "5"])
        self.assertEqual(client.dropped, 3)
        loop.close()

    def test_settings_validation(self):
        from sigsense_api.dvoice.runtime.manager import Settings

        s = Settings()
        s.validate()
        s.channels = [{"name": "a", "protocol": "dmr"}, {"name": "a", "protocol": "dmr"}]
        with self.assertRaises(ValueError):
            s.validate()
        s = Settings()
        s.protocols = ["nope"]
        with self.assertRaises(ValueError):
            s.validate()
        s = Settings()
        s.follow["hang_time_s"] = 999
        with self.assertRaises(ValueError):
            s.validate()
        self.assertTrue(Settings().to_engine_config(Path(".")).follow.skip_encrypted)

    def test_feeder_reports_why_it_is_not_streaming(self):
        from sigsense_api.dvoice.runtime.manager import HackrfFeeder

        rt = DvoiceRuntime(Path(tempfile.mkdtemp()))
        self.addCleanup(rt.shutdown)
        feeder = HackrfFeeder(rt)
        rt.get_demo = lambda: True
        feeder._step()
        self.assertEqual(feeder.state, "unavailable")
        self.assertIn("demo", feeder.reason)
        rt.get_demo = lambda: False
        feeder._step()
        self.assertIn("no HackRF capture", feeder.reason)

        class Cap:
            streaming = False
            sample_rate_hz = 2_400_000.0
            center_freq_hz = 462.6e6

            def __init__(self):
                self.taps = []

            def add_tap(self, seconds):
                from sigsense_api.hackrf import HackRFCapture

                cap = HackRFCapture(center_freq_hz=1.0, sample_rate_hz=2_400_000.0, gain_db=1.0)
                tap = cap.add_tap(seconds)
                self.taps.append(tap)
                return tap

        cap = Cap()
        rt.get_capture = lambda: cap
        feeder._step()
        self.assertEqual(feeder.state, "waiting")
        rt.get_hopping = lambda: True
        feeder._step()
        self.assertIn("hopping", feeder.reason)
        feeder.stop()


if __name__ == "__main__":
    unittest.main()
