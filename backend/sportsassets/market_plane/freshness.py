"""Per-symbol latency decomposition and SLO evaluation."""
from __future__ import annotations

import math

VERSION = "MARKET_FRESHNESS_SLO_V1"
DEFAULT_SLO_MS = {"processing_p95": 250.0, "distribution_p95": 250.0,
                  "internal_total_p95": 500.0}


def _ms(a, b):
    if a is None or b is None:
        return None
    return max(0.0, (float(b) - float(a)) * 1000.0)


def decompose(*, venue_at=None, received_at=None, normalized_at=None,
              distributed_at=None, read_at=None, now=None) -> dict:
    now = float(now if now is not None else (read_at if read_at is not None else 0.0))
    transport = _ms(venue_at, received_at)
    processing = _ms(received_at, normalized_at)
    distribution = _ms(normalized_at, distributed_at)
    internal = _ms(received_at, distributed_at)
    return {
        "venue_age_ms": None if venue_at is None or not now else max(0.0, (now-float(venue_at))*1000),
        "transport_ms": transport, "processing_ms": processing,
        "distribution_ms": distribution, "internal_total_ms": internal,
        "local_read_age_ms": None if distributed_at is None or not now else max(0.0, (now-float(distributed_at))*1000),
        "version": VERSION,
    }


def percentile(values, q):
    xs = sorted(float(x) for x in values if x is not None and math.isfinite(float(x)))
    if not xs: return None
    if len(xs) == 1: return xs[0]
    pos = (len(xs)-1)*float(q); lo=int(pos); hi=min(lo+1,len(xs)-1); w=pos-lo
    return xs[lo]*(1-w)+xs[hi]*w


def summarize(samples: list[dict], slo: dict | None = None) -> dict:
    slo = dict(DEFAULT_SLO_MS | (slo or {}))
    fields = ("transport_ms","processing_ms","distribution_ms","internal_total_ms")
    out = {"n": len(samples), "version": VERSION, "slo": slo}
    for f in fields:
        vals=[s.get(f) for s in samples if s.get(f) is not None]
        out[f] = {"p50": percentile(vals,.5), "p95": percentile(vals,.95),
                  "p99": percentile(vals,.99), "n": len(vals)}
    checks = {
        "processing_p95": out["processing_ms"]["p95"],
        "distribution_p95": out["distribution_ms"]["p95"],
        "internal_total_p95": out["internal_total_ms"]["p95"],
    }
    failures=[]
    for k,v in checks.items():
        if v is None: failures.append(k+":UNMEASURED")
        elif v > slo[k]: failures.append(k)
    out["green"] = not failures
    out["failures"] = failures
    return out
