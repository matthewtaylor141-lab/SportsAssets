"""THE OWNER'S ASTROS EXAMPLE, AS A DETERMINISTIC REGRESSION.

NOT A TRADING INSTRUCTION. Every price, depth and probability below is
SYNTHETIC and chosen to pin a property of the decision machinery; none is a
view about any fixture. Pure where possible (the production selector, fee
schedule, structure classifier, payout-state distribution, whole-position
ranking and the one-measure valuation that gates real money), plus one case
through the scheduled cycle.

THE POSITION. $200 of the Astros moneyline at $0.522. Orders are for WHOLE
contracts, so the quantity is floor(200 / 0.522) = 383 contracts, costing
383 x 0.522 = $199.926 -- the largest whole-contract position whose contract
cost does not exceed $200. The entry fee is charged ON TOP, from the dated
schedule in force on the game date (2026-09-29 -> the published schedule
effective 2026-09-25, PMUS_PUBLISHED_2026_09_25): theta x 383 x 0.522 x
0.478 rounded = $6.64. The book records the entry's cash to the cent, so the
booked remaining basis is $199.93 (the pure cases below use the exact
contract cost $199.926; the scheduled case reads the booked $199.93).

WHAT IS PINNED.
  * A full exit is executable now for $10 net: 383 bid at 0.028 (a 0.001
    tick, like the example's own 0.522) -> 10.724 - 0.72 fee = $10.004.
  * The opponent +1.5 at 0.985 is an executable hedge ONLY with sufficient
    displayed depth and established (unexpired) book evidence; without them
    it is visible and not rankable, with the exact blocker.
  * The opponent -1.5 exposes the both-lose region (opponent by exactly
    one): the payoff row, the taxonomy and the worst case.
  * A first-five contract is never admitted as protection for the full-game
    moneyline.
  * Whole-position economics include the hedge's capital, its fee and the
    uncovered quantity when depth is short.
  * A historical loss forces neither a hedge nor an exit: HOLD beats the $10
    exit at one forward probability and loses to it at another, with the
    same sunk basis; the +1.5 at 0.985 is chosen only when its forward
    increment over HOLD is positive. The basis enters total P&L, never the
    ranking difference.
  * The daily review's estimate for the unchosen hedge is a
    HYPOTHETICAL_ESTIMATE with could_have_filled = UNPROVEN.
"""
from __future__ import annotations

import math
import os
from decimal import Decimal
from fractions import Fraction

import pytest

from sportsassets import bettor_common_valuation as CV
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_fee_schedule as FEES
from sportsassets import bettor_funded_hedge_supply as HS
from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_indirect_structures as IS
from sportsassets import bettor_mgmt_select as MS
from sportsassets import bettor_payout_states as PS
from sportsassets import bettor_settlement_clauses as SC
from sportsassets import bettor_xavier_review as XR

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

GAME_DATE = "2026-09-29"
FIXTURE = "mlb-hou-sea-2026-09-29"
BUDGET = 200.0
ENTRY_PX = 0.522
QTY = math.floor(BUDGET / ENTRY_PX)            # 383 whole contracts
BASIS = QTY * ENTRY_PX                           # 199.926
EXIT_BID = 0.028                                 # SYNTHETIC, 0.001 tick
HEDGE_PX = 0.985                                 # the opponent +1.5
SCHEDULE = FEES.for_date(GAME_DATE)
#: SYNTHETIC venue prose, both contracts: extra innings included (no level
#: end); a cancelled game refunds each contract's own purchase basis.
PROSE = ("Resolves on the final score and includes any extra innings played. "
         "A tie resolves 50-50. If the game is cancelled all stakes are "
         "refunded.")


def fee(qty, px) -> float:
    return float(SCHEDULE.fill_fee(int(qty), Decimal(str(px)), maker=False))


