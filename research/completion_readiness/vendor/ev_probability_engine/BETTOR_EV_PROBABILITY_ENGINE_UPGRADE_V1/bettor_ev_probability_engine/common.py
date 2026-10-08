from __future__ import annotations
import hashlib, json, math
from dataclasses import asdict, is_dataclass
from pathlib import Path
EPS=1e-12

def clamp01(x,eps=EPS): return min(1-eps,max(eps,float(x)))
def logit(p):
    p=clamp01(p); return math.log(p/(1-p))
def sigmoid(x):
    if x>=0:
        z=math.exp(-x); return 1/(1+z)
    z=math.exp(x); return z/(1+z)
def canonical_json(o):
    if is_dataclass(o): o=asdict(o)
    return json.dumps(o,sort_keys=True,separators=(",",":"),default=str)
def sha256_bytes(b:bytes)->str: return hashlib.sha256(b).hexdigest()
def fingerprint(o)->str: return sha256_bytes(canonical_json(o).encode())
def write_json(path,obj):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,indent=2,sort_keys=True,default=str)+"\n")
