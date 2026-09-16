"""Latency metrics: detected_at - ts per trade, aggregated to p50/p95."""

from __future__ import annotations


def percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolated percentile; pct in [0, 100]. None on empty input."""
    if not values:
        return None
    data = sorted(values)
    if len(data) == 1:
        return data[0]
    rank = (pct / 100) * (len(data) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(data) - 1)
    frac = rank - lo
    return data[lo] + (data[hi] - data[lo]) * frac


def latency_summary(latencies_s: list[float]) -> dict:
    """Summarize detection latencies (seconds)."""
    return {
        "count": len(latencies_s),
        "p50": percentile(latencies_s, 50),
        "p95": percentile(latencies_s, 95),
        "max": max(latencies_s) if latencies_s else None,
    }
