"""Classification reports and validation-only temperature calibration."""

import torch
import torch.nn.functional as F


@torch.no_grad()
def collect_predictions(model, loader, device, amp=False):
    logits, targets = [], []
    model.eval()
    for x, y in loader:
        with torch.autocast(device_type=torch.device(device).type, dtype=torch.float16, enabled=amp):
            prediction = model(x.to(device, non_blocking=True))[0]
        logits.append(prediction.float().cpu())
        targets.append(y.cpu())
    if not targets:
        raise ValueError("Evaluation split is empty")
    return torch.cat(logits), torch.cat(targets)


def fit_temperature(logits, targets):
    # Bounded grid is deterministic, cheap, and cannot produce negative scales.
    temperatures = torch.logspace(-1, 1, 81)
    losses = [F.cross_entropy(logits / t, targets).item() for t in temperatures]
    return float(temperatures[min(range(len(losses)), key=losses.__getitem__)])


def classification_report(logits, targets, classes, temperature=1., threshold=.45):
    probs = (logits / temperature).softmax(1)
    confidence, predictions = probs.max(1)
    n = len(classes)
    confusion = torch.bincount(targets * n + predictions, minlength=n*n).reshape(n, n)
    tp = confusion.diag().float()
    support = confusion.sum(1)
    precision = tp / confusion.sum(0).clamp_min(1)
    recall = tp / support.clamp_min(1)
    f1 = 2 * precision * recall / (precision + recall).clamp_min(1e-12)
    present = support > 0
    accepted = confidence >= threshold
    ece = 0.
    for low in torch.arange(0., 1., .1):
        mask = (confidence > low) & (confidence <= low + .1)
        if mask.any():
            ece += float(mask.float().mean() * (confidence[mask].mean() - (predictions[mask] == targets[mask]).float().mean()).abs())
    return {
        "samples": len(targets), "accuracy": float((predictions == targets).float().mean()),
        "macro_f1": float(f1[present].mean()), "balanced_accuracy": float(recall[present].mean()),
        "negative_log_likelihood": float(F.cross_entropy(logits / temperature, targets)),
        "expected_calibration_error_10_bins": ece,
        "temperature": temperature, "acceptance_threshold": threshold,
        "coverage": float(accepted.float().mean()),
        "accepted_accuracy": float((predictions[accepted] == targets[accepted]).float().mean()) if accepted.any() else None,
        "per_class": {label: {"support": int(support[i]), "precision": float(precision[i]), "recall": float(recall[i]), "f1": float(f1[i])} for i, label in enumerate(classes)},
        "confusion_matrix": confusion.tolist(),
        "note": "Closed-set split metrics. Coverage is not unknown-signal detection performance.",
    }
