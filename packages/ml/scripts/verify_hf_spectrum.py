"""Independently check browser FFT/decimation replay against SciPy on the same IQ."""
import argparse
import json
from pathlib import Path
import numpy as np
from scipy import signal

root = Path(__file__).resolve().parents[3]
p = argparse.ArgumentParser()
p.add_argument('--report', type=Path, default=root/'data/processed/hf-waterfall/comparison.json')
a = p.parse_args()
r = json.loads(a.report.read_text())
raw = np.fromfile(r['fixture'], dtype=np.uint8).astype(np.float64)
iq = (raw[::2]-127.5)/128 + 1j*(raw[1::2]-127.5)/128
n = 8192
window = np.blackman(n).astype(np.float32)


def spectrum(x, averages):
    starts = np.arange(0, len(x)-n+1, n//2)[-averages:]
    blocks = np.stack([x[s:s+n] for s in starts]) * window
    power = np.abs(np.fft.fft(blocks, axis=1)/n)**2
    return 10*np.log10(np.maximum(power.mean(axis=0), 1e-30))

wide = spectrum(iq, r['continuousWide']['averagedFfts'])
ratio = int(r['sampleRate']/r['after']['sampleRate'])
length = 63*ratio
length += (length+1) % 2
kernel = signal.firwin(length, r['after']['sampleRate']*.45, fs=r['sampleRate'], window='hamming').astype(np.float32)
filtered = signal.upfirdn(kernel, iq, down=ratio)[:int(np.ceil(len(iq)/ratio))]
narrow = spectrum(filtered, r['after']['averagedFfts'])
freq = np.fft.fftfreq(n, 1/r['after']['sampleRate'])
passband = np.abs(freq) <= 50000
wide_error = float(np.max(np.abs(wide-np.array(r['spectra']['wide']))))
narrow_error = float(np.max(np.abs(narrow[passband]-np.array(r['spectra']['after'])[passband])))
assert wide_error < .01, wide_error
assert narrow_error < .05, narrow_error
summary = {k:v for k,v in r.items() if k not in ('spectra','fixture')}
summary['reference'] = {'method':'SciPy FIR decimation and NumPy overlapped periodogram averaging on exactly the same 8-bit IQ file', 'wide_max_error_db':wide_error, 'narrow_passband_max_error_db':narrow_error}
(root/'docs/hf-spectrum-validation.json').write_text(json.dumps(summary, indent=2)+'\n')
print(json.dumps(summary, indent=2))
