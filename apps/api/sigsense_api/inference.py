"""Model inference and open-set classification helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from sigsense_ml.classes import SIGNAL_CLASSES, idx_to_class
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
NEIGHBOR_THRESHOLD = 0.55


@dataclass
class AnalyzeResult:
    label: str
    confidence: float
    is_unknown: bool
    alternatives: list[dict]
    neighbors: list[dict]
    meta: dict
    embedding: list[float]


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
            probs = F.softmax(logits, dim=1)[0]
            conf, pred_idx = torch.max(probs, dim=0)
            conf_f = float(conf.item())
            pred_label = idx_to_class[int(pred_idx.item())]
            topk = torch.topk(probs, k=min(5, probs.numel()))
            alternatives = [
                {"label": idx_to_class[int(i.item())], "confidence": float(p.item())}
                for p, i in zip(topk.values, topk.indices)
            ]
            emb_np = emb[0].cpu().numpy()

        neighbors = self.library.search(emb_np, top_k=5)
        is_unknown = conf_f < self.threshold
        label = pred_label
        confidence = conf_f

        # If low classifier confidence, prefer library neighbor when strong
        if is_unknown and neighbors and neighbors[0]["similarity"] >= NEIGHBOR_THRESHOLD:
            label = neighbors[0]["label"]
            confidence = float(neighbors[0]["similarity"])
            is_unknown = False
        elif is_unknown:
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
