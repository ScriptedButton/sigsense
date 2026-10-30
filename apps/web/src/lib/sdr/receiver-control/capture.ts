export type PcmListener = (samples: Float32Array, sampleRate: number) => void;
export type IqListener = (i: Float32Array, q: Float32Array, sampleRate: number, centerHz: number) => void;

export function pcmMetrics(samples: Float32Array) {
  let energy = 0, peak = 0, clipped = 0, invalid = 0, sum = 0;
  for (const sample of samples) {
    if (!Number.isFinite(sample)) { invalid++; continue; }
    energy += sample * sample; sum += sample;
    peak = Math.max(peak, Math.abs(sample));
    if (Math.abs(sample) >= 1) clipped++;
  }
  return { rms: Math.sqrt(energy / Math.max(1, samples.length)), peak,
    dc: sum / Math.max(1, samples.length), clippedFraction: clipped / Math.max(1, samples.length), invalidSamples: invalid };
}

function base64(bytes: Uint8Array): string {
  let binary = "";
  for (let at = 0; at < bytes.length; at += 16_384) binary += String.fromCharCode(...bytes.subarray(at, at + 16_384));
  return btoa(binary);
}

export function encodeWav(samples: Float32Array, sampleRate: number): string {
  const bytes = new Uint8Array(44 + samples.length * 2), view = new DataView(bytes.buffer);
  const word = (offset: number, text: string) => { for (let i = 0; i < text.length; i++) bytes[offset + i] = text.charCodeAt(i); };
  word(0, "RIFF"); view.setUint32(4, bytes.length - 8, true); word(8, "WAVE");
  word(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  word(36, "data"); view.setUint32(40, samples.length * 2, true);
  samples.forEach((value, index) => {
    const sample = Number.isFinite(value) ? Math.max(-1, Math.min(1, value)) : 0;
    view.setInt16(44 + index * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
  });
  return base64(bytes);
}

function bounded(value: number, lo: number, hi: number, name: string): number {
  if (!Number.isFinite(value) || value < lo || value > hi) throw new Error(`${name} must be between ${lo} and ${hi}`);
  return Math.floor(value);
}

/** Collects new PCM only; samples stay untouched and the listener always detaches. */
export function captureAudioStream(
  subscribe: (listener: PcmListener) => () => void,
  durationMs = 1_000,
  maxSamples = 480_000,
) {
  durationMs = bounded(durationMs, 50, 10_000, "durationMs"); maxSamples = bounded(maxSamples, 256, 480_000, "maxSamples");
  return new Promise<{ format: "wav"; mimeType: "audio/wav"; data: string; sampleRate: number; samples: number; durationMs: number; metrics: ReturnType<typeof pcmMetrics>; capturedAt: string }>((resolve, reject) => {
    let rate = 0, count = 0, target = 0;
    const chunks: Float32Array[] = [];
    let unsubscribe = () => {};
    const timer = setTimeout(() => { unsubscribe(); reject(new Error("Audio capture timed out: no continuous audio is arriving from this receiver")); }, durationMs + 5_000);
    unsubscribe = subscribe((samples, sampleRate) => {
      if (rate && rate !== sampleRate) { clearTimeout(timer); unsubscribe(); reject(new Error("Audio sample rate changed during capture")); return; }
      rate = sampleRate; target = Math.min(maxSamples, Math.ceil(rate * durationMs / 1_000));
      if (!(rate > 0) || samples.length === 0) return;
      const part = samples.slice(0, target - count); chunks.push(part); count += part.length;
      if (count < target) return;
      clearTimeout(timer); unsubscribe();
      const pcm = new Float32Array(count); let at = 0;
      for (const chunk of chunks) { pcm.set(chunk, at); at += chunk.length; }
      resolve({ format: "wav", mimeType: "audio/wav", data: encodeWav(pcm, rate), sampleRate: rate,
        samples: count, durationMs: count / rate * 1_000, metrics: pcmMetrics(pcm), capturedAt: new Date().toISOString() });
    });
  });
}

/** Native-rate complex float32 LE, bounded to 2 MiB; no retuning or resampling. */
export function captureIqStream(subscribe: (listener: IqListener) => () => void, durationMs = 250, maxSamples = 262_144) {
  durationMs = bounded(durationMs, 10, 2_000, "durationMs"); maxSamples = bounded(maxSamples, 256, 262_144, "maxSamples");
  return new Promise<{ format: "complex-f32-le"; data: string; sampleRate: number; centerHz: number; samples: number; durationMs: number; iMetrics: ReturnType<typeof pcmMetrics>; qMetrics: ReturnType<typeof pcmMetrics>; capturedAt: string }>((resolve, reject) => {
    let rate = 0, center: number | null = null, count = 0;
    const iChunks: Float32Array[] = [], qChunks: Float32Array[] = [];
    let unsubscribe = () => {};
    const timer = setTimeout(() => { unsubscribe(); reject(new Error("IQ capture timed out: this source may provide only remote audio/spectrum")); }, durationMs + 5_000);
    unsubscribe = subscribe((i, q, sampleRate, centerHz) => {
      if (rate && (rate !== sampleRate || center !== centerHz)) {
        clearTimeout(timer); unsubscribe(); reject(new Error("Receiver retuned or changed sample rate during IQ capture")); return;
      }
      rate = sampleRate; center = centerHz;
      if (!(rate > 0)) return;
      const target = Math.min(maxSamples, Math.ceil(rate * durationMs / 1_000));
      const size = Math.min(i.length, q.length, target - count);
      iChunks.push(i.slice(0, size)); qChunks.push(q.slice(0, size)); count += size;
      if (count < target) return;
      clearTimeout(timer); unsubscribe();
      const I = new Float32Array(count), Q = new Float32Array(count); let at = 0;
      for (let k = 0; k < iChunks.length; k++) { I.set(iChunks[k], at); Q.set(qChunks[k], at); at += iChunks[k].length; }
      const bytes = new Uint8Array(count * 8), view = new DataView(bytes.buffer);
      for (let k = 0; k < count; k++) { view.setFloat32(k * 8, I[k], true); view.setFloat32(k * 8 + 4, Q[k], true); }
      resolve({ format: "complex-f32-le", data: base64(bytes), sampleRate: rate, centerHz, samples: count,
        durationMs: count / rate * 1_000, iMetrics: pcmMetrics(I), qMetrics: pcmMetrics(Q), capturedAt: new Date().toISOString() });
    });
  });
}
