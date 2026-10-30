"""Model inference and open-set classification helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from sigsense_api.feedback import FeedbackStore
from sigsense_api.learning import Decision, UserLearner
from sigsense_api.live_classifier import LiveFamilyClassifier
from sigsense_ml.library import SignalLibrary
from sigsense_ml.meta import estimate_rf_meta
from sigsense_ml.model import SignalEmbeddingNet, load_model
from sigsense_ml.preprocess import (
    iq_to_spectrogram,
    load_iq_file,
    spectrogram_to_tensor,
    waterfall_image_to_spectrogram,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODELS = REPO_ROOT / "models"
OPEN_SET_THRESHOLD = 0.45


@dataclass
class AnalyzeResult:
    label: str
    confidence: float
    is_unknown: bool
    alternatives: list[dict]
    neighbors: list[dict]
    meta: dict
    embedding: list[float]
    # What the track capture needs to be corrected later; not sent to clients.
    spec: np.ndarray | None = None
    cnn_probs: np.ndarray | None = None
    cnn_label: str | None = None
    # Set when the user's corrections changed this prediction.
    learned: dict | None = None


class InferenceEngine:
    def __init__(self, models_dir: Path | None = None) -> None:
        self.models_dir = models_dir or DEFAULT_MODELS
        self.device = torch.device("cpu")
        weights = self.models_dir / "signal_embedding.pt"
        self.model: SignalEmbeddingNet = load_model(
            weights if weights.is_file() else None, device=self.device
        )
        self.library = SignalLibrary(
            self.models_dir / "library.sqlite",
            self.models_dir / "library.faiss",
        )
        self.threshold = OPEN_SET_THRESHOLD
        self._builtin: tuple[np.ndarray, list[str]] | None = None
        self.learner = UserLearner()
        self.feedback = FeedbackStore(self)
        family_weights = self.models_dir / "live_families.json"
        self.live_families = LiveFamilyClassifier(family_weights) if family_weights.is_file() else None

    def embed_specs(self, specs: list[np.ndarray]) -> np.ndarray:
        """L2-normalised embeddings for a batch of model-input spectrograms."""
        batch = np.stack([np.asarray(s, dtype=np.float32) for s in specs])[:, None]
        with torch.no_grad():
            _, emb = self.model(torch.from_numpy(batch).float().to(self.device))
        return emb.cpu().numpy()

    def competitor_similarity(self, emb: np.ndarray, label: str) -> float | None:
        """Cosine from an embedding to the nearest built-in class that is not `label`."""
        if self._builtin is None:
            self._builtin = self.library.source_vectors("builtin")
        vectors, names = self._builtin
        if not len(names):
            return None
        keep = np.array([n != label for n in names])
        if not keep.any():
            return None
        e = np.asarray(emb, dtype=np.float32)
        e = e / max(float(np.linalg.norm(e)), 1e-9)
        return float((vectors[keep] @ e).max())

    def decide(self, cnn_probs: np.ndarray, emb: np.ndarray) -> dict:
        """Final label from CNN probabilities plus the user's corrections.

        Used for live predictions and to re-evaluate a stored capture right
        after a correction, so both always agree.
        """
        learner = self.__dict__.get("learner")  # absent on a bare/test engine
        classes = list(self.model.classes)
        decision = (
            learner.apply(cnn_probs, classes, emb)
            if learner is not None
            else Decision(classes, cnn_probs, None)
        )
        order = np.argsort(decision.probs)[::-1][:5]
        alternatives = [
            {"label": decision.labels[int(i)], "confidence": float(decision.probs[int(i)])}
            for i in order
        ]
        confidence = alternatives[0]["confidence"]
        learned = decision.learned
        # A confident match to the user's own examples is a known signal.
        is_unknown = confidence < self.threshold and not (
            learned and learned["weight"] >= 0.5
        )
        return {
            "label": "unknown" if is_unknown else alternatives[0]["label"],
            "confidence": confidence,
            "is_unknown": is_unknown,
            "alternatives": alternatives,
            "learned": learned,
        }

    def class_names(self) -> list[str]:
        families = self.live_families.classes if self.live_families is not None else []
        return list(dict.fromkeys([*self.model.classes, *families, *self.learner.custom_labels]))

    def _neighbors(self, emb: np.ndarray, count: int = 5) -> list[dict]:
        """Library neighbours, user corrections first and wiki screenshots last.

        The Artemis screenshots outnumber everything else and separate poorly in
        this embedding (mean pairwise cosine 0.85), so left alone they crowd out
        the neighbours that mean something. They keep at most two slots.
        """
        found = self.library.search(emb, top_k=max(40, count * 8))
        bonus = {"user": 0.06, "builtin": 0.0, "artemis": -0.10}
        ranked = sorted(
            found,
            key=lambda n: n["similarity"] + bonus.get(n.get("source"), 0.0),
            reverse=True,
        )
        out: list[dict] = []
        artemis = 0
        for n in ranked:
            if n.get("source") == "artemis":
                if artemis >= 2:
                    continue
                artemis += 1
            out.append(n)
            if len(out) >= count:
                break
        return out

    def _predict_from_spec(
        self,
        spec: np.ndarray,
        *,
        iq: np.ndarray | None = None,
        sample_rate: float | None = None,
        center_freq: float | None = None,
    ) -> AnalyzeResult:
        tensor = torch.from_numpy(spectrogram_to_tensor(spec)).float().to(self.device)
        with torch.no_grad():
            logits, emb = self.model(tensor)
            probs = F.softmax(logits / self.model.temperature, dim=1)[0]
            cnn_probs = probs.cpu().numpy()
            emb_np = emb[0].cpu().numpy()

        cnn_label = self.model.classes[int(np.argmax(cnn_probs))]
        decided = self.decide(cnn_probs, emb_np)
        alternatives = decided["alternatives"]
        learned = decided["learned"]
        neighbors = self._neighbors(emb_np)
        is_unknown = decided["is_unknown"]
        label = decided["label"]
        confidence = decided["confidence"]

        # Cosine similarity is not a calibrated class probability. Keep neighbors
        # as suggestions; do not use them to override the unknown decision.
        if is_unknown:
            label = "unknown"

        meta = estimate_rf_meta(
            iq=iq,
            spectrogram=spec if iq is None else None,
            sample_rate=sample_rate,
            center_freq=center_freq,
        )

        return AnalyzeResult(
            label=label,
            confidence=confidence,
            is_unknown=is_unknown,
            alternatives=alternatives,
            neighbors=neighbors,
            meta=meta,
            embedding=emb_np.tolist(),
            spec=np.asarray(spec, dtype=np.float32),
            cnn_probs=cnn_probs,
            cnn_label=cnn_label,
            learned=learned,
        )

    def analyze_image(self, data: bytes) -> AnalyzeResult:
        spec = waterfall_image_to_spectrogram(data)
        return self._predict_from_spec(spec)

    def analyze_iq(
        self,
        data: bytes,
        *,
        dtype: str = "auto",
        sample_rate: float | None = None,
        center_freq: float | None = None,
        filename: str | None = None,
    ) -> AnalyzeResult:
        iq, wav_rate = load_iq_file(
            data, dtype=dtype, sample_rate=sample_rate, filename=filename
        )
        rate = sample_rate if sample_rate is not None else wav_rate
        if iq.size == 0:
            raise ValueError("Empty IQ buffer")
        # Use a window from the middle for long captures
        if iq.size > 65536:
            mid = iq.size // 2
            iq = iq[mid - 32768 : mid + 32768]
        spec = iq_to_spectrogram(iq)
        return self._predict_from_spec(
            spec, iq=iq, sample_rate=rate, center_freq=center_freq
        )

    def analyze_spectrogram(self, spec: np.ndarray, **kwargs) -> AnalyzeResult:
        return self._predict_from_spec(spec, **kwargs)

    def enroll(
        self, embedding: list[float] | np.ndarray, label: str, notes: str = ""
    ) -> int:
        return self.library.enroll(np.asarray(embedding, dtype=np.float32), label, notes=notes)

    def list_library(self) -> list[dict]:
        return self.library.list_classes()
