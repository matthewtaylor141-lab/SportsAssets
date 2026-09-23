"""ROUTING MATCHED AND RESIDUAL TO THE ENGINES THAT ALREADY EXIST.

The failure this prevents is specific and it is the Ferrari residual
failure in miniature: a book holding both legs looks hedged, both
engines decline it as the other's problem, and the directional remainder
quietly leaves management.
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

from sportsassets import bettor_exit_engine as EE               # noqa: E402
from sportsassets import bettor_inventory as BINV               # noqa: E402
from sportsassets import bettor_mgmt_router as R                # noqa: E402
from sportsassets import bettor_mgmt_select as MS               # noqa: E402
from sportsassets import bettor_pair_engine as PE               # noqa: E402

CONFIRMED = "EXACT_ONE_TO_COMPLEMENT_BASKET"
PENDING = ("STRUCTURALLY_IDENTIFIED_COMPLEMENT_PENDING_"
           "INSTITUTIONAL_CONFIRMATION")


def fee(qty, price):
    return round(0.01 * qty * price, 6)


def rows(yes_qty="100", yes_px="0.45", no_qty="40", no_px="0.50"):
    out = []
    if yes_qty:
        out.append({"leg": "YES", "qty": yes_qty, "price": yes_px,
                    "position_id": "p_y"})
    if no_qty:
        out.append({"leg": "NO", "qty": no_qty, "price": no_px,
                    "position_id": "p_n"})
    return out


def route(**kw):
    kw.setdefault("identity_status", CONFIRMED)
    kw.setdefault("held_book", {"bid": "0.52"})
    kw.setdefault("complement_book", {"ask": "0.49"})
    return R.route(rows(**{k: v for k, v in kw.items()
                          if k in ("yes_qty", "yes_px", "no_qty", "no_px")}),
                   identity_status=kw["identity_status"],
                   held_book=kw["held_book"],
                   complement_book=kw["complement_book"],
                   seconds_unpaired=kw.get("seconds_unpaired", 600))


# ── the split ────────────────────────────────────────────────────────

def test_100_yes_and_40_no_is_40_matched_and_60_residual_yes():
    """THE CASE THAT NAMES THE WHOLE PROBLEM."""
    out = route()
    s = out["split"]
    assert float(s["MATCHED_QTY"]) == 40
    assert float(s["RESIDUAL_YES_QTY"]) == 60
    assert float(s["RESIDUAL_NO_QTY"]) == 0
    assert s["pairStatus"] == BINV.PAIR_CONFIRMED


def test_the_split_reconciles_to_the_legs_it_started_with():
    """A routing bug that dropped inventory would leave part of the
    position unmanaged and nothing else would notice."""
    out = route()
    assert out["split_reconciles"] is True
    c = out["split_check"]
    assert c["matched_plus_residual_yes"] == c["yes_qty"]
    assert c["matched_plus_residual_no"] == c["no_qty"]


def test_holding_both_legs_does_NOT_remove_the_residual_from_management():
    """THE FAILURE THIS FILE EXISTS FOR. Both engines decline a
    both-legs book -- each says it is the other's domain -- so an
    unrouted position would go unmanaged while looking hedged."""
    out = route()
    assert [r["leg"] for r in out["residuals"]] == ["YES"]
    assert out["residuals"][0]["qty"] == 60.0
    # and the engine really did evaluate it
    assert len(out["residuals"][0]["actions"]) == 8


def test_both_engines_decline_the_unrouted_both_legs_book():
    """THE CONTROL. Without it, the routing above could be solving a
    problem that did not exist."""
    both = {"YES_QTY": "100", "YES_AVG_BASIS": "0.45",
            "NO_QTY": "40", "NO_AVG_BASIS": "0.50"}
    assert EE.evaluate(both).get("actions") == []
    assert PE.pair_view(both).get("status") == "NOT_IDENTIFIED"
    assert "one leg held" in EE.evaluate(both)["why"]


def test_an_unconfirmed_pair_claims_no_matched_quantity():
    """A locked P&L across an unconfirmed pair would book profit on an
    assumption about which contract pays."""
    out = route(identity_status=PENDING)
    assert out["split"]["MATCHED_QTY"] == R.NOT_IDENTIFIED
    assert out["matched"]["capital_release"] == R.NOT_IDENTIFIED


# ── the matched part: retained, never released ───────────────────────

def test_matched_inventory_is_retained_and_its_capital_stays_occupied():
    out = route()
    m = out["matched"]
    # 0.45 + 0.50 = 0.95 pair basis; 40 pairs occupy 38.00; the pair
    # pays 1.00 so 0.05 x 40 = 2.00 is locked.
    assert abs(float(m["pair_basis"]) - 0.95) < 1e-9
    assert abs(float(m["capital_occupied"]) - 38.0) < 1e-9
    assert abs(float(m["locked_pnl"]) - 2.0) < 1e-9
    assert m["action_available"] == "RETAIN_MATCHED"


def test_no_capital_release_is_manufactured():
    """It is NOT_IDENTIFIED, and specifically not zero -- zero would
    assert that completing frees nothing, a claim about the venue we
    have not established."""
    m = route()["matched"]
    assert m["capital_release"] == R.NOT_IDENTIFIED
    assert m["merge"] == R.NOT_IDENTIFIED
    assert "MERGE_MECHANISM" in m["capital_release_why"]


# ── the residual part: the real engine, and no selection ─────────────

def test_the_residual_goes_to_the_REAL_exit_engine():
    out = route()
    assert "bettor_exit_engine.evaluate" in out["engines_used"]
    assert out["residuals"][0]["engine_status"] == \
        "EXECUTION_COSTS_IDENTIFIED_RANKING_NOT_IDENTIFIED"


def test_nothing_is_selected_and_the_reason_is_the_missing_ranking():
    r = route()["residuals"][0]
    assert r["selected"] is None
    assert "EV_VS_HOLD = NOT_IDENTIFIED" in r["selection_reason"]


def test_the_resting_actions_are_flagged_as_needing_our_fill():
    r = route()["residuals"][0]
    assert set(r["requires_our_fill"]) == {"POST_COMPLEMENT",
                                          "WAIT_REQUOTE"}


def test_the_objective_says_it_is_NOT_ev_maximisation():
    out = route()
    assert "maximising expected value" in out["objective"]["not_the_goal"]
    assert "experimental_rules_are_allowed" in out["objective"]


def test_every_input_carries_a_model_rule_or_assumption_label():
    lab = route()["labels"]
    assert lab["queue_share"] == "ASSUMPTION"
    assert lab["selection_hurdle"].startswith("RULE")
    assert lab["EV_VS_HOLD"].startswith("NOT_IDENTIFIED")
    assert "FORBIDDEN" in lab["P_PAIR_COMPLETION"]


# ── the priced-action ranking (the rule) ─────────────────────────────

def test_the_ranking_prefers_completion_when_1_minus_ask_beats_the_bid():
    r = MS.rank_priced_actions(60, 0.45, bid=0.40, bid_size=500,
                               complement_ask=0.49, complement_ask_size=500,
                               fee_fn=fee)
    assert r["selected"] == "TAKE_COMPLEMENT"
    # 1 - 0.49 = 0.51 > bid 0.40
    assert "1 - ask = 0.5100 vs bid = 0.4000" in r["selection_reason"]


def test_the_ranking_prefers_the_sale_when_the_bid_beats_it():
    """CONTROL: the ranking is a calculation, not a bias toward pairing."""
    r = MS.rank_priced_actions(60, 0.45, bid=0.60, bid_size=500,
                               complement_ask=0.55, complement_ask_size=500,
                               fee_fn=fee)
    assert r["selected"] == "DIRECT_EXIT"


def test_above_par_completion_is_SELECTABLE_when_it_limits_a_loss():
    """THE BLANKET SUB-PAR GATE IS WITHDRAWN. Basis 0.45 and a 0.55
    complement locks a loss, and it still beats selling into a 0.10
    bid."""
    r = MS.rank_priced_actions(60, 0.45, bid=0.10, bid_size=500,
                               complement_ask=0.55, complement_ask_size=500,
                               fee_fn=fee)
    assert r["selected"] == "TAKE_COMPLEMENT"
    best = r["priced_actions"][0]
    assert best["locks_a_loss"] is True
    assert "LOCKS A LOSS and is selected anyway" in r["selection_reason"]
    assert "below-par" not in r["selection_reason"]


def test_a_priced_action_is_NOT_a_secured_execution():
    """Computing an attractive completion does not obtain it."""
    r = MS.rank_priced_actions(60, 0.45, bid=0.40, bid_size=500,
                               complement_ask=0.49, complement_ask_size=500,
                               fee_fn=fee)
    for c in r["priced_actions"]:
        assert c["execution_secured"] is False
        assert "outcome_if_filled_usd" in c
        assert "value_usd" not in c


def test_a_price_without_depth_is_refused_not_ranked():
    r = MS.rank_priced_actions(60, 0.45, bid=0.40, bid_size=0,
                               complement_ask=0.49, complement_ask_size=0,
                               fee_fn=fee)
    assert r["selected"] is None
    blockers = {x["action"]: x["blocker"] for x in r["refused"]}
    assert blockers["DIRECT_EXIT"] == "NO_EXECUTABLE_DEPTH"
    assert blockers["TAKE_COMPLEMENT"] == "NO_EXECUTABLE_DEPTH"


def test_depth_shorter_than_our_size_releases_basis_pro_rata():
    r = MS.rank_priced_actions(100, 0.45, bid=0.50, bid_size=25,
                               complement_ask=None, fee_fn=fee)
    c = r["priced_actions"][0]
    assert c["qty"] == 25.0
    assert c["depth_limited"] is True
    # 0.50*25 = 12.50 - fee 0.125 - basis 45*(25/100)=11.25 -> 1.125
    assert abs(c["outcome_if_filled_usd"] - 1.125) < 1e-9


def test_unknown_fees_refuse_rather_than_pricing_execution_as_free():
    r = MS.rank_priced_actions(60, 0.45, bid=0.40, bid_size=500,
                               complement_ask=0.49, complement_ask_size=500,
                               fee_fn=None)
    assert r["selected"] is None
    assert all(x["blocker"] == "FEE_SCHEDULE_NOT_ESTABLISHED"
               for x in r["refused"])


def test_hold_is_named_as_not_comparable_never_as_zero():
    r = MS.rank_priced_actions(60, 0.45, bid=0.40, bid_size=500,
                               complement_ask=0.49, complement_ask_size=500,
                               fee_fn=fee)
    assert r["hold"]["value_usd"] is None
    assert r["hold"]["status"] == MS.NOT_IDENTIFIED
    assert "NOT zero" in r["hold"]["why"]
    assert "HOLD is NOT comparable" in r["selection_reason"]


def test_the_rule_never_claims_to_be_EV_optimal_or_executed():
    d = MS.RULE_DECLARATION
    assert "EV-optimal" not in d.get("claims", "")
    assert "evidence that acting beats holding" in d["is_not"]
    assert "may not fill at all" in d["is_not"]
    assert d["supports_loss_limiting"].startswith("YES")
    assert "blanket" in d["withdrawn"]
    assert d["inputs"]["settlement"].startswith("NOT USED")
