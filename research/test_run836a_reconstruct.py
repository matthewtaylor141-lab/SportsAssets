#!/usr/bin/env python3
"""Synthetic unit tests for the RUN 83.6A replay semantics.

These tests contact nothing and read no capture. Every fixture here is
hand-written, so a test that passes says the ENGINE behaves as specified --
it says nothing at all about what the venue does. That separation is the
whole point: the engine is checked against its own definition here, and the
venue is checked against the engine over in the frozen evidence.

The two engines must be distinguishable. A suite that only exercised inputs
where absolute and delta agree would let a broken engine score 96% on real
data and look like a protocol finding. So several tests below are written
specifically to make the two disagree.

Run:  python3 -m pytest research/test_run836a_reconstruct.py -q
      python3 research/test_run836a_reconstruct.py        (no pytest needed)
"""
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "run836a", Path(__file__).with_name("run836a_clob_reconstruct.py"))
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)


def snap(bids=(), asks=()):
    return {"bids": [{"price": p, "size": s} for p, s in bids],
            "asks": [{"price": p, "size": s} for p, s in asks]}


def ch(side, price, size, asset_id="T"):
    return {"side": side, "price": price, "size": size, "asset_id": asset_id}


# ------------------------------------------------------------ price keys
def test_price_normalisation_collapses_trailing_zeros():
    assert R.norm("0.6") == R.norm("0.60") == R.norm("0.600")


def test_two_spellings_of_one_price_are_one_level():
    b = R.Book.from_snapshot(snap(bids=[("0.60", "10")]))
    R.apply_absolute(b, ch("BUY", "0.6", "25"))
    assert b.bids == {R.norm("0.6"): Decimal(25)}


def test_snapshot_drops_zero_size_levels():
    b = R.Book.from_snapshot(snap(bids=[("0.4", "0"), ("0.5", "7")]))
    assert list(b.bids) == [R.norm("0.5")]


# -------------------------------------------------------- ABSOLUTE engine
def test_absolute_replaces_the_level():
    b = R.Book.from_snapshot(snap(bids=[("0.5", "100")]))
    R.apply_absolute(b, ch("BUY", "0.5", "40"))
    assert b.bids[R.norm("0.5")] == Decimal(40)


def test_absolute_zero_removes_the_level():
    b = R.Book.from_snapshot(snap(bids=[("0.5", "100")]))
    R.apply_absolute(b, ch("BUY", "0.5", "0"))
    assert R.norm("0.5") not in b.bids


def test_absolute_creates_a_level_that_did_not_exist():
    b = R.Book.from_snapshot(snap())
    R.apply_absolute(b, ch("SELL", "0.7", "12"))
    assert b.asks == {R.norm("0.7"): Decimal(12)}


def test_absolute_zero_on_an_absent_level_is_a_no_op():
    b = R.Book.from_snapshot(snap(bids=[("0.5", "9")]))
    R.apply_absolute(b, ch("BUY", "0.9", "0"))
    assert b.bids == {R.norm("0.5"): Decimal(9)}


def test_absolute_is_idempotent():
    b = R.Book.from_snapshot(snap(bids=[("0.5", "100")]))
    for _ in range(5):
        R.apply_absolute(b, ch("BUY", "0.5", "40"))
    assert b.bids[R.norm("0.5")] == Decimal(40)


# ----------------------------------------------------------- DELTA engine
def test_delta_accumulates():
    b = R.Book.from_snapshot(snap(bids=[("0.5", "100")]))
    R.apply_delta(b, ch("BUY", "0.5", "40"))
    assert b.bids[R.norm("0.5")] == Decimal(140)


def test_delta_is_not_idempotent():
    b = R.Book.from_snapshot(snap(bids=[("0.5", "100")]))
    for _ in range(5):
        R.apply_delta(b, ch("BUY", "0.5", "40"))
    assert b.bids[R.norm("0.5")] == Decimal(300)


def test_delta_zero_leaves_the_level_alone():
    """The one place the two engines part company on a zero.

    Under DELTA a zero adds nothing, so the level survives; under ABSOLUTE a
    zero IS the new size and the level goes. The frozen capture carries 970
    zero-size updates, so this difference is load-bearing, not academic.
    """
    b = R.Book.from_snapshot(snap(bids=[("0.5", "100")]))
    R.apply_delta(b, ch("BUY", "0.5", "0"))
    assert b.bids[R.norm("0.5")] == Decimal(100)


