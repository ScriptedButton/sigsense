"""User corrections: storage, persistence and the live decision data.

Each correction keeps the exact model input (a 256x256 spectrogram) and the
embeddings derived from it, under `<models>/user_labels/`:

    index.json        what was corrected, in order
    <id>.npz          spec (float16) + embedding

The real example's embedding is also enrolled in the FAISS library as
source="user" so it shows up in neighbour suggestions. Embeddings are tied to
the model that made them; if the weights change they are recomputed from the
stored spectrograms.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from sigsense_api.learning import UserLearner

NOISE_LABEL = "Noise"
MAX_LABEL_LEN = 48
INDEX_VERSION = 1
_ID = re.compile(r"^[0-9a-f]{12}$")


class FeedbackError(ValueError):
    """A correction that cannot be stored (bad label, no capture, ...)."""


def canonical_label(label: str, known: list[str]) -> str:
    """Normalise a user-typed label; map case variants onto known class names."""
    raw = (label or "").strip()
    if raw == "__noise__":
        return NOISE_LABEL
    raw = re.sub(r"\s+", " ", raw)
    if not raw:
        raise FeedbackError("A label is required")
    if len(raw) > MAX_LABEL_LEN:
        raise FeedbackError(f"Label is longer than {MAX_LABEL_LEN} characters")
    if any(ord(c) < 32 for c in raw):
        raise FeedbackError("Label contains control characters")
    if raw.lower() in ("unknown", "none"):
        raise FeedbackError("Pick a real class, a custom name, or 'Not a signal'")
    for name in known:
        if name.lower() == raw.lower():
            return name
    return raw


def _weights_fingerprint(models_dir: Path) -> str:
    weights = models_dir / "signal_embedding.pt"
    if not weights.is_file():
        return "untrained"
    stat = weights.stat()
    return f"{stat.st_size}-{int(stat.st_mtime)}"


def thumbnail_png(spec: np.ndarray, size: int = 64) -> bytes:
    img = Image.fromarray((np.clip(spec, 0, 1) * 255).astype(np.uint8), mode="L")
    img = img.resize((size, size), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FeedbackStore:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.dir = Path(engine.models_dir) / "user_labels"
        self.learner: UserLearner = engine.learner
        self._lock = threading.RLock()
        self._fp = _weights_fingerprint(Path(engine.models_dir))
        self._items: list[dict] = []
        self._backed_up = False
        self._load()

    # ---- persistence -----------------------------------------------------

    @property
    def _index_path(self) -> Path:
        return self.dir / "index.json"

    def _load(self) -> None:
        with self._lock:
            if not self._index_path.is_file():
                self._items = []
                self.learner.set_prototypes([])
                return
            try:
                data = json.loads(self._index_path.read_text())
            except (OSError, ValueError):
                data = {"items": []}
            items = [i for i in data.get("items", []) if _ID.match(str(i.get("id", "")))]
            self._items = [i for i in items if (self.dir / f"{i['id']}.npz").is_file()]
            stale = data.get("model_fp") != self._fp
            if stale and self._items:
                self._reembed_all()
            self._publish()

    def _save_index(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {"version": INDEX_VERSION, "model_fp": self._fp, "items": self._items},
                indent=1,
            )
        )
        tmp.replace(self._index_path)

    def _backup_library_once(self) -> None:
        """Keep a copy of the library as it was before any user label touched it."""
        if self._backed_up:
            return
        self._backed_up = True
        self.dir.mkdir(parents=True, exist_ok=True)
        backup = self.dir / "backup"
        if backup.exists():
            return
        backup.mkdir()
        for path in (self.engine.library.db_path, self.engine.library.index_path):
            if Path(path).is_file():
                shutil.copy2(path, backup / Path(path).name)

    def _npz(self, example_id: str) -> Path:
        return self.dir / f"{example_id}.npz"

    def _read(self, example_id: str) -> dict:
        with np.load(self._npz(example_id)) as z:
            return {
                "spec": z["spec"].astype(np.float32),
                "embeddings": z["embeddings"].astype(np.float32),
            }

    def _write(self, example_id: str, spec: np.ndarray, embeddings: np.ndarray) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f"{example_id}.tmp.npz"
        np.savez_compressed(
            tmp, spec=spec.astype(np.float16), embeddings=embeddings.astype(np.float32)
        )
        tmp.replace(self._npz(example_id))

    def _embed_example(self, spec: np.ndarray) -> np.ndarray:
        return self.engine.embed_specs([spec])

    def _reembed_all(self) -> None:
        for item in self._items:
            data = self._read(item["id"])
            self._write(item["id"], data["spec"], self._embed_example(data["spec"]))
        self._fp = _weights_fingerprint(Path(self.engine.models_dir))
        self._save_index()

    def _publish(self) -> None:
        rows = []
        for item in self._items:
            try:
                data = self._read(item["id"])
            except (OSError, ValueError, KeyError):
                continue
            rows.append(
                {
                    "label": item["label"],
                    "embeddings": data["embeddings"],
                    "wrong_label": item.get("wrong_label"),
                    "competitor": self.engine.competitor_similarity(
                        data["embeddings"][0], item["label"]
                    ),
                }
            )
        self.learner.set_prototypes(rows)

    # ---- queries ---------------------------------------------------------

    def known_classes(self) -> list[str]:
        return list(self.engine.model.classes)

    def custom_labels(self) -> list[str]:
        return sorted(
            {i["label"] for i in self._items if i["label"] not in self.engine.model.classes}
        )

    def list(self) -> dict:
        with self._lock:
            counts: dict[str, int] = {}
            for item in self._items:
                counts[item["label"]] = counts.get(item["label"], 0) + 1
            return {
                "items": [dict(i) for i in reversed(self._items)],
                "counts": counts,
                "total": len(self._items),
                "classes": self.known_classes(),
                "custom": self.custom_labels(),
            }

    def thumbnail(self, example_id: str) -> bytes | None:
        if not _ID.match(example_id) or not self._npz(example_id).is_file():
            return None
        return thumbnail_png(self._read(example_id)["spec"])

    # ---- changes ---------------------------------------------------------

    def add(
        self,
        spec: np.ndarray,
        label: str,
        *,
        note: str = "",
        wrong_label: str | None = None,
        track_id: str | None = None,
        source: str = "track",
        example_id: str | None = None,
    ) -> dict:
        label = canonical_label(label, self.known_classes() + self.custom_labels())
        spec = np.asarray(spec, dtype=np.float32)
        if spec.ndim != 2 or min(spec.shape) < 8:
            raise FeedbackError("The captured signal has no usable spectrogram")
        with self._lock:
            self._backup_library_once()
            example_id = example_id or uuid.uuid4().hex[:12]
            embeddings = self._embed_example(spec)
            self._write(example_id, spec, embeddings)
            note = (note or "").strip()[:200]
            item = {
                "id": example_id,
                "label": label,
                "note": note,
                "created_at": time.time(),
                "wrong_label": wrong_label if wrong_label and wrong_label != label else None,
                "track_id": track_id,
                "source": source,
            }
            self._items.append(item)
            self.engine.library.enroll(
                embeddings[0], label, source="user", notes=f"user:{example_id}:{note}"
            )
            self._save_index()
            self._publish()
            return dict(item)

    def remove(self, example_id: str) -> bool:
        with self._lock:
            if not any(i["id"] == example_id for i in self._items):
                return False
            self._drop([example_id])
            return True

    def remove_label(self, label: str) -> int:
        with self._lock:
            ids = [i["id"] for i in self._items if i["label"].lower() == label.lower()]
            if ids:
                self._drop(ids)
            return len(ids)

    def clear(self) -> int:
        with self._lock:
            ids = [i["id"] for i in self._items]
            if ids:
                self._drop(ids)
            return len(ids)

    def _drop(self, ids: list[str]) -> None:
        doomed = set(ids)
        self._items = [i for i in self._items if i["id"] not in doomed]
        for example_id in doomed:
            self.engine.library.remove_notes("user", f"user:{example_id}:")
            try:
                self._npz(example_id).unlink()
            except OSError:
                pass
        self._save_index()
        self._publish()

    # ---- export / import -------------------------------------------------

    def export_zip(self) -> bytes:
        with self._lock:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr(
                    "index.json",
                    json.dumps({"version": INDEX_VERSION, "items": self._items}, indent=1),
                )
                for item in self._items:
                    z.write(self._npz(item["id"]), f"{item['id']}.npz")
            return buf.getvalue()

    def import_zip(self, data: bytes) -> int:
        """Add the examples from an export; embeddings are recomputed here."""
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            index = json.loads(z.read("index.json"))
        except (zipfile.BadZipFile, KeyError, ValueError) as exc:
            raise FeedbackError("Not a SigSense corrections export") from exc
        added = 0
        existing = {i["id"] for i in self._items}
        for item in index.get("items", [])[:5000]:
            example_id = str(item.get("id", ""))
            if not _ID.match(example_id) or example_id in existing:
                continue
            try:
                with np.load(io.BytesIO(z.read(f"{example_id}.npz"))) as arrays:
                    spec = arrays["spec"].astype(np.float32)
            except (KeyError, ValueError, OSError):
                continue
            try:
                self.add(
                    spec,
                    str(item.get("label", "")),
                    note=str(item.get("note", "")),
                    wrong_label=item.get("wrong_label"),
                    track_id=None,
                    source="import",
                    example_id=example_id,
                )
                added += 1
            except FeedbackError:
                continue
        return added
