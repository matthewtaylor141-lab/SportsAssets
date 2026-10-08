"""Runtime stability tests for the completion/readiness patch.

These tests are intended to be copied into backend/tests/ and adapted only
where the current tree has moved. They pin the verified production defect:
shared workers must not supervise the Universal Market Plane at all.
"""
from __future__ import annotations


def test_shared_workers_do_not_start_universal_market_plane(monkeypatch):
    from sportsassets.workers import all as workers_all

    monkeypatch.setenv("UNIVERSAL_MARKET_PLANE", "on")
    names = [name for name, _ in workers_all.startable_loops()]
    assert "universal_market_plane" not in names
    assert "premap" in names
    assert "institutional_md" in names


def test_universal_market_plane_is_dedicated_only_constant():
    from sportsassets.workers import all as workers_all

    assert "universal_market_plane" in workers_all.DEDICATED_ONLY_LOOPS


def test_universal_market_plane_defaults_to_single_subscribe_all_stream():
    from sportsassets.workers import universal_market_plane as ump

    env = {}
    assert ump.subscribe_all_enabled(env) is True
    assert ump.caps(env)[0] == 1
