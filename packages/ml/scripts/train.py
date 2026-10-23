#!/usr/bin/env python3
"""Train and evaluate SigSense with isolated train/validation/test splits."""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import importlib.metadata
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import ConcatDataset, DataLoader, Dataset

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
sys.path.insert(0, str(ROOT))

from sigsense_ml.classes import EMBEDDING_DIM, SIGNAL_CLASSES, PROXY_CLASSES
from sigsense_ml.capture_dataset import CaptureDataset
from sigsense_ml.evaluation import collect_predictions, classification_report, fit_temperature
from sigsense_ml.external_datasets import (MixedSpectrogramDataset, RadioMLSpectrogramDataset,
    SyntheticSpectrogramDataset, TorchSigSpectrogramDataset)
from sigsense_ml.library import SignalLibrary
from sigsense_ml.model import SignalEmbeddingNet, load_model
from sigsense_ml.radiopile import COMPACT_SOURCES, RadioPileSpectrogramDataset


class SelectedClasses(Dataset):
    """Filter global dataset indices and remap to checkpoint-local labels."""
    def __init__(self, dataset, classes):
        self.dataset = dataset
        self.mapping = {SIGNAL_CLASSES.index(label): i for i, label in enumerate(classes)}
        labels = dataset_labels(dataset)
        self.indices = [i for i, label in enumerate(labels) if label in self.mapping]
        self.labels = [self.mapping[labels[i]] for i in self.indices]
        sources = dataset_sources(dataset)
        self.sources = [sources[i] for i in self.indices]

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, i):
        x, label = self.dataset[self.indices[i]]
        return x, self.mapping[int(label)]


def make_split(args, split):
    spc = args.samples_per_class if split == "train" else args.eval_samples_per_class
    # Wide seed separation also prevents overlap in synthetic per-class seed ranges.
    seed = args.seed + {"train": 0, "val": 1_000_000, "test": 2_000_000}[split]
    augment = split == "train"
    if args.source == "captures":
        return CaptureDataset(args.manifest, split)
    generated = None
    if args.source in {"synthetic", "torchsig", "mixed", "all", "compact"}:
        factory = {"synthetic": SyntheticSpectrogramDataset, "torchsig": TorchSigSpectrogramDataset,
                   "mixed": MixedSpectrogramDataset, "all": MixedSpectrogramDataset,
                   "compact": MixedSpectrogramDataset}[args.source]
        generated = factory(spc, seed=seed, augment=augment)
    if args.source == "compact":
        # Never contaminate verified LTE examples with the local OFDM proxy.
        generated.items = [(x, y) for x, y in generated.items if SIGNAL_CLASSES[y] not in PROXY_CLASSES]
        external_spc = args.external_samples_per_class if split == "train" else args.eval_samples_per_class
        real = [RadioPileSpectrogramDataset(args.external_data_dir / f"{name}.h5", split,
                    samples_per_class=external_spc, seed=args.seed, snr_min=args.radioml_snr_min)
                for name in COMPACT_SOURCES]
        return ConcatDataset([generated, *real])
    if args.source in {"radioml", "all"}:
        radio = RadioMLSpectrogramDataset(args.data_dir, samples_per_class=spc,
            snr_min=args.radioml_snr_min, seed=args.seed, split=split, augment=augment)
        return ConcatDataset([generated, radio]) if generated is not None else radio
    return generated


def dataset_labels(dataset):
    if isinstance(dataset, ConcatDataset):
        return [label for child in dataset.datasets for label in dataset_labels(child)]
    if hasattr(dataset, "items"):
        return [int(label) for _, label in dataset.items]
    if isinstance(dataset, CaptureDataset):
        return [SIGNAL_CLASSES.index(row["label"]) for row in dataset.rows]
    return [int(dataset[i][1]) for i in range(len(dataset))]


def dataset_sources(dataset):
    if isinstance(dataset, ConcatDataset):
        return [source for child in dataset.datasets for source in dataset_sources(child)]
    return [getattr(dataset, "source", dataset.__class__.__name__)] * len(dataset)


def split_evidence(dataset):
    if isinstance(dataset, ConcatDataset):
        return [entry for child in dataset.datasets for entry in split_evidence(child)]
    if isinstance(dataset, RadioPileSpectrogramDataset):
        return [{"source": dataset.source, "scope": dataset.split_scope,
                 "rows": dataset.row_indices, "groups": sorted(set(dataset.group_ids))}]
    return []


def active_classes(dataset, include_proxies=False):
    present = set(dataset_labels(dataset))
    return [label for i, label in enumerate(SIGNAL_CLASSES)
            if i in present and (include_proxies or label not in PROXY_CLASSES)]


