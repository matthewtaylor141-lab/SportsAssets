"""Build steps 3-4 gates: feed staleness/cache/quota, mapper 0.95 rule,
match-rate accounting, one-per-event registry, state hub."""

import time

from edge.fairvalue.feed import FeedEvent, OpticOddsFeed, TheOddsAPIClient
from edge.ledger.service import Ledger
from edge.venues.mapper import TRADEABLE_SCORE, MarketMatch, VenueMarket, match_event


# ── step 3: feed ────────────────────────────────────────────────────────

def test_staleness_stamp_gates_orders():
    ev = FeedEvent("soccer_epl", "epl", "Arsenal", "Chelsea", 0, fetched_at=time.time())
    assert ev.is_fresh(30)
    ev.fetched_at = time.time() - 31
    assert not ev.is_fresh(30)


def test_event_key_stable_across_name_noise():
    a = FeedEvent("soccer_epl", "epl", "Arsenal FC", "Chelsea", 1_750_000_000)
    b = FeedEvent("soccer_epl", "epl", "arsenal", "Chelsea FC", 1_750_000_000)
    c = FeedEvent("soccer_epl", "epl", "Arsenal", "Liverpool", 1_750_000_000)
    assert a.event_key() == b.event_key() != c.event_key()


class _FakeResp:
    status_code = 200

    def __init__(self, payload, headers=None):
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def _raw_event():
    return {
        "home_team": "Arsenal", "away_team": "Chelsea",
        "commence_time": "2026-08-01T15:00:00Z",
        "bookmakers": [{"key": "pinnacle", "markets": [
            {"key": "h2h", "outcomes": [
                {"name": "Arsenal", "price": 2.1}, {"name": "Chelsea", "price": 3.4},
                {"name": "Draw", "price": 3.5}]}]}],
    }


def test_cache_absorbs_refetch_and_quota_floor_serves_stale(monkeypatch):
    client = TheOddsAPIClient(api_key="k", cache_ttl_s=60, quota_reserve=50)
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _FakeResp([_raw_event()], headers={"x-requests-remaining": "40"})

    monkeypatch.setattr(client._sess, "get", fake_get)
    first = client.fetch_events("soccer_epl")
    assert len(first) == 1 and first[0].fetched_at > 0
    client.fetch_events("soccer_epl")
    assert len(calls) == 1  # served from cache

    # Cache expired but quota below reserve -> stale cache, no HTTP spend.
    client._cache["soccer_epl"] = (time.time() - 120, first)
    again = client.fetch_events("soccer_epl")
    assert again == first
    assert len(calls) == 1


def test_quota_headers_tracked(monkeypatch):
    client = TheOddsAPIClient(api_key="k")
    monkeypatch.setattr(
        client._sess, "get",
        lambda *a, **k: _FakeResp([_raw_event()], headers={
            "x-requests-remaining": "123", "x-requests-used": "877"}),
    )
    client.fetch_events("soccer_epl")
    assert client.quota() == {"remaining": 123.0, "used": 877.0}


def test_opticodds_is_an_explicit_stub():
    import pytest

    with pytest.raises(NotImplementedError):
        OpticOddsFeed(api_key="x").fetch_events("soccer_epl")


# ── step 4: mapper 0.95 rule + match-rate report ───────────────────────

def _mkt(title, league="epl"):
    return VenueMarket(market_id="c1", title=title, league_code=league,
                       outcome_tokens={"Arsenal": "t1", "Chelsea": "t2"})


def test_exact_match_is_tradeable():
    m = match_event("Arsenal", "Chelsea", "epl", [_mkt("Arsenal vs. Chelsea")])
    assert m is not None and m.tradeable and m.score >= TRADEABLE_SCORE


def test_fuzzy_but_below_95_is_mapped_not_tradeable():
    m = MarketMatch(_mkt("x"), score=0.90, home_outcome="Arsenal", away_outcome="Chelsea")
    assert not m.tradeable  # 0.85-0.95: counts as mapped, carries no orders