def _leg(cid, *, kind, backs, line=None, qty=QTY, cost=None,
         period=IS.PERIOD_FULL):
    read = SC.interpret(PROSE, source="SYNTHETIC")
    tie = read["rules"]["TIE"]
    void = read["rules"]["CANCELLED"]
    return IS.Leg(condition_id=cid, fixture_id=FIXTURE, kind=kind,
                  period=period, overtime=IS.OT_INCLUDED, backs=backs,
                  line=line, quantity=qty, cost_cents_per_unit=cost,
                  tie_rule=tie.get("clause") if tie.get("established") else None,
                  void_rule=(void.get("clause") if void.get("established")
                             else None),
                  settlement_text_captured=True,
                  settlement_rules=read["rules"],
                  settlement_provenance=read["provenance"])


#: THE HELD LEG: the Astros (team A) moneyline, LONG.
ASTROS_ML = _leg("aec-mlb-hou-sea-2026-09-29#ORDER_INTENT_BUY_LONG",
                 kind=IS.KIND_MONEYLINE, backs="A", cost=52.2)
#: THE OPPONENT +1.5: pays unless the Astros win by two or more.
OPP_PLUS_1_5 = _leg("asc-mlb-hou-sea-2026-09-29-neg-1pt5#"
                    "ORDER_INTENT_BUY_SHORT", kind=IS.KIND_SPREAD, backs="B",
                    line=Fraction(-3, 2), cost=98.5)
#: THE OPPONENT -1.5: pays only if the opponent wins by two or more.
OPP_MINUS_1_5 = _leg("asc-mlb-sea-hou-2026-09-29-neg-1pt5#"
                     "ORDER_INTENT_BUY_LONG", kind=IS.KIND_SPREAD, backs="B",
                     line=Fraction(3, 2), cost=40)


# ════════════════════════════════════════════════════════════════════
# THE POSITION AND THE $10 EXIT
# ════════════════════════════════════════════════════════════════════

def test_the_entry_is_383_whole_contracts_costing_199_926_plus_a_dated_fee():
    assert QTY == 383
    assert QTY * ENTRY_PX <= BUDGET < (QTY + 1) * ENTRY_PX
    assert BASIS == pytest.approx(199.926, abs=1e-9)
    assert SCHEDULE.effective_from == "2026-09-25", SCHEDULE.schedule_id
    assert SCHEDULE.schedule_id == "PMUS_PUBLISHED_2026_09_25"
    assert fee(QTY, ENTRY_PX) == pytest.approx(6.64)
    # THE TOTAL OUTLAY: contracts plus the entry fee charged on top
    assert BASIS + fee(QTY, ENTRY_PX) == pytest.approx(206.566, abs=1e-9)


def _ladder(levels):
    return {"levels": [{"acquisition_price": px, "api_price": px, "qty": q}
                       for px, q in levels]}


def _selector(p, *, levels=((EXIT_BID, QTY),)):
    """HOLD / DIRECT_EXIT / REDUCE from the DEPLOYED selector, on HOLD's
    forward probability `p`, the real basis and the dated fee schedule."""
    return MS.rank_with_hold(
        QTY, ENTRY_PX, ev_hold={"probability": p, "status": "IDENTIFIED"},
        bid=levels[0][0], bid_size=levels[0][1], complement_ask=None,
        complement_ask_size=None,
        fee_fn=lambda *, qty, price: fee(qty, price), venue="PMUS",
        us_market_slug="aec-mlb-hou-sea-2026-09-29", held_is_long=True,
        sale_ladder=_ladder(levels), executable_actions=FM.EXECUTABLE_ACTIONS)


def _cand(ranking, action):
    return next(c for c in ranking["candidates"] if c["action"] == action)


def test_a_full_exit_nets_ten_dollars_after_the_fee():
    gross = QTY * EXIT_BID
    f = fee(QTY, EXIT_BID)
    assert gross == pytest.approx(10.724, abs=1e-9)
    assert f == pytest.approx(0.72)
    assert gross - f == pytest.approx(10.00, abs=0.01)
    # THE DEPLOYED SELECTOR PRICES THE SAME FULL EXIT
    ex = _cand(_selector(0.10), "DIRECT_EXIT")
    assert ex["qty"] == QTY
    assert ex["cash_now_usd"] == pytest.approx(10.004, abs=1e-6)
    assert ex["cash_now_usd"] == pytest.approx(10.00, abs=0.01)


# ════════════════════════════════════════════════════════════════════
# THE +1.5 AT 0.985: EXECUTABLE ONLY WITH DEPTH AND EVIDENCE
# ════════════════════════════════════════════════════════════════════

