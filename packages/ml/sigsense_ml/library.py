"""FAISS + SQLite signal library for open-set matching and enrollment."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import faiss
import numpy as np

from sigsense_ml.classes import EMBEDDING_DIM, SIGNAL_CLASSES


class SignalLibrary:
    def __init__(
        self,
        db_path: str | Path,
        index_path: str | Path,
        *,
        embedding_dim: int = EMBEDDING_DIM,
    ) -> None:
        self.db_path = Path(db_path)
        self.index_path = Path(index_path)
        self.embedding_dim = embedding_dim
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()
        self._index = self._load_or_create_index()
        self._id_map: list[int] = self._load_id_map()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prototypes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                source TEXT DEFAULT 'builtin',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def _load_or_create_index(self) -> faiss.IndexFlatIP:
        if self.index_path.is_file():
            return faiss.read_index(str(self.index_path))
        return faiss.IndexFlatIP(self.embedding_dim)

    def _load_id_map(self) -> list[int]:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'id_map'"
        ).fetchone()
        if row:
            return json.loads(row["value"])
        return []

    def _save_id_map(self) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('id_map', ?)",
            (json.dumps(self._id_map),),
        )
        self._conn.commit()

    def save(self) -> None:
        faiss.write_index(self._index, str(self.index_path))
        self._save_id_map()

    def enroll(
        self,
        embedding: np.ndarray,
        label: str,
        *,
        source: str = "user",
        notes: str = "",
    ) -> int:
        emb = np.asarray(embedding, dtype=np.float32).reshape(1, -1)
        faiss.normalize_L2(emb)
        cur = self._conn.execute(
            "INSERT INTO prototypes(label, source, notes) VALUES (?, ?, ?)",
            (label, source, notes),
        )
        self._conn.commit()
        pid = int(cur.lastrowid)
        self._index.add(emb)
        self._id_map.append(pid)
        self.save()
        return pid

    def list_classes(self) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT label, source, COUNT(*) as count
            FROM prototypes
            GROUP BY label, source
            ORDER BY label
            """
        ).fetchall()
        builtin = [
            {"label": c, "source": "builtin", "count": 0, "enrolled": False}
            for c in SIGNAL_CLASSES
        ]
        by_label = {b["label"]: b for b in builtin}
        for r in rows:
            label = r["label"]
            entry = by_label.get(label)
            if entry is None:
                by_label[label] = {
                    "label": label,
                    "source": r["source"],
                    "count": r["count"],
                    "enrolled": True,
                }
            else:
                entry["count"] += r["count"]
                entry["enrolled"] = entry["count"] > 0 or r["source"] == "user"
                if r["source"] == "user":
                    entry["source"] = "user"
        return sorted(by_label.values(), key=lambda x: x["label"].lower())

    def search(
        self, embedding: np.ndarray, *, top_k: int = 5
    ) -> list[dict]:
        if self._index.ntotal == 0:
            return []
        emb = np.asarray(embedding, dtype=np.float32).reshape(1, -1)
        faiss.normalize_L2(emb)
        k = min(top_k, self._index.ntotal)
        scores, indices = self._index.search(emb, k)
        results: list[dict] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._id_map):
                continue
            pid = self._id_map[idx]
            row = self._conn.execute(
                "SELECT id, label, source, notes FROM prototypes WHERE id = ?",
                (pid,),
            ).fetchone()
            if row:
                results.append(
                    {
                        "id": row["id"],
                        "label": row["label"],
                        "source": row["source"],
                        "notes": row["notes"],
                        "similarity": float(score),
                    }
                )
        return results

    def seed_from_embeddings(
        self, labels: list[str], embeddings: np.ndarray, *, source: str = "builtin"
    ) -> None:
        """Bulk-enroll class prototypes (used after training)."""
        embeddings = np.asarray(embeddings, dtype=np.float32)
        assert len(labels) == embeddings.shape[0]
        faiss.normalize_L2(embeddings)
        for label, emb in zip(labels, embeddings):
            cur = self._conn.execute(
                "INSERT INTO prototypes(label, source, notes) VALUES (?, ?, ?)",
                (label, source, "training prototype"),
            )
            pid = int(cur.lastrowid)
            self._id_map.append(pid)
        self._index.add(embeddings)
        self.save()
