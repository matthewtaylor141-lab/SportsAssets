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


# ── SH1: the preview bound in COLLATERAL space (owner order 2026-09-05,
# "we need to make sure we are mirroring shorts") ────────────────────

def _short_stub(monkeypatch, venue_cost, create_resp=None, wire=None, qty=None, side=None):
    """A preview stating a cash figure and, when `wire`/`qty` are given,
    echoing the order (U13: a short's guard reads the echo; the SH1
    fixtures below echo what they send). `side`, when given, is the
    venue's derived side on the previewed order."""
    order = {"cashOrderQty": {"value": f"{venue_cost:.2f}", "currency": "USD"}}
    if wire is not None:
        order["price"] = {"value": f"{wire:.2f}", "currency": "USD"}
    if qty is not None:
        order["quantity"] = qty
    if side is not None:
        order["side"] = side
    orders = _StubOrders(
        preview_order=order,
        create_resp=create_resp or {"id": "ord-s", "executions": []},
    )
    monkeypatch.setattr(pmus, "_get_client", lambda: type("C", (), {"orders": orders,
                                                    "markets": _SideLookupStub()})())
    return orders


@pytest.mark.parametrize("wire,qty", [(0.78, 20), (0.89, 1086), (0.65, 675), (0.52, 100),
                                      (0.50, 40), (0.28, 20), (0.11, 50), (0.05, 10)])
def test_expected_cost_is_collateral_on_a_short(monkeypatch, wire, qty):
    """A BUY_SHORT at contract price `wire` ties up (1 - wire) x qty; a
    venue preview stating exactly that is agreement, from a longshot's
    short (0.05: collateral 0.95 a share) to a favourite's (0.89: 0.11)."""
    orders = _short_stub(monkeypatch, (1.0 - wire) * qty, wire=wire, qty=qty)
    r = pmus.submit_fok("m", wire, qty, intent="ORDER_INTENT_BUY_SHORT")
    assert r["status"] != "preview_mismatch", r
    assert orders.created and orders.created[0]["intent"] == "ORDER_INTENT_BUY_SHORT"
    assert r["raw"]["expected_cost"] == pytest.approx((1.0 - wire) * qty)
    assert r["raw"]["cost_space"] == "cash"
    # the same venue figure held to the LONG formula would have refused
    # every short of a favourite and passed every short of a longshot by
    # (1 - wire) / wire: the void-and-inverted guard SH1 replaces
    long_expected = wire * qty
    assert ((1.0 - wire) * qty > long_expected * pmus.PREVIEW_COST_TOLERANCE) == (wire < 0.495)


def test_a_correctly_priced_short_of_a_favourite_is_not_a_preview_mismatch(monkeypatch):
    # his 0.72 leg: the contract sells at 0.28, the collateral is 0.72 x 20 = 14.40
    orders = _short_stub(monkeypatch, 14.40, wire=0.28, qty=20)
    r = pmus.submit_fok("m", 0.28, 20, intent="ORDER_INTENT_BUY_SHORT")
    assert r["status"] != "preview_mismatch" and orders.created


def test_a_short_whose_venue_cost_exceeds_the_collateral_still_refuses(monkeypatch):
    # collateral 0.22 x 20 = 4.40, notional 0.78 x 20 = 15.60; the venue's
    # cash figure is in one of those two spaces (U13), so it is held to
    # the larger: 16.00 is above both (2.6% over the notional): refused,
    # nothing placed. (Before U13 this fixture said 4.60, 4.5% over the
    # collateral alone; that figure is now read as a notional-space
    # statement well inside its bound.)
    orders = _short_stub(monkeypatch, 16.00, wire=0.78, qty=20)
    r = pmus.submit_fok("m", 0.78, 20, intent="ORDER_INTENT_BUY_SHORT")
    assert r["ok"] is False and r["status"] == "preview_mismatch"
    assert r["raw"]["expected_cost"] == pytest.approx(4.40) and orders.created == []
    assert r["raw"]["venue_cost"] == pytest.approx(16.00) and r["raw"]["cost_space"] == "cash"


