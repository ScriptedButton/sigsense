import importlib.util
import json
from pathlib import Path
import pickle
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import torch

from sigsense_ml.capture_dataset import CaptureDataset
from sigsense_ml.classes import SIGNAL_CLASSES
from sigsense_ml.evaluation import fit_temperature, classification_report
from sigsense_ml.external_datasets import RadioMLSpectrogramDataset, SyntheticSpectrogramDataset
from sigsense_ml.model import SignalEmbeddingNet, load_model
from sigsense_ml.synthetic import generate_iq

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'train.py'
spec = importlib.util.spec_from_file_location('train', SCRIPT)
train = importlib.util.module_from_spec(spec)
spec.loader.exec_module(train)


class TrainingTests(unittest.TestCase):
    def test_legacy_and_subset_checkpoint_labels(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'model.pt'
            model = SignalEmbeddingNet(num_classes=32)
            torch.save({'model_state': model.state_dict(), 'num_classes': 32, 'classes': SIGNAL_CLASSES[:32]}, p)
            loaded = load_model(p)
            self.assertEqual(loaded.classifier.out_features, 32)
            self.assertEqual(loaded.classes, SIGNAL_CLASSES[:32])
            model = SignalEmbeddingNet(num_classes=2)
            torch.save({'model_state': model.state_dict(), 'classes': ['WiFi', 'ASK4'], 'temperature': 2.}, p)
            loaded = load_model(p)
            self.assertEqual(loaded.classes, ['WiFi', 'ASK4'])
            self.assertEqual(loaded.temperature, 2.)
            with self.assertRaises(ValueError):
                load_model(p, num_classes=32)

    def test_capture_group_and_file_leakage_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            for name in ['a.npy', 'b.npy']:
                np.save(p / name, generate_iq('BPSK', n=8192, seed=3))
            rows = [{'path': 'a.npy', 'label': 'BPSK', 'split': 'train', 'group': 'session'},
                    {'path': 'b.npy', 'label': 'BPSK', 'split': 'test', 'group': 'session'}]
            manifest = p / 'captures.jsonl'
            manifest.write_text('\n'.join(map(json.dumps, rows)))
            with self.assertRaisesRegex(ValueError, 'leakage'):
                CaptureDataset(manifest, 'train')
            rows[1]['group'] = 'other'
            rows[1]['path'] = 'a.npy'
            manifest.write_text('\n'.join(map(json.dumps, rows)))
            with self.assertRaisesRegex(ValueError, 'leakage'):
                CaptureDataset(manifest, 'train')
            rows[1]['path'] = 'b.npy'
            manifest.write_text('\n'.join(map(json.dumps, rows)))
            dataset = CaptureDataset(manifest, 'train')
            self.assertEqual(dataset[0][0].shape, (1, 256, 256))

    def test_synthetic_reproducibility_and_no_split_overlap(self):
        a = SyntheticSpectrogramDataset(64, n_iq=512, seed=42, class_names=['BPSK'], augment=False)
        b = SyntheticSpectrogramDataset(16, n_iq=512, seed=1_000_042, class_names=['BPSK'], augment=False)
        c = SyntheticSpectrogramDataset(16, n_iq=512, seed=1_000_042, class_names=['BPSK'], augment=False)
        self.assertEqual({x.tobytes() for x, _ in b.items}, {x.tobytes() for x, _ in c.items})
        self.assertFalse({x.tobytes() for x, _ in a.items} & {x.tobytes() for x, _ in b.items})

    def test_radioml_splits_independent_of_augmentation(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            raw = {(name, 10): np.random.default_rng(i).normal(size=(100, 2, 512)).astype(np.float32)
                   for i, name in enumerate(['BPSK', 'QPSK'])}
            with (p / 'RML2016.10a_dict.pkl').open('wb') as f:
                pickle.dump(raw, f)
            # Track input IQ identity rather than augmentation-dependent output.
            def identify(iq):
                return np.array([float(iq.real.sum()), float(iq.imag.sum())], dtype=np.float32)
            with patch('sigsense_ml.external_datasets.iq_to_spectrogram', side_effect=identify):
                datasets = [RadioMLSpectrogramDataset(p, split=s, seed=42, augment=False) for s in ['train', 'val', 'test']]
                sets = [{x.tobytes() for x, _ in ds.items} for ds in datasets]
                self.assertEqual([len(s) for s in sets], [140, 30, 30])
                self.assertFalse(sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2])
                small = RadioMLSpectrogramDataset(p, split='train', seed=42, samples_per_class=2, augment=True)
                self.assertEqual(len(small), 4)

    def test_label_remap_and_proxy_exclusion(self):
        ds = SyntheticSpectrogramDataset(1, n_iq=512, class_names=['AIS', 'ASK4', 'BPSK'])
        classes = train.active_classes(ds)
        self.assertEqual(classes, ['BPSK', 'ASK4'])
        selected = train.SelectedClasses(ds, classes)
        self.assertEqual(set(selected.labels), {0, 1})
        self.assertEqual([selected[i][1] for i in range(len(selected))], selected.labels)

    def test_calibration_and_confusion(self):
        logits = torch.tensor([[10., 0.], [10., 0.], [0., 10.], [0., 10.]])
        y = torch.tensor([0, 1, 0, 1])
        t = fit_temperature(logits, y)
        before = classification_report(logits, y, ['a', 'b'])
        after = classification_report(logits, y, ['a', 'b'], t)
        self.assertLess(after['negative_log_likelihood'], before['negative_log_likelihood'])
        self.assertEqual(after['confusion_matrix'], [[1, 1], [1, 1]])
        self.assertEqual(after['accuracy'], .5)
        self.assertEqual(after['macro_f1'], .5)

    def test_neighbor_cannot_override_unknown(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'apps' / 'api'))
        from sigsense_api.inference import InferenceEngine
        engine = object.__new__(InferenceEngine)
        engine.device = torch.device('cpu')
        class Model:
            classes = ['BPSK', 'QPSK']
            temperature = 1.
            def __call__(self, x):
                return torch.zeros(1, 2), torch.ones(1, 128)
        class Library:
            def search(self, *args, **kwargs):
                return [{'label': 'WiFi', 'similarity': .999}]
        engine.model, engine.library, engine.threshold = Model(), Library(), .8
        result = engine.analyze_spectrogram(np.zeros((256, 256), dtype=np.float32))
        self.assertTrue(result.is_unknown)
        self.assertEqual(result.label, 'unknown')
        self.assertEqual(result.confidence, .5)


if __name__ == '__main__':
    unittest.main()