@torch.no_grad()
def build_prototypes(model, loader, device, classes):
    """Use the training distribution, never synthetic substitutes for new labels."""
    model.eval()
    sums = torch.zeros(len(classes), EMBEDDING_DIM, device=device)
    counts = torch.zeros(len(classes), device=device)
    for x, y in loader:
        y = y.to(device)
        emb = model(x.to(device))[1]
        sums.index_add_(0, y, emb)
        counts.index_add_(0, y, torch.ones(len(y), device=device))
    if (counts == 0).any():
        raise ValueError("Cannot export a class with no prototype training examples")
    return (sums / counts[:, None]).cpu().numpy().astype(np.float32)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["synthetic", "torchsig", "mixed", "radioml", "all", "captures", "compact"], default="mixed")
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--samples-per-class", type=int, default=128)
    parser.add_argument("--eval-samples-per-class", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--patience", type=int, default=15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--radioml-snr-min", type=int, default=0)
    parser.add_argument("--data-dir", type=Path, default=REPO / "data" / "radioml")
    parser.add_argument("--external-data-dir", type=Path, default=REPO / "data" / "raw" / "radiopile")
    parser.add_argument("--external-samples-per-class", type=int, default=256)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--amp", action="store_true", help="Enable CUDA mixed precision for higher throughput")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--include-proxy-labels", action="store_true", help="Include unverified AIS/APRS/LTE/ACARS proxy labels (not recommended)")
    parser.add_argument("--out-dir", type=Path, default=REPO / "checkpoints" / "candidate")
    parser.add_argument("--device", default="auto", help="auto, cuda, or cpu; GPU needs device access outside the Codex sandbox")
    args = parser.parse_args()
    if min(args.epochs, args.batch_size, args.samples_per_class, args.eval_samples_per_class, args.external_samples_per_class, args.patience) < 1 or args.lr <= 0 or args.workers < 0:
        parser.error("Epochs, batch size, sample counts, patience, and learning rate must be positive")
    if args.source == "captures" and args.manifest is None:
        parser.error("--source captures requires --manifest")
    if (args.out_dir / "signal_embedding.pt").exists() or (args.out_dir / "library.sqlite").exists():
        parser.error("Output already contains model artifacts; choose a new --out-dir")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device(("cuda" if torch.cuda.is_available() else "cpu") if args.device == "auto" else args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable; check GPU access outside the sandbox")
    print(f"Device: {device}; source: {args.source}; seed: {args.seed}", flush=True)
    train_raw = make_split(args, "train")
    classes = active_classes(train_raw, args.include_proxy_labels or args.source in {"captures", "compact"})
    if len(classes) < 2:
        raise ValueError("Training needs at least two supported classes")
    train = SelectedClasses(train_raw, classes)
    val_raw = make_split(args, "val")
    val = SelectedClasses(val_raw, classes)
    evidence = {"train": split_evidence(train_raw), "val": split_evidence(val_raw)}
    expected = set(range(len(classes)))
    if set(train.labels) != expected or set(val.labels) != expected:
        raise ValueError("Every trained class must be represented in train and validation")
    print(f"Classes={len(classes)} train={len(train)} val={len(val)}; absent proxy labels={sorted(PROXY_CLASSES - set(classes))}", flush=True)
    generator = torch.Generator().manual_seed(args.seed)
    loader_options = dict(batch_size=args.batch_size, num_workers=args.workers,
                          pin_memory=device.type == "cuda", persistent_workers=args.workers > 0)
    train_loader = DataLoader(train, shuffle=True, generator=generator, **loader_options)
    val_loader = DataLoader(val, **loader_options)
    model = SignalEmbeddingNet(num_classes=len(classes)).to(device)
    model.classes = classes
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-3)
    amp = args.amp and device.type == "cuda"
    scaler = torch.amp.GradScaler("cuda", enabled=amp)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs, eta_min=args.lr * .01)
    best, best_epoch, history = -1., 0, []
    config = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    config["versions"] = {name: importlib.metadata.version(name) for name in ["torch", "numpy", "torchsig", "h5py"] if importlib.util.find_spec(name)}
    (args.out_dir / "run_config.json").write_text(json.dumps(config, indent=2))
    for epoch in range(1, args.epochs + 1):
        started = time.perf_counter()
        model.train()
        total = 0.
        for x, y in train_loader:
            x, y = x.to(device, non_blocking=True), y.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type=device.type, dtype=torch.float16, enabled=amp):
                logits, _ = model(x)
                loss = F.cross_entropy(logits, y)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.)
            scaler.step(optimizer)
            scaler.update()
            total += float(loss.detach()) * len(y)
        val_logits, val_y = collect_predictions(model, val_loader, device, amp=amp)
        report = classification_report(val_logits, val_y, classes)
        score = report["macro_f1"]
        row = {"epoch": epoch, "loss": total / len(train), "val_acc": report["accuracy"], "val_macro_f1": score, "lr": optimizer.param_groups[0]["lr"],
               "seconds": time.perf_counter() - started,
               "peak_gpu_memory_mb": torch.cuda.max_memory_allocated(device) / 1024**2 if device.type == "cuda" else 0.}
        history.append(row)
        print(f"epoch {epoch}/{args.epochs} loss={row['loss']:.4f} val_acc={row['val_acc']:.3f} macro_f1={score:.3f} seconds={row['seconds']:.1f} peak_gpu_mb={row['peak_gpu_memory_mb']:.0f}", flush=True)
        if score > best:
            best, best_epoch = score, epoch
            torch.save({"model_state": model.state_dict(), "num_classes": len(classes),
                "embedding_dim": EMBEDDING_DIM, "classes": classes, "source": args.source,
                "val_acc": report["accuracy"], "val_macro_f1": best, "epoch": epoch, "seed": args.seed}, args.out_dir / "signal_embedding.pt")
        scheduler.step()
        (args.out_dir / "history.json").write_text(json.dumps(history, indent=2))
        if epoch - best_epoch >= args.patience:
            print(f"Early stopping; best epoch={best_epoch}", flush=True)
            break
    model = load_model(args.out_dir / "signal_embedding.pt", device=device)
    val_logits, val_y = collect_predictions(model, val_loader, device, amp=amp)
    temperature = fit_temperature(val_logits, val_y)
    checkpoint = torch.load(args.out_dir / "signal_embedding.pt", map_location="cpu", weights_only=False)
    checkpoint["temperature"] = temperature
    torch.save(checkpoint, args.out_dir / "signal_embedding.pt")
    # Generate/inspect test only after all selection and calibration are finished.
    test_raw = make_split(args, "test")
    test = SelectedClasses(test_raw, classes)
    evidence["test"] = split_evidence(test_raw)
    for source in {entry["source"] for split in evidence.values() for entry in split}:
        groups = {s: set(g for entry in entries if entry["source"] == source for g in entry["groups"]) for s, entries in evidence.items()}
        if groups["train"] & groups["val"] or groups["train"] & groups["test"] or groups["val"] & groups["test"]:
            raise ValueError(f"Recording-group leakage in {source}")
    (args.out_dir / "split_evidence.json").write_text(json.dumps(evidence, indent=2))
    if set(test.labels) != expected:
        raise ValueError("Every trained class must be represented in the test split")
    test_logits, test_y = collect_predictions(model, DataLoader(test, **loader_options), device, amp=amp)
    reports = {"validation": classification_report(val_logits, val_y, classes, temperature),
               "test": classification_report(test_logits, test_y, classes, temperature),
               "classes": classes, "best_epoch": best_epoch, "source": args.source,
               "evaluation_scope": "Generated IID splits only" if args.source not in {"captures", "compact"} else "Declared recording/observation groups; audit original capture/session independence separately"}
    reports["test_by_source"] = {}
    for source in sorted(set(test.sources)):
        indices = [i for i, s in enumerate(test.sources) if s == source]
        reports["test_by_source"][source] = classification_report(test_logits[indices], test_y[indices], classes, temperature)
    (args.out_dir / "evaluation.json").write_text(json.dumps(reports, indent=2))
    embeddings = build_prototypes(model, DataLoader(train, batch_size=args.batch_size), device, classes)
    library = SignalLibrary(args.out_dir / "library.sqlite", args.out_dir / "library.faiss", embedding_dim=EMBEDDING_DIM)
    library.seed_from_embeddings(classes, embeddings)
    library._conn.close()
    (args.out_dir / "class_map.json").write_text(json.dumps({"classes": classes,
        "val_acc": checkpoint["val_acc"], "test_acc": reports["test"]["accuracy"],
        "source": args.source, "epochs": len(history), "best_epoch": best_epoch,
        "samples_per_class": args.samples_per_class, "temperature": temperature}, indent=2))
    print(f"Best epoch={best_epoch}; test_acc={reports['test']['accuracy']:.3f}; test_macro_f1={reports['test']['macro_f1']:.3f}; temperature={temperature:.3f}", flush=True)
    print(f"Candidate artifacts written to {args.out_dir}. Review evaluation.json before promotion.", flush=True)


if __name__ == "__main__":
    main()
