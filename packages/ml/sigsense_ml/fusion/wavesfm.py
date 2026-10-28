"""WavesFM-style multimodal wireless ViT encoder (spectrogram image + IQ tokenisers), our own implementation.

Layout read from the checkpoint tensor shapes (ahmedaboulfo/wavesfm, Apache-2.0
weights) and from the publicly posted fine-tuning code, read as text: an
8-block, 256-wide, 8-head pre-LN transformer; a 16x16 patch tokeniser for
224x224 single-channel images; a 16-sample-segment tokeniser for IQ with a
learned antenna embedding; per-modality FiLM scale/bias applied before the
blocks; cls token prepended. Only the encoder is built (the MAE decoder in the
checkpoint is ignored).
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .loading import read_tensor_checkpoint


class _Block(nn.Module):
    def __init__(self, dim: int, heads: int, mlp_ratio: float = 4.0):
        super().__init__()
        self.heads = heads
        self.norm1 = nn.LayerNorm(dim, eps=1e-5)
        self.qkv = nn.Linear(dim, dim * 3)
        self.proj = nn.Linear(dim, dim)
        self.norm2 = nn.LayerNorm(dim, eps=1e-5)
        self.fc1 = nn.Linear(dim, int(dim * mlp_ratio))
        self.fc2 = nn.Linear(int(dim * mlp_ratio), dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        n, t, d = x.shape
        qkv = self.qkv(self.norm1(x)).reshape(n, t, 3, self.heads, d // self.heads).permute(2, 0, 3, 1, 4)
        a = F.scaled_dot_product_attention(qkv[0], qkv[1], qkv[2])
        x = x + self.proj(a.transpose(1, 2).reshape(n, t, d))
        return x + self.fc2(F.gelu(self.fc1(self.norm2(x))))


class WavesFMEncoder(nn.Module):
    def __init__(self, dim=256, depth=8, heads=8, patch=16, img=224, iq_segment=16, iq_max_tokens=256, antennas=16):
        super().__init__()
        self.dim, self.patch, self.iq_segment = dim, patch, iq_segment
        self.cls_token = nn.Parameter(torch.zeros(1, 1, dim))
        self.vis_proj = nn.Conv2d(1, dim, patch, stride=patch)
        self.register_buffer("vis_pos", torch.zeros(1, 1 + (img // patch) ** 2, dim))
        self.iq_embed = nn.Conv1d(2, dim, iq_segment)
        self.register_buffer("iq_pos", torch.zeros(1, 1 + iq_max_tokens, dim))
        self.ant = nn.Embedding(antennas, dim)
        self.film_scale = nn.ParameterDict({"vision": nn.Parameter(torch.ones(dim)), "iq": nn.Parameter(torch.ones(dim))})
        self.film_bias = nn.ParameterDict({"vision": nn.Parameter(torch.zeros(dim)), "iq": nn.Parameter(torch.zeros(dim))})
        self.blocks = nn.ModuleList(_Block(dim, heads) for _ in range(depth))
        self.norm = nn.LayerNorm(dim, eps=1e-6)
        self.use_film = True

    def _encode(self, tokens: torch.Tensor, modality: str) -> torch.Tensor:
        n = tokens.shape[0]
        z = torch.cat([self.cls_token.expand(n, -1, -1), tokens], 1)
        if self.use_film:
            z = z * self.film_scale[modality] + self.film_bias[modality]
        for block in self.blocks:
            z = block(z)
        return self.norm(z)

    @staticmethod
    def _pool(z: torch.Tensor) -> torch.Tensor:
        """cls token and mean of the patch tokens, concatenated (2 x dim)."""
        return torch.cat([z[:, 0], z[:, 1:].mean(1)], 1)

    def forward_vision(self, images: torch.Tensor) -> torch.Tensor:
        """images: (N,1,224,224) in [-1, 1]."""
        tok = self.vis_proj(images).flatten(2).transpose(1, 2) + self.vis_pos[:, 1:]
        return self._pool(self._encode(tok, "vision"))

    def forward_iq(self, iq: torch.Tensor) -> torch.Tensor:
        """iq: (N,2,T) standardised; T is cut to a multiple of the 16-sample segment and <= 4096."""
        n, _, t = iq.shape
        seg = self.iq_segment
        t = min(t, self.iq_pos.shape[1] - 1) // seg * seg if t >= seg else seg
        if iq.shape[-1] < seg:
            iq = F.pad(iq, (0, seg - iq.shape[-1]))
        iq = iq[..., :t]
        s = t // seg
        segments = iq.unfold(2, seg, seg).permute(0, 2, 1, 3).reshape(n * s, 2, seg)
        tok = self.iq_embed(segments).squeeze(-1).view(n, s, self.dim)
        tok = tok + self.iq_pos[:, 1:1 + s] + self.ant.weight[0]
        return self._pool(self._encode(tok, "iq"))


def load_wavesfm(path: str, device: str = "cpu", dtype=torch.float32) -> WavesFMEncoder:
    sd = read_tensor_checkpoint(path, key="model")
    model = WavesFMEncoder()
    state = {
        "cls_token": sd["cls_token"], "vis_pos": sd["vis_pos_embed"], "iq_pos": sd["iq_time_pos_embed"],
        "vis_proj.weight": sd["vis_patch_embed.0.proj.weight"], "vis_proj.bias": sd["vis_patch_embed.0.proj.bias"],
        "iq_embed.weight": sd["iq_segment_embed.weight"], "iq_embed.bias": sd["iq_segment_embed.bias"],
        "ant.weight": sd["iq_ant_embed.weight"],
        "film_scale.vision": sd["mod_ln_scale.vision"], "film_scale.iq": sd["mod_ln_scale.iq"],
        "film_bias.vision": sd["mod_ln_bias.vision"], "film_bias.iq": sd["mod_ln_bias.iq"],
        "norm.weight": sd["norm.weight"], "norm.bias": sd["norm.bias"],
    }
    for i in range(len(model.blocks)):
        for mine, theirs in (("norm1", "norm1"), ("norm2", "norm2"), ("qkv", "attn.qkv"), ("proj", "attn.proj"),
                             ("fc1", "mlp.fc1"), ("fc2", "mlp.fc2")):
            for leaf in ("weight", "bias"):
                state[f"blocks.{i}.{mine}.{leaf}"] = sd[f"blocks.{i}.{theirs}.{leaf}"]
    model.load_state_dict(state, strict=True)
    return model.to(device=device, dtype=dtype).eval()
