"""Download-free checks for the fusion backbone reimplementations (random tiny weights)."""
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

from sigsense_ml.fusion.ast_model import ASTEncoder
from sigsense_ml.fusion.cldnn import CLDNN
from sigsense_ml.fusion.loading import read_safetensors, read_tensor_checkpoint
from sigsense_ml.fusion.wavesfm import WavesFMEncoder


class FusionModelShapes(unittest.TestCase):
    def test_ast_shapes(self):
        m = ASTEncoder(dim=32, depth=2, heads=4, mlp=64, n_mels=32, frames=64, labels=5).eval()
        feat, logits = m(torch.randn(2, 64, 32))
        self.assertEqual(tuple(feat.shape), (2, 32))
        self.assertEqual(tuple(logits.shape), (2, 5))

    def test_wavesfm_both_paths(self):
        m = WavesFMEncoder(dim=32, depth=2, heads=4, iq_max_tokens=16).eval()
        self.assertEqual(tuple(m.forward_vision(torch.randn(2, 1, 224, 224)).shape), (2, 64))
        self.assertEqual(tuple(m.forward_iq(torch.randn(2, 2, 128)).shape), (2, 64))
        self.assertEqual(tuple(m.forward_iq(torch.randn(2, 2, 8)).shape), (2, 64))  # shorter than one segment

    def test_cldnn_shapes(self):
        feat, logits = CLDNN().eval()(torch.randn(3, 2, 1024))
        self.assertEqual((tuple(feat.shape), tuple(logits.shape)), ((3, 256), (3, 11)))


class SafeLoading(unittest.TestCase):
    def test_safetensors_roundtrip(self):
        import json
        data = np.arange(6, dtype=np.float32).reshape(2, 3)
        header = json.dumps({"w": {"dtype": "F32", "shape": [2, 3], "data_offsets": [0, 24]}}).encode()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "m.safetensors"
            path.write_bytes(struct.pack("<Q", len(header)) + header + data.tobytes())
            np.testing.assert_array_equal(read_safetensors(path)["w"].numpy(), data)

    def test_refuses_non_tensor_pickles(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad.pt"
            torch.save({"x": torch.zeros(1), "meta": {"a": 1}}, path)
            with self.assertRaises(ValueError):
                read_tensor_checkpoint(path)


if __name__ == "__main__":
    unittest.main()
