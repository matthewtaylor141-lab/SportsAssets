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
       # ACQUIRED BY OUR OWN EXECUTION, on a netting venue. Both facts are
       # READ from the row, never inferred: the provenance decides whether
       # the seed is the opening, and the venue decides whether an
       # opposite-side fill is a second leg or a reduction.
       "provenance": "AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW",
       "venue": "PMUS",
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

ASSIGNED_POS = dict(POS, position_id="A1",
                    provenance="ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY")

#: The acquisition fill an ACQUIRED position must have in its ledger.
BUY_100 = {"fill_id": "f0", "order_id": "o1", "at": 1, "qty": 100,
           "price": 0.60, "fee_usd": 0.0}


def test_the_reviews_counterexample_partial_exit_then_win():
    """Buy 100 at .60, sell 40 at .80, remaining 60 win.

    Total cash $92, net $32. The broken version reported $140 and $80: it
    paid the settlement on contracts already sold and then forgot the sale
    proceeds entirely.
    """
    fills = [BUY_100,
             {"fill_id": "f2", "order_id": "o2", "at": 2, "qty": 40,
              "price": 0.80, "fee_usd": 0.0}]
    r = S.replay(POS, _orders(), fills)
    assert r["ok"] is True, r
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
    fills = [BUY_100,
             {"fill_id": "f2", "order_id": "o2", "at": 2, "qty": 40,
              "price": 0.80, "fee_usd": 0.0}]
    a = S.accounting(S.replay(POS, _orders(), fills),
                     condition_id=COND, held_index=0, payout=0.0)
    assert a["settlement_cash_usd"] == pytest.approx(0.0)
    assert a["total_cash_returned_usd"] == pytest.approx(32.0)
    assert a["net_usd"] == pytest.approx(-28.0)


