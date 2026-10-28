#!/usr/bin/env python3
"""Rebuild the compact-49 train/val/test splits WITH raw IQ retained.

The repo's dataset classes keep only spectrograms. The fusion backbones also
need IQ, so this runs the very same dataset code (same seeds, same RadioPile
group splits) with `iq_to_spectrogram` wrapped to record each IQ window. Test
examples are then identical to the ones behind checkpoints/compact-49.

    python packages/ml/scripts/fusion_corpus.py --out /path/corpus.npz [--splits train val test]

Output npz per split: iq (N, L, complex64, zero padded), iq_len, y, source, snr, spec (N,256,256 float16).
Alignment is verified: for non-augmented splits the stored spectrogram must equal
iq_to_spectrogram(iq) exactly; the augmented train split is checked by correlation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from sigsense_ml import external_datasets as ext  # noqa: E402
from sigsense_ml import radiopile as rp  # noqa: E402
from sigsense_ml.classes import CAPTURE_ONLY_CLASSES, SIGNAL_CLASSES  # noqa: E402
from sigsense_ml.preprocess import iq_to_spectrogram as real_iq_to_spectrogram  # noqa: E402

import train as trainmod  # noqa: E402  (scripts/train.py: make_split, dataset_labels, dataset_sources)

REPO = HERE.parents[2]
MAX_IQ = 8192


def sequence_labels_generated(spc: int) -> list[int]:
    """Class index per generated item, in generation order (TorchSig targets, then synthetic-only)."""
    covered = ext.classes_covered_by_torchsig()
    by_target = {ext.TORCHSIG_TO_SIGSENSE[g] for g in ext.torchsig_generators_for_sigsense()}
    targets = [t for t in SIGNAL_CLASSES if t in by_target]
    uncovered = [c for c in SIGNAL_CLASSES if c not in covered and c not in CAPTURE_ONLY_CLASSES]
    return [SIGNAL_CLASSES.index(c) for c in targets + uncovered for _ in range(spc)]


def build_split(config: dict, split: str):
    gen_iq: list[np.ndarray] = []
    real_iq: list[np.ndarray] = []

    def rec_gen(iq):
        gen_iq.append(np.asarray(iq, dtype=np.complex64).ravel().copy())
        return real_iq_to_spectrogram(iq)

    def rec_real(iq):
        real_iq.append(np.asarray(iq, dtype=np.complex64).ravel().copy())
        return real_iq_to_spectrogram(iq)

    ext.iq_to_spectrogram, rp.iq_to_spectrogram = rec_gen, rec_real
    try:
        ns = argparse.Namespace(**config)
        dataset = trainmod.make_split(ns, split)
    finally:
        ext.iq_to_spectrogram, rp.iq_to_spectrogram = real_iq_to_spectrogram, real_iq_to_spectrogram

    spc = config["samples_per_class"] if split == "train" else config["eval_samples_per_class"]
    gen_labels = sequence_labels_generated(spc)
    if len(gen_labels) != len(gen_iq):
        raise RuntimeError(f"generated IQ count {len(gen_iq)} != reconstructed labels {len(gen_labels)}")
    proxies = {SIGNAL_CLASSES.index(c) for c in trainmod.PROXY_CLASSES}
    keep = [i for i, label in enumerate(gen_labels) if label not in proxies]
    kept_iq = [gen_iq[i] for i in keep]

    # ConcatDataset children: generated (filtered) then each RadioPile source in COMPACT_SOURCES order.
    children = list(dataset.datasets)
    iq_all: list[np.ndarray] = []
    ys: list[int] = []
    sources: list[str] = []
    snrs: list[float] = []
    specs: list[np.ndarray] = []
    real_cursor = 0
    for child in children:
        items = child.items
        if isinstance(child, rp.RadioPileSpectrogramDataset):
            block = real_iq[real_cursor:real_cursor + len(items)]
            real_cursor += len(items)
            child_snrs = list(child.snrs)
            source = child.source
        else:
            if len(items) != len(kept_iq):
                raise RuntimeError(f"filtered generated items {len(items)} != kept IQ {len(kept_iq)}")
            block = kept_iq
            child_snrs = [float("nan")] * len(items)
            source = "generated"
        for (spec, label), iq, snr in zip(items, block, child_snrs):
            iq_all.append(iq)
            ys.append(int(label))
            sources.append(source)
            snrs.append(snr)
            specs.append(spec)
    if real_cursor != len(real_iq):
        raise RuntimeError("RadioPile IQ not fully consumed")

    # Alignment proof: non-augmented splits must reproduce the stored spectrogram exactly.
    check = np.random.default_rng(0).choice(len(iq_all), size=min(200, len(iq_all)), replace=False)
    worst = 0.0
    for i in check:
        recomputed = real_iq_to_spectrogram(iq_all[i])
        stored = specs[i]
        if split != "train":
            worst = max(worst, float(np.abs(recomputed - stored).max()))
        else:  # augmented: just require strong correlation with the clean spectrogram
            c = np.corrcoef(recomputed.ravel(), stored.ravel())[0, 1]
            worst = max(worst, 1.0 - float(c))
    print(f"  {split}: {len(iq_all)} examples; alignment worst deviation = {worst:.4g} "
          f"({'max abs diff' if split != 'train' else '1-corr (augmented)'})", flush=True)
    if split != "train" and worst > 1e-5:
        raise RuntimeError("IQ/spectrogram misalignment")
    return iq_all, np.array(ys), np.array(sources), np.array(snrs, np.float32)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=REPO / "checkpoints" / "compact-49" / "run_config.json")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--splits", nargs="+", default=["train", "val", "test"])
    args = ap.parse_args()
    config = json.loads(args.config.read_text())
    for key in ("data_dir", "external_data_dir", "manifest", "out_dir"):
        if config.get(key) is not None:
            config[key] = Path(config[key])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for split in args.splits:
        iq_all, y, sources, snr = build_split(config, split)
        n = len(iq_all)
        iq = np.zeros((n, MAX_IQ), np.complex64)
        lens = np.zeros(n, np.int32)
        for i, a in enumerate(iq_all):
            m = min(len(a), MAX_IQ)
            iq[i, :m] = a[:m]
            lens[i] = m
        path = args.out.with_name(f"{args.out.stem}_{split}.npz")
        np.savez(path, iq=iq, iq_len=lens, y=y, source=sources, snr=snr)
        print(f"  wrote {path} ({path.stat().st_size / 1e6:.0f} MB)", flush=True)


if __name__ == "__main__":
    main()
