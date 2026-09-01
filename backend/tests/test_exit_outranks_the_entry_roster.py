"""An entry gate must never be able to strand a position we hold.

At this venue a whale exits by BUYING the complementary leg, so every
exit arrives labelled BUY. That makes gate ORDER inside maybe_execute a
money property, not a style question, and the 95% gate says so in words:

    "PLACEMENT IS LOAD-BEARING. classify_exit and mirror_exit are ABOVE
     this line, so a refused whale can still be SOLD -- at this venue an
     exit arrives labelled BUY, and a guard placed any higher would
     silently kill exit detection for every whale it refuses."

Two guards sat ~100 lines higher than the line that rule defends:

    if payload["side"] != "BUY" or username not in cfg.source_whales()
    if username in COPY_CUT_WHALES

So a whale dropped from the entry roster -- or cut -- had his exits
misread as entries and every position we already held from him ran to
resolution unmanaged. exitable_whales() is deliberately wider than the
verified set for exactly this reason and mirror_exit honours it, but the
detector that feeds mirror_exit never reached it on the lane carrying
1,572 of the 1,595 attempts.

The whole suite passed throughout, because nothing pinned the ORDER.
These tests pin it. They read maybe_execute's own source and assert the
relative position of each guard, which is the property that matters and
the one a behavioural stub cannot easily reach through 20 other gates.
"""
import inspect
import re

import pytest

from sportsassets import live_executor as le


def _src() -> str:
    return inspect.getsource(le.maybe_execute)


def _pos(needle: str) -> int:
    src = _src()
    assert needle in src, f"not found in maybe_execute: {needle!r}"
    return src.index(needle)


# ------------------------------------------------------ the ordering

def test_exit_classification_precedes_the_source_roster_gate():
    """THE BUG. cfg.source_whales() is an ENTRY roster; running it first
    means a de-rostered whale's exits are never even looked for."""
    assert _pos("_exit = await classify_exit") < _pos("cfg.source_whales()"), (
        "the entry roster gate runs before exit classification — every "
        "position held from a de-rostered whale is stranded")


def test_exit_classification_precedes_the_cut_gate():
    """0x2c33 is cut AND asserted exitable (test_cut_whale_exit). Both
    can only be true if the cut gate is below classification."""
    assert _pos("_exit = await classify_exit") < _pos("COPY_CUT_WHALES"), (
        "the cut gate runs before exit classification — a cut whale's "
        "positions can never be sold, contradicting exitable_whales()")


def test_mirror_exit_precedes_both_entry_gates():
    """Classifying is not enough; the SALE has to happen before the
    guards too, or the exit is detected and then discarded."""
    m = _pos("await mirror_exit(")
    assert m < _pos("cfg.source_whales()")
    assert m < _pos("COPY_CUT_WHALES")


def test_the_entry_gates_still_exist():
    """Fixing the order must not delete the gates. Off-roster and cut
    whales must still be refused for ENTRIES."""
    src = _src()
    assert 'return _copy_stop("not_buy_or_off_roster", username)' in src
    assert 'return _copy_stop("whale_cut", username)' in src


def test_the_global_kill_switches_stay_above_everything():
    """Exits outrank the entry roster, but nothing outranks the master
    kill or the emergency halt — those must still stop every path."""
    c = _pos("_exit = await classify_exit")
    assert _pos('_copy_stop("mode_off")') < c
    assert _pos('_copy_stop("probe_disabled")') < c
    assert _pos('_copy_stop("halted")') < c


# ------------------------------------------------------- the scoping

def test_classification_is_scoped_to_the_whales_we_could_sell_for():
    """Running classify_exit above the roster is only free if it is
    bounded by the same set mirror_exit honours. Unbounded, every BUY
    from every tracked whale would pay for the lookup."""
    src = _src()
    assert "username in exitable_whales()" in src, (
        "classification must be scoped to exitable_whales() — the exact "
        "set mirror_exit acts on")
    assert _pos("username in exitable_whales()") < _pos(
        "_exit = await classify_exit")


def test_only_a_BUY_is_ever_classified():
    """An exit is a complement BUY. A SELL can never be one, so it must
    not cost a lookup."""
    src = _src()
    guard = src[_pos('_exit = None'):_pos("_exit = await classify_exit")]
    assert 'payload.get("side") == "BUY"' in guard


def test_exitable_whales_is_wider_than_the_verified_roster():
    """The property the ordering exists to serve. If this set ever
    narrows to the entry roster, the reordering stops protecting
    anything."""
    src = inspect.getsource(le.exitable_whales)
    assert "COPY_CUT_WHALES" in src
    assert "PER_FILL_BY_WHALE" in src


@pytest.mark.parametrize("whale", ["rn1", "homerunhazard"])
def test_both_live_whales_are_unconditionally_exitable(whale):
    """Neither the env nor the DB roster override can remove a whale
    that carries a configured clip, so de-rostering either of the two
    the owner just put live cannot strand their open positions."""
    assert whale in {w.lower() for w in le.PER_FILL_BY_WHALE}
    assert whale in le.exitable_whales()


def test_a_cut_whale_is_still_exitable():
    """Restates test_cut_whale_exit's property here, because it is the
    one the gate order was silently violating end to end."""
    for w in le.COPY_CUT_WHALES:
        assert w in le.exitable_whales()


# --------------------------------------------------- no lost coverage

def test_bad_price_still_refuses_before_any_order_work():
    assert _pos('_copy_stop("bad_price_or_notional"') < _pos(
        "_exit = await classify_exit")


def test_every_pre_insert_return_is_still_counted():
    """The census property from test_copy_census_sees_the_whole_funnel,
    re-checked here because this change moved three returns."""
    src = _src()
    region = src[:src.index("INSERT INTO live_orders")]
    bare = [ln for ln in region.split("\n") if re.match(r"^\s+return\s*$", ln)]
    assert not bare, f"{len(bare)} uncounted pre-INSERT returns"
