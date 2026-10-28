"""Runtime that owns the engine's surroundings (WP-G): settings, recorder, event hub for WebSocket
clients, the HackRF IQ feeder and the opt-in speech-to-text bridge.

The FastAPI event loop never runs DSP. Worker threads publish into `EventHub.publish_*`, which hops
onto the loop with `call_soon_threadsafe` (the same pattern /live/audio uses for transcripts).
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import itertools
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from sigsense_api.dvoice.engine import (
    ChannelConfig,
    DigitalVoiceEngine,
    EngineConfig,
    validate_config,
)
from sigsense_api.dvoice.events import Protocol, unpack_audio_frame
from sigsense_api.dvoice.recorder import CallRecorder
from sigsense_api.dvoice.trunking.base import FollowPolicy

log = logging.getLogger("sigsense.dvoice")

REPLAY_EVENTS = 500
CLIENT_QUEUE = 4000


def default_data_dir() -> Path:
    override = os.environ.get("SIGSENSE_DVOICE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "sigsense" / "dvoice"


# ----------------------------------------------------------------------------- settings


@dataclass
class Settings:
    """What the user can change (POST /dvoice/config); persisted as settings.json."""

    enabled: bool = False
    source: str = "hackrf"  # "hackrf" (server capture) | "upload" (clients push IQ/discriminator)
    channels: list[dict[str, Any]] = field(
        default_factory=lambda: [{"name": "ch0", "protocol": "auto", "offset_hz": 0.0, "bandwidth_hz": 12500.0}]
    )
    protocols: list[str] = field(default_factory=lambda: [p.value for p in EngineConfig().protocols])
    follow: dict[str, Any] = field(
        default_factory=lambda: {
            "hold_talkgroups": [],
            "skip_talkgroups": [],
            "priority_talkgroups": [],
            "skip_encrypted": True,
            "hang_time_s": 2.0,
            "max_voice_followers": 1,
        }
    )
    record: str = "off"  # "off" (memory log only) | "log" (persist metadata) | "audio" (persist metadata + WAV)
    audio: bool = True  # stream decoded voice to WebSocket clients
    transcribe: bool = False
    language: str = "en"
    talkgroup_labels: dict[str, str] = field(default_factory=dict)
    max_recording_gb: float = 5.0

    def to_engine_config(self, data_dir: Path) -> EngineConfig:
        channels = [
            ChannelConfig(
                name=str(c.get("name", f"ch{i}")),
                protocol=str(c.get("protocol", "auto")),
                offset_hz=float(c.get("offset_hz", 0.0)),
                frequency_hz=(float(c["frequency_hz"]) if c.get("frequency_hz") is not None else None),
                bandwidth_hz=float(c.get("bandwidth_hz", 12500.0)),
                invert=(None if c.get("invert") is None else bool(c["invert"])),
            )
            for i, c in enumerate(self.channels)
        ]
        f = self.follow
        follow = FollowPolicy(
            hold_talkgroups={int(x) for x in f.get("hold_talkgroups", [])},
            skip_talkgroups={int(x) for x in f.get("skip_talkgroups", [])},
            priority_talkgroups=[int(x) for x in f.get("priority_talkgroups", [])],
            skip_encrypted=True,  # policy: never relaxed
            hang_time_s=float(f.get("hang_time_s", 2.0)),
            max_voice_followers=int(f.get("max_voice_followers", 1)),
        )
        cfg = EngineConfig(
            channels=channels,
            protocols=[Protocol(p) for p in self.protocols],
            follow=follow,
            record_dir=(data_dir / "calls") if self.record in ("log", "audio") else None,
            talkgroup_labels={int(k): str(v) for k, v in self.talkgroup_labels.items()},
        )
        validate_config(cfg)
        return cfg

    def validate(self) -> None:
        if self.source not in ("hackrf", "upload"):
            raise ValueError("source must be 'hackrf' or 'upload'")
        if self.record not in ("off", "log", "audio"):
            raise ValueError("record must be 'off', 'log' or 'audio'")
        if not (0.01 <= float(self.max_recording_gb) <= 2000):
            raise ValueError("max_recording_gb out of range")
        if len(self.language) > 12:
            raise ValueError("language too long")
        for p in self.protocols:
            Protocol(p)
        for key in ("hold_talkgroups", "skip_talkgroups", "priority_talkgroups"):
            [int(x) for x in self.follow.get(key, [])]
        if not (0.0 <= float(self.follow.get("hang_time_s", 2.0)) <= 60.0):
            raise ValueError("hang_time_s must be between 0 and 60")
        if not (0 <= int(self.follow.get("max_voice_followers", 1)) <= 4):
            raise ValueError("max_voice_followers must be between 0 and 4")
        self.to_engine_config(Path("."))  # channel validation


# ----------------------------------------------------------------------------- event hub


class _Client:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE)
        self.loop = loop
        self.channels: Optional[set[str]] = None
        self.types: Optional[set[str]] = None
        self.talkgroups: Optional[set[int]] = None
        self.audio = True
        self.dropped = 0

    def wants_event(self, env: dict[str, Any]) -> bool:
        if self.channels is not None and env.get("channel") not in self.channels and env.get("channel") != "*":
            return False
        if self.types is not None and env.get("type") not in self.types:
            return False
        if self.talkgroups is not None and env.get("type", "").startswith("call_"):
            if env["data"].get("target_id") not in self.talkgroups:
                return False
        return True

    def push(self, item: tuple[str, Any]) -> None:
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()  # drop the oldest, keep the stream moving
            except asyncio.QueueEmpty:
                pass
            self.dropped += 1
            try:
                self.queue.put_nowait(item)
            except asyncio.QueueFull:
                pass


class EventHub:
    """Fans engine events and audio to WebSocket clients. Thread-safe publishers, loop-side delivery."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._ring: collections.deque[tuple[int, dict[str, Any]]] = collections.deque(maxlen=REPLAY_EVENTS)
        self._clients: set[_Client] = set()
        self._call_tg: dict[int, Optional[int]] = {}  # call_num -> talkgroup, for audio filters
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    # --- publishers (any thread)
    def publish_event(self, env: dict[str, Any]) -> None:
        with self._lock:
            env["id"] = next(self._ids)
            if env["type"] in ("call_start", "call_update", "call_end"):
                num = env["data"].get("meta", {}).get("call_num")
                if num is not None:
                    self._call_tg[int(num)] = env["data"].get("target_id")
                    if len(self._call_tg) > 512:
                        self._call_tg.pop(next(iter(self._call_tg)))
            if env["type"] != "link_stats" and env["type"] != "status":
                self._ring.append((env["id"], env))
            clients = list(self._clients)
        self._deliver(clients, "event", env)

    def publish_audio(self, frame: bytes) -> None:
        with self._lock:
            clients = list(self._clients)
        if clients:
            self._deliver(clients, "audio", frame)

    def publish_json(self, doc: dict[str, Any]) -> None:
        """Out-of-band JSON (transcripts): not part of the numbered, replayable event stream."""
        with self._lock:
            clients = list(self._clients)
        self._deliver(clients, "json", doc)

    def _deliver(self, clients: list[_Client], kind: str, payload: Any) -> None:
        for client in clients:
            if kind == "event":
                if not client.wants_event(payload):
                    continue
                item = ("text", json.dumps(payload))
            elif kind == "json":
                item = ("text", json.dumps(payload))
            else:
                if not client.audio:
                    continue
                if client.talkgroups is not None:
                    try:
                        header, _ = unpack_audio_frame(payload)
                    except ValueError:
                        continue
                    if self._call_tg.get(header["call_num"]) not in client.talkgroups:
                        continue
                item = ("bytes", payload)
            try:
                client.loop.call_soon_threadsafe(client.push, item)
            except RuntimeError:  # that loop is closed
                with self._lock:
                    self._clients.discard(client)

    # --- clients (loop thread)
    def add_client(self, last_event_id: Optional[int] = None) -> tuple[_Client, list[str]]:
        loop = asyncio.get_running_loop()
        self.loop = loop
        client = _Client(loop)
        backlog: list[str] = []
        with self._lock:
            if last_event_id is not None:
                backlog = [json.dumps(env) for eid, env in self._ring if eid > last_event_id]
            self._clients.add(client)
        return client, backlog

    def remove_client(self, client: _Client) -> None:
        with self._lock:
            self._clients.discard(client)

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    @property
    def last_id(self) -> int:
        with self._lock:
            return self._ring[-1][0] if self._ring else 0


