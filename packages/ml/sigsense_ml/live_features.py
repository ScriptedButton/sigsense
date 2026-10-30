"""Receiver-scale features for live modulation families, shared by training/API.

Frequency widths stay in Hz. Display cadence is deliberately not interpreted as
an IQ sample clock. No frequency allocation or protocol name is an input.
"""
from __future__ import annotations
import numpy as np

FAMILIES = ["AM", "NFM", "WFM", "SSB", "FSK", "Digital", "Carrier", "Noise"]
FEATURE_VERSION = 1


def live_features(rows_db, lo_bin: int, hi_bin: int, bin_hz: float):
    rows = np.asarray(rows_db, dtype=np.float64)
    bins = rows.shape[1]
    width = hi_bin - lo_bin + 1
    pad = max(3, int(width * .5), int(5000 / bin_hz))
    lo, hi = max(0, lo_bin - pad), min(bins, hi_bin + pad + 1)
    # A relative reference prevents overflow and makes features gain invariant.
    floor = float(np.percentile(rows, 20))
    relative = np.clip(rows[:, lo:hi] - floor, -60, 90)
    power = np.maximum(10 ** (relative / 10) - 1, 0)
    energy = power.sum(axis=1)
    active = energy >= max(float(energy.max()) * .05, 1e-9)
    power = power[active] if active.any() else power
    profile = power.mean(axis=0)
    total = float(profile.sum()) + 1e-12
    norm = profile / total
    peak = int(profile.argmax())
    axis = np.arange(len(profile))
    centroid = float(norm @ axis)
    variance = float(norm @ ((axis - centroid) ** 2))
    sigma = np.sqrt(max(variance, 1e-9))
    cdf = np.cumsum(norm)
    def bw(q):
        a, b = np.searchsorted(cdf, [(1-q)/2, (1+q)/2])
        return max(1, min(int(b), len(profile)-1) - min(int(a), len(profile)-1) + 1) * bin_hz
    def peak_width(drop):
        keep = profile >= profile[peak] * 10 ** (-drop/10)
        a = b = peak
        while a > 0 and keep[a-1]: a -= 1
        while b+1 < len(keep) and keep[b+1]: b += 1
        return (b-a+1) * bin_hz
    db = 10*np.log10(profile + 1)
    shape = np.interp(np.linspace(0, len(profile)-1, 64), axis, db / max(float(db.max()), 1))
    # Variation of each frequency relative to its own level; independent of gain.
    variation = power.std(axis=0) / (power.mean(axis=0) + 1)
    temporal = np.interp(np.linspace(0,len(profile)-1,32), axis, np.minimum(variation, 3)/3)
    ordered = np.sort(profile)[::-1]
    peaks = (db[1:-1] > db[:-2]) & (db[1:-1] >= db[2:]) & (db[1:-1] > db.max()-12)
    scalars = [
        np.log10(max(width*bin_hz,1))/6, np.log10(max(bin_hz,1))/5,
        *[np.log10(max(bw(q),1))/6 for q in (.5,.8,.95)],
        np.log10(max(peak_width(3),1))/6, np.log10(max(peak_width(15),1))/6,
        float(ordered[0]/total),float(ordered[:3].sum()/total),
        float(np.exp(np.log(profile+1e-9).mean())/(profile.mean()+1e-9)),
        float(norm @ ((axis-centroid)**3)/sigma**3)/10,
        float(norm @ ((axis-centroid)**4)/sigma**4)/30,
        (centroid-peak)/max(len(profile),1),
        min(int(peaks.sum()),20)/20,
        np.log10(max(float(profile.max()),1))/9,
        float(active.mean()),
        float(np.std(np.log10(energy+1)))/3,
        float(np.std(np.argmax(power,axis=1)))/max(len(profile),1),
    ]
    return np.nan_to_num(np.r_[scalars,shape,temporal],nan=0,posinf=0,neginf=0).astype(np.float32)
