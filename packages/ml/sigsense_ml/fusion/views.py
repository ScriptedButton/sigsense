"""Turn an IQ window (and its spectrogram) into each backbone's expected input."""
from __future__ import annotations

import numpy as np
import torch
import torch.nn.functional as F

from sigsense_ml.preprocess import iq_to_spectrogram

AST_FRAMES, AST_MELS = 1024, 128
AST_MEAN, AST_STD = -4.2677393, 4.5689974  # from the model's preprocessor_config.json


def spectrograms(iq_rows: list[np.ndarray]) -> torch.Tensor:
    """(N,1,256,256) float32 spectrograms, freq on rows, via the deployed preprocessing."""
    return torch.from_numpy(np.stack([iq_to_spectrogram(r) for r in iq_rows]).astype(np.float32)).unsqueeze(1)


def wavesfm_vision(spec: torch.Tensor, transpose: bool = False) -> torch.Tensor:
    """224x224 bicubic resize, normalised to [-1, 1] as the model's image tasks do."""
    x = spec.transpose(2, 3) if transpose else spec
    x = F.interpolate(x, size=(224, 224), mode="bicubic", align_corners=False).clamp(0, 1)
    return (x - 0.5) / 0.5


def standardise_iq(iq: np.ndarray) -> np.ndarray:
    """(2,T) float32 scaled to unit standard deviation (WavesFM's IQ tasks standardise their samples)."""
    out = np.stack([iq.real, iq.imag]).astype(np.float32)
    return out / (out.std() + 1e-9)


def wavesfm_iq(iq_rows: list[np.ndarray], max_len: int = 4096) -> torch.Tensor:
    """(N,2,T) with a common T (<= 4096): windows are centre-cropped, shorter ones zero padded."""
    t = min(max_len, max(len(r) for r in iq_rows))
    t = max(16, t // 16 * 16)
    out = np.zeros((len(iq_rows), 2, t), np.float32)
    for i, r in enumerate(iq_rows):
        s = standardise_iq(r)
        n = s.shape[1]
        if n >= t:
            start = (n - t) // 2
            out[i] = s[:, start:start + t]
        else:
            out[i, :, :n] = s
    return torch.from_numpy(out)


def cldnn_iq(iq_rows: list[np.ndarray], target_rms: float | None = None, length: int = 1024) -> torch.Tensor:
    """(N,2,1024): centre crop or zero pad; optional RMS scaling (RadioML windows have RMS around 0.4-1.2)."""
    out = np.zeros((len(iq_rows), 2, length), np.float32)
    for i, r in enumerate(iq_rows):
        n = len(r)
        if n >= length:
            start = (n - length) // 2
            seg = r[start:start + length]
        else:
            seg = r
        x = np.stack([seg.real, seg.imag]).astype(np.float32)
        if target_rms is not None:
            x = x * (target_rms / (np.sqrt((x ** 2).mean()) + 1e-9))
        out[i, :, :x.shape[1]] = x
    return torch.from_numpy(out)


def ast_from_spectrogram(spec: torch.Tensor, swap_axes: bool = False) -> torch.Tensor:
    """Treat the RF spectrogram as AST's (frames, mel) input: (N,1024,128), standardised like AST inputs.

    Our spectrograms have frequency on rows and time on columns. AST wants time first, so by default the
    image is transposed; ``swap_axes`` feeds it unchanged (rows as time) for comparison.
    """
    x = spec if swap_axes else spec.transpose(2, 3)      # (N,1,time,freq) in the default orientation
    x = F.interpolate(x, size=(AST_FRAMES, AST_MELS), mode="bilinear", align_corners=False).squeeze(1)
    mean = x.mean(dim=(1, 2), keepdim=True)
    std = x.std(dim=(1, 2), keepdim=True) + 1e-6
    return (x - mean) / (2 * std)


def ast_from_audio(iq_rows: list[np.ndarray], kind: str = "fm") -> torch.Tensor:
    """Pseudo-audio views: FM discriminator ('fm') or AM envelope ('am') read as 16 kHz audio, then
    kaldi-style 128-bin log-mel filterbanks exactly as the model's feature extractor builds them."""
    import torchaudio

    feats = []
    for r in iq_rows:
        z = r.astype(np.complex64)
        if kind == "fm":
            wave = np.angle(z[1:] * np.conj(z[:-1])).astype(np.float32) / np.pi
        else:
            wave = np.abs(z).astype(np.float32)
            wave = wave - wave.mean()
        if len(wave) < 400:                      # one 25 ms frame at 16 kHz is the filterbank minimum
            wave = np.pad(wave, (0, 400 - len(wave)))
        wave = torch.from_numpy(wave).unsqueeze(0) * 32768.0 / max(1.0, float(np.abs(wave).max() + 1e-9))
        fb = torchaudio.compliance.kaldi.fbank(
            wave - wave.mean(), htk_compat=True, sample_frequency=16000, use_energy=False, window_type="hanning",
            num_mel_bins=AST_MELS, dither=0.0, frame_shift=10)
        n = fb.shape[0]
        fb = F.pad(fb, (0, 0, 0, AST_FRAMES - n)) if n < AST_FRAMES else fb[:AST_FRAMES]
        feats.append((fb - AST_MEAN) / (AST_STD * 2))
    return torch.stack(feats)
