from __future__ import annotations
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from .common import logit, sigmoid, clamp01

class MarketResidualLogistic:
    """Learns only a residual correction around the market prior."""
    def __init__(self,c:float=0.05,max_iter:int=1000): self.model=LogisticRegression(C=c,solver='lbfgs',max_iter=max_iter); self.fitted=False
    def _x(self,market_p,features):
        m=np.array([logit(x) for x in np.asarray(market_p,dtype=float)]).reshape(-1,1)
        f=np.asarray(features,dtype=float)
        if f.ndim==1: f=f.reshape(-1,1)
        return np.hstack([m,f])
    def fit(self,market_p,features,y): self.model.fit(self._x(market_p,features),np.asarray(y,dtype=int)); self.fitted=True; return self
    def predict_proba(self,market_p,features):
        if not self.fitted: raise RuntimeError('model not fitted')
        return np.clip(self.model.predict_proba(self._x(market_p,features))[:,1],1e-9,1-1e-9)

def shrink_to_market(candidate_p,market_p,evidence_weight:float):
    w=max(0.0,min(1.0,float(evidence_weight)))
    return np.array([sigmoid((1-w)*logit(m)+w*logit(c)) for c,m in zip(candidate_p,market_p)])
