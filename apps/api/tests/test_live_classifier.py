"""Live family inference, raw IQ transport, and conservative family labels."""
import asyncio
import base64
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
from fastapi import HTTPException
from sigsense_api.inference import AnalyzeResult
from sigsense_api.live_classifier import LiveFamilyClassifier
from sigsense_api import main
from sigsense_ml.live_features import live_features

ROOT=Path(__file__).resolve().parents[3]


class LiveClassifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model=LiveFamilyClassifier(ROOT/'models/live_families.json')

    def scene(self):
        rng=np.random.default_rng(73)
        rows=(-90+rng.normal(size=(24,2048))).astype(np.float32)
        rows[:,1100]+=35
        return rows

    def prediction(self):
        return AnalyzeResult('LoRa',.99,False,[],[],{},[],learned=None)

    def test_gain_invariance_and_finite_probabilities(self):
        rows=self.scene()
        a=self.model.probabilities(rows,1099,1101,500)
        b=self.model.probabilities(rows+35,1099,1101,500)
        np.testing.assert_allclose(a,b,atol=1e-5)
        self.assertAlmostEqual(float(a.sum()),1,places=5)
        self.assertTrue(np.isfinite(a).all())
        self.assertTrue(np.isfinite(live_features(np.full((8,256),-200),120,124,1000)).all())

    def test_carrier_does_not_inherit_spurious_protocol_label(self):
        p=self.model.refine(self.prediction(),self.scene(),1099,1101,500)
        self.assertEqual(p.label,'Carrier')
        self.assertGreater(p.confidence,.65)

    def test_unresolved_remote_bins_do_not_claim_a_precise_label(self):
        p=self.model.refine(self.prediction(),self.scene(),1100,1100,30000)
        self.assertTrue(p.is_unknown)
        self.assertEqual(p.label,'unknown')

    def test_corrections_outrank_the_family_model(self):
        p=self.prediction();p.learned={'weight':.8,'examples':3}
        self.assertIs(self.model.refine(p,self.scene(),1099,1101,500),p)

    def test_iq_endpoint_decodes_and_keeps_session_captures(self):
        rng=np.random.default_rng(44)
        pairs=rng.integers(-8000,8000,(8192,2),dtype=np.int16)
        body=main.IqBandsRequest(session='iq-test',center_freq_hz=100e6,sample_rate_hz=1024000,data=base64.b64encode(pairs.astype('<i2').tobytes()).decode())
        with patch.object(main,'get_engine',return_value=object()), patch.object(main,'analyze_spectrum_rows',return_value={'tracks':[]}) as analyze:
            result=asyncio.run(main.analyze_iq_bands(body))
        self.assertIn('raw receiver IQ',result['approximation'])
        args=analyze.call_args.args
        self.assertEqual(args[2].shape,(2,4096))
        self.assertTrue(np.isfinite(args[2]).all())
        self.assertIs(args[1],main.spectrum_sessions.get('iq-test'))
        for bad in ('invalid!',base64.b64encode(b'\x00'*15).decode()):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(main.analyze_iq_bands(body.model_copy(update={'data':bad})))
            self.assertEqual(caught.exception.status_code,400)

if __name__=='__main__':unittest.main()
