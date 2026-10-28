# Model fusion research: four Hugging Face RF models

Question: can SigSense's signal identification be improved by combining
`ATDI-Group/AST_finetuned_SIGIDwiki`, `ahmedaboulfo/wavesfm`,
`alirezaaminzadeh/radio-modulation-classifier` and `sohelimi/cvnet-rf`?

"Revolutionary" is not a claim this document makes. The only thing that counts is
measured improvement over the deployed pipeline on held-out data; section 5
(results) is where that is judged, including where fusion does not help.

## 1. What each model actually is

| | AST_finetuned_SIGIDwiki | wavesfm | radio-modulation-classifier | cvnet-rf |
|---|---|---|---|---|
| Publisher | ATDI-Group | ahmedaboulfo (paper arXiv:2511.15162) | alirezaaminzadeh ("Aria AI Engineering") | sohelimi |
| Pinned commit | `3a29d4158b6e…` | `6a63011e7cbc…` | `839f80a8de05…` | `df32f6cd9bb0…` (listing only; weights not downloaded) |
| What it is | Audio Spectrogram Transformer (ViT-base), fine-tuned | Multimodal wireless foundation model (MAE-pretrained ViT), IQ + spectrogram-image tokenisers | CLDNN (3 conv blocks + bidirectional LSTM) | Complex-valued CNN vs real CNN comparison for modulation classification |
| Parameters | 86.2 M | 7.44 M (encoder ~5 M, plus MAE decoder) | 0.24 M | 0.38 M (complex) / 0.93 M (real) |
| Input | 128 mel x 1024 frames log-mel from **16 kHz audio** | 1x224x224 spectrogram image **or** IQ in 16-sample segments (<= 4096 samples) | 2x1024 IQ | 2x1024 IQ |
| Output | 21 labels (config.json) - the model card lists 24 | none (foundation model; downstream heads are trained per task) | 11 RadioML classes | 24 RadioML 2018 classes |
| Weights format | `model.safetensors` (fp32, 345 MB) | `.pth` pickle (30 MB) | `.pt` pickle (1 MB) | `.pt` pickles (4.6 MB, 11 MB) |
| Licence | BSD-3-Clause (card) **plus a gate** | Apache-2.0 (weights). The GitHub code repository has **no licence file** | MIT | **none stated** |
| Gated | yes (automatic approval; the user accepted it) | no | no | no |
| Custom code needed | no (stock HF AST layout) | yes, from the GitHub repo (read as text only) | architecture not published, only tensor shapes | `inference.py` in the repo (not run) |
| Reported quality | accuracy 0.99 on the authors' own evaluation set (no split details) | benchmark site only; no number on the card | 79.0 % all-SNR / 93.7 % SNR >= 0 dB on "RadioML 2018.01A" | best val accuracy 50.3 % (complex), 54.4 % (real) on RadioML 2018 |

### Things the model cards do not tell you (checked here)

- **AST** was fine-tuned on SigID wiki *recordings* (audio of signals as heard), not RF waterfall
  images. Its natural input is audio, so using it on spectrograms or IQ is a domain transfer. The
  card lists 24 systems (incl. DVB-T) but the shipped head has 21. Its training data is the same
  wiki our Artemis catalogue comes from: **evaluating it on Artemis material would be leakage.**
- **wavesfm**'s checkpoint contains the MAE decoder as well; only the encoder is used here. The
  card does not give image or IQ normalisation; I took it from the preprocessing scripts in the
  authors' repository (images standardised to [-1, 1], IQ standardised per dataset).
- **radio-modulation-classifier** says that when the real RadioML pickle is unavailable "a synthetic
  I/Q generator" was used for training. It does not say which one produced the quoted numbers.
  Run on the author's own 550-window sample set (MIT-licensed companion dataset), the best
  reconstruction of its forward pass scores **66.2 % all-SNR and 79.4 % at SNR >= 0 dB**, below
  the card's 79.0 % / 93.7 %. I could not close that gap (the activation, pooling and readout
  are not published; 68 variants tried), so either my reconstruction differs in a detail or the
  card's numbers come from a different split.
- **cvnet-rf** states no licence, and its own comparison shows 50 % accuracy on its task.

## 2. Safety inspection of the weight files

Nothing from these repositories was executed. Downloaded into separate empty directories
(`scratchpad/hf-models/<name>/`), all three weight files were verified against the repository
listing (size and SHA256 for LFS files). The two pickles were audited without unpickling:
every opcode and imported global was listed, and the only globals are
`collections.OrderedDict`, `torch._utils._rebuild_tensor_v2` and `torch.FloatStorage`/`LongStorage`.
They are plain state dicts, so they are loaded with `torch.load(..., weights_only=True)` only.
AST uses a small safetensors reader of our own (`fusion/loading.py`).
`trust_remote_code` is never used. WavesFM's repository code and cvnet-rf's `inference.py` were
read as text, never imported.

## 3. Verdicts