def _discover(cand):
    got = PC.discover(held_leg=ASTROS_ML, candidate_legs=[cand],
                      sport_permits_tie=False, fixture_can_void=True,
                      fixture_can_postpone=False)
    assert got["admitted"], got
    return got["admitted"]


def _rank(admitted, *, depth, expire_at):
    detail = {"candidate_id": admitted[0]["condition_id"], "price": HEDGE_PX,
              "fee_usd": fee(min(QTY, depth or QTY), HEDGE_PX)}
    if depth is not None:
        detail["depth_qty"] = depth
    if expire_at is not None:
        detail["inputs_expire_at"] = expire_at
    return PC.rank_admitted(admitted, details=[detail], wanted_qty=QTY,
                            held_leg=ASTROS_ML, sport_permits_tie=False,
                            fixture_can_void=True, fixture_can_postpone=False)


def _options(admitted, ranking, *, now=1000.0):
    return PC.decision_options(
        admitted=admitted, ranking=ranking,
        facts={"held_leg": ASTROS_ML, "model_inputs": {}},
        position={"intent_id": "astros", "portfolio_group_id": "grp:astros",
                  "us_market_slug": "aec-mlb-hou-sea-2026-09-29",
                  "residual_qty": QTY, "event_key": FIXTURE},
        account_id="acct-astros-example", venue="PMUS", now=now)


def test_the_plus_1_5_is_visible_but_not_rankable_without_depth_or_evidence():
    adm = _discover(OPP_PLUS_1_5)
    # NO DISPLAYED DEPTH READ: visible, not rankable, the exact blocker
    r = _rank(adm, depth=None, expire_at=1030.0)
    assert r["ranked"] == []
    assert [x["refusal"] for x in r["not_rankable"]] == [
        PC.R_CANDIDATE_DEPTH_NOT_ESTABLISHED]
    # A BOOK THAT SUPPORTS NOTHING: the same, with its own blocker
    r = _rank(adm, depth=0, expire_at=1030.0)
    assert [x["refusal"] for x in r["not_rankable"]] == [
        PC.R_CANDIDATE_DEPTH_TOO_THIN]
    # EVIDENCE THAT HAS EXPIRED: ranked on its number, but no order option
    r = _rank(adm, depth=500, expire_at=999.0)
    assert r["ranked"], r
    opts, plans, refused = _options(adm, r, now=1000.0)
    assert opts == [] and plans == {}
    assert [x["refusal"] for x in refused] == ["ACQUISITION_INPUTS_EXPIRED"]
    # EVIDENCE NEVER ESTABLISHED (no expiry at all): refused by the plan
    r = _rank(adm, depth=500, expire_at=None)
    opts, plans, refused = _options(adm, r, now=1000.0)
    assert opts == [] and refused and refused[0]["refusal"], refused
    # WITH SUFFICIENT DEPTH AND LIVE EVIDENCE IT IS AN EXECUTABLE OPTION
    r = _rank(adm, depth=500, expire_at=1030.0)
    opts, plans, refused = _options(adm, r, now=1000.0)
    assert refused == [] and len(opts) == 1, refused
    plan = plans[opts[0]["candidate_id"]]
    assert plan.quantity == QTY
    assert plan.side == "ORDER_INTENT_BUY_SHORT"
    # YES-DENOMINATED AND ROUNDED THE PROTECTIVE WAY: the venue takes cents,
    # so the 0.015 YES price of a 0.985 NO cost goes UP to 0.02 -- the order
    # can never pay more than 0.98, i.e. never more than it was ranked at
    assert plan.limit_price == pytest.approx(
        FX.safe_cent(1 - HEDGE_PX, "ORDER_INTENT_BUY_SHORT")) == 0.02


