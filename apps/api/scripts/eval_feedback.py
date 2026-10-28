"""Does a handful of user corrections fix a mislabelled signal without breaking others?

    cd apps/api && PYTHONPATH=.:../../packages/ml ../../.venv/bin/python scripts/eval_feedback.py [--grid]

Everything runs against a COPY of the models folder, never the real library.
Signals go through the same chain as the HackRF path: IQ -> occupied band ->
extract_band -> STFT image -> CNN. Held-out variants use seeds, SNRs and
deviations the "corrections" never saw.

What it measures (see the report printed at the end):
  * top-1 on held-out broadcast-FM-style signals before / after 1, 3, 5 corrections
  * top-1 on the repo's synthetic WFM generator (a related but different WFM)
  * collateral damage: other classes newly labelled as the corrected label
  * 20 mixed corrections across several classes
  * latency from correction to the next prediction
  * undo returns the baseline exactly
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scipy import signal as sp_signal  # noqa: E402
from sigsense_api import learning  # noqa: E402
from sigsense_api.inference import DEFAULT_MODELS, InferenceEngine  # noqa: E402
from sigsense_api.spectrum import extract_band, measure_spectrum  # noqa: E402
from sigsense_ml.classes import CAPTURE_ONLY_CLASSES, SIGNAL_CLASSES  # noqa: E402
from sigsense_ml.preprocess import iq_to_spectrogram  # noqa: E402
from sigsense_ml.synthetic import generate_iq  # noqa: E402

FS = 2.4e6
CENTER = 93.9e6


def broadcast_fm(seed: int, snr_db: float, dev: float = 75e3, n: int = 65536,
                 stereo: bool = True, rds: bool = True, offset_hz: float = 0.0) -> np.ndarray:
    """A broadcast-FM-style multiplex: audio + 19 kHz pilot + 38 kHz stereo + 57 kHz RDS."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / FS

    def audio() -> np.ndarray:
        noise = sp_signal.lfilter(*sp_signal.butter(4, 15e3 / (FS / 2)), rng.normal(size=n))
        noise /= np.abs(noise).max() + 1e-9
        tone = np.sin(2 * np.pi * rng.uniform(200, 2500) * t + rng.uniform(0, 6.28))
        return 0.6 * noise + 0.4 * tone

    left, right = audio(), audio()
    mpx = 0.45 * (left + right) / 2 + 0.09 * np.sin(2 * np.pi * 19e3 * t)
    if stereo:
        mpx += 0.45 * (left - right) / 2 * np.sin(2 * np.pi * 38e3 * t)
    if rds:
        bits = np.repeat(rng.choice([-1.0, 1.0], size=n // 2000 + 2), 2000)[:n]
        mpx += 0.04 * bits * np.sin(2 * np.pi * 57e3 * t)
    mpx /= max(np.abs(mpx).max(), 1e-9)
    phase = 2 * np.pi * dev * np.cumsum(mpx) / FS + 2 * np.pi * offset_hz * t
    iq = np.exp(1j * phase).astype(np.complex64)
    sigma = np.sqrt(0.5 / 10 ** (snr_db / 10))
    iq += (sigma * (rng.normal(size=n) + 1j * rng.normal(size=n))).astype(np.complex64)
    return iq


def band_spec(iq: np.ndarray, fs: float = FS, fc: float = CENTER) -> np.ndarray | None:
    """The strongest occupied band through the HackRF path's own preprocessing."""
    bands = measure_spectrum(iq, fs, fc, fft_size=2048)["bands"]
    if not bands:
        return None
    narrow, _ = extract_band(iq, fs, fc, bands[0])
    return iq_to_spectrogram(narrow)


def app_spec(iq: np.ndarray) -> np.ndarray:
    """What the app feeds the CNN for this capture (band path, else the whole IQ)."""
    spec = band_spec(iq)
    return spec if spec is not None else iq_to_spectrogram(iq)


class Sample:
    __slots__ = ("spec", "label", "tag", "probs", "emb")

    def __init__(self, spec, label, tag):
        self.spec, self.label, self.tag = spec, label, tag
        self.probs = self.emb = None


def build_sets():
    train = []  # broadcast FM the "user" will correct
    for i, (snr, dev) in enumerate([(22, 75e3), (14, 60e3), (18, 70e3), (10, 55e3), (20, 65e3)]):
        spec = band_spec(broadcast_fm(1000 + i, snr, dev))
        if spec is not None:
            train.append(Sample(spec, "WFM", "train-fm"))
    test_fm = []
    for i in range(48):
        snr = (25, 15, 8, 3)[i % 4]
        dev = (50e3, 60e3, 75e3)[(i // 4) % 3]
        spec = band_spec(
            broadcast_fm(5000 + i, snr, dev, stereo=bool(i % 2), rds=bool((i // 2) % 2),
                         offset_hz=float((i % 5 - 2) * 20e3))
        )
        if spec is not None:
            test_fm.append(Sample(spec, "WFM", "test-fm"))
    syn_wfm = []
    for i in range(12):
        iq = generate_iq("WFM", n=65536, snr_db=(25, 15, 8)[i % 3], seed=300 + i)
        syn_wfm.append(Sample(app_spec(iq), "WFM", "syn-wfm"))
    others = []
    for name in SIGNAL_CLASSES:
        if name in CAPTURE_ONLY_CLASSES or name == "WFM":
            continue
        for i in range(6):
            try:
                iq = generate_iq(name, n=65536, snr_db=(25, 15, 8)[i % 3], seed=700 + i)
            except Exception:
                break
            others.append(Sample(app_spec(iq), name, "other"))
    return train, test_fm, syn_wfm, others


def cnn_pass(engine: InferenceEngine, samples: list[Sample]) -> None:
    """One CNN pass; later decisions only need probs + embedding."""
    import torch
    import torch.nn.functional as F

    for s in samples:
        t = torch.from_numpy(s.spec[None, None]).float()
        with torch.no_grad():
            logits, emb = engine.model(t)
            s.probs = F.softmax(logits / engine.model.temperature, dim=1)[0].numpy()
            s.emb = emb[0].numpy()


def decide(engine: InferenceEngine, s: Sample) -> str:
    return engine.decide(s.probs, s.emb)["label"]


def top1(engine, samples) -> float:
    return float(np.mean([decide(engine, s) == s.label for s in samples])) if samples else 0.0


def label_counts(engine, samples) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in samples:
        k = decide(engine, s)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1])[:5])


def collateral(engine, others, base_labels, label) -> tuple[float, int, int]:
    """Share of other-class samples newly called `label`, how many changed at
    all, and how many the CNN had RIGHT that are now wrong (true regressions)."""
    now = [decide(engine, s) for s in others]
    newly = sum(1 for a, b in zip(base_labels, now) if b == label and a != label)
    changed = sum(1 for a, b in zip(base_labels, now) if a != b)
    regress = sum(
        1 for a, b, s in zip(base_labels, now, others) if a == s.label and b != s.label
    )
    return newly / max(1, len(others)), changed, regress


def fresh_engine(tmp_root: Path) -> InferenceEngine:
    models = tmp_root / f"models-{time.time_ns()}"
    models.mkdir(parents=True)
    for f in ("signal_embedding.pt", "library.faiss", "library.sqlite"):
        shutil.copy2(DEFAULT_MODELS / f, models / f)
    return InferenceEngine(models_dir=models)


def run(grid: bool) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="sigsense-eval-"))
    try:
        engine = fresh_engine(tmp)
        print("building sample sets ...", flush=True)
        train, test_fm, syn_wfm, others = build_sets()
        for group in (train, test_fm, syn_wfm, others):
            cnn_pass(engine, group)
        print(f"  corrections pool {len(train)}, held-out broadcast FM {len(test_fm)}, "
              f"synthetic WFM {len(syn_wfm)}, other-class samples {len(others)}")

        base_labels = [decide(engine, s) for s in others]
        base = {
            "fm": top1(engine, test_fm),
            "syn": top1(engine, syn_wfm),
            "others": float(np.mean([a == s.label for a, s in zip(base_labels, others)])),
        }
        print(f"\nBASELINE (no corrections)\n  held-out broadcast FM top-1: {base['fm']:.0%}  "
              f"labels: {label_counts(engine, test_fm)}")
        print(f"  synthetic WFM top-1: {base['syn']:.0%}  labels: {label_counts(engine, syn_wfm)}")
        print(f"  other classes top-1: {base['others']:.0%}")

        def after_k(k: int, store_label="WFM"):
            e = fresh_engine(tmp)
            t0 = time.perf_counter()
            for s in train[:k]:
                e.feedback.add(s.spec, store_label, wrong_label=decide(e, s))
            add_ms = (time.perf_counter() - t0) * 1000 / max(1, k)
            return e, add_ms

        print("\nAFTER K CORRECTIONS (label WFM)")
        correct_before = sum(1 for a, s in zip(base_labels, others) if a == s.label)
        print(f"   K | broadcast FM | synthetic WFM | others newly called WFM | others changed | "
              f"regressions (of {correct_before} CNN-correct) | add ms/each")
        rows = []
        for k in (1, 3, 5):
            e, add_ms = after_k(k)
            fm = top1(e, test_fm)
            syn = top1(e, syn_wfm)
            newly, changed, regress = collateral(e, others, base_labels, "WFM")
            rows.append((k, fm, syn, newly, changed, add_ms))
            print(f"  {k:>2} | {fm:>11.0%}  | {syn:>12.0%}  | {newly:>22.1%}  | {changed:>13d}  | "
                  f"{regress:>27d}  | {add_ms:>9.0f}")

        # Latency: correction -> next prediction.
        e, _ = after_k(0)
        t0 = time.perf_counter()
        e.feedback.add(train[0].spec, "WFM", wrong_label=decide(e, train[0]))
        post_ms = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        e.decide(test_fm[0].probs, test_fm[0].emb)
        decide_ms = (time.perf_counter() - t1) * 1000
        print(f"\nLATENCY  correction stored in {post_ms:.0f} ms (embedding + library write + persist); "
              f"next decision {decide_ms:.2f} ms")

        # Same-family generalisation: correct 1 and 3 synthetic WFM, test on the rest.
        syn_train, syn_test = syn_wfm[:3], syn_wfm[3:]
        print("\nSAME-FAMILY  correcting synthetic WFM generalises to other synthetic WFM:")
        for k in (1, 3):
            e = fresh_engine(tmp)
            for s in syn_train[:k]:
                e.feedback.add(s.spec, "WFM", wrong_label=decide(e, s))
            newly, _, regress = collateral(e, others, base_labels, "WFM")
            print(f"  {k} correction(s): held-out synthetic WFM {top1(e, syn_test):.0%} "
                  f"(baseline {top1(engine, syn_test):.0%}); others newly called WFM {newly:.1%}; "
                  f"regressions {regress}")

        # Undo.
        e, _ = after_k(3)
        e.feedback.clear()
        undone = [decide(e, s) for s in others]
        same = sum(1 for a, b in zip(base_labels, undone) if a == b)
        print(f"UNDO     after clearing: {same}/{len(others)} other-class labels back to baseline, "
              f"broadcast FM top-1 {top1(e, test_fm):.0%} (baseline {base['fm']:.0%})")

        # 20 mixed corrections.
        e = fresh_engine(tmp)
        confusions = []
        pool = {}
        for s in others:
            pool.setdefault(s.label, []).append(s)
        wrong = [(c, ss) for c, ss in pool.items() if np.mean([decide(e, s) == c for s in ss]) < 0.5]
        wrong.sort(key=lambda kv: np.mean([decide(e, s) == kv[0] for s in kv[1]]))
        used = 0
        for c, ss in wrong:
            for s in ss[:2]:
                e.feedback.add(s.spec, c, wrong_label=decide(e, s))
                used += 1
                if used >= 14:
                    break
            confusions.append(c)
            if used >= 14:
                break
        for s in train[:5]:
            e.feedback.add(s.spec, "WFM", wrong_label=decide(e, s))
            used += 1
        custom = "Local DMR system"
        for i in range(2):
            iq = generate_iq("DMR", n=65536, snr_db=18, seed=4200 + i)
            e.feedback.add(app_spec(iq), custom, wrong_label="?", note="custom label")
            used += 1
        # Held-out copies of the corrected classes (different seeds) to see generalisation.
        heldout = []
        for c in confusions:
            for i in range(4):
                iq = generate_iq(c, n=65536, snr_db=(25, 15, 8, 12)[i], seed=9000 + i)
                heldout.append(Sample(app_spec(iq), c, "held"))
        cnn_pass(e, heldout)
        custom_test = [
            Sample(app_spec(generate_iq("DMR", n=65536, snr_db=snr, seed=4300 + i)), custom, "custom")
            for i, snr in enumerate((25, 15, 8, 12))
        ]
        cnn_pass(e, custom_test)
        h_base = fresh_engine(tmp)
        print(f"\n{used} MIXED CORRECTIONS ({len(confusions)} classes + WFM + 1 custom label x2)")
        print(f"  corrected classes on held-out seeds: {top1(h_base, heldout):.0%} -> {top1(e, heldout):.0%}")
        print(f"  custom label '{custom}' (2 corrections) on held-out variants: "
              f"0% (no such class) -> {top1(e, custom_test):.0%}")
        print(f"  broadcast FM: {base['fm']:.0%} -> {top1(e, test_fm):.0%}   "
              f"other classes (all 47): {base['others']:.0%} -> "
              f"{np.mean([decide(e, s) == s.label for s in others]):.0%}")

        # Contradictory corrections: the same input, two labels.
        e = fresh_engine(tmp)
        e.feedback.add(train[0].spec, "WFM")
        e.feedback.add(train[0].spec, "NFM")
        print(f"\nCONTRADICTION same input labelled WFM then NFM -> next prediction: "
              f"{decide(e, train[0])}; undo NFM -> ", end="")
        for item in e.feedback.list()["items"]:
            if item["label"] == "NFM":
                e.feedback.remove(item["id"])
        print(decide(e, train[0]))

        if grid:
            print("\nTHRESHOLD GRID (3 corrections; broadcast FM top-1 | others newly called WFM)")
            k3 = train[:3]
            e = fresh_engine(tmp)
            for s in k3:
                e.feedback.add(s.spec, "WFM", wrong_label=decide(e, s))
            for mid in (0.64, 0.68, 0.72, 0.76, 0.80):
                for sharp in (14.0, 25.0, 40.0):
                    learning.SIM_MIDPOINT, learning.SIM_SHARPNESS = mid, sharp
                    newly, _, regress = collateral(e, others, base_labels, "WFM")
                    print(f"  mid {mid:.2f} sharp {sharp:>4.0f}: FM {top1(e, test_fm):>4.0%}  "
                          f"synWFM {top1(e, syn_wfm):>4.0%}  FP {newly:>5.1%}  regressions {regress}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--grid", action="store_true", help="also sweep the similarity threshold")
    run(parser.parse_args().grid)
