"""SETTLEMENT SETTLES THE RESIDUAL, AND ONLY WHAT THE VENUE ESTABLISHED.

The lifecycle test pins the happy path end to end through
`run_continuing_management`. These pin what it does not reach, and they
pin the four defects a review of the first version found. All four are
mine, and each one is reproduced here as its own test before the repair is
asserted.

  1 IT SETTLED GROSS FILLS. The review's arithmetic, fees zero to isolate
    it: buy 100 at .60, sell 40 at .80, remaining 60 win. Correct total
    cash $92 and net $32; it reported $140 and $80. `test_the_reviews_
    counterexample_*` is that exact case.

  2 IT GUESSED THE CONTRACT. "The latest admissible valuation for the same
    condition" is not a link to the decision that opened the position and
    never checked which SIDE the valuation described.
    `test_a_valuation_for_the_wrong_side_*` is the case where the only
    identity available describes the other outcome.

  3 IT READ ANY NONBINARY PRICE AS A REFUND. `test_a_nonbinary_price_*`.

  4 A SETTLED POSITION STAYED IN MANAGEMENT.
    `test_a_settled_position_leaves_recurring_management`.
"""

from __future__ import annotations

import os
import time

import pytest

from sportsassets import bettor_entry_inventory as inv
from sportsassets import bettor_entry_settlement as S
from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_rn1x_store as store
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

COND = "c1"
POS = {"position_id": "P1", "condition_id": COND, "outcome_index": 0,
       "policy": "POL", "experiment_id": "EXP",
       "seed_qty": 100.0, "seed_price": 0.60, "seed_basis_usd": 60.0,
       "decision_ts": 0.0,
       "venue_market_slug": "aec-mlb-x",
       "venue_buy_intent": "ORDER_INTENT_BUY_LONG",
       "venue_ladder_side": "ASK",
       "payout_event": "Houston Astros",
       "source_valuation_id": 7}

#: The identity `held_identity` would return for POS: the catalogue's
#: outcome at the position's own index, agreeing with what the position
#: recorded for itself.
IDENT = {"held_outcome": "Houston Astros", "held_token_id": "t0",
         "outcomes_listed": 2, "source": "PERSISTED_ON_THE_POSITION",
         "us_market_slug": "aec-mlb-x",
         "buy_intent": "ORDER_INTENT_BUY_LONG", "ladder_side": "ASK",
         "payout_event": "Houston Astros", "valuation_id": 7,
         "candidates": 1, "refusal": None, "why": None}


def _orders(*, sell=True, comp=False):
    out = [{"order_id": "o1", "condition_id": COND, "outcome_index": 0,
            "side": "BUY"}]
    if sell:
        out.append({"order_id": "o2", "condition_id": COND,
                    "outcome_index": 0, "side": "SELL"})
    if comp:
        out.append({"order_id": "o3", "condition_id": COND,
                    "outcome_index": 1, "side": "BUY"})
    return out


def _res(price_raw, *, status="RESOLVED", settled_at=None, **extra):
    got = {"status": status, "settlement_price_raw": price_raw,
           "settlement_price": (None if price_raw is None
                                else float(price_raw)),
           "outcome": price_raw, "settled_at": settled_at}
    got.update(extra)
    return got


# ── 1 · THE RESIDUAL, NOT THE GROSS FILLS ────────────────────────────

def test_the_reviews_counterexample_partial_exit_then_win():
    """Buy 100 at .60, sell 40 at .80, remaining 60 win.

    Total cash $92, net $32. The broken version reported $140 and $80: it
    paid the settlement on contracts already sold and then forgot the sale
    proceeds entirely.
    """
    fills = [{"fill_id": "f1", "order_id": "o1", "at": 1, "qty": 100,
              "price": 0.60, "fee_usd": 0.0},
             {"fill_id": "f2", "order_id": "o2", "at": 2, "qty": 40,
              "price": 0.80, "fee_usd": 0.0}]
    r = S.replay(POS, _orders(), fills)
    assert r["portfolio"]._leg(COND, 0)["qty"] == pytest.approx(60.0)
    assert r["exit_proceeds_usd"] == pytest.approx(32.0)
    a = S.accounting(r, condition_id=COND, held_index=0, payout=1.0)
    assert a["residual_qty_settled"] == pytest.approx(60.0), (
        "settlement applies to the 60 still held, not to the 100 ever bought")
    assert a["settlement_cash_usd"] == pytest.approx(60.0)
    assert a["prior_exit_proceeds_usd"] == pytest.approx(32.0)
    assert a["total_cash_returned_usd"] == pytest.approx(92.0)
    assert a["net_usd"] == pytest.approx(32.0)
    assert a["residual_qty"] == 0.0
    assert a["reconciles"] is True
    # AND THE NUMBERS THE BROKEN VERSION PRODUCED ARE NOT THESE.
    assert a["total_cash_returned_usd"] != pytest.approx(140.0)
    assert a["net_usd"] != pytest.approx(80.0)