def test_whole_position_economics_carry_hedge_capital_fees_and_the_uncovered():
    adm = _discover(OPP_PLUS_1_5)
    r = _rank(adm, depth=100, expire_at=1030.0)          # 100 of 383
    row = r["ranked"][0]
    assert row["covered_qty"] == pytest.approx(100.0)
    assert row["uncovered_qty"] == pytest.approx(283.0)
    assert row["fee_usd"] == pytest.approx(fee(100, HEDGE_PX))
    pw = row["position_worst_case"]
    assert pw["ok"] is True
    assert pw["scale"] == "WHOLE_POSITION"
    assert pw["held_qty"] == QTY and pw["hedge_qty"] == 100
    # THE POSITION'S FLOOR, AT THE REAL QUANTITIES: in every region the
    # Astros do not win, the 100 hedged contracts pay $100 and ALL 383 held
    # contracts lose their basis -- the 283 uncovered included. (The
    # position table prices each leg at its whole-cent cost: 0.52 and 0.98.)
    cost = QTY * pw["held_basis_usd_per_unit"] + 100 * pw[
        "hedge_basis_usd_per_unit"]
    assert pw["cost_usd"] == pytest.approx(cost)
    assert pw["worst_case_usd"] == pytest.approx(
        100 * 1.0 - cost - row["fee_usd"], abs=1e-6)
    lose = [x for x in pw["regions"] if x["per_leg_cents"] == [0, 100]]
    assert lose and all(x["net_usd"] == pytest.approx(100.0 - cost)
                        for x in lose)
    # THE ADDED CAPITAL: the hedge's own collateral at its order's price,
    # and its fee
    opts, plans, _ = _options(adm, r)
    plan = plans[opts[0]["candidate_id"]]
    assert plan.quantity == 100
    assert plan.collateral_usd == pytest.approx(
        FX.collateral_for(plan.limit_price, 100, "ORDER_INTENT_BUY_SHORT"))
    assert opts[0]["fee_usd"] == pytest.approx(fee(100, HEDGE_PX))


# ════════════════════════════════════════════════════════════════════
# THE OPPONENT -1.5: THE BOTH-LOSE REGION
# ════════════════════════════════════════════════════════════════════

def test_the_minus_1_5_exposes_the_opponent_by_exactly_one_both_lose_region():
    st = IS.classify(ASTROS_ML, OPP_MINUS_1_5, sport_permits_tie=False,
                     fixture_can_void=True, fixture_can_postpone=False)
    # NEVER BOTH WIN, BOTH-LOSE REACHABLE: a GAP, not protection
    assert st.taxonomy == IS.GAP, st.why
    assert st.both_win_regions == ()
    by = {r["region"]: r for r in st.table}
    lose1 = [r for r in st.table if r["state"] == IS.STATE_REGULAR
             and r["per_leg_cents"] == [0, 0]]
    row = next(r for r in lose1 if r["region"] == "margin = -1")
    # the opponent wins by exactly one: Astros ML loses, opponent -1.5 loses
    assert row["region"] == "margin = -1", row
    assert row["joint_cents"] == 0
    assert row["region"] in st.both_lose_regions
    assert st.min_payout_cents == 0          # the worst case: nothing back
    del by


def test_a_first_five_contract_is_never_admitted_as_full_game_protection():
    for st in ("baseball_team_first_five_innings_winner",
               "baseball_team_first_five_spread"):
        got = HS.derive_kind({"sports_type": st, "side_norm": "sea"})
        assert got["kind"] is None, got
        assert got["refusal"] == HS.R_TYPE_NOT_A_GRADED_VARIABLE, got
    # and any part-game leg is rejected against the full-game holding by the
    # grading key, with the named reason
    part = _leg("asc-f5#ORDER_INTENT_BUY_SHORT", kind=IS.KIND_SPREAD,
                backs="B", line=Fraction(-1, 2), cost=60,
                period=IS.PERIOD_H1)
    got = PC.discover(held_leg=ASTROS_ML, candidate_legs=[part],
                      sport_permits_tie=False, fixture_can_void=True,
                      fixture_can_postpone=False)
    assert got["admitted"] == []
    assert got["rejected"][0]["refusal"] == PC.R_NOT_SETTLEMENT_COMPATIBLE


# ════════════════════════════════════════════════════════════════════
# A HISTORICAL LOSS FORCES NOTHING: FORWARD VALUE DECIDES
# ════════════════════════════════════════════════════════════════════

HELD_CENTS = CV.held_outcome_cents(ASTROS_ML, sport_permits_tie=False,
                                   fixture_can_void=True)["held_cents"]
