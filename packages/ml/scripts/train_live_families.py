"""Train a receiver-scale family model from modulated IQ, not drawn silhouettes.

Separate emitter seeds for train/validation/test; scores are synthetic only.
Run with PYTHONPATH=apps/api:packages/ml .venv/bin/python this_file.py.
"""
from __future__ import annotations
import argparse,json,time
from pathlib import Path
import numpy as np
from scipy import signal
import torch
from torch import nn
from sigsense_ml.live_features import live_features,FAMILIES,FEATURE_VERSION


def capture(name,rng,n=32768,return_scene=False):
    rate=float(rng.choice([256000,512000,1024000,2048000,2400000]))
    if name=='WFM': rate=max(rate,512000)
    t=np.arange(n)/rate
    # Different multi-tone voice spectra and filtered noise every emitter.
    audio=np.zeros(n)
    for _ in range(int(rng.integers(3,12))):
        audio+=rng.uniform(.1,1)*np.sin(2*np.pi*rng.uniform(200,3000)*t+rng.uniform(0,6.28))
    filt=signal.butter(3,rng.uniform(1800,3500),fs=rate,output='sos')
    audio+=signal.sosfilt(filt,rng.normal(size=n))*rng.uniform(0,10)
    audio/=np.max(abs(audio))+1e-9
    bandwidth=10000
    if name=='AM':
        x=(1+rng.uniform(.25,.95)*audio).astype(complex);bandwidth=8000
    elif name in ('NFM','WFM'):
        deviation=rng.uniform(1800,5500) if name=='NFM' else rng.uniform(40000,85000)
        if name=='WFM':
            audio=.7*audio+.1*np.sin(2*np.pi*19000*t)+.2*audio*np.sin(2*np.pi*38000*t)
        x=np.exp(2j*np.pi*deviation*np.cumsum(audio)/rate)
        bandwidth=2*(deviation+3000)
    elif name=='SSB':
        x=signal.hilbert(audio)
        if rng.random()<.5: x=x.conj()
        bandwidth=4000
    elif name=='Carrier': x=np.ones(n,dtype=complex);bandwidth=1000
    elif name=='FSK':
        baud=rng.uniform(500,12000); sps=max(2,int(rate/baud))
        order=int(rng.choice([2,2,4])); symbols=np.repeat(rng.integers(0,order,int(np.ceil(n/sps))),sps)[:n]
        freq=(symbols-(order-1)/2)*rng.uniform(800,5000)
        if rng.random()<.5: freq=signal.sosfilt(signal.butter(2, min(baud*.8,rate*.4),fs=rate,output='sos'),freq)
        x=np.exp(2j*np.pi*np.cumsum(freq)/rate);bandwidth=order*5000+baud
    elif name=='Digital':
        baud=rng.uniform(4000,min(200000,rate*.25));sps=max(2,int(rate/baud))
        count=int(np.ceil(n/sps))
        if rng.random()<.5:
            order=int(rng.choice([2,4,8]));symbols=np.exp(2j*np.pi*rng.integers(0,order,count)/order)
        else:
            symbols=rng.choice([-3,-1,1,3],count)+1j*rng.choice([-3,-1,1,3],count)
        x=np.repeat(symbols,sps)[:n]
        x=signal.sosfilt(signal.butter(4,min(baud*.65,rate*.4),fs=rate,output='sos'),x)
        bandwidth=baud*1.5
    else:
        # Occupied noise bands, not just an empty receiver.
        bandwidth=rng.uniform(5000,rate*.3)
        x=rng.normal(size=n)+1j*rng.normal(size=n)
        x=signal.sosfilt(signal.butter(4,bandwidth/2,fs=rate,output='sos'),x)
    x=x/np.sqrt(np.mean(abs(x)**2)+1e-12)
    offset=rng.uniform(-.25,.25)*rate
    x*=np.exp(2j*np.pi*offset*t)
    # Key bursts, receiver gain, frequency drift and real periodogram noise.
    if rng.random()<.25:
        gate=np.ones(n); a=int(rng.integers(0,n//3)); b=int(rng.integers(2*n//3,n));gate[:a]=0;gate[b:]=0;x*=gate
    snr=rng.uniform(8,35)
    x+=(rng.normal(size=n)+1j*rng.normal(size=n))*10**(-snr/20)/np.sqrt(2)
    fft=int(rng.choice([256,512,1024,2048,4096]))
    blocks=x[:n//fft*fft].reshape(-1,fft)*np.hanning(fft)
    power=abs(np.fft.fftshift(np.fft.fft(blocks,axis=1),axes=1))**2 / np.hanning(fft).sum()**2
    # Random averaging and time cadence approximate different receiver displays.
    average=min(int(rng.choice([1,1,2,4])),len(power)//2)
    power=power[:len(power)//average*average].reshape(-1,average,fft).mean(axis=1)
    rows=10*np.log10(power+1e-20)+rng.uniform(-35,15)
    from sigsense_api.spectrum_bands import find_spectrum_bands
    bands,_,bin_hz=find_spectrum_bands(rows,100e6,rate,10)
    target=100e6+offset
    matching=[b for b in bands if b['low_hz']-bandwidth<=target<=b['high_hz']+bandwidth]
    if not matching:return None
    band=max(matching,key=lambda b:b['power_dbfs'])
    features = live_features(rows,band['_lo_bin'],band['_hi_bin'],bin_hz)
    return (features, rows, band, rate) if return_scene else features


def dataset(per_class,seed):
    rng=np.random.default_rng(seed); xs=[]; ys=[]
    for y,name in enumerate(FAMILIES):
        count=0
        for _ in range(per_class*4):
            x=capture(name,rng)
            if x is None:continue
            xs.append(x);ys.append(y);count+=1
            if count>=per_class:break
        print(name,count,flush=True)
    return np.stack(xs),np.array(ys)


def main():
    p=argparse.ArgumentParser();p.add_argument('--per-class',type=int,default=500);p.add_argument('--epochs',type=int,default=100);args=p.parse_args()
    torch.set_num_threads(2);torch.manual_seed(731)
    root=Path(__file__).resolve().parents[3]
    cache=root/'data/processed/live-families.npz';cache.parent.mkdir(parents=True,exist_ok=True)
    if cache.exists():d=np.load(cache); train=(d['tx'],d['ty']);val=(d['vx'],d['vy']);test=(d['ex'],d['ey'])
    else:
        train=dataset(args.per_class,731);val=dataset(max(60,args.per_class//5),1731);test=dataset(max(100,args.per_class//4),2731)
        np.savez_compressed(cache,tx=train[0],ty=train[1],vx=val[0],vy=val[1],ex=test[0],ey=test[1])
    mean=train[0].mean(0);std=np.maximum(train[0].std(0),.03)
    tx=torch.tensor((train[0]-mean)/std);ty=torch.tensor(train[1]);vx=torch.tensor((val[0]-mean)/std);vy=torch.tensor(val[1]);ex=torch.tensor((test[0]-mean)/std);ey=torch.tensor(test[1])
    model=nn.Sequential(nn.Linear(tx.shape[1],96),nn.ReLU(),nn.Linear(96,48),nn.ReLU(),nn.Linear(48,len(FAMILIES)))
    optim=torch.optim.AdamW(model.parameters(),lr=.002,weight_decay=.002)
    best=0;state=None
    for epoch in range(args.epochs):
        model.train()
        for ids in torch.randperm(len(tx)).split(128):
            loss=nn.functional.cross_entropy(model(tx[ids]),ty[ids],label_smoothing=.04);optim.zero_grad();loss.backward();optim.step()
        model.eval()
        with torch.no_grad():acc=float((model(vx).argmax(1)==vy).float().mean())
        if acc>best:best=acc;state={k:v.clone() for k,v in model.state_dict().items()}
        if epoch%20==0:print('epoch',epoch,'val',acc,flush=True)
    model.load_state_dict(state);model.eval()
    # Validation-only temperature selection; test remains untouched.
    with torch.no_grad():
        logits=model(vx); temp=min(np.linspace(.6,3,49),key=lambda t:float(nn.functional.cross_entropy(logits/float(t),vy)))
        probs=(model(ex)/float(temp)).softmax(1).numpy();pred=probs.argmax(1);conf=probs.max(1)
    confusion=np.zeros((len(FAMILIES),len(FAMILIES)),dtype=int)
    np.add.at(confusion,(test[1],pred),1)
    report={'scope':'synthetic IQ rendered at receiver FFT resolutions; not a captured RF benchmark','seed':2731,'classes':FAMILIES,'test_count':len(pred),'accuracy':float((pred==test[1]).mean()),'accepted_065':float((conf>=.65).mean()),'accepted_accuracy_065':float((pred[conf>=.65]==test[1][conf>=.65]).mean()),'confusion':confusion.tolist()}
    layers=[{'w':layer.weight.detach().numpy().round(7).tolist(),'b':layer.bias.detach().numpy().round(7).tolist()} for layer in model if isinstance(layer,nn.Linear)]
    artifact={'version':FEATURE_VERSION,'classes':FAMILIES,'mean':mean.tolist(),'std':std.tolist(),'layers':layers,'temperature':float(temp),'evaluation':report}
    dest=root/'models/live_families.json';dest.write_text(json.dumps(artifact,separators=(',',':')))
    (root/'docs/live-family-evaluation.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)
if __name__=='__main__':main()
