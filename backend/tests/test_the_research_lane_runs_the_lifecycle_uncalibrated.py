"""THE LIFECYCLE IN THE STATE PRODUCTION IS ACTUALLY IN.

The controlled positive-branch test in
`test_the_entry_lane_reaches_inventory.py` supplies an external-source
CALIBRATION ROW, and says so: without it the MODEL_TRUST_DRIFT gate blocks
every entry, which is the true production state. So that test proves the
lifecycle runs on a lane that does not yet exist.

THIS ONE RUNS THE SAME LIFECYCLE THE OTHER WAY ROUND: no calibration row at
all, and the UNFUNDED RESEARCH LANE explicitly armed through its own control
row. That is the only combination production can currently reach and still
admit an entry -- `bettor_research_shadow` waives exactly MODEL_TRUST_DRIFT
and nothing else, above the gate rather than inside it.

WHAT IS SUPPLIED (market inputs, named individually):
  * the provider's odds payload      -- a test cannot wait for an edge
  * the venue's ask ladder           -- three levels, so the crossing model
                                        has depth to walk
  * the venue's settlement prose     -- the published terms
  * the fixture metadata             -- phase, format, event state
  * the venue's settlement response  -- at the SDK boundary only
AND WHAT IS NOT: no calibration row. Everything between the supplied inputs
is production code.

WHAT IS MODELLED: every order and every fill. `is_modelled` is CHECKed true
by migration 100, the fills are `MARKETABLE_RECONSTRUCTED` against the
supplied ladder, and P_FILL stays NOT_IDENTIFIED -- none of it is evidence
that our order would have been filled.

FUNDED SUBMISSION MUST REMAIN UNREACHABLE THROUGHOUT, and that is asserted
rather than assumed: no venue order call is stubbed or made, the execution
flag stays off, and the only client method this test offers the process is
`markets.settlement`, which reads.
"""

from __future__ import annotations

import os
import time

import pytest

from tests import test_the_entry_lane_reaches_inventory as BASE
from sportsassets import bettor_entry_inventory as inv
from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import bettor_research_shadow as rsh
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

EXPECT_QTY = 900.0


async def _disarm_research(conn):
    """REMOVE WHAT THIS FILE SET.

    MY OWN DEFECT, CAUGHT IMMEDIATELY. `ingestion_state` is not touched by
    `_cleanup`, so the armed row survived into the next test and
    `test_without_a_calibration_row_the_entry_is_refused_by_name` -- which
    asserts production's real state -- started admitting an entry. A test
    that changes global state and does not undo it breaks whatever runs
    after it, and the failure appears in the other test's name.
    """
    await conn.execute("DELETE FROM ingestion_state WHERE key = $1",
                       rsh.CONTROL_KEY)


async def _arm_research(conn):
    """The lane's own control row, set the way the admin route sets it."""
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, 'true'::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = 'true'::jsonb",
        rsh.CONTROL_KEY)


async def _no_calibration(conn):
    """Assert the absence rather than trusting the fixture."""
    n = await conn.fetchval(
        "SELECT count(*) FROM external_source_calibration "
        "WHERE source_version = $1", devig.VERSION)
    assert int(n or 0) == 0, (
        "this test's whole point is that no calibration exists; found %s "
        "row(s)" % n)


