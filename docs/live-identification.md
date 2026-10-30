# Live modulation identification

The live display now uses a separate receiver-scale family classifier. The image
CNN was trained for different inputs and was producing confident protocol names
for ordinary analog spectra. In a comparison on 96 identical synthetic receiver
scenes, the prior CNN got 6 family labels right; the new classifier got 80 right,
accepted 83, and rejected 13. These scenes use an emitter seed separate from
training, validation, and the larger test set. Rejections count as wrong.

The larger test set contains 1,000 synthetic scenes (125 per family), with
90.5% accuracy before rejection, 91.1% coverage at confidence 0.65, and 94.8%
accuracy among accepted predictions. See `live-family-evaluation.json` and
`live-family-comparison.json` for counts and confusion matrices. These are
synthetic results, not a captured RF accuracy measurement or an unknown-signal
false-acceptance benchmark. FSK versus narrow FM remains the main confusion.

Families: AM, narrow FM, wide FM, SSB (side unresolved), FSK, digital, carrier,
and noise. Exact digital protocols are not established by this model. Validated
BLE packets still supply exact BLE identities. User corrections take precedence.

RTL USB, rtl_tcp, and browser demos send consecutive raw receiver IQ to
`/analyze/iq-bands`, before display averaging. Captures contain 65,536 complex
samples encoded as interleaved little-endian signed int16, at most once per
900 ms, with one request in flight. Tuning and sample-rate changes discard
partial captures. The backend forms 4,096-bin STFT rows and classifies detected
bands with the family model. OpenWebRX/Kiwi spectrum sources use the same family
model on their available display rows. HackRF uses the family model on its IQ.

The image CNN remains available for uploads, embeddings and correction storage.
The live model uses bandwidths in Hz, spectral shape, concentration, distribution
moments and temporal variation. It receives neither frequency allocations nor
protocol names. Input features are invariant to receiver gain. The bundled MLP
has two hidden layers (96, 48), with validation-selected weights and temperature.
Training creates physically modulated complex signals with randomized audio,
carrier offset, SNR, gain, burst gating, sample rates, FFT sizes and power averaging.
Training, validation and test use independent emitter seeds (731, 1731, 2731).

Reproduce from repository root:

```sh
PYTHONPATH=apps/api:packages/ml OMP_NUM_THREADS=1 .venv/bin/python packages/ml/scripts/train_live_families.py
PYTHONPATH=apps/api:packages/ml OMP_NUM_THREADS=1 .venv/bin/python packages/ml/scripts/eval_live_families.py
```

The feature cache is `data/processed/live-families.npz`. Remove it to regenerate
training scenes. Shipped weights are `models/live_families.json`; no training
framework is needed for its NumPy inference. If the artifact is absent, existing
CNN inference remains available. A backend restart loads changed weights, and a
new web build plus page refresh enables browser IQ transport.
