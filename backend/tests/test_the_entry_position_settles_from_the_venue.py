"""SETTLEMENT IS READ FROM THE VENUE, NOT SUPPLIED BY THE TEST.

The lifecycle test pins the happy path end to end through
`run_continuing_management`. These pin the branches that path does not
reach, and they pin them at the SAME transport boundary: the only thing
any of them supplies is the venue's own response.

WHAT EACH ONE IS FOR. A settled contract, a confirmed void, a market the
venue has not settled, a read that failed, a fixture the venue reports only
as a named winner, and a position whose venue side is not established are
SIX DIFFERENT STATES. Collapsing any of them into "not settled yet" is how
a finished fixture stayed open, and collapsing any of them into a written
outcome would be worse.
"""

from __future__ import annotations

import os
import time

import pytest

from sportsassets import bettor_entry_inventory as inv
from sportsassets import bettor_entry_settlement as S
from sportsassets import bettor_external_shadow as ext
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

LONG_ROW = {
    "position_id": "P1", "condition_id": "c1", "outcome_index": 0,
    "seed_qty": 100.0, "filled_qty": 100.0,
    # THE BASIS INCLUDES THE FEES, which is how the entry writes it.
    "cost_basis_usd": 64.0, "fees_usd": 2.0,
    "us_market_slug": "aec-mlb-x", "buy_intent": "ORDER_INTENT_BUY_LONG",
    "ladder_side": "ASK", "payout_event": "Houston Astros",
    "valuation_id": 7,
}


def _res(price_raw, *, status="RESOLVED", settled_at=None):
    return {"status": status, "settlement_price_raw": price_raw,
            "settlement_price": (None if price_raw is None
                                 else float(price_raw)),
            "outcome": price_raw, "settled_at": settled_at}


# ── the accounting, on its own ────────────────────────────────────────

def test_a_winning_contract_returns_its_quantity_in_cash():
    a = S.accounting(filled_qty=100.0, cost_basis_usd=64.0, fees_usd=2.0,
                     outcome=1)
    assert a["payout_per_contract"] == 1.0
    assert a["realized_cash_usd"] == 100.0
    # NET IS AGAINST THE BASIS INCLUDING FEES. A net computed against the
    # stake alone would report the fees as profit.
    assert a["net_usd"] == pytest.approx(36.0)
    assert a["stake_usd"] == pytest.approx(62.0)
    assert a["residual_qty"] == 0.0, "settlement closes the inventory"
    assert a["residual_settled_usd"] == 100.0
    assert a["unpaired_qty"] == 0.0
    assert a["reconciles"] is True


def test_a_losing_contract_pays_nothing_and_loses_the_whole_basis():
    a = S.accounting(filled_qty=100.0, cost_basis_usd=64.0, fees_usd=2.0,
                     outcome=0)
    assert a["payout_per_contract"] == 0.0
    assert a["realized_cash_usd"] == 0.0
    assert a["net_usd"] == pytest.approx(-64.0)
    assert a["residual_qty"] == 0.0


def test_a_void_returns_the_stake_and_names_the_fee_assumption():
    """Whether a venue refunds its fee on a voided market is NOT in the
    captured terms. The conservative reading is taken and stated, so a
    reader who learns otherwise knows exactly which line to revise."""
    a = S.accounting(filled_qty=100.0, cost_basis_usd=64.0, fees_usd=2.0,
                     outcome=None, void=True)
    assert a["void"] is True
    assert a["payout_per_contract"] is None
    assert a["stake_returned_usd"] == pytest.approx(62.0)
    assert a["realized_cash_usd"] == pytest.approx(62.0)
    assert a["net_usd"] == pytest.approx(-2.0), "the fee is the whole loss"
    assert "NOT_IN_THE_CAPTURED_TERMS" in a["fee_treatment"]


def test_an_outcome_that_is_neither_zero_nor_one_is_refused():
    with pytest.raises(ValueError):
        S.accounting(filled_qty=1.0, cost_basis_usd=1.0, fees_usd=0.0,
                     outcome=0.5)


# ── the plan, one position at a time ─────────────────────────────────

def test_a_long_position_settles_on_the_venues_own_price():
    got = S.plan(LONG_ROW, _res("1"))
    assert got["status"] == S.S_SETTLED
    assert got["basis"] == S.BASIS_SETTLED
    assert got["side_map"] == loop.SIDE_LONG
    assert got["accounting"]["realized_cash_usd"] == 100.0
    # THE VENUE'S STRING, unparsed, so a later recheck checks the venue's
    # answer and not our float reading of it.
    assert got["settlement_read"] == "1"
    assert got["needed_fresh_odds"] is False


