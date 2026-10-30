"""Score playback enhancement against clean speech on the same added-noise mixture.

First generate the f32 files with apps/web/scripts/audio-enhancement-compare.ts.
The clean input is the RNNoise author's public demo sample, used for evaluation.
"""
import json
from pathlib import Path
import numpy as np
from scipy import signal

root = Path(__file__).resolve().parents[3]
p = root/'data/processed/audio-enhancement'
r = np.fromfile(p/'reference.f32', dtype='<f4').astype(float)
results = {}
for name in ['noisy', 'nr-medium', 'voice-isolated']:
    y = np.fromfile(p/(name+'.f32'), dtype='<f4').astype(float)
    corr = signal.correlate(y, r, mode='full', method='fft')
    lags = signal.correlation_lags(len(y), len(r))
    mask = (lags >= 0) & (lags <= 2000)
    delay = int(lags[mask][np.argmax(corr[mask])])
    a, b = r[48000:len(r)-48000-delay], y[48000+delay:len(r)-48000]
    gain = float(a@b/(a@a+1e-18))
    target = gain*a
    error = b-target
    score = 10*np.log10((target@target)/(error@error+1e-18))
    results[name] = dict(delay_ms=delay/48, speech_correlation=float(np.corrcoef(a, b)[0, 1]),
                         speech_gain=gain, si_sdr_db=float(score), tail_noise_rms=float(np.sqrt(np.mean(y[-24000:]**2))))
base = results['noisy']['tail_noise_rms']
for result in results.values():
    result['noise_attenuation_db'] = float(20*np.log10(base/max(result['tail_noise_rms'], 1e-12)))
report = dict(scope='8 seconds of speech from the RNNoise author demo, with seeded added white noise at 10 dB SNR and 1-second noise-only margins. Not a captured radio benchmark.',
              speech_source='https://jmvalin.ca/demo/rnnoise/samples/clean.opus', sample_rate=48000, results=results)
assert results['nr-medium']['speech_correlation'] > .95
assert results['voice-isolated']['speech_correlation'] > .95
assert results['voice-isolated']['si_sdr_db'] > results['noisy']['si_sdr_db']+2
(root/'docs/audio-enhancement-validation.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report, indent=2))
