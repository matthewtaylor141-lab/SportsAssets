import pandas as pd, numpy as np
from bettor_ev_probability_engine.engine import run_event_clustered_probability_receipt
rng=np.random.default_rng(9); rows=[]
for i in range(150):
    mp=float(rng.uniform(.25,.75)); true=np.clip(mp+rng.normal(0,.025),.03,.97); y=int(rng.binomial(1,true))
    for j in range(2):
        rows.append({'event_id':f'e{i}','event_time':pd.Timestamp('2026-01-01')+pd.Timedelta(days=i),'sport':'NFL','family':'ML','regime':'PRE','market_prior_p':mp,'bettor_raw_p':np.clip(mp+rng.normal(0,.02),.02,.98),'outcome':y,'entry_price':mp-.005,'realized_pnl':y-(mp-.005)})
df=pd.DataFrame(rows)
r=run_event_clustered_probability_receipt(df,min_train_events=50,test_events=20,min_segment_n=30)
print({k:r[k] for k in ['status','decision_rows','unique_events','verdict','reasons']})
print('Synthetic demo only - not profitability evidence.')
