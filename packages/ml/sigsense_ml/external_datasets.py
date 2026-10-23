"""External training datasets: TorchSig generators + RadioML 2016.10a."""

from __future__ import annotations

import pickle
import tarfile
import urllib.request
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset

from sigsense_ml.classes import SIGNAL_CLASSES, CAPTURE_ONLY_CLASSES, class_to_idx
from sigsense_ml.preprocess import iq_to_spectrogram
from sigsense_ml.synthetic import generate_iq

# TorchSig class names -> SigSense taxonomy (many-to-one OK)
TORCHSIG_TO_SIGSENSE: dict[str, str] = {
    "ook": "ASK",
    "4ask": "ASK4",
    "8ask": "ASK8",
    "16ask": "ASK16",
    "2fsk": "FSK",
    "4fsk": "FSK4",
    "8fsk": "FSK8",
    "16fsk": "FSK16",
    "2gfsk": "GFSK",
    "4gfsk": "GFSK4",
    "8gfsk": "GFSK8",
    "2msk": "MSK",
    "2gmsk": "GMSK",
    "bpsk": "BPSK",
    "qpsk": "QPSK",
    "8psk": "8PSK",
    "16psk": "16PSK",
    "32psk": "32PSK",
    "16qam": "QAM16",
    "64qam": "QAM64",
    "32qam": "QAM32",
    "256qam": "QAM256",
    "16apsk": "APSK16",
    "32apsk": "APSK32",
    "ofdm-64": "OFDM",
    "ofdm-128": "OFDM",
    "ofdm-256": "OFDM",
    "fm": "WFM",
    "am-dsb": "AM",
    "am-dsb-sc": "AM",
    "am-usb": "USB",
    "am-lsb": "LSB",
    "tone": "CW",
    "lfm-radar": "Chirp-LFM",
    "lfm-data": "Chirp-LFM",
    "adsb-long": "ADS-B",
    "adsb-short": "ADS-B",
    "btle": "Bluetooth-LE",
    "gsm": "GSM",
    "dmr": "DMR",
    "p25": "P25",
    "lora": "LoRa",
    "80211a": "WiFi",
    "zigbee": "Zigbee",
}

# RadioML 2016.10a modulation labels -> SigSense
RADIOML_TO_SIGSENSE: dict[str, str] = {
    "8PSK": "8PSK",
    "AM-DSB": "AM",
    "AM-SSB": "USB",
    "BPSK": "BPSK",
    "CPFSK": "FSK",
    "GFSK": "GFSK",
    "PAM4": "ASK4",
    "QAM16": "QAM16",
    "QAM64": "QAM64",
    "QPSK": "QPSK",
    "WBFM": "WFM",
}

RADIOML_URL = "https://zenodo.org/records/18397070/files/RML2016.10a.tar.bz2?download=1"


