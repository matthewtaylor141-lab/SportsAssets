from __future__ import annotations
from dataclasses import dataclass
import numpy as np, pandas as pd
@dataclass(frozen=True)
class EventFold:
    train_events:list[str]
    test_events:list[str]
    train_idx:list[int]
    test_idx:list[int]

def chronological_event_splits(df:pd.DataFrame,event_col='event_id',time_col='event_time',n_splits:int=5,min_train_events:int=40,test_events:int=20)->list[EventFold]:
    ev=df[[event_col,time_col]].drop_duplicates(event_col).sort_values(time_col).reset_index(drop=True)
    if len(ev)<min_train_events+test_events: return []
    out=[]; start=min_train_events
    while start+test_events<=len(ev) and len(out)<n_splits:
        tr=list(ev.iloc[:start][event_col].astype(str)); te=list(ev.iloc[start:start+test_events][event_col].astype(str))
        train_idx=df.index[df[event_col].astype(str).isin(tr)].tolist(); test_idx=df.index[df[event_col].astype(str).isin(te)].tolist()
        assert not set(tr)&set(te)
        out.append(EventFold(tr,te,train_idx,test_idx)); start+=test_events
    return out

def sacred_holdout_split(df,event_col='event_id',time_col='event_time',holdout_frac:float=0.2):
    ev=df[[event_col,time_col]].drop_duplicates(event_col).sort_values(time_col)
    n_hold=max(1,int(round(len(ev)*holdout_frac)))
    hold=set(ev.tail(n_hold)[event_col].astype(str)); train=set(ev.iloc[:-n_hold][event_col].astype(str))
    return df.index[df[event_col].astype(str).isin(train)].tolist(), df.index[df[event_col].astype(str).isin(hold)].tolist()

def clustered_bootstrap_lcb(values,cluster_ids,alpha=0.05,n_boot=1000,seed=7):
    vals=np.asarray(values,dtype=float); clusters=np.asarray(cluster_ids).astype(str); rng=np.random.default_rng(seed)
    uniq=np.array(sorted(set(clusters)))
    if len(uniq)<2: return float('-inf')
    means=[]
    for _ in range(n_boot):
        sample=rng.choice(uniq,size=len(uniq),replace=True)
        mask=np.isin(clusters,sample)
        means.append(float(vals[mask].mean()) if mask.any() else 0.0)
    return float(np.quantile(means,alpha))
