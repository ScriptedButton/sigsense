#!/usr/bin/env python3
"""Train SigSense embedding CNN on synthetic / TorchSig / RadioML spectrograms."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import ConcatDataset, DataLoader, Dataset

# Allow running without install
ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
sys.path.insert(0, str(ROOT))

from sigsense_ml.classes import (  # noqa: E402
    EMBEDDING_DIM,
    NUM_CLASSES,
    SIGNAL_CLASSES,
    class_to_idx,
)
from sigsense_ml.external_datasets import (  # noqa: E402
    MixedSpectrogramDataset,
    RadioMLSpectrogramDataset,
    SyntheticSpectrogramDataset,
    TorchSigSpectrogramDataset,
)
from sigsense_ml.library import SignalLibrary  # noqa: E402
from sigsense_ml.model import SignalEmbeddingNet  # noqa: E402
from sigsense_ml.preprocess import iq_to_spectrogram  # noqa: E402
from sigsense_ml.synthetic import generate_iq  # noqa: E402


def build_datasets(args: argparse.Namespace) -> tuple[Dataset, Dataset]:
    spc = args.samples_per_class
    val_spc = max(4, spc // 4)
    source = args.source

    if source == "synthetic":
        train_ds: Dataset = SyntheticSpectrogramDataset(spc, seed=42)
        val_ds: Dataset = SyntheticSpectrogramDataset(val_spc, seed=99)
    elif source == "torchsig":
        train_ds = TorchSigSpectrogramDataset(spc, seed=42)
        val_ds = TorchSigSpectrogramDataset(val_spc, seed=99)
    elif source == "mixed":
        train_ds = MixedSpectrogramDataset(spc, seed=42)
        val_ds = MixedSpectrogramDataset(val_spc, seed=99)
    elif source == "radioml":
        data_dir = args.data_dir
        train_ds = RadioMLSpectrogramDataset(
            data_dir, samples_per_class=spc, snr_min=args.radioml_snr_min, seed=42, split="train"
        )
        val_ds = RadioMLSpectrogramDataset(
            data_dir,
            samples_per_class=val_spc,
            snr_min=args.radioml_snr_min,
            seed=42,
            split="val",
        )
    elif source == "all":
        # TorchSig+synthetic mixed + RadioML overlays for shared mods
        mixed_tr = MixedSpectrogramDataset(spc, seed=42)
        mixed_va = MixedSpectrogramDataset(val_spc, seed=99)
        rml_tr = RadioMLSpectrogramDataset(
            args.data_dir, samples_per_class=spc, snr_min=args.radioml_snr_min, seed=7, split="train"
        )
        rml_va = RadioMLSpectrogramDataset(
            args.data_dir,
            samples_per_class=val_spc,
            snr_min=args.radioml_snr_min,
            seed=7,
            split="val",
        )
        train_ds = ConcatDataset([mixed_tr, rml_tr])
        val_ds = ConcatDataset([mixed_va, rml_va])
    else:
        raise ValueError(f"Unknown --source {source}")

    return train_ds, val_ds


def train_epoch(model, loader, opt, device) -> float:
    model.train()
    total = 0.0
    n = 0
    for x, y in loader:
        x = x.to(device)
        y = y.to(device)
        opt.zero_grad()
        logits, _emb = model(x)
        loss = F.cross_entropy(logits, y)
        loss.backward()
        opt.step()
        total += float(loss.item()) * x.size(0)
        n += x.size(0)
    return total / max(n, 1)


@torch.no_grad()
def eval_acc(model, loader, device) -> float:
    model.eval()
    correct = 0
    total = 0
    for x, y in loader:
        x = x.to(device)
        y = y.to(device)
        logits, _ = model(x)
        pred = logits.argmax(dim=1)
        correct += int((pred == y).sum().item())
        total += y.numel()
    return correct / max(total, 1)


@torch.no_grad()
def build_prototypes(model, device, samples_per_class: int = 8) -> tuple[list[str], np.ndarray]:
    model.eval()
    labels: list[str] = []
    embs: list[np.ndarray] = []
    for name in SIGNAL_CLASSES:
        specs = []
        for j in range(samples_per_class):
            iq = generate_iq(name, n=8192, snr_db=12.0 + j, seed=9000 + class_to_idx[name] * 50 + j)
            specs.append(iq_to_spectrogram(iq))
        batch = torch.from_numpy(np.stack(specs)[:, None, ...]).float().to(device)
        _, emb = model(batch)
        proto = emb.mean(dim=0).cpu().numpy()
        labels.append(name)
        embs.append(proto)
    return labels, np.stack(embs).astype(np.float32)


def faiss_reset(dim: int):
    import faiss

    return faiss.IndexFlatIP(dim)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=["synthetic", "torchsig", "mixed", "radioml", "all"],
        default="mixed",
        help="Training data source (default: mixed = TorchSig + synthetic fill)",
    )
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--samples-per-class", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--radioml-snr-min", type=int, default=0)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=REPO / "data" / "radioml",
        help="Directory for RadioML downloads",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO / "models",
    )
    parser.add_argument("--device", type=str, default="cpu")
    args = parser.parse_args()

    device = torch.device(args.device)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"Building dataset source={args.source} "
        f"({args.samples_per_class}/class, {NUM_CLASSES} SigSense classes)..."
    )
    train_ds, val_ds = build_datasets(args)
    print(f"  train={len(train_ds)}  val={len(val_ds)}")
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, num_workers=0)

    model = SignalEmbeddingNet(num_classes=NUM_CLASSES, embedding_dim=EMBEDDING_DIM).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)

    best_acc = 0.0
    for epoch in range(1, args.epochs + 1):
        loss = train_epoch(model, train_loader, opt, device)
        acc = eval_acc(model, val_loader, device)
        print(f"epoch {epoch}/{args.epochs}  loss={loss:.4f}  val_acc={acc:.3f}")
        if acc >= best_acc:
            best_acc = acc
            ckpt = {
                "model_state": model.state_dict(),
                "num_classes": NUM_CLASSES,
                "embedding_dim": EMBEDDING_DIM,
                "classes": SIGNAL_CLASSES,
                "val_acc": acc,
                "source": args.source,
            }
            torch.save(ckpt, args.out_dir / "signal_embedding.pt")

    print(f"Best val_acc={best_acc:.3f}")
    model = SignalEmbeddingNet()
    ckpt = torch.load(args.out_dir / "signal_embedding.pt", map_location=device, weights_only=False)
    model.load_state_dict(ckpt["model_state"])
    model.to(device)
    model.eval()

    labels, embs = build_prototypes(model, device)
    lib = SignalLibrary(
        args.out_dir / "library.sqlite",
        args.out_dir / "library.faiss",
        embedding_dim=EMBEDDING_DIM,
    )
    if lib._index.ntotal > 0:
        lib._index = faiss_reset(EMBEDDING_DIM)
        lib._id_map = []
        lib._conn.execute("DELETE FROM prototypes")
        lib._conn.commit()
    lib.seed_from_embeddings(labels, embs, source="builtin")

    class_map = {
        "classes": SIGNAL_CLASSES,
        "val_acc": best_acc,
        "source": args.source,
        "samples_per_class": args.samples_per_class,
        "epochs": args.epochs,
    }
    (args.out_dir / "class_map.json").write_text(json.dumps(class_map, indent=2))
    print(f"Wrote artifacts to {args.out_dir}")


if __name__ == "__main__":
    main()