def normalize_spectrogram(spec: np.ndarray) -> np.ndarray:
    """Percentile-normalize a spectrogram (linear or dB) to float32 [0, 1]."""
    arr = np.asarray(spec, dtype=np.float32)
    lo, hi = np.percentile(arr, [5, 99])
    if hi - lo < 1e-6:
        return np.zeros_like(arr, dtype=np.float32)
    return np.clip((arr - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)


def _aug_spec(spec: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    out = spec
    if rng.random() < 0.3:
        out = np.roll(out, int(rng.integers(-20, 21)), axis=0)
    if rng.random() < 0.2:
        out = np.clip(out + rng.normal(0, 0.02, out.shape), 0, 1).astype(np.float32)
    return out.astype(np.float32)


def classes_covered_by_torchsig() -> set[str]:
    return set(TORCHSIG_TO_SIGSENSE.values())


def torchsig_generators_for_sigsense() -> list[str]:
    """Unique TorchSig generator names that map into SIGNAL_CLASSES."""
    covered = set(SIGNAL_CLASSES)
    return sorted({ts for ts, ss in TORCHSIG_TO_SIGSENSE.items() if ss in covered})


class SyntheticSpectrogramDataset(Dataset):
    """Original SigSense synthetic IQ → spectrogram dataset."""

    def __init__(
        self,
        samples_per_class: int,
        n_iq: int = 8192,
        seed: int = 0,
        class_names: list[str] | None = None,
        augment: bool = True,
    ) -> None:
        self.items: list[tuple[np.ndarray, int]] = []
        names = class_names if class_names is not None else [c for c in SIGNAL_CLASSES if c not in CAPTURE_ONLY_CLASSES]
        rng = np.random.default_rng(seed)
        for name in names:
            if name not in class_to_idx:
                continue
            ci = class_to_idx[name]
            for j in range(samples_per_class):
                snr = float(rng.uniform(0, 25))
                iq = generate_iq(name, n=n_iq, snr_db=snr, seed=seed + ci * 1000 + j)
                spec = iq_to_spectrogram(iq)
                if augment:
                    spec = _aug_spec(spec, rng)
                self.items.append((spec, ci))

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx: int):
        spec, label = self.items[idx]
        return torch.from_numpy(spec).unsqueeze(0), label


class TorchSigSpectrogramDataset(Dataset):
    """Finite materialization of TorchSig IQ → spectrogram, remapped to SigSense labels."""

    def __init__(
        self,
        samples_per_class: int,
        *,
        seed: int = 0,
        snr_db_min: float = 0.0,
        snr_db_max: float = 25.0,
        augment: bool = True,
    ) -> None:
        try:
            from torchsig.datasets.datasets import TorchSigIterableDataset
            from torchsig.utils.defaults import TorchSigDefaults
        except ImportError as e:
            raise ImportError(
                "torchsig is required for --source torchsig|mixed. "
                "Install with: pip install torchsig"
            ) from e

        generators = torchsig_generators_for_sigsense()
        if not generators:
            raise RuntimeError("No TorchSig generators map to SIGNAL_CLASSES")

        by_target: dict[str, list[str]] = {}
        for ts in generators:
            ss = TORCHSIG_TO_SIGSENSE[ts]
            by_target.setdefault(ss, []).append(ts)
        targets = [t for t in SIGNAL_CLASSES if t in by_target]
        total = samples_per_class * len(targets)

        # Short IQ bursts → SigSense STFT (256×256); much faster than TorchSig defaults
        num_iq = 8192
        meta = dict(TorchSigDefaults().default_dataset_metadata)
        meta.update(
            {
                "num_iq_samples_dataset": num_iq,
                "fft_size": 256,
                "fft_stride": 128,
                "num_signals_min": 1,
                "num_signals_max": 1,
                "snr_db_min": snr_db_min,
                "snr_db_max": snr_db_max,
                "signal_duration_in_samples_min": int(num_iq * 0.85),
                "signal_duration_in_samples_max": num_iq,
                "bandwidth_min": 1e5,
                "bandwidth_max": 2.5e6,
            }
        )

        print(
            f"  TorchSig: materializing {total} spectrograms "
            f"({samples_per_class}/class × {len(targets)} classes)...",
            flush=True,
        )

        rng = np.random.default_rng(seed)
        self.items: list[tuple[np.ndarray, int]] = []
        counts = {t: 0 for t in targets}

        # One iterable per target keeps class balance without rejection waste
        for ti, ss in enumerate(targets):
            ds = TorchSigIterableDataset(
                signal_generators=by_target[ss],
                transforms=[],
                target_labels=["class_name"],
                **meta,
            )
            ds.seed(seed + ti * 10000)
            it = iter(ds)
            while counts[ss] < samples_per_class:
                data, _name = next(it)
                iq = np.asarray(data).astype(np.complex64).ravel()
                if iq.size < 1024:
                    continue
                spec = iq_to_spectrogram(iq)
                if augment:
                    spec = _aug_spec(spec, rng)
                self.items.append((spec, class_to_idx[ss]))
                counts[ss] += 1
            if (ti + 1) % 6 == 0 or ti == len(targets) - 1:
                print(
                    f"    … {ti + 1}/{len(targets)} classes ({len(self.items)} samples)",
                    flush=True,
                )

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx: int):
        spec, label = self.items[idx]
        return torch.from_numpy(spec).unsqueeze(0), label


