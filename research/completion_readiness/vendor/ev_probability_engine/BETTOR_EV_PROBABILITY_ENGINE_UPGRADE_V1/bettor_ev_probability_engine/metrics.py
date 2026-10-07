from __future__ import annotations
import numpy as np, pandas as pd
from sklearn.metrics import brier_score_loss, log_loss

def expected_calibration_error(y,p,bins:int=10)->float:
    y=np.asarray(y,dtype=float); p=np.clip(np.asarray(p,dtype=float),1e-9,1-1e-9)
    if len(y)==0: return float('nan')
    edges=np.linspace(0,1,bins+1); out=0.0
    for lo,hi in zip(edges[:-1],edges[1:]):
        m=(p>=lo)&((p<hi) if hi<1 else (p<=hi))
        if m.any(): out += m.mean()*abs(y[m].mean()-p[m].mean())
    return float(out)

def probability_metrics(y,p)->dict:
    y=np.asarray(y,dtype=int); p=np.clip(np.asarray(p,dtype=float),1e-9,1-1e-9)
    return {'n':int(len(y)),'brier':float(brier_score_loss(y,p)),'log_loss':float(log_loss(y,p,labels=[0,1])),'ece_10':expected_calibration_error(y,p,10),'mean_p':float(np.mean(p)),'outcome_rate':float(np.mean(y))}

def model_delta_metrics(y,baseline_p,candidate_p)->dict:
    b=probability_metrics(y,baseline_p); c=probability_metrics(y,candidate_p)
    return {'baseline':b,'candidate':c,'delta_brier':c['brier']-b['brier'],'delta_log_loss':c['log_loss']-b['log_loss'],'delta_ece_10':c['ece_10']-b['ece_10'],'candidate_beats_baseline':(c['brier']<b['brier'] and c['log_loss']<b['log_loss'])}

def reliability_table(y,p,bins:int=10)->pd.DataFrame:
    y=np.asarray(y,dtype=float); p=np.clip(np.asarray(p,dtype=float),1e-9,1-1e-9)
    rows=[]; edges=np.linspace(0,1,bins+1)
    for i,(lo,hi) in enumerate(zip(edges[:-1],edges[1:])):
        m=(p>=lo)&((p<hi) if hi<1 else (p<=hi))
        rows.append({'bin':i,'lo':lo,'hi':hi,'n':int(m.sum()),'mean_p':float(p[m].mean()) if m.any() else None,'outcome_rate':float(y[m].mean()) if m.any() else None,'abs_error':float(abs(y[m].mean()-p[m].mean())) if m.any() else None})
    return pd.DataFrame(rows)

def net_ev_probability_edge(p,price,all_in_cost=0.0): return np.asarray(p,dtype=float)-np.asarray(price,dtype=float)-float(all_in_cost)
