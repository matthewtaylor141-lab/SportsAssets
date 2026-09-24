"""THE SELECTED FIELD IS FILLED, OR THE REASON IT IS NOT HAS A NAME.

Owner directive, "MAKE THE FULL EXIT POLICY OPERATIONAL" §2:

    "Choose an action and quantity, with a recorded reason. An unranked
     action table or permanently empty selected field does not meet this
     requirement."

WHAT WAS ACTUALLY WRONG, AND IT WAS NOT THAT THE ENGINE WAS MISSING.
Every part existed. `bettor_exit_engine.evaluate` enumerated and COSTED
all eight residual actions and returned status
EXECUTION_COSTS_IDENTIFIED_RANKING_NOT_IDENTIFIED with `bestAction:
NOT_IDENTIFIED`, because every row is built by `_unranked(...)` and
ranking needs EV_HOLD. `bettor_mgmt_select.select` sat on top and
selected nothing, correctly, for the same reason.

So the gap was ONE NUMBER: the value of doing nothing. Migration 108
made it obtainable by fixing the payout identity on the external
probability; `bettor_hold_value` supplies it; the challenger ranking
consumes it. These tests pin the behaviour that number unlocks, and
the three refusals that must survive it.

THE THREE FAILURES THESE EXIST FOR, ALL THREE MINE, ALL THREE CAUGHT IN
THE FIRST SMOKE RUN OF THIS CODE:

  1 with EV_HOLD unpriced, ranking the remaining candidates selected an
    exit EVERY TIME -- "liquidating the book for want of a settlement
    model", which is the exact failure `select` was written to refuse.
    The named fallback closes it.
  2 a fallback trigger that EVALUATED NOTHING (no last price, no time
    open) returns fired=False, and that was being read as a decision to
    hold. A rule that looked at nothing did not decide.
  3 an action whose VENUE TRANSLATION was refused was still ranked and
    selected. The economics were computable; the inventory consequence
    was not, and an action we cannot state the consequence of is not
    available.
"""
import pytest

from sportsassets import bettor_hold_value as HV
from sportsassets import bettor_mgmt_lifecycle as LC
from sportsassets import bettor_mgmt_select as MS
from sportsassets import bettor_venue_position_model as VPM

SLUG = "aec-mlb-chc-mia-2026-09-24-cubs"
PAYS_ON = "Chicago Cubs"


def _fee(qty, price, maker=False):
    return 0.01 * float(qty) * float(price)


def _managed(qty=100.0, price=0.57):
    return LC.Managed(condition_id=SLUG, outcome_index=0, seed_qty=qty,
                      seed_price=price, at=1000.0, fee_fn=_fee)


def _row(p=0.70, observed_at=900.0, **kw):
    base = {"id": 42, "provider": "PINNACLE", "book": "pinnacle",
            "devig_method": "power", "us_market_slug": SLUG,
            "probability": p, "probability_event": PAYS_ON,
            "payout_event": PAYS_ON, "payout_is_complement": False,
            "buy_intent": "ORDER_INTENT_BUY_LONG",
            "matched_side_norm": "cubs", "resolver_asked_for": PAYS_ON,
            "ladder_side": "ASK", "observed_at": observed_at,
            "received_at": observed_at + 1.0, "eligibility": "ELIGIBLE"}
    base.update(kw)
    return base


def _ev(p=0.70, now=1000.0, qty=100.0, basis=0.57, **kw):
    return HV.ev_hold(qty=qty, basis_per_contract=basis,
                      probability_row=_row(p, **kw), now=now,
                      payout_event_held=PAYS_ON)


# ── EV_HOLD: the number the whole stack was waiting for ──────────────

def test_ev_hold_is_priced_from_the_external_probability():
    ev = _ev(p=0.70)
    assert ev["status"] == "IDENTIFIED"
    # 0.70 x 100 - 57.00
    assert ev["ev_hold_usd"] == pytest.approx(13.0, abs=1e-9)
    assert ev["probability_event"] == PAYS_ON
    assert ev["provenance"]["is_a_model_we_fitted"] is False
    # and it says what it is NOT
    assert "validated expected value" in ev["is_not"]


def test_an_unknown_probability_is_never_zero():
    ev = HV.ev_hold(qty=100.0, basis_per_contract=0.57,
                    probability_row=None, now=1000.0,
                    payout_event_held=PAYS_ON)
    assert ev["status"] == HV.NOT_IDENTIFIED
    assert ev["ev_hold_usd"] is None
    assert ev["refusal"] == HV.R_NO_SOURCE
    assert "NOT zero" in ev["why"]