def test_fees_are_carried_through_settlement_not_dropped():
    fills = [dict(BUY_100, fee_usd=1.50),
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
    fills = [BUY_100,
             {"fill_id": "f2", "order_id": "o2", "at": 2, "qty": 100,
              "price": 0.80, "fee_usd": 0.0}]
    r = S.replay(POS, _orders(), fills)
    assert r["ok"] is True
    assert r["portfolio"]._leg(COND, 0)["qty"] == pytest.approx(0.0)
    got = S.plan(POS, IDENT, r, _res("1"))
    assert got["status"] == S.S_NOTHING_HELD
    assert got["accounting"] is None
    assert got["realized_pnl_usd"] == pytest.approx(20.0)
    # A SEED FALLBACK MUST NOT RESURRECT IT. The position was opened by
    # fills, so the seed is not an alternative source of inventory.
    assert r["opening_basis"] == S.OPEN_FROM_FILLS


def test_assigned_inventory_is_established_once_before_any_exit():
    """THE REVIEW'S SECOND ITEM, and the review's own numbers.

    Assigned 100 at $0.60, sell 40 at $0.80, settle the remaining 60:
    total proceeds $92, net $32 before fees.

    The broken version applied the seed AFTER replaying the fills and
    decided "assigned" by counting BUY fills. An assigned position that
    has sold part of its inventory has SELL fills and NO BUY fills, so the
    sell was replayed against an empty book and `Portfolio.sell` raised --
    this case could not run at all.
    """
    fills = [{"fill_id": "f2", "order_id": "o2", "at": 2, "qty": 40,
              "price": 0.80, "fee_usd": 0.0}]
    r = S.replay(ASSIGNED_POS, _orders(), fills)
    assert r["ok"] is True, r
    # THE OPENING IS READ FROM PROVENANCE, not inferred from the ledger.
    assert r["opening_mode"] == S.ASSIGNED
    assert r["opening_basis"] == S.OPEN_FROM_SEED
    assert r["buys"] == 0, "no acquisition fill of ours exists"
    # INTERMEDIATE INVENTORY AND CASH, not just the final P&L.
    leg = r["portfolio"]._leg(COND, 0)
    assert leg["qty"] == pytest.approx(60.0)
    assert leg["cost"] == pytest.approx(36.0)
    assert r["portfolio"].cash == pytest.approx(-28.0)
    assert r["portfolio"].fees == pytest.approx(0.0)
    assert r["entry_outlay_usd"] == pytest.approx(60.0)
    assert r["direct_exit_proceeds_usd"] == pytest.approx(32.0)
    a = S.accounting(r, condition_id=COND, held_index=0, payout=1.0)
    assert a["residual_qty_settled"] == pytest.approx(60.0)
    assert a["settlement_cash_usd"] == pytest.approx(60.0)
    assert a["total_cash_returned_usd"] == pytest.approx(92.0)
    assert a["net_usd"] == pytest.approx(32.0)
    assert a["reconciles"] is True


def test_an_assigned_position_is_seeded_exactly_once():
    """Two exits in sequence must draw down ONE opening, not re-seed."""
    fills = [{"fill_id": "fa", "order_id": "o2", "at": 2, "qty": 30,
              "price": 0.70, "fee_usd": 0.0},
             {"fill_id": "fb", "order_id": "o2", "at": 3, "qty": 30,
              "price": 0.90, "fee_usd": 0.0}]
    r = S.replay(ASSIGNED_POS, _orders(), fills)
    assert r["portfolio"]._leg(COND, 0)["qty"] == pytest.approx(40.0)
    assert r["entry_outlay_usd"] == pytest.approx(60.0), (
        "the seed is the opening once, not once per exit")


def test_an_acquired_position_with_no_acquisition_fill_is_refused():
    """NOT seeded as a fallback. The provenance says our own execution is
    in the ledger; if it is not, that is a ledger fault, and assigning
    inventory to cover it would invent a position."""
    r = S.replay(POS, [], [])
    assert r["ok"] is False
    assert r["refusal"] == S.R_NO_ACQUISITION
    got = S.plan(POS, IDENT, r, _res("1"))
    assert got["status"] == S.R_NO_ACQUISITION
    assert got["accounting"] is None


def test_an_undeclared_provenance_or_venue_refuses_rather_than_defaults():
    prov = S.replay(dict(POS, provenance="SOMETHING_NEW"), _orders(),
                    [BUY_100])
    assert prov["ok"] is False
    assert prov["refusal"] == S.R_PROVENANCE_UNKNOWN
    ven = S.replay(dict(POS, venue="A_VENUE_WE_HAVE_NOT_MODELLED"),
                   _orders(), [BUY_100])
    assert ven["ok"] is False
    assert ven["refusal"] == S.R_VENUE_UNKNOWN
    # AND NEITHER REACHES THE ACCOUNTING.
    for r in (prov, ven):
        assert S.plan(POS, IDENT, r, _res("1"))["accounting"] is None


#: The opposite-side acquisition both venue models see, and disagree about.
COMP_40 = {"fill_id": "f3", "order_id": "o3", "at": 2, "qty": 40,
           "price": 0.30, "fee_usd": 0.0}


def test_on_pmus_an_opposite_side_acquisition_reduces_the_signed_position():
    """THE REVIEW'S FIRST ITEM, and its numbers. THE TEST THIS REPLACES
    ASSERTED THE OPPOSITE, which was worse than having no test: it made
    the defect look deliberate.

    PMUS keeps ONE SIGNED netPosition per market slug --
    docs/rn1-two-sided-design.md §1, "Buying the complement of something
    you hold is not a second position; it is a sale of the first". So a
    complement bought at .30 retires the long at .70, share for share.

    Long 100 at $0.60, opposite-side reduction 40 at $0.30: residual 60,
    reduction proceeds $28, winning settlement proceeds $60, net $28
    before fees.
    """
    r = S.replay(POS, _orders(sell=False, comp=True), [BUY_100, COMP_40])
    assert r["ok"] is True, r
    assert r["venue_model"]["model"] == \
        "ONE_SIGNED_NET_POSITION_PER_MARKET"
    assert r["nets_opposite_side"] is True
    # ONE LEG. Not two.
    assert r["legs"] == ["%s:0" % COND], r["legs"]
    assert r["portfolio"]._leg(COND, 1)["qty"] == pytest.approx(0.0), (
        "an opposite-side acquisition must not create a second leg")
    # INTERMEDIATE INVENTORY, CASH AND FEES.
    assert r["portfolio"]._leg(COND, 0)["qty"] == pytest.approx(60.0)
    assert r["portfolio"]._leg(COND, 0)["cost"] == pytest.approx(36.0)
    assert r["portfolio"].cash == pytest.approx(-32.0)
    assert r["reductions"] == 1 and r["buys"] == 1 and r["sells"] == 0
    assert r["reduction_proceeds_usd"] == pytest.approx(28.0)
    assert r["direct_exit_proceeds_usd"] == pytest.approx(0.0)

    got = S.plan(POS, IDENT, r, _res("1"))
    assert got["status"] == S.S_SETTLED
    a = got["accounting"]
    assert len(a["settled_legs"]) == 1, "there is one leg to settle"
    assert a["residual_qty_settled"] == pytest.approx(60.0)
    assert a["reduction_proceeds_usd"] == pytest.approx(28.0)
    assert a["settlement_cash_usd"] == pytest.approx(60.0)
    assert a["total_cash_returned_usd"] == pytest.approx(88.0)
    assert a["net_usd"] == pytest.approx(28.0)
    assert a["reconciles"] is True


def test_a_reduction_may_never_become_a_reversal():
    """The position model's own rule: 150 short against 100 long exits and
    then opens 50 of opposite exposure nobody decided to take. Capped at
    the held quantity, and the surplus is REPORTED, not trimmed silently."""
    r = S.replay(POS, _orders(sell=False, comp=True),
                 [BUY_100, dict(COMP_40, qty=150)])
    assert r["ok"] is True
    assert r["portfolio"]._leg(COND, 0)["qty"] == pytest.approx(0.0)
    assert r["portfolio"]._leg(COND, 1)["qty"] == pytest.approx(0.0)
    assert len(r["discrepancies"]) == 1
    assert r["discrepancies"][0]["surplus_qty"] == pytest.approx(50.0)
    # AND IT SHOWS UP ON THE PLAN, not only in the replay.
    got = S.plan(POS, IDENT, r, _res("1"))
    assert got["status"] == S.S_NOTHING_HELD
    assert got["discrepancies"][0]["surplus_qty"] == pytest.approx(50.0)


def test_genuine_two_token_accounting_is_kept_separate():
    """On Polymarket's global CLOB the two legs ARE distinct tokens and an
    account really holds both -- which is what `bettor_inventory`'s
    MATCHED_QTY and LOCKED_PNL describe. That accounting is preserved; it
    is simply not PMUS's."""
    two = dict(POS, venue="POLYMARKET")
    r = S.replay(two, _orders(sell=False, comp=True), [BUY_100, COMP_40])
    assert r["ok"] is True
    assert r["venue_model"]["model"] == "TWO_SEPARATE_TOKENS_BOTH_HOLDABLE"
    assert r["nets_opposite_side"] is False
    assert r["legs"] == ["%s:0" % COND, "%s:1" % COND]
    assert r["portfolio"]._leg(COND, 0)["qty"] == pytest.approx(100.0)
    assert r["portfolio"]._leg(COND, 1)["qty"] == pytest.approx(40.0)
    assert r["reductions"] == 0 and r["buys"] == 2
    got = S.plan(two, IDENT, r, _res("1"))
    assert got["status"] == S.S_SETTLED
    a = got["accounting"]
    assert len(a["settled_legs"]) == 2, "both tokens are really held"
    held = [l for l in a["settled_legs"] if l["is_the_held_leg"]][0]
    comp = [l for l in a["settled_legs"] if not l["is_the_held_leg"]][0]
    assert held["payout_per_contract"] == 1.0
    assert comp["payout_per_contract"] == 0.0, "exactly one side pays"
    assert a["entry_outlay_usd"] == pytest.approx(72.0)
    assert a["net_usd"] == pytest.approx(28.0)
    assert a["reconciles"] is True


def test_a_second_leg_with_no_confirmed_complement_is_refused():
    """A pair is a claim the identity layer makes, not an arithmetic fact.
    On a catalogue that lists three outcomes, holding index 1 beside index
    0 does not make them complements."""
    two = dict(POS, venue="POLYMARKET")
    r = S.replay(two, _orders(sell=False, comp=True), [BUY_100, COMP_40])
    got = S.plan(two, dict(IDENT, outcomes_listed=3), r, _res("1"))
    assert got["status"] == S.S_UNPAIRED_LEG
    assert got["accounting"] is None
    assert "not established as complements" in got["why"]


def test_a_fill_whose_order_is_missing_refuses_rather_than_guesses():
    fills = [BUY_100, {"fill_id": "f9", "order_id": "GONE", "at": 1,
                       "qty": 10, "price": 0.5, "fee_usd": 0.0}]
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
    r = S.replay(short_pos, _orders(), [BUY_100])
    got = S.plan(short_pos, short_id, r, _res("1"))
    assert got["status"] == S.S_SETTLED
    assert got["side_map"] == loop.SIDE_SHORT
    assert got["accounting"]["payout_per_contract"] == 0.0
    assert got["accounting"]["net_usd"] == pytest.approx(-60.0)


def test_an_identity_refusal_stops_the_settlement_before_accounting():
    for code in (S.S_NO_SLUG, S.S_WRONG_SIDE, S.S_NO_HELD_EVENT):
        bad = dict(IDENT, refusal=code, why="because")
        got = S.plan(POS, bad, S.replay(POS, _orders(), [BUY_100]), _res("1"))
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
                 S.replay(POS, _orders(), [BUY_100]), _res("1"))
    assert got["status"] == S.S_WRONG_SIDE


