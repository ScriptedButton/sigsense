# Compact training results — 2026-10-23

The 49-class compact model is installed in `models/`. The prior deployed 32-class model and its matching library are preserved in `checkpoints/pre-compact-49/`. The originally shipped artifacts remain in `checkpoints/pre-training/`.

## Run

- Download: 2,468,806,272 bytes (2.47 GB) for the three RadioPile source HDF5 files; all publisher size and MD5 checks passed.
- Training: 10,752 examples; validation: 4,096; test: 4,096. Each generated class has 128 training examples; each external source/class has up to 256. Evaluation uses 64 per source/class.
- RTX 4070, CUDA mixed precision, batch size 96, four loader workers. GPU utilization reached 99% in a measured snapshot. Training took 17.0 minutes for 60 epochs.
- Checkpoint selected by validation macro F1: epoch 52. Temperature 1.496 fitted on validation only and applied in API inference.
- Catalog: 52 labels; checkpoint: 49 labels. AIS/APRS/ACARS remain excluded pending verified data. LTE now uses external labels, with its synthetic proxy removed from the compact training mix.

## Held-out results

| Source | Test examples | Accuracy | Macro F1 (supported labels) |
|---|---:|---:|---:|
| TorchSig + local synthetic | 2816 | 41.6% | 41.1% |
| ITS band (captured observations) | 384 | 97.1% | 97.7% |
| LTE / Wi-Fi / DVB-T (captured recordings) | 192 | 79.2% | 84.8% |
| RadioML 2016.10A (simulated, SNR >= 0 dB) | 704 | 79.8% | 79.4% |
| **Combined** | **4096** | **55.2%** | **54.0%** |

Captured LTE/Wi-Fi/DVB-T windows are grouped by original file + recording ID. ITS windows are grouped by observation ID; observations still originate from the same source collection, so this is not an independent receiver/site benchmark. RadioML has no original recording groups and uses a synthetic-example split. Selected group/row evidence is saved and checked for overlap.

## Comparisons on identical test examples

Each pair uses only test labels supported by both checkpoints. All outputs of each model remain eligible predictions. Different rows therefore have different example sets.

| Earlier checkpoint | Shared labels | Same examples | Earlier accuracy | New accuracy |
|---|---:|---:|---:|---:|
| Generated-only 44-class candidate | 44 | 3712 | 26.1% | 51.1% |
| Previously deployed 32-class | 29 | 2752 | 18.6% | 59.7% |

The new model improves on both earlier checkpoints in their shared-label comparisons. Checkpoint hashes and per-source comparisons are stored in `comparison.json`; the prior deployed path is resolved to its preserved backup so the comparison remains auditable after installation.

## Remaining limitations

Higher-order modulation recognition is still weak: test recall was about 1.6% for QAM32/APSK16, 12.5% for 16PSK/32PSK, and 25% for QAM16/QAM64. Important confusion pairs include QAM16 vs QAM64 and USB vs LSB. The next model experiment should retain complex IQ/phase information and target these confusions, with additional independently captured tests.

At the current 0.45 confidence threshold, the model accepted 54.7% of these known-class test examples with 85.6% accuracy among accepted predictions. This is not a validated unknown-signal detection result. Library neighbors remain suggestions and cannot turn a low classifier score into a probability.

## Validation and artifacts

Ten regression tests passed, including recording split isolation, native HDF5 mapping, short-IQ temporal structure, legacy checkpoint compatibility and confidence behavior. Existing BLE fixture tests passed. Export checks verified finite inference, 49 matching class/prototype labels, FAISS lookup and SQLite integrity. The API inference engine successfully loaded the installed artifacts and returned valid predictions.

- Deployed artifacts: `models/signal_embedding.pt`, `class_map.json`, `library.sqlite`, `library.faiss`.
- Detailed reports: `checkpoints/compact-49/evaluation.json`, `comparison.json`, `split_evidence.json`.
- Reproduction: `checkpoints/compact-49/run_config.json`, `history.json`, and `checkpoints/compact-49.log`.
- Dataset sources and next steps: [research](model-accuracy-research.md), [training guide](../packages/ml/README.md).

An already-running API process must reload its inference engine to pick up the newly installed weights.