def test_partial_exit_then_loss_keeps_the_exit_proceeds():
    """The same book, the other result. The 40 sold at .80 still brought
    in $32; only the 60 still held are lost."""
    fills = [{"fill_id": "f1", "order_id": "o1", "at": 1, "qty": 100,
              "price": 0.60, "fee_usd": 0.0},
             {"fill_id": "f2", "order_id": "o2", "at": 2, "qty": 40,
              "price": 0.80, "fee_usd": 0.0}]
    a = S.accounting(S.replay(POS, _orders(), fills),
                     condition_id=COND, held_index=0, payout=0.0)
    assert a["settlement_cash_usd"] == pytest.approx(0.0)
    assert a["total_cash_returned_usd"] == pytest.approx(32.0)
    assert a["net_usd"] == pytest.approx(-28.0)


def test_fees_are_carried_through_settlement_not_dropped():
    fills = [{"fill_id": "f1", "order_id": "o1", "at": 1, "qty": 100,
              "price": 0.60, "fee_usd": 1.50},
             {"fill_id": "f2", "order_id": "o2", "at": 2, "qty": 40,
              "price": 0.80, "fee_usd": 0.40}]
    a = S.accounting(S.replay(POS, _orders(), fills),
                     condition_id=COND, held_index=0, payout=1.0)
    assert a["fees_usd"] == pytest.approx(1.90)
    # NET IS THE SAME $32 LESS EVERY FEE CHARGED, entry and exit alike.
    assert a["net_usd"] == pytest.approx(32.0 - 1.90)
    assert a["reconciles"] is True


def test_a_fully_exited_position_has_nothing_to_settle():
    """No residual means no payout to apply. Writing a settlement here
    would book cash against inventory that was already sold."""
    fills = [{"fill_id": "f1", "order_id": "o1", "at": 1, "qty": 100,
              "price": 0.60, "fee_usd": 0.0},
             {"fill_id": "f2", "order_id": "o2", "at": 2, "qty": 100,
              "price": 0.80, "fee_usd": 0.0}]
    r = S.replay(POS, _orders(), fills)
    assert r["portfolio"]._leg(COND, 0)["qty"] == pytest.approx(0.0)
    got = S.plan(POS, IDENT, r, _res("1"))
    assert got["status"] == S.S_NOTHING_HELD
    assert got["accounting"] is None
    assert got["realized_pnl_usd"] == pytest.approx(20.0)
    # A SEED FALLBACK MUST NOT RESURRECT IT. The position was opened by
    # fills, so the seed is not an alternative source of inventory.
    assert r["opening_basis"] == S.OPEN_FROM_FILLS


def test_the_acceptance_positions_assigned_inventory_settles():
    """It was ASSIGNED inventory by a seeder, not filled by an order, so
    requiring an acquisition fill would refuse to settle inventory that
    demonstrably exists. The basis of the opening is reported rather than
    implied."""
    r = S.replay(POS, [], [])
    assert r["opening_basis"] == S.OPEN_FROM_SEED
    assert r["portfolio"]._leg(COND, 0)["qty"] == pytest.approx(100.0)
    assert r["entry_outlay_usd"] == pytest.approx(60.0)
    a = S.accounting(r, condition_id=COND, held_index=0, payout=1.0)
    assert a["opening_basis"] == S.OPEN_FROM_SEED
    assert a["settlement_cash_usd"] == pytest.approx(100.0)
    assert a["net_usd"] == pytest.approx(40.0)


