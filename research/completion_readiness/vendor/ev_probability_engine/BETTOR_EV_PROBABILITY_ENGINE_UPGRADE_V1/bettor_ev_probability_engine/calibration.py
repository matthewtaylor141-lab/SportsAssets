from __future__ import annotations
from dataclasses import dataclass
import numpy as np, pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from .common import clamp01

class BetaCalibrator:
    def __init__(self,c:float=100.0): self.model=LogisticRegression(C=c,solver='lbfgs'); self.fitted=False
    def _x(self,p):
        p=np.clip(np.asarray(p,dtype=float),1e-9,1-1e-9); return np.column_stack([np.log(p),np.log1p(-p)])
    def fit(self,p,y): self.model.fit(self._x(p),np.asarray(y,dtype=int)); self.fitted=True; return self
    def predict(self,p):
        if not self.fitted: raise RuntimeError('calibrator not fitted')
        return self.model.predict_proba(self._x(p))[:,1]

class IsotonicCalibrator:
    def __init__(self): self.model=IsotonicRegression(out_of_bounds='clip'); self.fitted=False
    def fit(self,p,y): self.model.fit(np.asarray(p,dtype=float),np.asarray(y,dtype=float)); self.fitted=True; return self
    def predict(self,p):
        if not self.fitted: raise RuntimeError('calibrator not fitted')
        return np.asarray(self.model.predict(np.asarray(p,dtype=float)),dtype=float)

@dataclass
class SegmentCalibrator:
    min_n:int=80
    method:str='beta'
    keys:tuple[str,...]=('sport','family','regime')
    global_cal:object|None=None
    calibrators:dict|None=None
    counts:dict|None=None
    def _new(self): return BetaCalibrator() if self.method=='beta' else IsotonicCalibrator()
    def fit(self,df:pd.DataFrame,p_col='raw_p',y_col='outcome'):
        self.global_cal=self._new().fit(df[p_col].values,df[y_col].values); self.calibrators={}; self.counts={}
        for depth in range(1,len(self.keys)+1):
            ks=self.keys[:depth]
            for key,g in df.groupby(list(ks),dropna=False):
                if not isinstance(key,tuple): key=(key,)
                self.counts[key]=len(g)
                if len(g)>=self.min_n and g[y_col].nunique()>1:
                    self.calibrators[key]=self._new().fit(g[p_col].values,g[y_col].values)
        return self
    def _best_key(self,row):
        for depth in range(len(self.keys),0,-1):
            key=tuple(row.get(k,'') for k in self.keys[:depth])
            if self.calibrators and key in self.calibrators: return key
        return ()
    def predict_row(self,row,p_col='raw_p'):
        key=self._best_key(row)
        cal=self.calibrators.get(key) if key else self.global_cal
        p=float(cal.predict([row[p_col]])[0])
        return clamp01(p), key if key else ('GLOBAL',)
    def predict_frame(self,df,p_col='raw_p'):
        ps=[]; seg=[]
        for _,r in df.iterrows():
            p,k=self.predict_row(r,p_col); ps.append(p); seg.append('/'.join(map(str,k)))
        return np.array(ps), seg