| Model | Verdict | Reason |
|---|---|---|
| AST_finetuned_SIGIDwiki | **Go, conditional on the gate terms** | Safe, standard architecture, access works. Domain transfer is the risk; judged by measurement. Redistribution is not assumed (see licences). |
| wavesfm | **Go** | Apache-2.0 weights, small, designed for both spectrogram and IQ inputs. The repository code has no licence, so the encoder is **independently reimplemented** from tensor shapes and the model structure; no code is copied. |
| radio-modulation-classifier | **Go with a caveat** | MIT, tiny, IQ view. Provenance of its quality numbers is unclear and my reproduction only partly matches, so it is used as a feature extractor and judged by measurement, not trusted for its labels. |
| cvnet-rf | **No-go** | No licence means no right to use or redistribute its weights. Even setting that aside its reported accuracy (about 50 %) is the weakest of the four, so it would add little. Weights were not downloaded. The complex-valued-network idea itself is not claimed by anyone and could be tried as our own head later. |

## 4. Licences and what the user must decide

- **Apache-2.0 (wavesfm weights):** may be used and redistributed with the licence text, a NOTICE
  of changes, and attribution. **MIT (CLDNN):** keep the copyright notice. Both are compatible with
  a combined model.
- **BSD-3-Clause (AST card) plus a Hugging Face gate:** the gate's own terms are shown only to the
  person who accepts them and are not visible to this tool. **Decision for the user:** read the
  terms on the model page. Until you have, this work treats the AST weights as *not redistributable*:
  they stay in the local download directory and are never copied into the repository or into
  `models/fusion/`. The fused head trained on top of AST features is small and contains none of the
  AST weights, but it only works for someone who also has AST access; the manifest says how to fetch
  it with their own accepted access.
- **WavesFM code repository (no licence file):** nothing from it is included. If you want to use its
  code directly, ask the authors to add a licence.
- **cvnet-rf:** excluded; ask the author to add a licence if it matters to you.
- **Third-party training data:** only the repository's own data are used (RadioPile sources with their
  recorded licences in `data/raw/radiopile/LICENSES.md`, the local synthetic generators, TorchSig
  synthetic). No new public dataset was added.

## 5. Evaluation design

- **Same examples as the deployed model.** `scripts/fusion_corpus.py` rebuilds the compact-49
  train/validation/test splits through the repository's own dataset code (same seeds, same RadioPile
  group split) while recording each example's IQ. Validation and test spectrograms recomputed from
  the stored IQ are bit-identical to the originals (verified on every split), so the test numbers are
  comparable to `docs/compact-training-results.md`.
- **Leakage controls.** Splits are grouped by recording (captured sources) or by example (RadioML
  simulations, as noted in the existing report). Input-view choices (image orientation, scaling) are
  made on a small train/validation subset and never on the test split.
- **Reported:** accuracy and macro-F1 on the full test split and by source, calibration (ECE) with a
  temperature fitted on validation, confidence quality (AUROC of confidence for correctness), and
  the broadcast-WFM confusion that motivated this work. Per-source SNR, latency and VRAM follow.

## 6. Results (stage 1: frozen backbones, the cheap test)

Setup: each backbone embeds every example of the compact-49 train/validation/test splits (10,752 /
4,096 / 4,096; synthetic + TorchSig + RadioPile captured and simulated windows). A head is trained on
the train split only, tuned on validation, and scored once on test. The deployed CNN is scored on
identical examples and **reproduces the published numbers exactly** (55.2 % overall; 41.7 / 97.1 / 79.2 / 79.8 %
by source), so the comparison is fair. Input views (image orientation, scaling) were chosen on a
588-example train / 392-example validation subset, never on test. "Diff" is paired bootstrap against the
deployed CNN over 2,000 resamples of the 4,096 test examples; `*` means the 95 % interval excludes zero.

| Variant (frozen backbones + trained head) | Test acc | Diff vs CNN (95 % CI) | synthetic | its_band (captured) | lte/wifi/dvb-t (captured, n=192) | RadioML sim |
|---|---:|---|---:|---:|---:|---:|
| **Deployed CNN (baseline)** | **55.2 %** | - | 41.7 | 97.1 | 79.2 | 79.8 |
| AST (spectrogram view) | 46.9 % | -8.5 * [-9.9, -7.2] | 30.5 | 96.9 | 83.9 | 75.0 |
| WavesFM, image tokeniser | 36.5 % | -18.3 * [-19.8, -16.9] | 19.9 | 96.6 | 84.4 | 57.5 |
| WavesFM, IQ tokeniser | 28.3 % | -26.8 * | 11.9 | 78.9 | 43.2 | 62.5 |
| CLDNN features | 29.7 % | -25.0 * | 15.6 | 65.4 | 62.5 | 58.0 |
| AST on demodulated "audio" view | 28.4 % | -26.9 * | 18.9 | 58.6 | 27.6 | 50.0 |
| Control: CNN embedding + the same new head | 55.4 % | +0.2 [-0.7, +1.0] | 41.9 | 97.1 | 78.6 | 80.4 |
| All backbones, no CNN | 47.5 % | -8.7 * | 31.2 | 97.4 | 83.3 | 75.9 |
| All backbones + CNN (concatenated) | 55.6 % | +1.0 [-0.1, +2.2] | 42.2 | 97.7 | 83.9 | 79.0 |
| AST + WavesFM-image + CNN (no IQ needed) | 55.3 % | +0.1 [-0.9, +1.1] | 41.2 | 97.1 | 87.0 | 79.5 |
| Late fusion (val-weighted log-probs; CNN weight 0.51) | 57.0 % | **+1.8 * [+0.9, +2.7]** | 43.5 | 97.9 | 83.9 | 82.0 |