def test_delta_removes_a_level_driven_to_zero_or_below():
    b = R.Book.from_snapshot(snap(bids=[("0.5", "100")]))
    R.apply_delta(b, ch("BUY", "0.5", "-100"))
    assert R.norm("0.5") not in b.bids


def test_delta_never_stores_a_negative_size():
    b = R.Book.from_snapshot(snap(bids=[("0.5", "10")]))
    R.apply_delta(b, ch("BUY", "0.5", "-40"))
    assert R.norm("0.5") not in b.bids


# --------------------------------------------- the engines must disagree
def test_the_two_engines_diverge_on_a_repeated_update():
    seq = [ch("BUY", "0.5", "40"), ch("BUY", "0.5", "40")]
    a = R.Book.from_snapshot(snap(bids=[("0.5", "100")]))
    d = R.Book.from_snapshot(snap(bids=[("0.5", "100")]))
    for c in seq:
        R.apply_absolute(a, c)
        R.apply_delta(d, c)
    assert a.bids[R.norm("0.5")] == Decimal(40)
    assert d.bids[R.norm("0.5")] == Decimal(180)
    assert a.bids != d.bids


def test_a_synthetic_absolute_stream_scores_absolute_perfect_and_delta_wrong():
    """End to end on a hand-built ABSOLUTE stream, using the real compare()."""
    start = R.Book.from_snapshot(snap(bids=[("0.5", "100")], asks=[("0.6", "80")]))
    updates = [ch("BUY", "0.5", "60"), ch("SELL", "0.6", "20"), ch("BUY", "0.4", "10")]
    truth = R.Book.from_snapshot(
        snap(bids=[("0.4", "10"), ("0.5", "60")], asks=[("0.6", "20")]))
    a, d = start.copy(), start.copy()
    for c in updates:
        R.apply_absolute(a, c)
        R.apply_delta(d, c)
    assert R.compare(a, truth)["full_book_equal"] is True
    assert R.compare(d, truth)["full_book_equal"] is False


def test_a_synthetic_delta_stream_scores_delta_perfect_and_absolute_wrong():
    """The mirror image. Without this, a suite could pass with an engine that
    simply always favours ABSOLUTE."""
    start = R.Book.from_snapshot(snap(bids=[("0.5", "100")]))
    updates = [ch("BUY", "0.5", "10"), ch("BUY", "0.5", "10")]
    truth = R.Book.from_snapshot(snap(bids=[("0.5", "120")]))
    a, d = start.copy(), start.copy()
    for c in updates:
        R.apply_absolute(a, c)
        R.apply_delta(d, c)
    assert R.compare(d, truth)["full_book_equal"] is True
    assert R.compare(a, truth)["full_book_equal"] is False


# ------------------------------------------------------------- scoring
def test_compare_counts_differing_levels_and_size_error():
    r = R.Book.from_snapshot(snap(bids=[("0.5", "100")], asks=[("0.6", "10")]))
    t = R.Book.from_snapshot(snap(bids=[("0.5", "90")], asks=[("0.6", "10")]))
    res = R.compare(r, t)
    assert res["full_book_equal"] is False
    assert res["bid_diff_levels"] == 1
    assert res["ask_diff_levels"] == 0
    assert res["absolute_size_error"] == Decimal(10)


def test_compare_counts_a_level_present_on_only_one_side():
    r = R.Book.from_snapshot(snap(bids=[("0.5", "100"), ("0.4", "5")]))
    t = R.Book.from_snapshot(snap(bids=[("0.5", "100")]))
    res = R.compare(r, t)
    assert res["bid_diff_levels"] == 1
    assert res["absolute_size_error"] == Decimal(5)


