import asyncio
import json
import os
import socket
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
import uvicorn
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from fastapi import FastAPI
from sigsense_api.audio_cleaner import LiveCleaner, PROFILES, router as cleaner_router
from sigsense_api.processing_client import LanNeuralWorker, WorkerConfig, public_info, worker_health
from sigsense_api.processing_protocol import BLOCK, DELAY, HELLO, Delay, decode_audio
from sigsense_api.processing_worker import create_app

TOKEN = "test-only-0123456789abcdef0123456789abcdef"
HEADERS = {"Authorization": "Bearer " + TOKEN}


class TestEngine:
    info = {"engine": "test-enhancer", "device": "cpu", "accelerated": False}

    def __init__(self):
        self.fail = False
        self.stall = 0

    def session(self):
        engine = self
        delay = Delay(DELAY)

        class Session:
            def apply(self, audio):
                if engine.fail:
                    raise RuntimeError("Injected processing failure")
                if engine.stall:
                    time.sleep(engine.stall)
                return delay.apply(audio * .25)
        return Session()


class Local:
    def __init__(self):
        self.delay = Delay(480)
        self.closed = False

    async def start(self):
        pass

    async def apply(self, audio):
        return self.delay.apply(audio * .5)

    async def close(self):
        self.closed = True


class WorkerServer:
    """Actual TCP/WebSocket boundary; no mocked network transport."""
    def __init__(self, engine):
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.socket.getsockname()[1]}"
        self.app = create_app(lambda: engine)
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="error", ws_max_size=16000, timeout_graceful_shutdown=2))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.socket]}, daemon=True)

    def __enter__(self):
        self.thread.start()
        end = time.monotonic() + 8
        while not self.server.started and self.thread.is_alive() and time.monotonic() < end:
            time.sleep(.01)
        if not self.server.started:
            raise RuntimeError("Test worker failed to start")
        return self

    def __exit__(self, *args):
        self.server.should_exit = True
        self.thread.join(5)
        self.socket.close()
        if self.thread.is_alive():
            raise RuntimeError("Test worker did not stop")


class ProtocolTests(unittest.TestCase):
    def test_config_validation_and_secret_allowlist(self):
        with patch.dict(os.environ, {"SIGSENSE_PROCESSING_URL": ""}):
            self.assertIsNone(WorkerConfig.environment())
        for url in ("file:///tmp/a", "http://user:pass@worker", "http://worker/path", "http://worker?token=bad", "ws://worker"):
            with patch.dict(os.environ, {"SIGSENSE_PROCESSING_URL": url, "SIGSENSE_PROCESSING_TOKEN": TOKEN}):
                with self.assertRaises(ValueError):
                    WorkerConfig.environment()
        self.assertEqual(public_info({"engine": "x", "token": TOKEN, "url": "secret"}), {"engine": "x", "accelerated": False})
        with patch.dict(os.environ, {"SIGSENSE_PROCESSING_URL": "http://worker:8090", "SIGSENSE_PROCESSING_TOKEN": TOKEN}):
            self.assertEqual(WorkerConfig.environment().socket_url, "ws://worker:8090/v1/enhance")

    def test_malformed_audio_is_rejected(self):
        for data in (b"bad", np.zeros(BLOCK + 1, dtype="<f4").tobytes(), np.full(BLOCK, np.nan, dtype="<f4").tobytes(), np.full(BLOCK, 5, dtype="<f4").tobytes()):
            with self.assertRaises(ValueError):
                decode_audio(data)

    def test_worker_changes_invalidate_previously_winning_profiles(self):
        cleaner = LiveCleaner()
        cleaner.profile = PROFILES[3]
        cleaner.process(np.ones(BLOCK) * .1, np.ones(BLOCK) * .05)
        cleaner.invalidate_neural()
        self.assertEqual(cleaner.profile.name, "Natural")
        self.assertEqual(len(cleaner.neural), 0)
        self.assertIsNone(cleaner.report["metrics"])

    def test_failure_diagnostics_do_not_log_exception_secrets(self):
        with patch.dict(os.environ, {"SIGSENSE_PROCESSING_URL": ""}):
            worker = LanNeuralWorker(Local())
        for error, reason in ((TimeoutError(TOKEN), "worker_stall"), (ValueError(TOKEN), "invalid_audio"), (OSError(TOKEN), "connection_error")):
            self.assertEqual(worker._reason(error), reason)
            with self.assertLogs("sigsense_api.processing_client", level="WARNING") as messages:
                worker._record_failure(reason, error)
            self.assertIn(reason, messages.output[0])
            self.assertNotIn(TOKEN, " ".join(messages.output))


