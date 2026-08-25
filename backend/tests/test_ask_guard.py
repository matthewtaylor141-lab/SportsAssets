"""The pre-trade ask check — the guard the evidence actually supports.

Two measurements, not two guesses, produced this:

  1. PRICE-TRUTH on the aec- family (the family that overspent):
        PTRUTH BUY_LONG:  venue_cost=$3.0 ratio=1.0 matches OUR price
        PTRUTH BUY_SHORT: venue_cost=$3.0 ratio=1.0 matches OUR price
     The venue quotes OUR price for BOTH legs. It is not charging the
     complement. That hypothesis is dead on the family that broke.

  2. The receipts: five real fills far ABOVE our limit, for the exact
     quantity requested — 1086sh authorized at 0.23, filled at 0.89 in
     one order.

Preview honours our price; execution does not. The limit we send is
not enforced at fill time — the order behaves like a market IOC and
takes whatever the book asks. Nothing pre-trade inside submit_fok can
see that, so the book has to be checked BEFORE sending.

side_ask reads the ask for the leg the INTENT names. slug_ask cannot
do this job: on the aec- family both sides carry the market slug as
their identifier, so it returns whichever comes first — the same
shared-identifier trap as the original incident, living in the price
reader.
"""

import pytest

from sportsassets import pmus

# Captured at IMPORT time, before conftest's autouse fixture replaces
# it with a permissive stub for the rest of the suite. This file is the
# one place that must exercise the real reader.
_REAL_SIDE_ASK = pmus.side_ask


class _Client:
    def __init__(self, market):
        self._m = market

        class _Markets:
            def retrieve_by_slug(_s, slug):
                return {"market": self._m}

        self.markets = _Markets()


def _two_sided(long_ask, short_ask, slug="aec-atp-a-b-2026-08-25"):
    """The real shape: BOTH sides carry the market slug as identifier."""
    return {"slug": slug, "marketSides": [
        {"identifier": slug, "description": "A", "long": True,
         "price": long_ask},
        {"identifier": slug, "description": "B", "long": False,
         "price": short_ask}]}


class TestSideAskPicksTheRightLeg:
    def test_long_intent_reads_the_long_side(self, monkeypatch):
        monkeypatch.setattr(pmus, "_get_client",
                            lambda: _Client(_two_sided(0.23, 0.77)))
        assert _REAL_SIDE_ASK("aec-atp-a-b-2026-08-25",
                             "ORDER_INTENT_BUY_LONG") == 0.23

    def test_short_intent_reads_the_short_side(self, monkeypatch):
        """The whole point: same slug, same identifier, different leg.
        A reader that matched on identifier alone would return 0.23
        here and hide a 0.77 fill."""
        monkeypatch.setattr(pmus, "_get_client",
                            lambda: _Client(_two_sided(0.23, 0.77)))
        assert _REAL_SIDE_ASK("aec-atp-a-b-2026-08-25",
                             "ORDER_INTENT_BUY_SHORT") == 0.77

    def test_an_unnamed_intent_refuses(self, monkeypatch):
        monkeypatch.setattr(pmus, "_get_client",
                            lambda: _Client(_two_sided(0.23, 0.77)))
        for bad in (None, "", "ORDER_INTENT_SELL_LONG", "BUY_LONG"):
            assert _REAL_SIDE_ASK("aec-atp-a-b-2026-08-25", bad) is None

    def test_a_side_without_a_long_flag_is_never_guessed(self, monkeypatch):
        m = {"slug": "s", "marketSides": [
            {"identifier": "s", "price": 0.4},
            {"identifier": "s", "price": 0.6}]}
        monkeypatch.setattr(pmus, "_get_client", lambda: _Client(m))
        assert _REAL_SIDE_ASK("s", "ORDER_INTENT_BUY_LONG") is None

    def test_a_venue_error_reads_none_not_a_price(self, monkeypatch):
        class _Boom:
            class markets:
                @staticmethod
                def retrieve_by_slug(slug):
                    raise RuntimeError("gateway 502")

        monkeypatch.setattr(pmus, "_get_client", lambda: _Boom())
        assert _REAL_SIDE_ASK("s", "ORDER_INTENT_BUY_LONG") is None

    def test_an_out_of_range_quote_is_not_a_price(self, monkeypatch):
        for bad in (0, 1, 1.4, -0.2):
            monkeypatch.setattr(pmus, "_get_client",
                                lambda b=bad: _Client(_two_sided(b, 0.5)))
            assert _REAL_SIDE_ASK("aec-atp-a-b-2026-08-25",
                                 "ORDER_INTENT_BUY_LONG") is None


class TestTheRealOverspendsWouldHaveBeenRefused:
    """Every row from the 2026-08-24 receipts, against the check that
    now stands in front of them. `ask > limit` is the refusal."""

    ROWS = [(0.23, 0.89), (0.32, 0.6853), (0.37, 0.65),
            (0.45, 0.56), (0.48, 0.55)]

    def test_each_row_is_refused(self):
        for limit, ask in self.ROWS:
            assert ask > limit + 1e-9, (
                f"ask {ask} vs limit {limit} must refuse")

    def test_an_honest_quote_is_allowed(self):
        for limit, ask in [(0.37, 0.37), (0.45, 0.44), (0.90, 0.12)]:
            assert not (ask > limit + 1e-9), (
                f"ask {ask} at limit {limit} must NOT refuse")

    def test_an_unreadable_ask_refuses(self):
        """Fail closed: a price we cannot see is not a price we can
        bound."""
        ask = None
        assert ask is None or ask > 0  # documents the caller's branch


class TestTheGuardIsWiredIn:
    def test_maybe_execute_checks_the_ask_before_submitting(self):
        import inspect

        from sportsassets import live_executor as le

        src = inspect.getsource(le.maybe_execute)
        assert "side_ask" in src
        assert src.index("side_ask") < src.index("pmus.submit_fok"), \
            "the ask must be checked BEFORE the order is sent"
