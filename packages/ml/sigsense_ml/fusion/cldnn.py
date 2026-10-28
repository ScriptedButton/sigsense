"""CLDNN modulation classifier (conv stack + bidirectional LSTM), our own implementation.

Structure read from alirezaaminzadeh/radio-modulation-classifier's tensor
shapes. The exact activation/pooling/readout were not published; the variant
here (ReLU, two stride-2 max-pools, last LSTM output) is the one that best fits
the weights on the author's own 550-window sample set. It reproduces the card's
accuracy only partly - see docs/model-fusion-research.md.
"""
from __future__ import annotations

import torch
import torch.nn as nn

from .loading import read_tensor_checkpoint

CLASSES = ("AM-DSB", "AM-SSB", "WBFM", "QPSK", "QAM16", "QAM64", "8PSK", "BPSK", "CPFSK", "GFSK", "PAM4")


class CLDNN(nn.Module):
    def __init__(self, classes: int = 11):
        super().__init__()
        conv = lambda i, o: nn.Conv1d(i, o, 3, padding=1, bias=False)
        self.features = nn.Sequential(
            conv(2, 64), nn.BatchNorm1d(64), nn.ReLU(), conv(64, 64), nn.BatchNorm1d(64), nn.ReLU(), nn.MaxPool1d(2),
            conv(64, 64), nn.BatchNorm1d(64), nn.ReLU(), conv(64, 64), nn.BatchNorm1d(64), nn.ReLU(), nn.MaxPool1d(2))
        self.lstm = nn.LSTM(64, 128, batch_first=True, bidirectional=True)
        self.head = nn.Sequential(nn.Dropout(0.0), nn.Linear(256, classes))

    def forward(self, iq: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """iq: (N,2,1024). Returns (256-d feature, 11 logits)."""
        out, _ = self.lstm(self.features(iq).transpose(1, 2))
        feature = out[:, -1]
        return feature, self.head(feature)


def load_cldnn(path: str, device: str = "cpu") -> CLDNN:
    """Our module indices (conv/bn/relu/pool slots, head.1) coincide with the checkpoint's keys."""
    model = CLDNN()
    model.load_state_dict(read_tensor_checkpoint(path), strict=True)
    return model.to(device).eval()
