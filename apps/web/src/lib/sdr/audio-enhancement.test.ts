import assert from "node:assert/strict";
import { test } from "node:test";
import { AudioEnhancer, loadVoiceIsolation, type AudioEnhancementSettings } from "./audio-enhancement";

function noise(n:number,level=.03){let seed=73;return Float32Array.from({length:n},()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return (seed/2**32-.5)*level*Math.sqrt(12);});}
function rms(x:Float32Array,start=0){let sum=0;for(let i=start;i<x.length;i++)sum+=x[i]**2;return Math.sqrt(sum/Math.max(1,x.length-start));}
function process(config:AudioEnhancementSettings,input:Float32Array,rate=48000,chunk=137){const e=new AudioEnhancer(()=>config),out=new Float32Array(input.length);for(let i=0;i<input.length;i+=chunk)out.set(e.process(input.subarray(i,i+chunk),rate),i);e.reset();return out;}

test("audio bypass is exact and does not mutate decoder input",()=>{
 const input=noise(2048),copy=input.slice(),e=new AudioEnhancer(()=>({noiseReduction:"off",voiceIsolation:false}));
 assert.equal(e.process(input,48000),input);assert.deepEqual(input,copy);
});
test("speech filter rejects rumble and high hiss while preserving the voice band at multiple rates",()=>{
 for(const rate of [8000,12000,48000]){
  for(const hz of [50,1000,rate===8000?3800:5000]){
   const input=Float32Array.from({length:rate},(_,i)=>.2*Math.sin(2*Math.PI*hz*i/rate)),copy=input.slice();
   const config:AudioEnhancementSettings={noiseReduction:"off",voiceIsolation:false,speechFilter:true};
   const a=process(config,input,rate,137),b=process(config,input,rate,4096);
   assert.deepEqual(a,b);assert.deepEqual(input,copy);
   const gain=20*Math.log10(rms(a,rate/2)/rms(input,rate/2));
   if(hz===50)assert.ok(gain < -18,`${rate} Hz rumble attenuation: ${gain}`);
   else if(hz===1000)assert.ok(gain > -1,`${rate} Hz wanted speech attenuation: ${gain}`);
   else assert.ok(gain < -8,`${rate} Hz high hiss attenuation: ${gain}`);
  }
 }
});
test("adaptive audio reduction attenuates stationary hiss and is independent of chunk boundaries",()=>{
 const input=noise(48000*2),copy=input.slice();const config:AudioEnhancementSettings={noiseReduction:"medium",voiceIsolation:false};
 const a=process(config,input,48000,137),b=process(config,input,48000,4096);
 assert.deepEqual(a,b);assert.deepEqual(input,copy);
 const reduction=20*Math.log10(rms(input,48000)/rms(a,48000));
 assert.ok(reduction>8,`hiss reduction only ${reduction.toFixed(1)} dB`);
});
test("hiss reduction retains structured voice-band energy after learning a quiet background",()=>{
 const rate=48000,input=noise(rate*2,.01);for(let j=rate/2;j<input.length;j++)input[j]+=.1*Math.sin(2*Math.PI*600*j/rate)+.05*Math.sin(2*Math.PI*1300*j/rate);
 const out=process({noiseReduction:"medium",voiceIsolation:false},input);
 assert.ok(rms(out,rate)>.05,"wanted voice-band energy was removed");
 assert.ok(out.every(Number.isFinite));
});
test("strong reduction does not erase sustained noise-like speech energy",()=>{
 const rate=48000,input=noise(rate*8,.01);
 const speech=noise(rate*7,.12);input.set(speech,rate);
 const out=process({noiseReduction:"high",voiceIsolation:false},input,rate,137);
 const inputLevel=rms(input,rate*2),outputLevel=rms(out,rate*2+512);
 const gain=outputLevel/inputLevel;
 assert.ok(gain>=.23,`noise-like voice collapsed to ${(gain*100).toFixed(1)}% amplitude`);
});
test("reducer contains invalid input and clips unsafe output",()=>{
 const input=noise(4096,.1);input[700]=Number.NaN;input[701]=Number.POSITIVE_INFINITY;input[2500]=4;
 const out=process({noiseReduction:"medium",voiceIsolation:false},input,48000,137);
 assert.ok(out.every((sample)=>Number.isFinite(sample)&&Math.abs(sample)<=1));
});
test("neural voice isolation loads and suppresses noise at native and remote PCM rates",async()=>{
 await loadVoiceIsolation();
 for(const rate of [8000,12000,44100,48000]){
  const input=noise(rate*2,.03),out=process({noiseReduction:"off",voiceIsolation:true},input,rate,241);
  assert.equal(out.length,input.length);assert.ok(out.every(Number.isFinite));
  const reduction=20*Math.log10(rms(input,rate)/Math.max(rms(out,rate),1e-12));
  assert.ok(reduction>8,`${rate} Hz neural noise attenuation only ${reduction.toFixed(1)} dB`);
 }
});