def test_a_held_row_never_reaches_a_decision():
    ev = _ev(eligibility="INELIGIBLE_PAYOUT_IDENTITY_UNVERIFIED",
             ineligible_reason="written before migration 108")
    assert ev["status"] == HV.NOT_IDENTIFIED
    assert ev["refusal"] == HV.R_INELIGIBLE


def test_a_row_that_prices_a_different_event_is_refused_not_flipped():
    """THE 42a68c4 DEFECT, closed at the consuming end too."""
    ev = HV.ev_hold(qty=100.0, basis_per_contract=0.57,
                    probability_row=_row(payout_event="NOT(Chicago Cubs)",
                                         probability_event="NOT(Chicago Cubs)"),
                    now=1000.0, payout_event_held=PAYS_ON)
    assert ev["refusal"] == HV.R_PAYOUT_MISMATCH
    assert ev["ev_hold_usd"] is None


def test_staleness_is_measured_from_the_bookmakers_own_stamp():
    fresh = _ev(now=1000.0, observed_at=900.0)
    assert fresh["freshness"]["aged_against"] == "OBSERVATION"
    assert fresh["freshness"]["age_from_observation_s"] == pytest.approx(100.0)
    stale = _ev(now=1000.0, observed_at=1000.0 - HV.MAX_PROBABILITY_AGE_S - 1)
    assert stale["refusal"] == HV.R_STALE


# ── the venue's actual position model ────────────────────────────────

def test_buying_the_other_side_reduces_and_makes_no_second_leg():
    t = VPM.translate("TAKE_COMPLEMENT", venue="PMUS", held_qty=100.0,
                      us_market_slug=SLUG, requested_qty=100.0)
    assert t["ok"] is True
    assert t["net_effect"] == "REDUCE"
    assert t["creates_second_leg"] is False
    assert t["matched_pair_created"] is False
    assert t["locked_pnl_claimable"] is False
    # the intent is what names the side, and only that
    assert t["venue_order"]["intent"] == VPM.BUY_SHORT
    # and no merge is manufactured out of it
    assert VPM.translate("MERGE", venue="PMUS", held_qty=100.0
                         )["capital_release"] == VPM.NOT_APPLICABLE


def test_the_two_token_venue_keeps_its_matched_pair_and_its_refusal():
    t = VPM.translate("TAKE_COMPLEMENT", venue="POLYMARKET", held_qty=100.0,
                      requested_qty=100.0)
    assert t["creates_second_leg"] is True
    assert t["matched_pair_created"] is True
    assert t["capital_release"] == VPM.NOT_IDENTIFIED
    m = VPM.translate("MERGE", venue="POLYMARKET", held_qty=100.0)
    assert m["ok"] is False
    assert m["capital_release"] == VPM.NOT_IDENTIFIED


def test_a_reduction_is_capped_and_never_becomes_a_reversal():
    t = VPM.translate("DIRECT_EXIT", venue="PMUS", held_qty=100.0,
                      requested_qty=150.0)
    assert t["qty"] == pytest.approx(100.0)
    assert t["capped"] is True
    assert t["fully_closes"] is True
    assert t["net_after"] == pytest.approx(0.0)


def test_an_unknown_venue_is_refused_not_defaulted():
    t = VPM.translate("DIRECT_EXIT", venue="SOMEWHERE", held_qty=10.0)
    assert t["ok"] is False
    assert t["refusal"] == VPM.R_VENUE_MODEL_NOT_ESTABLISHED


# ── the decision: an action AND a quantity ───────────────────────────

def test_hold_wins_when_the_book_pays_less_than_holding():
    m = _managed()
    d = m.decide_challenger(at=1001.0, ev_hold=_ev(0.70), bid=0.62,
                            bid_size=500.0, complement_ask=0.39,
                            complement_ask_size=500.0, venue="PMUS",
                            us_market_slug=SLUG, last_price=0.62,
                            seconds_open=60.0, decision_id="d")
    assert d["selected_action"] == "HOLD"
    assert d["selected_qty"] == pytest.approx(100.0)
    assert d["is_a_deliberate_hold"] is True
    assert d["operating_state"] == "HOLD_BY_DECISION"
    # HOLD was IN the comparison, not excluded from it
    actions = [a["action"] for a in d["alternatives"]]
    assert "HOLD" in actions and "DIRECT_EXIT" in actions
    assert d["selection_reason"]


