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
    # The harness whale is RN1; the 2026-08-24 premap-live allowlist
    # only admits the verified-profitable set, so the lever mechanics
    # are tested with RN1 explicitly allowlisted. The allowlist itself
    # has its own tests (test_trueedge_cut.py).
    monkeypatch.setenv("LIVE_PREMAP_WHALES", "rn1")

    async def fake_premap(_pool, *_a, **_k):
        return {"market_slug": f"atc-epl-ars-che-{TODAY}-ars",
                "title": "Arsenal", "outcome": "arsenal",
                "intent": "ORDER_INTENT_BUY_LONG",
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
    monkeypatch.setenv("LIVE_PREMAP_WHALES", "rn1")  # see admit test

    async def fake_premap(_pool, *_a, **_k):
        return {"market_slug": f"atc-epl-ars-che-{TODAY}-ars",
                "title": "Arsenal", "outcome": "arsenal",
                "intent": "ORDER_INTENT_BUY_LONG",
                "matched_by": "premap", "score": 1.0}

    monkeypatch.setattr(premap_mod, "resolve", fake_premap)
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    assert submitted == []
    assert len(_rejections(pool)) == 1


class TestPerWhaleStalenessCaps:
    """The swisstony work (owner order 2026-08-24 evening): one global
    staleness cap is wrong because edges decay at wildly different
    rates. Measured latency cost against each whale's own edge:
      0x076         lat_cost    -59 on +15,576 — decay is ~free
      homerunhazard lat_cost 10,612 on +24,657 — survives it
      swisstony     lat_cost 15,063 on +14,805 — latency eats ALL of it
    A whale whose entire edge is consumed by delay must not be copied
    on a delayed signal: at 90s his expected value is negative by his
    own numbers."""

    def test_swisstony_gets_the_tight_cap(self):
        from sportsassets.live_executor import _stale_cap_for

        assert _stale_cap_for("swisstony") == 15.0
        assert _stale_cap_for("SwissTony") == 15.0

    def test_whales_whose_edge_survives_keep_the_default(self):
        from sportsassets.live_executor import _stale_cap_for

        assert _stale_cap_for("homerunhazard") == 90.0
        assert _stale_cap_for("0x076daa87") == 90.0
        assert _stale_cap_for(None) == 90.0

    def test_env_moves_the_default_but_not_a_measured_cap(self,
                                                          monkeypatch):
        """A measured cap is evidence, not a preference — a stray
        environment variable must not be able to loosen it."""
        from sportsassets.live_executor import _stale_cap_for

        monkeypatch.setenv("LIVE_MAX_REACTION_S", "600")
        assert _stale_cap_for("homerunhazard") == 600.0
        assert _stale_cap_for("swisstony") == 15.0

    def test_a_late_swisstony_signal_is_refused(self, monkeypatch):
        """30s is fine for everyone else and far too late for him.
        (The harness slug is a soccer TOTAL, which is not one of his
        cells, so the cell gate would refuse him first — stubbed here
        to isolate the staleness rule under test.)"""
        from sportsassets import copy_sports as _cs

        pool = _LadderPool([])
        submitted = _wire(monkeypatch, pool,
                          f"tsc-epl-ars-che-{TODAY}-o3pt5")
        monkeypatch.setattr(live_executor, "COPY_CUT_WHALES", frozenset())
        monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
        asyncio.run(live_executor.maybe_execute(
            _payload(whale_username="SwissTony"), 30.0))
        assert submitted == []
        stale = [(sql, a) for sql, a in pool.updates
                 if "status='rejected'" in sql and "stale-signal" in str(a)]
        assert len(stale) == 1
