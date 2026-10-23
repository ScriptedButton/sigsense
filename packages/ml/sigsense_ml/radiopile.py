"""Native RadioPile HDF5 ingestion with source/recording-aware partitions."""
from __future__ import annotations

import hashlib
from pathlib import Path

import h5py
import numpy as np
import torch
from torch.utils.data import Dataset

from sigsense_ml.classes import class_to_idx
from sigsense_ml.preprocess import iq_to_spectrogram

COMPACT_SOURCES = ("rml2016_10a", "its_band", "lte_wifi_dvbt")
LABEL_MAP = {
    "QPSK": "QPSK", "PAM4": "ASK4", "AM-DSB": "AM", "GFSK": "GFSK",
    "64QAM": "QAM64", "AM-SSB": "USB", "8PSK": "8PSK", "16QAM": "QAM16",
    "WBFM": "WFM", "CPFSK": "FSK", "BPSK": "BPSK", "LTE": "LTE",
    "5G NR": "5G-NR", "ITS-G5": "ITS-G5", "C-V2X PC5": "C-V2X",
    "WiFi": "WiFi", "Noise": "Noise", "DVB-T": "DVB-T",
}


def partition_groups(groups, labels, seed=42):
    """Stratify single-label groups, preserving every shared group atomically."""
    by_group = {}
    for group, label in zip(groups, labels):
        by_group.setdefault(str(group), set()).add(int(label))
    # Multi-label groups stay atomic and are stratified by their label set.
    strata = {}
    for group, targets in by_group.items():
        strata.setdefault(tuple(sorted(targets)), []).append(group)
    assignments = {}
    for targets, members in sorted(strata.items()):
        # Stable across processes, Python hash randomization, caps and augmentations.
        members.sort(key=lambda g: hashlib.sha256(f"{seed}:{g}".encode()).digest())
        if len(members) < 3:
            raise ValueError(f"Need at least 3 independent groups for labels {targets}; found {len(members)}")
        train_end = max(1, min(len(members)-2, int(.7 * len(members))))
        val_end = max(train_end+1, min(len(members)-1, int(.85 * len(members))))
        for i, group in enumerate(members):
            assignments[group] = "train" if i < train_end else "val" if i < val_end else "test"
    return np.array([assignments[str(group)] for group in groups])


class RadioPileSpectrogramDataset(Dataset):
    """Materialize a bounded stratified subset, retaining split evidence.

    Source HDF5 is read a row at a time for selected IQ only. Labels/metadata are
    read first, and correlated windows are partitioned before per-class sampling.
    RadioML has no capture groups and therefore uses source-example splits.
    """
    def __init__(self, path: Path, split: str, samples_per_class=256, seed=42, snr_min=0.):
        self.items, self.snrs, self.group_ids, self.row_indices = [], [], [], []
        self.source = Path(path).stem
        if split not in {"train", "val", "test"}:
            raise ValueError(f"Invalid split: {split}")
        with h5py.File(path, "r") as f:
            names = list(f["label_names"].asstr()[:])
            unknown = set(names) - set(LABEL_MAP)
            if unknown:
                raise ValueError(f"Unmapped labels in {path}: {sorted(unknown)}")
            original_labels = np.asarray(f["labels"][:]).reshape(-1)
            labels = np.array([class_to_idx[LABEL_MAP[names[int(i)]]] for i in original_labels])
            meta = f["meta"]
            snrs = np.asarray(meta["snr_db"][:], dtype=np.float32) if "snr_db" in meta else np.full(len(labels), np.nan)
            if self.source == "rml2016_10a":
                groups = np.array([f"{self.source}:example:{i}" for i in range(len(labels))])
                self.split_scope = "RadioML synthetic-example split; capture/session independence unavailable"
            else:
                for field in ("file_path", "recording_id"):
                    if field not in meta:
                        raise ValueError(f"Missing grouping metadata: {field}")
                paths = meta["file_path"].asstr()[:]
                recordings = meta["recording_id"].asstr()[:]
                if any(not str(r).strip() for r in recordings):
                    raise ValueError("Captured source contains empty recording IDs")
                groups = np.array([f"{self.source}:{p}:{r}" for p, r in zip(paths, recordings)])
                self.split_scope = "Grouped by source + file_path + recording_id; original session independence must be audited"
            partitions = partition_groups(groups, labels, seed)
            # Unknown SNR in real captures is retained, never treated as 0 dB.
            eligible = (partitions == split) & (~np.isfinite(snrs) | (snrs >= snr_min))
            rng = np.random.default_rng(seed + {"train": 0, "val": 1_000_000, "test": 2_000_000}[split])
            for label in sorted(set(labels)):
                indices = np.flatnonzero(eligible & (labels == label))
                rng.shuffle(indices)
                for index in sorted(indices[:samples_per_class]):
                    channels = np.asarray(f["data"][int(index)], dtype=np.float32)
                    iq = channels[0] + 1j * channels[1]
                    if not np.isfinite(iq).all():
                        raise ValueError(f"Non-finite IQ in {path}, row {index}")
                    # Short native windows are zero-padded by the normal STFT path;
                    # never tile a burst and pretend it has more temporal evidence.
                    self.items.append((iq_to_spectrogram(iq), int(label)))
                    self.snrs.append(float(snrs[index]))
                    self.group_ids.append(str(groups[index]))
                    self.row_indices.append(int(index))
        print(f"  {self.source} {split}: {len(self.items)} samples, {len(set(self.group_ids))} groups", flush=True)

    def __len__(self):
        return len(self.items)

    def __getitem__(self, index):
        x, y = self.items[index]
        return torch.from_numpy(x)[None], y