# ----------------------------------------------------------------------------- HackRF feeder


class HackrfFeeder(threading.Thread):
    """Pulls IQ from the HackRF capture's tap into the engine; survives capture swaps and retunes."""

    CHUNK_SECONDS = 0.1

    def __init__(self, runtime: "DvoiceRuntime") -> None:
        super().__init__(name="dvoice-hackrf-feed", daemon=True)
        self.rt = runtime
        self._halt = threading.Event()
        self._cap: Any = None
        self._tap: Any = None
        self._generation: Optional[int] = None
        self.state = "starting"
        self.reason = ""
        self.fed_samples = 0

    def stop(self) -> None:
        self._halt.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=2.0)
        self._drop_tap()

    def _drop_tap(self) -> None:
        if self._tap is not None:
            try:
                self._tap.close()
            except Exception:
                pass
        self._tap, self._cap, self._generation = None, None, None

    def _set(self, state: str, reason: str = "") -> None:
        if (state, reason) != (self.state, self.reason):
            self.state, self.reason = state, reason

    def run(self) -> None:
        while not self._halt.wait(0.02):
            try:
                self._step()
            except Exception:
                log.exception("hackrf feeder step failed")
                self._set("error", "feeder error; see the API log")
                time.sleep(0.5)

    def _step(self) -> None:
        rt = self.rt
        if rt.get_demo():
            self._drop_tap()
            self._set("unavailable", "the source is the synthetic demo; digital voice needs a real HackRF capture")
            time.sleep(0.3)
            return
        cap = rt.get_capture()
        if cap is None:
            self._drop_tap()
            self._set("unavailable", "no HackRF capture is open; start the HackRF receiver first")
            time.sleep(0.3)
            return
        if cap is not self._cap:
            self._drop_tap()
            self._cap = cap
            self._tap = cap.add_tap(1.0)
            self._generation = None
        if rt.get_hopping():
            self._set("unavailable", "Bluetooth channel hopping is on; it retunes the HackRF constantly. Turn it off to decode voice")
            self._tap.take()  # discard
            time.sleep(0.2)
            return
        if not cap.streaming:
            self._set("waiting", "the HackRF stream is not running; connect the receiver (it only streams while connected)")
            time.sleep(0.1)
            return
        iq, generation = self._tap.take(int(cap.sample_rate_hz * self.CHUNK_SECONDS))
        if generation != self._generation:
            if self._generation is not None:
                rt.engine().discontinuity()
            self._generation = generation
        if iq.size == 0:
            self._set("streaming", "")
            return
        self._set("streaming", "")
        self.fed_samples += int(iq.size)
        rt.engine().feed_iq(iq, float(cap.sample_rate_hz), float(cap.center_freq_hz), "*")


