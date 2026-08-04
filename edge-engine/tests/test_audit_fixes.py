"""Pins for the 2026-08-04 audit fixes: even-count median interpolation,
overround sanity band, and the FOK limit placed at the worst price that
still clears the bar (not the observed ask)."""

import tempfile

import pytest

from edge.fairvalue.feed import _weighted_median


def test_even_count_median_interpolates_instead_of_taking_the_short_side():
    # Two equally-weighted anchors: the old code returned 1.90 (the shorter
    # odds) on every outcome, inflating implied probabilities on both sides.
    assert _weighted_median([(1.90, 3.0), (2.00, 3.0)]) == pytest.approx(1.95)
    # Odd counts and dominant weights keep plain median behaviour.
    assert _weighted_median([(1.90, 3.0), (2.00, 3.0), (2.10, 3.0)]) == 2.00
    assert _weighted_median([(1.90, 1.0), (2.00, 5.0)]) == 2.00


def test_underround_pairs_are_refused_before_devig():
    """sum(1/odds) < 0.99 across mixed books is an artifact, not a market —
    power de-vig would push the exponent above 1 and exaggerate favourites."""
    import pathlib
    import time

    from edge.fairvalue.feed import FeedEvent
    from tests.test_run_cycle_e2e import POLICY, StubFeed, StubVenue, _rig
    from edge.shadow.runner import run_cycle

    ledger, risk = _rig(pathlib.Path(tempfile.mkdtemp()))
    ev = FeedEvent(
        sport_key="soccer_epl", league_code="epl", home="Arsenal",
        away="Chelsea", commence_ts=time.time() + 3600,
        h2h={"Arsenal": 2.30, "Chelsea": 2.30},   # sum(1/o) = 0.87
        fetched_at=time.time(), anchors=1)
    funnel = run_cycle([StubVenue(ask_price=0.30)], StubFeed([ev]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert ledger.summary()["fills"] == 0
    assert funnel.get("overround_rejected", {}).get("2way", 0) >= 1


def test_fok_limit_is_the_worst_price_that_still_clears_the_bar():
    from edge.execution.executor import execute
    from edge.ledger.service import Ledger

    class _Adapter:
        name = "polymarket-us"

        def __init__(self):
            self.orders = []

        def taker_fee(self, price):
            return 0.0

        def place_order(self, slug, price, qty, **kw):
            self.orders.append({"slug": slug, "price": price, "qty": qty})
            return {"ok": True, "count": qty, "price": price,
                    "order_id": "o1", "status": "filled"}

    a = _Adapter()
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    res = execute(adapter=a, ledger=led, mode="LIVE_BETA",
                  mkey="polymarket-us:tok", league="mlb",
                  ask_price=0.50, ask_size=100, size_usd=2.0,
                  edge=0.05, threshold=0.02, decision={}, ts=1.0,
                  entry_price=0.50, taker=True)
    assert res["placed"]
    # entry 0.50 with 3c of slack above the bar -> limit 0.53, never 0.50.
    assert a.orders[0]["price"] == pytest.approx(0.53)
    # A book uptick to 0.52 now fills (venue matches at the real ask); a
    # move past 0.53 kills. Either way we never pay above the bar.
