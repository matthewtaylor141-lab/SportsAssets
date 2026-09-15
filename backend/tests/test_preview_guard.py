"""The preview cost guard must fail CLOSED.

submit_fok previews every buy and refuses when the venue's own cost
exceeds ours by more than PREVIEW_COST_TOLERANCE. It is the one control
positioned to catch a venue charging more than we authorized.

It was inert. `_order_cost(order, default=expected_cost)` returned OUR
number whenever the preview stated no cost, so the guard compared
expected against expected and passed. On 2026-08-24 five fills went
through it at 1.15x-3.87x the authorized clip — 1086 shares requested
at $0.23 filled at $0.89, $249.78 authorized and $966.54 taken.

An unreadable preview is the ABSENCE of a second opinion, not
agreement with ours.
"""

import pytest

from sportsassets import pmus


def _order(px=None, qty=None, cash=None):
    o = {}
    if cash is not None:
        o["cashOrderQty"] = {"value": cash}
    if px is not None:
        o["price"] = {"value": px}
    if qty is not None:
        o["quantity"] = qty
    return o


class TestOrderCost:
    def test_cash_order_qty_wins_when_present(self):
        assert pmus._order_cost(_order(cash=500.0, px=0.5, qty=10)) == 500.0

    def test_price_times_quantity_when_no_cash(self):
        assert pmus._order_cost(_order(px=0.5, qty=10)) == 5.0

    def test_a_silent_preview_reads_none_not_our_own_number(self):
        """The regression itself: no cost stated must NOT come back as
        whatever the caller expected."""
        assert pmus._order_cost({}) is None
        assert pmus._order_cost(_order(px=0.5)) is None
        assert pmus._order_cost(_order(qty=10)) is None
        assert pmus._order_cost(_order(px=0, qty=10)) is None

    def test_none_is_distinguishable_from_zero(self):
        """`if not cost` would collapse these; the caller must not."""
        assert pmus._order_cost({}) is not 0.0  # noqa: F632 — identity intended


class _Client:
    def __init__(self, preview_order, seen):
        self._pv, self._seen = preview_order, seen

        class _Orders:
            def preview(_s, params):
                return {"order": self._pv}

            def create(_s, params):
                self._seen.append(params)
                return {"id": "o1", "executions": []}

        class _Markets:
            def retrieve_by_slug(_s, slug):
                return {"market": {"slug": slug, "marketSides": [
                    {"identifier": slug, "description": "A"},
                    {"identifier": slug + "-b", "description": "B"}]}}

        self.orders, self.markets = _Orders(), _Markets()


class TestSubmitRefuses:
    def _run(self, monkeypatch, preview_order, limit=0.30, qty=100):
        seen = []
        monkeypatch.setattr(pmus, "_get_client",
                            lambda: _Client(preview_order, seen))
        out = pmus.submit_fok("aec-atp-a-b-2026-08-25", limit, qty,
                              intent="ORDER_INTENT_BUY_LONG")
        return out, seen

    def test_a_silent_preview_refuses_and_places_nothing(self, monkeypatch):
        out, seen = self._run(monkeypatch, {})
        assert out["ok"] is False
        assert out["status"] == "preview_unreadable"
        assert seen == [], "no order may reach the venue on a blind guard"

    def test_the_overspend_shape_is_refused(self, monkeypatch):
        """The real 2026-08-24 row: 1086sh authorized at 0.23 ($249.78),
        venue pricing it at 0.89 ($966.54)."""
        out, seen = self._run(
            monkeypatch, _order(px=0.89, qty=1086), limit=0.23, qty=1086)
        assert out["ok"] is False
        assert out["status"] == "preview_mismatch"
        assert out["raw"]["venue_cost"] == pytest.approx(966.54, abs=0.01)
        assert seen == []

    def test_an_agreeing_preview_still_places(self, monkeypatch):
        out, seen = self._run(monkeypatch, _order(px=0.30, qty=100))
        assert seen and seen[0]["quantity"] == 100

    def test_the_two_percent_tolerance_still_absorbs_rounding(self,
                                                              monkeypatch):
        # expected 30.00; venue 30.30 is +1% — inside tolerance
        out, seen = self._run(monkeypatch, _order(cash=30.30))
        assert seen, "a cent of venue rounding must not block a copy"

    def test_just_past_the_tolerance_refuses(self, monkeypatch):
        # expected 30.00; venue 30.90 is +3%
        out, seen = self._run(monkeypatch, _order(cash=30.90))
        assert out["status"] == "preview_mismatch"
        assert seen == []
