from __future__ import annotations

import hashlib
import json
import math
from statistics import NormalDist
from typing import Iterable

VERSION = "CAPITAL_READINESS_LAB_V1"
AUTHORITY = "SHADOW_NO_AUTHORITY"
LABEL = "RESEARCH"


def num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def rnd(x, n=6):
    return None if x is None else round(float(x), n)


def canonical_json(v) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)


def sha(v) -> str:
    return hashlib.sha256(canonical_json(v).encode()).hexdigest()


def mean(xs: Iterable[float]):
    xs = [float(x) for x in xs]
    return None if not xs else sum(xs) / len(xs)


def sample_sd(xs: Iterable[float]):
    xs = [float(x) for x in xs]
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def ci95_mean(xs: Iterable[float]):
    xs = [float(x) for x in xs]
    n = len(xs)
    if n == 0:
        return None, None
    m = mean(xs)
    sd = sample_sd(xs)
    if sd is None:
        return m, m
    z = NormalDist().inv_cdf(0.975)
    half = z * sd / math.sqrt(n)
    return m - half, m + half


def envelope(status: str, why=None, **extra):
    out = {
        "label": LABEL,
        "authority": AUTHORITY,
        "version": VERSION,
        "status": status,
        "why": why,
    }
    out.update(extra)
    return out
