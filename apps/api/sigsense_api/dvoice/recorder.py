"""Call recorder + call log persistence (CONTRACT v1). Owner: WP-G.

* `root=None` keeps the call log in memory only (nothing is written to disk);
  with a root, calls are indexed in `<root>/index.sqlite`.
* `record_audio` additionally writes one WAV (8 kHz mono int16) and a JSON sidecar
  per call under `<root>/YYYY-MM-DD/`.

SAFETY POLICY (non-negotiable): an encrypted call is logged with metadata only.
No WAV is ever created for it, and if a call turns out to be encrypted after some
frames were written (late encryption information), the partial WAV is deleted.
There is no key handling anywhere in this package.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

from sigsense_api.dvoice.events import Call, EncryptionState, to_jsonable

_SAFE = re.compile(r"[^A-Za-z0-9._-]")

_COLUMNS = (
    "call_id",
    "call_num",
    "protocol",
    "slot",
    "kind",
    "target_id",
    "source_id",
    "frequency_hz",
    "channel",
    "started_at",
    "ended_at",
    "encrypted",
    "emergency",
    "voice_frames",
    "voice_frames_bad",
    "ber",
    "end_reason",
    "audio_path",
    "audio_bytes",
    "transcript",
    "label",
)

CSV_COLUMNS = (
    "call_id",
    "protocol",
    "slot",
    "kind",
    "target_id",
    "label",
    "source_id",
    "frequency_hz",
    "channel",
    "started_at",
    "ended_at",
    "duration_s",
    "encrypted",
    "emergency",
    "voice_frames",
    "voice_frames_bad",
    "ber",
    "end_reason",
    "has_audio",
    "transcript",
)


def safe_name(call_id: str) -> str:
    """A call id as a file name: no separators, no dots-only names, bounded length."""
    cleaned = _SAFE.sub("_", call_id)[:120].lstrip(".")
    return cleaned or "call"


@dataclass
class _OpenWav:
    path: Path
    writer: "wave.Wave_write"
    samples: int = 0


class CallRecorder:
    """Writes one WAV (8 kHz mono int16) per call plus a JSON sidecar, and keeps a call index.

    Layout: <root>/YYYY-MM-DD/<call_id>.wav and .json; index.sqlite for listing/search.
    Encrypted calls are logged (metadata only) and never produce a WAV.
    """

    def __init__(
        self,
        root: Optional[Path],
        max_bytes: int = 5 * 1024**3,
        *,
        record_audio: bool = True,
        max_memory_calls: int = 5000,
    ) -> None:
        self.root = Path(root) if root is not None else None
        self.max_bytes = int(max_bytes)
        self.record_audio = bool(record_audio) and self.root is not None
        self.max_memory_calls = int(max_memory_calls)
        self._lock = threading.RLock()
        self._wavs: dict[str, _OpenWav] = {}
        self._encrypted: set[str] = set()  # calls seen as encrypted: never write audio for them
        self._closed = False
        if self.root is not None:
            self.root.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(self.root / "index.sqlite"), check_same_thread=False)
        else:
            self._db = sqlite3.connect(":memory:", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init_schema()

    # ------------------------------------------------------------------ schema
    def _init_schema(self) -> None:
        with self._lock:
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS calls (
                    call_id TEXT PRIMARY KEY,
                    call_num INTEGER,
                    protocol TEXT,
                    slot INTEGER,
                    kind TEXT,
                    target_id INTEGER,
                    source_id INTEGER,
                    frequency_hz REAL,
                    channel TEXT,
                    started_at REAL,
                    ended_at REAL,
                    encrypted INTEGER DEFAULT 0,
                    emergency INTEGER DEFAULT 0,
                    voice_frames INTEGER DEFAULT 0,
                    voice_frames_bad INTEGER DEFAULT 0,
                    ber REAL,
                    end_reason TEXT,
                    audio_path TEXT,
                    audio_bytes INTEGER DEFAULT 0,
                    transcript TEXT,
                    label TEXT,
                    json TEXT
                )
                """
            )
            self._db.execute("CREATE INDEX IF NOT EXISTS calls_started ON calls(started_at)")
            self._db.execute("CREATE INDEX IF NOT EXISTS calls_target ON calls(target_id)")
            self._db.commit()

    # ------------------------------------------------------------------ writes
    def on_call(self, call: Call) -> None:
        """Upsert the call's metadata; finalise its audio when it has ended or turned encrypted."""
        with self._lock:
            if self._closed:
                return
            encrypted = call.encryption.state == EncryptionState.ENCRYPTED
            if encrypted:
                self._encrypted.add(call.call_id)
                self._discard_audio_locked(call.call_id)
            doc = to_jsonable(call)
            existing = self._db.execute(
                "SELECT audio_path, audio_bytes, transcript FROM calls WHERE call_id = ?", (call.call_id,)
            ).fetchone()
            audio_path = existing["audio_path"] if existing else None
            audio_bytes = existing["audio_bytes"] if existing else 0
            transcript = existing["transcript"] if existing else None
            if call.call_id in self._wavs and self.root is not None:
                # audio started before the first on_call (a decoder emitted voice early): keep its path
                audio_path = str(self._wavs[call.call_id].path.relative_to(self.root))
            if encrypted:
                audio_path, audio_bytes = None, 0
            label = call.meta.get("talkgroup_label")
            self._db.execute(
                """
                INSERT INTO calls (call_id, call_num, protocol, slot, kind, target_id, source_id,
                                   frequency_hz, channel, started_at, ended_at, encrypted, emergency,
                                   voice_frames, voice_frames_bad, ber, end_reason, audio_path,
                                   audio_bytes, transcript, label, json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(call_id) DO UPDATE SET
                    call_num=excluded.call_num, slot=excluded.slot, kind=excluded.kind,
                    target_id=excluded.target_id, source_id=excluded.source_id,
                    frequency_hz=excluded.frequency_hz, channel=excluded.channel,
                    ended_at=excluded.ended_at, encrypted=excluded.encrypted,
                    emergency=excluded.emergency, voice_frames=excluded.voice_frames,
                    voice_frames_bad=excluded.voice_frames_bad, ber=excluded.ber,
                    end_reason=excluded.end_reason, audio_path=excluded.audio_path,
                    audio_bytes=excluded.audio_bytes, label=excluded.label, json=excluded.json
                """,
                (
                    call.call_id,
                    call.meta.get("call_num"),
                    call.protocol.value,
                    call.slot,
                    call.kind.value,
                    call.target_id,
                    call.source_id,
                    call.frequency_hz,
                    call.channel,
                    call.started_at,
                    call.ended_at,
                    1 if encrypted else 0,
                    1 if call.emergency else 0,
                    call.voice_frames,
                    call.voice_frames_bad,
                    call.ber,
                    call.end_reason,
                    audio_path,
                    audio_bytes,
                    transcript,
                    label,
                    json.dumps(doc),
                ),
            )
            if call.ended_at is not None:
                self._finish_audio_locked(call.call_id, doc)
            self._trim_memory_locked()
            self._db.commit()

    def on_voice(self, call_id: str, pcm: np.ndarray, sample_rate: int = 8000) -> None:
        """Append decoded PCM to the call's WAV (no-op when audio recording is off or the call is encrypted)."""
        if not self.record_audio or pcm is None or len(pcm) == 0:
            return
        with self._lock:
            if self._closed or call_id in self._encrypted:
                return
            wav = self._wavs.get(call_id)
            if wav is None:
                row = self._db.execute("SELECT started_at FROM calls WHERE call_id = ?", (call_id,)).fetchone()
                started = float(row["started_at"]) if row and row["started_at"] else time.time()
                wav = self._open_wav_locked(call_id, started, sample_rate)
                if wav is None:
                    return
            samples = np.ascontiguousarray(pcm, dtype="<i2")
            wav.writer.writeframes(samples.tobytes())
            wav.samples += int(samples.size)

    def on_transcript(self, call_id: str, text: str) -> None:
        """Attach (or append to) the call's transcript; also lands in the sidecar when it is written."""
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            if self._closed:
                return
            row = self._db.execute("SELECT transcript FROM calls WHERE call_id = ?", (call_id,)).fetchone()
            if row is None:
                return
            joined = f"{row['transcript']} {text}".strip() if row["transcript"] else text
            self._db.execute("UPDATE calls SET transcript = ? WHERE call_id = ?", (joined, call_id))
            self._db.commit()
            self._write_sidecar_locked(call_id)

    # ------------------------------------------------------------- wav helpers
    def _open_wav_locked(self, call_id: str, started_at: float, sample_rate: int) -> Optional[_OpenWav]:
        assert self.root is not None
        day = time.strftime("%Y-%m-%d", time.gmtime(started_at))
        folder = self.root / day
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{safe_name(call_id)}.wav"
        if path.resolve().parent != folder.resolve():  # belt and braces; safe_name already strips separators
            return None
        writer = wave.open(str(path), "wb")
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(int(sample_rate))
        wav = _OpenWav(path, writer)
        self._wavs[call_id] = wav
        self._db.execute(
            "UPDATE calls SET audio_path = ? WHERE call_id = ?", (str(path.relative_to(self.root)), call_id)
        )
        return wav

    def _close_wav_locked(self, call_id: str) -> Optional[_OpenWav]:
        wav = self._wavs.pop(call_id, None)
        if wav is not None:
            try:
                wav.writer.close()
            except Exception:
                pass
        return wav

    def _discard_audio_locked(self, call_id: str) -> None:
        """Encrypted (or late-discovered encrypted): remove whatever audio exists."""
        wav = self._close_wav_locked(call_id)
        paths = [wav.path] if wav is not None else []
        row = self._db.execute("SELECT audio_path FROM calls WHERE call_id = ?", (call_id,)).fetchone()
        if row and row["audio_path"] and self.root is not None:
            paths.append(self.root / row["audio_path"])
        for path in paths:
            try:
                path.unlink()
            except OSError:
                pass
        if row:
            self._db.execute("UPDATE calls SET audio_path = NULL, audio_bytes = 0 WHERE call_id = ?", (call_id,))

    def _finish_audio_locked(self, call_id: str, doc: dict[str, Any]) -> None:
        wav = self._close_wav_locked(call_id)
        if wav is not None and call_id not in self._encrypted:
            size = wav.path.stat().st_size if wav.path.exists() else 0
            self._db.execute("UPDATE calls SET audio_bytes = ? WHERE call_id = ?", (size, call_id))
        if self.root is not None and self.record_audio and call_id not in self._encrypted:
            self._write_sidecar_locked(call_id, doc)
        self._enforce_retention_locked()
        self._encrypted.discard(call_id)

    def _write_sidecar_locked(self, call_id: str, doc: Optional[dict[str, Any]] = None) -> None:
        if self.root is None or not self.record_audio:
            return
        row = self._db.execute(
            "SELECT json, audio_path, transcript, started_at, encrypted FROM calls WHERE call_id = ?", (call_id,)
        ).fetchone()
        if row is None or row["encrypted"]:
            return
        if doc is None:
            doc = json.loads(row["json"])
        doc = dict(doc)
        doc["audio_file"] = row["audio_path"]
        doc["transcript"] = row["transcript"]
        day = time.strftime("%Y-%m-%d", time.gmtime(row["started_at"] or time.time()))
        folder = self.root / day
        folder.mkdir(parents=True, exist_ok=True)
        try:
            (folder / f"{safe_name(call_id)}.json").write_text(json.dumps(doc, indent=2))
        except OSError:
            pass

    def _enforce_retention_locked(self) -> None:
        if self.root is None:
            return
        total = self._db.execute("SELECT COALESCE(SUM(audio_bytes), 0) FROM calls").fetchone()[0]
        if total <= self.max_bytes:
            return
        rows = self._db.execute(
            "SELECT call_id, audio_path, audio_bytes FROM calls WHERE audio_path IS NOT NULL "
            "AND ended_at IS NOT NULL ORDER BY started_at ASC"
        ).fetchall()
        for row in rows:
            if total <= self.max_bytes:
                break
            try:
                (self.root / row["audio_path"]).unlink()
            except OSError:
                pass
            total -= row["audio_bytes"] or 0
            self._db.execute("UPDATE calls SET audio_path = NULL, audio_bytes = 0 WHERE call_id = ?", (row["call_id"],))

    def _trim_memory_locked(self) -> None:
        if self.root is not None:
            return
        count = self._db.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
        extra = count - self.max_memory_calls
        if extra > 0:
            self._db.execute(
                "DELETE FROM calls WHERE call_id IN (SELECT call_id FROM calls ORDER BY started_at ASC LIMIT ?)",
                (extra,),
            )

    # ------------------------------------------------------------------- reads
    def _row_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        doc = json.loads(row["json"]) if row["json"] else {}
        doc["has_audio"] = bool(row["audio_path"])
        doc["transcript"] = row["transcript"]
        return doc

    def list_calls(
        self,
        limit: int = 100,
        since: Optional[float] = None,
        talkgroup: Optional[int] = None,
        *,
        offset: int = 0,
        protocol: Optional[str] = None,
        encrypted: Optional[bool] = None,
        source: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        where, args = self._filters(since, talkgroup, protocol, encrypted, source)
        sql = f"SELECT * FROM calls{where} ORDER BY started_at DESC LIMIT ? OFFSET ?"
        with self._lock:
            rows = self._db.execute(sql, (*args, max(1, min(int(limit), 1000)), max(0, int(offset)))).fetchall()
            return [self._row_dict(r) for r in rows]

    def count(
        self,
        since: Optional[float] = None,
        talkgroup: Optional[int] = None,
        protocol: Optional[str] = None,
        encrypted: Optional[bool] = None,
        source: Optional[int] = None,
    ) -> int:
        where, args = self._filters(since, talkgroup, protocol, encrypted, source)
        with self._lock:
            return int(self._db.execute(f"SELECT COUNT(*) FROM calls{where}", args).fetchone()[0])

    @staticmethod
    def _filters(since, talkgroup, protocol, encrypted, source) -> tuple[str, tuple]:
        clauses, args = [], []
        if since is not None:
            clauses.append("started_at >= ?")
            args.append(float(since))
        if talkgroup is not None:
            clauses.append("target_id = ?")
            args.append(int(talkgroup))
        if protocol:
            clauses.append("protocol = ?")
            args.append(str(protocol))
        if encrypted is not None:
            clauses.append("encrypted = ?")
            args.append(1 if encrypted else 0)
        if source is not None:
            clauses.append("source_id = ?")
            args.append(int(source))
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", tuple(args)

    def get_call(self, call_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._db.execute("SELECT * FROM calls WHERE call_id = ?", (call_id,)).fetchone()
            return self._row_dict(row) if row else None

    def wav_path(self, call_id: str) -> Optional[Path]:
        """The call's finished WAV, or None (no audio, still being written, encrypted, or removed)."""
        if self.root is None:
            return None
        with self._lock:
            if call_id in self._wavs:
                return None  # still recording
            row = self._db.execute(
                "SELECT audio_path, encrypted FROM calls WHERE call_id = ?", (call_id,)
            ).fetchone()
            if row is None or row["encrypted"] or not row["audio_path"]:
                return None
            path = (self.root / row["audio_path"]).resolve()
            if self.root.resolve() not in path.parents:  # never serve a path outside the root
                return None
            return path if path.exists() else None

    def delete_call(self, call_id: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT audio_path FROM calls WHERE call_id = ?", (call_id,)).fetchone()
            if row is None:
                return False
            self._close_wav_locked(call_id)
            if row["audio_path"] and self.root is not None:
                for suffix in (row["audio_path"], str(Path(row["audio_path"]).with_suffix(".json"))):
                    try:
                        (self.root / suffix).unlink()
                    except OSError:
                        pass
            self._db.execute("DELETE FROM calls WHERE call_id = ?", (call_id,))
            self._db.commit()
            return True

    def export(self, fmt: str = "json") -> bytes:
        """fmt: 'json' | 'csv'."""
        fmt = fmt.lower()
        if fmt not in ("json", "csv"):
            raise ValueError("fmt must be 'json' or 'csv'")
        with self._lock:
            rows = self._db.execute("SELECT * FROM calls ORDER BY started_at ASC").fetchall()
            docs = [self._row_dict(r) for r in rows]
        if fmt == "json":
            return json.dumps(docs, indent=2).encode()
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(CSV_COLUMNS)
        for doc, row in zip(docs, rows):
            started, ended = doc.get("started_at"), doc.get("ended_at")
            duration = round(ended - started, 3) if started and ended else ""
            writer.writerow(
                [
                    doc.get("call_id"),
                    doc.get("protocol"),
                    doc.get("slot"),
                    doc.get("kind"),
                    doc.get("target_id"),
                    row["label"],
                    doc.get("source_id"),
                    doc.get("frequency_hz"),
                    doc.get("channel"),
                    started,
                    ended,
                    duration,
                    int(bool(row["encrypted"])),
                    int(bool(doc.get("emergency"))),
                    doc.get("voice_frames"),
                    doc.get("voice_frames_bad"),
                    doc.get("ber"),
                    doc.get("end_reason"),
                    int(bool(doc.get("has_audio"))),
                    doc.get("transcript") or "",
                ]
            )
        return out.getvalue().encode()

    # --------------------------------------------------------------- lifecycle
    def set_record_audio(self, enabled: bool) -> None:
        with self._lock:
            self.record_audio = bool(enabled) and self.root is not None
            if not self.record_audio:
                for call_id in list(self._wavs):
                    self._close_wav_locked(call_id)

    def close(self) -> None:
        """Finalise every open WAV and close the database; safe to call twice."""
        with self._lock:
            if self._closed:
                return
            for call_id in list(self._wavs):
                self._close_wav_locked(call_id)
            self._closed = True
            try:
                self._db.commit()
                self._db.close()
            except sqlite3.Error:
                pass
