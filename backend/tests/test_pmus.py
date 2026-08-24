"""Polymarket US adapter: mapping verification, order costing, fill parsing.

The SDK client is stubbed — no network. These tests pin the safety-critical
behaviors: never trade an unverified outcome match, never accept a venue
cost above ours, and parse fills only from actual executions.
"""

import pytest

import sportsassets.pmus as pmus
from sportsassets.live_executor import plan_order


# ── whole-unit planning (US venue: integer contracts, whole-cent limit) ──

def test_plan_whole_units_rounds_down_contracts():
    limit, usd, shares = plan_order(0.52, 41600, 0.001, 25.0, 1.0, whole_units=True)
    assert limit == pytest.approx(0.53)
    assert shares == float(int(25.0 / 0.53)) == 47.0
    assert usd == pytest.approx(round(47 * 0.53, 2))
    assert usd <= 25.0


def test_plan_whole_units_small_clip_can_go_sub_dollar():
    # $1.10 budget at 0.60 -> 1 contract -> $0.60; executor's $1 floor skips it.
    _, usd, shares = plan_order(0.60, 1100, 0.001, 25.0, 0.0, whole_units=True)
    assert shares == 1.0
    assert usd == pytest.approx(0.60)


# ── outcome matching ─────────────────────────────────────────────────

def test_outcome_score_exact_team_name():
    m = {"outcome": "Red Sox", "team": {"name": "Boston Red Sox", "abbreviation": "BOS"}}
    assert pmus._outcome_score(m, "Red Sox") == 1.0
    assert pmus._outcome_score(m, "Boston Red Sox") == 1.0


def test_outcome_score_rejects_other_team():
    m = {"outcome": "Yankees", "team": {"name": "New York Yankees"}}
    assert pmus._outcome_score(m, "Red Sox") < pmus.MATCH_FLOOR


def test_outcome_score_accent_and_case_insensitive():
    m = {"outcome": "Atlético Madrid"}
    assert pmus._outcome_score(m, "atletico madrid") == 1.0


# ── resolve_market with a stubbed client ─────────────────────────────

class _StubMarkets:
    def __init__(self, by_slug=None, by_event=None):
        self.by_slug = by_slug or {}
        self.by_event = by_event or {}

    def retrieve_by_slug(self, slug):
        import httpx
        from polymarket_us.errors import NotFoundError

        if slug in self.by_slug:
            return {"market": self.by_slug[slug]}
        resp = httpx.Response(404, request=httpx.Request("GET", "https://gateway.test"))
        raise NotFoundError("not found", response=resp)

    def list(self, params):
        slugs = params.get("eventSlug") or []
        out = []
        for s in slugs:
            out.extend(self.by_event.get(s, []))
        return {"markets": out}


class _StubSearch:
    def __init__(self, events=None):
        self.events = events or []

    def query(self, params):
        return {"events": self.events}


class _StubClient:
    def __init__(self, markets, search=None):
        self.markets = markets
        self.search = search or _StubSearch()


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    monkeypatch.setattr(pmus, "_client", None)
    yield
    pmus._client = None


def _use(client, monkeypatch):
    monkeypatch.setattr(pmus, "_get_client", lambda: client)


def test_resolve_direct_slug_parity(monkeypatch):
    _use(_StubClient(_StubMarkets(by_slug={
        "yankees-red-sox-2026-07-23": {
            "slug": "yankees-red-sox-2026-07-23", "title": "Red Sox to win",
            "outcome": "Red Sox", "closed": False},
    })), monkeypatch)
    r = pmus.resolve_market("yankees-red-sox-2026-07-23", "mlb-nyy-bos",
                            "Yankees vs. Red Sox", "Yankees vs. Red Sox", "Red Sox")
    assert r is not None and r["matched_by"] == "slug"


def test_resolve_slug_hit_wrong_outcome_falls_to_event(monkeypatch):
    # Direct slug exists but is the OTHER team's market — must not match;
    # the event listing contains the right per-outcome sibling.
    _use(_StubClient(_StubMarkets(
        by_slug={"g1": {"slug": "g1", "outcome": "Yankees", "closed": False}},
        by_event={"mlb-nyy-bos": [
            {"slug": "g1-yankees", "outcome": "Yankees", "closed": False},
            {"slug": "g1-red-sox", "outcome": "Red Sox", "closed": False},
        ]},
    )), monkeypatch)
    r = pmus.resolve_market("g1", "mlb-nyy-bos", "Yankees vs. Red Sox",
                            "Yankees vs. Red Sox", "Red Sox")
    assert r is not None
    assert r["market_slug"] == "g1-red-sox"
    assert r["matched_by"] == "event"