def test_a_venue_side_that_is_not_established_refuses_rather_than_guesses():
    got = S.plan(POS, dict(IDENT, buy_intent="ORDER_INTENT_BUY_SHORT",
                           ladder_side="ASK"),
                 S.replay(POS, _orders(), [BUY_100]), _res("1"))
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
    r = S.replay(POS, _orders(), [BUY_100])
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
    fills = [dict(BUY_100, fee_usd=2.0),
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
    r = S.replay(POS, _orders(), [BUY_100])
    assert S.plan(POS, IDENT, r,
                  _res("0.5", settlementStatus="VOID"))["status"] == S.S_VOID
    # A SUBSTRING MATCH WOULD READ THIS AS A VOID. It is not one.
    denied = S.plan(POS, IDENT, S.replay(POS, _orders(), [BUY_100]),
                    _res("0.5", settlementStatus="NOT_VOIDED"))
    assert denied["status"] == S.S_NO_REFUND_ESTABLISHED
    assert denied["void_evidence"]["declared"] is False
    assert "settlementStatus" in denied["void_evidence"]["fields_present"]


def test_void_accounting_refuses_to_run_without_declared_evidence():
    with pytest.raises(ValueError):
        S.void_accounting(S.replay(POS, _orders(), [BUY_100]), condition_id=COND,
                          held_index=0, void_evidence={"declared": False})


def test_the_named_winner_and_inferred_cases_still_settle_nothing():
    r = S.replay(POS, _orders(), [BUY_100])
    named = S.plan(POS, IDENT, r, {"status": "RESOLVED",
                                   "outcome": "Houston Astros",
                                   "settlement_price": None})
    assert named["status"] == S.S_NOT_AUTHORITATIVE
    assert named["venue_class"] == loop.C_NAMED_WINNER
    inferred = S.plan(POS, IDENT, S.replay(POS, _orders(), [BUY_100]),
                      {"status": "RESOLVED_DERIVED",
                       "outcome": "Houston Astros"})
    assert inferred["status"] == S.S_NOT_AUTHORITATIVE
    assert inferred["venue_class"] == loop.C_INFERRED


def test_pending_and_unreadable_stay_different_answers():
    r = S.replay(POS, _orders(), [BUY_100])
    assert S.plan(POS, IDENT, r,
                  {"status": "PENDING"})["status"] == S.S_PENDING
    bad = S.plan(POS, IDENT, S.replay(POS, _orders(), [BUY_100]),
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


# ── THE REVIEW'S TWO CASES, THROUGH THE PRODUCTION CONSUMER ──────────
#
# The pure tests above pin the arithmetic. These drive the SAME two cases
# through `settle_open_positions` against the real schema: the open query,
# the identity resolution, the ledger read, the replay, the write, the
# terminal exclusion, the exposure release and a restart.

#: THE ORDER'S OWN CLOCK IS SUPPLIED, not `now()`. The schema enforces
#: that a modelled fill cannot precede the creation of the order it filled
#: -- "an order created during delayed processing cannot fill against a
#: print that preceded it" -- so a fixture that dates its fills in the past
#: and its orders at `now()` is refused, correctly.
ORD_SQL = (
    "INSERT INTO rn1x_orders (order_id, position_id, decision_id, "
    "condition_id, outcome_index, side, intent, liquidity, limit_price, "
    "qty, filled_qty, state, placed_at, updated_at, fill_basis, "
    "created_at_runtime, created_at_basis) VALUES "
    "($1,$2,$3,$4,$5,$6,$7,'TAKER',$8,$9,$9,'FILLED',$10,$10,"
    "'MARKETABLE_RECONSTRUCTED',$10,'RUNTIME_WALL_CLOCK') "
    "ON CONFLICT (order_id) DO NOTHING")

FILL_SQL = (
    "INSERT INTO rn1x_fills (fill_id, order_id, at, qty, price, fee_usd, "
    "evidence_id, queue_share, fill_basis) VALUES "
    "($1,$2,$3,$4,$5,$6,$7,0,'MARKETABLE_RECONSTRUCTED') "
    "ON CONFLICT (fill_id) DO NOTHING")


async def _make_position(conn, *, pid, provenance, venue, seed_qty,
                         seed_price, legs, condition_id, slug,
                         payout_event, experiment_id, policy):
    """One position and its ledger, written straight into the four tables.

    `legs` is a list of (order_suffix, outcome_index, side, qty, price,
    fee, at) -- the executions in the order they happened.
    """
    import datetime as _dt
    import time as _t

    base = _dt.datetime.fromtimestamp(_t.time() - 3600.0,
                                      tz=_dt.timezone.utc)
    await inv.ensure_experiment(conn, experiment_id)
    await conn.execute(
        "INSERT INTO rn1x_positions (position_id, experiment_id, policy, "
        "source_trade_id, source_account, condition_id, outcome_index, "
        "entry_kind, entry_kind_why, seed_qty, seed_price, seed_basis_usd, "
        "source_ts, detected_ts, decision_ts, decision_basis, provenance, "
        "venue_market_slug, venue_buy_intent, venue_ladder_side, "
        "payout_event, venue) VALUES "
        "($1,$2,$3,NULL,'t',$4,0,'t','t',$5,$6,$7,now(),now(),now(),"
        "'RUNTIME_WALL_CLOCK',$8,$9,'ORDER_INTENT_BUY_LONG','ASK',$10,$11) "
        "ON CONFLICT (position_id) DO NOTHING",
        pid, experiment_id, policy, condition_id, seed_qty, seed_price,
        seed_qty * seed_price, provenance, slug, payout_event, venue)
    for k, (suffix, oi, side, qty, price, fee, at) in enumerate(legs):
        oid = "%s:O:%s" % (pid, suffix)
        placed = base + _dt.timedelta(seconds=float(at))
        await conn.execute(ORD_SQL, oid, pid, None, condition_id, oi, side,
                           "ORDER_INTENT_BUY_LONG", price, qty, placed)
        await conn.execute(
            FILL_SQL, "%s:F%03d" % (oid, k), oid,
            placed + _dt.timedelta(seconds=1.0),
            qty, price, fee, "venue_book:test")


@pg
@pytest.mark.asyncio
async def test_case_one_assigned_then_partial_exit_through_the_consumer():
    """Assigned 100 at $0.60 -> sell 40 at $0.80 -> settle the remaining
    60. Total proceeds $92, net $32 before fees. End to end."""
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_entry_execution as entryx
    from . import test_the_entry_lane_reaches_inventory as L

    conn = await asyncpg.connect(DSN)
    pid = "SETTLE-CASE1"
    try:
        await L._seed(conn)
        await _make_position(
            conn, pid=pid,
            provenance="ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY", venue="PMUS",
            seed_qty=100.0, seed_price=0.60,
            legs=[("s1", 0, "SELL", 40.0, 0.80, 0.0, 10.0)],
            condition_id=L.CONDITION, slug=L.US_SLUG,
            payout_event="Houston Astros",
            experiment_id="SETTLE_TEST_EXP", policy="SETTLE_TEST_POL")

        got = await S.settle_open_positions(
            conn, experiment_id="SETTLE_TEST_EXP", policy="SETTLE_TEST_POL",
            now=time.time(),
            read_resolution=lambda slug: _res(
                "1", settled_at="2026-09-24T23:14:07Z"))
        assert got["settled"] == 1, got
        r = got["results"][0]
        assert r["status"] == S.S_SETTLED and r["written"] is True
        assert r["opening_mode"] == S.ASSIGNED
        assert r["venue_model"] == "ONE_SIGNED_NET_POSITION_PER_MARKET"
        a = r["accounting"]
        # INTERMEDIATE INVENTORY AND BASIS, not only the final number.
        assert a["held_leg_before"]["qty"] == pytest.approx(60.0)
        assert a["held_leg_before"]["cost"] == pytest.approx(36.0)
        assert a["held_leg_before"]["avg_cost"] == pytest.approx(0.60)
        # CASH AND FEES AS THEY STOOD BEFORE THE PAYOUT: 60 out, 32 back.
        assert a["cash_before_settlement_usd"] == pytest.approx(-28.0)
        assert a["fees_before_settlement_usd"] == pytest.approx(0.0)
        assert a["realized_before_settlement_usd"] == pytest.approx(8.0)
        assert a["entry_outlay_usd"] == pytest.approx(60.0)
        assert a["direct_exit_proceeds_usd"] == pytest.approx(32.0)
        assert a["reduction_proceeds_usd"] == pytest.approx(0.0)
        assert a["residual_qty_settled"] == pytest.approx(60.0)
        assert a["settlement_cash_usd"] == pytest.approx(60.0)
        assert a["total_cash_returned_usd"] == pytest.approx(92.0)
        assert a["net_usd"] == pytest.approx(32.0)
        assert a["fees_usd"] == pytest.approx(0.0)
        assert a["reconciles"] is True

        # WHAT LANDED IN THE TABLE.
        row = await conn.fetchrow(
            "SELECT realized_cash_usd::float8 AS cash, net_usd::float8 AS "
            "net, residual_qty::float8 AS resid, outcome_basis "
            "FROM rn1x_outcomes WHERE position_id = $1", pid)
        assert row["net"] == pytest.approx(32.0)
        assert row["resid"] == 0.0
        assert row["outcome_basis"] == S.BASIS_SETTLED

        # TERMINAL EXCLUSION, both queries.
        assert pid not in [o["position_id"] for o in await
                           store.open_positions(
                               conn, experiment_id="SETTLE_TEST_EXP",
                               limit=10)]
        assert pid not in [x["position_id"] for x in await conn.fetch(
            S.OPEN_ENTRY_SQL, ["SETTLE_TEST_EXP"], ["SETTLE_TEST_POL"], 10)]

        # EXPOSURE RELEASED by the outcome row alone.
        rows = await loop.open_shadow_book(conn, "SETTLE_TEST_EXP")
        exp = entryx.exposure_from_rows(
            rows, condition_id="other", event_key="other",
            proposed_cost_usd=10.0, proposed_qty=10.0, now=time.time())
        assert exp["settled_positions_excluded_from_exposure"] >= 1
        assert exp["observed"]["MAX_CAPITAL_DEPLOYED"] == pytest.approx(10.0)

        # RESTART REPLAY: a new connection, nothing re-read, nothing rewritten.
        written_at = await conn.fetchval(
            "SELECT written_at FROM rn1x_outcomes WHERE position_id=$1", pid)
        await conn.close()
        conn = await asyncpg.connect(DSN)
        again = await S.settle_open_positions(
            conn, experiment_id="SETTLE_TEST_EXP", policy="SETTLE_TEST_POL",
            now=time.time(),
            read_resolution=lambda slug: pytest.fail("must not re-read"))
        assert again["examined"] == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_outcomes WHERE position_id=$1",
            pid) == 1
        assert await conn.fetchval(
            "SELECT written_at FROM rn1x_outcomes WHERE position_id=$1",
            pid) == written_at
    finally:
        await _drop(conn, pid)
        await L._cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_case_two_pmus_reduction_through_the_consumer():
    """PMUS long 100 at $0.60 -> opposite-side reduction 40 at $0.30:
    residual 60, reduction proceeds $28, winning settlement proceeds $60,
    net $28 before fees. And ONE inventory leg throughout."""
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_entry_execution as entryx
    from . import test_the_entry_lane_reaches_inventory as L

    conn = await asyncpg.connect(DSN)
    pid = "SETTLE-CASE2"
    try:
        await L._seed(conn)
        await _make_position(
            conn, pid=pid,
            provenance="AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW",
            venue="PMUS", seed_qty=100.0, seed_price=0.60,
            legs=[("b1", 0, "BUY", 100.0, 0.60, 0.0, 5.0),
                  ("c1", 1, "BUY", 40.0, 0.30, 0.0, 10.0)],
            condition_id=L.CONDITION, slug=L.US_SLUG,
            payout_event="Houston Astros",
            experiment_id="SETTLE_TEST_EXP", policy="SETTLE_TEST_POL")

        got = await S.settle_open_positions(
            conn, experiment_id="SETTLE_TEST_EXP", policy="SETTLE_TEST_POL",
            now=time.time(),
            read_resolution=lambda slug: _res("1"))
        assert got["settled"] == 1, got
        r = got["results"][0]
        assert r["status"] == S.S_SETTLED and r["written"] is True
        assert r["opening_mode"] == S.ACQUIRED
        assert r["nets_opposite_side"] is True
        a = r["accounting"]
        # ONE LEG, AND THE REDUCTION IS A REDUCTION.
        assert len(a["settled_legs"]) == 1
        assert a["reductions_replayed"] == 1
        assert a["held_leg_before"]["qty"] == pytest.approx(60.0)
        assert a["held_leg_before"]["cost"] == pytest.approx(36.0)
        # 60 out, 28 back from the reduction, before the payout.
        assert a["cash_before_settlement_usd"] == pytest.approx(-32.0)
        assert a["realized_before_settlement_usd"] == pytest.approx(4.0)
        assert a["entry_outlay_usd"] == pytest.approx(60.0)
        assert a["reduction_proceeds_usd"] == pytest.approx(28.0)
        assert a["direct_exit_proceeds_usd"] == pytest.approx(0.0)
        assert a["residual_qty_settled"] == pytest.approx(60.0)
        assert a["settlement_cash_usd"] == pytest.approx(60.0)
        assert a["total_cash_returned_usd"] == pytest.approx(88.0)
        assert a["net_usd"] == pytest.approx(28.0)
        assert a["fees_usd"] == pytest.approx(0.0)
        assert a["reconciles"] is True

        assert await conn.fetchval(
            "SELECT net_usd::float8 FROM rn1x_outcomes WHERE position_id=$1",
            pid) == pytest.approx(28.0)
        assert pid not in [o["position_id"] for o in await
                           store.open_positions(
                               conn, experiment_id="SETTLE_TEST_EXP",
                               limit=10)]
        rows = await loop.open_shadow_book(conn, "SETTLE_TEST_EXP")
        exp = entryx.exposure_from_rows(
            rows, condition_id="other", event_key="other",
            proposed_cost_usd=10.0, proposed_qty=10.0, now=time.time())
        assert exp["observed"]["MAX_CAPITAL_DEPLOYED"] == pytest.approx(10.0)

        await conn.close()
        conn = await asyncpg.connect(DSN)
        again = await S.settle_open_positions(
            conn, experiment_id="SETTLE_TEST_EXP", policy="SETTLE_TEST_POL",
            now=time.time(),
            read_resolution=lambda slug: pytest.fail("must not re-read"))
        assert again["examined"] == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_outcomes WHERE position_id=$1",
            pid) == 1
    finally:
        await _drop(conn, pid)
        await L._cleanup(conn)
        await conn.close()


async def _drop(conn, pid):
    await conn.execute("DELETE FROM rn1x_outcomes WHERE position_id=$1", pid)
    await conn.execute(
        "DELETE FROM rn1x_fills WHERE order_id IN (SELECT order_id FROM "
        "rn1x_orders WHERE position_id=$1)", pid)
    await conn.execute("DELETE FROM rn1x_orders WHERE position_id=$1", pid)
    await conn.execute("DELETE FROM rn1x_decisions WHERE position_id=$1", pid)
    await conn.execute("DELETE FROM rn1x_positions WHERE position_id=$1", pid)