class MixedSpectrogramDataset(Dataset):
    """TorchSig for covered classes + synthetic for the rest."""

    def __init__(self, samples_per_class: int, seed: int = 0, augment: bool = True) -> None:
        covered = classes_covered_by_torchsig()
        uncovered = [c for c in SIGNAL_CLASSES if c not in covered and c not in CAPTURE_ONLY_CLASSES]
        torch_ds = TorchSigSpectrogramDataset(samples_per_class, seed=seed, augment=augment)
        synth_ds = SyntheticSpectrogramDataset(
            samples_per_class, seed=seed + 17, class_names=uncovered, augment=augment
        )
        self.items = list(torch_ds.items) + list(synth_ds.items)
        print(
            f"  Mixed: {len(torch_ds)} TorchSig + {len(synth_ds)} synthetic "
            f"({len(uncovered)} synthetic-only classes)"
        )

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx: int):
        spec, label = self.items[idx]
        return torch.from_numpy(spec).unsqueeze(0), label


def ensure_radioml(data_dir: Path) -> Path:
    """Download + extract RadioML 2016.10a if needed; return path to pickle."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "RML2016.10a_dict.pkl",
        "RML2016.10a_dict.dat",
        "RML2016.10a_dict_optimized.pkl",
        "RML2016.10a.pkl",
    ):
        candidate = data_dir / name
        if candidate.is_file():
            return candidate
    # Also search one level deep after extract
    for path in data_dir.rglob("RML2016.10a*"):
        if path.suffix in {".pkl", ".dat"} and path.is_file():
            return path

    archive = data_dir / "RML2016.10a.tar.bz2"
    if not archive.is_file():
        print(f"Downloading RadioML 2016.10a → {archive} ...")
        urllib.request.urlretrieve(RADIOML_URL, archive)  # noqa: S310
    print(f"Extracting {archive} ...")
    with tarfile.open(archive, "r:bz2") as tar:
        tar.extractall(data_dir, filter="data")

    for path in data_dir.rglob("RML2016.10a*"):
        if path.suffix in {".pkl", ".dat"} and path.is_file():
            return path
    raise FileNotFoundError(f"RadioML pickle not found under {data_dir}")


class RadioMLSpectrogramDataset(Dataset):
    """RadioML 2016.10a IQ bursts → SigSense spectrograms."""

    def __init__(
        self,
        data_dir: Path,
        *,
        samples_per_class: int | None = None,
        snr_min: int = 0,
        seed: int = 0,
        split: str = "train",
        train_frac: float = 0.7,
        augment: bool = True,
    ) -> None:
        path = ensure_radioml(Path(data_dir))
        with open(path, "rb") as f:
            raw = pickle.load(f, encoding="latin1")  # noqa: S301

        rng = np.random.default_rng(seed)
        buckets: dict[str, list[np.ndarray]] = {c: [] for c in SIGNAL_CLASSES}

        for key, examples in raw.items():
            if isinstance(key, tuple):
                mod, snr = key
            else:
                continue
            if int(snr) < snr_min:
                continue
            ss = RADIOML_TO_SIGSENSE.get(str(mod))
            if ss is None:
                continue
            arr = np.asarray(examples)  # (N, 2, 128) or similar
            for i in range(arr.shape[0]):
                iq = (arr[i, 0] + 1j * arr[i, 1]).astype(np.complex64)
                buckets[ss].append(iq)

        self.items: list[tuple[np.ndarray, int]] = []
        for ss, iqs in buckets.items():
            if not iqs:
                continue
            # Partition RNG must not depend on augmentation/sample-cap consumption.
            np.random.default_rng(seed + class_to_idx[ss]).shuffle(iqs)
            n = len(iqs)
            cut = int(n * train_frac)
            val_end = cut + (n - cut) // 2
            if split not in {"train", "val", "test"}:
                raise ValueError(f"Unknown split: {split}")
            chosen = {"train": iqs[:cut], "val": iqs[cut:val_end], "test": iqs[val_end:]}[split]
            if samples_per_class is not None:
                chosen = chosen[:samples_per_class]
            for iq in chosen:
                # Tile short 128-sample bursts so STFT has enough length
                if iq.size < 2048:
                    reps = int(np.ceil(2048 / iq.size))
                    iq = np.tile(iq, reps)[:4096]
                spec = iq_to_spectrogram(iq)
                if augment:
                    spec = _aug_spec(spec, rng)
                self.items.append((spec, class_to_idx[ss]))

        print(
            f"  RadioML ({split}): {len(self.items)} samples from {path.name} "
            f"(snr>={snr_min})"
        )

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx: int):
        spec, label = self.items[idx]
        return torch.from_numpy(spec).unsqueeze(0), label
