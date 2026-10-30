/** Playback-only hiss reduction and neural speech isolation. No microphone access. */
import type { RnnoiseModule } from "@jitsi/rnnoise-wasm/dist/rnnoise-sync.js";

export type AudioNoiseReduction = "off" | "low" | "medium" | "high";
export type AudioEnhancementSettings = { noiseReduction: AudioNoiseReduction; voiceIsolation: boolean; speechFilter?: boolean };
const DEFAULTS: AudioEnhancementSettings = { noiseReduction: "off", voiceIsolation: false, speechFilter: false };
let settings = { ...DEFAULTS };
let voiceModule: RnnoiseModule | null = null;
let voiceLoading: Promise<void> | null = null;

export function getAudioEnhancementSettings(): AudioEnhancementSettings { return settings; }
export function setAudioEnhancementSettings(patch: Partial<AudioEnhancementSettings>): void {
  settings = { ...settings, ...patch };
  try { localStorage.setItem("sigsense.audio-enhancement", JSON.stringify(settings)); } catch { /* private mode */ }
}
export function restoreAudioEnhancementSettings(): AudioEnhancementSettings {
  try {
    const saved = JSON.parse(localStorage.getItem("sigsense.audio-enhancement") ?? "null");
    if (saved) settings = {
      noiseReduction: ["off","low","medium","high"].includes(saved.noiseReduction) ? saved.noiseReduction : "off",
      voiceIsolation: saved.voiceIsolation === true,
      speechFilter: saved.speechFilter === true,
    };
  } catch { /* storage unavailable */ }
  return settings;
}
export function loadVoiceIsolation(): Promise<void> {
  if (voiceModule) return Promise.resolve();
  if (!voiceLoading) voiceLoading = import("@jitsi/rnnoise-wasm/dist/rnnoise-sync.js")
    .then(async ({ default: create }) => { voiceModule = await create().ready; })
    .catch((error) => { voiceLoading = null; throw error; });
  return voiceLoading;
}

/** Small radix-2 FFT independent of relaxed-SIMD, including on remote-source Safari. */
function fft(re: Float64Array, im: Float64Array, inverse = false): void {
  const n = re.length;
  for (let i=1,j=0;i<n;i++) {
    let bit=n>>1; for(;j&bit;bit>>=1) j^=bit; j^=bit;
    if(i<j){[re[i],re[j]]=[re[j],re[i]];[im[i],im[j]]=[im[j],im[i]];}
  }
  for(let len=2;len<=n;len<<=1){
    const angle=(inverse?2:-2)*Math.PI/len, cr=Math.cos(angle),ci=Math.sin(angle);
    for(let start=0;start<n;start+=len){
      let wr=1,wi=0;
      for(let k=0;k<len/2;k++){
        const a=start+k,b=a+len/2, br=re[b]*wr-im[b]*wi,bi=re[b]*wi+im[b]*wr;
        re[b]=re[a]-br;im[b]=im[a]-bi;re[a]+=br;im[a]+=bi;
        const next=wr*cr-wi*ci;wi=wr*ci+wi*cr;wr=next;
      }
    }
  }
  if(inverse) for(let j=0;j<n;j++){re[j]/=n;im[j]/=n;}
}

