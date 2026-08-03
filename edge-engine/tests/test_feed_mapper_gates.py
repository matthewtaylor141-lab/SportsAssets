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
    q = client.quota()
    assert q["remaining"] == 123.0 and q["used"] == 877.0


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
    from datetime import datetime, timezone

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")  # relative: never ages out
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


def test_stream_prune_frees_memory_and_subscription_room():
    """Ended games must give their book memory and subscription room back.

    `ensure` measures room against the lifetime subscription count, so
    without pruning the 4,000-slug bound slowly fills with finished
    markets and NEW games silently stop streaming — a leak that is also a
    coverage bug. This (plus two more unbounded caches) is what kept
    OOM-restarting the deployed worker on 2026-08-02.
    """
    from edge.venues.pmus_stream import BookStreamer

    s = BookStreamer("k", "s", autostart=False)
    s.ensure(["live-1", "ended-1", "ended-2"])
    s._on_market_data({"marketData": {"marketSlug": "ended-1",
                                      "offers": [{"px": {"value": "0.5"}, "qty": "1"}]}})
    s.prune({"live-1"})
    assert s._subscribed == {"live-1"}
    assert s._pending == ["live-1"]
    assert s.get("ended-1", max_age_s=1e9) is None
    # ...and the freed room is actually usable again.
    s.ensure(["fresh-1"])
    assert "fresh-1" in s._subscribed


def test_stream_stores_only_the_level_arrays():
    """The venue's marketData payload carries metadata the adapter never
    reads. At 4,000 cached slugs on a memory-capped worker, storing it is
    pure ballast — only the level arrays may be kept."""
    from edge.venues.pmus_stream import BookStreamer

    s = BookStreamer("k", "s", autostart=False)
    s._on_market_data({"marketData": {"marketSlug": "m1",
                                      "offers": [{"px": {"value": "0.47"}, "qty": "9"}],
                                      "title": "x" * 5000, "eventSlug": "y",
                                      "volume": "123", "extra": {"deep": "z"}}})
    cached = s.get("m1")
    assert set(cached) == {"offers"}


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


def _soon(hours):
    import datetime as dt

    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_a_sport_with_games_in_the_window_is_always_polled_fast(monkeypatch):
    """The freshness rule is 30s, so slowing a sport down is the same as
    switching it off. Anything with tradeable games stays fast, full stop."""
    client = TheOddsAPIClient(api_key="k", cache_ttl_s=10)
    monkeypatch.setattr(client._sess, "get",
                        lambda *a, **k: _FakeResp([dict(_raw_event(),
                                                        commence_time=_soon(48))]))
    client.fetch_events("soccer_epl")
    assert client._sport_events["soccer_epl"] == 1
    assert client._ttl_for("soccer_epl") == 10
    client._quota["remaining"] = 1000.0      # even when credits run low
    assert client._ttl_for("soccer_epl") == 10


def _rich_client(remaining, used=10_000.0, elapsed_s=86_400.0):
    """A client whose measured burn rate gives `remaining / (used/day)` of
    runway — the input the speed scaler actually reads."""
    c = TheOddsAPIClient(api_key="k", cache_ttl_s=25)
    c._sport_events["baseball_mlb"] = 3
    c._quota = {"remaining": remaining, "used": used}
    c._quota_t0, c._quota_used0 = time.time() - elapsed_s, 0.0
    return c


def test_a_rich_quota_buys_freshness_automatically():
    """Staleness is the measured edge leak (retention 0.72), and freshness
    is bought with credits. A plan upgrade must speed the engine up on its
    own — no config edit, no redeploy: runway >= 1.25x the billing target
    drops active sports to the fast TTL and props to the fast event TTL."""
    c = _rich_client(remaining=10_000.0 * 90)   # ~90 days of runway
    assert c._credits_rich() is True
    assert c._ttl_for("baseball_mlb") == c.FAST_TTL_S
    assert c._event_ttl_s() == c.FAST_EVENT_TTL_S
    assert c.quota()["fast_mode"] is True


def test_a_thin_or_unknown_quota_never_speeds_up():
    """The same knob must fail SAFE: a runway under 1.25x the target keeps
    the base clocks (the governor handles real scarcity by parking sports),
    and an unmeasured quota is not permission to burn it."""
    thin = _rich_client(remaining=10_000.0 * 8)    # 8d < 1.25x7d target
    assert thin._credits_rich() is False
    assert thin._ttl_for("baseball_mlb") == 25
    assert thin._event_ttl_s() == thin.EVENT_TTL_S

    unknown = TheOddsAPIClient(api_key="k", cache_ttl_s=25)
    unknown._sport_events["baseball_mlb"] = 3
    assert unknown._credits_rich() is False
    assert unknown._ttl_for("baseball_mlb") == 25


def test_a_sport_with_nothing_in_the_window_coasts(monkeypatch):
    """Covering every sport means most of them are out of season at any time.
    They cost credits and can never trade — there is nothing to be stale
    about, so they get a long TTL instead of a share of the budget."""
    client = TheOddsAPIClient(api_key="k", cache_ttl_s=10)
    idle = dict(_raw_event(), commence_time="2027-12-01T15:00:00Z")
    monkeypatch.setattr(client._sess, "get", lambda *a, **k: _FakeResp([idle]))
    client.fetch_events("soccer_epl")
    assert client._sport_events["soccer_epl"] == 0
    assert client._ttl_for("soccer_epl") >= 900