def test_a_pmus_complement_leg_is_settled_apart_never_netted():
    """An exit executed as a BUY of the complement is not a sell.
    `bettor_inventory.COMPLEMENT_IS_NOT_A_SELL` -- so both legs are held,
    both are settled, and exactly one pays."""
    fills = [{"fill_id": "f1", "order_id": "o1", "at": 1, "qty": 100,
              "price": 0.60, "fee_usd": 0.0},
             {"fill_id": "f3", "order_id": "o3", "at": 2, "qty": 40,
              "price": 0.30, "fee_usd": 0.0}]
    r = S.replay(POS, _orders(sell=False, comp=True), fills)
    assert r["portfolio"]._leg(COND, 0)["qty"] == pytest.approx(100.0)
    assert r["portfolio"]._leg(COND, 1)["qty"] == pytest.approx(40.0), (
        "the complement is a second leg, not a reduction of the first")
    got = S.plan(POS, IDENT, r, _res("1"))
    assert got["status"] == S.S_SETTLED
    a = got["accounting"]
    assert len(a["settled_legs"]) == 2
    held = [l for l in a["settled_legs"] if l["is_the_held_leg"]][0]
    comp = [l for l in a["settled_legs"] if not l["is_the_held_leg"]][0]
    assert held["payout_per_contract"] == 1.0
    assert comp["payout_per_contract"] == 0.0, "exactly one side pays"
    # OUTLAY 60 + 12 = 72; the held 100 pay 100. Net 28.
    assert a["entry_outlay_usd"] == pytest.approx(72.0)
    assert a["settlement_cash_usd"] == pytest.approx(100.0)
    assert a["net_usd"] == pytest.approx(28.0)
    assert a["reconciles"] is True


def test_a_second_leg_with_no_confirmed_complement_is_refused():
    """A pair is a claim the identity layer makes, not an arithmetic fact.
    On a catalogue that lists three outcomes, holding index 1 beside index
    0 does not make them complements."""
    fills = [{"fill_id": "f1", "order_id": "o1", "at": 1, "qty": 100,
              "price": 0.60, "fee_usd": 0.0},
             {"fill_id": "f3", "order_id": "o3", "at": 2, "qty": 40,
              "price": 0.30, "fee_usd": 0.0}]
    r = S.replay(POS, _orders(sell=False, comp=True), fills)
    three_way = dict(IDENT, outcomes_listed=3)
    got = S.plan(POS, three_way, r, _res("1"))
    assert got["status"] == S.S_UNPAIRED_LEG
    assert got["accounting"] is None
    assert "not established as complements" in got["why"]


def test_a_fill_whose_order_is_missing_refuses_rather_than_guesses():
    fills = [{"fill_id": "f9", "order_id": "GONE", "at": 1, "qty": 10,
              "price": 0.5, "fee_usd": 0.0}]
    r = S.replay(POS, _orders(), fills)
    assert r["unknown_order_fills"] == 1
    got = S.plan(POS, IDENT, r, _res("1"))
    assert got["status"] == S.S_LEDGER_INCOMPLETE
    assert got["accounting"] is None


# ── 2 · BOUND TO THIS POSITION'S EXPOSURE ────────────────────────────

def test_a_short_position_settles_on_one_minus_the_price():
    short_pos = dict(POS, venue_buy_intent="ORDER_INTENT_BUY_SHORT",
                     venue_ladder_side="BID")
    short_id = dict(IDENT, buy_intent="ORDER_INTENT_BUY_SHORT",
                    ladder_side="BID")
    r = S.replay(short_pos, [], [])
    got = S.plan(short_pos, short_id, r, _res("1"))
    assert got["status"] == S.S_SETTLED
    assert got["side_map"] == loop.SIDE_SHORT
    assert got["accounting"]["payout_per_contract"] == 0.0
    assert got["accounting"]["net_usd"] == pytest.approx(-60.0)


def test_an_identity_refusal_stops_the_settlement_before_accounting():
    for code in (S.S_NO_SLUG, S.S_WRONG_SIDE, S.S_NO_HELD_EVENT):
        bad = dict(IDENT, refusal=code, why="because")
        got = S.plan(POS, bad, S.replay(POS, [], []), _res("1"))
        assert got["status"] == code
        assert got["accounting"] is None