def test_resolve_no_verified_match_returns_none(monkeypatch):
    _use(_StubClient(_StubMarkets(by_event={"mlb-nyy-bos": [
        {"slug": "g1-yankees", "outcome": "Yankees", "closed": False},
    ]})), monkeypatch)
    r = pmus.resolve_market(None, "mlb-nyy-bos", "Yankees vs. Red Sox",
                            "Yankees vs. Red Sox", "Red Sox")
    assert r is None  # only the other side listed -> skip, never guess/short


def test_resolve_skips_closed_markets(monkeypatch):
    _use(_StubClient(_StubMarkets(by_event={"e": [
        {"slug": "m1", "outcome": "Red Sox", "closed": True},
    ]})), monkeypatch)
    assert pmus.resolve_market(None, "e", "t", "t", "Red Sox") is None


def test_resolve_via_search_events(monkeypatch):
    _use(_StubClient(
        _StubMarkets(),
        _StubSearch(events=[{
            "title": "Yankees vs. Red Sox",
            "markets": [{"slug": "s-red-sox", "outcome": "Red Sox", "closed": False}],
        }]),
    ), monkeypatch)
    r = pmus.resolve_market(None, None, "Yankees vs. Red Sox",
                            "Yankees vs. Red Sox", "Red Sox")
    assert r is not None and r["market_slug"] == "s-red-sox"
    assert r["matched_by"] == "search"


# ── submit_fok: preview gate and fill parsing ────────────────────────

class _StubOrders:
    def __init__(self, preview_order, create_resp):
        self.preview_order = preview_order
        self.create_resp = create_resp
        self.created = []

    def preview(self, params):
        return {"order": self.preview_order}

    def create(self, params):
        self.created.append(params)
        return self.create_resp


class _SideLookupStub:
    """The last-gate backstop (2026-08-24) asks the venue whether the
    slug unambiguously names a side when the caller passes no intent.
    These fixtures use the SAFE shape (distinct side identifiers); the
    ambiguous shape has its own tests in test_side_intent.py."""

    def retrieve_by_slug(self, slug):
        return {"market": {"slug": slug, "marketSides": [
            {"identifier": slug, "description": "A"},
            {"identifier": slug + "-b", "description": "B"}]}}


def test_submit_fok_filled(monkeypatch):
    orders = _StubOrders(
        preview_order={"cashOrderQty": {"value": "9.54", "currency": "USD"},
                       "price": {"value": "0.53", "currency": "USD"}, "quantity": 18},
        create_resp={"id": "ord-1", "executions": [{
            "type": "EXECUTION_TYPE_FILL",
            "lastPx": {"value": "0.52", "currency": "USD"},
            "lastShares": "18",
            "order": {"state": "ORDER_STATE_FILLED"},
        }]},
    )
    monkeypatch.setattr(pmus, "_get_client", lambda: type("C", (), {"orders": orders,
                                                    "markets": _SideLookupStub()})())
    r = pmus.submit_fok("g1-red-sox", 0.53, 18)
    assert r["ok"] is True
    assert r["order_id"] == "ord-1"
    assert r["filled_shares"] == 18.0
    assert r["fill_price"] == pytest.approx(0.52)
    sent = orders.created[0]
    assert sent["tif"] == "TIME_IN_FORCE_FILL_OR_KILL"
    assert sent["intent"] == "ORDER_INTENT_BUY_LONG"
    assert sent["quantity"] == 18
    assert sent["price"]["value"] == "0.53"


def test_submit_fok_preview_cost_mismatch_aborts(monkeypatch):
    # Venue says this order costs way more than limit*qty -> abort pre-order.
    orders = _StubOrders(
        preview_order={"cashOrderQty": {"value": "17.00", "currency": "USD"}},
        create_resp={"id": "should-not-happen"},
    )
    monkeypatch.setattr(pmus, "_get_client", lambda: type("C", (), {"orders": orders,
                                                    "markets": _SideLookupStub()})())
    r = pmus.submit_fok("m", 0.50, 20)  # our cost: $10
    assert r["ok"] is False
    assert r["status"] == "preview_mismatch"
    assert orders.created == []  # no real order was placed