/** 50% overlap/add Wiener-style suppression with a speech-protecting noise tracker. */
class HissReducer {
  private n = 512;
  private input = new Float64Array(512);
  private re = new Float64Array(512);
  private im = new Float64Array(512);
  private window = Float64Array.from({length:512},(_,j)=>Math.sqrt(.5-.5*Math.cos(2*Math.PI*j/512)));
  private smooth = new Float64Array(257);
  private noise = new Float64Array(257);
  private gain = new Float64Array(257).fill(1);
  private overlap = new Float64Array(256);
  private output = new Float64Array(1024);
  private inAt=0; private filled=0; private hop=0; private outAt=0; private outEnd=0; private initialized=false;
  constructor(private rate: number) {}
  process(samples: Float32Array, strength: AudioNoiseReduction): Float32Array {
    const out = new Float32Array(samples.length);
    for(let j=0;j<samples.length;j++){
      out[j]=this.outAt!==this.outEnd ? this.output[this.outAt++%1024] : 0;
      this.input[this.inAt]=samples[j];this.inAt=(this.inAt+1)%this.n;
      if(this.filled<this.n){if(++this.filled===this.n){this.frame(strength);this.hop=0;}}
      else if(++this.hop>=256){this.frame(strength);this.hop=0;}
    }
    return out;
  }
  private frame(strength: AudioNoiseReduction): void {
    for(let j=0;j<this.n;j++){
      const sample=this.input[(this.inAt+j)%this.n];
      this.re[j]=(Number.isFinite(sample)?sample:0)*this.window[j];this.im[j]=0;
    }
    fft(this.re,this.im);
    let arithmetic=0,logarithm=0,count=0;
    for(let k=0;k<=256;k++){
      const p=this.re[k]**2+this.im[k]**2+1e-18;
      this.smooth[k]=this.initialized ? .7*this.smooth[k]+.3*p : p;
      if(!this.initialized)this.noise[k]=p*.1;
      const hz=k*this.rate/this.n;
      if(hz>=200&&hz<=2800){arithmetic+=this.smooth[k];logarithm+=Math.log(this.smooth[k]);count++;}
    }
    const noiseLike=count>0&&Math.exp(logarithm/count)/(arithmetic/count+1e-18)>.35;
    // This is playback cleanup, not a speech separator. Deep floors made HF
    // consonants and fading speech vanish whenever their short-time spectrum
    // looked noise-like. Keep the maximum attenuation bounded; voice isolation
    // remains the explicit option for stronger speech-only suppression.
    const alpha=strength==="high"?1.45:strength==="medium"?1.2:.9;
    const floor=strength==="high"?.25:strength==="medium"?.38:.63;
    for(let k=0;k<=256;k++){
      // Adapt slowly enough that a fricative or a fade is not learned as the
      // new floor in a few hundred milliseconds. Falling noise still follows
      // immediately through the min() branch.
      if(noiseLike)this.noise[k]=.992*this.noise[k]+.008*this.smooth[k];
      else this.noise[k]=Math.min(this.noise[k]*1.00005,this.smooth[k]);
      const target=Math.max(floor,1-alpha*this.noise[k]/(this.smooth[k]+1e-18));
      this.gain[k]=.7*this.gain[k]+.3*target;
    }
    for(let k=0;k<this.n;k++){
      const b=k<=256?k:512-k;
      const g=(this.gain[Math.max(0,b-1)]+2*this.gain[b]+this.gain[Math.min(256,b+1)])/4;
      this.re[k]*=g;this.im[k]*=g;
    }
    fft(this.re,this.im,true);
    for(let j=0;j<256;j++){
      const sample=this.re[j]*this.window[j]+this.overlap[j];
      this.output[this.outEnd++%1024]=Math.max(-1,Math.min(1,sample));
      this.overlap[j]=this.re[j+256]*this.window[j+256];
    }
    this.initialized=true;
  }
}