def test_a_position_whose_own_row_contradicts_itself_is_not_settled():
    """`payout_event` and `outcome_index` are deliberately redundant so
    they can be checked against each other. A row whose recorded payout
    event is not the outcome at its own index is not settled either way."""
    # Exercised through held_identity's logic via the DB test below; here
    # the contract is that plan honours the refusal it produces.
    got = S.plan(POS, dict(IDENT, refusal=S.S_WRONG_SIDE,
                           why="row contradicts itself"),
                 S.replay(POS, [], []), _res("1"))
    assert got["status"] == S.S_WRONG_SIDE


def test_a_venue_side_that_is_not_established_refuses_rather_than_guesses():
    got = S.plan(POS, dict(IDENT, buy_intent="ORDER_INTENT_BUY_SHORT",
                           ladder_side="ASK"),
                 S.replay(POS, [], []), _res("1"))
    assert got["status"] == S.S_NO_SIDE
    assert got["accounting"] is None


# ── 3 · A NONBINARY PRICE IS NOT A REFUND ────────────────────────────

def test_a_nonbinary_price_does_not_establish_a_refund():
    """THE REVIEW'S THIRD ITEM. 0.5 with no void or refund evidence.

    The broken version called it CONFIRMED_VOID, assumed the original
    stake came back, labelled the result authoritative and closed the
    inventory. Naming the fee treatment conservative did not establish the
    cash entitlement.
    """
    r = S.replay(POS, [], [])
    got = S.plan(POS, IDENT, r, _res("0.5"))
    assert got["status"] == S.S_NO_REFUND_ESTABLISHED
    assert got["venue_class"] == loop.C_NEITHER_SIDE_PAID
    assert got["accounting"] is None, "nothing is written, nothing released"
    assert got["basis"] is None
    assert got["void_evidence"]["declared"] is False
    assert "establishes none of them" in got["why"]


def test_a_declared_void_is_the_only_thing_treated_as_a_refund():
    """When the venue DOES state it, the refund is the residual's own
    average cost -- not the seed basis and not the gross notional."""
    fills = [{"fill_id": "f1", "order_id": "o1", "at": 1, "qty": 100,
              "price": 0.60, "fee_usd": 2.0},
             {"fill_id": "f2", "order_id": "o2", "at": 2, "qty": 40,
              "price": 0.80, "fee_usd": 0.0}]
    r = S.replay(POS, _orders(), fills)
    got = S.plan(POS, IDENT, r, _res("0.5", voided=True))
    assert got["status"] == S.S_VOID
    assert got["basis"] == S.BASIS_VOID
    a = got["accounting"]
    assert a["void"] is True
    assert a["void_evidence"]["field"] == "voided"
    # 60 held at an average cost of .60 comes back as $36.
    assert a["settlement_cash_usd"] == pytest.approx(36.0)
    assert "FEE" in a["fee_treatment"]


def test_a_status_field_that_says_void_counts_and_one_that_denies_it_does_not():
    r = S.replay(POS, [], [])
    assert S.plan(POS, IDENT, r,
                  _res("0.5", settlementStatus="VOID"))["status"] == S.S_VOID
    # A SUBSTRING MATCH WOULD READ THIS AS A VOID. It is not one.
    denied = S.plan(POS, IDENT, S.replay(POS, [], []),
                    _res("0.5", settlementStatus="NOT_VOIDED"))
    assert denied["status"] == S.S_NO_REFUND_ESTABLISHED
    assert denied["void_evidence"]["declared"] is False
    assert "settlementStatus" in denied["void_evidence"]["fields_present"]


def test_void_accounting_refuses_to_run_without_declared_evidence():
    with pytest.raises(ValueError):
        S.void_accounting(S.replay(POS, [], []), condition_id=COND,
                          held_index=0, void_evidence={"declared": False})


