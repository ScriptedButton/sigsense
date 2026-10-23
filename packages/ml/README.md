# @sigsense/ml

Spectrogram preprocessing, signal generators, embedding CNN and FAISS library utilities for SigSense. See [the research and accuracy plan](../../docs/model-accuracy-research.md) for dataset sources, limitations and proposed next work.

The catalog contains 52 labels (48 with demo generators, four capture-only). The default mixed training profile learns 44, excluding AIS/APRS/LTE/ACARS because their local generators are unverified protocol proxies. Checkpoints retain their own class list; existing 32-class models remain compatible.

```bash
.venv/bin/pip install -e 'packages/ml[torchsig]'

# GPU requires access to the actual GPU device (outside the Codex sandbox).
# Use a fresh output directory. Defaults: 128/class, 32/class eval, 60 epochs.
OMP_NUM_THREADS=8 .venv/bin/python packages/ml/scripts/train.py \
  --source mixed --device cuda --out-dir checkpoints/candidate-001

# Meaningful correctness/regression checks
OMP_NUM_THREADS=2 .venv/bin/python -m unittest discover -s packages/ml/tests -v
```

Sources: `synthetic`, `torchsig`, `mixed`, `radioml`, `all`, `captures`, `compact`. `radioml`/`all` downloads RadioML 2016.10A if missing; review the publisher's CC BY-NC-SA license before use. Other external datasets are researched, not automatically downloaded.

Training writes `signal_embedding.pt`, `class_map.json`, `library.sqlite`, `library.faiss`, `evaluation.json`, `history.json` and `run_config.json`. It selects by validation macro F1, fits temperature on validation, then evaluates the test split. Prototypes use training examples from the actual training distribution. Outputs go to `checkpoints/` by default; review the report before replacing all four deployed artifacts in `models/` together. Do not overwrite a library containing user enrollments without preserving/re-embedding their source captures.

Synthetic and TorchSig evaluation uses independent generated samples, not real-world captures. RadioML partitions are sample-level; original recording/session independence cannot be established from that pickle. Captured-data metrics are only as trustworthy as capture grouping and provenance. The model still consumes magnitude spectrograms; raw-IQ modeling is future work.

## Labeled capture ingestion

Create a JSONL manifest. Paths are relative to the manifest. Every recording, crop and correlated recording from one collection session must use the same `group` and split. Both group and file-path leakage across splits are rejected. Provide at least two classes, with each present in train/val/test. Proxy catalog labels are allowed when supplied by actual curated captures.

```json
{"path":"captures/ble-day1.npy","label":"Bluetooth-LE","group":"siteA-day1-rx1","split":"train","start":0,"count":8192,"source":"own packet-verified capture","license":"owned"}
{"path":"captures/ble-day2.sigmf-data","format":"cf32_le","label":"Bluetooth-LE","group":"siteA-day2-rx1","split":"val","start":16384,"count":8192}
{"path":"captures/ble-day3.npy","label":"Bluetooth-LE","group":"siteB-day3-rx2","split":"test","count":8192}
```

Also add the other classes to each split; these three rows alone intentionally do not meet the two-class requirement. `.npy` must be a one-dimensional complex IQ array. Raw formats `cf32_le` and `cf32_be` support memory-mapped bounded windows. The loader does not infer labels, read SigMF metadata, resample, detect bursts or verify protocol labels; curate those before ingestion. It checks declared grouping and file paths, not identical copies under different names; deduplicate and audit your source corpus. Preserve sample-rate/frequency/SNR/device/session and license metadata alongside the manifest for future analysis.

```bash
.venv/bin/python packages/ml/scripts/train.py --source captures \
  --manifest data/raw/captures.jsonl --device cuda \
  --out-dir checkpoints/captures-001
```

`/classes` returns `classes` from the loaded checkpoint and a separate `catalog`. Library similarity is returned as a neighbor suggestion, not as calibrated classifier confidence. Enrollment remains available for storing/searching embeddings; automatic acceptance of enrolled labels needs separate embedding/rejection validation.

## Compact captured-data experiment (2.47 GB)

The catalog now also includes capture-only 5G-NR, ITS-G5, C-V2X and DVB-T (52 total catalog labels). These have no fake demo generator. `--source compact` combines TorchSig + local synthetic fill with RadioPile's source files `rml2016_10a.h5`, `its_band.h5`, and `lte_wifi_dvbt.h5`. It excludes local AIS/APRS/LTE/ACARS proxies and uses verified dataset LTE labels, giving 49 trained labels when all sources are present.

```bash
# Fetches publisher metadata automatically if missing.
# Compact is the default; a hard size cap prevents the 128 GB bundle by accident.
.venv/bin/python packages/ml/scripts/download_sources.py --bundle compact --max-gb 3

OMP_NUM_THREADS=8 .venv/bin/python -u packages/ml/scripts/train.py \
  --source compact --device cuda --amp --batch-size 96 --workers 4 \
  --epochs 60 --patience 15 --samples-per-class 128 \
  --external-samples-per-class 256 --eval-samples-per-class 64 \
  --seed 20261023 --out-dir checkpoints/compact-49
```

Downloads are resumable and validated against publisher byte sizes and MD5 checksums. Default training caps examples **per class per source** to keep one large source from dominating. HDF5 labels/metadata are read first, with only selected IQ rows read for spectrogram construction; it does not load the entire IQ corpus into RAM. Short IQ windows use a shorter FFT to retain multiple time bins.

RadioPile's original class names are mapped explicitly. Captured sources split by `source + file_path + recording_id` before sampling; RadioML splits by synthetic example because original recording IDs are unavailable. ITS observation groups prevent its two adjacent windows from crossing partitions. These checks do not prove that different observations came from independent sessions or transmitters; reserve an independently collected benchmark before making deployment claims.

`evaluation.json` includes `test_by_source`; `split_evidence.json` records selected row/group IDs and asserts no cross-split group overlap. History records epoch time and peak GPU allocation. CUDA mixed precision improves throughput; batch size may need reducing if another application consumes GPU memory. Research-license terms for the compact sources are retained in `data/raw/radiopile/LICENSES.md`.
