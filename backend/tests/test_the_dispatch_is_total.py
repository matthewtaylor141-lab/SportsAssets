"""An action must not be selectable before its execution semantics exist.

`bettor_funded_management.select_exit` has ONE dispatch branch:

    if sel in ("DIRECT_EXIT", "REDUCE") and qty and float(qty) > 0:

Everything else fell through it to `return ok=True, refusal=None` whose
note reads "HOLD chosen by a named rule on observed inputs". So a
TAKE_COMPLEMENT selection was reported as a SUCCESSFUL servicing pass,
mislabelled as HOLD, with no order planned, no inventory reserved and
nothing saying so. TAKE_COMPLEMENT, POST_COMPLEMENT, COMPLETE_PAIR and
MERGE appear nowhere else in that module.

Two containments, tested here:

  1. the funded ranker is told what the dispatch can send, so those
     actions are ranked and shown but cannot WIN; and
  2. the dispatch is total, so one reaching it anyway is a named refusal
     rather than a success.
"""

import pytest

from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_mgmt_select as MS

HOLD = {"status": "IDENTIFIED", "probability": 0.35,
        "selection_eligible": True,
        "terminal_rule": {"disqualifies_selection": False}}


def _free(qty=None, price=None, maker=False, **kw):
    return 0.0


def _rank(venue="polymarket-us", bid=0.44, ask=0.55, executable=None):
    return MS.rank_with_hold(
        100.0, 0.60, ev_hold=dict(HOLD), bid=bid, bid_size=100.0,
        complement_ask=ask, complement_ask_size=100.0, fee_fn=_free,
        venue=venue, us_market_slug="x", held_is_long=True,
        executable_actions=executable)


def _unq(r):
    return {u["action"]: u["because"]["code"] for u in r.get("unqualified") or []}


# ── 1 · WHAT THE DISPATCH DECLARES ───────────────────────────────────

def test_the_module_declares_exactly_what_it_can_send():
    assert FM.EXECUTABLE_ACTIONS == ("DIRECT_EXIT", "REDUCE")


def test_the_unimplemented_routes_are_named_as_open_requirements():
    for a in ("TAKE_COMPLEMENT", "POST_COMPLEMENT", "COMPLETE_PAIR",
              "MERGE", "FORM_INDIRECT_HEDGE"):
        assert a in FM.UNIMPLEMENTED_ROUTES, a
        assert FM.UNIMPLEMENTED_ROUTES[a]


def test_the_declared_capability_matches_the_dispatch_branch():
    """Read the branch out of the source, so the two cannot drift."""
    import inspect
    src = inspect.getsource(FM.select_exit)
    assert 'if sel in ("DIRECT_EXIT", "REDUCE")' in src
    for a in FM.EXECUTABLE_ACTIONS:
        assert '"%s"' % a in src


# ── 2 · AN UNEXECUTABLE ACTION CANNOT WIN ────────────────────────────

def test_take_complement_is_unqualified_on_the_funded_capability():
    r = _rank(executable=FM.EXECUTABLE_ACTIONS)
    assert _unq(r)["TAKE_COMPLEMENT"] == "NO_EXECUTION_PATH_IN_THIS_CALLER"
    assert r["selected"] != "TAKE_COMPLEMENT"


def test_it_stays_visible_and_priced_rather_than_being_dropped():
    r = _rank(executable=FM.EXECUTABLE_ACTIONS)
    tc = next(c for c in r["candidates"] if c["action"] == "TAKE_COMPLEMENT")
    assert tc["value_usd"] is not None, "it must still be priced"
    assert tc["selection_eligible"] is False
    assert tc["selection_ineligible_because"]["detail"]


def test_the_supported_exit_still_wins_so_inventory_is_not_stranded():
    r = _rank(executable=FM.EXECUTABLE_ACTIONS)
    assert r["selected"] == "DIRECT_EXIT"
    assert r["selected_qty"] == pytest.approx(100.0)


def test_a_caller_that_declares_nothing_is_unchanged():
    """The shadow challenger ranks the whole table on purpose."""
    r = _rank(executable=None, ask=0.56)
    assert "NO_EXECUTION_PATH_IN_THIS_CALLER" not in _unq(r).values()


# ── 3 · THE UNESTABLISHED ADVANTAGE CANNOT WIN EITHER ────────────────

