"""TRUEEDGE cuts, the premap-live allowlist, and the side-echo tripwire
(owner order 2026-08-24, from the verified counterfactual table).

Three independent money blocks for a cut whale:
1. COPY_CUT_WHALES refuses at entry (before any row is written),
2. the 0.00 clip buys zero contracts,
3. the premap-live allowlist admits only the verified-profitable set.

And the running invariant: after any fill, the side echo re-derives the
mapping from the venue's LIVE board through the same matcher; a
confirmed divergence re-arms the total quarantine on its own.
"""

import asyncio
import json
from datetime import date

from sportsassets import live_executor
from sportsassets.workers import premap as premap_mod
from tests.test_live_executor_ladder import _LadderPool, _payload, _wire

TODAY = date.today().isoformat()

REAL_CUTS = frozenset({"rn1", "ferrarichampions2026",
                       live_executor._W2C33})


def _wire_with_real_cuts(monkeypatch, pool, mapped_slug):
    """The shared harness lifts the cut for its RN1 fixtures; these
    tests put the REAL cut set back after wiring."""
    submitted = _wire(monkeypatch, pool, mapped_slug)
    monkeypatch.setattr(live_executor, "COPY_CUT_WHALES", REAL_CUTS)
    return submitted


def test_cut_whales_are_refused_at_entry(monkeypatch):
    for whale in ("RN1", "rn1", "ferrarichampions2026",
                  live_executor._W2C33):
        pool = _LadderPool([])
        submitted = _wire_with_real_cuts(
            monkeypatch, pool, f"tsc-epl-ars-che-{TODAY}-o3pt5")
        asyncio.run(live_executor.maybe_execute(
            _payload(whale_username=whale), 5.0))
        assert submitted == [], f"{whale} must never reach the venue"
        assert pool.ladder_queries == 0, \
            f"{whale} must be refused before mapping even runs"


def test_verified_whales_still_flow_when_quarantine_lifted(monkeypatch):
    from sportsassets import copy_sports as _cs

    pool = _LadderPool([])
    submitted = _wire_with_real_cuts(
        monkeypatch, pool, f"tsc-epl-ars-che-{TODAY}-o3pt5")
    monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
    asyncio.run(live_executor.maybe_execute(
        _payload(whale_username="HomeRunHazard"), 5.0))
    assert submitted


class TestPremapLiveAllowlist:
    def _premap(self, monkeypatch):
        async def fake_premap(_pool, *_a, **_k):
            return {"market_slug": f"atc-epl-ars-che-{TODAY}-ars",
                    "title": "Arsenal", "outcome": "arsenal",
                    "matched_by": "premap", "score": 1.0}

        monkeypatch.setattr(premap_mod, "resolve", fake_premap)

    def _run(self, monkeypatch, whale, allowlist_env=None):
        from sportsassets import copy_sports as _cs

        pool = _LadderPool([])
        submitted = _wire_with_real_cuts(monkeypatch, pool, "unused")
        monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "on")
        monkeypatch.setenv("LIVE_PREMAP", "on")
        if allowlist_env is not None:
            monkeypatch.setenv("LIVE_PREMAP_WHALES", allowlist_env)
        else:
            monkeypatch.delenv("LIVE_PREMAP_WHALES", raising=False)
        self._premap(monkeypatch)
        asyncio.run(live_executor.maybe_execute(
            _payload(whale_username=whale), 5.0))
        return pool, submitted

    def test_default_allowlist_admits_hrh_and_0x076(self, monkeypatch):
        for whale in ("HomeRunHazard", "0x076daa87"):
            pool, submitted = self._run(monkeypatch, whale)
            assert submitted, f"{whale} is verified-profitable — trades"

    def test_swisstony_refused_until_allowlisted(self, monkeypatch):
        pool, submitted = self._run(monkeypatch, "SwissTony")
        assert submitted == []
        rej = [(sql, a) for sql, a in pool.updates
               if "status='rejected'" in sql
               and "verified-profitable" in str(a)]
        assert len(rej) == 1, "refusal must carry the allowlist reason"

    def test_swisstony_joins_via_env_no_deploy(self, monkeypatch):
        pool, submitted = self._run(
            monkeypatch, "SwissTony",
            allowlist_env="homerunhazard,0x076daa87,swisstony")
        assert submitted


class _EchoPool:
    """Pool for the side-echo unit tests: serves the us_premap event
    lookup and records every state write."""

    def __init__(self, event_slug="epl-ars-che"):
        self.event_slug = event_slug
        self.executes = []

    async def fetchval(self, sql, *a):
        if "FROM us_premap" in sql:
            return self.event_slug
        return None   # no prior side_echo_last

    async def execute(self, sql, *a):
        self.executes.append((" ".join(sql.split()), a))


def _board_market(slug, description):
    return {"question": "Arsenal vs Chelsea Winner", "slug": slug[:-4],
            "marketSides": [
                {"identifier": slug, "description": description},
                {"identifier": slug[:-4] + "-che", "description": "Chelsea"},
            ]}


def test_side_echo_ok_records_and_never_trips(monkeypatch):
    from sportsassets import pmus

    slug = f"atc-epl-ars-che-{TODAY}-ars"
    monkeypatch.setattr(pmus, "event_board",
                        lambda ev: [_board_market(slug, "Arsenal")])
    pool = _EchoPool()
    asyncio.run(live_executor._side_echo_verify(
        pool, 101, slug, "Arsenal", "Arsenal vs Chelsea"))
    assert not any("mapping_quarantine" in sql for sql, _ in pool.executes)
    state = [a for sql, a in pool.executes if "side_echo_last" in sql]
    assert state and json.loads(state[0][0])["ok"] == 1


def test_side_echo_mismatch_requarantines_alone(monkeypatch):
    from sportsassets import pmus

    ours = f"atc-epl-ars-che-{TODAY}-ars"
    # The live board now says the matcher's pick is a DIFFERENT
    # identifier than the one we bought — the one-order tripwire.
    other = f"atc-epl-ars-che-{TODAY}-v2-ars"
    monkeypatch.setattr(pmus, "event_board",
                        lambda ev: [_board_market(other, "Arsenal")])
    pool = _EchoPool()
    asyncio.run(live_executor._side_echo_verify(
        pool, 101, ours, "Arsenal", "Arsenal vs Chelsea"))
    sqls = [sql for sql, _ in pool.executes]
    assert any("mapping_quarantine" in s and "'true'" in s for s in sqls)
    assert any("premap_live" in s and "'false'" in s for s in sqls)
    err = [a for sql, a in pool.executes if "UPDATE live_orders" in sql]
    assert err and "SIDE-ECHO MISMATCH" in str(err[0])


def test_side_echo_outage_never_trips_the_breaker(monkeypatch):
    from sportsassets import pmus

    def boom(_ev):
        raise RuntimeError("venue 503")

    monkeypatch.setattr(pmus, "event_board", boom)
    pool = _EchoPool()
    asyncio.run(live_executor._side_echo_verify(
        pool, 101, f"atc-epl-ars-che-{TODAY}-ars", "Arsenal",
        "Arsenal vs Chelsea", attempts=1))
    assert not any("mapping_quarantine" in sql for sql, _ in pool.executes)
    state = [a for sql, a in pool.executes if "side_echo_last" in sql]
    assert state and json.loads(state[0][0])["unverified"] == 1