/** RNNoise consumes 480 samples at 48 kHz; rate conversion retains stream phase. */
class SpeechIsolator {
  private state: number;
  private ptr: number;
  private at=0; private upPhase=0; private downPhase=0; private previous=0;
  private queue=new Float32Array(8192); private read=0; private write=0;
  constructor(private module: RnnoiseModule, private rate: number) {
    this.state=module._rnnoise_create(0);this.ptr=module._malloc(480*4);
    if(!this.state||!this.ptr)throw new Error("Voice isolation could not allocate audio buffers");
  }
  process(samples: Float32Array): Float32Array {
    const out=new Float32Array(samples.length),ratio=48000/this.rate;
    for(let j=0;j<samples.length;j++){
      out[j]=this.read!==this.write?this.queue[this.read++%this.queue.length]:0;
      const value=Math.max(-1,Math.min(1,samples[j]));
      this.upPhase+=ratio;
      while(this.upPhase>=1){
        this.upPhase--;
        const fraction=1-this.upPhase/ratio;
        this.module.HEAPF32[this.ptr/4+this.at++]=(this.previous+(value-this.previous)*fraction)*32768;
        if(this.at===480){
          this.module._rnnoise_process_frame(this.state,this.ptr,this.ptr);
          for(let k=0;k<480;k++){
            this.downPhase+=this.rate/48000;
            while(this.downPhase>=1){
              this.downPhase--;
              this.queue[this.write++%this.queue.length]=this.module.HEAPF32[this.ptr/4+k]/32768;
            }
          }
          this.at=0;
        }
      }
      this.previous=value;
    }
    return out;
  }
  dispose():void {this.module._rnnoise_destroy(this.state);this.module._free(this.ptr);}
}

/** Stateful Butterworth voice band: removes rumble and out-of-band hiss without a noise model. */
class SpeechBandFilter {
  private sections: Array<{ b0: number; b1: number; b2: number; a1: number; a2: number; z1: number; z2: number }>;
  constructor(rate: number) {
    this.sections = ([true, false] as const).map((highpass) => {
      const frequency = highpass ? Math.min(150, rate * .1) : Math.min(3000, rate * .4);
      const omega = 2 * Math.PI * frequency / rate, c = Math.cos(omega), alpha = Math.sin(omega) / Math.SQRT2;
      const a0 = 1 + alpha, b0 = (highpass ? 1 + c : 1 - c) / 2;
      return { b0: b0 / a0, b1: (highpass ? -(1 + c) : 1 - c) / a0, b2: b0 / a0,
        a1: -2 * c / a0, a2: (1 - alpha) / a0, z1: 0, z2: 0 };
    });
  }
  process(input: Float32Array): Float32Array {
    const output = new Float32Array(input.length);
    for (let i = 0; i < input.length; i++) {
      let x = Number.isFinite(input[i]) ? input[i] : 0;
      for (const s of this.sections) {
        const y = s.b0 * x + s.z1;
        s.z1 = s.b1 * x - s.a1 * y + s.z2;
        s.z2 = s.b2 * x - s.a2 * y;
        x = y;
      }
      output[i] = Math.max(-1, Math.min(1, x));
    }
    return output;
  }
}

/** Each playback stream owns its own channel history; controls are shared across players. */
export class AudioEnhancer {
  private key="";
  private hiss: (HissReducer|null)[]=[];
  private speech: (SpeechIsolator|null)[]=[];
  private band: (SpeechBandFilter|null)[]=[];
  constructor(private getSettings:()=>AudioEnhancementSettings=getAudioEnhancementSettings) {}
  process(samples:Float32Array,rate:number,channel=0):Float32Array {
    const config=this.getSettings();
    const key=`${rate}:${config.noiseReduction}:${config.voiceIsolation}:${Boolean(config.speechFilter)}:${Boolean(voiceModule)}`;
    if(key!==this.key){this.reset();this.key=key;}
    if(config.noiseReduction==="off"&&!config.voiceIsolation&&!config.speechFilter)return samples;
    let out=samples;
    if(config.speechFilter){
      this.band[channel]??=new SpeechBandFilter(rate);
      out=this.band[channel]!.process(out);
    }
    if(config.noiseReduction!=="off"){
      this.hiss[channel]??=new HissReducer(rate);
      out=this.hiss[channel]!.process(out,config.noiseReduction);
    }
    if(config.voiceIsolation&&voiceModule){
      this.speech[channel]??=new SpeechIsolator(voiceModule,rate);
      out=this.speech[channel]!.process(out);
    }
    return out;
  }
  reset():void {for(const s of this.speech)s?.dispose();this.speech=[];this.hiss=[];this.band=[];this.key="";}
}
