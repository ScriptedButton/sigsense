#!/usr/bin/env python3
"""Compare frozen checkpoints on identical compact test examples and shared labels."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import torch
from torch.utils.data import DataLoader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sigsense_ml.classes import SIGNAL_CLASSES
from sigsense_ml.evaluation import collect_predictions, classification_report
from sigsense_ml.model import load_model
from train import make_split, dataset_labels, dataset_sources


def aligned_logits(logits, labels):
    aligned = torch.full((len(logits), len(SIGNAL_CLASSES)), -1e9)
    for i, label in enumerate(labels):
        aligned[:, SIGNAL_CLASSES.index(label)] = logits[:, i]
    return aligned


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True, help='Candidate directory containing run_config and checkpoint')
    parser.add_argument('--baseline', type=Path, action='append', required=True, help='Baseline checkpoint path; repeat for multiple comparisons')
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--batch-size', type=int, default=96)
    args = parser.parse_args()
    config = json.loads((args.candidate / 'run_config.json').read_text())
    for key in ['data_dir', 'external_data_dir', 'manifest', 'out_dir']:
        if config.get(key) is not None:
            config[key] = Path(config[key])
    dataset = make_split(argparse.Namespace(**config), 'test')
    targets = torch.tensor(dataset_labels(dataset))
    sources = dataset_sources(dataset)
    loader = DataLoader(dataset, batch_size=args.batch_size, pin_memory=True)
    candidate = load_model(args.candidate / 'signal_embedding.pt', device=args.device)
    raw, _ = collect_predictions(candidate, loader, args.device, amp=args.device.startswith('cuda'))
    candidate_logits = aligned_logits(raw, candidate.classes)
    results = {'scope': 'Same fresh test examples. Each baseline is evaluated only on labels supported by both checkpoints; all classifier outputs remain eligible predictions.', 'comparisons': []}
    for path in args.baseline:
        baseline = load_model(path, device=args.device)
        raw, _ = collect_predictions(baseline, loader, args.device, amp=args.device.startswith('cuda'))
        baseline_logits = aligned_logits(raw, baseline.classes)
        common = set(candidate.classes) & set(baseline.classes)
        allowed = torch.tensor([SIGNAL_CLASSES.index(label) for label in common])
        mask = torch.isin(targets, allowed)
        indices = torch.where(mask)[0]
        result = {'baseline': str(path), 'common_classes': sorted(common), 'samples': int(mask.sum()),
            'candidate': classification_report(candidate_logits[mask], targets[mask], SIGNAL_CLASSES, candidate.temperature),
            'baseline_metrics': classification_report(baseline_logits[mask], targets[mask], SIGNAL_CLASSES, baseline.temperature), 'by_source': {}}
        for source in sorted(set(sources)):
            selected = [int(i) for i in indices if sources[int(i)] == source]
            if not selected:
                continue
            result['by_source'][source] = {
                'candidate': classification_report(candidate_logits[selected], targets[selected], SIGNAL_CLASSES, candidate.temperature),
                'baseline': classification_report(baseline_logits[selected], targets[selected], SIGNAL_CLASSES, baseline.temperature)}
        results['comparisons'].append(result)
        print(f"{path}: {len(common)} shared labels, {int(mask.sum())} examples; candidate={result['candidate']['accuracy']:.3f} baseline={result['baseline_metrics']['accuracy']:.3f}", flush=True)
        del baseline
    (args.candidate / 'comparison.json').write_text(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
