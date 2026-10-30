"""A small receiver-scale modulation-family model for live RF bands.

The image CNN remains responsible for embeddings and user corrections. Live
family predictions use physical bandwidth and spectra from modulated IQ rather
than forcing display images into its 49 mutually exclusive protocol labels.
"""
from __future__ import annotations
import json
from dataclasses import replace
from pathlib import Path
import numpy as np
from sigsense_ml.live_features import FEATURE_VERSION, live_features


class LiveFamilyClassifier:
    def __init__(self, path: Path):
        model = json.loads(path.read_text())
        if model['version'] != FEATURE_VERSION:
            raise ValueError('Live family feature version mismatch')
        self.classes = model['classes']
        self.mean = np.array(model['mean'], dtype=np.float32)
        self.std = np.array(model['std'], dtype=np.float32)
        self.layers = [(np.array(l['w'], dtype=np.float32), np.array(l['b'], dtype=np.float32)) for l in model['layers']]
        self.temperature = model['temperature']

    def probabilities(self, rows, lo, hi, bin_hz):
        features = live_features(rows, lo, hi, bin_hz)
        x = (features-self.mean)/self.std
        for i, (w,b) in enumerate(self.layers):
            x = w @ x+b
            if i+1 < len(self.layers):x=np.maximum(x,0)
        x = x / self.temperature
        p = np.exp(x-x.max())
        return p/p.sum()

    def refine(self, prediction, rows, lo, hi, bin_hz):
        # Explicit corrections take precedence over the built-in family model.
        if prediction.learned and prediction.learned.get('weight',0) >= .3:
            return prediction
        probs = self.probabilities(rows,lo,hi,bin_hz)
        order = np.argsort(probs)[::-1]
        alternatives = [{'label':self.classes[int(i)],'confidence':float(probs[i])} for i in order[:5]]
        confidence=alternatives[0]['confidence']
        # Coarse remote spectra can erase the entire modulation silhouette.
        if bin_hz > 12000 and hi - lo + 1 < 4:
            confidence = min(confidence, .64)
        unknown=confidence<.65
        return replace(prediction,label='unknown' if unknown else alternatives[0]['label'],confidence=confidence,is_unknown=unknown,alternatives=alternatives,learned=None)
