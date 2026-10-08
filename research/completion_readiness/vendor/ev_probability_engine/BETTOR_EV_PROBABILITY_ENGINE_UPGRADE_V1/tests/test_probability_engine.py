import numpy as np, pandas as pd
from bettor_ev_probability_engine.market_priors import devig_binary_prices, market_residual_probability, closing_line_value
from bettor_ev_probability_engine.metrics import probability_metrics, model_delta_metrics, reliability_table
from bettor_ev_probability_engine.calibration import BetaCalibrator, SegmentCalibrator
from bettor_ev_probability_engine.event_validation import chronological_event_splits, sacred_holdout_split, clustered_bootstrap_lcb
from bettor_ev_probability_engine.loss_attribution import segment_loss_contribution, kill_list
from bettor_ev_probability_engine.engine import run_event_clustered_probability_receipt

def synthetic(n_events=140, rows_per_event=2, bettor_bad=False):
    rng=np.random.default_rng(4); rows=[]
    for i in range(n_events):
        mp=float(rng.uniform(.25,.75)); true=np.clip(mp+rng.normal(0,.03),.03,.97)
        y=int(rng.binomial(1,true))
        for j in range(rows_per_event):
            raw=np.clip(mp + ((-.12 if y else .12) if bettor_bad else rng.normal(0,.02)),.02,.98)
            rows.append({'event_id':f'e{i}','event_time':pd.Timestamp('2026-01-01')+pd.Timedelta(days=i),'sport':'NFL' if i%2 else 'NBA','family':'ML','regime':'PRE','market_prior_p':mp,'bettor_raw_p':raw,'outcome':y,'entry_price':mp-.01,'realized_pnl':y-(mp-.01)})
    return pd.DataFrame(rows)

def test_devig_binary_prices():
    a,b=devig_binary_prices(.52,.53); assert abs(a+b-1)<1e-12

def test_market_residual_probability_moves_from_prior():
    assert market_residual_probability(.5,.4,.5)>.5

def test_clv_yes_no_direction():
    assert closing_line_value(.45,.50,'YES')>0 and closing_line_value(.45,.40,'NO')>0

def test_probability_metrics_valid():
    m=probability_metrics([1,0,1],[.8,.3,.6]); assert 0<=m['brier']<=1 and m['n']==3

def test_delta_metrics_baseline():
    d=model_delta_metrics([1,0,1],[.7,.2,.7],[.6,.4,.6]); assert 'delta_log_loss' in d

def test_beta_calibrator_outputs_probabilities():
    df=synthetic(); cal=BetaCalibrator().fit(df.market_prior_p,df.outcome); p=cal.predict(df.market_prior_p[:5]); assert np.all((p>0)&(p<1))

def test_segment_calibrator_falls_back():
    df=synthetic(60); sc=SegmentCalibrator(min_n=999).fit(df.rename(columns={'bettor_raw_p':'raw_p'}),p_col='raw_p',y_col='outcome'); p,seg=sc.predict_frame(df.rename(columns={'bettor_raw_p':'raw_p'})); assert len(p)==len(df) and set(seg)=={'GLOBAL'}

def test_event_splits_no_overlap():
    df=synthetic(100); folds=chronological_event_splits(df,min_train_events=40,test_events=15); assert folds
    for f in folds: assert not set(f.train_events)&set(f.test_events)

def test_sacred_holdout_event_disjoint():
    df=synthetic(50); tr,ho=sacred_holdout_split(df); assert set(df.loc[tr].event_id).isdisjoint(set(df.loc[ho].event_id))

def test_cluster_lcb_positive_for_strong_values():
    vals=[.1]*50; c=[f'e{i}' for i in range(50)]; assert clustered_bootstrap_lcb(vals,c)>0

def test_loss_contribution_and_kill_list():
    df=synthetic(60,bettor_bad=True); df['bettor_p']=df.bettor_raw_p
    seg=segment_loss_contribution(df,['sport','family','regime']); assert len(seg)>0
    kl=kill_list(df,['sport','family','regime'],min_events=10); assert len(kl)>0

def test_engine_receipt_cash_when_bettor_worse_than_market():
    df=synthetic(120,bettor_bad=True)
    r=run_event_clustered_probability_receipt(df,min_train_events=40,test_events=20,min_segment_n=20)
    assert r['status']=='OK' and r['verdict']=='CASH'
    assert r['reasons']

def test_engine_reports_rows_and_unique_events():
    df=synthetic(120,bettor_bad=False)
    r=run_event_clustered_probability_receipt(df,min_train_events=40,test_events=20,min_segment_n=20)
    assert r['decision_rows']>r['unique_events']
    assert 'segment_loss_table' in r and 'market_vs_calibrated' in r

def test_reliability_table_bins():
    tab=reliability_table([1,0,1],[.2,.4,.9],bins=5); assert len(tab)==5
