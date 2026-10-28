"""Persistent named devices and exact IQ recordings, independent of model weights."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

import numpy as np

REPO = Path(__file__).resolve().parents[3]


class SignalStore:
    def __init__(self, root=None):
        self.root = Path(
            root
            or os.environ.get(
                "SIGSENSE_CAPTURE_DIR", REPO / "data" / "processed" / "sdr"
            )
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "signals.sqlite", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
          CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,name TEXT NOT NULL,updated REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS ble_collections(id TEXT PRIMARY KEY,name TEXT NOT NULL,
            created REAL NOT NULL,kind TEXT NOT NULL,digest TEXT NOT NULL UNIQUE,
            metadata TEXT NOT NULL,packets TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS recordings(id TEXT PRIMARY KEY,name TEXT NOT NULL,created REAL NOT NULL,
            digest TEXT NOT NULL,center REAL NOT NULL,rate REAL NOT NULL,bytes INTEGER NOT NULL,
            metadata TEXT NOT NULL,UNIQUE(digest,center,rate));
        """
        )
        self.db.commit()
        self._backfill_ble_identities()

    def _backfill_ble_identities(self):
        from sigsense_api.ble_library import device_keys
        # Older saves already contain the evidence; enrich metadata once so the
        # library can group them without loading every saved packet on refresh.
        with self.lock:
            rows = self.db.execute(
                "SELECT id,metadata,packets FROM ble_collections "
                "WHERE json_extract(metadata, '$.device_keys') IS NULL"
            ).fetchall()
            for row in rows:
                metadata = json.loads(row["metadata"])
                metadata["device_keys"] = device_keys(json.loads(row["packets"]))
                self.db.execute("UPDATE ble_collections SET metadata=? WHERE id=?",
                                (json.dumps(metadata), row["id"]))
            self.db.commit()

    def names(self):
        with self.lock:
            return {
                r["id"]: r["name"]
                for r in self.db.execute("SELECT id,name FROM devices")
            }

    def name_device(self, identity, name):
        if (
            not identity
            or len(identity) > 160
            or not name.strip()
            or len(name.strip()) > 80
        ):
            raise ValueError("Device ID and name (1–80 characters) are required")
        with self.lock:
            self.db.execute(
                "INSERT INTO devices VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,updated=excluded.updated",
                (identity, name.strip(), time.time()),
            )
            self.db.commit()
        return {"id": identity, "name": name.strip()}

    def _row(self, row):
        return {
            **json.loads(row["metadata"]),
            "id": row["id"],
            "name": row["name"],
            "created_at": row["created"],
            "bytes": row["bytes"],
            "sha256": row["digest"],
            "device_name": self.names().get(
                json.loads(row["metadata"]).get("device_id")
            ),
        }

    def list_recordings(self):
        with self.lock:
            return [
                self._row(r)
                for r in self.db.execute(
                    "SELECT * FROM recordings ORDER BY created DESC LIMIT 200"
                )
            ]

    def get(self, identity):
        try:
            uuid.UUID(identity)
        except ValueError:
            raise KeyError("Recording not found")
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM recordings WHERE id=?", (identity,)
            ).fetchone()
        if row is None:
            raise KeyError("Recording not found")
        return self._row(row)

    def path(self, identity):
        self.get(identity)
        return self.root / (identity + ".npy")

    def load(self, identity):
        metadata = self.get(identity)
        iq = np.load(self.path(identity), allow_pickle=False)
        if iq.ndim != 1 or iq.dtype != np.complex64 or not np.isfinite(iq).all():
            raise ValueError("Saved IQ recording is invalid")
        if hashlib.sha256(iq.tobytes()).hexdigest() != metadata["sha256"]:
            raise ValueError("Saved IQ recording checksum mismatch")
        return iq, metadata

    def save(self, iq, metadata, name):
        if not name.strip() or len(name.strip()) > 80:
            raise ValueError("Capture name must contain 1–80 characters")
        iq = np.asarray(iq, dtype=np.complex64).ravel()
        if not 16 <= len(iq) <= 8_000_000 or not np.isfinite(iq).all():
            raise ValueError("Capture must contain 16–8,000,000 finite IQ samples")
        raw = iq.tobytes()
        digest = hashlib.sha256(raw).hexdigest()
        center = float(metadata["center_freq_hz"])
        rate = float(metadata["sample_rate_hz"])
        if not np.isfinite(center) or not np.isfinite(rate) or rate <= 0:
            raise ValueError("Invalid capture frequency/rate")
        with self.lock:
            old = self.db.execute(
                "SELECT * FROM recordings WHERE digest=? AND center=? AND rate=?",
                (digest, center, rate),
            ).fetchone()
            if old is not None:
                return {**self._row(old), "duplicate": True}
            identity = str(uuid.uuid4())
            target = self.root / (identity + ".npy")
            temporary = target.with_suffix(".tmp")
            with temporary.open("wb") as stream:
                np.save(stream, iq, allow_pickle=False)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(target)
            enriched = {
                **metadata,
                "center_freq_hz": center,
                "sample_rate_hz": rate,
                "samples": len(iq),
                "duration_s": len(iq) / rate,
            }
            self.db.execute(
                "INSERT INTO recordings VALUES(?,?,?,?,?,?,?,?)",
                (
                    identity,
                    name.strip(),
                    time.time(),
                    digest,
                    center,
                    rate,
                    target.stat().st_size,
                    json.dumps(enriched),
                ),
            )
            self.db.commit()
        return {**self.get(identity), "duplicate": False}

    def rename(self, identity, name):
        self.get(identity)
        if not name.strip() or len(name.strip()) > 80:
            raise ValueError("Capture name must contain 1–80 characters")
        with self.lock:
            self.db.execute(
                "UPDATE recordings SET name=? WHERE id=?", (name.strip(), identity)
            )
            self.db.commit()
        return self.get(identity)


    def list_ble(self):
        with self.lock:
            return [self._ble_row(row) for row in self.db.execute(
                "SELECT id,name,created,kind,metadata FROM ble_collections ORDER BY created DESC"
            )]

    def _ble_row(self, row, include_packets=False):
        result = {**json.loads(row["metadata"]), "id": row["id"],
                  "name": row["name"], "kind": row["kind"], "created_at": row["created"]}
        if include_packets:
            result["packets"] = json.loads(row["packets"])
        return result

    def get_ble(self, identity, include_packets=True):
        columns = "*" if include_packets else "id,name,created,kind,metadata"
        with self.lock:
            row = self.db.execute(f"SELECT {columns} FROM ble_collections WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise KeyError("Saved Bluetooth messages not found")
            return self._ble_row(row, include_packets=include_packets)

    def save_ble(self, packets, name, kind="broadcast", demo=False):
        from sigsense_api.ble_library import broadcast_key, validate_packets, replayable, device_keys
        if not name.strip() or len(name.strip()) > 80:
            raise ValueError("Name must contain 1–80 characters")
        if kind not in ("broadcast", "device"):
            raise ValueError("Choose broadcast or device")
        packets = validate_packets(packets)
        keys = list(dict.fromkeys(broadcast_key(p) for p in packets))
        if kind == "broadcast" and len(packets) != 1:
            raise ValueError("An individual broadcast must contain exactly one message")
        # Single broadcasts deduplicate across captures/channels; device snapshots
        # retain every occurrence, its channel and its receive order.
        content = keys[0] if kind == "broadcast" else json.dumps(packets, sort_keys=True)
        digest = hashlib.sha256((kind + str(bool(demo)) + content).encode()).hexdigest()
        metadata = {"message_count": len(packets), "broadcast_count": len(keys),
                    "replayable_count": sum(replayable(p) for p in packets),
                    "broadcast_key": keys[0] if kind == "broadcast" else None,
                    "demo": bool(demo), "device_keys": device_keys(packets)}
        with self.lock:
            old = self.db.execute("SELECT * FROM ble_collections WHERE digest=?", (digest,)).fetchone()
            if old is not None:
                self.db.execute("UPDATE ble_collections SET name=? WHERE id=?", (name.strip(), old["id"]))
                self.db.commit()
                return {**self.get_ble(old["id"], include_packets=False), "duplicate": True}
            identity = str(uuid.uuid4())
            self.db.execute("INSERT INTO ble_collections VALUES(?,?,?,?,?,?,?)", (
                identity, name.strip(), time.time(), kind, digest,
                json.dumps(metadata), json.dumps(packets, allow_nan=False),
            ))
            self.db.commit()
            return {**self.get_ble(identity, include_packets=False), "duplicate": False}

    def rename_ble(self, identity, name):
        self.get_ble(identity)
        if not name.strip() or len(name.strip()) > 80:
            raise ValueError("Name must contain 1–80 characters")
        with self.lock:
            self.db.execute("UPDATE ble_collections SET name=? WHERE id=?", (name.strip(), identity))
            self.db.commit()
        return self.get_ble(identity)
