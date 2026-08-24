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

    def test_swisstony_refused_by_the_hold_first(self, monkeypatch):
        """Even fully allowlisted, swisstony's PROFITABILITY hold
        (independent of quarantine state) refuses until his paper
        cohort at the new latency certifies."""
        pool, submitted = self._run(
            monkeypatch, "SwissTony",
            allowlist_env="homerunhazard,0x076daa87,swisstony")
        assert submitted == []
        rej = [(sql, a) for sql, a in pool.updates
               if "status='rejected'" in sql and "hold:" in str(a)]
        assert len(rej) == 1, "refusal must carry the hold reason"

    def test_non_allowlisted_whale_refused_with_allowlist_reason(
            self, monkeypatch):
        # kch123: not cut, not held — the refusal isolates the
        # allowlist mechanics.
        pool, submitted = self._run(monkeypatch, "kch123")
        assert submitted == []
        rej = [(sql, a) for sql, a in pool.updates
               if "status='rejected'" in sql
               and "verified-profitable" in str(a)]
        assert len(rej) == 1, "refusal must carry the allowlist reason"

    def test_swisstony_joins_via_env_no_deploy(self, monkeypatch):
        """The certification path: clear the hold AND join the
        allowlist — both deliberate env actions, no deploy."""
        monkeypatch.setenv("LIVE_HOLD_WHALES", "")
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


def _live_rows(slug):
    """Rows in the sweep's own shape, as live_rows_for_event returns
    them (the echo consumes THIS shape — leak-hunt find 2026-08-24:
    desk-shaped event_board rows made the tripwire inert)."""
    return [{"identifier": slug, "side_norm": "arsenal", "line": "",
             "kind": "side", "question": "Arsenal vs Chelsea Winner"},
            {"identifier": slug[:-4] + "-che", "side_norm": "chelsea",
             "line": "", "kind": "side",
             "question": "Arsenal vs Chelsea Winner"}]


def test_side_echo_ok_records_and_never_trips(monkeypatch):
    slug = f"atc-epl-ars-che-{TODAY}-ars"
    monkeypatch.setattr(premap_mod, "live_rows_for_event",
                        lambda ev: _live_rows(slug))
    pool = _EchoPool()
    asyncio.run(live_executor._side_echo_verify(
        pool, 101, slug, "Arsenal", "Arsenal vs Chelsea"))
    assert not any("mapping_quarantine" in sql for sql, _ in pool.executes)
    state = [a for sql, a in pool.executes if "side_echo_last" in sql]
    assert state and json.loads(state[0][0])["ok"] == 1


def test_live_rows_for_event_produces_matchable_rows(monkeypatch):
    """The end-to-end shape contract the leak-hunt proved broken: raw
    venue markets (dict OR bare-list response) must come back as rows
    match_side can actually match."""
    from sportsassets import pmus as pmus_mod

    slug = f"atc-epl-ars-che-{TODAY}-ars"
    raw_market = {"question": "Arsenal vs Chelsea Winner",
                  "slug": slug[:-4], "eventSlug": "epl-ars-che",
                  "marketSides": [
                      {"identifier": slug, "description": "Arsenal"},
                      {"identifier": slug[:-4] + "-che",
                       "description": "Chelsea"}]}

    class _Markets:
        def list(self, _q):
            return [raw_market]          # the bare-list SDK shape

    class _Client:
        markets = _Markets()

    monkeypatch.setattr(pmus_mod, "_get_client", lambda: _Client())
    rows = premap_mod.live_rows_for_event("epl-ars-che")
    hit = premap_mod.match_side(rows, "Arsenal", "Arsenal vs Chelsea")
    assert hit and hit["identifier"] == slug


def test_side_echo_mismatch_requarantines_alone(monkeypatch):
    ours = f"atc-epl-ars-che-{TODAY}-ars"
    # The live board now says the matcher's pick is a DIFFERENT
    # identifier than the one we bought — the one-order tripwire.
    other = f"atc-epl-ars-che-{TODAY}-v2-ars"
    monkeypatch.setattr(premap_mod, "live_rows_for_event",
                        lambda ev: _live_rows(other))
    pool = _EchoPool()
    asyncio.run(live_executor._side_echo_verify(
        pool, 101, ours, "Arsenal", "Arsenal vs Chelsea"))
    sqls = [sql for sql, _ in pool.executes]
    assert any("mapping_quarantine" in s and "'true'" in s for s in sqls)
    assert any("premap_live" in s and "'false'" in s for s in sqls)
    # The un-overridable circuit (leak-hunt find 2026-08-24): the two
    # switches above can be env-shadowed; this one cannot.
    assert any("side_echo_tripped" in s and "'true'" in s for s in sqls)
    err = [a for sql, a in pool.executes if "UPDATE live_orders" in sql]
    assert err and "SIDE-ECHO MISMATCH" in str(err[0])


class _TrippedPool(_LadderPool):
    """The circuit is armed; everything else answers as _LadderPool."""

    async def fetchval(self, sql, *a):
        if "ingestion_state" in sql and a and a[0] == "side_echo_tripped":
            return "true"
        return await super().fetchval(sql, *a)