EXIT = {"action": "DIRECT_EXIT", "candidate_id": "DIRECT_EXIT", "qty": QTY,
        "net_proceeds_usd": QTY * EXIT_BID - fee(QTY, EXIT_BID)}


def _acquire(p, q, *, v_pt, v_hi, qty=QTY):
    """The +1.5 as the one-measure valuation takes it: the payout classes
    of the classified structure and their distribution from the primary
    probability p, P(+1.5 wins | Astros win) = q (the Astros won by exactly
    one), structural given a loss, and the void rate -- at the point rate and
    at both ends of its range."""
    st = IS.classify(ASTROS_ML, OPP_PLUS_1_5, sport_permits_tie=False,
                     fixture_can_void=True, fixture_can_postpone=False)
    cls = PS.payout_classes(st)
    assert cls["ok"], cls
    merged = PS.merged_structure(st, cls)

    def at(v):
        d = PS.distribution(cls, primary={"p_win": p, "source": "STATED"},
                            conditional={PS.WIN: q},
                            void={"rate": v, "n_fixtures": 400,
                                  "upper_95": v_hi, "source": "STATED"})
        assert d["ok"], d
        return d["probabilities"]
    return {"action": "ACQUIRE_INDIRECT_HEDGE", "candidate_id": "PLUS_1_5",
            "regions": [{"region": r["region"], "state": r["state"],
                         "per_leg_cents": r["per_leg_cents"]}
                        for r in merged["table"] if r.get("determined")],
            "region_probabilities": at(v_pt), "distribution_void_rate": v_pt,
            "region_probabilities_by_void": {0.0: at(0.0), v_pt: at(v_pt),
                                             v_hi: at(v_hi)},
            "hedge_qty": qty, "hedge_cost_usd": qty * HEDGE_PX,
            "fees_usd": fee(qty, HEDGE_PX)}


def _value(p, *, v_pt, v_hi, basis=BASIS, extra=()):
    return CV.value_actions(
        held_cents=HELD_CENTS, p_win=p, void_rate=v_pt, void_lower=0.0,
        void_upper=v_hi, qty=QTY, basis_usd=basis,
        candidates=[{"action": "HOLD", "candidate_id": "HOLD"}, EXIT]
        + list(extra))


def _vals(cv):
    return {r["fixed_action"][0]: r for r in cv["valued"]}


def test_hold_is_chosen_despite_the_loss_when_it_is_worth_more_than_ten():
    """(i) p = 0.10: HOLD is worth 38.30 forward against the $10.004 exit,
    so HOLD is chosen -- while the position's total P&L is a loss either way."""
    sel = _selector(0.10)
    assert sel["selected"] == "HOLD"
    cv = _value(0.10, v_pt=0.02, v_hi=0.05)
    assert cv["selection_basis"] == CV.BASIS_ROBUST
    assert cv["winner"]["fixed_action"][0] == "HOLD"
    v = _vals(cv)
    # THE RANKING DIFFERENCE HAS NO BASIS IN IT: 383 p - 10.004 at v = 0
    assert v["HOLD"]["value_at_range_low"] - v["DIRECT_EXIT"][
        "value_at_range_low"] == pytest.approx(QTY * 0.10 - 10.004, abs=1e-6)
    # AND THE BASIS IS IN TOTAL P&L: holding is still a large loss
    assert v["HOLD"]["value_at_range_low"] == pytest.approx(
        QTY * 0.10 - BASIS, abs=1e-6)
    assert v["HOLD"]["value_at_range_low"] < -150
    # the same comparison with NO sunk basis picks the same action by the
    # same margin
    cv0 = _value(0.10, v_pt=0.02, v_hi=0.05, basis=0.0)
    v0 = _vals(cv0)
    assert cv0["winner"]["fixed_action"] == cv["winner"]["fixed_action"]
    assert v0["HOLD"]["value_at_range_low"] - v0["DIRECT_EXIT"][
        "value_at_range_low"] == pytest.approx(
        v["HOLD"]["value_at_range_low"] - v["DIRECT_EXIT"][
            "value_at_range_low"], abs=1e-9)


