"""Mapping quarantine (owner emergency 2026-08-23): after the wrong-side
incident, fuzzy-resolved mappings and the aec- tennis side-slug class must
be REFUSED at entry (with the mapping kept on the row for the postmortem)
until re-verified against venue truth. LIVE_MAPPING_QUARANTINE=off lifts.
"""

import asyncio
from datetime import date

from sportsassets import live_executor, pmus
from tests.test_live_executor_ladder import _LadderPool, _payload, _wire

TODAY = date.today().isoformat()


def _run(monkeypatch, mapped_slug, quarantine_on=True):
    pool = _LadderPool([])          # nothing held — ladder never trips
    submitted = _wire(monkeypatch, pool, mapped_slug)
    if quarantine_on:
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "on")
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    return pool, submitted


def _rejections(pool):
    return [(sql, a) for sql, a in pool.updates
            if "status='rejected'" in sql and "quarantined" in str(a)]


def test_fuzzy_mapping_is_quarantined(monkeypatch):
    pool, submitted = _run(monkeypatch,
                           f"tsc-epl-ars-che-{TODAY}-o3pt5")
    assert submitted == []
    rej = _rejections(pool)
    assert len(rej) == 1 and "src=fuzzy" in str(rej[0][1])


def test_aec_slug_is_quarantined_even_when_exact(monkeypatch):
    pool, submitted = _run(monkeypatch, "ignored")
    # exact path returns the aec- market: still refused
    monkeypatch.setattr(pmus, "resolve_market_exact",
                        lambda *a, **k: {"market_slug":
                                         f"aec-atp-x-y-{TODAY}-x",
                                         "title": "X vs Y",
                                         "outcome": "X"})
    pool2 = _LadderPool([])
    submitted2 = _wire(monkeypatch, pool2, "unused")
    monkeypatch.setattr(pmus, "resolve_market_exact",
                        lambda *a, **k: {"market_slug":
                                         f"aec-atp-x-y-{TODAY}-x",
                                         "title": "X vs Y",
                                         "outcome": "X"})
    monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "on")
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    assert submitted2 == []
    assert len(_rejections(pool2)) == 1


def test_quarantine_off_restores_flow(monkeypatch):
    pool, submitted = _run(monkeypatch,
                           f"tsc-epl-ars-che-{TODAY}-o3pt5",
                           quarantine_on=False)
    assert _rejections(pool) == []
    assert submitted  # order reached the venue


def test_mapping_kept_on_row_for_postmortem(monkeypatch):
    pool, _ = _run(monkeypatch, f"tsc-epl-ars-che-{TODAY}-o3pt5")
    slug_writes = [a for sql, a in pool.updates
                   if "us_market_slug" in sql]
    assert slug_writes and f"tsc-epl-ars-che-{TODAY}-o3pt5" in str(slug_writes)


def test_staleness_gate_refuses_late_signals(monkeypatch):
    monkeypatch.setenv("LIVE_MAX_REACTION_S", "90")
    pool = _LadderPool([])
    submitted = _wire(monkeypatch, pool, f"tsc-epl-ars-che-{TODAY}-o3pt5")
    # reaction is stamped by the dispatcher and passed in — 300s is stale
    asyncio.run(live_executor.maybe_execute(_payload(), 300.0))
    assert submitted == []
    stale = [(sql, a) for sql, a in pool.updates
             if "status='rejected'" in sql and "stale-signal" in str(a)]
    assert len(stale) == 1


def test_premap_live_lever_admits_only_premap_mappings(monkeypatch):
    from sportsassets.workers import premap as premap_mod

    pool = _LadderPool([])
    submitted = _wire(monkeypatch, pool, "unused-fuzzy-slug")
    monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "on")
    monkeypatch.setenv("LIVE_PREMAP", "on")

    async def fake_premap(_pool, *_a, **_k):
        return {"market_slug": f"atc-epl-ars-che-{TODAY}-ars",
                "title": "Arsenal", "outcome": "arsenal",
                "matched_by": "premap", "score": 1.0}

    monkeypatch.setattr(premap_mod, "resolve", fake_premap)
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    assert submitted  # premap mapping trades while quarantine holds


def test_premap_live_off_keeps_premap_refused(monkeypatch):
    from sportsassets.workers import premap as premap_mod

    pool = _LadderPool([])
    submitted = _wire(monkeypatch, pool, "unused-fuzzy-slug")
    monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "on")
    monkeypatch.setenv("LIVE_PREMAP", "off")

    async def fake_premap(_pool, *_a, **_k):
        return {"market_slug": f"atc-epl-ars-che-{TODAY}-ars",
                "title": "Arsenal", "outcome": "arsenal",
                "matched_by": "premap", "score": 1.0}

    monkeypatch.setattr(premap_mod, "resolve", fake_premap)
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    assert submitted == []
    assert len(_rejections(pool)) == 1
