"""Compare live classifiers on identical held-out, physically modulated scenes."""
import argparse,json
from pathlib import Path
import numpy as np
from train_live_families import capture
from sigsense_ml.live_features import FAMILIES
from sigsense_api.inference import InferenceEngine
from sigsense_api.spectrum_bands import band_crop


def family(label):
    if label in FAMILIES:return label
    if label in ('USB','LSB'):return 'SSB'
    if label=='CW':return 'Carrier'
    if 'FSK' in label or label in ('MSK','GMSK'):return 'FSK'
    if label in ('unknown','Impulsive'):return label
    return 'Digital'


def main():
    p=argparse.ArgumentParser();p.add_argument('--per-class',type=int,default=12);p.add_argument('--seed',type=int,default=3731);a=p.parse_args()
    eng=InferenceEngine();rng=np.random.default_rng(a.seed);scores=[]
    for label in FAMILIES:
        old=new=accepted=correct=0
        for _ in range(a.per_class):
            scene=None
            while scene is None:scene=capture(label,rng,return_scene=True)
            _,rows,band,rate=scene
            spec,_=band_crop(rows,band['_lo_bin'],band['_hi_bin'],floor_db=float(np.percentile(rows,20)))
            prediction=eng.analyze_spectrogram(spec)
            refined=eng.live_families.refine(prediction,rows,band['_lo_bin'],band['_hi_bin'],rate/rows.shape[1])
            old+=family(prediction.label)==label;new+=family(refined.label)==label
            accepted+=not refined.is_unknown;correct+=(not refined.is_unknown and family(refined.label)==label)
        row={'class':label,'count':a.per_class,'cnn_correct':old,'live_correct':new,'live_accepted':accepted,'live_accepted_correct':correct};scores.append(row);print(row,flush=True)
    report={'scope':'synthetic IQ scenes; separate emitter seed; identical inputs, family-level comparison; misses/rejections count as wrong','seed':a.seed,'per_class':scores}
    for key in ('cnn_correct','live_correct','live_accepted','live_accepted_correct'):report[key]=sum(x[key] for x in scores)
    report['count']=len(FAMILIES)*a.per_class
    Path('docs/live-family-comparison.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