def test_the_named_winner_and_inferred_cases_still_settle_nothing():
    r = S.replay(POS, [], [])
    named = S.plan(POS, IDENT, r, {"status": "RESOLVED",
                                   "outcome": "Houston Astros",
                                   "settlement_price": None})
    assert named["status"] == S.S_NOT_AUTHORITATIVE
    assert named["venue_class"] == loop.C_NAMED_WINNER
    inferred = S.plan(POS, IDENT, S.replay(POS, [], []),
                      {"status": "RESOLVED_DERIVED",
                       "outcome": "Houston Astros"})
    assert inferred["status"] == S.S_NOT_AUTHORITATIVE
    assert inferred["venue_class"] == loop.C_INFERRED


def test_pending_and_unreadable_stay_different_answers():
    r = S.replay(POS, [], [])
    assert S.plan(POS, IDENT, r,
                  {"status": "PENDING"})["status"] == S.S_PENDING
    bad = S.plan(POS, IDENT, S.replay(POS, [], []),
                 {"status": "UNREADABLE", "error": "ConnectionError"})
    assert bad["status"] == S.S_UNREADABLE
    assert "ConnectionError" in bad["why"]


# ── 4 · THE TERMINAL BASES, AND WHAT THEY EXCLUDE ────────────────────

def test_the_terminal_bases_are_declared_once_and_shared():
    assert S.BASIS_SETTLED == store.BASIS_VENUE_SETTLED
    assert S.BASIS_VOID == store.BASIS_VENUE_VOID
    assert S.TERMINAL_BASES == store.TERMINAL_OUTCOME_BASES
    # SCORING-ONLY IS NOT TERMINAL. Excluding it would stop managing a
    # live challenger position the moment an observation was recorded.
    assert "OBSERVED_PAYOUT_SCORING_ONLY" not in S.TERMINAL_BASES
    # AND BOTH QUERIES CARRY THE SAME CONDITION, from the same constant.
    assert store.NOT_TERMINALLY_SETTLED in store.OPEN_POSITIONS_SQL
    assert store.NOT_TERMINALLY_SETTLED in S.OPEN_ENTRY_SQL


def test_the_module_states_what_it_does_and_does_not_require():
    d = S.describe()
    assert d["requires_fresh_bookmaker_odds"] is False
    assert d["submits_orders"] is False
    assert d["settles"] == "THE_RESIDUAL_NOT_THE_GROSS_FILLS"
    assert "DECLARE" in d["void_requires"]
    assert "Portfolio" in d["accounting"]


@pytest.mark.asyncio
async def test_the_scheduled_manager_settles_the_acceptance_lane_too(
        monkeypatch):
    """MLB "Final" does not establish that PMUS settled anything. The
    acceptance position is asked of the VENUE on the same schedule as the
    entry lane's, through the same consumer."""
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
    assert RS.ACCEPTANCE_POLICY in seen["policy"]
    assert ext.EXPERIMENT_ID in seen["experiment_id"]
    assert RS.CHALLENGER_EXPERIMENT_ID in seen["experiment_id"]


# ── against Postgres ─────────────────────────────────────────────────

