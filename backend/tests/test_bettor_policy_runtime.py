"""The runtime adapter: same policy, live inputs, engine still in charge.

These tests exist to catch the two ways this adapter could be wrong in
a way that costs money:

  1. THE POLICY ROUTING PAST A REFUSAL. The engine's blocks are there
     because each was a real defect. A proposal must never be reported
     as standing when the engine refused it, and the effective action
     must always be the engine's.

  2. REPLAY AND RUNTIME DRIFTING APART. They import the same module, so
     the test asserts identity rather than similarity -- and then
     drives the SAME state through both adapters and compares the
     decision, because two callers of one function can still disagree
     by assembling its arguments differently.
"""
from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
for _p in (os.path.join(_REPO, "backend"),
           os.path.join(_REPO, "research", "beta48")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sportsassets import bettor_decision_engine as de     # noqa: E402
from sportsassets import bettor_policy as bp              # noqa: E402
from sportsassets import bettor_policy_runtime as rt      # noqa: E402


# TWO VOCABULARIES, ON PURPOSE. The venue and the market stream say
# MARKET_STATE_OPEN; `bettor_observation_adapter` normalises that to
# OPEN before a Book reaches the engine. Both appear below because the
# shared policy is called from both sides and has to recognise each.
VENUE_OPEN = "MARKET_STATE_OPEN"
ENGINE_OPEN = "OPEN"


def _book(**kw):
    d = dict(market_id="m1", yes_bid=0.40, yes_ask=0.44,
             yes_bid_size=500.0, yes_ask_size=500.0,
             age_s=1.0, venue_state=ENGINE_OPEN)
    d.update(kw)
    return de.Book(**d)


# ── the engine stays in charge ───────────────────────────────────────

def test_an_unreadable_book_refuses_the_proposal_and_governs():
    """A book with no age is not evidence about now, at any layer."""
    r = rt.evaluate(_book(age_s=None), policy=bp.Policy(), base_size=100.0)
    assert r["data_quality"] == "REJECTED"
    assert r["engine_verdict"] == rt.REFUSED
    assert r["effective_action"] == de.NO_TRADE
    assert r["effective_size_contracts"] == 0.0
    # The proposal is still RECORDED. Dropping it would hide what the
    # strategy wanted on a book it should not have been offered.
    assert r["proposal"]["action"] in (bp.D_QUOTE_BOTH, bp.D_STAND_ASIDE)


def test_quoting_is_never_reported_as_approved_because_p_fill_is_unknown():
    """MAKE_* is NOT_IDENTIFIED. NOT_IDENTIFIED is not approval."""
    r = rt.evaluate(_book(), policy=bp.Policy(), base_size=100.0)
    assert r["proposal"]["action"] == bp.D_QUOTE_BOTH
    assert r["engine_verdict"] == rt.UNSCORED
    assert r["engine_verdict"] != rt.OK
    assert "NOT_IDENTIFIED" in (r["engine_detail"] or "")
    assert r["effective_action"] == de.NO_TRADE


def test_an_order_management_action_is_unscored_rather_than_approved():
    """The engine prices positions. A cancel is not one."""
    pol = bp.Policy(cancel_other_on_fill=True)
    inv = de.Inventory(yes_contracts=100.0, no_contracts=0.0)
    r = rt.evaluate(_book(), policy=pol, inventory=inv, clip=100.0,
                    open_no=100.0, base_size=100.0)
    # The policy's recovery answer governs the proposal at UNMATCHED;
    # what matters here is that nothing without an engine candidate is
    # ever reported as standing.
    assert r["engine_verdict"] in (rt.UNSCORED, rt.REFUSED)


def test_nothing_is_executable_from_this_module():
    for kw in ({}, {"inventory": de.Inventory(yes_contracts=50.0)}):
        r = rt.evaluate(_book(), policy=bp.Policy(), base_size=100.0, **kw)
        assert r["executable"] is False
        assert r["effective_size_contracts"] == 0.0


def test_a_verified_fee_schedule_does_not_unlock_a_maker_action():
    """Even fully specified, MAKE_* has no P_FILL and so has no EV."""
    fees = de.Fees(taker_per_contract=0.015, maker_per_contract=-0.003,
                   verified=True, source="TEST_ONLY")
    r = rt.evaluate(_book(), policy=bp.Policy(), base_size=100.0,
                    fees=fees, max_contracts=100.0)
    assert r["engine_verdict"] == rt.UNSCORED


# ── lifecycle coverage ───────────────────────────────────────────────

def test_a_partial_fill_is_unmatched_with_quotes_still_working():
    """The normal case in the corpus, not an exception path."""
    inv = rt.inventory_from_engine(
        de.Inventory(yes_contracts=40.0, no_contracts=0.0),
        clip=100.0, open_yes=60.0, open_no=100.0)
    assert rt.stage(inv) == rt.S_UNMATCHED
    assert inv.unmatched == 40.0
    assert inv.open_yes == 60.0


def test_every_stage_is_reachable_and_ordered_by_what_costs_money():
    flat = rt.inventory_from_engine(de.Inventory(), clip=100.0)
    working = rt.inventory_from_engine(de.Inventory(), clip=100.0,
                                       open_yes=100.0, open_no=100.0)
    unmatched = rt.inventory_from_engine(
        de.Inventory(yes_contracts=100.0, no_contracts=30.0), clip=100.0)
    matched = rt.inventory_from_engine(
        de.Inventory(yes_contracts=100.0, no_contracts=100.0), clip=100.0)
    assert rt.stage(flat) == rt.S_FLAT
    assert rt.stage(working) == rt.S_WORKING
    # A position that is BOTH partly matched and partly unmatched is
    # UNMATCHED: the directional part is the part that costs money.
    assert rt.stage(unmatched) == rt.S_UNMATCHED
    assert rt.stage(matched) == rt.S_MATCHED


def test_the_horizon_cancels_quotes_that_never_filled():
    pol = bp.Policy(quote_horizon_s=2400.0)
    book = rt.book_from_engine(_book())
    inv = rt.inventory_from_engine(de.Inventory(), clip=100.0,
                                   open_yes=100.0, open_no=100.0,
                                   elapsed_s=2400.0)
    p = rt.propose(pol, book, inv, base_size=100.0)
    assert p["action"] == bp.D_CANCEL_OTHER
    inv2 = rt.inventory_from_engine(de.Inventory(), clip=100.0,
                                    open_yes=100.0, open_no=100.0,
                                    elapsed_s=100.0)
    assert rt.propose(pol, book, inv2,
                      base_size=100.0)["action"] == bp.D_HOLD_BOTH


def test_capital_release_is_reachable_and_is_a_pair_sell_to_the_engine():
    pol = bp.Policy(release_matched=True)
    r = rt.evaluate(_book(), policy=pol,
                    inventory=de.Inventory(yes_contracts=100.0,
                                           no_contracts=100.0),
                    clip=100.0, base_size=100.0)
    assert r["proposal"]["action"] == bp.D_RELEASE
    assert rt._ENGINE_ACTION[bp.D_RELEASE] == (de.PAIR_SELL,)
    # It is scored or refused by the engine -- never simply assumed.
    assert r["engine_verdict"] in (rt.OK, rt.UNSCORED, rt.REFUSED)


def test_holding_a_pair_is_the_default_and_release_must_be_asked_for():
    r = rt.evaluate(_book(), policy=bp.Policy(),
                    inventory=de.Inventory(yes_contracts=100.0,
                                           no_contracts=100.0),
                    clip=100.0, base_size=100.0)
    assert r["proposal"]["action"] == bp.D_HOLD


def test_the_recovery_window_is_read_from_time_not_from_a_counter():
    pol = bp.Policy(recovery=bp.R_MAKER_THEN_TAKER, recovery_wait_s=1200.0)
    book = rt.book_from_engine(_book())
    einv = de.Inventory(yes_contracts=100.0, no_contracts=0.0)
    early = rt.inventory_from_engine(einv, clip=100.0, elapsed_s=1500.0,
                                     unmatched_since_s=1400.0)
    late = rt.inventory_from_engine(einv, clip=100.0, elapsed_s=2700.0,
                                    unmatched_since_s=1400.0)
    assert rt.propose(pol, book, early,
                      base_size=100.0)["action"] == bp.D_REST_EXIT
    assert rt.propose(pol, book, late,
                      base_size=100.0)["action"] == bp.D_EXIT_TAKER


# ── replay and runtime do not drift ──────────────────────────────────

def test_the_replay_and_the_runtime_import_the_same_policy_object():
    import bettor_episodes as epi
    assert epi.shared is bp


@pytest.mark.parametrize("recovery", [bp.R_MAKER_THEN_TAKER, bp.R_TAKER_NOW,
                                      bp.R_COMPLETE_PAIR, bp.R_HOLD])
@pytest.mark.parametrize("waited", [False, True])
def test_replay_and_runtime_recover_identically_from_the_same_state(
        recovery, waited):
    """Two adapters, one rule -- and the arguments must match too.

    The replay builds its Inventory from episode bookkeeping and the
    runtime from live position keeping. Identical imports do not by
    themselves guarantee identical ARGUMENTS, which is the other way
    these could diverge, so the state is constructed on both sides and
    the decisions compared.
    """
    import bettor_episodes as epi

    pol = bp.Policy(recovery=recovery, recovery_wait_s=1200.0)
    row = {"bid": 0.40, "ask": 0.44, "state": VENUE_OPEN, "tick": 0.01}
    elapsed, since = (2700.0 if waited else 1500.0), 1400.0

    ep = epi.Episode.__new__(epi.Episode)
    ep.yes, ep.no = 100.0, 0.0
    ep.open_yes = ep.open_no = 0.0
    ep.size = 100.0
    replay_inv = ep._inv_of(elapsed, unmatched_since=since)

    runtime_inv = rt.inventory_from_engine(
        de.Inventory(yes_contracts=100.0, no_contracts=0.0),
        clip=100.0, elapsed_s=elapsed, unmatched_since_s=since)

    assert replay_inv == runtime_inv
    book = epi._book_of(row, 0.01)
    rt_book = rt.book_from_engine(_book(), tick=0.01)
    # The PRICE-BEARING fields must match exactly. Depth deliberately
    # does not: the captured corpus carries none and the live stream
    # does, which is why every rule has to work without it.
    for f in ("bid", "ask", "tick"):
        assert getattr(book, f) == getattr(rt_book, f)
    assert bp.is_open(book.state) and bp.is_open(rt_book.state)
    assert book.depth_bid is None and rt_book.depth_bid == 500.0

    a = bp.recovery_action(pol, replay_inv, book)
    b = rt.propose(pol, book, runtime_inv, base_size=100.0)
    assert a["decision"] == b["action"]
    assert a["why"] == b["why"]


def test_both_state_vocabularies_admit_and_neither_is_guessed_at():
    """THE DEFECT THIS PINS, found by running the adapter rather than
    by reading it.

    `bettor_policy.admit` recognised only `MARKET_STATE_OPEN`, the
    venue's spelling, because the replay is the only caller that ever
    exercised it. Every Book arriving from the runtime side carries the
    NORMALISED `OPEN` -- so the live engine would have stood aside on
    every single market, reporting a market-state refusal that looked
    like an ordinary quiet day rather than a wiring fault.
    """
    pol = bp.Policy()
    for state in (VENUE_OPEN, ENGINE_OPEN, "ACTIVE", "active", None):
        b = bp.Book(bid=0.40, ask=0.44, tick=0.01, state=state)
        assert bp.admit(pol, b)["decision"] == bp.D_QUOTE_BOTH, state
    for state in ("MARKET_STATE_EXPIRED", "MARKET_STATE_HALTED", "CLOSED"):
        b = bp.Book(bid=0.40, ask=0.44, tick=0.01, state=state)
        assert bp.admit(pol, b)["decision"] == bp.D_STAND_ASIDE, state


def test_the_complement_instrument_is_not_invented_from_this_book():
    """no_bid/no_ask belong to a DIFFERENT instrument. Not folded in."""
    b = rt.book_from_engine(_book(no_bid=0.50, no_ask=0.70))
    assert b.bid == 0.40 and b.ask == 0.44


def test_the_adapter_declares_what_it_covers_and_that_it_places_nothing():
    d = rt.describe()
    assert d["places_orders"] is False
    for key in ("decision_time_sizing", "partial_fills", "inventory",
                "completion", "exits", "residual_exposure",
                "capital_recycling"):
        assert d["lifecycle_covered"][key]
