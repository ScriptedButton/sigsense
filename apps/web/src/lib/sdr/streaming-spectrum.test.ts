import assert from "node:assert/strict";
import { test } from "node:test";
import { StreamingSpectrum } from "./streaming-spectrum";

function iq(rate: number, seconds: number, tones: [number, number][], noise = 0, seed = 73) {
  const n = Math.round(rate * seconds), I = new Float32Array(n), Q = new Float32Array(n);
  const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return (seed + .5) / 2 ** 32; };
  for (let j = 0; j < n; j++) {
    const r = Math.sqrt(-2 * Math.log(random())) * noise / Math.SQRT2;
    const phase = 2 * Math.PI * random();
    I[j] = r * Math.cos(phase); Q[j] = r * Math.sin(phase);
    for (const [hz, amplitude] of tones) {
      I[j] += amplitude * Math.cos(2 * Math.PI * hz * j / rate);
      Q[j] += amplitude * Math.sin(2 * Math.PI * hz * j / rate);
    }
  }
  return { I, Q };
}
function feed(s: StreamingSpectrum, source: {I: Float32Array; Q: Float32Array}, chunk = 7001, frequency = 7.1e6) {
  for (let j = 0; j < source.I.length; j += chunk) s.receiveSamples({
    I: source.I.subarray(j,j+chunk), Q: source.Q.subarray(j,j+chunk), frequency,
  });
  const out = new Float32Array(s.size); s.getSpectrum(out); return out;
}
function peak(out: Float32Array, rate: number) {
  let k = 0; for (let j = 1; j < out.length; j++) if (out[j] > out[k]) k = j;
  return (k <= out.length / 2 ? k : k - out.length) * rate / out.length;
}

test("all consecutive IQ contributes to 50% overlapping FFTs regardless of block boundaries", () => {
  const source = iq(128000,.5,[[8000,.2]],.01);
  const a = new StreamingSpectrum(1024), b = new StreamingSpectrum(1024);
  a.setSampleRate(128000); b.setSampleRate(128000);
  const x = feed(a,source,701), y = feed(b,source,8192);
  assert.equal(a.processedFfts, 1 + Math.floor((source.I.length - 1024) / 512));
  assert.equal(a.processedFfts,b.processedFfts);
  assert.ok(a.getFrameInfo().averagedFfts > 1);
  for (let j=0;j<x.length;j++) assert.ok(Math.abs(x[j]-y[j]) < 1e-5);
  assert.equal(peak(x,128000),8000);
});

test("DDC actually filters and decimates to 128 kS/s, retaining absolute frequency and rejecting aliases", () => {
  const s = new StreamingSpectrum(8192);
  s.setSampleRate(1024000); s.setView(200000,100000);
  const info = s.getFrameInfo();
  assert.equal(info.sampleRate,128000); assert.equal(info.binHz,15.625);
  // Strong blocker is outside the selected channel and would alias to -4 kHz
  // without the anti-alias filter; wanted signal is 12 kHz above DDC center.
  const center = info.centerOffsetHz;
  const out = feed(s,iq(1024000,.6,[[center+12000,.01],[center+124000,.2]],.0001));
  assert.equal(peak(out,info.sampleRate)+center,center+12000);
  const wanted = out[Math.round(12000/info.binHz)];
  const alias = out[out.length-Math.round(4000/info.binHz)];
  assert.ok(wanted-alias > 35,`alias rejection only ${wanted-alias} dB`);
});

test("retuning and changing rate discard old IQ and filter history", () => {
  const s = new StreamingSpectrum(1024); s.setSampleRate(128000);
  feed(s,iq(128000,.1,[[8000,.2]])); assert.ok(s.isReady());
  s.receiveSamples({I:new Float32Array(100),Q:new Float32Array(100),frequency:7.2e6});
  assert.equal(s.isReady(),false);
  s.setSampleRate(256000); assert.equal(s.isReady(),false);
  const out = feed(s,iq(256000,.1,[[-16000,.2]]),701,7.2e6);
  assert.equal(peak(out,256000),-16000);
});

test("a narrowed FFT separates two nearby HF carriers on the same noisy IQ", () => {
  const source=iq(1024000,.8,[[12000,.0004],[12062.5,.0004]],.02);
  const wide = new StreamingSpectrum(8192), narrow = new StreamingSpectrum(8192);
  narrow.setView(0,100000);
  const wideOut=feed(wide,source), out=feed(narrow,source);
  const info=narrow.getFrameInfo();
  const a=12000/info.binHz,b=12062.5/info.binHz;
  assert.ok(out[a] > out[a-1] && out[a] > out[a+1]);
  assert.ok(out[b] > out[b-1] && out[b] > out[b+1]);
  const noise=Array.from(out.subarray(1300,1700)).sort((a,b)=>a-b)[200];
  const wideNoise=Array.from(wideOut.subarray(300,700)).sort((a,b)=>a-b)[200];
  const wideSnr=wideOut[Math.round(12000/125)]-wideNoise;
  const narrowSnr=out[a]-noise;
  assert.ok(narrowSnr-wideSnr > 5,`only ${narrowSnr-wideSnr} dB carrier contrast gain`);
});
