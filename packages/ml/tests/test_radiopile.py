import tempfile
from pathlib import Path
import unittest

import h5py
import numpy as np

from sigsense_ml.preprocess import iq_to_spectrogram
from sigsense_ml.radiopile import partition_groups, RadioPileSpectrogramDataset


class RadioPileTests(unittest.TestCase):
    def test_groups_are_atomic_and_reproducible(self):
        groups = np.repeat([f'capture{i}' for i in range(30)], 2)
        labels = np.repeat([i // 10 for i in range(30)], 2)
        a = partition_groups(groups, labels)
        b = partition_groups(groups, labels)
        np.testing.assert_array_equal(a, b)
        for group in set(groups):
            self.assertEqual(len(set(a[groups == group])), 1)
        for label in set(labels):
            self.assertEqual(set(a[labels == label]), {'train', 'val', 'test'})
        with self.assertRaises(ValueError):
            partition_groups(['one', 'two'], [0, 0])

    def test_native_hdf5_label_mapping_and_split_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'its_band.h5'
            rng = np.random.default_rng(42)
            with h5py.File(path, 'w') as f:
                f['data'] = rng.normal(size=(120, 2, 500)).astype(np.float32)
                f['labels'] = np.repeat([0, 1], 60)
                f.create_dataset('label_names', data=['LTE', '5G NR'], dtype=h5py.string_dtype())
                m = f.create_group('meta')
                m.create_dataset('file_path', data=['lte.mat'] * 60 + ['nr.mat'] * 60, dtype=h5py.string_dtype())
                m.create_dataset('recording_id', data=[str(i // 2) for i in range(120)], dtype=h5py.string_dtype())
            splits = {s: RadioPileSpectrogramDataset(path, s, samples_per_class=20) for s in ['train', 'val', 'test']}
            groups = {s: set(ds.group_ids) for s, ds in splits.items()}
            self.assertFalse(groups['train'] & groups['val'] or groups['train'] & groups['test'] or groups['val'] & groups['test'])
            for ds in splits.values():
                self.assertEqual(len({y for _, y in ds.items}), 2)
                self.assertTrue(np.isfinite(ds.items[0][0]).all())
                self.assertEqual(ds[0][0].shape, (1, 256, 256))

    def test_short_iq_has_real_temporal_structure(self):
        iq = np.random.default_rng(7).normal(size=128) + 1j * np.random.default_rng(8).normal(size=128)
        spec = iq_to_spectrogram(iq)
        self.assertGreater(float(spec.std(axis=1).mean()), .001)
        self.assertEqual(spec.shape, (256, 256))


if __name__ == '__main__':
    unittest.main()