def test_the_loss_taking_exit_is_chosen_when_holding_is_worth_less():
    """(ii) p = 0.01 with a stated void range [0, 0.01]: holding is worth at
    most 383 x (0.99 x 0.01 + 0.01 x 0.522) = 5.79 < 10.004, so the exit that
    REALISES A LOSS OF ~190 is chosen, robustly."""
    sel = _selector(0.01)
    assert sel["selected"] == "DIRECT_EXIT"
    cv = _value(0.01, v_pt=0.005, v_hi=0.01)
    assert cv["selection_basis"] == CV.BASIS_ROBUST
    assert cv["winner"]["fixed_action"][:3] == ["DIRECT_EXIT", "DIRECT_EXIT",
                                                float(QTY)]
    v = _vals(cv)
    assert v["DIRECT_EXIT"]["value_at_range_low"] == pytest.approx(
        10.004 - BASIS, abs=1e-6)                # the realised loss
    # AND THE HONEST COUNTERPOINT: at p = 0.02 over [0, 0.114] a void
    # refunding 0.522 makes HOLD worth more than the exit at the upper end,
    # so the choice is not robust and would not be sent -- the loss is not a
    # reason to sell either
    cv2 = _value(0.02, v_pt=0.033, v_hi=0.114)
    assert cv2["selection_basis"] == CV.BASIS_RESEARCH
    assert cv2["funded_dispatch_permitted"] is False


@pytest.mark.parametrize("q,chosen", [(0.90, True), (0.50, False)])
def test_the_plus_1_5_is_chosen_only_on_a_positive_forward_increment(q, chosen):
    """(iii) p = 0.10. The +1.5 pays unless the Astros win by two or more:
    P(cover) = p q + (1 - p). At v = 0 its increment over HOLD is exactly
    383 x (P(cover) - 0.985) - fee: +1.53 at q = 0.9 (P = 0.99), chosen;
    -13.79 at q = 0.5 (P = 0.95), rejected -- the loss already taken on the
    moneyline plays no part."""
    p = 0.10
    acq = _acquire(p, q, v_pt=0.01, v_hi=0.02)
    cv = _value(p, v_pt=0.01, v_hi=0.02, extra=[acq])
    v = _vals(cv)
    p_cover = p * q + (1 - p)
    inc0 = v["ACQUIRE_INDIRECT_HEDGE"]["value_at_range_low"] - \
        v["HOLD"]["value_at_range_low"]
    assert inc0 == pytest.approx(
        QTY * (p_cover - HEDGE_PX) - fee(QTY, HEDGE_PX), abs=1e-6)
    assert cv["selection_basis"] == CV.BASIS_ROBUST
    if chosen:
        assert inc0 > 0
        assert cv["winner"]["fixed_action"][:2] == [
            "ACQUIRE_INDIRECT_HEDGE", "PLUS_1_5"]
    else:
        assert inc0 < 0
        assert cv["winner"]["fixed_action"][0] == "HOLD"
    # the basis is subtracted from every action alike
    cvb = _value(p, v_pt=0.01, v_hi=0.02, basis=0.0, extra=[acq])
    assert cvb["winner"]["fixed_action"] == cv["winner"]["fixed_action"]


# ════════════════════════════════════════════════════════════════════
# THE DAILY REVIEW: AN UNCHOSEN HEDGE IS A HYPOTHETICAL ESTIMATE
# ════════════════════════════════════════════════════════════════════

