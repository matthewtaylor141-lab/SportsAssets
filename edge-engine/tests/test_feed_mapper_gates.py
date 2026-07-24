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