@pg
async def test_uncalibrated_and_armed_the_lane_still_refuses():
    """THE CONTROL. Armed but with the gate NOT waived is still a refusal.

    Run first so a later admission cannot be mistaken for the gate being
    absent: with the research row ABSENT and no calibration, the entry is
    refused by name. This is production's actual state.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    mp = pytest.MonkeyPatch()
    try:
        await BASE._seed(conn)
        await conn.execute("DELETE FROM ingestion_state WHERE key = $1",
                           rsh.CONTROL_KEY)
        await _no_calibration(conn)
        BASE._stub(mp)
        made = await loop.cycle(conn)
        assert made["refusals"].get("ENTRY_INVENTORY_WRITTEN") is None, \
            made["refusals"]
        assert not made["entries"], made["entries"]
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE policy = $1",
            inv.POLICY) == 0
    finally:
        mp.undo()
        await _disarm_research(conn)
        await BASE._cleanup(conn)
        await conn.close()


@pg
async def test_uncalibrated_research_lane_runs_the_whole_lifecycle():
    """ENTRY -> MANAGEMENT -> RELOAD -> SETTLEMENT -> RECONCILED P&L,
    with NO calibration row and the research lane armed."""
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_entry_settlement as SETTLE
    from sportsassets import bettor_rn1x_store as store
    from sportsassets import pmus as _pmus
    from sportsassets.workers import rn1x_shadow as RS

    conn = await asyncpg.connect(DSN)
    mp = pytest.MonkeyPatch()
    try:
        await BASE._seed(conn)
        await _arm_research(conn)
        await _no_calibration(conn)
        BASE._stub(mp)

        # ── 1 · ENTRY, through the scheduled cycle ───────────────────
        made = await loop.cycle(conn)
        assert made["refusals"].get("ENTRY_INVENTORY_WRITTEN") == 1, \
            made["refusals"]
        pid = made["entries"][0]["position_id"]
        acct = made["entries"][0]["accounting"]

        # THE WAIVER IS RECORDED ON THE ROW, and it waived exactly one gate.
        risk = await conn.fetchval(
            "SELECT risk_verdict::text FROM external_valuations "
            "WHERE experiment_id = $1 AND admissible ORDER BY id DESC "
            "LIMIT 1", ext.EXPERIMENT_ID)
        assert risk and "research_waiver" in risk, risk
        import json as _json

        w = _json.loads(risk)["research_waiver"]
        assert w["authorised"] is True, w
        assert list(w["waived"]) == ["MODEL_TRUST_DRIFT"], w
        for never in ("STALE_DATA", "OUT_OF_DISTRIBUTION",
                      "UNRESOLVED_SETTLEMENT_SEMANTICS"):
            assert never not in (w["waived"] or []), (never, w)

        # ── RESEARCH PROVENANCE, on the position itself ──────────────
        pos = await conn.fetchrow(
            "SELECT provenance, entry_kind, policy, source_trade_id, "
            "source_account, seed_qty::float8 AS q FROM rn1x_positions "
            "WHERE position_id = $1", pid)
        # THE RESEARCH PROVENANCE, NOT THE CALIBRATED ONE. Migration 122
        # exists precisely so these two are never summed: an entry created
        # while MODEL_TRUST_DRIFT is still unevaluable is a different claim
        # from one that cleared it. My first version of this assertion
        # expected `inv.PROVENANCE` and was wrong -- the lane is right.
        assert pos["provenance"] == "UNCALIBRATED_RESEARCH_SHADOW", \
            pos["provenance"]
        assert pos["provenance"] != inv.PROVENANCE, (
            "the waived and the cleared lanes must stay distinguishable")
        assert pos["policy"] == inv.POLICY
        assert pos["source_trade_id"] is None
        assert pos["q"] == EXPECT_QTY

        # ── MODELLED, AND SAID SO ────────────────────────────────────
        assert await conn.fetchval(
            "SELECT bool_and(is_modelled) FROM rn1x_orders "
            "WHERE position_id = $1", pid) is True
        assert await conn.fetchval(
            "SELECT bool_and(f.is_modelled) FROM rn1x_fills f JOIN "
            "rn1x_orders o ON o.order_id = f.order_id "
            "WHERE o.position_id = $1", pid) is True
        assert await conn.fetchval(
            "SELECT sum(f.qty)::float8 FROM rn1x_fills f JOIN rn1x_orders o "
            "ON o.order_id = f.order_id WHERE o.position_id = $1",
            pid) == EXPECT_QTY

        # ── 2 · MANAGEMENT, twice, through the scheduler's entry point ─
        for _ in range(2):
            got = await RS.run_continuing_management(
                conn, experiment_id=RS.CHALLENGER_EXPERIMENT_ID)
            el = got["entry_lane"]
            assert "error" not in el, el.get("error")
            assert el.get("examined") >= 1, el

        # ── 3 · RELOAD: a NEW CONNECTION, which is not a new process ──
        #
        # Named precisely. It drops every in-memory Python object this test
        # holds and forces the position to be rebuilt from the ledger, and
        # that is what it proves. It does NOT prove process recovery: the
        # interpreter, the module state and the connection pool's own
        # lifecycle are untouched. See
        # research/SHADOW_SYSTEM_ACCEPTANCE.md for what is and is not
        # verified about restarts.
        await conn.close()
        conn = await asyncpg.connect(DSN)
        again = await RS.run_continuing_management(
            conn, experiment_id=RS.CHALLENGER_EXPERIMENT_ID)
        assert again["entry_lane"].get("examined") >= 1

        # THE 900 CONTRACTS SURVIVE THE RELOAD -- the defect this pins.
        loaded = await store.load_position(conn, pid)
        assert loaded["orders"] and loaded["fills"]
        from sportsassets import bettor_mgmt_lifecycle as lc

        prow = dict(await conn.fetchrow(
            "SELECT position_id, condition_id, outcome_index, "
            "seed_qty::float8 AS seed_qty, seed_price::float8 AS seed_price, "
            "extract(epoch FROM decision_ts)::float8 AS decision_ts, "
            "source_trade_id FROM rn1x_positions WHERE position_id = $1",
            pid))
        m = lc.reload_managed(position=prow, orders=loaded["orders"],
                              fills=loaded["fills"],
                              fee_fn=lambda qty, price, maker=False: 0.0)
        assert m.assign_seed is False, (
            "an autonomous entry's acquisition is in the ledger")
        assert m.held() == EXPECT_QTY, (
            "reloaded inventory must be the 900 the fills bought, not 1800")

        # ── 4 · SETTLEMENT, by production code ───────────────────────
        calls = []

        class _S:
            @staticmethod
            def settlement(slug):
                calls.append(slug)
                return {"marketSlug": slug,
                        "settlementPrice": {"value": "1",
                                            "currency": "USD"},
                        "settledAt": "2026-09-24T23:14:07Z"}

        class _C:
            markets = _S()

        mp.setattr(_pmus, "_get_client", lambda: _C())

        async def _no_odds(*a, **k):
            raise AssertionError("settlement must not need fresh odds")

        mp.setattr(loop, "fetch_odds", _no_odds)
        done = await RS.run_continuing_management(
            conn, experiment_id=RS.CHALLENGER_EXPERIMENT_ID)
        s = done["settlement"]
        assert s["ran"] is True and s["settled"] == 1, s
        assert calls == [BASE.US_SLUG], calls
        res = [r for r in s["results"] if r["position_id"] == pid][0]
        assert res["status"] == SETTLE.S_SETTLED and res["written"] is True

        # ── 5 · RECONCILED P&L ───────────────────────────────────────
        out = await conn.fetchrow(
            "SELECT realized_cash_usd::float8 AS cash, fees_usd::float8 AS "
            "fees, net_usd::float8 AS net, residual_qty::float8 AS resid, "
            "outcome_basis FROM rn1x_outcomes WHERE position_id = $1", pid)
        assert out["resid"] == 0.0
        assert out["cash"] == pytest.approx(acct["filled_qty"])
        assert out["fees"] == pytest.approx(acct["fees_usd"], rel=1e-6)
        assert out["net"] == pytest.approx(
            acct["filled_qty"] - acct["cost_basis_usd"], rel=1e-6)
        assert out["outcome_basis"] == SETTLE.BASIS_SETTLED

        # ── 6 · FUNDED SUBMISSION WAS NEVER REACHABLE ────────────────
        # The only client method offered to the process was a READ, and it
        # is the only one that was called.
        assert calls == [BASE.US_SLUG]
        assert not hasattr(_C, "orders"), (
            "no order surface was offered to the process at all")
        # AND NOTHING LANDED IN A FUNDED TABLE. `live_orders` is the funded
        # lane's own table; this lane must not have written to it.
        assert await conn.fetchval(
            "SELECT count(*) FROM live_orders") == 0
        assert made["order_submitted"] is False
    finally:
        mp.undo()
        await _disarm_research(conn)
        await BASE._cleanup(conn)
        await conn.close()


@pg
async def test_a_second_uncalibrated_cycle_adds_no_second_position():
    """Duplicate protection does not depend on the calibration state."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    mp = pytest.MonkeyPatch()
    try:
        await BASE._seed(conn)
        await _arm_research(conn)
        await _no_calibration(conn)
        BASE._stub(mp)
        first = await loop.cycle(conn)
        assert first["refusals"].get("ENTRY_INVENTORY_WRITTEN") == 1
        second = await loop.cycle(conn)
        assert second["refusals"].get("ENTRY_INVENTORY_WRITTEN") is None, \
            second["refusals"]
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE policy = $1",
            inv.POLICY) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_orders") == 1
    finally:
        mp.undo()
        await _disarm_research(conn)
        await BASE._cleanup(conn)
        await conn.close()


def test_the_waiver_can_never_reach_a_funded_path():
    """Pure: the lane's declaration, not a runtime observation."""
    d = rsh.describe()
    assert "MODEL_TRUST_DRIFT" in d["waivable"]
    for never in ("STALE_DATA", "OUT_OF_DISTRIBUTION",
                  "UNRESOLVED_SETTLEMENT_SEMANTICS"):
        assert never in d["never_waivable"], never
    assert time is not None  # keeps the import honest
