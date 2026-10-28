# Improving signal identification: research and results

Written 2026-10-23. Scope: the browser signal-id pipeline (`apps/web/src/lib/sdr/signal-id/*`) that labels signals on the live spectrum. The backend CNN and the FAISS neighbour library were **not** changed (see "Not done").

## What was weak

The shipped MLP was trained on clean synthetic spectra at one resolution (500 Hz bins), then used on RTL-SDR and HackRF spectra. Evaluating it under receiver effects it never saw showed the problem (numbers below): accuracy fell to ~35–40 % at 8–16 kHz bins, to ~13 % on push-to-talk-style bursts, and a pure-noise spectrum produced ~30 false tracks. Its probabilities were also overconfident.

## Approaches surveyed

I read search-result summaries and abstracts, not full papers, unless noted. Numbers I quote from the literature are as reported by those sources and not reproduced here.

| Approach | What the sources say | Fit here (browser, no hardware, no big downloads, licence) | Verdict |
|---|---|---|---|
| Domain randomisation / impairment augmentation | TorchSig/Sig53 samples impairments (phase/frequency/time shift, fading, IQ imbalance, resampling) from distributions so models do not learn a single channel ([Sig53](https://arxiv.org/abs/2207.09918)); TorchSig 2.0 adds clock jitter, passband ripple, phase noise ([GRCon 2025](https://events.gnuradio.org/event/26/contributions/752/attachments/220/586/TorchSig_GRCon2025.pdf)). A 2025 paper trains embeddings only on synthetic protocols and reports transfer to unseen real signals ([2510.23186](https://arxiv.org/abs/2510.23186)). I found no study that quantifies the sim-to-real gain per impairment. | Cheap, pure code, directly targets the measured failures. | **Chosen** |
| Temperature scaling | One scalar fitted on held-out logits fixes overconfidence without changing the top class ([Guo et al. 2017](https://arxiv.org/abs/1706.04599)). | Trivial; weight format gets one optional field. | **Chosen** |
| Temporal evidence combination | Not a separate paper; standard product-of-experts / Bayesian filtering over frames. | A signal is seen for many frames. | **Chosen** (log-domain accumulation) |
| Robust detection by frame averaging | Standard spectrum-sensing practice (averaging periodograms reduces variance). | Fixed the false-track flood. | **Chosen** |
| Bayesian fusion with band-plan/catalog priors | Classifier fusion with an informative class prior is textbook ([Bayesian classifier fusion](https://arxiv.org/abs/2106.01770)); I found no RF-specific study of band-plan priors. | Catalog now has 608 entries. | **Built, evaluated, left OFF** (no gain; see results) |
| Larger / different deep models on raw IQ or spectrograms (CNN/ResNet, transformers) | RadioML-style benchmarks; O'Shea et al. report a CNN beating a boosted-tree feature classifier by about 4 dB at low-to-medium SNR ([intro to deep learning for the physical layer](https://arxiv.org/abs/1702.00832)). RadioML data is CC BY-NC-SA ([DeepSig](https://www.deepsig.ai/datasets/)) and disputed as a real-world proxy. | Needs the raw IQ, which the browser spectrum path does not have. Backend already has a CNN (`docs/model-accuracy-research.md`). | Not done here |
| Cyclostationary features / higher-order cumulants | Classic AMC, robust to unknown pulse shape but computationally heavy ([overview](https://www.researchgate.net/publication/4070695_Higher-Order_Cyclic_Cumulants_for_High_Order_Modulation_Classification)). I found no like-for-like boosted-trees vs CNN comparison on these features. | Needs IQ, too slow for a per-frame browser path. | Not done |
| Open-set / OOD rejection (energy score, OpenMax, prototypes) | Energy-based OOD is the standard baseline ([Liu et al.](https://arxiv.org/abs/2010.03759), cited by the RF papers I saw, e.g. [UAV open-set](https://www.mdpi.com/2504-446X/10/1/36)). | Needs real unknown-signal data to set thresholds; I have none. | Not done |
| Unsupervised domain adaptation / fine-tuning on real captures | Active research ([example](https://arxiv.org/abs/2510.00589), abstract only). | Needs real labelled captures from this setup. | Next step (biggest) |

## Chosen approach

A combination, each part addressing a measured failure:

1. **Impairment-augmented training** (`scripts/signal-id/impair.ts`, `dataset.ts`): each synthetic emitter also yields impaired copies (coarser bins 1–16 kHz, DC spike, gain offset, push-to-talk duty). They go through the same detect → re-bin → features path the live engine uses, so training matches deployment. Network is 128-64 (was 56-36); same weight-file format.
2. **Robust detection** (`pipeline.ts`): detect on the power average of the last 4 frames instead of one periodogram.
3. **Temperature calibration** (`train.ts`, `mlp.ts`): fitted on a separate validation split; stored as `temperature` in `model-weights.json` (optional field, old files still load).
4. **Log-domain temporal smoothing** (`engine.ts`) replaces the moving average.
5. **Catalog prior fusion** (`priors.ts`): implemented with a floor so it can reorder but never exclude a class. Off by default.

## Results

Reproduce: `cd apps/web && npx tsx scripts/signal-id/eval.ts --per-class=20 [--seed=N]`. The harness synthesises **held-out** emitters (seed differs from training), applies the impairments, and runs the real `SignalIdEngine` under each configuration. "Baseline" = the previous weights, single-frame detection, moving-average smoothing, no priors (kept in `model-weights.prev.json`). "Shipped" = the new defaults. Accuracy counts a missed signal as wrong. Two independent seeds, 20 emitters per class × 10 classes per scenario:

| Scenario | Baseline (seed 987654) | Shipped (seed 987654) | Baseline (seed 424242) | New model+log (seed 424242) |
|---|---|---|---|---|
| clean | 85.5 % | 95.5 % | 85.5 % | 92.5 % |
| 2 kHz bins | 82.5 | 86.5 | 77.5 | 84.0 |
| 8 kHz bins | 41.0 | 82.5 | 41.0 | 74.0 |
| 16 kHz bins | 34.0 | 72.0 | 33.5 | 71.5 |
| DC spike + gain | 86.0 | 85.5 | 82.0 | 83.5 |
| bursty (PTT) | 12.5 | 72.0 | 14.5 | 73.0 |
| adjacent interferer | 84.5 | 96.5 | 82.0 | 95.0 |
| combined (coarse+DC+bursty+gain) | 13.0 | 41.0 | 11.0 | 44.5 |
| **mean over scenarios** | **56.9 %** | **79.5 %** | **54.5 %** | **78.8 %** |
| mean calibration error (ECE) | 0.21 | 0.12 | 0.21 | 0.13 |

False tracks on a pure-noise spectrum at fine resolution: **~30 → 0** (baseline vs new).

Catalog prior fusion, clean ablation (4 kHz bins, 20/class, seed 424242, new model, log smoothing, prior off → prior on at full strength): informative prior 84.5 → 83.5 %, no catalog coverage 83.5 → 83.5 %, misleading prior 81.0 → 79.5 %. Weaker strengths (exponent 0.5 and 0.25) moved results by under a point either way. A second seed compares "prior + moving average" with "shipped" (no prior, log smoothing), so it mixes two changes and is not a clean ablation; it points the same way (informative 84.0 vs 81.0, misleading 75.0 vs 81.0, i.e. gains and losses of similar size). Net effect is about zero, so it ships **off**. The harness assigns the informative/misleading frequencies from the catalog itself, so this is a controlled experiment about prior reliability, not a measurement of real band occupancy.

Per-class accuracy over all scenarios (seed 424242, baseline → new model+log): AM 53→78, USB 43→88, LSB 53→81, NBFM 41→79, WBFM 69→97, FSK 34→66, DIGITAL 79→93, NOISE 54→83, CW 56→58, CARRIER 65→66.

## What this does **not** show

- All numbers are on **synthetic** signals with **modelled** impairments. They show robustness to those impairments, not accuracy on real RTL-SDR or HackRF captures. The model is still trained only on synthetic data; no real-world accuracy claim is made.
- The synthetic noise and the model share one generator family, so some gain is probably optimistic. Real GMRS/NBFM at coarse bins remains unverified.
- The accuracy stored in `model-weights.json` (and shown in the UI) is now 76.9 %: the held-out accuracy on the harder, impaired training mixture, not comparable to the old 88 % on clean data only.

## Known remaining weaknesses

- **CW vs CARRIER vs NOISE** are still confused (~58 % / ~66 %); a plain carrier and a very slow keyed carrier look alike in a spectrum.
- **DC spike on a noise-only spectrum** is still reported as one false track (a narrow steady carrier at the exact centre looks like a real carrier). Tuning slightly off a signal avoids it; the earlier GMRS-at-centre change deliberately disabled the DC guard on coarse spectra. A real fix needs the receiver to offset its LO.
- **Combined impairments** (coarse bins + DC + bursty + gain) reach only ~41–45 %.
- The low-resolution cases (≥8 kHz bins) cannot recover shape that the bins do not contain; the flag and confidence discount from the earlier work still apply.

## Files

Changed or added under `apps/web`: `src/lib/sdr/signal-id/{engine,classifier,mlp,pipeline,priors}.ts` (new: `pipeline.ts`, `priors.ts`), `catalog/match.ts` (exports `catalogEntries`), `model-weights.json` (retrained), `model-weights.prev.json` (the previous weights, for rollback: copy it back over `model-weights.json`), `scripts/signal-id/{impair,eval,dataset,train}.ts` (new: `impair.ts`, `eval.ts`).

Not touched: `models/library.*`, `models/signal_embedding.pt`, the backend, and the UI components.

## Next steps

1. Record real labelled captures with the HackRF and RTL-SDR (a few minutes each of NBFM/GMRS, WBFM, AM, SSB, a CW beacon, a known digital mode, plus empty-band noise at the spans you use) and add them as a real-data test set; then fine-tune on them. This is the single most valuable step.
2. Only after that, revisit the catalog prior; it needs real band-occupancy statistics to be worth enabling.
3. Consider LO offsetting for HackRF so the DC spike never sits on a signal.
4. For raw-IQ models and open-set rejection (energy score), use the backend, where IQ exists, and set thresholds on real unknown signals.
