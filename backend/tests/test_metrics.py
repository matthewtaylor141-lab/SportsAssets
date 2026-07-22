"""Latency metric aggregation (p50/p95 for the admin dashboard)."""

import pytest

from sportsassets.metrics import latency_summary, percentile


def test_percentile_basics():
    data = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(data, 50) == 3.0
    assert percentile(data, 0) == 1.0
    assert percentile(data, 100) == 5.0
    assert percentile(data, 95) == pytest.approx(4.8)


def test_percentile_edge_cases():
    assert percentile([], 50) is None
    assert percentile([7.5], 95) == 7.5


def test_latency_summary():
    s = latency_summary([1.2, 1.8, 2.4, 9.0])
    assert s["count"] == 4
    assert s["p50"] == pytest.approx(2.1)
    assert s["max"] == 9.0
    assert latency_summary([]) == {"count": 0, "p50": None, "p95": None, "max": None}
