"""Shared constants and pure helpers for the profitability layer. No I/O.

Reuses the intelligence layer's pure helpers (intel.common: num, epoch,
jload, Out, book levels, exit walk, cost space) rather than duplicating
them.
"""
from __future__ import annotations

import hashlib
import json
import math
import random

from ..intel import common as IC

LABEL = "RESEARCH"
AUTHORITY = "SHADOW_NO_AUTHORITY"
DISCLOSURE = (
    "RESEARCH / SHADOW: computed from the existing records, persisted only to "
    "the append-only pos_* tables and displayed. No venue, order, sizing, "
    "limit, threshold or capital authority. PAPER, ACTUAL and COUNTERFACTUAL "
    "are never summed. Unmeasured values are null with a named reason, never "
    "zero.")
PAPER_ACCOUNT = IC.PAPER_ACCOUNT
BOOKS = ("PAPER", "ACTUAL")
DRAW_BUDGET = 300000
ALL_BOOKS = ("PAPER", "ACTUAL", "COUNTERFACTUAL")

# metric / capacity statuses
MEASURED = "MEASURED"
INSUFFICIENT = "INSUFFICIENT_SAMPLE"
UNAVAILABLE = "UNAVAILABLE"
UNPROVEN = "UNPROVEN"

num = IC.num
epoch = IC.epoch
jload = IC.jload
rnd = IC.rnd
Out = IC.Out
median = IC.median
mean = IC.mean
day_start = IC.day_start


def envelope(status, why=None, *, computed_at=None, data=None, **extra):
    """The read envelope every profitability endpoint answers with."""
    out = {"label": LABEL, "authority": AUTHORITY, "status": status,
           "why": why, "computed_at": computed_at, "data": data,
           "disclosure": DISCLOSURE}
    out.update(extra)
    return out


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                          .encode()).hexdigest()


def seed_of(obj) -> int:
    """A deterministic bootstrap seed from the inputs (reproducible)."""
    return int(sha(obj)[:12], 16)


def quantile(sorted_xs, q):
    """Linear-interpolated quantile of an already sorted list."""
    if not sorted_xs:
        return None
    if len(sorted_xs) == 1:
        return float(sorted_xs[0])
    pos = q * (len(sorted_xs) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_xs) - 1)
    frac = pos - lo
    return float(sorted_xs[lo] * (1 - frac) + sorted_xs[hi] * frac)


def bootstrap_ratio_ci(pairs, *, seed, resamples=1000, alpha=0.10):
    """Percentile bootstrap CI of sum(a)/sum(b) over (a, b) pairs. None when
    fewer than 2 pairs or a resample has a non-positive denominator only."""
    if len(pairs) < 2:
        return None, None
    rng = random.Random(seed)
    n = len(pairs)
    # bounded cost: at most DRAW_BUDGET draws, never fewer than 200 resamples
    resamples = max(200, min(resamples, DRAW_BUDGET // n))
    stats = []
    for _ in range(resamples):
        sa = sb = 0.0
        for _ in range(n):
            a, b = pairs[rng.randrange(n)]
            sa += a
            sb += b
        if sb > 0:
            stats.append(sa / sb)
    if len(stats) < resamples * 0.5:
        return None, None
    stats.sort()
    return quantile(stats, alpha / 2), quantile(stats, 1 - alpha / 2)


def max_drawdown(values) -> float:
    """Peak-to-trough fall of a cumulative path that starts at 0 (>= 0)."""
    peak = 0.0
    cum = 0.0
    dd = 0.0
    for v in values:
        cum += v
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd
