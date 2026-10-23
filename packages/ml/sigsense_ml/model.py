"""Embedding CNN for spectrogram classification / open-set matching."""

from __future__ import annotations

from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

from sigsense_ml.classes import EMBEDDING_DIM, NUM_CLASSES


class ConvBlock(nn.Module):
    def __init__(self, in_ch: int, out_ch: int) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(in_ch, out_ch, 3, padding=1, bias=False),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_ch, out_ch, 3, padding=1, bias=False),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class SignalEmbeddingNet(nn.Module):
    """Compact CNN: spectrogram (1x256x256) -> embedding + logits."""

    def __init__(
        self,
        num_classes: int = NUM_CLASSES,
        embedding_dim: int = EMBEDDING_DIM,
    ) -> None:
        super().__init__()
        self.embedding_dim = embedding_dim
        self.features = nn.Sequential(
            ConvBlock(1, 32),
            ConvBlock(32, 64),
            ConvBlock(64, 128),
            ConvBlock(128, 256),
            # 256x256 -> 16x16 after 4 pools
        )
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.embedding = nn.Sequential(
            nn.Flatten(),
            nn.Linear(256, embedding_dim),
            nn.ReLU(inplace=True),
            nn.Dropout(0.2),
        )
        self.classifier = nn.Linear(embedding_dim, num_classes)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = self.features(x)
        h = self.pool(h)
        emb = self.embedding(h)
        emb_n = F.normalize(emb, p=2, dim=1)
        logits = self.classifier(emb)
        return logits, emb_n

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        _, emb = self.forward(x)
        return emb


def load_model(
    path: str | Path | None = None,
    *,
    device: str | torch.device | None = None,
    num_classes: int = NUM_CLASSES,
) -> SignalEmbeddingNet:
    """Load weights if present; otherwise return randomly initialized model."""
    if device is None:
        device = torch.device("cpu")
    model = SignalEmbeddingNet(num_classes=num_classes)
    if path is not None and Path(path).is_file():
        ckpt = torch.load(path, map_location=device, weights_only=False)
        state = ckpt.get("model_state", ckpt)
        model.load_state_dict(state)
        if "num_classes" in ckpt and ckpt["num_classes"] != num_classes:
            raise ValueError(
                f"Checkpoint num_classes={ckpt['num_classes']} != {num_classes}"
            )
    model.to(device)
    model.eval()
    return model
