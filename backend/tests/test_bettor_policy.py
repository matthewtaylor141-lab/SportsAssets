"""The shared policy: one definition, two consumers, and the invariants."""
from __future__ import annotations

import os
import sys

import pytest

from sportsassets import bettor_policy as bp

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.join(HERE, "..", "..", "research", "beta48")
if RESEARCH not in sys.path:
    sys.path.insert(0, RESEARCH)


def _book(bid=0.50, ask=0.52, tick=0.01, state="MARKET_STATE_OPEN"):
    return bp.Book(bid=bid, ask=ask, tick=tick, state=state)


# ── the policy is SHARED, not copied ─────────────────────────────────

def test_the_replay_imports_the_shared_policy_rather_than_copying_it():
    import bettor_episodes as epi
    assert epi.shared is bp
    assert epi.shared.POLICY_VERSION == bp.POLICY_VERSION


def test_replay_and_shared_agree_on_size_for_the_same_book():
    """The decision a backtest makes must be the decision the runtime
    would make. Asserted over a sweep, not a single case."""
    import bettor_episodes as epi
    pol = epi.Policy(name="X", size_rule=bp.SIZE_EDGE_SCALED,
                     min_spread_ticks=2)
    shared_pol = epi._as_shared(pol)
    for t in range(1, 10):
        row = {"bid": 0.50, "ask": 0.50 + 0.01 * t, "tick": 0.01}
        replay_size, _ = epi.decision_time_size(pol, 100.0, row)
        direct = bp.quote_size(shared_pol, _book(0.50, 0.50 + 0.01 * t),
                               100.0)["size"]
        assert replay_size == direct, "t=%d" % t


def test_the_adapter_carries_every_shared_field():
    """A knob added to one side and not the other must not vanish."""
    import bettor_episodes as epi
    import dataclasses
    pol = epi.Policy(name="X", min_spread_ticks=3, max_unmatched_mult=0.5,
                     release_matched=True, recovery="COMPLETE_PAIR")
    sp = epi._as_shared(pol)
    for f in dataclasses.fields(bp.Policy):
        if hasattr(pol, f.name):
            assert getattr(sp, f.name) == getattr(pol, f.name), f.name


# ── entry ────────────────────────────────────────────────────────────

def test_a_book_below_the_spread_floor_is_refused():
    p = bp.Policy(min_spread_ticks=2)
    d = bp.admit(p, _book(0.50, 0.51))
    assert d["decision"] == bp.D_STAND_ASIDE
    assert "below the 2-tick minimum" in d["why"]


def test_a_one_sided_book_is_never_quoted():
    d = bp.admit(bp.Policy(), bp.Book(bid=0.50, ask=None))
    assert d["decision"] == bp.D_STAND_ASIDE
    assert "directional bet" in d["why"]


def test_a_halted_market_is_refused():
    d = bp.admit(bp.Policy(), _book(state="MARKET_STATE_HALTED"))
    assert d["decision"] == bp.D_STAND_ASIDE


# ── sizing reads only decision-time inputs ───────────────────────────

def test_sizing_never_reads_an_outcome():
    """A rule that needed a realised fill rate could not be run live."""
    for r in bp.RULES.values():
        for bad in ("fill_rate", "realised", "settlement", "later_price",
                    "outcome"):
            assert bad not in [i.lower() for i in r["inputs"]]


def test_sizing_is_bounded_both_ways():
    p = bp.Policy(size_rule=bp.SIZE_EDGE_SCALED, min_spread_ticks=2,
                  size_min_mult=0.5, size_max_mult=2.0)
    assert bp.quote_size(p, _book(0.50, 0.51), 100.0)["size"] == 50.0
    assert bp.quote_size(p, _book(0.50, 0.70), 100.0)["size"] == 200.0


# ── inventory and recovery ───────────────────────────────────────────

def test_a_fill_cancels_the_other_side_when_the_policy_says_so():
    p = bp.Policy(cancel_other_on_fill=True)
    d = bp.on_fill(p, bp.Inventory(yes=50.0, no=0.0, open_no=100.0))
    assert d["decision"] == bp.D_CANCEL_OTHER
    assert "directional trader by accident" in d["why"]


def test_the_inventory_cap_binds_at_the_declared_multiple():
    p = bp.Policy(max_unmatched_mult=0.5)
    assert not bp.inventory_cap_breached(
        p, bp.Inventory(yes=40.0, clip=100.0))["breached"]
    assert bp.inventory_cap_breached(
        p, bp.Inventory(yes=50.0, clip=100.0))["breached"]


def test_maker_then_taker_rests_first_then_crosses():
    p = bp.Policy(recovery=bp.R_MAKER_THEN_TAKER, recovery_wait_s=600.0)
    early = bp.Inventory(yes=50.0, elapsed_s=100.0, unmatched_since_s=0.0)
    late = bp.Inventory(yes=50.0, elapsed_s=900.0, unmatched_since_s=0.0)
    assert bp.recovery_action(p, early, _book())["decision"] == bp.D_REST_EXIT
    assert bp.recovery_action(p, late, _book())["decision"] == bp.D_EXIT_TAKER


def test_release_is_off_by_default_and_prices_its_cost_when_on():
    inv = bp.Inventory(yes=100.0, no=100.0)
    assert bp.release_action(bp.Policy(), inv, _book())["decision"] == \
        bp.D_HOLD
    d = bp.release_action(bp.Policy(release_matched=True), inv, _book())
    assert d["decision"] == bp.D_RELEASE
    assert d["spread_cost_usd"] == pytest.approx(2.0)


# ── provenance ───────────────────────────────────────────────────────

def test_every_rule_declares_derived_or_hypothesis():
    for name, r in bp.RULES.items():
        assert r["provenance"] in (bp.DERIVED, bp.HYPOTHESIS), name
        assert r["rationale"] and r["evidence"], name
        if r["provenance"] == bp.DERIVED:
            assert r["case_study"], name


def test_every_decision_carries_its_own_provenance():
    for d in (bp.admit(bp.Policy(), _book()),
              bp.on_fill(bp.Policy(cancel_other_on_fill=True),
                         bp.Inventory(yes=50.0)),
              bp.recovery_action(bp.Policy(recovery=bp.R_COMPLETE_PAIR),
                                 bp.Inventory(yes=50.0), _book()),
              bp.release_action(bp.Policy(), bp.Inventory(yes=1, no=1),
                                _book())):
        assert d["provenance"] in (bp.DERIVED, bp.HYPOTHESIS)
        assert d["why"]


def test_the_hypotheses_are_named_and_not_dressed_as_case_study_rules():
    d = bp.describe()
    assert "sizing" in d["hypotheses"]
    assert "release" in d["hypotheses"]
    assert "entry" in d["derived"]
    assert "recovery" in d["derived"]