def test_the_long_expectation_is_byte_identical(monkeypatch):
    """Every existing long caller: the same formula, the same float."""
    orders = _short_stub(monkeypatch, 17.00)
    r = pmus.submit_fok("m", 0.50, 20)                    # our cost: $10, as before
    assert r["status"] == "preview_mismatch" and r["raw"]["expected_cost"] == 0.50 * 20
    assert orders.created == []
    orders = _short_stub(monkeypatch, 10.00)
    assert pmus.submit_fok("m", 0.50, 20, intent="ORDER_INTENT_BUY_LONG")["status"] != "preview_mismatch"
    assert orders.created[0]["intent"] == "ORDER_INTENT_BUY_LONG"


# ── U13: the short's guard checks what the preview can tell us (the
# first two short books, 2026-09-06 15:43Z: every placement refused
# preview_mismatch against a venue figure that was the NOTIONAL) ──────

_SHORT_FACTS = ("expected_cost", "venue_cost", "venue_price", "venue_quantity",
                "venue_side", "cost_space")


def _book6(monkeypatch, cash, wire=0.89, qty=92, side=None):
    """Book 6's placement: BUY_SHORT 92 at wire 0.89, collateral 10.12,
    notional 81.88; the preview echoes `wire`/`qty` and states `cash`."""
    return _short_stub(monkeypatch, cash, wire=wire, qty=qty, side=side)


def _place_short(orders):
    r = pmus.submit_fok("m", 0.89, 92, intent="ORDER_INTENT_BUY_SHORT")
    return r, orders.created


def test_a_short_whose_preview_echoes_it_with_no_cash_figure_is_placed_in_echo_space(monkeypatch):
    # the live shape: cashOrderQty 0.0000 (the per-fill lane's short executions)
    r, created = _place_short(_book6(monkeypatch, 0.0))
    assert r["status"] != "preview_mismatch" and created
    assert created[0]["intent"] == "ORDER_INTENT_BUY_SHORT"
    assert r["raw"]["cost_space"] == "echo" and r["raw"]["venue_cost"] is None
    assert r["raw"]["expected_cost"] == pytest.approx(10.12)
    assert r["raw"]["venue_price"] == pytest.approx(0.89) and r["raw"]["venue_quantity"] == 92
    assert r["raw"]["venue_side"] is None
    assert all(k in r["raw"] for k in _SHORT_FACTS)
    # the placed raw's existing keys come first and are untouched
    assert list(r["raw"])[:2] == ["preview", "response"]


def test_a_cash_figure_that_is_the_notional_is_inside_the_bound(monkeypatch):
    # the observed mismatch: 0.89 x 92 = 81.88, the contract notional
    r, created = _place_short(_book6(monkeypatch, 81.88))
    assert r["status"] != "preview_mismatch" and created
    assert r["raw"]["cost_space"] == "cash" and r["raw"]["venue_cost"] == pytest.approx(81.88)


def test_a_cash_figure_that_is_the_collateral_is_inside_the_bound(monkeypatch):
    r, created = _place_short(_book6(monkeypatch, 10.12))
    assert r["status"] != "preview_mismatch" and created
    assert r["raw"]["cost_space"] == "cash"


def test_a_cash_figure_above_both_spaces_is_a_real_overcharge(monkeypatch):
    # 90.00 > max(10.12, 81.88) x 1.02 = 83.52
    r, created = _place_short(_book6(monkeypatch, 90.00))
    assert r["ok"] is False and r["status"] == "preview_mismatch" and created == []
    assert r["raw"]["venue_cost"] == pytest.approx(90.00) and r["raw"]["cost_space"] == "cash"
    assert r["raw"]["expected_cost"] == pytest.approx(10.12)
    assert r["raw"]["expected_price"] == 0.89 and r["raw"]["expected_quantity"] == 92
    assert "preview" in r["raw"]


def test_a_preview_that_read_our_price_in_the_other_space_is_refused(monkeypatch):
    # the venue echoing 0.11 for a 0.89 wire would place a different order
    r, created = _place_short(_book6(monkeypatch, 0.0, wire=0.11))
    assert r["status"] == "preview_mismatch" and created == []
    assert r["raw"]["venue_price"] == pytest.approx(0.11) and r["raw"]["expected_price"] == 0.89
    assert r["raw"]["cost_space"] is None


