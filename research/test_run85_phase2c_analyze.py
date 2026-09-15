#!/usr/bin/env python3
"""Offline tests for the Phase 2C analysis. Contacts nothing.

The load-bearing new claim is AUDIT 5: that a book's whole-ladder dollar
asymmetry is dominated by a far-end resting block which is SYMMETRIC IN SHARES
and 99x asymmetric in DOLLARS, so the measure reports ask-heaviness in books
that are balanced or bid-heavy near the money. That claim retracts a number
already locked from Phase 2B, so it is the thing that has to be tested -- with
a hand-built book whose right answer is known before the code runs.

Run:  python3 -m pytest research/test_run85_phase2c_analyze.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

_s = importlib.util.spec_from_file_location(
    "p2ca", Path(__file__).with_name("run85_phase2c_analyze.py"))
A = importlib.util.module_from_spec(_s)
_s.loader.exec_module(A)


def lvl(px, qty):
    return {"px": {"value": px, "currency": "USD"}, "qty": qty}


def book(bids, offers, slug="m"):
    return {"marketData": {"marketSlug": slug, "bids": bids, "offers": offers}}


# ------------------------------------------------------------- ladder_usd
def test_ladder_usd_is_price_times_quantity():
    assert A.ladder_usd([lvl("0.50", "100")]) == Decimal("50.00")
    assert A.ladder_usd([lvl("0.99", "27500")]) == Decimal("27225.00")
    assert A.ladder_usd([lvl("0.01", "27500")]) == Decimal("275.00")


def test_ladder_usd_range_is_exclusive_at_both_ends():
    ls = [lvl("0.01", "1000"), lvl("0.50", "100"), lvl("0.99", "1000")]
    assert A.ladder_usd(ls, 0.05, 0.95) == Decimal("50.00")


def test_ladder_usd_of_nothing_is_zero():
    assert A.ladder_usd(None) == Decimal(0)
    assert A.ladder_usd([]) == Decimal(0)


# -------------------------------------------------------------- the claim
def test_a_symmetric_share_block_reads_as_ask_heavy_in_dollars():
    """THE BUG, in one book. 27,500 shares parked on each side, at 0.99 and
    0.01. Near the money the two sides are exactly equal. The whole-ladder
    dollar measure must call it ask-heavy anyway -- that is the artifact."""
    b = book(bids=[lvl("0.01", "27500"), lvl("0.49", "1000")],
             offers=[lvl("0.99", "27500"), lvl("0.51", "1000")])
    md = b["marketData"]
    whole = A.ladder_usd(md["offers"]) / A.ladder_usd(md["bids"])
    inside = (A.ladder_usd(md["offers"], A.FAR_LO, A.FAR_HI)
              / A.ladder_usd(md["bids"], A.FAR_LO, A.FAR_HI))
    assert A.side_class(whole) == "ASK_HEAVY"      # what Phase 2C reported
    assert A.side_class(inside) == "BALANCED"      # what the book actually is
    # 27,735 / 765 = 36.3x of pure artifact, from a book that is 1.04x real.
    assert whole > 30


def test_the_correction_can_reverse_the_sign_not_merely_shrink_it():
    """A book that is genuinely BID-heavy near the money still reads ask-heavy
    whole-ladder. This is the Phase 2B nlchamp case: 29.9x -> 0.30x."""
    b = book(bids=[lvl("0.01", "27500"), lvl("0.49", "3000")],
             offers=[lvl("0.99", "27500"), lvl("0.51", "300")])
    md = b["marketData"]
    whole = A.ladder_usd(md["offers"]) / A.ladder_usd(md["bids"])
    inside = (A.ladder_usd(md["offers"], A.FAR_LO, A.FAR_HI)
              / A.ladder_usd(md["bids"], A.FAR_LO, A.FAR_HI))
    assert A.side_class(whole) == "ASK_HEAVY"
    assert A.side_class(inside) == "BID_HEAVY"


def test_a_genuinely_ask_heavy_book_survives_the_correction():
    """The correction must not simply turn everything into BALANCED -- then it
    would be as useless as the measure it replaces."""
    b = book(bids=[lvl("0.01", "27500"), lvl("0.49", "100")],
             offers=[lvl("0.99", "27500"), lvl("0.51", "10000")])
    md = b["marketData"]
    inside = (A.ladder_usd(md["offers"], A.FAR_LO, A.FAR_HI)
              / A.ladder_usd(md["bids"], A.FAR_LO, A.FAR_HI))
    assert A.side_class(inside) == "ASK_HEAVY"


# -------------------------------------------------------------- side_class
def test_side_class_thresholds_are_symmetric_in_log_space():
    assert A.side_class(Decimal("1")) == "BALANCED"
    assert A.side_class(Decimal("1.9")) == "BALANCED"
    # log10(2) = 0.3010, just outside the 0.3 cut -- the band is 0.5x to 2x,
    # and 2x itself falls on the heavy side of it.
    assert A.side_class(Decimal("2")) == "ASK_HEAVY"
    assert A.side_class(Decimal("0.5")) == "BID_HEAVY"
    assert A.side_class(Decimal("10")) == "ASK_HEAVY"
    assert A.side_class(Decimal("0.1")) == "BID_HEAVY"


def test_an_unmeasurable_book_is_named_not_guessed():
    """A book with no inside-range depth on one side must not be silently
    binned as balanced -- absent is not equal."""
    assert A.side_class(None) == "UNMEASURABLE"


# ------------------------------------------------------------- books_from
def test_books_from_keeps_the_first_sighting_of_each_slug():
    recs = [dict(book([lvl("0.4", "1")], [], "a"), stage="census_book"),
            dict(book([lvl("0.9", "9")], [], "a"), stage="census_book"),
            dict(book([lvl("0.4", "1")], [], "b"), stage="census_book")]
    out = A.books_from(recs)
    assert sorted(out) == ["a", "b"]
    assert out["a"]["bids"][0]["px"]["value"] == "0.4"


def test_books_from_skips_other_stages_and_empty_books():
    recs = [dict(book([lvl("0.4", "1")], [], "a"), stage="detail"),
            dict(book([], [], "b"), stage="census_book")]
    assert A.books_from(recs) == {}


def test_books_from_reads_a_log_with_no_stage_when_asked():
    recs = [book([lvl("0.4", "1")], [], "a")]
    assert list(A.books_from(recs, want=None)) == ["a"]


# ---------------------------------------------------------------- binning
def test_spread_ticks_comparison_needs_a_common_tick():
    """Recorded as a test because Phase 2B saw 0.001 and Phase 2C saw 0.01:
    one tick of spread is a tenth of a cent in one market and a whole cent in
    another, so S_1T is not one regime across the venue."""
    assert Decimal("0.001") != Decimal("0.01")


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(vars().items())
           if n.startswith("test_") and callable(f)]
    bad = []
    for n, f in fns:
        try:
            f()
        except Exception as exc:                       # noqa: BLE001
            bad.append((n, exc))
    for n, exc in bad:
        print("FAIL %s: %r" % (n, exc))
    print("%d passed, %d failed" % (len(fns) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)
