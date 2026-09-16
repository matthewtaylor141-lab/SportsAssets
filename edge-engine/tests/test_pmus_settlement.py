"""Settlement comes from the ACCOUNT, not the public market endpoint.

The probe's settle_stats named the failure exactly: the public per-slug
settlement endpoint priced 0 of 782 traded slugs (700 no_price, 82
NotFoundError) while the account showed hundreds of settlements. On this
venue a resolution is an account activity — ACTIVITY_TYPE_POSITION_RESOLUTION
keyed by marketSlug — so fetch_results must read the authed activity feed.

For a buy-only book the payout is the sign of `realized`: every entry costs
less than $1/contract, so a win (payout $1) has realized = qty - cost > 0
and a loss has realized = -cost < 0. realized == 0 proves nothing and must
stay unsettled.
"""

import pytest

from edge.venues.polymarket_us import PolymarketUSAdapter


def _resolution(slug, realized, use_after=True):
    pos = {"realized": {"value": str(realized), "currency": "USD"}}
    return {
        "type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
        "positionResolution": {
            "marketSlug": slug,
            "afterPosition": pos if use_after else {},
            "beforePosition": {} if use_after else pos,
        },
    }


class _Portfolio:
    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def activities(self, req):
        self.requests.append(dict(req))
        idx = int(req.get("cursor") or 0)
        acts = self.pages[idx] if idx < len(self.pages) else []
        nxt = str(idx + 1) if idx + 1 < len(self.pages) else ""
        return {"activities": acts, "nextCursor": nxt, "eof": not nxt}


class _Client:
    def __init__(self, pages):
        self.portfolio = _Portfolio(pages)


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("EDGE_PMUS_WS", "0")
    a = PolymarketUSAdapter.__new__(PolymarketUSAdapter)
    a.book_errors = {}
    return a


def test_win_and_loss_come_from_the_sign_of_realized(adapter):
    adapter._auth = _Client([[
        _resolution("aec-atp-a-b-2026-08-04", 1.32),
        _resolution("aec-wta-c-d-2026-08-04", -2.00, use_after=False),
    ]])
    out = adapter.fetch_results(
        ["aec-atp-a-b-2026-08-04", "aec-wta-c-d-2026-08-04"])
    assert out == {"aec-atp-a-b-2026-08-04": 1.0,
                   "aec-wta-c-d-2026-08-04": 0.0}
    assert adapter.last_settle_stats["priced"] == 2
    assert adapter.last_settle_stats["no_price"] == 0


def test_unresolved_slugs_stay_unsettled_not_guessed(adapter):
    adapter._auth = _Client([[
        _resolution("settled-market", 0.50),
        _resolution("zero-realized", 0.0),   # ambiguous: refuse
    ]])
    out = adapter.fetch_results(
        ["settled-market", "zero-realized", "still-open"])
    assert out == {"settled-market": 1.0}
    assert adapter.last_settle_stats["no_price"] == 2


def test_pagination_follows_the_cursor_until_found(adapter):
    adapter._auth = _Client([
        [_resolution("other-market", 3.0)],
        [_resolution("wanted-market", -1.0)],
    ])
    out = adapter.fetch_results(["wanted-market"])
    assert out == {"wanted-market": 0.0}
    assert len(adapter._auth.portfolio.requests) == 2
    assert adapter._auth.portfolio.requests[1]["cursor"] == "1"


def test_a_feed_error_reports_partial_results_and_names_the_error(adapter):
    class _Boom:
        class portfolio:  # noqa: N801 — mimics client attribute
            @staticmethod
            def activities(req):
                raise RuntimeError("venue down")

    adapter._auth = _Boom()
    out = adapter.fetch_results(["any-slug"])
    assert out == {}
    assert adapter.last_settle_stats["errors"] == 1
    assert "RuntimeError" in adapter.last_settle_stats["first_error"]