def test_the_cheaper_exit_ladder_wins_and_is_still_a_reduction():
    """SwissTony survives: buying the other side can beat selling ours."""
    m = _managed()
    d = m.decide_challenger(at=1002.0, ev_hold=_ev(0.70), bid=0.80,
                            bid_size=500.0, complement_ask=0.15,
                            complement_ask_size=500.0, venue="PMUS",
                            us_market_slug=SLUG, last_price=0.80,
                            seconds_open=60.0, decision_id="d")
    assert d["selected_action"] == "TAKE_COMPLEMENT"
    # 1 - 0.15 = 0.85 beats the 0.80 bid
    tc = next(c for c in d["ranking"]["candidates"]
              if c["action"] == "TAKE_COMPLEMENT")
    assert tc["equivalent_sale_price"] == pytest.approx(0.85)
    assert tc["creates_second_leg"] is False
    assert tc["collateral_released_now"] is True


def test_sell_some_is_the_quantity_the_book_pays_a_premium_for():
    """THE PARTIAL SIZE IS DERIVED, NOT A FRACTION SOMEBODY PICKED."""
    ladder = {"levels": [
        {"level": 1, "acquisition_price": 0.80, "qty": 30.0},
        {"level": 2, "acquisition_price": 0.72, "qty": 40.0},
        {"level": 3, "acquisition_price": 0.60, "qty": 200.0}]}
    m = _managed()
    d = m.decide_challenger(at=1003.0, ev_hold=_ev(0.70), bid=0.80,
                            bid_size=30.0, sale_ladder=ladder,
                            venue="PMUS", us_market_slug=SLUG,
                            last_price=0.80, seconds_open=60.0,
                            decision_id="d")
    assert d["selected_action"] == "REDUCE"
    # levels 1 and 2 beat holding at 0.70 net of fees; level 3 at 0.60
    # does not, and that is where it stops.
    assert d["selected_qty"] == pytest.approx(70.0)
    marg = d["ranking"]["marginal_sale"]
    assert [lv["level"] for lv in marg["taken"]] == [1, 2]
    assert marg["skipped"][0]["level"] == 3
    assert marg["covers_whole_position"] is False


def test_a_loss_limiting_completion_is_permitted_not_refused():
    """Completing above par locks a loss and must still be selectable."""
    m = _managed(price=0.57)
    # the leg is collapsing: hold is worth 0.05, the bid is 0.02, and
    # the other side costs 0.90 (equivalent sale at 0.10)
    d = m.decide_challenger(at=1004.0, ev_hold=_ev(0.05), bid=0.02,
                            bid_size=500.0, complement_ask=0.90,
                            complement_ask_size=500.0, venue="PMUS",
                            us_market_slug=SLUG, last_price=0.02,
                            seconds_open=60.0, decision_id="d")
    assert d["selected_action"] == "TAKE_COMPLEMENT"
    tc = next(c for c in d["ranking"]["candidates"]
              if c["action"] == "TAKE_COMPLEMENT")
    assert tc["locks_a_loss"] is True
    assert "LOCKS A LOSS" in d["selection_reason"]


# ── the three refusals that must survive a priced HOLD ───────────────

def test_without_ev_hold_it_does_not_liquidate_the_book():
    """FAILURE 1. Ranking the priced subset alone selects an exit every
    time, because it is the only action carrying a number."""
    m = _managed()
    ev = HV.ev_hold(qty=100.0, basis_per_contract=0.57,
                    probability_row=None, now=1000.0,
                    payout_event_held=PAYS_ON)
    d = m.decide_challenger(at=1005.0, ev_hold=ev, bid=0.62,
                            bid_size=500.0, complement_ask=0.39,
                            complement_ask_size=500.0, venue="PMUS",
                            us_market_slug=SLUG, last_price=0.62,
                            seconds_open=60.0, decision_id="d")
    assert d["selected_action"] == "HOLD"
    assert d["operating_state"] == "HOLD_BY_FALLBACK_RULE"
    assert MS.FALLBACK_RULE in d["governing_rule"]
    assert d["is_a_deliberate_hold"] is True
    assert not m.open_orders(), "no order may be placed on this path"


def test_the_fallback_still_closes_a_position_that_is_collapsing():
    """...and it is not a refusal to ever act. A 30% adverse move fires
    EXPOSURE_TRIGGER_RULE_V1 and the METHOD is then ranked."""
    m = _managed()
    ev = HV.ev_hold(qty=100.0, basis_per_contract=0.57,
                    probability_row=None, now=1000.0,
                    payout_event_held=PAYS_ON)
    d = m.decide_challenger(at=1006.0, ev_hold=ev, bid=0.40,
                            bid_size=500.0, complement_ask=0.55,
                            complement_ask_size=500.0, venue="PMUS",
                            us_market_slug=SLUG, last_price=0.40,
                            seconds_open=60.0, decision_id="d")
    assert d["selected_action"] in ("DIRECT_EXIT", "TAKE_COMPLEMENT")
    assert MS.FALLBACK_RULE in d["governing_rule"]
    assert "does NOT establish that acting beat holding" in \
        d["selection_reason"]


