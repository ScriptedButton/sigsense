/** Replay exactly the same quantized IQ through old and new display pipelines.
 * pnpm --dir apps/web exec tsx scripts/hf-spectrum-compare.ts [--iq=/path/file.cu8] [--rate=1024000]
 * Input is interleaved unsigned 8-bit IQ, matching rtl_tcp. No receiver retuning.
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { Spectrum as SnapshotSpectrum } from "@jtarrio/signals/demod/spectrum.js";
import { StreamingSpectrum } from "../src/lib/sdr/streaming-spectrum";

const arg = (name: string, fallback: string) => process.argv.find((a)=>a.startsWith(`--${name}=`))?.split("=").slice(1).join("=") ?? fallback;
const rate=Number(arg("rate","1024000")), center=Number(arg("center","7100000"));
const outDir=resolve(process.cwd(),"../../data/processed/hf-waterfall");
mkdirSync(outDir,{recursive:true});
let file=arg("iq","");
if (!file) {
  file=resolve(outDir,"weak-carriers.cu8");
  const bytes=new Uint8Array(rate*2);
  let seed=73;
  const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return(seed+.5)/2**32;};
  for(let j=0;j<rate;j++){
    const r=Math.sqrt(-2*Math.log(random()))*.02/Math.SQRT2, phase=2*Math.PI*random();
    let i=r*Math.cos(phase),q=r*Math.sin(phase);
    for(const [hz,amplitude] of [[12000,.0004],[12062.5,.0004]] as const){i+=amplitude*Math.cos(2*Math.PI*hz*j/rate);q+=amplitude*Math.sin(2*Math.PI*hz*j/rate);}
    bytes[j*2]=Math.max(0,Math.min(255,Math.round(i*128+127.5)));
    bytes[j*2+1]=Math.max(0,Math.min(255,Math.round(q*128+127.5)));
  }
  writeFileSync(file,bytes);
}
const bytes=readFileSync(file);
if(bytes.length%2 || bytes.length<16384)throw new Error("Expected at least 8192 interleaved unsigned 8-bit complex samples");
const old=new SnapshotSpectrum(8192),wide=new StreamingSpectrum(8192),narrow=new StreamingSpectrum(8192);
wide.setSampleRate(rate);narrow.setSampleRate(rate);narrow.setView(0,100000);
let processingMs=0;
for(let start=0;start<bytes.length;start+=16384){
  const count=Math.min(8192,(bytes.length-start)/2), I=new Float32Array(count),Q=new Float32Array(count);
  for(let j=0;j<count;j++){I[j]=(bytes[start+j*2]-127.5)/128;Q[j]=(bytes[start+j*2+1]-127.5)/128;}
  const block={I,Q,frequency:center};
  old.receiveSamples(block);
  const t=performance.now();wide.receiveSamples(block);narrow.receiveSamples(block);processingMs+=performance.now()-t;
}
const before=new Float32Array(8192),averaged=new Float32Array(8192),after=new Float32Array(8192);
old.getSpectrum(before);wide.getSpectrum(averaged);narrow.getSpectrum(after);
function stats(spectrum:Float32Array,binHz:number){
  const quiet=Array.from(spectrum.subarray(Math.ceil(20000/binHz),Math.floor(40000/binHz)));
  const mean=quiet.reduce((a,b)=>a+b,0)/quiet.length;
  const sorted=quiet.slice().sort((a,b)=>a-b),floor=sorted[Math.floor(sorted.length/2)];
  return {noiseScatterDb:Math.sqrt(quiet.reduce((s,v)=>s+(v-mean)**2,0)/quiet.length),carrierContrastDb:spectrum[Math.round(12000/binHz)]-floor};
}
const info=narrow.getFrameInfo();
const report={scope:arg("iq","")?"user-supplied IQ":"generated, seeded weak carriers plus noise, quantized to rtl_tcp's 8-bit IQ; not a captured RF test",fixture:file,sampleRate:rate,centerHz:center,durationSeconds:bytes.length/2/rate,processingMs,
  before:{binHz:rate/8192,...stats(before,rate/8192)},continuousWide:{...wide.getFrameInfo(),...stats(averaged,rate/8192)},after:{...info,...stats(after,info.binHz)},
  spectra:{before:Array.from(before),wide:Array.from(averaged),after:Array.from(after)}};
writeFileSync(resolve(outDir,"comparison.json"),JSON.stringify(report));
// A stereo PCM WAV can be loaded in SDR software for an independent visual comparison.
const wav=Buffer.alloc(44+bytes.length*2);wav.write("RIFF",0);wav.writeUInt32LE(wav.length-8,4);wav.write("WAVEfmt ",8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(2,22);wav.writeUInt32LE(rate,24);wav.writeUInt32LE(rate*4,28);wav.writeUInt16LE(4,32);wav.writeUInt16LE(16,34);wav.write("data",36);wav.writeUInt32LE(bytes.length*2,40);
for(let j=0;j<bytes.length;j++)wav.writeInt16LE(Math.round((bytes[j]-127.5)/128*32767),44+j*2);
writeFileSync(resolve(outDir,"comparison-iq.wav"),new Uint8Array(wav));
const {spectra,...summary}=report;void spectra;console.log(JSON.stringify(summary,null,2));
