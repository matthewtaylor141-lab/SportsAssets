"""THE COHORT-SEEDED MANAGEMENT TEST, and the ways it could cheat.

The three that matter most, each with a test that would fail if the
guard were removed:

  LOOK-AHEAD    handing the decision the settled payout. My first
                version did exactly this.
  SEED SELECTION  choosing seeds after seeing which did well.
  ASSUMPTION-AS-RESULT  reporting one queue_share as if it were measured.
"""
from __future__ import annotations

import inspect
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

from sportsassets import bettor_mgmt_test as MT                # noqa: E402
from sportsassets import bettor_mgmt_value as MV               # noqa: E402


def fee(qty, price):
    return round(0.01 * qty * price, 6)


def make(payout0):
    seed = MT.Seed(condition_id="0xc1", outcome_index=0, qty=100.0,
                   entry_price=0.45, entry_at=0.0, account="w1",
                   payouts={0: payout0, 1: 1.0 - payout0},
                   resolved_at=1000.0)
    prints = [
        MT.Print(at=10.0, outcome_index=1, side="BUY", price=0.48,
                 size=400),
        MT.Print(at=20.0, outcome_index=0, side="SELL", price=0.60,
                 size=300),
        MT.Print(at=30.0, outcome_index=0, side="BUY", price=0.62,
                 size=200, by_seed_account=True),
    ]
    return seed, prints


# ── the look-ahead guard ─────────────────────────────────────────────

def test_the_decision_time_market_carries_NO_settled_payout():
    """THE BUG THIS PINS, which I shipped and then found. Supplying the
    observed payout to `_mk_market` lets a policy compare HOLD against
    EXIT using the answer. Every such comparison is contaminated."""
    seed, prints = make(1.0)
    mk = MT._mk_market(seed, prints[1], None, 0.0)
    assert mk.payout is None
    assert mk.payout_source == MV.SETTLE_UNKNOWN
    # and therefore HOLD is refused, exactly as it would be live
    pos = MV.Position("0xc1", 0, 100.0, 45.0)
    assert MV.value_hold(pos, mk).blocker == "SETTLEMENT_NOT_ESTIMATED"


def test_the_payout_is_used_to_SCORE_and_only_to_score():
    """CONTROL for the test above: the payout must still reach
    settlement, or the runner would be measuring nothing."""
    src = inspect.getsource(MT)
    # `seed.payouts` may be read in exactly one decision-free place.
    users = [ln.strip() for ln in src.splitlines()
             if "seed.payouts" in ln and not ln.strip().startswith("#")]
    assert users == ["residual = book.settle(seed.payouts)"], users


# ── the mechanism ────────────────────────────────────────────────────

def test_completion_locks_the_same_result_whatever_settlement_does():
    """THE FERRARI MECHANISM, demonstrated. A completed pair pays 1.00
    per contract however the event resolves, so the policy's net must
    not depend on the payout at all."""
    won = MT.run_seed_all_policies(*make(1.0), fee_fn=fee)
    lost = MT.run_seed_all_policies(*make(0.0), fee_fn=fee)
    a = won["net_by_policy_by_queue_share"]["COMPLETE_ON_LOCKED_GAIN_V1"]
    b = lost["net_by_policy_by_queue_share"]["COMPLETE_ON_LOCKED_GAIN_V1"]
    # At queue shares where the pair COMPLETES IN FULL the two agree.
    assert abs(a[0.25] - b[0.25]) < 1e-9
    assert abs(a[0.50] - b[0.50]) < 1e-9
    assert abs(a[0.25] - 6.52) < 1e-6


def test_a_partial_completion_leaves_real_residual_risk():
    """AND THE SWEEP IS WHAT REVEALS IT. At queue_share 0.10 only 40 of
    100 contracts pair, so 60 stay directional and the result swings
    with settlement after all. A single assumed queue share would have
    hidden this entirely."""
    won = MT.run_seed_all_policies(*make(1.0), fee_fn=fee)
    lost = MT.run_seed_all_policies(*make(0.0), fee_fn=fee)
    a = won["net_by_policy_by_queue_share"]["COMPLETE_ON_LOCKED_GAIN_V1"]
    b = lost["net_by_policy_by_queue_share"]["COMPLETE_ON_LOCKED_GAIN_V1"]
    assert abs(a[0.10] - b[0.10]) > 10.0, (
        "a partial completion that showed no settlement sensitivity "
        "would mean the residual was being valued as if it were paired")