def test_a_fallback_that_evaluated_nothing_did_not_decide_to_hold():
    """FAILURE 2. No last price and no time open leaves both trigger
    conditions NOT_IDENTIFIED, and fired=False then means 'blind', not
    'chose to wait'."""
    m = _managed()
    ev = HV.ev_hold(qty=100.0, basis_per_contract=0.57,
                    probability_row=None, now=1000.0,
                    payout_event_held=PAYS_ON)
    d = m.decide_challenger(at=1007.0, ev_hold=ev, bid=0.62,
                            bid_size=500.0, venue="PMUS",
                            us_market_slug=SLUG, decision_id="d")
    assert d["selected_action"] is None
    assert d["operating_state"] == "HOLD_FOR_MISSING_INPUT"
    assert d["is_a_deliberate_hold"] is False
    # AND IT IS NOT SILENT. An empty selection with no reason would not
    # meet the requirement; this one names what was missing.
    assert "NOTHING SELECTED" in d["selection_reason"]
    assert "did not decide to hold" in d["selection_reason"]


def test_an_action_the_venue_model_cannot_translate_is_not_selectable():
    """FAILURE 3. The economics were computable and the INVENTORY
    CONSEQUENCE was not."""
    m = _managed()
    d = m.decide_challenger(at=1008.0, ev_hold=_ev(0.70), bid=0.99,
                            bid_size=500.0, venue="AN_UNKNOWN_VENUE",
                            us_market_slug=SLUG, last_price=0.99,
                            seconds_open=60.0, decision_id="d")
    assert d["selected_action"] != "DIRECT_EXIT"
    refused = {r["action"]: r["blocker"] for r in d["refused"]}
    assert refused["DIRECT_EXIT"] == VPM.R_VENUE_MODEL_NOT_ESTABLISHED
    assert not m.open_orders()


def test_a_tiny_improvement_does_not_churn_the_book():
    m = _managed()
    # HOLD is worth 0.70 a contract. The fee is 1% of notional, so a bid
    # of 0.7085 nets 0.7085 x 0.99 = 0.70142 -- it GENUINELY BEATS
    # HOLDING, by 0.00142, which is below the declared 0.005 minimum.
    # The gate has to be reached by an exit that actually wins, or it is
    # not the gate being tested; an earlier version of this test used
    # 0.7005, which nets BELOW hold and lost on value alone.
    d = m.decide_challenger(at=1009.0, ev_hold=_ev(0.70), bid=0.7085,
                            bid_size=500.0, venue="PMUS",
                            us_market_slug=SLUG, last_price=0.7085,
                            seconds_open=60.0, decision_id="d")
    gain = d["ranking"]["improvement_over_hold_per_contract"]
    assert 0 < gain < MS.MIN_IMPROVEMENT_USD_PER_CONTRACT, gain
    assert d["ranking"]["runner_up"] == "DIRECT_EXIT"
    assert d["selected_action"] == "HOLD"
    assert "MIN_IMPROVEMENT" in d["governing_rule"]
    assert d["is_a_deliberate_hold"] is True


# ── the resting order, and the one-active-order discipline ───────────

def test_one_active_order_survives_the_challenger():
    assert LC.SUPPORTS_SIMULTANEOUS is False
    m = _managed()
    m.decide_challenger(at=1010.0, ev_hold=_ev(0.70), bid=0.90,
                        bid_size=500.0, venue="PMUS", us_market_slug=SLUG,
                        last_price=0.90, seconds_open=60.0, decision_id="a")
    assert len(m.open_orders()) == 1
    # a different price selects a different order: the incumbent is
    # cancelled and the replacement WAITS for the acknowledgement.
    d = m.decide_challenger(at=1011.0, ev_hold=_ev(0.70), bid=0.95,
                            bid_size=500.0, venue="PMUS",
                            us_market_slug=SLUG, last_price=0.95,
                            seconds_open=61.0, decision_id="b")
    assert d["operating_state"] == "WAIT_CANCEL_ACK"
    assert d["resting_order_decision"]["decision"] == "CANCEL_THEN_REPLACE"
    assert len(m.open_orders()) == 1


