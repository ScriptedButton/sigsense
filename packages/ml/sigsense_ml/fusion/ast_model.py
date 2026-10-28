"""Audio Spectrogram Transformer (ViT-base over overlapping 16x16 patches), our own implementation.

Matches the published AST/HF layout for ATDI-Group/AST_finetuned_SIGIDwiki
(12 pre-LN layers, 768 wide, 12 heads, patch stride 10, 128 mel x 1024 frames,
cls + distillation tokens, pooled = mean of the two, layernorm + linear head).
Verified numerically against transformers' ASTForAudioClassification in tests
that run only when that reference is importable.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.utils.checkpoint

from .loading import read_safetensors

EPS = 1e-12


class _Layer(nn.Module):
    def __init__(self, dim: int, heads: int, mlp: int):
        super().__init__()
        self.heads = heads
        self.ln1 = nn.LayerNorm(dim, eps=EPS)
        self.q, self.k, self.v, self.o = (nn.Linear(dim, dim) for _ in range(4))
        self.ln2 = nn.LayerNorm(dim, eps=EPS)
        self.fc1 = nn.Linear(dim, mlp)
        self.fc2 = nn.Linear(mlp, dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        n, t, d = x.shape
        h = self.ln1(x)
        split = lambda m: m(h).view(n, t, self.heads, d // self.heads).transpose(1, 2)
        a = F.scaled_dot_product_attention(split(self.q), split(self.k), split(self.v))
        x = x + self.o(a.transpose(1, 2).reshape(n, t, d))
        return x + self.fc2(F.gelu(self.fc1(self.ln2(x))))


class ASTEncoder(nn.Module):
    def __init__(self, dim=768, depth=12, heads=12, mlp=3072, n_mels=128, frames=1024,
                 patch=16, stride=10, labels=21):
        super().__init__()
        self.dim, self.n_mels, self.frames = dim, n_mels, frames
        self.proj = nn.Conv2d(1, dim, (patch, patch), stride=(stride, stride))
        tokens = ((n_mels - patch) // stride + 1) * ((frames - patch) // stride + 1) + 2
        self.cls = nn.Parameter(torch.zeros(1, 1, dim))
        self.dist = nn.Parameter(torch.zeros(1, 1, dim))
        self.pos = nn.Parameter(torch.zeros(1, tokens, dim))
        self.layers = nn.ModuleList(_Layer(dim, heads, mlp) for _ in range(depth))
        self.ln = nn.LayerNorm(dim, eps=EPS)
        self.head_ln = nn.LayerNorm(dim, eps=EPS)
        self.head = nn.Linear(dim, labels)
        self.grad_ckpt = False

    def forward(self, fbank: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """fbank: (N, frames, n_mels), already normalised. Returns (feature 768, logits)."""
        x = fbank.unsqueeze(1).transpose(2, 3)
        x = self.proj(x).flatten(2).transpose(1, 2)
        n = x.shape[0]
        x = torch.cat([self.cls.expand(n, -1, -1), self.dist.expand(n, -1, -1), x], 1) + self.pos
        for layer in self.layers:
            if self.grad_ckpt and self.training:   # fine-tuning on a shared GPU: trade compute for memory
                x = torch.utils.checkpoint.checkpoint(layer, x, use_reentrant=False)
            else:
                x = layer(x)
        x = self.ln(x)
        feature = self.head_ln((x[:, 0] + x[:, 1]) / 2)
        return feature, self.head(feature)


def load_ast(model_dir: str, device: str = "cpu", dtype=torch.float32) -> ASTEncoder:
    sd = read_safetensors(f"{model_dir}/model.safetensors")
    p = "audio_spectrogram_transformer."
    model = ASTEncoder()
    m = {
        "cls": p + "embeddings.cls_token", "dist": p + "embeddings.distillation_token", "pos": p + "embeddings.position_embeddings",
        "proj.weight": p + "embeddings.patch_embeddings.projection.weight", "proj.bias": p + "embeddings.patch_embeddings.projection.bias",
        "ln.weight": p + "layernorm.weight", "ln.bias": p + "layernorm.bias",
        "head_ln.weight": "classifier.layernorm.weight", "head_ln.bias": "classifier.layernorm.bias",
        "head.weight": "classifier.dense.weight", "head.bias": "classifier.dense.bias",
    }
    for i in range(len(model.layers)):
        b = f"{p}encoder.layer.{i}."
        for mine, theirs in (("ln1", "layernorm_before"), ("ln2", "layernorm_after")):
            m[f"layers.{i}.{mine}.weight"] = b + theirs + ".weight"
            m[f"layers.{i}.{mine}.bias"] = b + theirs + ".bias"
        for mine, theirs in (("q", "attention.attention.query"), ("k", "attention.attention.key"),
                             ("v", "attention.attention.value"), ("o", "attention.output.dense"),
                             ("fc1", "intermediate.dense"), ("fc2", "output.dense")):
            m[f"layers.{i}.{mine}.weight"] = b + theirs + ".weight"
            m[f"layers.{i}.{mine}.bias"] = b + theirs + ".bias"
    state = {mine: sd[theirs] for mine, theirs in m.items()}
    model.load_state_dict(state, strict=True)
    return model.to(device=device, dtype=dtype).eval()
