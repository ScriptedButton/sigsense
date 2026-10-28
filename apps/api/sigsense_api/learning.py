"""Online learning from user corrections.

The CNN is a frozen feature extractor here. A correction stores the exact model
input (a 256x256 spectrogram) and its embedding; the decision layer then blends
a nearest-example vote over those user examples into the CNN's class
probabilities. Nothing is retrained, so a correction takes effect on the very
next prediction.

Matching uses the real examples only. Augmented copies (shifts, stretches,
noise) were tried and measured: they did not raise accuracy on held-out
signals and made unrelated signals match (see scripts/eval_feedback.py), so
they are not used.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np

# Cosine similarity at which a user example carries 50% of the vote against the
# CNN, and how sharply that weight rises around it. Chosen on held-out broadcast
# FM against 47 other classes (scripts/eval_feedback.py --grid): at this setting
# no signal the CNN already got right was flipped by 1, 3 or 5 corrections.
SIM_MIDPOINT = 0.72
SIM_SHARPNESS = 25.0
# A correction only matches inputs at least this close to it, relative to how
# close the *nearest other class* already is to that example. Where a corrected
# signal sits right next to another class, matching must be stricter or it
# drags that class's signals along (eval_feedback.py, "same-family": flips fell
# from 17.7% to 5.7% with no loss in hits).
COMPETITOR_MARGIN = 0.05
# User labels whose best similarity is within this of the top one are treated as
# tied, and the label with the newest correction wins the tie. This is how a
# later correction overrides an earlier, contradicting one on the same signal.
TIE_BAND = 0.03
# Sharpness of the vote between competing user labels.
LABEL_SHARPNESS = 30.0
# More corrections for a label earns it more authority over the CNN.
MAX_WEIGHT_BY_EXAMPLES = (0.0, 0.90, 0.95, 0.97)  # index = examples, capped


@dataclass
class Snapshot:
    """Immutable view of every prototype, swapped in atomically on change."""

    protos: np.ndarray  # (N, D) L2-normalised
    label_idx: np.ndarray  # (N,) index into labels
    example_idx: np.ndarray  # (N,) which user example each row came from
    newest: np.ndarray  # (L,) newest example index per label (higher = newer)
    shift: np.ndarray  # (N,) extra similarity this example must clear (see above)
    labels: list[str]
    examples_per_label: dict[str, int]
    # example index -> label the model said before the user corrected it
    wrong_labels: dict[int, str] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return int(self.protos.shape[0])


@dataclass
class Decision:
    labels: list[str]
    probs: np.ndarray
    learned: dict | None


class UserLearner:
    """Blends user-example evidence into CNN class probabilities."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snap: Snapshot | None = None

    def set_prototypes(
        self,
        rows: list[dict],
    ) -> None:
        """`rows`: one per user example with `label`, `embeddings` (1, D), and
        optionally `wrong_label` (what the model had said) and `competitor` (cosine
        to the nearest other-class prototype)."""
        if not rows:
            with self._lock:
                self._snap = None
            return
        labels: list[str] = []
        protos, label_idx, example_idx, shift = [], [], [], []
        per_label: dict[str, int] = {}
        wrong: dict[int, str] = {}
        for ex, row in enumerate(rows):
            label = row["label"]
            if label not in labels:
                labels.append(label)
            per_label[label] = per_label.get(label, 0) + 1
            emb = np.asarray(row["embeddings"], dtype=np.float32)
            emb = emb / np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9)
            protos.append(emb)
            label_idx.extend([labels.index(label)] * len(emb))
            example_idx.extend([ex] * len(emb))
            competitor = row.get("competitor")
            shift.extend(
                [
                    0.0
                    if competitor is None
                    else max(0.0, float(competitor) - COMPETITOR_MARGIN - SIM_MIDPOINT)
                ]
                * len(emb)
            )
            if row.get("wrong_label"):
                wrong[ex] = row["wrong_label"]
        snap = Snapshot(
            protos=np.vstack(protos).astype(np.float32),
            label_idx=np.asarray(label_idx, dtype=np.int32),
            example_idx=np.asarray(example_idx, dtype=np.int32),
            labels=labels,
            examples_per_label=per_label,
            wrong_labels=wrong,
            newest=np.asarray(
                [max(i for i, l in zip(example_idx, label_idx) if l == k) for k in range(len(labels))],
                dtype=np.int32,
            ),
            shift=np.asarray(shift, dtype=np.float32),
        )
        with self._lock:
            self._snap = snap

    @property
    def snapshot(self) -> Snapshot | None:
        return self._snap

    @property
    def custom_labels(self) -> list[str]:
        snap = self._snap
        return list(snap.labels) if snap else []

    def apply(self, probs: np.ndarray, classes: list[str], emb: np.ndarray) -> Decision:
        snap = self._snap
        if snap is None or snap.n == 0:
            return Decision(list(classes), probs, None)

        emb = np.asarray(emb, dtype=np.float32)
        emb = emb / max(float(np.linalg.norm(emb)), 1e-9)
        raw = snap.protos @ emb
        # The stricter threshold applies to look-alikes, not to the example
        # itself: it fades out as the input approaches an exact match.
        fade = np.clip((1.0 - raw) / 0.1, 0.0, 1.0)
        sims = raw - snap.shift * fade
        best = np.full(len(snap.labels), -1.0, dtype=np.float32)
        np.maximum.at(best, snap.label_idx, sims)
        top = int(np.argmax(best))
        s_top = float(best[top])

        labels = list(classes) + [l for l in snap.labels if l not in classes]
        base = np.zeros(len(labels), dtype=np.float32)
        base[: len(classes)] = probs

        gate = 1.0 / (1.0 + np.exp(-SIM_SHARPNESS * (s_top - SIM_MIDPOINT)))
        n_ex = snap.examples_per_label[snap.labels[top]]
        cap = MAX_WEIGHT_BY_EXAMPLES[min(n_ex, len(MAX_WEIGHT_BY_EXAMPLES) - 1)]
        w = float(gate * cap)

        # Vote among user labels: sharpened softmax of their best similarities.
        z = LABEL_SHARPNESS * (best - best.max())
        tied = np.flatnonzero(best >= best.max() - TIE_BAND)
        if len(tied) > 1:
            z[tied[np.argmax(snap.newest[tied])]] += 6.0  # newest correction wins a tie
        vote = np.exp(z)
        vote /= vote.sum()
        user_dist = np.zeros(len(labels), dtype=np.float32)
        for j, name in enumerate(snap.labels):
            user_dist[labels.index(name)] = vote[j]

        final = (1.0 - w) * base + w * user_dist

        # Hard negative: the example that matched best was a correction *of*
        # some label; that label should not also keep its CNN mass.
        best_proto = int(np.argmax(sims))
        wrong = snap.wrong_labels.get(int(snap.example_idx[best_proto]))
        if wrong and wrong in labels and wrong != snap.labels[top]:
            final[labels.index(wrong)] *= max(0.0, 1.0 - w) ** 2

        total = float(final.sum())
        if total <= 0:
            return Decision(list(classes), probs, None)
        final = final / total
        learned = None
        if w >= 0.05:
            learned = {
                "label": snap.labels[top],
                "examples": int(n_ex),
                "similarity": round(s_top, 4),
                "weight": round(w, 4),
            }
        return Decision(labels, final, learned)
