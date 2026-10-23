"""Lazy labeled capture ingestion with explicit recording/session split isolation."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset

from sigsense_ml.classes import class_to_idx
from sigsense_ml.preprocess import iq_to_spectrogram


class CaptureDataset(Dataset):
    """JSONL rows: path, label, group, split; optional start/count, format.

    Paths are relative to the manifest. Formats: npy (1-D complex), cf32_le,
    cf32_be. Put every crop of a recording AND correlated recordings from a
    collection session in the same group. Split grouping is validated globally.
    """

    def __init__(self, manifest: Path, split: str):
        manifest = Path(manifest)
        self.rows = []
        groups, paths = {}, {}
        for line in manifest.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["split"] not in {"train", "val", "test"}:
                raise ValueError("Capture split must be train, val, or test")
            if row["label"] not in class_to_idx:
                raise ValueError(f"Unknown capture label: {row['label']}")
            if not str(row["group"]).strip():
                raise ValueError("Capture group must identify the recording/session")
            path = (manifest.parent / row["path"]).resolve()
            for values, key in ((groups, row["group"]), (paths, str(path))):
                if key in values and values[key] != row["split"]:
                    raise ValueError(f"Capture leakage across splits: {key}")
                values[key] = row["split"]
            if not path.is_file():
                raise FileNotFoundError(path)
            if row["split"] == split:
                self.rows.append({**row, "path": path})

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        row = self.rows[index]
        fmt = row.get("format", "npy" if row["path"].suffix == ".npy" else "cf32_le")
        if fmt == "npy":
            iq = np.load(row["path"], mmap_mode="r", allow_pickle=False)
            if iq.ndim != 1 or not np.iscomplexobj(iq):
                raise ValueError("Capture .npy must contain a 1-D complex IQ array")
        elif fmt in {"cf32_le", "cf32_be"}:
            iq = np.memmap(row["path"], dtype="<c8" if fmt == "cf32_le" else ">c8", mode="r")
        else:
            raise ValueError(f"Unsupported capture format: {fmt}")
        start, count = int(row.get("start", 0)), int(row.get("count", 8192))
        if start < 0 or count < 512 or start + count > len(iq):
            raise ValueError(f"Invalid capture sample interval in {row['path']}")
        window = np.asarray(iq[start:start + count], dtype=np.complex64)
        if not np.isfinite(window).all():
            raise ValueError("Capture contains non-finite IQ")
        return torch.from_numpy(iq_to_spectrogram(window))[None], class_to_idx[row["label"]]