def test_governor_parks_the_quietest_sports_when_credits_would_run_out():
    client = TheOddsAPIClient(api_key="k", cache_ttl_s=10)
    sports = ["busy", "medium", "quiet", "empty"]
    client._sport_events = {"busy": 40, "medium": 20, "quiet": 5, "empty": 0}
    # ~2.3 days of runway against a 7-day target -> afford a third of them.
    client._quota = {"remaining": 2_333.0, "used": 5_000.0}
    client._quota_t0, client._quota_used0 = time.time() - 86_400, 4_000.0
    parked = client.rebalance_budget(sports)
    assert "busy" not in parked and "empty" in parked
    assert client.quota()["parked_sports"] == len(parked)

    # Credits recover -> everything comes back. A park is never permanent.
    client._quota["remaining"] = 10_000_000.0
    assert client.rebalance_budget(sports) == set()


def test_governor_does_nothing_without_a_burn_rate():
    client = TheOddsAPIClient(api_key="k")
    assert client.rebalance_budget(["a", "b"]) == set()


# ── coverage: ask for every sport, block only what's measured negative ──

class _SportsResp(_FakeResp):
    pass


def _sports_payload():
    return [
        {"key": "soccer_epl", "active": True},
        {"key": "soccer_norway_eliteserien", "active": True},   # unmapped
        {"key": "americanfootball_ncaaf", "active": True},      # unmapped
        {"key": "baseball_mlb", "active": True},                # allowed (ML blocked)
        {"key": "soccer_uefa_champs_league", "active": True},   # BLOCKED
        {"key": "tennis_atp_wimbledon", "active": True},        # allowed (ML blocked)
        {"key": "soccer_epl_winner", "active": False},          # not active
    ]


def test_coverage_is_every_active_sport_not_a_hardcoded_list(monkeypatch):
    """The static map capped the top of the funnel: a market we never ask
    about can never be traded, however good its price."""
    from edge.execution.engine import Policy

    client = TheOddsAPIClient(api_key="k")
    monkeypatch.setattr(client._sess, "get",
                        lambda *a, **k: _FakeResp(_sports_payload()))
    keys = client.resolve_sport_keys(Policy.load())
    assert "soccer_norway_eliteserien" in keys    # unmeasured, still traded
    assert "americanfootball_ncaaf" in keys
    assert "soccer_epl" in keys


def test_measured_negative_leagues_stay_blocked_under_full_coverage(monkeypatch):
    """Widening coverage must not quietly re-admit leagues that lost money.
    These keys were mapped precisely so the blocklist can reach them."""
    from edge.execution.engine import Policy

    client = TheOddsAPIClient(api_key="k")
    monkeypatch.setattr(client._sess, "get",
                        lambda *a, **k: _FakeResp(_sports_payload()))
    keys = client.resolve_sport_keys(Policy.load())
    assert "soccer_uefa_champs_league" not in keys   # -1.13% on 170,922 fills
    # Flat-but-not-negative sports are FETCHED; their moneyline is shut by
    # category_blocks while run lines and totals trade.
    assert "baseball_mlb" in keys and "tennis_atp_wimbledon" in keys
    assert "soccer_epl_winner" not in keys        # inactive


def test_unmapped_sport_keys_still_get_a_league_code():
    c = TheOddsAPIClient(api_key="k")
    assert c.league_of("soccer_epl") == "epl"
    assert c.league_of("baseball_mlb") == "mlb"          # reaches category_blocks
    assert c.league_of("tennis_atp_wimbledon") == "atp"  # reaches category_blocks
    assert c.league_of("soccer_norway_eliteserien") == "soccer_norway_eliteserien"


def test_a_competitions_qualifying_rounds_inherit_its_league_code():
    """Otherwise the blocklist cannot reach them.

    The feed carries qualifying and playoff rounds under keys that EXTEND the
    parent key. Only the parent was mapped, so
    `soccer_uefa_champs_league_qualification` resolved to its raw sport key,
    never matched the `ucl` blocklist entry, and was waved through by
    `unknown_league_policy: allow`. Caught live 2026-08-02 holding two open
    positions in a league measured net-negative and deliberately shut.
    """
    c = TheOddsAPIClient(api_key="k")
    assert c.league_of("soccer_uefa_champs_league_qualification") == "ucl"
    assert c.league_of("soccer_germany_bundesliga_playoffs") == "bun"
    # The exact key still wins over any prefix.
    assert c.league_of("soccer_uefa_champs_league") == "ucl"
    # A different competition is not swallowed by a shared stem: the match
    # requires a full segment boundary, not a character prefix.
    assert c.league_of("soccer_eplx") == "soccer_eplx"


def test_the_blocklist_actually_reaches_a_qualifying_round():
    """The mapping above only matters if policy then refuses the trade."""
    from edge.execution.engine import Policy, strategy_filter

    c = TheOddsAPIClient(api_key="k")
    league = c.league_of("soccer_uefa_champs_league_qualification")
    p = Policy.load()
    p.leagues = {**p.leagues, "blocked_categories": []}   # isolate the league gate
    assert not strategy_filter(p, league, 0.50, 0.55, consensus_books=6).ok


def test_an_unmeasured_league_is_allowed_not_shadowed():
    """An absent league is unmeasured, not disproven — and the de-vig that
    produces the edge is the same arithmetic in every league."""
    from edge.execution.engine import Policy

    p = Policy.load()
    assert p.league_allowed("soccer_norway_eliteserien") == "allow"
    assert p.league_allowed("ucl") == "block"        # measured -1.13%
    # MLB was blocked for being FLAT (-0.14c over 203,317 fills, ~1.3 SE from
    # zero). That is "unmeasured", not "disproven" — and it was measured on
    # moneyline bets, which is the only category still shut.
    assert p.league_allowed("mlb") == "allow"
    assert p.category_blocked("mlb", "moneyline")
    assert not p.category_blocked("mlb", "spread")
    assert not p.category_blocked("mlb", "total")
    assert p.league_allowed("epl") == "allow"
