#!/usr/bin/env python3
"""Paired bootstrap of accuracy differences against the deployed CNN, from preds_test.npz."""
import json, sys
import numpy as np

z = np.load(sys.argv[1], allow_pickle=False)
y, source = z["y"], z["source"]
base = z["deployed_cnn"] == y
names = [k for k in z.files if k not in ("y", "source", "deployed_cnn")]
rng = np.random.default_rng(0)
out = {}
for name in names:
    cand = z[name] == y
    row = {}
    for scope in ["ALL"] + sorted(set(source.tolist())):
        idx = np.arange(len(y)) if scope == "ALL" else np.flatnonzero(source == scope)
        d = cand[idx].astype(float) - base[idx].astype(float)
        boots = np.array([d[rng.integers(0, len(d), len(d))].mean() for _ in range(2000)])
        lo, hi = np.percentile(boots, [2.5, 97.5])
        row[scope] = {"n": int(len(idx)), "cand": round(float(cand[idx].mean()), 4), "base": round(float(base[idx].mean()), 4),
                      "diff": round(float(d.mean()), 4), "ci95": [round(float(lo), 4), round(float(hi), 4)],
                      "significant": bool(lo > 0 or hi < 0)}
    out[name] = row
print(json.dumps(out, indent=1))