def test_a_short_position_settles_on_one_minus_that_price():
    """THE REVIEW'S SECOND ITEM, reaching the accounting. The venue's YES
    settled at 1; this exposure is its SHORT side, so it pays 0 and the
    whole basis is lost. The uncorrected mapping recorded a full payout."""
    short = dict(LONG_ROW, buy_intent="ORDER_INTENT_BUY_SHORT",
                 ladder_side="BID")
    got = S.plan(short, _res("1"))
    assert got["status"] == S.S_SETTLED
    assert got["side_map"] == loop.SIDE_SHORT
    assert got["accounting"]["payout_per_contract"] == 0.0
    assert got["accounting"]["net_usd"] == pytest.approx(-64.0)
    # AND THE OTHER WAY ROUND.
    other = S.plan(short, _res("0"))
    assert other["accounting"]["payout_per_contract"] == 1.0


def test_a_confirmed_void_is_the_only_thing_treated_as_stakes_returned():
    got = S.plan(LONG_ROW, _res("0.5"))
    assert got["status"] == S.S_VOID
    assert got["basis"] == S.BASIS_VOID
    assert got["accounting"]["void"] is True


def test_a_pending_market_is_not_a_settlement_and_writes_nothing():
    got = S.plan(LONG_ROW, {"status": "PENDING"})
    assert got["status"] == S.S_PENDING
    assert got["accounting"] is None
    assert "has not settled it" in got["why"]


def test_an_unreadable_read_is_reported_as_a_read_failure():
    got = S.plan(LONG_ROW, {"status": "UNREADABLE",
                            "error": "ConnectionError"})
    assert got["status"] == S.S_UNREADABLE
    assert got["accounting"] is None
    assert "ConnectionError" in got["why"]
    assert "not a pending settlement" in got["why"]
    # UNMATCHED IS ALSO NOT PENDING.
    assert S.plan(LONG_ROW, {"status": "UNMATCHED"})["status"] == \
        S.S_UNREADABLE


def test_a_named_winner_does_not_settle_the_position():
    """The venue reported a label, not a price, or its prices merely
    converged. Either way this reader cannot map a team name onto the side
    we hold, and inventing the mapping is worse than reporting it."""
    named = S.plan(LONG_ROW, {"status": "RESOLVED",
                              "outcome": "Houston Astros",
                              "settlement_price": None})
    assert named["status"] == S.S_NOT_AUTHORITATIVE
    assert named["venue_class"] == loop.C_NAMED_WINNER
    assert named["accounting"] is None
    inferred = S.plan(LONG_ROW, {"status": "RESOLVED_DERIVED",
                                 "outcome": "Houston Astros"})
    assert inferred["status"] == S.S_NOT_AUTHORITATIVE
    assert inferred["venue_class"] == loop.C_INFERRED
    assert inferred["accounting"] is None


def test_a_position_whose_venue_side_is_unclear_is_not_settled():
    bad = dict(LONG_ROW, buy_intent="ORDER_INTENT_BUY_SHORT",
               ladder_side="ASK")
    got = S.plan(bad, _res("1"))
    assert got["status"] == S.S_NO_SIDE
    assert got["accounting"] is None


def test_a_position_with_no_venue_contract_has_nothing_to_ask():
    got = S.plan(dict(LONG_ROW, us_market_slug=None), _res("1"))
    assert got["status"] == S.S_NO_SLUG
    assert got["accounting"] is None


def test_a_position_with_no_fills_is_not_settled_as_zero():
    got = S.plan(dict(LONG_ROW, filled_qty=0.0), _res("1"))
    assert got["status"] == S.S_LEDGER_INCOMPLETE
    assert got["accounting"] is None


def test_two_recorded_identities_for_one_condition_refuse():
    """A position that is NOT the entry lane's own decision -- the
    acceptance position, for instance -- has no admissible valuation of
    its own. A valuation for the same condition may name the venue
    contract, but it may describe the OTHER side, and nothing records an
    outcome index to check that against. So agreement is required."""
    ambiguous = dict(LONG_ROW, identity_is_the_own_decision=False,
                     candidate_identities=2)
    got = S.plan(ambiguous, _res("1"))
    assert got["status"] == S.S_NO_SIDE
    assert got["accounting"] is None
    assert "not established" in got["why"]
    # ONE AGREED IDENTITY IS ENOUGH.
    agreed = dict(LONG_ROW, identity_is_the_own_decision=False,
                  candidate_identities=1)
    assert S.plan(agreed, _res("1"))["status"] == S.S_SETTLED
    # AND THE LANE'S OWN DECISION IS EXACT, so it never consults the count.
    own = dict(LONG_ROW, identity_is_the_own_decision=True,
               candidate_identities=9)
    assert S.plan(own, _res("1"))["status"] == S.S_SETTLED


