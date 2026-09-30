"""Deterministic counterexamples; no venue, database or order authority.

The asynchronous tests replace persistence only. Production pricing/composition
runs unchanged. These are not a migrated-database or live-execution gate.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import pytest

from sportsassets import bettor_funded_decision as D
from sportsassets import bettor_funded_pair_cycle as C
from sportsassets import bettor_funded_reservations as R
from sportsassets import bettor_funded_indirect_pair as I
from sportsassets import bettor_funded_execution as E
from sportsassets import bettor_indirect_structures as S
from tests.test_the_position_is_not_the_matched_slice import HELD, OPP


@pytest.mark.parametrize("side", ["ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"])
def test_every_exact_cent_survives_wire_rounding(side):
    for cent in range(1, 100):
        assert E.safe_cent(cent / 100, side) == cent / 100


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "bad", None])
def test_non_numeric_wire_limits_refuse(value):
    assert E.safe_cent(value, E.LONG) is None


@pytest.mark.asyncio
async def test_internal_quote_error_does_not_repeat_the_http_operation():
    from sportsassets import bettor_funded_hedge_supply as supply
    calls = []
    async def quote(slug, side=None):
        calls.append((slug, side))
        raise TypeError("transport decoder failed")
    result, side_aware = await supply._quote_side(quote, "market", "ORDER_INTENT_BUY_SHORT")
    assert calls == [("market", "ORDER_INTENT_BUY_SHORT")]
    assert side_aware and result["error"] == "TypeError"


@pytest.mark.asyncio
async def test_fees_are_priced_for_each_candidates_own_quantity():
    from sportsassets.workers import ext_pinnacle_loop as loop
    result = await loop._fee_for_candidates([
        {"candidate_id": "a", "market_slug": "a", "price": .5, "depth_qty": 2},
        {"candidate_id": "b", "market_slug": "b", "price": .9, "depth_qty": 100}],
        at=1790500000, sport="baseball", wanted_qty=10)
    fees = result["candidate_fees"]
    assert fees["a"]["fee_quantity"] == 2
    assert fees["b"]["fee_quantity"] == 10
    assert fees["a"]["fee_usd"] != fees["b"]["fee_usd"]


@pytest.mark.parametrize("primary,hedge,role,quantity", [
    (10, 6, "PRIMARY", 4), (4, 10, "HEDGE", 6),
    (0, 10, "HEDGE", 10), (10, 0, "PRIMARY", 10),
    (0, 0, None, 0), (6, 6, None, 0)])
def test_unpaired_risk_reports_the_remaining_side(primary, hedge, role, quantity):
    risk = C.residual_pair_risk({"primary_qty": primary, "hedge_qty": hedge,
        "group_id": "g", "event_key": "f", "legs": 2,
        "structure": "MIDDLE", "hedge_intent": None})
    if quantity == 0:
        assert risk is None
    else:
        assert risk["unpaired_role"] == role and risk["unpaired_qty"] == quantity
        assert risk["primary_residual_qty"] == primary
        assert risk["hedge_residual_qty"] == hedge


@pytest.mark.parametrize("probabilities", [
    {"a": -0.1, "b": 1.1}, {"a": float("nan"), "b": .5},
    {"a": float("inf"), "b": .5}, {"a": True, "b": 0},
    {"a": "garbage", "b": .5}, {1: .5, "b": .5},
    {"a": .5, "b": .5, "extra": 0}, {"a": .5},
])
def test_invalid_measures_never_price_a_trade(probabilities):
    table = [{"region": r, "determined": True, "joint_cents": 100}
             for r in ("a", "b")]
    assert D._regions_expected_cents(table, probabilities)["ok"] is False


def test_nonfinite_hold_does_not_authorize_a_priced_alternative():
    result = D.decide(hold_ranking={"candidates": [
        {"action": "HOLD", "value_usd": float("nan")},
        {"action": "DIRECT_EXIT", "value_usd": 1}]})
    assert result["selected"] is None
    assert result["refusal"] == D.R_HOLD_NOT_PRICED


def test_missing_downside_is_not_a_pass_of_the_downside_limit():
    result = D.decide(hold_ranking={"candidates": [
        {"action": "HOLD", "value_usd": 0}]}, indirect={
            "action": D.ACTION_ACQUIRE_INDIRECT_HEDGE, "value_usd": 10,
            "rankable": True, "units": 1, "downside_usd": None},
        limits={"max_downside_usd": 2})
    assert result["selected"] == "HOLD"
    assert result["not_rankable"][0]["blocker"] == D.R_REQUIRED_RISK_MEASUREMENT


def _whole_option(cid="hedge", probabilities=None):
    position = I.position_worst_case(held_leg=HELD, hedge_leg=OPP,
        hedge_qty=6, sport_permits_tie=False, fee_usd=.1, fee_basis="test")
    structure = S.classify(HELD, OPP, sport_permits_tie=False).to_dict()
    # Explicit synthetic terminal measure: B wins with certainty, all other
    # cells (including postponement) have stated zero probability.
    probs = {r["region"]: 0.0 for r in structure["table"]}
    probs[next(r["region"] for r in position["regions"]
               if r["state"] == S.STATE_REGULAR and r["payout_usd"] == 6)] = 1
    return {"admitted": {"condition_id": cid, "structure": structure},
        "candidate_id": cid, "region_probabilities": probabilities or probs,
        "evidence_quality": "SYNTHETIC_TEST_ONLY", "fee_usd": .1,
        "depth": I.depth_supports(wanted_qty=6, depth_qty_at_price=6),
        "incremental": I.incremental_capital_usd(hedge_qty=6,
                            hedge_price=.3, hedge_fee_usd=.1),
        "position_value": position, "plan_digest": "test-plan-" + cid}


@pytest.mark.asyncio
async def test_expected_value_keeps_the_four_uncovered_contracts():
    got = await C._price_indirect(None, **_whole_option())
    candidate = got["candidate"]
    assert candidate["rankable"], got
    # 6 payout - (10*.55 + 6*.30) - .10, not six matched pairs.
    assert candidate["value_usd"] == pytest.approx(-1.4)
    assert candidate["downside_usd"] == pytest.approx(-1.4)
    assert candidate["cost_usd"] == pytest.approx(7.3)
    assert candidate["position_includes_uncovered_inventory"]


@pytest.mark.asyncio
async def test_all_hedges_reach_one_persisted_ev_comparison(monkeypatch):
    writes = []
    async def record(conn, **kwargs):
        writes.append(kwargs)
        return {"ok": True}
    monkeypatch.setattr(C.FL, "record_decision", record)
    def option(cid, payouts, cost):
        # One synthetic partition and ONE probability measure for both choices.
        # Choice 1: .30 in every outcome. Choice 2: -.80 or +1.20,
        # expectation .70 at probabilities .25/.75. All figures recomputed.
        regions = [{"region": r, "payout_usd": payout}
                   for r, payout in zip(("a", "b"), payouts)]
        return dict(_whole_option(cid),
            admitted={"condition_id": cid, "structure": {
                "taxonomy": "MIDDLE", "units": 1, "cost_cents": int(cost * 100)}},
            region_probabilities={"a": .25, "b": .75}, fee_usd=0,
            position_value={"ok": True, "regions": regions, "cost_usd": cost,
                            "whole_position_usd": min(payouts) - cost})
    lower_ev = option("high-floor-low-ev", [1, 1], .7)
    higher_ev = option("low-floor-high-ev", [0, 2], .8)
    higher_ev["plan_digest"] = "winning-plan"
    result = await C.decide_and_record(None, decision_id="decision", account_id="a",
        venue="v", fixture="f", group_id="g", admitted=None,
        hold_ranking={"candidates": [{"action": "HOLD", "value_usd": .2},
                                    {"action": "DIRECT_EXIT", "value_usd": .3}]},
        indirect_options=[lower_ev, higher_ev], now=100)
    assert result["ok"] and len(writes) == 1
    assert result["selected"]["candidate_id"] == "low-floor-high-ev"
    assert result["selected"]["plan_digest"] == "winning-plan"
    assert len(writes[0]["ranking"]["ranked"]) == 4
    assert result["selected"]["value_usd"] == pytest.approx(.7)


@pytest.mark.asyncio
@pytest.mark.parametrize("side,price,wire", [
    ("ORDER_INTENT_BUY_LONG", .31, .31),
    ("ORDER_INTENT_BUY_SHORT", .29, .71)])
async def test_production_quote_uses_correct_side_fields_and_single_price_depth(monkeypatch, side, price, wire):
    from sportsassets.workers import ext_pinnacle_loop as loop
    calls = []
    async def read(conn, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "read_at": 100,
            "book_currency": {"book_state_established_at_epoch_s": 98, "bound_s": 10},
            "acquisition_ladder": {"levels": [
                {"acquisition_price": price, "api_price": wire, "qty": 4},
                {"acquisition_price": price + .1, "qty": 500}]}}
    monkeypatch.setattr(loop, "venue_quote", read)
    got = await loop._candidate_quote(None, "hedge", side, now=100)
    assert calls[0]["intent"] == side
    assert got["price"] == price and got["depth_qty"] == 4
    assert got["api_price"] == wire
    assert got["inputs_expire_at"] == min(108, 100 + loop.MAX_OUR_PROCESSING_DELAY_S)


@pytest.mark.parametrize("side,cost,wire", [
    ("ORDER_INTENT_BUY_LONG", .3, .3),
    ("ORDER_INTENT_BUY_SHORT", .3, .7),
    ("ORDER_INTENT_BUY_SHORT", .71, .29)])
def test_acquisition_wire_price_and_collateral_describe_the_same_side(side, cost, wire):
    from tests.test_the_selected_side_is_the_side_that_is_ordered import winner, row, HELD_SLUG
    cid = "hedge#" + side
    plan = C.acquisition_plan_for(winner=winner(cid),
        ranked_row=row(cid, price=cost), account_id="a", venue="v", group_id="g",
        held_position={"us_market_slug": HELD_SLUG}, inputs_expire_at=150)
    assert plan.limit_price == wire
    assert plan.collateral_usd == pytest.approx(plan.quantity * cost)


@pytest.mark.asyncio
async def test_expired_acquisition_stops_before_reserving_or_sending():
    from tests.test_the_selected_side_is_the_side_that_is_ordered import plan
    p = plan()
    got = await C.acquire_second_leg(None, operation_id="op", group_id="grp-1",
        decision_record={}, account_id="acct", venue="PMUS", plan=p,
        expect_digest=p.digest, now=p.inputs_expire_at + 1)
    assert got["refusal"] == "ACQUISITION_INPUTS_EXPIRED"
    assert got["nothing_was_sent"]


@pytest.mark.asyncio
@pytest.mark.parametrize("persisted", [True, False])
async def test_scheduled_pass_compares_all_contracts_then_sends_only_the_persisted_winner(monkeypatch, persisted):
    """Real discovery, payoff, EV, plan building and dispatch orchestration.

    Database reads/writes and the final acquisition boundary are substituted;
    no candidate, ranking or selected action is injected into the pass.
    """
    money = replace(OPP, condition_id="money#ORDER_INTENT_BUY_SHORT")
    middle = replace(OPP, condition_id="middle#ORDER_INTENT_BUY_SHORT",
                     kind=S.KIND_SPREAD, line=Fraction(-9, 2), cost_cents_per_unit=45)
    # `event_key` IS ON EVERY REAL FUNDED INTENT: `plan_from_decision` refuses an
    # entry without one (R_NO_EVENT_KEY), because MAX_EVENT_EXPOSURE is enforced
    # per event. This fixture omitted it, and the hedge admission record now
    # refuses a hedge whose held position names no event -- asserted below in
    # `test_a_hedge_on_a_position_with_no_event_is_not_dispatched`.
    position = {"intent_id": "held-intent", "portfolio_group_id": "group",
                "us_market_slug": "held", "residual_qty": 10, "filled_qty": 10,
                "event_key": "event-held"}
    measures = {}
    for leg in (money, middle):
        structure = S.classify(HELD, leg, sport_permits_tie=False)
        p = {r["region"]: 0 for r in structure.table}
        # Synthetic event has margin +1: A's moneyline and B+4.5 both pay.
        pays = [r for r in structure.table if r["state"] == S.STATE_REGULAR
                and r["per_leg_cents"][0] == 100]
        chosen = max(pays, key=lambda r: r["joint_cents"])
        p[chosen["region"]] = 1
        measures[leg.condition_id] = p
    facts = {"ok": True, "held_leg": HELD, "candidate_legs": [money, middle],
        "sport_permits_tie": False, "decision_id": "decision", "operation_id": "operation",
        "hold_ranking": {"candidates": [
            {"action": "HOLD", "value_usd": 4.5},
            {"action": "DIRECT_EXIT", "value_usd": 4.7}]},
        "region_probabilities_by_candidate": measures,
        "candidate_leg_details": [{"candidate_id": leg.condition_id,
             "price": leg.cost_cents_per_unit / 100, "depth_qty": 6,
             "fee_usd": .1, "inputs_expire_at": 200} for leg in (money, middle)]}
    events, records, orders = [], [], []
    async def live(*args, **kwargs):
        return []
    async def positions(*args, **kwargs):
        return [position]
    async def supplier(*args, **kwargs):
        return facts
    async def record(conn, **kwargs):
        events.append("persist")
        records.append(kwargs)
        return {"ok": persisted, "refusal": None if persisted else "DB_UNAVAILABLE"}
    async def send(conn, **kwargs):
        events.append("send")
        orders.append(kwargs)
        return {"ok": True, "submitted": True}
    monkeypatch.setattr(C.RSV, "live", live)
    monkeypatch.setattr(C.FB, "open_entry_positions", positions)
    monkeypatch.setattr(C.FL, "record_decision", record)
    monkeypatch.setattr(C, "acquire_second_leg", send)
    # XAVIER'S PRE-ACTION RECORD AND DISPATCH CLAIM are database writes as
    # well, and every dispatch now requires both; substituted like the ledger
    # write above because this harness has no database.
    from sportsassets import bettor_xavier as XV

    async def xrecord(conn, **kwargs):
        return {"ok": True, "refusal": None, "xavier_decision_id": "xav:t"}

    async def xclaim(conn, **kwargs):
        return {"ok": True, "claimed": True, "refusal": None}

    async def xevents(conn, **kwargs):
        return {"ok": True, "written": False, "refusal": None}
    monkeypatch.setattr(XV, "record_decision", xrecord)
    monkeypatch.setattr(XV, "claim_dispatch", xclaim)
    monkeypatch.setattr(XV, "record_dispatch", xevents)

    # THE GROUP LOCK AND THE QUANTITY RE-READS, likewise: taken, and finding
    # this harness's position unchanged under the lock and before the send.
    async def xlock(conn, key):
        return {"ok": True, "key": key, "refusal": None}

    async def xquantities(conn, *, intent_ids, group_id=None):
        return {"ok": True, "orders_in_flight": [],
                "legs": {position["intent_id"]: {
                    "residual": float(position["residual_qty"])}}}
    monkeypatch.setattr(XV, "try_group_lock", xlock)
    monkeypatch.setattr(XV, "group_quantities", xquantities)
    got = await C.pass_once(None, account_id="account", venue="PMUS",
                            pair_inputs=supplier, now=100)
    step = got["considered"][0]
    assert len(records) == 1, step
    hedges = [c for c in records[0]["ranking"]["ranked"]
              if c["action"] == D.ACTION_ACQUIRE_INDIRECT_HEDGE]
    assert len(hedges) == 2, step
    assert step["decision"]["selected"]["candidate_id"] == middle.condition_id
    if persisted:
        assert events == ["persist", "send"]
        assert orders[0]["plan"].candidate_id == middle.condition_id
        assert orders[0]["plan"].limit_price == .55
        assert orders[0]["expect_digest"] == step["decision"]["selected"]["plan_digest"]
    else:
        assert events == ["persist"] and not orders


def _absence():
    reservation = {"operation_id": "op", "intent_id": "intent", "us_market_slug": "market"}
    evidence = dict(reservation, account_id="account", group_account="account",
        venue="venue", group_venue="venue", kind=R.EV_NO_SUCH_ORDER,
        search_endpoint="/orders/history", covered_terminal_orders=True,
        results_returned=0, intent_sent_at=100, window_from_epoch_s=90,
        window_to_epoch_s=120, read_at=125,
        search_scope={"operation_id": "op", "intent_id": "intent", "status": "ALL",
                      "pagination_complete": True, "complete_through_epoch_s": 120})
    return evidence, reservation


def test_complete_correlated_absence_remains_usable():
    e, r = _absence()
    assert R.evidence_matches_operation(e, r)["ok"]


@pytest.mark.parametrize("field,value", [
    ("account_id", "other"), ("venue", "other"), ("intent_id", "other"),
    ("us_market_slug", "other"), ("window_from_epoch_s", 101),
    ("window_to_epoch_s", 99), ("intent_sent_at", None),
    ("read_at", 119), ("search_endpoint", "/orders/open"),
    ("covered_terminal_orders", False), ("results_returned", 1),
])
def test_unrelated_or_incomplete_search_cannot_free_capacity(field, value):
    e, r = _absence()
    e[field] = value
    assert not R.evidence_matches_operation(e, r)["ok"]


@pytest.mark.parametrize("field,value", [
    ("operation_id", "other"), ("intent_id", "other"), ("status", "OPEN"),
    ("pagination_complete", False), ("complete_through_epoch_s", None),
    ("complete_through_epoch_s", 99), ("complete_through_epoch_s", float("nan")),
])
def test_search_scope_and_visibility_are_required(field, value):
    e, r = _absence()
    e["search_scope"][field] = value
    assert not R.evidence_matches_operation(e, r)["ok"]