def test_top_of_book_can_agree_while_depth_does_not():
    """Exactly the shape of both real ABSOLUTE failures: best bid and best ask
    right, a deeper level wrong. The score must not call that a match."""
    r = R.Book.from_snapshot(snap(bids=[("0.5", "100"), ("0.1", "999")],
                                  asks=[("0.6", "10")]))
    t = R.Book.from_snapshot(snap(bids=[("0.5", "100"), ("0.1", "111")],
                                  asks=[("0.6", "10")]))
    res = R.compare(r, t)
    assert res["best_bid_equal"] is True
    assert res["best_ask_equal"] is True
    assert res["full_book_equal"] is False


def test_best_bid_and_ask_on_an_empty_side_are_none():
    b = R.Book()
    assert b.best_bid() is None and b.best_ask() is None
    assert R.compare(b, R.Book())["full_book_equal"] is True


def test_best_prices_are_numeric_not_lexicographic():
    b = R.Book.from_snapshot(snap(bids=[("0.09", "1"), ("0.1", "1")],
                                  asks=[("0.9", "1"), ("0.11", "1")]))
    assert Decimal(b.best_bid()) == Decimal("0.1")
    assert Decimal(b.best_ask()) == Decimal("0.11")


def test_copy_is_independent():
    a = R.Book.from_snapshot(snap(bids=[("0.5", "1")]))
    b = a.copy()
    R.apply_absolute(b, ch("BUY", "0.5", "9"))
    assert a.bids[R.norm("0.5")] == Decimal(1)


# ------------------------------------------------- legacy hash serialisation
def test_legacy_hash_is_stable_and_order_sensitive():
    p1 = {"market": "m", "asset_id": "a", "timestamp": "1", "hash": "",
          "bids": [], "asks": []}
    p2 = {"asset_id": "a", "market": "m", "timestamp": "1", "hash": "",
          "bids": [], "asks": []}
    assert R.legacy_hash(p1)[0] == R.legacy_hash(p1)[0]
    assert R.legacy_hash(p1)[0] != R.legacy_hash(p2)[0]


def test_legacy_hash_serialises_compactly():
    _, ser = R.legacy_hash({"a": 1, "b": [1, 2]})
    assert ser == '{"a":1,"b":[1,2]}'


def test_variants_are_the_four_named_in_advance():
    full = {"market": "m", "asset_id": "a", "timestamp": "1", "hash": "h",
            "bids": [{"price": "0.5", "size": "1"}], "asks": [],
            "min_order_size": "5", "tick_size": "0.01", "neg_risk": False,
            "last_trade_price": "0.5"}
    assert set(R.legacy_variants(full, None)) == {
        "V1_full_transmitted_order", "V2_full_reversed_levels",
        "V3_omit_absent_keys", "V4_absent_as_empty_false"}


def test_a_source_missing_min_order_size_offers_only_the_two_testable_variants():
    """A websocket `book` frame carries neither min_order_size nor neg_risk, so
    V1 and V2 cannot be built from it at all. They must be absent rather than
    fabricated from a default, or a MISMATCH would be reported as if the venue
    had failed the test."""
    ws = {"market": "m", "asset_id": "a", "timestamp": "1", "hash": "h",
          "bids": [], "asks": [], "tick_size": "0.01", "last_trade_price": "0.5"}
    assert set(R.legacy_variants(ws, None)) == {
        "V3_omit_absent_keys", "V4_absent_as_empty_false"}


# ------------------------------------------------------------- event shapes
def test_events_of_reads_both_a_single_object_and_a_list():
    one = list(R.events_of({"parsed_json": {"event_type": "book"}}))
    many = list(R.events_of({"parsed_json": [{"event_type": "book"},
                                             {"event_type": "price_change"}]}))
    assert len(one) == 1 and len(many) == 2


def test_events_of_yields_nothing_for_an_unparsed_frame():
    assert list(R.events_of({"parsed_json": None})) == []


def test_etype_names_a_missing_key_rather_than_guessing():
    assert R.etype({}) == "<no event_type key>"


# ---------------------------------------------------------------- runner
if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(vars().items())
           if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in fns:
        try:
            fn()
        except Exception as exc:                      # noqa: BLE001
            failed.append((name, exc))
    for name, exc in failed:
        print("FAIL %s: %r" % (name, exc))
    print("%d passed, %d failed" % (len(fns) - len(failed), len(failed)))
    sys.exit(1 if failed else 0)