def test_completion_is_NOT_free_money_and_loses_when_the_leg_wins():
    """THE CLAIM THIS BLOCKS. Locking a certain gain forgoes an
    uncertain larger one. A summary calling this a profitable strategy
    would be wrong in the direction that flatters us."""
    won = MT.run_seed_all_policies(*make(1.0), fee_fn=fee)
    n = won["net_by_policy_by_queue_share"]
    assert n["COMPLETE_ON_LOCKED_GAIN_V1"][0.25] < \
           n["HOLD_TO_SETTLEMENT"][0.25]


def test_the_value_ranked_policy_degenerates_to_hold_and_that_is_reported():
    """With no settlement view, nothing can be RANKED against holding,
    so a purely EV-ranked manager never acts. That is a result about the
    missing model, not a broken policy, and it is named in the register
    rather than quietly producing a flat line."""
    won = MT.run_seed_all_policies(*make(1.0), fee_fn=fee)
    n = won["net_by_policy_by_queue_share"]
    assert n["VALUE_RANKED_V1"] == n["HOLD_TO_SETTLEMENT"]
    desc = dict((p[0], p[1]) for p in MT.POLICIES)["VALUE_RANKED_V1"]
    assert "never acts" in desc


def test_every_policy_starts_from_the_IDENTICAL_assigned_inventory():
    seed, prints = make(1.0)
    out = MT.run_seed_all_policies(seed, prints, fee_fn=fee)
    seeds = {(r["seed"]["qty"], r["seed"]["basis_usd"],
              r["seed"]["outcome_index"]) for r in out["runs"]}
    assert len(seeds) == 1, seeds


def test_no_residual_is_left_unvalued_when_the_payout_is_observed():
    seed, prints = make(1.0)
    out = MT.run_seed_all_policies(seed, prints, fee_fn=fee)
    for r in out["runs"]:
        assert r["unvalued_residual"] == [], r["policy"]


# ── the honesty surface ──────────────────────────────────────────────

def test_the_seed_rule_references_no_outcome():
    """A seed set chosen after seeing which positions did well is not a
    test of a policy but a description of a subset."""
    blob = " ".join(MT.SEED_RULE["include"]).lower()
    for word in ("profit", "won", "winning", "in the money", "return"):
        assert word not in blob, word


def test_the_run_never_claims_a_fill_rate_and_labels_our_fills():
    seed, prints = make(1.0)
    r = MT.run_one(seed, prints,
                   policy=MT.POLICIES[3], queue_share=0.25, fee_fn=fee)
    assert r["p_fill"] == "NOT_IDENTIFIED"
    assert r["execution_basis"] == MT.EXEC_SCENARIO
    assert "SCENARIO" in r["labels"]["our_fills"]
    assert "not a bid" in r["labels"]["exit_price"]
    for a in r["actions"]:
        if a.get("qty"):
            assert a["fill_basis"] == MT.EXEC_SCENARIO


def test_the_result_states_what_it_does_not_establish():
    out = MT.run_seed_all_policies(*make(1.0), fee_fn=fee)
    blob = " ".join(out["does_not_establish"]).lower()
    assert "p_fill" in blob and "profitability" in blob


def test_a_print_that_does_not_reach_our_price_fills_nothing():
    """CONTROL on the execution scenario. If every print filled us, the
    queue-share sweep would be meaningless and so would the model."""
    pr = MT.Print(at=1.0, outcome_index=1, side="BUY", price=0.90,
                  size=1000)
    # we want to BUY at 0.48; a print at 0.90 is far through our price
    assert MT._fill_qty(pr, 100.0, 0.48, "BUY", 0.25) == 0.0
    # and one at or below it does fill, capped by the queue share
    ok = MT.Print(at=1.0, outcome_index=1, side="BUY", price=0.40,
                  size=1000)
    assert MT._fill_qty(ok, 100.0, 0.48, "BUY", 0.25) == 100.0
    assert MT._fill_qty(ok, 500.0, 0.48, "BUY", 0.25) == 250.0