class WorkerTests(unittest.TestCase):
    def test_startup_refuses_missing_authentication(self):
        for token in ("", "a" * 31, "a" * 32 + "\ntrailing", "REPLACE_WITH_A_LONG_UNCHANGED_EXAMPLE_SECRET"):
            with patch.dict(os.environ, {"SIGSENSE_PROCESSING_TOKEN": token}):
                with self.assertRaises(RuntimeError), TestClient(create_app(TestEngine)):
                    pass

    def test_auth_protocol_capacity_clock_and_disconnect_cleanup(self):
        with patch.dict(os.environ, {"SIGSENSE_PROCESSING_TOKEN": TOKEN, "SIGSENSE_PROCESSING_MAX_STREAMS": "1"}), TestClient(create_app(TestEngine)) as client:
            self.assertEqual(client.get("/v1/health").status_code, 401)
            health = client.get("/v1/health", headers=HEADERS).json()
            self.assertTrue(health["ready"])
            self.assertFalse(health["accelerated"])
            self.assertEqual(health["device"], "cpu")
            self.assertNotIn(TOKEN, json.dumps(health))
            for headers in ({}, {**HEADERS, "Origin": "https://browser.test"}):
                with self.assertRaises(WebSocketDisconnect), client.websocket_connect("/v1/enhance", headers=headers):
                    pass
            with client.websocket_connect("/v1/enhance", headers=HEADERS) as ws:
                ws.send_json(HELLO)
                self.assertEqual(ws.receive_json()["delaySamples"], DELAY)
                with self.assertRaises(WebSocketDisconnect), client.websocket_connect("/v1/enhance", headers=HEADERS):
                    pass
                data = np.linspace(-.3, .3, BLOCK, dtype="<f4")
                ws.send_bytes(data.tobytes())
                np.testing.assert_array_equal(decode_audio(ws.receive_bytes()), np.zeros(BLOCK))
                ws.send_bytes(np.zeros(BLOCK, dtype="<f4").tobytes())
                np.testing.assert_allclose(decode_audio(ws.receive_bytes()), data * .25)
            self.assertEqual(client.get("/v1/health", headers=HEADERS).json()["activeStreams"], 0)
            with client.websocket_connect("/v1/enhance", headers=HEADERS) as ws:
                ws.send_json({**HELLO, "version": 900})
                self.assertEqual(ws.receive()["code"], 1008)
            with client.websocket_connect("/v1/enhance", headers=HEADERS) as ws:
                ws.send_json(HELLO); ws.receive_json()
                ws.send_bytes(np.full(BLOCK, np.nan, dtype="<f4").tobytes())
                self.assertEqual(ws.receive()["code"], 1008)

    def test_lan_stream_fails_over_without_clock_shift_and_recovers(self):
        engine = TestEngine()
        with patch.dict(os.environ, {"SIGSENSE_PROCESSING_TOKEN": TOKEN}), WorkerServer(engine) as server, patch.dict(os.environ, {"SIGSENSE_PROCESSING_URL": server.url}):
            async def run():
                local = Local(); worker = LanNeuralWorker(local)
                await worker.start()
                try:
                    health = await worker_health()
                    self.assertTrue(health["ready"])
                    self.assertNotIn(TOKEN, json.dumps(health))
                    # Let the outbound handshake complete while local audio keeps flowing.
                    await asyncio.wait_for(worker.connect_task, 2)
                    for i in range(8):
                        await worker.apply(np.full(BLOCK, (i + 1) / 100, dtype=np.float32))
                    self.assertEqual(worker.status["location"], "lan")
                    self.assertFalse(worker.status["accelerated"])
                    self.assertEqual(worker.generation, 1)
                    await asyncio.sleep(.02)  # the final buffered exchange is complete
                    engine.fail = True
                    await worker.apply(np.full(BLOCK, .09, dtype=np.float32))
                    await asyncio.sleep(.03)
                    output = await worker.apply(np.full(BLOCK, .10, dtype=np.float32))
                    # Two-block timeline: .08 * warm local gain .5, with no clock shift.
                    np.testing.assert_allclose(output, .04, atol=1e-7)
                    self.assertEqual(worker.status["location"], "local")
                    self.assertTrue(worker.status["fallback"])
                    self.assertEqual(worker.generation, 2)
                    self.assertEqual(worker.status["lastFailure"], "worker_processing")
                    engine.fail = False
                    worker.retry_at = 0
                    await worker.apply(np.full(BLOCK, .1, dtype=np.float32))
                    await asyncio.wait_for(worker.connect_task, 2)
                    for _ in range(7):
                        output = await worker.apply(np.full(BLOCK, .1, dtype=np.float32))
                    np.testing.assert_allclose(output, .025, atol=1e-7)
                    self.assertEqual(worker.status["location"], "lan")
                    self.assertEqual(worker.generation, 3)
                finally:
                    await worker.close()
                self.assertTrue(local.closed)
                self.assertIsNone(worker.remote)
                self.assertEqual(server.app.state.active, 0)
            asyncio.run(run())

    def test_auth_failure_is_safe_and_does_not_leak_credentials(self):
        with patch.dict(os.environ, {"SIGSENSE_PROCESSING_TOKEN": TOKEN}), WorkerServer(TestEngine()) as server, patch.dict(os.environ, {"SIGSENSE_PROCESSING_URL": server.url, "SIGSENSE_PROCESSING_TOKEN": "wrong-but-long-enough-0123456789abcdef"}):
            async def run():
                worker = LanNeuralWorker(Local())
                await worker.start()
                try:
                    await asyncio.wait_for(worker.connect_task, 2)
                    self.assertEqual(worker.status["location"], "local")
                    self.assertTrue(worker.status["fallback"])
                    self.assertNotIn("wrong-but", json.dumps(worker.status))
                    self.assertFalse((await worker_health())["ready"])
                finally:
                    await worker.close()
            asyncio.run(run())

    def test_no_local_neural_dependency_keeps_spectral_fallback_and_remote_recovery(self):
        class MissingLocal(Local):
            async def start(self):
                raise RuntimeError("Node absent")

        with patch.dict(os.environ, {"SIGSENSE_PROCESSING_TOKEN": TOKEN}), WorkerServer(TestEngine()) as server, patch.dict(os.environ, {"SIGSENSE_PROCESSING_URL": server.url}):
            async def run():
                worker = LanNeuralWorker(MissingLocal())
                await worker.start()
                try:
                    self.assertFalse(worker.available)
                    await asyncio.wait_for(worker.connect_task, 2)
                    for _ in range(6):
                        output = await worker.apply(np.full(BLOCK, .1, dtype=np.float32))
                    self.assertTrue(worker.available)
                    self.assertEqual(worker.status["location"], "lan")
                    await worker._drop_remote()
                    output = await worker.apply(np.full(BLOCK, .1, dtype=np.float32))
                    np.testing.assert_allclose(output, .1)
                    self.assertFalse(worker.available)
                    self.assertEqual(worker.status["location"], "spectral")
                finally:
                    await worker.close()
            asyncio.run(run())

    def test_isolated_late_block_keeps_connection_and_returns_warm_local(self):
        engine = TestEngine()
        with patch.dict(os.environ, {"SIGSENSE_PROCESSING_TOKEN": TOKEN}), WorkerServer(engine) as server, patch.dict(os.environ, {"SIGSENSE_PROCESSING_URL": server.url, "SIGSENSE_PROCESSING_DEADLINE_MS": "40"}):
            async def run():
                worker = LanNeuralWorker(Local())
                await worker.start()
                try:
                    await asyncio.wait_for(worker.connect_task, 2)
                    for _ in range(6):
                        await worker.apply(np.full(BLOCK, .1, dtype=np.float32))
                    await asyncio.sleep(.02)
                    connection = worker.remote
                    engine.stall = .2
                    await worker.apply(np.full(BLOCK, .1, dtype=np.float32))
                    await asyncio.sleep(.01)
                    engine.stall = 0  # only the already-running exchange is late
                    started = time.monotonic()
                    output = await worker.apply(np.full(BLOCK, .1, dtype=np.float32))
                    self.assertLess(time.monotonic() - started, .18)
                    np.testing.assert_allclose(output, .05, atol=1e-7)
                    self.assertTrue(worker.status["fallback"])
                    self.assertIs(worker.remote, connection)
                    self.assertEqual(worker.status["lateBlocks"], 1)
                    self.assertEqual(worker.status["lastFailure"], "playout_deadline")
                    await asyncio.sleep(.22)
                    for i in range(8):
                        output = await worker.apply(np.full(BLOCK, (i + 11) / 100, dtype=np.float32))
                    # Late results were drained, not mistaken for a newer audio block.
                    np.testing.assert_allclose(output, .16 * .25, atol=1e-7)
                    self.assertEqual(worker.status["location"], "lan")
                    self.assertIs(worker.remote, connection)
                    self.assertEqual(worker.status["reconnects"], 0)
                    self.assertIsNone(worker.warning)
                finally:
                    await worker.close()
            asyncio.run(run())

    def test_buffer_absorbs_a_reply_slower_than_old_deadline(self):
        engine = TestEngine()
        with patch.dict(os.environ, {"SIGSENSE_PROCESSING_TOKEN": TOKEN}), WorkerServer(engine) as server, patch.dict(os.environ, {"SIGSENSE_PROCESSING_URL": server.url, "SIGSENSE_PROCESSING_DEADLINE_MS": "90"}):
            async def run():
                worker = LanNeuralWorker(Local())
                await worker.start()
                try:
                    await asyncio.wait_for(worker.connect_task, 2)
                    for _ in range(8):
                        await worker.apply(np.full(BLOCK, .1, dtype=np.float32))
                    await asyncio.sleep(.02)
                    connection = worker.remote
                    engine.stall = .13
                    await worker.apply(np.full(BLOCK, .1, dtype=np.float32))
                    # Real incoming audio arrives every 100 ms. A 130 ms response
                    # now has only ~30 ms left to wait inside its playout window.
                    await asyncio.sleep(.1)
                    engine.stall = 0
                    output = await worker.apply(np.full(BLOCK, .1, dtype=np.float32))
                    np.testing.assert_allclose(output, .025, atol=1e-7)
                    self.assertIs(worker.remote, connection)
                    self.assertEqual(worker.status["lateBlocks"], 0)
                    self.assertEqual(worker.status["reconnects"], 0)
                    self.assertEqual(worker.generation, 1)
                finally:
                    await worker.close()
            asyncio.run(run())

    def test_sustained_slow_worker_cannot_accumulate_audio_or_delay_local_output(self):
        engine = TestEngine()
        with patch.dict(os.environ, {"SIGSENSE_PROCESSING_TOKEN": TOKEN}), WorkerServer(engine) as server, patch.dict(os.environ, {"SIGSENSE_PROCESSING_URL": server.url, "SIGSENSE_PROCESSING_DEADLINE_MS": "20"}):
            async def run():
                worker = LanNeuralWorker(Local())
                await worker.start()
                try:
                    await asyncio.wait_for(worker.connect_task, 2)
                    for _ in range(8):
                        await worker.apply(np.full(BLOCK, .1, dtype=np.float32))
                    await asyncio.sleep(.02)
                    engine.stall = .4
                    for _ in range(8):
                        started = time.monotonic()
                        output = await worker.apply(np.full(BLOCK, .1, dtype=np.float32))
                        self.assertLess(time.monotonic() - started, .12)
                        if worker.requests:
                            self.assertLessEqual(worker.requests.qsize(), 2)
                        self.assertLessEqual(len(worker.results), 1)
                        if worker.remote is None:
                            break
                    self.assertIsNone(worker.remote)
                    self.assertEqual(worker.status["reconnects"], 1)
                    self.assertIn(worker.status["lastFailure"], ("queue_saturation", "sustained_lateness"))
                    np.testing.assert_allclose(output, .05, atol=1e-7)
                    self.assertGreater(worker.retry_at, time.monotonic())
                finally:
                    await worker.close()
                self.assertIsNone(worker.exchange_task)
                self.assertFalse(worker.results)
                self.assertFalse(worker.cleanup_tasks)
            asyncio.run(run())

    def test_stalled_exchange_has_distinct_reason_and_is_closed(self):
        engine = TestEngine()
        with patch.dict(os.environ, {"SIGSENSE_PROCESSING_TOKEN": TOKEN}), WorkerServer(engine) as server, patch.dict(os.environ, {"SIGSENSE_PROCESSING_URL": server.url}):
            async def run():
                worker = LanNeuralWorker(Local())
                await worker.start()
                try:
                    await asyncio.wait_for(worker.connect_task, 2)
                    engine.stall = .7
                    await worker.apply(np.full(BLOCK, .1, dtype=np.float32))
                    await asyncio.sleep(.6)
                    self.assertIsNone(worker.remote)
                    self.assertEqual(worker.status["lastFailure"], "worker_stall")
                    self.assertTrue(worker.status["fallback"])
                finally:
                    await worker.close()
            asyncio.run(run())

    def test_receiver_source_and_ab_monitor_use_lan_worker_and_report_fallback(self):
        engine = TestEngine()
        with patch.dict(os.environ, {"SIGSENSE_PROCESSING_TOKEN": TOKEN}), WorkerServer(engine) as server, patch.dict(os.environ, {"SIGSENSE_PROCESSING_URL": server.url}):
            app = FastAPI(); app.include_router(cleaner_router)
            with TestClient(app) as client, client.websocket_connect("/autofinder/cleaner/source/lan-integration") as source:
                source.send_json({"frequencyHz": 5950000, "sampleRate": 16000, "source": "demo"})
                first = source.receive_json()
                self.assertEqual(first["processing"]["delayMs"], 200)
                self.assertEqual(first["processing"]["bufferMs"], 100)
                with client.websocket_connect("/autofinder/cleaner/listen/lan-integration?stage=raw") as listener:
                    listener.receive_json()
                    active = {}
                    for _ in range(30):
                        source.send_bytes(np.full(BLOCK, 2000, dtype="<i2").tobytes())
                        # Consume returned audio and metadata without losing source timing.
                        while True:
                            packet = source.receive()
                            if packet.get("text"):
                                active = json.loads(packet["text"])
                            if packet.get("bytes"):
                                self.assertEqual(len(packet["bytes"]), BLOCK * 2)
                                break
                        if active.get("processing", {}).get("location") == "lan":
                            break
                    self.assertEqual(active.get("processing", {}).get("location"), "lan")
                    self.assertFalse(active["processing"]["accelerated"])
                    # The monitor shares this source, not another remote GPU stream.
                    self.assertEqual(server.app.state.active, 1)
                    while True:
                        packet = listener.receive()
                        if packet.get("bytes"):
                            self.assertEqual(len(packet["bytes"]), BLOCK * 2)
                            break
                    engine.fail = True
                    fallback = {}
                    for _ in range(3):
                        source.send_bytes(np.full(BLOCK, 2000, dtype="<i2").tobytes())
                        while True:
                            packet = source.receive()
                            if packet.get("text"):
                                state = json.loads(packet["text"])
                                if state.get("processing", {}).get("fallback"):
                                    fallback = state
                            if packet.get("bytes"):
                                break
                        if fallback:
                            break
                    self.assertTrue(fallback["processing"]["fallback"])
                    self.assertEqual(fallback["profile"], "Natural")
                    self.assertIsNone(fallback["metrics"])
                    self.assertNotIn(TOKEN, json.dumps(fallback))


if __name__ == "__main__":
    unittest.main()
