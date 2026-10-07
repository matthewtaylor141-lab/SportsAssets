from __future__ import annotations
import numpy as np
from .common import clamp01, logit, sigmoid

def devig_binary_prices(yes_price:float,no_price:float)->tuple[float,float]:
    if yes_price<=0 or no_price<=0: raise ValueError('prices must be positive')
    s=yes_price+no_price
    return yes_price/s,no_price/s

def devig_decimal_odds(*odds:float)->list[float]:
    imp=np.array([1/float(o) for o in odds],dtype=float)
    if np.any(imp<=0): raise ValueError('invalid odds')
    return list((imp/imp.sum()).astype(float))

def market_residual_probability(market_p:float,residual_logit:float,shrinkage:float=1.0)->float:
    return sigmoid(logit(market_p)+float(shrinkage)*float(residual_logit))

def closing_line_value(entry_price:float,closing_price:float,side:str='YES')->float:
    if side.upper()=='YES': return float(closing_price)-float(entry_price)
    if side.upper()=='NO': return float(entry_price)-float(closing_price)
    raise ValueError('side must be YES or NO')

def market_baseline_decision_probability(row)->float:
    for k in ('market_prior_p','sharp_prior_p','no_vig_p','closing_prior_p'):
        if k in row and row[k] is not None:
            return clamp01(row[k])
    raise KeyError('no market prior probability on row')
