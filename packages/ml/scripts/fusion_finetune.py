#!/usr/bin/env python3
"""Fine-tune a spectrogram backbone (AST or WavesFM-vision) on the compact-49 train split.

Everything is chosen on the validation split: the epoch to keep and (via --lr) the schedule. The test
split is only scored once, at the end, with the kept epoch. Output: per-split logits in the global label
space (for late fusion with the deployed CNN) and a small JSON of metrics. Weights are written to --out
and never into the repository tree.

  python packages/ml/scripts/fusion_finetune.py --backbone ast --work DIR --models DIR --out DIR2
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from sigsense_ml.classes import SIGNAL_CLASSES  # noqa: E402
from sigsense_ml.fusion import views as V  # noqa: E402
from sigsense_ml.fusion.ast_model import load_ast  # noqa: E402
from sigsense_ml.fusion.wavesfm import load_wavesfm  # noqa: E402
from fusion_experiment import load_split, metrics  # noqa: E402

DEV = "cuda"


class Tuner(torch.nn.Module):
    """Backbone + fresh linear head over the full label space (absent classes are masked)."""

    def __init__(self, backbone: str, models: Path):
        super().__init__()
        self.kind = backbone
        if backbone == "ast":
            self.net = load_ast(str(models / "ast-sigid" / "AST_finetuned_SIGIDwiki"), "cpu", torch.float32)
            self.net.grad_ckpt = True
            dim = 768
        else:
            self.net = load_wavesfm(str(models / "wavesfm" / "wavesfm-v1p0.pth"), "cpu", torch.float32)
            dim = 512
        self.head = torch.nn.Sequential(torch.nn.Dropout(0.1), torch.nn.Linear(dim, len(SIGNAL_CLASSES)))

    def forward(self, spec: torch.Tensor) -> torch.Tensor:
        if self.kind == "ast":
            feat, _ = self.net(V.ast_from_spectrogram(spec))
        else:
            feat = self.net.forward_vision(V.wavesfm_vision(spec))
        return self.head(feat)


def augment(spec: torch.Tensor, gen: torch.Generator) -> torch.Tensor:
    """Roll in time, mild noise: cheap invariances that don't change what the signal is."""
    n = spec.shape[0]
    shift = int(torch.randint(0, spec.shape[3], (1,), generator=gen))
    spec = torch.roll(spec, shifts=shift, dims=3)
    noise = torch.rand(n, 1, 1, 1, generator=gen, device=spec.device) * 0.03
    return (spec + torch.randn(spec.shape, generator=gen, device=spec.device) * noise).clamp(0, 1)


@torch.no_grad()
def predict(model, specs: torch.Tensor, mask: torch.Tensor, batch: int) -> torch.Tensor:
    model.eval()
    out = []
    for s in range(0, len(specs), batch):
        with torch.autocast("cuda", dtype=torch.float16):
            lg = model(specs[s:s + batch].to(DEV).float())
        out.append(lg.float().masked_fill(~mask, -1e4).cpu())
    return torch.cat(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backbone", choices=["ast", "wavesfm"], required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--models", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-5, help="backbone learning rate (head uses 50x)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    gen = torch.Generator(device=DEV).manual_seed(args.seed)

    data = {}
    for split in ("train", "val", "test"):
        rows, y, source, _ = load_split(args.work, split)
        t0 = time.time()
        data[split] = (V.spectrograms(rows).half(), torch.from_numpy(y), source)
        print(f"  {split}: {len(y)} spectrograms ({time.time() - t0:.0f}s)", flush=True)
        del rows
    present = torch.zeros(len(SIGNAL_CLASSES), dtype=torch.bool)
    present[data["train"][1].unique()] = True
    mask = present.to(DEV)

    model = Tuner(args.backbone, args.models).to(DEV)
    opt = torch.optim.AdamW([
        {"params": model.net.parameters(), "lr": args.lr},
        {"params": model.head.parameters(), "lr": args.lr * 50}], weight_decay=0.01)
    steps_per_epoch = (len(data["train"][1]) + args.batch - 1) // args.batch
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=[args.lr, args.lr * 50], total_steps=args.epochs * steps_per_epoch, pct_start=0.15)
    scaler = torch.amp.GradScaler()
    xtr, ytr, _ = data["train"]
    best = (-1.0, None, -1)
    log = []
    for epoch in range(args.epochs):
        model.train()
        perm = torch.randperm(len(ytr))
        t0, losses = time.time(), []
        for s in range(0, len(perm), args.batch):
            idx = perm[s:s + args.batch]
            xb = augment(xtr[idx].to(DEV).float(), gen)
            yb = ytr[idx].to(DEV)
            with torch.autocast("cuda", dtype=torch.float16):
                logits = model(xb)
            loss = F.cross_entropy(logits.float().masked_fill(~mask, -1e4), yb, label_smoothing=0.05)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(opt); scaler.update(); sched.step()
            losses.append(float(loss))
        val_logits = predict(model, data["val"][0], mask, 64)
        val_acc = float((val_logits.argmax(1) == data["val"][1]).float().mean())
        log.append({"epoch": epoch + 1, "train_loss": round(float(np.mean(losses)), 4), "val_acc": round(val_acc, 4), "secs": round(time.time() - t0)})
        print(f"  epoch {epoch + 1}/{args.epochs}  loss {np.mean(losses):.3f}  val acc {val_acc:.3f}  ({time.time() - t0:.0f}s)  peak VRAM {torch.cuda.max_memory_allocated() / 1e9:.1f} GB", flush=True)
        if val_acc > best[0]:
            best = (val_acc, {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}, epoch + 1)
    model.load_state_dict(best[1])
    out = {"backbone": args.backbone, "kept_epoch": best[2], "val_acc": best[0], "log": log, "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2)}
    logits = {s: predict(model, data[s][0], mask, 64) for s in ("val", "test")}
    out["test"] = metrics(logits["test"], data["test"][1])
    np.savez(args.out / f"logits_{args.backbone}.npz", val=logits["val"].numpy(), test=logits["test"].numpy(),
             y_val=data["val"][1].numpy(), y_test=data["test"][1].numpy(), source_test=data["test"][2])
    torch.save(best[1], args.out / f"finetuned_{args.backbone}.pt")
    (args.out / f"finetune_{args.backbone}.json").write_text(json.dumps(out, indent=2))
    print(f"kept epoch {best[2]}  val {best[0]:.3f}  TEST acc {out['test']['acc']:.3f}  F1 {out['test']['macro_f1']:.3f}", flush=True)


if __name__ == "__main__":
    main()