# ----------------------------------------------------------------------------- speech bridge


class TranscriptionBridge:
    """Opt-in: voice PCM -> the server speech model, one session per call, results as JSON events."""

    MAX_SESSIONS = 6

    def __init__(self, runtime: "DvoiceRuntime") -> None:
        self.rt = runtime
        self._sessions: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._unsub: Optional[Callable[[], None]] = None
        self._warned = False

    def attach(self, engine: DigitalVoiceEngine) -> None:
        if self._unsub is None:
            self._unsub = engine.subscribe_audio(self.on_audio)

    def detach(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self.close_all()

    def close_all(self) -> None:
        with self._lock:
            sessions, self._sessions = list(self._sessions.values()), {}
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass

    def on_audio(self, call_id: str, pcm: np.ndarray, rate: int) -> None:
        if not self.rt.settings.transcribe:
            return
        if pcm.size == 0:  # end of call
            with self._lock:
                session = self._sessions.pop(call_id, None)
            if session is not None:
                session.close()
            return
        session = self._session_for(call_id, rate)
        if session is not None:
            session.feed(pcm.astype(np.float32) / 32768.0)

    def _session_for(self, call_id: str, rate: int) -> Any:
        with self._lock:
            session = self._sessions.get(call_id)
            if session is not None:
                return session
            if len(self._sessions) >= self.MAX_SESSIONS:
                return None
        from sigsense_api.stt import SessionConfig, StreamingSession, get_stt

        service = get_stt()
        if not service.package_available():
            if not self._warned:
                self._warned = True
                self.rt.hub.publish_json({"type": "transcript_status", "state": "error", "message": "faster-whisper is not installed on the server"})
            return None
        service.load_in_background()
        meta = self.rt.call_meta(call_id)
        cfg = SessionConfig.from_message(
            {
                "language": self.rt.settings.language,
                "sample_rate": rate,
                "source_label": meta.get("channel", ""),
                "frequency_hz": meta.get("frequency_hz"),
                "mode": meta.get("protocol", ""),
                "partials": True,
            }
        )

        def emit(ev: dict[str, Any]) -> None:
            doc = {"type": "transcript", "kind": ev.get("type"), "call_id": call_id, **{k: v for k, v in ev.items() if k != "type"}}
            self.rt.hub.publish_json(doc)
            if ev.get("type") == "final" and ev.get("text"):
                rec = self.rt.recorder
                if rec is not None:
                    rec.on_transcript(call_id, ev["text"])

        session = StreamingSession(service, cfg, emit)
        with self._lock:
            self._sessions[call_id] = session
        return session


# ----------------------------------------------------------------------------- the runtime


class DvoiceRuntime:
    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.hub = EventHub()
        self.settings = Settings()
        self._engine: Optional[DigitalVoiceEngine] = None
        self._lock = threading.RLock()
        self.recorder: Optional[CallRecorder] = None
        self.feeder: Optional[HackrfFeeder] = None
        self.bridge = TranscriptionBridge(self)
        self.get_capture: Callable[[], Any] = lambda: None
        self.get_demo: Callable[[], bool] = lambda: False
        self.get_hopping: Callable[[], bool] = lambda: False
        self._call_meta: dict[str, dict[str, Any]] = {}
        self._load_settings()

    # --- wiring from main.py
    def bind(
        self,
        get_capture: Callable[[], Any],
        get_demo: Optional[Callable[[], bool]] = None,
        get_hopping: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.get_capture = get_capture
        if get_demo is not None:
            self.get_demo = get_demo
        if get_hopping is not None:
            self.get_hopping = get_hopping

    # --- persistence
    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    def _load_settings(self) -> None:
        try:
            doc = json.loads(self.settings_path.read_text())
            known = {f.name for f in dataclasses.fields(Settings)}
            candidate = Settings(**{k: v for k, v in doc.items() if k in known})
            candidate.enabled = False  # never auto-start capture decoding after a restart
            candidate.validate()
            self.settings = candidate
        except (OSError, ValueError, TypeError):
            self.settings = Settings()

    def _save_settings(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(json.dumps(dataclasses.asdict(self.settings), indent=2))
        except OSError:
            log.warning("could not save digital voice settings", exc_info=True)

    # --- engine
    def engine(self) -> DigitalVoiceEngine:
        with self._lock:
            if self._engine is None:
                cfg = self.settings.to_engine_config(self.data_dir)
                self._engine = DigitalVoiceEngine(cfg, self._on_event, self._on_audio)
                self._install_recorder()
                self.bridge.attach(self._engine)
            return self._engine

    def _on_event(self, env: dict[str, Any]) -> None:
        if env["type"] in ("call_start", "call_update"):
            data = env["data"]
            self._call_meta[data["call_id"]] = {
                "channel": env.get("channel"),
                "frequency_hz": data.get("frequency_hz"),
                "protocol": data.get("protocol"),
            }
            if len(self._call_meta) > 256:
                self._call_meta.pop(next(iter(self._call_meta)))
        self.hub.publish_event(env)

    def _on_audio(self, frame: bytes) -> None:
        if self.settings.audio:
            self.hub.publish_audio(frame)

    def call_meta(self, call_id: str) -> dict[str, Any]:
        return self._call_meta.get(call_id, {})

    def _install_recorder(self) -> None:
        engine = self._engine
        assert engine is not None
        root = self.data_dir / "calls" if self.settings.record in ("log", "audio") else None
        new = CallRecorder(
            root,
            max_bytes=int(self.settings.max_recording_gb * 1024**3),
            record_audio=self.settings.record == "audio",
        )
        old, self.recorder = self.recorder, new
        engine.set_recorder(new)
        if old is not None:
            old.close()

    def apply(self, settings: Settings) -> None:
        """Validate, persist and apply new settings; starts/stops the engine and the HackRF feeder."""
        settings.validate()
        with self._lock:
            previous = self.settings
            self.settings = settings
            engine = self.engine()
            engine.configure(settings.to_engine_config(self.data_dir))
            if (previous.record, previous.max_recording_gb) != (settings.record, settings.max_recording_gb):
                self._install_recorder()
            if settings.enabled:
                engine.start()
                if settings.source == "hackrf":
                    self._start_feeder()
                else:
                    self._stop_feeder()
            else:
                self._stop_feeder()
                engine.stop()
                self.bridge.close_all()
            self._save_settings()

    def _start_feeder(self) -> None:
        if self.feeder is None or not self.feeder.is_alive():
            self.feeder = HackrfFeeder(self)
            self.feeder.start()

    def _stop_feeder(self) -> None:
        if self.feeder is not None:
            self.feeder.stop()
            self.feeder = None

    def shutdown(self) -> None:
        with self._lock:
            self._stop_feeder()
            self.bridge.detach()
            if self._engine is not None:
                self._engine.close()
            if self.recorder is not None:
                self.recorder.close()
                self.recorder = None

    def feeder_status(self) -> dict[str, Any]:
        f = self.feeder
        if f is None or not f.is_alive():
            return {"state": "off", "reason": "", "fed_samples": 0}
        return {"state": f.state, "reason": f.reason, "fed_samples": f.fed_samples}


_runtime: Optional[DvoiceRuntime] = None
_runtime_lock = threading.Lock()


def get_runtime() -> DvoiceRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = DvoiceRuntime()
        return _runtime


def set_runtime(runtime: Optional[DvoiceRuntime]) -> None:
    """Replace the process-wide runtime (tests; the previous one is shut down)."""
    global _runtime
    with _runtime_lock:
        old, _runtime = _runtime, runtime
    if old is not None and old is not runtime:
        old.shutdown()
