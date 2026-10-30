/** Compare playback enhancement on local 48 kHz mono float32 speech plus seeded hiss. */
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { AudioEnhancer, loadVoiceIsolation, type AudioEnhancementSettings } from "../src/lib/sdr/audio-enhancement";

async function main(){
 const dir=resolve(process.cwd(),"../../data/processed/audio-enhancement"),raw=readFileSync(resolve(dir,"clean.f32"));
 const clean=new Float32Array(raw.buffer.slice(raw.byteOffset,raw.byteOffset+raw.byteLength));
 const rate=48000,input=new Float32Array(clean.length+2*rate),reference=new Float32Array(input.length);
 let peak=0;for(const x of clean)peak=Math.max(peak,Math.abs(x));
 for(let j=0;j<clean.length;j++)reference[j+rate]=clean[j]*.3/Math.max(peak,1e-9);
 let power=0;for(const x of reference)power+=x*x;const noiseRms=Math.sqrt(power/clean.length)/Math.sqrt(10);
 let seed=73;const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return(seed+.5)/2**32;};
 for(let j=0;j<input.length;j++)input[j]=reference[j]+Math.sqrt(-2*Math.log(random()))*Math.cos(2*Math.PI*random())*noiseRms;
 await loadVoiceIsolation();
 const configs:Record<string,AudioEnhancementSettings>={noisy:{noiseReduction:"off",voiceIsolation:false},"nr-medium":{noiseReduction:"medium",voiceIsolation:false},"voice-isolated":{noiseReduction:"off",voiceIsolation:true}};
 function save(name:string,pcm:Float32Array){
  writeFileSync(resolve(dir,name+".f32"),new Uint8Array(pcm.buffer));
  const wav=Buffer.alloc(44+pcm.length*2);wav.write("RIFF");wav.writeUInt32LE(wav.length-8,4);wav.write("WAVEfmt ",8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(rate,24);wav.writeUInt32LE(rate*2,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write("data",36);wav.writeUInt32LE(pcm.length*2,40);
  for(let j=0;j<pcm.length;j++)wav.writeInt16LE(Math.round(Math.max(-1,Math.min(1,pcm[j]))*32767),44+j*2);
  writeFileSync(resolve(dir,name+".wav"),new Uint8Array(wav));
 }
 save("reference",reference);
 for(const [name,config]of Object.entries(configs)){
  const e=new AudioEnhancer(()=>config),out=new Float32Array(input.length);const start=performance.now();
  for(let j=0;j<input.length;j+=997)out.set(e.process(input.subarray(j,j+997),rate),j);
  e.reset();save(name,out);console.log(name,Math.round(performance.now()-start),"ms");
 }
}
void main();