def test_an_unchanged_intent_keeps_its_queue_position():
    m = _managed()
    m.decide_challenger(at=1012.0, ev_hold=_ev(0.70), bid=0.90,
                        bid_size=500.0, venue="PMUS", us_market_slug=SLUG,
                        last_price=0.90, seconds_open=60.0, decision_id="a")
    first = m.open_orders()[0].order_id
    d = m.decide_challenger(at=1013.0, ev_hold=_ev(0.70), bid=0.90,
                            bid_size=500.0, venue="PMUS",
                            us_market_slug=SLUG, last_price=0.90,
                            seconds_open=61.0, decision_id="b")
    assert d["resting_order_decision"]["decision"] == "MAINTAIN"
    assert m.open_orders()[0].order_id == first


def test_selecting_hold_cancels_an_order_nobody_decided_to_have():
    m = _managed()
    m.decide_challenger(at=1014.0, ev_hold=_ev(0.70), bid=0.90,
                        bid_size=500.0, venue="PMUS", us_market_slug=SLUG,
                        last_price=0.90, seconds_open=60.0, decision_id="a")
    assert len(m.open_orders()) == 1
    d = m.decide_challenger(at=1015.0, ev_hold=_ev(0.70), bid=0.50,
                            bid_size=500.0, venue="PMUS",
                            us_market_slug=SLUG, last_price=0.50,
                            seconds_open=61.0, decision_id="b")
    assert d["selected_action"] == "HOLD"
    assert d["resting_order_decision"]["decision"] == "CANCEL"
    assert m.orders[d["cancelled_orders"][0]].state == "CANCEL_PENDING"


def test_a_fill_that_races_a_pending_cancel_is_still_booked():
    m = _managed()
    m.decide_challenger(at=1016.0, ev_hold=_ev(0.70), bid=0.90,
                        bid_size=500.0, venue="PMUS", us_market_slug=SLUG,
                        last_price=0.90, seconds_open=60.0, decision_id="a")
    m.decide_challenger(at=1017.0, ev_hold=_ev(0.70), bid=0.50,
                        bid_size=500.0, venue="PMUS", us_market_slug=SLUG,
                        last_price=0.50, seconds_open=61.0, decision_id="b")
    fills = m.on_print(at=1018.0, outcome_index=0, price=0.92, size=200.0,
                       evidence_id="t1")
    assert fills and fills[0]["raced_a_pending_cancel"] is True
    assert m.residual() < 100.0


# ── the decision is inspectable ──────────────────────────────────────

def test_every_decision_carries_the_whole_field_list():
    m = _managed()
    d = m.decide_challenger(at=1019.0, ev_hold=_ev(0.70), bid=0.62,
                            bid_size=500.0, venue="PMUS",
                            us_market_slug=SLUG, last_price=0.62,
                            seconds_open=60.0, decision_id="d")
    for field in ("at", "residual_qty", "held_qty", "matched_qty",
                  "basis_per_contract", "policy_id", "governing_rule",
                  "selected_action", "selected_qty", "selection_reason",
                  "operating_state", "is_a_deliberate_hold",
                  "hold_input", "alternatives", "refused",
                  "venue_translation", "resting_order_decision",
                  "resulting_inventory"):
        assert field in d, field
    hi = d["hold_input"]
    assert hi["available"] is True
    assert hi["freshness"]["age_from_observation_s"] is not None
    assert hi["identity"]["row_payout_event"] == PAYS_ON
    assert hi["provenance"]["is_a_model_we_fitted"] is False
    assert d["resulting_inventory"]["invariant_ok"] is True


def test_the_frozen_benchmark_is_not_modified():
    """§1: the $0.91 / second-half-16% experiment stays the benchmark."""
    from sportsassets import bettor_rn1x_policy as pol

    assert pol.POLICY_ID == "MANAGEMENT_PAIR_091_STOP_16_V1"
    assert pol.PAIR_TARGET_COST == 0.91
    assert pol.LOSS_TRIGGER_FRACTION == 0.84
    assert pol.FROZEN_AT == "2026-09-23"
    # the challenger is a DIFFERENT id, so no row can be mistaken
    assert MS.CHALLENGER_ID != pol.POLICY_ID
    assert MS.RULE_ID == "PRICED_ACTION_RANKING_V1"
    assert MS.TRIGGER_ID == "EXPOSURE_TRIGGER_RULE_V1"


def test_the_challenger_declares_what_it_is_not():
    obj = MS.CHALLENGER_OBJECTIVE
    assert "optimal" in obj["not_the_goal"] or \
        "optimality" in obj["not_the_goal"]
    assert obj["constraints"]
    assert obj["tie_breaking"]
    assert obj["min_improvement_usd_per_contract"] > 0
    assert "never" in HV.describe()