def test_match_rate_report_gate(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    day = "2026-07-24"
    for _ in range(96):
        led.record_match_stat(day, "kalshi", "epl", mapped=True, tradeable=True)
    for _ in range(4):
        led.record_match_stat(day, "kalshi", "epl", mapped=True, tradeable=False)
    led.record_match_stat(day, "kalshi", "nba", mapped=False, tradeable=False)
    report = {(r["venue"], r["league"]): r for r in led.match_rate_report(days=2)}
    epl = report[("kalshi", "epl")]
    assert epl["feed_events"] == 100 and epl["tradeable_rate"] == 0.96
    assert report[("kalshi", "nba")]["tradeable_rate"] == 0.0


# ── one-per-event registry + state hub ─────────────────────────────────

def test_event_claimed_exactly_once(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    assert led.claim_event("ev1", "mkt-a", "kalshi") is True
    assert led.claim_event("ev1", "mkt-b", "polymarket-us") is False  # never add
    assert led.event_traded("ev1") and not led.event_traded("ev2")


def test_state_hub_round_trip(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    assert led.get_state("halt_until") is None
    led.set_state("halt_until", {"ts": 123.0, "reason": "circuit_breaker"})
    assert led.get_state("halt_until")["reason"] == "circuit_breaker"
    led.log_mode("PAPER", "startup")
    led.log_mode("LIVE_BETA", "checklist clean")
    modes = led.mode_transitions()
    assert modes[0]["mode"] == "LIVE_BETA" and modes[1]["mode"] == "PAPER"


# ── PM-US live book stream (build: WS integration) ─────────────────────

def test_stream_cache_serves_and_expires():
    from edge.venues.pmus_stream import BookStreamer

    s = BookStreamer("k", "s", autostart=False)
    s._on_market_data({"marketData": {"marketSlug": "m1",
                                      "offers": [{"px": {"value": "0.47"}, "qty": "100"}]}})
    assert s.get("m1")["offers"][0]["qty"] == "100"
    assert s.get("m2") is None
    s._cache["m1"] = (time.time() - 120, s._cache["m1"][1])
    assert s.get("m1", max_age_s=90) is None  # stale entries never serve
    st = s.stats()
    assert st["updates"] == 1 and st["connected"] is False


def test_stream_ensure_dedupes_and_bounds():
    from edge.venues.pmus_stream import BookStreamer

    s = BookStreamer("k", "s", autostart=False)
    s.ensure(["a", "b", "a", ""])
    s.ensure(["b", "c"])
    assert s._subscribed == {"a", "b", "c"}
    assert sorted(s._pending) == ["a", "b", "c"]


def test_adapter_serves_book_from_stream_without_rest():
    import types

    from edge.venues.polymarket_us import PolymarketUSAdapter
    from edge.venues.pmus_stream import BookStreamer

    a = PolymarketUSAdapter.__new__(PolymarketUSAdapter)
    a.book_errors = {}
    a._stream = BookStreamer("k", "s", autostart=False)
    a._stream._on_market_data({"marketData": {"marketSlug": "slug-x",
        "bids": [{"px": {"value": "0.45"}, "qty": "50"}],
        "offers": [{"px": {"value": "0.47"}, "qty": "80"}]}})

    class ExplodingMarkets:  # REST must not be touched on a cache hit
        def book(self, slug):
            raise AssertionError("REST called despite stream cache hit")

    a._pub = types.SimpleNamespace(markets=ExplodingMarkets())
    book = a.get_book("evt", "slug-x")
    assert book.asks[0].price == 0.47 and book.asks[0].size == 80
    assert book.bids[0].price == 0.45


# ── free-tier speedups: alternates, games-aware TTL ────────────────────

def test_alternate_lines_fold_into_spread_total_buckets(monkeypatch):
    client = TheOddsAPIClient(api_key="k")
    raw = {
        "home_team": "Lakers", "away_team": "Celtics",
        "commence_time": "2026-08-01T15:00:00Z",
        "bookmakers": [{"key": "pinnacle", "markets": [
            {"key": "h2h", "outcomes": [
                {"name": "Lakers", "price": 1.9}, {"name": "Celtics", "price": 1.9}]},
            {"key": "spreads", "outcomes": [
                {"name": "Lakers", "price": 1.9, "point": -5.5},
                {"name": "Celtics", "price": 1.9, "point": 5.5}]},
            {"key": "alternate_spreads", "outcomes": [
                {"name": "Lakers", "price": 2.4, "point": -8.5},
                {"name": "Celtics", "price": 1.55, "point": 8.5}]},
            {"key": "alternate_totals", "outcomes": [
                {"name": "Over", "price": 2.1, "point": 215.5},
                {"name": "Under", "price": 1.75, "point": 215.5}]},
        ]}],
    }
    monkeypatch.setattr(client._sess, "get", lambda *a, **k: _FakeResp([raw]))
    [ev] = client.fetch_events("basketball_nba")
    assert "Lakers -5.5" in ev.spreads and "Lakers -8.5" in ev.spreads
    assert "Over 215.5" in ev.totals


def test_games_aware_ttl(monkeypatch):
    client = TheOddsAPIClient(api_key="k", cache_ttl_s=10)
    # No games within the live/imminent window -> slow TTL.
    idle = dict(_raw_event(), commence_time="2026-12-01T15:00:00Z")
    monkeypatch.setattr(client._sess, "get", lambda *a, **k: _FakeResp([idle]))
    client.fetch_events("soccer_epl")
    assert client._sport_active["soccer_epl"] is False
    assert client._ttl_for("soccer_epl") >= 300
    # Imminent game -> fast TTL.
    import datetime as dt

    soon = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
    live = dict(_raw_event(), commence_time=soon)
    client._cache.clear()
    monkeypatch.setattr(client._sess, "get", lambda *a, **k: _FakeResp([live]))
    client.fetch_events("soccer_epl")
    assert client._sport_active["soccer_epl"] is True
    assert client._ttl_for("soccer_epl") == 10