def test_a_sub_cent_echo_that_implies_more_collateral_is_refused(monkeypatch):
    # 0.8860 rounds to the 0.89 cent, so the echo passes, but the collateral
    # it implies, 0.114 x 92 = 10.49, is over 10.12 x 1.02 (U13 review, F1:
    # the echo-space money branch had no fixture)
    orders = _short_stub(monkeypatch, 0.0, qty=92)
    orders.preview_order["price"] = {"value": "0.8860", "currency": "USD"}
    r = pmus.submit_fok("m", 0.89, 92, intent="ORDER_INTENT_BUY_SHORT")
    assert r["status"] == "preview_mismatch" and orders.created == []
    assert r["raw"]["cost_space"] == "echo"


def test_an_echo_off_by_one_cent_is_refused_even_when_the_money_would_pass(monkeypatch):
    # 0.90 for a 0.89 wire implies LESS collateral (money passes); the echo
    # alone refuses it (U13 review, F2)
    orders = _short_stub(monkeypatch, 0.0, wire=0.90, qty=92)
    r = pmus.submit_fok("m", 0.89, 92, intent="ORDER_INTENT_BUY_SHORT")
    assert r["status"] == "preview_mismatch" and orders.created == []
    orders = _short_stub(monkeypatch, 0.0, wire=0.89, qty=91)
    r = pmus.submit_fok("m", 0.89, 92, intent="ORDER_INTENT_BUY_SHORT")
    assert r["status"] == "preview_mismatch" and orders.created == []


def test_a_preview_that_read_our_quantity_wrong_is_refused(monkeypatch):
    r, created = _place_short(_book6(monkeypatch, 0.0, qty=920))
    assert r["status"] == "preview_mismatch" and created == []
    assert r["raw"]["venue_quantity"] == 920 and r["raw"]["expected_quantity"] == 92


def test_a_preview_whose_side_is_not_sell_is_a_side_mismatch(monkeypatch):
    r, created = _place_short(_book6(monkeypatch, 0.0, side="ORDER_SIDE_BUY"))
    assert r["ok"] is False and r["status"] == "preview_side_mismatch" and created == []
    assert r["raw"]["venue_side"] == "ORDER_SIDE_BUY"


def test_a_preview_whose_side_is_sell_is_placed(monkeypatch):
    r, created = _place_short(_book6(monkeypatch, 0.0, side="ORDER_SIDE_SELL"))
    assert r["status"] != "preview_side_mismatch" and created
    assert r["raw"]["venue_side"] == "ORDER_SIDE_SELL"


def test_a_preview_without_a_side_is_placed(monkeypatch):
    r, created = _place_short(_book6(monkeypatch, 0.0))
    assert r["status"] not in ("preview_side_mismatch", "preview_mismatch") and created


def test_a_short_preview_with_neither_cash_nor_echo_is_unreadable(monkeypatch):
    orders = _StubOrders(preview_order={}, create_resp={"id": "no"})
    monkeypatch.setattr(pmus, "_get_client", lambda: type("C", (), {"orders": orders,
                                                    "markets": _SideLookupStub()})())
    r = pmus.submit_fok("m", 0.89, 92, intent="ORDER_INTENT_BUY_SHORT")
    assert r["status"] == "preview_unreadable" and orders.created == []
    assert r["raw"]["expected_cost"] == pytest.approx(10.12)


def test_a_long_with_a_cash_figure_above_its_cost_refuses_exactly_as_today(monkeypatch):
    # 0.50 x 20 = 10.00; the venue says 17.00: the long guard, byte-identical
    orders = _short_stub(monkeypatch, 17.00, wire=0.50, qty=20)
    r = pmus.submit_fok("m", 0.50, 20)
    assert r["status"] == "preview_mismatch" and orders.created == []
    assert r["raw"] == {"preview": {"order": orders.preview_order},
                        "expected_cost": 10.0, "venue_cost": 17.0}
    # and a placed long's raw carries none of the short facts
    orders = _short_stub(monkeypatch, 10.00, wire=0.50, qty=20)
    r = pmus.submit_fok("m", 0.50, 20)
    assert not any(k in r["raw"] for k in _SHORT_FACTS)
    assert list(r["raw"]) == ["preview", "response"]
