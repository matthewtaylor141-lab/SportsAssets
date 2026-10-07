from __future__ import annotations
import pandas as pd, numpy as np
from .calibration import SegmentCalibrator
from .metrics import model_delta_metrics, probability_metrics, net_ev_probability_edge
from .event_validation import chronological_event_splits, clustered_bootstrap_lcb
from .loss_attribution import segment_loss_contribution, kill_list

REQUIRED=['event_id','event_time','sport','family','regime','market_prior_p','bettor_raw_p','outcome','entry_price']

def validate_input(df:pd.DataFrame):
    missing=[c for c in REQUIRED if c not in df]
    if missing: raise ValueError('missing columns: '+','.join(missing))
    if df['event_id'].isna().any(): raise ValueError('event_id missing')
    for c in ['market_prior_p','bettor_raw_p','outcome','entry_price']:
        if df[c].isna().any(): raise ValueError(c+' missing')
    return True

def run_event_clustered_probability_receipt(df:pd.DataFrame,min_train_events=40,test_events=20,min_segment_n=80,all_in_cost_col=None)->dict:
    validate_input(df); df=df.sort_values(['event_time','event_id']).reset_index(drop=True).copy()
    folds=chronological_event_splits(df,min_train_events=min_train_events,test_events=test_events)
    records=[]
    for i,f in enumerate(folds):
        train=df.loc[f.train_idx].copy(); test=df.loc[f.test_idx].copy()
        cal=SegmentCalibrator(min_n=min_segment_n).fit(train.rename(columns={'bettor_raw_p':'raw_p'}),p_col='raw_p',y_col='outcome')
        p,seg=cal.predict_frame(test.rename(columns={'bettor_raw_p':'raw_p'}),p_col='raw_p')
        tmp=test.copy(); tmp['bettor_calibrated_p']=p; tmp['calibration_segment']=seg; tmp['fold']=i
        cost=tmp[all_in_cost_col].values if all_in_cost_col and all_in_cost_col in tmp else 0.0
        tmp['net_ev']=net_ev_probability_edge(tmp.bettor_calibrated_p.values,tmp.entry_price.values,cost)
        records.append(tmp)
    if not records: return {'status':'INSUFFICIENT_EVENT_HISTORY','folds':0,'verdict':'CASH'}
    oos=pd.concat(records,ignore_index=True)
    delta_raw=model_delta_metrics(oos.outcome,oos.market_prior_p,oos.bettor_raw_p)
    delta_cal=model_delta_metrics(oos.outcome,oos.market_prior_p,oos.bettor_calibrated_p)
    ev_lcb=clustered_bootstrap_lcb(oos.net_ev.values,oos.event_id.values,alpha=0.05,n_boot=500)
    kill=kill_list(oos.rename(columns={'bettor_calibrated_p':'bettor_p'}),['sport','family','regime'],min_events=10)
    seg=segment_loss_contribution(oos.rename(columns={'bettor_calibrated_p':'bettor_p'}),['sport','family','regime'])
    verdict='CASH'
    reasons=[]
    if not delta_cal['candidate_beats_baseline']: reasons.append('CALIBRATED_BETTOR_DOES_NOT_BEAT_MARKET_PRIOR')
    if delta_cal['candidate']['ece_10']>0.06: reasons.append('CALIBRATION_ERROR_ABOVE_LIMIT')
    if ev_lcb<=0: reasons.append('EVENT_CLUSTERED_NET_EV_LOWER_BOUND_NOT_POSITIVE')
    if len(kill): reasons.append('SEGMENTS_REQUIRE_KILL_OR_FALLBACK')
    if not reasons: verdict='SHADOW_CHALLENGER'
    return {'status':'OK','folds':len(folds),'decision_rows':int(len(oos)),'unique_events':int(oos.event_id.nunique()),'market_vs_raw':delta_raw,'market_vs_calibrated':delta_cal,'event_clustered_net_ev_lcb_5pct':float(ev_lcb),'verdict':verdict,'reasons':reasons,'segment_loss_table':seg.to_dict(orient='records'),'kill_list':kill.to_dict(orient='records'),'oos_records':oos.to_dict(orient='records')}