@pytest.mark.asyncio
async def test_the_scheduled_manager_settles_the_acceptance_lane_too(
        monkeypatch):
    """MLB "Final" does not establish that PMUS settled anything. The
    acceptance position is asked of the VENUE on the same schedule as the
    entry lane's, through the same consumer -- and settling it neither
    reseeds it nor touches its provenance."""
    from sportsassets.workers import rn1x_shadow as RS

    seen = {}

    async def _capture(conn, **kw):
        seen.update(kw)
        return {"ran": True, "examined": 0, "settled": 0, "void": 0,
                "already": 0, "unresolved": 0, "errors": 0,
                "by_status": {}, "results": []}

    monkeypatch.setattr(S, "settle_open_positions", _capture)

    async def _no_manage(conn, *, experiment_id):
        return {"experiment_id": experiment_id, "examined": 0}

    monkeypatch.setattr(RS, "manage_open_positions", _no_manage)
    got = await RS.run_continuing_management(
        None, experiment_id=RS.CHALLENGER_EXPERIMENT_ID)
    assert got["settlement"]["ran"] is True
    assert inv.POLICY in seen["policy"]
    assert RS.ACCEPTANCE_POLICY in seen["policy"], (
        "the acceptance position must be settled from the venue, not left "
        "open because an MLB feed said Final")
    assert ext.EXPERIMENT_ID in seen["experiment_id"]
    assert RS.CHALLENGER_EXPERIMENT_ID in seen["experiment_id"]


def test_the_module_states_that_it_needs_no_bookmaker_odds():
    d = S.describe()
    assert d["requires_fresh_bookmaker_odds"] is False
    assert d["submits_orders"] is False
    assert "realized_net_usd" in d["releases_exposure_by"]


# ── against Postgres: the void path and the pending path ─────────────

@pg
@pytest.mark.asyncio
async def test_a_void_read_from_the_venue_closes_the_position(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    from . import test_the_entry_lane_reaches_inventory as L

    conn = await asyncpg.connect(DSN)
    try:
        await L._seed(conn)
        await L._calibrate(conn)
        L._stub(monkeypatch)
        made = await loop.cycle(conn)
        pid = made["entries"][0]["position_id"]
        acct = made["entries"][0]["accounting"]

        # A market that settled at neither side. THE VENUE'S OWN
        # settlement endpoint said so -- which is the only thing taken as
        # a void.
        got = await S.settle_open_positions(
            conn, experiment_id=ext.EXPERIMENT_ID, policy=inv.POLICY,
            now=time.time(),
            read_resolution=lambda slug: _res("0.5", settled_at=None))
        assert got["void"] == 1, got
        assert got["settled"] == 0
        row = await conn.fetchrow(
            "SELECT outcome_basis, residual_qty::float8 AS r, "
            "net_usd::float8 AS net, realized_cash_usd::float8 AS cash "
            "FROM rn1x_outcomes WHERE position_id = $1", pid)
        assert row["outcome_basis"] == S.BASIS_VOID
        assert row["r"] == 0.0
        # STAKES BACK, FEES GONE: the net is exactly the fees paid.
        assert row["net"] == pytest.approx(-acct["fees_usd"], rel=1e-6)
        assert row["cash"] == pytest.approx(
            acct["cost_basis_usd"] - acct["fees_usd"], rel=1e-6)
        # AND THE EXPOSURE IS RELEASED, because the outcome row is what
        # releases it -- no second write, no separate flag.
        from sportsassets import bettor_entry_execution as entryx

        rows = await loop.open_shadow_book(conn, ext.EXPERIMENT_ID)
        exp = entryx.exposure_from_rows(
            rows, condition_id="other", event_key="other",
            proposed_cost_usd=10.0, proposed_qty=10.0, now=time.time())
        assert exp["settled_positions_excluded_from_exposure"] >= 1
        assert exp["observed"]["MAX_CAPITAL_DEPLOYED"] == pytest.approx(10.0)
    finally:
        await L._cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_pending_venue_leaves_the_position_open_and_says_why(
        monkeypatch):
    """AND IT STAYS IN THE QUEUE. An unsettled position must be asked
    about again next cycle, which is the difference between "not yet" and
    "handled"."""
    asyncpg = pytest.importorskip("asyncpg")
    from . import test_the_entry_lane_reaches_inventory as L

    conn = await asyncpg.connect(DSN)
    try:
        await L._seed(conn)
        await L._calibrate(conn)
        L._stub(monkeypatch)
        made = await loop.cycle(conn)
        pid = made["entries"][0]["position_id"]

        for _ in range(2):
            got = await S.settle_open_positions(
                conn, experiment_id=ext.EXPERIMENT_ID, policy=inv.POLICY,
                now=time.time(),
                read_resolution=lambda slug: {"status": "PENDING"})
            assert got["examined"] == 1, got
            assert got["settled"] == 0 and got["void"] == 0
            assert got["unresolved"] == 1
            assert got["by_status"][S.S_PENDING] == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_outcomes WHERE position_id=$1",
            pid) == 0
    finally:
        await L._cleanup(conn)
        await conn.close()
