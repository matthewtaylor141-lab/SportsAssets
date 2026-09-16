"""Shared fixtures for the edge-engine suite.

The 2026-08-24 TRUEEDGE cut zeroed rn1 / ferrarichampions2026 / 0x2c33
in the REAL Kalshi clip maps (their full detected books graded negative
at their own prices — venue parity with the PMUS leg) and re-sized
homerunhazard to $300 parity. The legacy fixture ecosystem predates the
cut and exercises sweep MECHANICS with RN1/0x2c33/HRH rows, so the
historical clips are restored here for every test. The cut itself is
pinned by test_trueedge_cut_kalshi.py against the source of truth.
"""

import pytest


@pytest.fixture(autouse=True)
def _legacy_clips(monkeypatch):
    from edge.shadow import kalshi_copies as kc

    monkeypatch.setitem(kc.PER_COPY_USD, "rn1", 225.00)
    monkeypatch.setitem(kc.PER_COPY_USD, kc._W2C33, 300.00)
    monkeypatch.setitem(kc.PER_COPY_USD, "homerunhazard", 112.50)
    monkeypatch.setitem(kc.PER_COPY_USD_SPORT, ("rn1", "tennis"), 112.50)
    monkeypatch.setitem(kc.PER_COPY_USD_SPORT, ("rn1", "baseball"), 375.00)
    monkeypatch.setitem(kc.PER_COPY_USD_SPORT, ("rn1", "soccer"), 300.00)
    monkeypatch.setitem(kc.PER_COPY_USD_SPORT,
                        ("homerunhazard", "baseball"), 225.00)
    monkeypatch.setitem(kc.PER_COPY_USD_SPORT,
                        ("homerunhazard", "football"), 37.50)
