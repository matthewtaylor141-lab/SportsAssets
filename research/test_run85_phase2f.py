#!/usr/bin/env python3
"""Offline tests for Run 85 Phase 2F. Contacts nothing.

The load-bearing rules: the pacer drain actually clears the debt, a null
primaryTag is never counted as a sport, and the markout concepts stay apart --
an unchanged book at a horizon is an observation worth 0, not a missing one.

Run:  python3 -m pytest research/test_run85_phase2f.py -q
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

_s = importlib.util.spec_from_file_location(
    "p2f", Path(__file__).with_name("run85_phase2f.py"))
F = importlib.util.module_from_spec(_s)
_s.loader.exec_module(F)


def lvl(px, qty):
    return {"px": {"value": px, "currency": "USD"}, "qty": qty}


def body(bids, offers):
    return {"marketData": {"marketSlug": "m", "bids": bids, "offers": offers}}


# ------------------------------------------------------------- the drain
def test_drain_clears_the_debt_so_the_next_wait_returns_at_once():
    """THE PHASE 2E DEFECT, pinned. After drain() the next wait() must not
    block -- that blocking is exactly what displaced 2E's t0 by 2.6 s."""
    p = F.B.AdaptivePacer(base=0.4)
    p.wait()                       # stamp _last, as a real request would
    slept = F.drain(p)
    t = time.monotonic()
    p.wait()
    blocked = time.monotonic() - t
    assert slept > 0, "drain should have had debt to clear"
    assert blocked < 0.05, "wait() still blocked %.3fs after drain" % blocked


def test_drain_does_not_consume_a_slot():
    """drain() must not restamp _last -- restamping would create fresh debt
    and the caller would never converge."""
    p = F.B.AdaptivePacer(base=0.2)
    p.wait()
    before = p._last
    F.drain(p)
    assert p._last == before


def test_drain_on_a_fresh_pacer_is_a_no_op():
    p = F.B.AdaptivePacer(base=0.2)
    assert F.drain(p) == 0.0


def test_drain_respects_a_widened_spacing_after_backoff():
    """After a 429 the spacing widens; the drain must clear the WIDER debt,
    not the base one."""
    p = F.B.AdaptivePacer(base=0.2)
    p.wait()
    p.spacing = 0.6
    p._last = time.monotonic()
    slept = F.drain(p)
    assert slept > 0.4, "drain cleared only the base spacing, not the widened one"


# ------------------------------------------------------------ sport rule
def test_a_null_primary_tag_is_not_a_sport():
    """Section 3 is explicit: do not count null primaryTag as a sport. Phase 2E
    counted 'None' as one of its four."""
    assert F.sport_bucket(None) is None
    assert F.sport_bucket("") is None


def test_sport_buckets_resolve_the_named_families():
    assert F.sport_bucket("nfl") == "nfl"
    assert F.sport_bucket("cfb") == "cfb"
    assert F.sport_bucket("mlb") == "mlb"
    assert F.sport_bucket("epl") == "soccer"
    assert F.sport_bucket("lmx") == "other"


# ---------------------------------------------------------- price bands
def test_price_bands_split_low_mid_high():
    assert F.price_band("0.05") == "P_LOW"
    assert F.price_band("0.50") == "P_MID"
    assert F.price_band("0.95") == "P_HIGH"
    assert F.price_band(None) is None


# ------------------------------------------------------ ladder identity
def test_ladder_hash_is_stable_and_discriminating():
    a = body([lvl("0.5", "1")], [lvl("0.6", "1")])
    b = body([lvl("0.5", "1")], [lvl("0.6", "1")])
    c = body([lvl("0.5", "2")], [lvl("0.6", "1")])
    assert F.ladder_hash(a) == F.ladder_hash(b)
    assert F.ladder_hash(a) != F.ladder_hash(c)


def test_touch_of_picks_highest_bid_and_lowest_ask():
    bb, ba = F.touch_of(body([lvl("0.45", "1"), lvl("0.50", "1")],
                             [lvl("0.60", "1"), lvl("0.55", "1")]))
    assert str(bb) == "0.50" and str(ba) == "0.55"


# ------------------------------------------------- markout semantics
def test_an_unchanged_book_is_an_observation_not_a_gap():
    """THE RETRACTION, pinned as a test. Same ladder at t and t+h means the
    horizon observation EXISTS and the markout is zero. Nothing in this module
    may treat sameness as missingness."""
    t0 = body([lvl("0.50", "10")], [lvl("0.51", "10")])
    th = body([lvl("0.50", "10")], [lvl("0.51", "10")])
    assert F.ladder_hash(t0) == F.ladder_hash(th)      # B: book did not change
    b0, a0 = F.touch_of(t0)
    bh, ah = F.touch_of(th)
    assert (bh - b0) == 0 and (ah - a0) == 0           # C: markout is 0
    # A is about whether a snapshot was obtained, and both of these are
    # well-formed snapshots -- so availability does not depend on B.
    assert F.B.market_data(t0) and F.B.market_data(th)


def test_a_changed_book_yields_a_nonzero_markout():
    t0 = body([lvl("0.50", "10")], [lvl("0.51", "10")])
    th = body([lvl("0.52", "10")], [lvl("0.53", "10")])
    assert F.ladder_hash(t0) != F.ladder_hash(th)
    b0, _ = F.touch_of(t0)
    bh, _ = F.touch_of(th)
    assert (bh - b0) != 0


# --------------------------------------------------------------- budget
def test_budget_raises_rather_than_overspend():
    b = F.Budget(1)
    b.take()
    try:
        b.take()
    except RuntimeError as exc:
        assert "BOUND_REACHED" in str(exc)
    else:
        raise AssertionError("budget exceeded its disclosed bound")


# ------------------------------------------------------------ rate rule
def test_the_scheduler_plan_shape_fits_the_conservative_rate():
    """0.4 rps nominal means a 2.5 s spacing floor. The two-subject plan must
    satisfy both the floor and the mean."""
    starts = [0.0, 2.5]
    plan = sorted(s + h for s in starts for h in (0.0,) + F.HORIZONS)
    gaps = [plan[i + 1] - plan[i] for i in range(len(plan) - 1)]
    span = plan[-1] - plan[0]
    assert min(gaps) >= F.SPACING_S - 1e-9
    assert len(plan) / span <= F.NOMINAL_RPS + 1e-9
    assert len(plan) <= F.MAX_SCHEDULER


def test_the_nominal_rate_is_below_the_rate_that_produced_429s():
    assert F.NOMINAL_RPS < 0.5
    assert F.SPACING_S > 2.0


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