Original (third-party) classification heads on the classes they share with SigSense:
CLDNN 10-way **10.2 %** on 1,280 windows (10 classes, chance 10 %); AST **17.4 %** on 512 windows
(6 classes, chance 16.7 %). Their own labels do not transfer to our data at all; only their features
carry information.

Observations:
- No frozen backbone beats the task-trained CNN. The best (AST) is 8.5 points worse overall.
- On the two captured sources AST and WavesFM roughly tie the CNN (its_band) or are ahead on the small
  LTE/Wi-Fi/DVB-T set (+3.6 and +2.1, not significant at n=192, and those 192 windows come from only 29
  recordings, so even the "significant" +5.7 for late fusion there overstates the evidence).
- Concatenation with the CNN adds nothing measurable. A late-fusion ensemble adds +1.8 points
  (significant at the example level), at the cost of running a 86 M-parameter ViT per signal, and its
  calibration is worse (ECE 0.094 vs 0.043 before recalibration).
- Run-to-run jitter on the same data is about 0.1-0.2 points (GPU nondeterminism), small next to the
  intervals above.

### The WFM case that motivated this

In this test set the deployed CNN labels 43 % of true WFM windows correctly (55/128), calling 39 of them
**AM** and none 5G-NR. Late fusion/concatenation moves WFM recall to about 45 % (+2 windows).
The reported failure (broadcast FM called 5G-NR / C-V2X on a live capture) is **not reproducible here**:
the corpus has no captured broadcast-FM windows, only simulated and synthetic WFM. Fixing that needs real
broadcast-FM recordings, not a bigger model; the manual-correction feature added earlier is the
right tool until they exist.

### Not done, and why

- **Artemis images were not evaluated.** There is one image per wiki signal (about 570 classes of one
  example), so there is no held-out repetition per class to score a probe on, and AST was fine-tuned on
  this same wiki material, so any AST score there would be leakage rather than evidence.
- **Fine-tuning AST/WavesFM** (the case most likely to change the picture) was started and stopped:
  the stage gate in this task calls for continuing only if a frozen backbone clearly beats the
  current model, and none does. The scripts (`packages/ml/scripts/fusion_finetune.py`) exist but are
  untested beyond the first crash and were not run to completion.
- **Per-SNR / latency / VRAM tables** were not produced, since the gate stopped the work before the
  inference module was built. Frozen-feature extraction on the RTX 4070 (fp16) ran at roughly
  7 ms per example for AST and under 1 ms for WavesFM's image path (batch throughput from the extraction
  logs, including view preparation; not isolated single-crop latency).

## 7. Go / no-go

| Question | Answer |
|---|---|
| Does any backbone beat the current CNN on held-out data? | **No.** Frozen AST is 8.5 points worse; WavesFM 18 worse. |
| Does a fused model clearly beat it? | **Barely, and only as an ensemble:** late fusion +1.8 points (CI +0.9 to +2.7), concatenation +1.0 (not significant). Gains concentrate on the small captured LTE/Wi-Fi/DVB-T set (n=192, 29 recordings), which is exactly where the evidence is weakest. |
| Ship the fused model? | **No-go for now.** The cost (a gated 86 M-parameter model plus three more networks per signal, a licence/gate dependency, redistribution limits) is large for +1-2 points that are unproven on real recordings. |
| What would change this? | (1) Real labelled recordings of the cases that fail in use (broadcast FM, GMRS, digital voice) to measure and train on; (2) fine-tuning AST or WavesFM on those, which frozen features cannot predict; (3) if revisited, a late-fusion ensemble of AST + CNN is the only configuration worth building, behind a flag that is off by default. |
| Per model | AST: usable, conditional on the gate terms; WavesFM: usable, weak frozen; CLDNN: no value (10 % native, 30 % as features); cvnet-rf: excluded (no licence). |

Everything above came from `packages/ml/scripts/fusion_corpus.py`, `fusion_experiment.py` and
`fusion_bootstrap.py`; the backbone implementations are in `packages/ml/sigsense_ml/fusion/`
(own code, verified against the reference AST implementation to 0.0 difference and against `timm`'s block to 3.5e-4).
No file in `models/` was changed and no third-party weights are in the repository.