def test_an_advantage_that_is_one_book_quoted_twice_is_unqualified():
    """The code changed with its basis: absence of evidence -> evidence.

    It read ADVANTAGE_RESTS_ON_UNESTABLISHED_LIQUIDITY, which was accurate
    while neither reading had authority. The venue documents one
    instrument per market, so the claim is now refuted rather than
    unestablished -- a stronger and different statement, and it gets its
    own code.
    """
    r = _rank(executable=None, venue="polymarket-us", ask=0.50)
    assert _unq(r)["TAKE_COMPLEMENT"] == \
        "ADVANTAGE_IS_THE_SAME_BOOK_QUOTED_TWICE"
    # THE DIRECT EXIT STILL WINS. Containment must not strand inventory.
    assert r["selected"] == "DIRECT_EXIT"


def test_the_old_code_is_still_discoverable_for_stored_rows():
    """Rows written before this change carry the old code."""
    r = _rank(executable=None, venue="polymarket-us", ask=0.50)
    why = next(u for u in r["unqualified"]
               if u["action"] == "TAKE_COMPLEMENT")["because"]
    assert why["supersedes_code"] == \
        "ADVANTAGE_RESTS_ON_UNESTABLISHED_LIQUIDITY"


def test_the_ineligibility_names_what_established_it():
    r = _rank(executable=None, venue="polymarket-us", ask=0.50)
    why = next(u for u in r["unqualified"]
               if u["action"] == "TAKE_COMPLEMENT")["because"]
    # NOT "missing evidence" any more -- there is no evidence missing.
    assert "missing_evidence" not in why
    assert "one instrument per market" in why["established_by"]
    assert "count one piece of depth twice" in why["so_the_difference_is"]
    assert "TWO_TOKEN" in why["where_two_routes_are_real"]
    assert why["compare_against"] == "DIRECT_EXIT"


def test_a_consistent_netting_pair_is_eligible_again():
    r = _rank(executable=None, venue="polymarket-us", ask=0.56)
    assert "TAKE_COMPLEMENT" not in _unq(r)


def test_the_two_token_model_is_not_constrained_by_the_identity():
    r = _rank(executable=None, venue="polymarket-clob", ask=0.50)
    assert "TAKE_COMPLEMENT" not in _unq(r)
    assert r["selected"] == "TAKE_COMPLEMENT"


def test_nothing_is_selected_when_every_priced_action_is_ineligible():
    """A refusal that names both lists, not an empty selection."""
    r = MS.rank_with_hold(
        100.0, 0.60, ev_hold=dict(HOLD), bid=None, bid_size=None,
        complement_ask=0.50, complement_ask_size=100.0, fee_fn=_free,
        venue="polymarket-us", us_market_slug="x", held_is_long=True,
        executable_actions=("REDUCE",))
    assert r["selected"] in (None, "HOLD")
    if r["selected"] is None:
        assert "NO ACTION IS BOTH PRICED AND ELIGIBLE" in \
            r["selection_reason"]


# ── 4 · THE DISPATCH IS TOTAL ────────────────────────────────────────

def test_a_selection_with_no_dispatch_is_a_refusal_not_a_success():
    """Read the total-dispatch guard out of the source.

    select_exit needs a live connection and a bound account, so the guard
    itself is asserted structurally: an unexecutable selection returns
    ok=False with the named refusal, and the old success-with-a-HOLD-note
    path is unreachable for it.
    """
    import inspect
    src = inspect.getsource(FM.select_exit)
    assert 'sel not in EXECUTABLE_ACTIONS' in src
    assert 'R_ACTION_NOT_EXECUTABLE' in src
    assert 'ok=False' in src
    i = src.index("sel not in EXECUTABLE_ACTIONS")
    j = src.index('note=("this IS a decision. HOLD chosen by a named rule')
    assert i < j, ("the total-dispatch refusal must come BEFORE the "
                   "success return, or the fall-through still wins")


def test_the_refusal_code_says_where_the_gap_is():
    assert FM.R_ACTION_NOT_EXECUTABLE == \
        "SELECTED_ACTION_HAS_NO_DISPATCH_IN_THIS_MODULE"


def test_the_refusal_states_that_nothing_was_sent():
    import inspect
    src = inspect.getsource(FM.select_exit)
    assert "No order was planned, no inventory was" in src
    assert "inventory_untouched=True" in src


def test_hold_is_not_caught_by_the_total_dispatch_guard():
    """HOLD legitimately sends nothing and must stay a success."""
    import inspect
    src = inspect.getsource(FM.select_exit)
    assert 'sel != "HOLD"' in src


# ── 5 · WHAT THIS DOES NOT ESTABLISH ─────────────────────────────────

def test_the_complement_route_is_still_an_open_requirement():
    """Containment is not implementation."""
    note = FM.UNIMPLEMENTED_ROUTES["TAKE_COMPLEMENT"]
    assert "planner" in note
    assert "reservation" in note
    assert "NOT_ESTABLISHED" in note
