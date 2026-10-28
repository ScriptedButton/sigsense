#!/usr/bin/env python3
"""Backbone view selection, feature extraction and fusion evaluation for the model-fusion study.

Stages (cached under --work):
  select    choose each backbone's input view on a train subset -> validation subset (never the test split)
  extract   frozen features for every split with the chosen views
  evaluate  heads / fusion variants trained on train, tuned on val, reported on test

Example:
  python packages/ml/scripts/fusion_experiment.py --work DIR --models DIR select extract evaluate
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

from sigsense_ml.classes import EMBEDDING_DIM, SIGNAL_CLASSES  # noqa: E402
from sigsense_ml.fusion import views as V  # noqa: E402
from sigsense_ml.fusion.ast_model import load_ast  # noqa: E402
from sigsense_ml.fusion.cldnn import load_cldnn  # noqa: E402
from sigsense_ml.fusion.wavesfm import load_wavesfm  # noqa: E402
from sigsense_ml.model import load_model  # noqa: E402

REPO = HERE.parents[2]
DEV = "cuda" if torch.cuda.is_available() else "cpu"
SPLITS = ("train", "val", "test")


# ----------------------------------------------------------------------------- data
def load_split(work: Path, split: str):
    # Read each array once: indexing an npz member inside a loop re-reads the whole array every time.
    with np.load(work / f"corpus_{split}.npz", allow_pickle=False) as z:
        iq, lens, y, source, snr = z["iq"], z["iq_len"], z["y"], z["source"], z["snr"]
    rows = [iq[i, : lens[i]] for i in range(len(y))]
    return rows, y.astype(np.int64), source, snr


def stratified(y: np.ndarray, per_class: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    idx = []
    for c in np.unique(y):
        pool = np.flatnonzero(y == c)
        idx.extend(rng.choice(pool, size=min(per_class, len(pool)), replace=False))
    return np.array(sorted(idx))


# ----------------------------------------------------------------------------- backbones
class Backbones:
    def __init__(self, models: Path):
        self.models = models
        self._cache = {}

    def get(self, name: str):
        if name not in self._cache:
            m = self.models
            if name == "ast":
                self._cache[name] = load_ast(str(m / "ast-sigid" / "AST_finetuned_SIGIDwiki"), DEV, torch.float16)
            elif name == "wavesfm":
                self._cache[name] = load_wavesfm(str(m / "wavesfm" / "wavesfm-v1p0.pth"), DEV, torch.float32)
            elif name == "cldnn":
                self._cache[name] = load_cldnn(str(m / "radiomod" / "model_for_hub.pt"), DEV)
            elif name == "cnn":
                self._cache[name] = load_model(REPO / "models" / "signal_embedding.pt", device=DEV)
        return self._cache[name]


@torch.no_grad()
def run_batches(fn, n: int, batch: int):
    out = []
    for s in range(0, n, batch):
        out.append(fn(s, min(n, s + batch)).float().cpu())
    return torch.cat(out).numpy()


def compute_view(bb: Backbones, view: str, rows: list[np.ndarray], specs: torch.Tensor | None = None) -> np.ndarray:
    """One feature matrix for a named view. Views: <backbone>:<variant>."""
    n = len(rows)
    name, _, variant = view.partition(":")
    if specs is None and name in ("wvis", "ast_spec", "cnn"):
        specs = V.spectrograms(rows)
    if name == "wvis":
        m = bb.get("wavesfm")
        m.use_film = "nofilm" not in variant
        transpose = "T" in variant.split("+")
        return run_batches(lambda a, b: m.forward_vision(V.wavesfm_vision(specs[a:b], transpose).to(DEV)), n, 128)
    if name == "wiq":
        m = bb.get("wavesfm")
        m.use_film = "nofilm" not in variant
        return run_batches(lambda a, b: m.forward_iq(V.wavesfm_iq(rows[a:b]).to(DEV)), n, 128)
    if name == "cldnn":
        m = bb.get("cldnn")
        rms = None if variant == "raw" else float(variant)
        return run_batches(lambda a, b: torch.cat(m(V.cldnn_iq(rows[a:b], rms).to(DEV)), 1), n, 256)
    if name == "ast_spec":
        m = bb.get("ast")
        swap = variant == "rows_as_time"
        def f(a, b):
            with torch.autocast("cuda", dtype=torch.float16, enabled=DEV == "cuda"):
                feat, logits = m(V.ast_from_spectrogram(specs[a:b], swap).to(DEV, torch.float16))
            return torch.cat([feat, logits], 1)
        return run_batches(f, n, 24)
    if name == "ast_audio":
        m = bb.get("ast")
        def f(a, b):
            with torch.autocast("cuda", dtype=torch.float16, enabled=DEV == "cuda"):
                feat, logits = m(V.ast_from_audio(rows[a:b], variant).to(DEV, torch.float16))
            return torch.cat([feat, logits], 1)
        return run_batches(f, n, 24)
    if name == "cnn":
        m = bb.get("cnn")
        def f(a, b):
            logits, emb = m(specs[a:b].to(DEV))
            return torch.cat([emb, logits], 1)
        return run_batches(f, n, 128)
    raise KeyError(view)


# ----------------------------------------------------------------------------- probe / heads
def present_mask(y_train: np.ndarray) -> torch.Tensor:
    mask = torch.zeros(len(SIGNAL_CLASSES), dtype=torch.bool)
    mask[np.unique(y_train)] = True
    return mask


def fit_head(x_tr, y_tr, x_va=None, y_va=None, hidden=0, wd=1e-3, steps=400, lr=3e-3, seed=0):
    """Linear (hidden=0) or 1-hidden-layer softmax head on standardised features."""
    g = torch.Generator().manual_seed(seed)
    mu, sd = x_tr.mean(0, keepdim=True), x_tr.std(0, keepdim=True) + 1e-6
    k = len(SIGNAL_CLASSES)
    d = x_tr.shape[1]
    if hidden:
        net = torch.nn.Sequential(torch.nn.Linear(d, hidden), torch.nn.GELU(), torch.nn.Dropout(0.2), torch.nn.Linear(hidden, k))
    else:
        net = torch.nn.Linear(d, k)
    for p in net.parameters():
        if p.dim() > 1:
            torch.nn.init.normal_(p, 0, 0.02, generator=g)  # on the CPU, with the seeded generator
    net = net.to(DEV)
    mask = present_mask(y_tr.cpu().numpy()).to(DEV)
    xs, ys = ((x_tr - mu) / sd).to(DEV), y_tr.to(DEV)
    opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=wd)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, steps)
    for _ in range(steps):
        net.train()
        logits = net(xs).masked_fill(~mask, -1e4)
        loss = F.cross_entropy(logits, ys, label_smoothing=0.05)
        opt.zero_grad(); loss.backward(); opt.step(); sched.step()
    net.eval()

    def predict(x):
        with torch.no_grad():
            return net(((x - mu) / sd).to(DEV)).masked_fill(~mask, -1e4).cpu()

    return predict


def fit_temperature(logits: torch.Tensor, y: torch.Tensor) -> float:
    t = torch.ones(1, requires_grad=True)
    opt = torch.optim.LBFGS([t], lr=0.1, max_iter=60)
    def closure():
        opt.zero_grad(); loss = F.cross_entropy(logits / t.clamp(0.05, 20), y); loss.backward(); return loss
    opt.step(closure)
    return float(t.clamp(0.05, 20))


def metrics(logits: torch.Tensor, y: torch.Tensor, temperature: float = 1.0) -> dict:
    p = F.softmax(logits / temperature, 1)
    conf, pred = p.max(1)
    correct = (pred == y).float()
    out = {"acc": float(correct.mean())}
    f1s = []
    for c in torch.unique(y):
        tp = float(((pred == c) & (y == c)).sum()); fp = float(((pred == c) & (y != c)).sum()); fn = float(((pred != c) & (y == c)).sum())
        f1s.append(2 * tp / max(1.0, 2 * tp + fp + fn))
    out["macro_f1"] = float(np.mean(f1s))
    bins = torch.linspace(0, 1, 16)
    ece = 0.0
    for a, b in zip(bins[:-1], bins[1:]):
        sel = (conf > a) & (conf <= b)
        if sel.any():
            ece += float(sel.float().mean()) * abs(float(correct[sel].mean()) - float(conf[sel].mean()))
    out["ece"] = ece
    # AUROC of confidence for "this prediction is correct" (selective-classification quality)
    order = conf.argsort()
    ranks = torch.empty_like(order); ranks[order] = torch.arange(len(order))
    pos = correct.bool()
    if pos.any() and (~pos).any():
        out["conf_auroc"] = float((ranks[pos].float().sum() - pos.sum() * (pos.sum() - 1) / 2) / (pos.sum() * (~pos).sum()))
    return out


def by_group(logits, y, groups, temperature=1.0):
    return {str(g): metrics(logits[groups == g], y[groups == g], temperature)["acc"] for g in sorted(set(groups.tolist())) if (groups == g).sum() >= 10}


# ----------------------------------------------------------------------------- stages
CANDIDATES = {
    "wvis": ["wvis:film", "wvis:film+T", "wvis:nofilm", "wvis:nofilm+T"],
    "wiq": ["wiq:film", "wiq:nofilm"],
    "cldnn": ["cldnn:raw", "cldnn:0.9"],
    "ast_spec": ["ast_spec:time_cols", "ast_spec:rows_as_time"],
    "ast_audio": ["ast_audio:fm", "ast_audio:am"],
}


def stage_select(args, bb):
    tr_rows, ytr, _, _ = load_split(args.work, "train")
    va_rows, yva, _, _ = load_split(args.work, "val")
    itr, iva = stratified(ytr, args.sel_train, 1), stratified(yva, args.sel_val, 2)
    rtr, rva = [tr_rows[i] for i in itr], [va_rows[i] for i in iva]
    str_, sva = V.spectrograms(rtr), V.spectrograms(rva)
    ytr_t, yva_t = torch.from_numpy(ytr[itr]), torch.from_numpy(yva[iva])
    results, chosen = {}, {}
    for family, views in CANDIDATES.items():
        if family.startswith("ast") and not (args.models / "ast-sigid").exists():
            continue
        best = None
        for view in views:
            t0 = time.time()
            ftr = torch.from_numpy(compute_view(bb, view, rtr, str_))
            fva = torch.from_numpy(compute_view(bb, view, rva, sva))
            pred = fit_head(ftr, ytr_t, hidden=0)
            acc = float((pred(fva).argmax(1) == yva_t).float().mean())
            results[view] = {"val_acc": round(acc, 4), "secs": round(time.time() - t0, 1)}
            print(f"  {view:28s} val probe acc {acc:.3f}  ({time.time() - t0:.0f}s)", flush=True)
            if best is None or acc > best[0]:
                best = (acc, view)
        chosen[family] = best[1]
    (args.work / "selection.json").write_text(json.dumps({"candidates": results, "chosen": chosen,
        "subset": {"train_per_class": args.sel_train, "val_per_class": args.sel_val, "n_train": len(itr), "n_val": len(iva)}}, indent=2))
    print("chosen views:", chosen)


def stage_extract(args, bb):
    chosen = json.loads((args.work / "selection.json").read_text())["chosen"]
    chosen["cnn"] = "cnn:deployed"
    for split in SPLITS:
        rows, y, source, snr = load_split(args.work, split)
        specs = V.spectrograms(rows)
        feats = {}
        for family, view in chosen.items():
            t0 = time.time()
            feats[family] = compute_view(bb, view, rows, specs)
            print(f"  {split:5s} {view:24s} {feats[family].shape} {time.time() - t0:.0f}s", flush=True)
        np.savez(args.work / f"feat_{split}.npz", y=y, source=source, snr=snr, **feats)


def stage_evaluate(args, bb):
    chosen = json.loads((args.work / "selection.json").read_text())["chosen"]
    d = {s: np.load(args.work / f"feat_{s}.npz", allow_pickle=False) for s in SPLITS}
    y = {s: torch.from_numpy(d[s]["y"]) for s in SPLITS}
    src = {s: d[s]["source"] for s in SPLITS}
    n_cls = len(SIGNAL_CLASSES)
    deployed_classes = bb.get("cnn").classes
    # Deployed CNN: its own logits mapped into the global label space.
    def cnn_logits(split):
        raw = torch.from_numpy(d[split]["cnn"][:, EMBEDDING_DIM:EMBEDDING_DIM + len(deployed_classes)])
        out = torch.full((raw.shape[0], n_cls), -1e4)
        for i, label in enumerate(deployed_classes):
            out[:, SIGNAL_CLASSES.index(label)] = raw[:, i]
        return out
    results = {}
    preds = {}                                  # per-example test predictions, for paired bootstrap
    t_cnn = float(getattr(bb.get("cnn"), "temperature", 1.0) or 1.0)
    cnn_te = cnn_logits("test")
    preds["deployed_cnn"] = cnn_te.argmax(1).numpy()
    results["deployed_cnn"] = {**metrics(cnn_te, y["test"], t_cnn), "by_source": by_group(cnn_te, y["test"], src["test"], t_cnn)}

    def X(split, parts):
        return torch.from_numpy(np.concatenate([d[split][p] for p in parts], 1).astype(np.float32))

    # Native zero-shot behaviour of AST and CLDNN heads on the classes they share with ours.
    results["native_zero_shot"] = native_zero_shot(args, d, y)

    def ev(name, parts, hidden_choices=(0, 512)):
        best = None
        for hidden in hidden_choices:
            head = fit_head(X("train", parts), y["train"], hidden=hidden)
            val = head(X("val", parts))
            acc = float((val.argmax(1) == y["val"]).float().mean())
            if best is None or acc > best[0]:
                best = (acc, hidden, head, val)
        acc, hidden, head, val = best
        t = fit_temperature(val[:, present_mask(y["train"].numpy())], torch.searchsorted(torch.unique(y["train"]), y["val"]))
        te = head(X("test", parts))
        entry = {**metrics(te, y["test"], t), "val_acc": round(acc, 4), "hidden": hidden, "temperature": round(t, 3),
                 "by_source": by_group(te, y["test"], src["test"], t)}
        results[name] = entry
        preds[name] = te.argmax(1).numpy()
        print(f"  {name:34s} test acc {entry['acc']:.3f}  macroF1 {entry['macro_f1']:.3f}  ECE {entry['ece']:.3f}  (val {acc:.3f}, hidden={hidden})", flush=True)
        return head, t

    heads = {}
    singles = [(k, [k]) for k in chosen if k != "cnn"] + [("cnn_embedding_new_head", ["cnn"])]
    for name, parts in singles:
        heads[name] = ev(f"head[{name}]", parts)
    non_cnn = [k for k in chosen if k != "cnn"]
    heads["all_but_cnn"] = ev("fusion[all backbones, no CNN]", non_cnn)
    heads["all"] = ev("fusion[all backbones + CNN]", non_cnn + ["cnn"])
    if "ast_spec" in chosen or "ast_audio" in chosen:
        no_ast = [k for k in non_cnn if not k.startswith("ast")]
        heads["no_ast"] = ev("fusion[WavesFM + CLDNN + CNN]", no_ast + ["cnn"])
    # Spectrogram-only variants: what the spectrum-bands path can use, since it has no IQ.
    spec_parts = [k for k in ("wvis", "ast_spec") if k in chosen]
    heads["spec_only_no_cnn"] = ev("fusion[spectrogram-only: AST + WavesFM-vision]", spec_parts)
    heads["spec_only"] = ev("fusion[spectrogram-only: AST + WavesFM-vision + CNN]", spec_parts + ["cnn"])
    # Late fusion: val-tuned weights over per-backbone calibrated log-probs plus the deployed CNN.
    streams = {}
    for name in [n for n, _ in singles if n != "cnn_embedding_new_head"]:
        head, t = heads[name]
        streams[name] = tuple(F.log_softmax(head(X(s, [name])) / t, 1) for s in ("val", "test"))
    streams["deployed_cnn"] = tuple(F.log_softmax(cnn_logits(s) / t_cnn, 1) for s in ("val", "test"))
    names = list(streams)
    best = (-1, None)
    rng = np.random.default_rng(0)
    for _ in range(400):
        w = rng.dirichlet(np.ones(len(names)))
        val = sum(wi * streams[n][0] for wi, n in zip(w, names))
        acc = float((val.argmax(1) == y["val"]).float().mean())
        if acc > best[0]:
            best = (acc, w)
    te = sum(wi * streams[n][1] for wi, n in zip(best[1], names))
    results["late_fusion[val-weighted log-prob]"] = {**metrics(te, y["test"], 1.0), "val_acc": round(best[0], 4),
        "weights": {n: round(float(w), 3) for n, w in zip(names, best[1])}, "by_source": by_group(te, y["test"], src["test"], 1.0)}
    preds["late_fusion"] = te.argmax(1).numpy()
    print(f"  late fusion test acc {results['late_fusion[val-weighted log-prob]']['acc']:.3f}  weights {results['late_fusion[val-weighted log-prob]']['weights']}", flush=True)

    # Pain case: true WFM windows, what do they get called?
    wfm = SIGNAL_CLASSES.index("WFM")
    pain = {}
    sel = y["test"] == wfm
    candidates = {"deployed_cnn": cnn_te}
    for key in ("fusion[all backbones + CNN]", "fusion[all backbones, no CNN]"):
        h, t = heads["all" if key.endswith("+ CNN]") else "all_but_cnn"]
        parts = non_cnn + ["cnn"] if key.endswith("+ CNN]") else non_cnn
        candidates[key] = h(X("test", parts))
    for name, lg in candidates.items():
        pred = lg[sel].argmax(1)
        counts = {SIGNAL_CLASSES[int(c)]: int((pred == c).sum()) for c in pred.unique()}
        pain[name] = {"n_wfm": int(sel.sum()), "recall": float((pred == wfm).float().mean()), "predicted_as": dict(sorted(counts.items(), key=lambda kv: -kv[1])[:6])}
    results["wfm_pain_case"] = pain
    (args.work / "results.json").write_text(json.dumps(results, indent=2))
    np.savez(args.work / "preds_test.npz", y=y["test"].numpy(), source=src["test"], **{k.replace("[", "_").replace("]", "").replace(" ", "_").replace("+", "plus").replace(",", ""): v for k, v in preds.items()})
    print("wrote", args.work / "results.json")


def native_zero_shot(args, d, y):
    """AST's own 21 labels and CLDNN's own 11 labels, mapped onto the SigSense classes they correspond to."""
    out = {}
    ast_map = {"4G LTE Network": "LTE", '5G "New Radio" cellular network - Downlink': "5G-NR", "Digital Mobile Radio (DMR)": "DMR",
               "Bluetooth": "Bluetooth-LE", "Amplitude Modulation (AM)": "AM", "Morse Code (CW)": "CW"}
    cl_map = {"AM-DSB": "AM", "WBFM": "WFM", "QPSK": "QPSK", "QAM16": "QAM16", "QAM64": "QAM64", "8PSK": "8PSK", "BPSK": "BPSK",
              "CPFSK": "FSK", "GFSK": "GFSK", "PAM4": "ASK4"}
    from sigsense_ml.fusion.cldnn import CLASSES as CL
    if "cldnn" in d["test"].files:
        f = d["test"]["cldnn"]; logits = torch.from_numpy(f[:, 256:267])
        targets = {c: i for i, c in enumerate(CL)}
        mapped = [(targets[k], SIGNAL_CLASSES.index(v)) for k, v in cl_map.items()]
        yy = y["test"]; keep = torch.isin(yy, torch.tensor([m[1] for m in mapped]))
        restricted = logits[:, [m[0] for m in mapped]]
        pred = torch.tensor([m[1] for m in mapped])[restricted.argmax(1)]
        out["cldnn_native_11way_on_shared_classes"] = {"n": int(keep.sum()), "acc": float((pred[keep] == yy[keep]).float().mean()),
                                                        "shared_classes": sorted({v for v in cl_map.values()})}
    for key in ("ast_spec", "ast_audio"):
        if key in d["test"].files:
            f = d["test"][key]; logits = torch.from_numpy(f[:, 768:789])
            names = ["Aircraft Communications Addressing and Reporting System (ACARS)", "Non-Directional Beacon (NDB)", "Tetrapol", "VHF Omnidirectional Range (VOR)",
                     "Digital Audio Broadcasting Plus (DAB+)", "Automatic Picture Transmission (APT)", "Instrument Landing System", "Single Sideband Voice", "4G LTE Network",
                     "Digital Mobile Radio (DMR)", "Bluetooth", "M20 Radiosonde", "Differential Global Positioning System (DGPS)", '5G "New Radio" cellular network - Downlink',
                     "High Frequency Data Link (HFDL)", "STANAG 5065", "Morse Code (CW)", "Automatic Link Set-up (ALIS)", "Automatic Identification System (AIS)",
                     "Amplitude Modulation (AM)", "VHF Data Link - Mode 2 (VDL-M2)"]
            mapped = [(names.index(k), SIGNAL_CLASSES.index(v)) for k, v in ast_map.items()]
            yy = y["test"]; keep = torch.isin(yy, torch.tensor([m[1] for m in mapped]))
            restricted = logits[:, [m[0] for m in mapped]]
            pred = torch.tensor([m[1] for m in mapped])[restricted.argmax(1)]
            out[f"{key}_native_on_shared_classes"] = {"n": int(keep.sum()), "acc": float((pred[keep] == yy[keep]).float().mean()),
                                                       "shared_classes": sorted({v for v in ast_map.values()})}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stages", nargs="+", choices=["select", "extract", "evaluate"])
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--models", type=Path, required=True)
    ap.add_argument("--sel-train", type=int, default=12)
    ap.add_argument("--sel-val", type=int, default=8)
    args = ap.parse_args()
    bb = Backbones(args.models)
    for stage in args.stages:
        print(f"== {stage}", flush=True)
        {"select": stage_select, "extract": stage_extract, "evaluate": stage_evaluate}[stage](args, bb)


if __name__ == "__main__":
    main()
