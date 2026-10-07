from __future__ import annotations
import pandas as pd, numpy as np
from .metrics import probability_metrics, model_delta_metrics

def segment_loss_contribution(df:pd.DataFrame,segment_cols,prob_col='bettor_p',market_col='market_prior_p',y_col='outcome',pnl_col='realized_pnl'):
    rows=[]
    for key,g in df.groupby(segment_cols,dropna=False):
        if not isinstance(key,tuple): key=(key,)
        m=model_delta_metrics(g[y_col].values,g[market_col].values,g[prob_col].values)
        rows.append({**{c:v for c,v in zip(segment_cols,key)},'decision_rows':int(len(g)),'unique_events':int(g['event_id'].nunique()) if 'event_id' in g else None,'realized_pnl':float(g[pnl_col].sum()) if pnl_col in g else None,'outcome_variance':float((g[y_col]-g[prob_col]).sum()) if y_col in g else None,'delta_brier':m['delta_brier'],'delta_log_loss':m['delta_log_loss'],'delta_ece_10':m['delta_ece_10'],'bettor_beats_market':m['candidate_beats_baseline']})
    out=pd.DataFrame(rows)
    if 'realized_pnl' in out: out=out.sort_values('realized_pnl')
    return out

def kill_list(df,segment_cols,min_events:int=20):
    s=segment_loss_contribution(df,segment_cols)
    return s[(s.unique_events.fillna(0)>=min_events)&((s.delta_log_loss>0)|(s.delta_brier>0))].copy()
