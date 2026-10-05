"""Shared constants and pure helpers of the profitability OS view. No I/O.

SECTION STATUSES (the page contract):
  OK            the component computed something from recorded rows
  EMPTY         its inputs were read and hold nothing to compute from (why)
  UNAVAILABLE   an input could not be read, or the computation failed (why)
An unread input is UNAVAILABLE, never zero. A metric that the rows cannot
support is null with a named reason.
"""
from __future__ import annotations

import math
import random

from ..profitability import common as PC

OK, EMPTY, UNAVAILABLE = "OK", "EMPTY", "UNAVAILABLE"
MEASURED = PC.MEASURED
INSUFFICIENT = PC.INSUFFICIENT
LABEL = PC.LABEL
AUTHORITY = PC.AUTHORITY
EXISTS, BUILT = "EXISTS", "BUILT"
PAPER_ACCOUNT = PC.PAPER_ACCOUNT
SLEEVES = PC.SLEEVES
INVESTMENT = PC.INVESTMENT
HOUR = 3600.0
DAY = 86400.0

#: why-codes the view names (classified in refusal_taxonomy_table)
R_INPUT_NOT_READ = "POS_OS_INPUT_NOT_READ"
R_NO_ROWS = "POS_OS_NO_RECORDED_ROWS_IN_WINDOW"
R_COMPONENT_RAISED = "POS_OS_COMPONENT_RAISED"
R_BELOW_MIN_SAMPLE = "POS_OS_BELOW_MINIMUM_SAMPLE"
R_NO_MARK_AFTER_FILL = "POS_OS_NO_RECORDED_MARK_AT_HORIZON"
R_NO_MID_AT_FILL = "POS_OS_NO_RECORDED_MID_AT_FILL"

num = PC.num
rnd = PC.rnd
mean = PC.mean
median = PC.median
quantile = PC.quantile
seed_of = PC.seed_of
jload = PC.jload
epoch = PC.epoch
sleeve_of = PC.sleeve_of


def section(status, why=None, *, audit, data=None, sources=(), **extra):
    out = {"status": status, "why": why, "audit": audit,
           "sources": list(sources), "data": data}
    out.update(extra)
    return out


def missing(inputs: dict, names) -> list:
    """Names of inputs that were not read (None)."""
    return [n for n in names if inputs.get(n) is None]


def unread(names, inputs, *, audit, sources=()):
    """The UNAVAILABLE section for inputs that could not be read."""
    errs = inputs.get("_errors") or {}
    return section(UNAVAILABLE, "%s:%s" % (R_INPUT_NOT_READ, ",".join(
        "%s(%s)" % (n, errs.get(n, "NOT_READ")) for n in names)),
        audit=audit, sources=sources)


def bootstrap_mean_ci(xs, *, seed, resamples=1000, alpha=0.10):
    """Percentile bootstrap CI of the mean. (None, None) below 2 values."""
    xs = [float(x) for x in xs if x is not None]
    n = len(xs)
    if n < 2:
        return None, None
    rng = random.Random(seed)
    resamples = max(200, min(resamples, PC.DRAW_BUDGET // n))
    stats = sorted(sum(xs[rng.randrange(n)] for _ in range(n)) / n
                   for _ in range(resamples))
    return quantile(stats, alpha / 2), quantile(stats, 1 - alpha / 2)


def bootstrap_diff_ci(a, b, *, seed, resamples=1000, alpha=0.10):
    """Percentile bootstrap CI of mean(a) - mean(b), independent samples."""
    a = [float(x) for x in a if x is not None]
    b = [float(x) for x in b if x is not None]
    if len(a) < 2 or len(b) < 2:
        return None, None
    rng = random.Random(seed)
    resamples = max(200, min(resamples, PC.DRAW_BUDGET // (len(a) + len(b))))
    stats = []
    for _ in range(resamples):
        ma = sum(a[rng.randrange(len(a))] for _ in a) / len(a)
        mb = sum(b[rng.randrange(len(b))] for _ in b) / len(b)
        stats.append(ma - mb)
    stats.sort()
    return quantile(stats, alpha / 2), quantile(stats, 1 - alpha / 2)


bootstrap_ratio_ci = PC.bootstrap_ratio_ci


def share(n, d):
    return None if not d else rnd(n / d)


def psi(base, recent, *, bins=(0.0, 0.2, 0.35, 0.45, 0.55, 0.65, 0.8, 1.0)):
    """Population stability index of `recent` against `base` over fixed
    probability bins (a 1e-4 floor per bin)."""
    def hist(xs):
        h = [0] * (len(bins) - 1)
        for x in xs:
            for i in range(len(bins) - 1):
                last = i == len(bins) - 2
                if bins[i] <= x < bins[i + 1] or (last and x == bins[-1]):
                    h[i] += 1
                    break
        n = float(sum(h)) or 1.0
        return [max(1e-4, c / n) for c in h]
    hb, hr = hist(base), hist(recent)
    return sum((r - b) * math.log(r / b) for b, r in zip(hb, hr))


def mid_of(bids, offers, holding_side):
    """The holding side's mid from a recorded book (LONG-side wire prices):
    LONG = (best bid + best offer) / 2; SHORT = 1 - that. None when either
    side is empty or unreadable."""
    from ..intel import common as IC
    try:
        v = IC.book_view(jload(bids), jload(offers))
    except Exception:                                           # noqa: BLE001
        return None
    m = v.get("mid")
    if m is None:
        return None
    return m if str(holding_side).upper() != "SHORT" else 1.0 - m


def probability_of(row):
    for k in ("p_blended", "p_pinnacle", "p_internal"):
        v = num((row or {}).get(k))
        if v is not None and 0.0 < v < 1.0:
            return v
    return None