def test_tripped_circuit_refuses_even_with_env_overrides(monkeypatch):
    """The leak-hunt scenario verbatim: LIVE_MAPPING_QUARANTINE=off and
    LIVE_PREMAP=on (env-armed operation) must NOT sail past a tripped
    side-echo circuit — the circuit has no env override."""
    from sportsassets import copy_sports as _cs

    pool = _TrippedPool([])
    submitted = _wire_with_real_cuts(
        monkeypatch, pool, f"tsc-epl-ars-che-{TODAY}-o3pt5")
    monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
    monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "off")
    monkeypatch.setenv("LIVE_PREMAP", "on")
    asyncio.run(live_executor.maybe_execute(
        _payload(whale_username="HomeRunHazard"), 5.0))
    assert submitted == []
    rej = [(sql, a) for sql, a in pool.updates
           if "status='rejected'" in sql and "side-echo tripped" in str(a)]
    assert len(rej) == 1


def test_side_echo_outage_never_trips_the_breaker(monkeypatch):
    def boom(_ev):
        raise RuntimeError("venue 503")

    monkeypatch.setattr(premap_mod, "live_rows_for_event", boom)
    pool = _EchoPool()
    asyncio.run(live_executor._side_echo_verify(
        pool, 101, f"atc-epl-ars-che-{TODAY}-ars", "Arsenal",
        "Arsenal vs Chelsea", attempts=1))
    assert not any("mapping_quarantine" in sql for sql, _ in pool.executes)
    state = [a for sql, a in pool.executes if "side_echo_last" in sql]
    assert state and json.loads(state[0][0])["unverified"] == 1


def test_hold_refuses_swisstony_even_with_quarantine_off(monkeypatch):
    """The leak-hunt scenario: lifting the quarantine (a mapping-
    fidelity action) must NOT re-arm swisstony — his hold is a
    profitability decision with its own gate."""
    from sportsassets import copy_sports as _cs

    pool = _LadderPool([])
    submitted = _wire_with_real_cuts(
        monkeypatch, pool, f"tsc-epl-ars-che-{TODAY}-o3pt5")
    monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
    # quarantine fully OFF (env, as _wire sets) — the hold still bites
    asyncio.run(live_executor.maybe_execute(
        _payload(whale_username="SwissTony"), 5.0))
    assert submitted == []
    rej = [(sql, a) for sql, a in pool.updates
           if "status='rejected'" in sql and "hold:" in str(a)]
    assert len(rej) == 1
    # clearing the hold (deliberate env action) restores his flow
    pool2 = _LadderPool([])
    submitted2 = _wire_with_real_cuts(
        monkeypatch, pool2, f"tsc-epl-ars-che-{TODAY}-o3pt5")
    monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
    monkeypatch.setenv("LIVE_HOLD_WHALES", "")
    asyncio.run(live_executor.maybe_execute(
        _payload(whale_username="SwissTony"), 5.0))
    assert submitted2


def test_clob_fallback_is_fail_closed(monkeypatch):
    """Leak-hunt find: active_venue() silently falls back to the global
    CLOB on a PMUS-credential fault, and that branch carried none of
    the freeze controls. It must refuse unless deliberately reopened."""
    from sportsassets import copy_sports as _cs

    pool = _LadderPool([])
    submitted = _wire_with_real_cuts(
        monkeypatch, pool, f"tsc-epl-ars-che-{TODAY}-o3pt5")
    monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
    monkeypatch.setattr(live_executor, "active_venue",
                        lambda: "polymarket-clob")
    clob_orders = []
    monkeypatch.setattr(live_executor, "_submit_fok",
                        lambda *a, **k: clob_orders.append(a) or
                        {"ok": True, "filled_shares": 1.0,
                         "fill_price": 0.5, "order_id": "x", "raw": {}})
    asyncio.run(live_executor.maybe_execute(
        _payload(whale_username="HomeRunHazard"), 5.0))
    assert submitted == [] and clob_orders == []
    rej = [(sql, a) for sql, a in pool.updates
           if "status='rejected'" in sql and "clob-leg-closed" in str(a)]
    assert len(rej) == 1
    # the deliberate reopen lever
    pool2 = _LadderPool([])
    _wire_with_real_cuts(monkeypatch, pool2,
                         f"tsc-epl-ars-che-{TODAY}-o3pt5")
    monkeypatch.setattr(_cs, "copy_allowed", lambda *a, **k: True)
    monkeypatch.setattr(live_executor, "active_venue",
                        lambda: "polymarket-clob")
    monkeypatch.setattr(live_executor, "_submit_fok",
                        lambda *a, **k: clob_orders.append(a) or
                        {"ok": True, "filled_shares": 1.0,
                         "fill_price": 0.5, "order_id": "x", "raw": {}})
    monkeypatch.setenv("LIVE_CLOB_COPIES", "on")
    asyncio.run(live_executor.maybe_execute(
        _payload(whale_username="HomeRunHazard"), 5.0))
    assert clob_orders, "the explicit lever reopens the leg"
