"""Partial-game markets come from the PER-EVENT endpoint.

Measured against the live provider on 2026-07-31: the bulk odds endpoint
answers 422 for every segment key we ask for —

    {"message":"Markets not supported by this endpoint: h2h_1st_5_innings,
      h2h_h1, h2h_p1, spreads_1st_5_innings, ...","error_code":"INVALID_MARKET"}

— which is not a plan limit but the wrong URL. They are served per event, at
`/sports/{sport}/events/{id}/odds`, and Pinnacle quotes them for MLB and EPL.
"""

import time

import pytest

from edge.fairvalue.feed import FeedEvent, TheOddsAPIClient


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code, self.headers = payload, status, {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


def _client(handler):
    c = TheOddsAPIClient.__new__(TheOddsAPIClient)
    c._key, c.BASE = "k", "https://api.example/v4"
    c._quota, c._quota_reserve = {}, 50
    c._no_segments, c._no_props, c._event_cache = set(), set(), {}
    c.calls = []

    class Sess:
        def get(_self, url, params=None, timeout=None):
            c.calls.append((url, (params or {}).get("markets")))
            return handler(url, params or {})

    c._sess = Sess()
    c._track_response = lambda r: None
    return c


def _ev(eid="e1", ts=None):
    return FeedEvent(sport_key="baseball_mlb", league_code="mlb", home="Cubs",
                     away="Yankees", commence_ts=ts or (time.time() + 3600),
                     h2h={"Cubs": 2.0, "Yankees": 2.0}, event_id=eid,
                     fetched_at=time.time())


_F5 = {"bookmakers": [
    {"key": "pinnacle", "markets": [
        {"key": "h2h_1st_5_innings",
         "outcomes": [{"name": "Cubs", "price": 2.10},
                      {"name": "Yankees", "price": 1.80}]},
        {"key": "totals_1st_5_innings",
         "outcomes": [{"name": "Over", "price": 1.95, "point": 4.5},
                      {"name": "Under", "price": 1.95, "point": 4.5}]}]},
    {"key": "lowvig", "markets": [
        {"key": "h2h_1st_5_innings",
         "outcomes": [{"name": "Cubs", "price": 2.30},
                      {"name": "Yankees", "price": 1.70}]}]},
]}


def test_segments_arrive_from_the_per_event_endpoint():
    c = _client(lambda url, p: _Resp(_F5))
    ev = _ev()
    assert c._enrich_segments("baseball_mlb", [ev], time.time()) == 1
    assert "f5" in ev.segments
    assert set(ev.segments["f5"]) == {"h2h", "totals"}
    assert ev.segments["f5"]["h2h"]["Cubs"] == 2.10        # Pinnacle outvotes
    assert ev.segments["f5"]["totals"]["Over 4.5"] == 1.95
    # ...and it asked the per-EVENT url, not the bulk one.
    assert "/events/e1/odds" in c.calls[0][0]


def test_a_segment_quote_can_supply_the_anchor():
    """Pinnacle may quote the first five innings without quoting the full
    game. That still anchors the bet we are actually pricing."""
    c = _client(lambda url, p: _Resp(_F5))
    ev = _ev()
    ev.anchors = 0
    c._enrich_segments("baseball_mlb", [ev], time.time())
    assert ev.anchors == 1


def test_a_422_is_remembered_instead_of_retried_forever():
    """Being told 'we do not carry this' costs credits. Paying for that
    answer once per cycle, per event, forever, is how a quota disappears."""
    c = _client(lambda url, p: _Resp({"message": "nope"}, status=422))
    evs = [_ev(f"e{i}") for i in range(5)]
    assert c._enrich_segments("baseball_mlb", evs, time.time()) == 0
    assert "baseball_mlb" in c._no_segments
    assert len(c.calls) == 1                       # gave up after the first
    assert c._enrich_segments("baseball_mlb", evs, time.time()) == 0
    assert len(c.calls) == 1                       # and never asked again


def test_payloads_are_cached_for_the_ttl():
    """Per-event calls are billed per market per region. Refreshing a
    first-five-innings line every 30s costs more than the rest of the feed
    and tells us nothing — these move slowly."""
    c = _client(lambda url, p: _Resp(_F5))
    ev, now = _ev(), time.time()
    c._enrich_segments("baseball_mlb", [ev], now)
    c._enrich_segments("baseball_mlb", [ev], now + 60)
    assert len(c.calls) == 1
    c._enrich_segments("baseball_mlb", [ev], now + c.EVENT_TTL_S + 1)
    assert len(c.calls) == 2


def test_the_quota_floor_stops_enrichment():
    c = _client(lambda url, p: _Resp(_F5))
    c._quota = {"remaining": 10}                   # below the 50 reserve
    assert c._enrich_segments("baseball_mlb", [_ev()], time.time()) == 0
    assert c.calls == []


def test_the_fan_out_is_bounded_and_takes_the_nearest_games():
    c = _client(lambda url, p: _Resp(_F5))
    now = time.time()
    evs = [_ev(f"e{i}", ts=now + 3600 * (100 - i)) for i in range(100)]
    c._enrich_segments("baseball_mlb", evs, now)
    assert len(c.calls) == c.EVENT_MAX_PER_SPORT
    # Nearest first: e99 kicks off soonest, so it must be in the batch.
    assert any("/events/e99/" in u for u, _ in c.calls)


def test_enrichment_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("EDGE_EVENT_MARKETS", "0")
    c = _client(lambda url, p: _Resp(_F5))
    assert c._enrich_segments("baseball_mlb", [_ev()], time.time()) == 0
    assert c.calls == []


def test_a_transport_failure_costs_the_extras_not_the_slate():
    def boom(url, p):
        raise ConnectionError("provider down")

    c = _client(boom)
    ev = _ev()
    assert c._enrich_segments("baseball_mlb", [ev], time.time()) == 0
    assert ev.h2h == {"Cubs": 2.0, "Yankees": 2.0}   # full game untouched


# ── props ride the same call ────────────────────────────────────────────

_PROPS = {"bookmakers": [
    {"key": "pinnacle", "markets": [
        {"key": "pitcher_strikeouts",
         "outcomes": [
             {"name": "Over", "description": "Shota Imanaga", "point": 4.5,
              "price": 1.80},
             {"name": "Under", "description": "Shota Imanaga", "point": 4.5,
              "price": 2.10}]}]}]}


def test_props_and_segments_share_one_request():
    """Credits are billed per market per region either way, so asking for
    both in one round trip is strictly cheaper than two calls."""
    c = _client(lambda url, p: _Resp(_PROPS))
    c._enrich_segments("baseball_mlb", [_ev()], time.time())
    assert len(c.calls) == 1
    asked = c.calls[0][1]
    assert "h2h_1st_5_innings" in asked          # segment
    assert "pitcher_strikeouts" in asked         # prop


def test_a_422_drops_only_what_the_provider_named():
    """Losing props because a SEGMENT key is unsupported would throw away
    the larger half of the universe for an unrelated reason."""
    def handler(url, p):
        return _Resp({"message": "Markets not supported by this endpoint: "
                                 "h2h_1st_5_innings"}, status=422)

    c = _client(handler)
    c._enrich_segments("baseball_mlb", [_ev()], time.time())
    assert "baseball_mlb" in c._no_segments
    assert "baseball_mlb" not in c._no_props     # props survive


def test_props_can_be_switched_off_independently(monkeypatch):
    monkeypatch.setenv("EDGE_PROP_MARKETS", "0")
    c = _client(lambda url, p: _Resp(_F5))
    c._enrich_segments("baseball_mlb", [_ev()], time.time())
    assert "pitcher_strikeouts" not in (c.calls[0][1] or "")
    assert "h2h_1st_5_innings" in c.calls[0][1]


def test_the_player_comes_from_description_not_name():
    """`name` carries Over/Under; the player is in `description`. Reading
    `name` as the player — the obvious mistake, since every other market
    puts the subject there — collapses every player in the game onto one key
    and prices them all off whichever outcome landed last."""
    two_players = {"bookmakers": [{"key": "pinnacle", "markets": [
        {"key": "pitcher_strikeouts", "outcomes": [
            {"name": "Over", "description": "Shota Imanaga", "point": 4.5,
             "price": 1.80},
            {"name": "Under", "description": "Shota Imanaga", "point": 4.5,
             "price": 2.10},
            {"name": "Over", "description": "Will Warren", "point": 5.5,
             "price": 2.00},
            {"name": "Under", "description": "Will Warren", "point": 5.5,
             "price": 1.90}]}]}]}
    c = _client(lambda url, p: _Resp(two_players))
    ev = _ev()
    c._enrich_segments("baseball_mlb", [ev], time.time())
    assert ("pitcher_strikeouts", "shota imanaga", 4.5) in ev.props
    assert ("pitcher_strikeouts", "will warren", 5.5) in ev.props
    assert ev.props[("pitcher_strikeouts", "shota imanaga", 4.5)] == {
        "Over": 1.80, "Under": 2.10}


def test_props_alone_are_enough_to_enrich_an_event():
    """A sport can carry props without carrying segments. Requiring both
    would silently discard the larger half."""
    c = _client(lambda url, p: _Resp(_PROPS))
    ev = _ev()
    assert c._enrich_segments("baseball_mlb", [ev], time.time()) == 1
    assert ev.props and not ev.segments


def test_a_prop_quote_supplies_the_anchor():
    c = _client(lambda url, p: _Resp(_PROPS))
    ev = _ev()
    ev.anchors = 0
    c._enrich_segments("baseball_mlb", [ev], time.time())
    assert ev.anchors == 1
