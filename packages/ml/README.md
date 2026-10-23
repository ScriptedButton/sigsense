# @sigsense/ml

Spectrogram preprocessing, synthetic IQ generation, embedding CNN, and FAISS library utilities for SigSense.

```bash
# from repo root
.venv/bin/pip install -e "packages/ml[torchsig]"

# Default: TorchSig generators (mapped to SigSense labels) + synthetic fill
.venv/bin/python packages/ml/scripts/train.py --source mixed --epochs 12

# Alternatives
.venv/bin/python packages/ml/scripts/train.py --source synthetic
.venv/bin/python packages/ml/scripts/train.py --source torchsig
.venv/bin/python packages/ml/scripts/train.py --source radioml   # downloads RadioML 2016.10a
.venv/bin/python packages/ml/scripts/train.py --source all       # mixed + RadioML
```