@pg
@pytest.mark.asyncio
async def test_a_valuation_for_the_wrong_side_refuses_rather_than_settles(
        monkeypatch):
    """THE REVIEW'S SECOND ITEM, end to end.

    The position holds index 0. The ONLY venue identity recorded for its
    condition names the OTHER outcome -- which is what a valuation for the
    away side looks like. Settling against it would record the opposite
    result, and the "one distinct identity" count would have called it
    unambiguous.
    """
    asyncpg = pytest.importorskip("asyncpg")
    from . import test_the_entry_lane_reaches_inventory as L

    conn = await asyncpg.connect(DSN)
    try:
        await L._seed(conn)
        await L._calibrate(conn)
        L._stub(monkeypatch)
        made = await loop.cycle(conn)
        pid = made["entries"][0]["position_id"]

        # STRIP THE POSITION'S OWN IDENTITY, so the consumer has to fall
        # back to a candidate -- the state every position created before
        # migration 119 is in.
        await conn.execute(
            "UPDATE rn1x_positions SET venue_market_slug = NULL, "
            "venue_buy_intent = NULL, venue_ladder_side = NULL, "
            "payout_event = NULL, source_valuation_id = NULL "
            "WHERE position_id = $1", pid)
        # AND MAKE EVERY RECORDED IDENTITY DESCRIBE THE OTHER SIDE.
        await conn.execute(
            "UPDATE external_valuations SET payout_event = $2 "
            "WHERE condition_id = $1", L.CONDITION, "Seattle Mariners")

        row = await conn.fetchrow(
            S.OPEN_ENTRY_SQL, [ext.EXPERIMENT_ID], [inv.POLICY], 5)
        assert row is not None and row["position_id"] == pid
        ident = await S.held_identity(conn, dict(row))
        assert ident["held_outcome"] == "Houston Astros"
        assert ident["refusal"] == S.S_WRONG_SIDE, ident
        assert "none of them names the outcome" in ident["why"]

        got = await S.settle_open_positions(
            conn, experiment_id=ext.EXPERIMENT_ID, policy=inv.POLICY,
            now=time.time(),
            read_resolution=lambda slug: pytest.fail(
                "the venue must not be asked for a position whose held "
                "side is not established"))
        assert got["settled"] == 0 and got["void"] == 0
        assert got["by_status"][S.S_WRONG_SIDE] == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_outcomes WHERE position_id=$1",
            pid) == 0
    finally:
        await L._cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_positions_own_identity_is_used_and_cross_checked(
        monkeypatch):
    """With migration 119 the entry writer records the contract and the
    side it opened, and `held_identity` confirms them against
    `market_tokens` instead of searching for a valuation."""
    asyncpg = pytest.importorskip("asyncpg")
    from . import test_the_entry_lane_reaches_inventory as L

    conn = await asyncpg.connect(DSN)
    try:
        await L._seed(conn)
        await L._calibrate(conn)
        L._stub(monkeypatch)
        made = await loop.cycle(conn)
        pid = made["entries"][0]["position_id"]
        row = await conn.fetchrow(
            "SELECT venue_market_slug, venue_buy_intent, venue_ladder_side, "
            "payout_event, source_valuation_id FROM rn1x_positions "
            "WHERE position_id = $1", pid)
        assert row["venue_market_slug"] == L.US_SLUG
        assert row["venue_buy_intent"] == "ORDER_INTENT_BUY_LONG"
        assert row["venue_ladder_side"] == "ASK"
        assert row["payout_event"] == "Houston Astros"
        assert row["source_valuation_id"] is not None

        got = await S.held_identity(conn, dict(await conn.fetchrow(
            S.OPEN_ENTRY_SQL, [ext.EXPERIMENT_ID], [inv.POLICY], 5)))
        assert got["refusal"] is None, got
        assert got["source"] == "PERSISTED_ON_THE_POSITION"
        assert got["held_outcome"] == "Houston Astros"
        assert got["us_market_slug"] == L.US_SLUG

        # A ROW THAT CONTRADICTS ITSELF IS NOT SETTLED EITHER WAY.
        await conn.execute(
            "UPDATE rn1x_positions SET payout_event = 'Seattle Mariners' "
            "WHERE position_id = $1", pid)
        bad = await S.held_identity(conn, dict(await conn.fetchrow(
            S.OPEN_ENTRY_SQL, [ext.EXPERIMENT_ID], [inv.POLICY], 5)))
        assert bad["refusal"] == S.S_WRONG_SIDE
        assert "contradicts itself" in bad["why"]
    finally:
        await L._cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_settled_position_leaves_recurring_management(monkeypatch):
    """THE REVIEW'S FOURTH ITEM. Settle once through the production
    consumer, then prove the next scheduled cycle does not select it, the
    exposure is released, and a restart preserves both."""
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_entry_execution as entryx
    from sportsassets.workers import rn1x_shadow as RS
    from . import test_the_entry_lane_reaches_inventory as L

    conn = await asyncpg.connect(DSN)
    try:
        await L._seed(conn)
        await L._calibrate(conn)
        L._stub(monkeypatch)
        made = await loop.cycle(conn)
        pid = made["entries"][0]["position_id"]
        acct = made["entries"][0]["accounting"]

        # BEFORE: the manager selects it and the rails count it.
        opened = await store.open_positions(
            conn, experiment_id=ext.EXPERIMENT_ID, limit=10)
        assert pid in [o["position_id"] for o in opened]

        got = await S.settle_open_positions(
            conn, experiment_id=ext.EXPERIMENT_ID, policy=inv.POLICY,
            now=time.time(),
            read_resolution=lambda slug: _res(
                "1", settled_at="2026-09-24T23:14:07Z"))
        assert got["settled"] == 1, got
        res = got["results"][0]
        assert res["status"] == S.S_SETTLED and res["written"] is True
        # THE RESIDUAL SETTLED IS THE QUANTITY ACTUALLY HELD.
        assert res["accounting"]["residual_qty_settled"] == pytest.approx(
            acct["filled_qty"])
        assert res["accounting"]["net_usd"] == pytest.approx(
            acct["filled_qty"] - acct["cost_basis_usd"], rel=1e-6)

        # AFTER: the manager does not select it.
        opened2 = await store.open_positions(
            conn, experiment_id=ext.EXPERIMENT_ID, limit=10)
        assert pid not in [o["position_id"] for o in opened2], (
            "a terminally settled position must leave recurring management")

        # AND THE SCHEDULED MANAGER AGREES.
        again = await RS.run_continuing_management(
            conn, experiment_id=RS.CHALLENGER_EXPERIMENT_ID)
        el = again["entry_lane"]
        assert not [r for r in (el.get("results") or [])
                    if r.get("position_id") == pid]

        # EXPOSURE RELEASED, by the outcome row alone.
        rows = await loop.open_shadow_book(conn, ext.EXPERIMENT_ID)
        exp = entryx.exposure_from_rows(
            rows, condition_id="other", event_key="other",
            proposed_cost_usd=10.0, proposed_qty=10.0, now=time.time())
        assert exp["settled_positions_excluded_from_exposure"] >= 1
        assert exp["observed"]["MAX_CAPITAL_DEPLOYED"] == pytest.approx(10.0)

        # RESTART: a new connection is a new process for these purposes.
        written_at = await conn.fetchval(
            "SELECT written_at FROM rn1x_outcomes WHERE position_id=$1", pid)
        await conn.close()
        conn = await asyncpg.connect(DSN)
        twice = await S.settle_open_positions(
            conn, experiment_id=ext.EXPERIMENT_ID, policy=inv.POLICY,
            now=time.time(),
            read_resolution=lambda slug: pytest.fail(
                "a settled position must not be re-read"))
        assert twice["examined"] == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_outcomes WHERE position_id=$1",
            pid) == 1
        assert await conn.fetchval(
            "SELECT written_at FROM rn1x_outcomes WHERE position_id=$1",
            pid) == written_at
        assert pid not in [o["position_id"] for o in await
                           store.open_positions(
                               conn, experiment_id=ext.EXPERIMENT_ID,
                               limit=10)]
    finally:
        await L._cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_scoring_only_outcome_does_not_remove_a_live_position(
        monkeypatch):
    """The other direction, and it matters as much. A payout recorded FOR
    SCORING is not a settlement, and a position carrying one must still be
    managed."""
    asyncpg = pytest.importorskip("asyncpg")
    from . import test_the_entry_lane_reaches_inventory as L

    conn = await asyncpg.connect(DSN)
    try:
        await L._seed(conn)
        await L._calibrate(conn)
        L._stub(monkeypatch)
        made = await loop.cycle(conn)
        pid = made["entries"][0]["position_id"]
        await conn.execute(
            "INSERT INTO rn1x_outcomes (position_id, settled_at, "
            "payout_per_leg, realized_cash_usd, fees_usd, residual_qty, "
            "unpaired_qty, net_usd, outcome_basis) VALUES "
            "($1, now(), '{}'::jsonb, 0, 0, 0, 0, 0, "
            "'OBSERVED_PAYOUT_SCORING_ONLY')", pid)
        opened = await store.open_positions(
            conn, experiment_id=ext.EXPERIMENT_ID, limit=10)
        assert pid in [o["position_id"] for o in opened], (
            "a scoring observation is not a settlement")
        # AND THE SETTLEMENT CONSUMER STILL SEES IT AS OPEN.
        rows = await conn.fetch(S.OPEN_ENTRY_SQL, [ext.EXPERIMENT_ID],
                                [inv.POLICY], 5)
        assert pid in [r["position_id"] for r in rows]
    finally:
        await L._cleanup(conn)
        await conn.close()