def test_submit_fok_killed_not_ok(monkeypatch):
    orders = _StubOrders(
        preview_order={"cashOrderQty": {"value": "10.00", "currency": "USD"}},
        create_resp={"id": "ord-2", "executions": [{
            "type": "EXECUTION_TYPE_CANCELED", "lastShares": "0",
            "order": {"state": "ORDER_STATE_CANCELED"},
        }]},
    )
    monkeypatch.setattr(pmus, "_get_client", lambda: type("C", (), {"orders": orders,
                                                    "markets": _SideLookupStub()})())
    r = pmus.submit_fok("m", 0.50, 20)
    assert r["ok"] is False
    assert r["filled_shares"] == 0.0


# ── venue selection ──────────────────────────────────────────────────

def test_active_venue_prefers_us(monkeypatch):
    from sportsassets import live_executor
    from sportsassets.config import settings

    cfg = settings()
    monkeypatch.setattr(cfg, "live_trading_enabled", True)
    monkeypatch.setattr(cfg, "pmus_key_id", "k")
    monkeypatch.setattr(cfg, "pmus_secret_key", "s")
    monkeypatch.setattr(cfg, "pm_private_key", "0xabc")
    assert live_executor.active_venue() == "polymarket-us"
    monkeypatch.setattr(cfg, "pmus_key_id", "")
    assert live_executor.active_venue() == "polymarket-clob"
    monkeypatch.setattr(cfg, "live_trading_enabled", False)
    assert live_executor.active_venue() is None


def test_market_sides_map_to_the_named_sides_own_slug(monkeypatch):
    """The venue decomposes two-sided markets into per-side INSTRUMENT
    markets: description names the side, identifier is that side's own
    orderable slug. Matching the side must return the SIDE's slug — the
    structural end of wrong-side risk (schema named by the 2026-08-04
    audit trails)."""
    from sportsassets import pmus

    two_sided = {
        "slug": "who-will-win-galfi-seidel",
        "question": "Who will win in the upcoming tennis event Dalma Galfi vs Ella Seidel?",
        "closed": False,
        "marketSides": [
            {"id": "1", "description": "Dalma Galfi",
             "identifier": "aec-wta-dalgal-ellsei-2026-08-03"},
            {"id": "2", "description": "Ella Seidel",
             "identifier": "aec-wta-ellsei-dalgal-2026-08-03"},
        ],
    }

    class _Markets:
        def retrieve_by_slug(self, slug):
            raise KeyError(slug)

        def list(self, params):
            return {"markets": []}

    class _Search:
        def query(self, params):
            return {"events": [{"title": "Dalma Galfi vs Ella Seidel",
                                "markets": [two_sided]}]}

    class _Client:
        markets = _Markets()
        search = _Search()

    monkeypatch.setattr(pmus, "_get_client", lambda: _Client())
    r = pmus.resolve_market(None, None,
                            "Warsaw: Dalma Galfi vs Ella Seidel", None,
                            "Ella Seidel")
    assert r is not None
    assert r["market_slug"] == "aec-wta-ellsei-dalgal-2026-08-03"
    assert r["outcome"] == "Ella Seidel"


def test_submit_ioc_partial_fill(monkeypatch):
    """Owner order 2026-08-21 (partial-take copies): an IOC order takes
    the book's available size at or below the limit and cancels the
    rest — a thin book yields a smaller position, not a killed order.
    Default tif stays FOK so every other caller is unchanged."""
    orders = _StubOrders(
        preview_order={"cashOrderQty": {"value": "53.00", "currency": "USD"},
                       "price": {"value": "0.53", "currency": "USD"},
                       "quantity": 100},
        create_resp={"id": "ord-2", "executions": [{
            "type": "EXECUTION_TYPE_PARTIAL_FILL",
            "lastPx": {"value": "0.53", "currency": "USD"},
            "lastShares": "37",
            "order": {"state": "ORDER_STATE_PARTIALLY_FILLED"},
        }]},
    )
    monkeypatch.setattr(pmus, "_get_client",
                        lambda: type("C", (), {"orders": orders,
                                                    "markets": _SideLookupStub()})())
    r = pmus.submit_fok("g1-red-sox", 0.53, 100, False,
                        "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
    assert r["ok"] is True
    assert r["filled_shares"] == 37.0
    assert r["fill_price"] == pytest.approx(0.53)
    assert orders.created[0]["tif"] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