def test_the_unchosen_hedge_is_a_hypothetical_estimate_that_could_not_be_proved_to_fill():
    """HOLD was chosen; the +1.5 was the unchosen alternative, and the
    Astros won by exactly one, so it WOULD have paid. The review's estimate
    says what it would have been at decision-time prices and quantities --
    labelled HYPOTHETICAL_ESTIMATE, could_have_filled UNPROVEN."""
    alt = {"action": "ACQUIRE_INDIRECT_HEDGE", "expected_net_usd": -160.1,
           "hedge_us_market_slug": "asc-mlb-hou-sea-2026-09-29-neg-1pt5",
           "hedge_order_intent": "ORDER_INTENT_BUY_SHORT", "hedge_qty": QTY,
           "held_qty": QTY, "hedge_cost_usd": QTY * HEDGE_PX,
           "fees_usd": fee(QTY, HEDGE_PX)}
    est = XR.hypothetical_estimate(
        alt, held={"qty": QTY, "basis_per_contract": ENTRY_PX,
                   "payout_per_contract": 1.0, "payout_source": "SYNTHETIC"},
        hedge={"payout_per_contract": 1.0, "source": "SYNTHETIC"})
    assert est["kind"] == XR.KIND_HYPOTHETICAL == "HYPOTHETICAL_ESTIMATE"
    assert est["could_have_filled"] == XR.COULD_HAVE_FILLED == "UNPROVEN"
    assert est["fill_basis"] == XR.FILL_BASIS_DISPLAYED
    assert est["eventual_outcome_known"] is True
    assert est["estimate_usd"] == pytest.approx(
        QTY * 1.0 + QTY * 1.0 - (QTY * HEDGE_PX + BASIS) - fee(QTY, HEDGE_PX),
        abs=1e-6)


# ════════════════════════════════════════════════════════════════════
# THE SCHEDULED PATH: HOLD, DESPITE THE LOSS, ON THE PERSISTED RECORD
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_scheduled_cycle_holds_the_astros_despite_the_loss(
        monkeypatch):
    """Through `ext_pinnacle_loop.cycle` (the scheduled harness of
    test_xavier_management_defects_reproduced_through_the_cycle, venue
    transport and the held-leg read substituted): 383 at 0.522, forward
    probability 0.10, the $10.004 exit on the book. Xavier's persisted
    decision is HOLD; the exit is on the record, blocked by name as worth
    less than holding (its improvement over HOLD stated); HOLD's value is
    forward value less the BOOKED basis, $199.93; nothing is sent."""
    asyncpg = pytest.importorskip("asyncpg")
    from tests import test_xavier_management_defects_reproduced_through_the_cycle as XD
    from sportsassets import bettor_xavier as XV

    conn = await asyncpg.connect(DSN)
    try:
        await XD._clean(conn)
        await XD._seed(conn)
        await XD._entry(conn, qty=QTY, limit=ENTRY_PX)
        await XD._probability(conn, p=0.10)
        book = [{"px": {"value": "%.3f" % EXIT_BID, "currency": "USD"},
                 "qty": str(QTY)}]
        out, sent = await XD._run_cycle(conn, monkeypatch, bids=book)
        assert out["funded_servicing"]["ok"] is True
        rec = (await XV.history(conn, intent_id="xdf-a"))["decisions"][0]
        assert rec["chosen_action"] == "HOLD"
        assert rec["execution_eligibility"] == XV.E_HOLD
        alts = {a["action"]: a for a in rec["alternatives"]}
        hold = alts["HOLD"]
        # THE BOOKED BASIS: the entry's cash to the cent, $199.93
        rb = await XD.FB.remaining_basis(conn, "xdf-a")
        assert float(rb["remaining_basis_usd"]) == pytest.approx(199.93)
        assert hold["expected_net_usd"] == pytest.approx(
            QTY * 0.10 - 199.93, abs=1e-4)
        ex = alts["DIRECT_EXIT"]
        # THE $10 EXIT IS ON THE RECORD -- a full 383 at 0.028, locking a
        # loss -- and it does not win: it is worth 28.30 less than HOLD
        # (-0.0739 a contract), which the selector's churn rule names
        assert ex["qty"] == QTY
        assert ex["proceeds_per_contract"] == pytest.approx(EXIT_BID)
        assert ex["locks_a_loss"] is True
        from sportsassets.workers import ext_pinnacle_loop as L
        assert ex["blocker"] == L.R_BELOW_MIN_IMPROVEMENT
        led = await XD._ledger(conn)
        blocked = next(u for u in led["unrankable"]
                       if u.get("action") == "DIRECT_EXIT")
        assert blocked["improvement_over_hold_per_contract"] == \
            pytest.approx((10.004 - QTY * 0.10) / QTY, abs=1e-5)
        assert "history_is_not_a_reason" in rec["reasoning"]
        assert led["action"] == "HOLD"
        assert XD._creates(sent) == []
    finally:
        await XD._clean(conn)
        await conn.close()
