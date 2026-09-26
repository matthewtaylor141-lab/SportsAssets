"""The venue publishes the market type. We were parsing the slug instead.

THE DEFECT. `pmus.list_desk_events` built market rows as
`{us_slug, label, price, kind}` where `kind` is `ident.split("-", 1)[0]` --
OUR reading of the identifier prefix. The venue's own Sports Schema carries
`sportsMarketTypeV2` and directs consumers away from parsing identifiers,
and the adapter was dropping it. So `period_of_venue_slug` had nothing but
the slug and the title to work from, and a contract whose identifier reads
like a full-game money line while the venue calls it a spread would have
been admitted -- which is precisely the case slug grammar cannot catch.

THE VALUES ARE ONES THIS REPOSITORY HAS OBSERVED in live responses, not
values read off a page: see `research/run85_phase2e.classify`,
`run85_trackbl_block.GAME_TYPES` and the census in
`run85_phase2d_analyze`, which found SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME
carrying the v1 type `soccer_team_full_time_winner`.

ABSENCE IS REPORTED AS ABSENCE. `available: False` is not `None` used as a
value, and the period rule refuses on it under its own name instead of
falling back to the identifier.
"""

import pytest

from sportsassets import bettor_venue_mapping as V
from sportsassets.workers import ext_pinnacle_loop as loop


def _board(rows):
    """A board in the exact shape `pmus.list_desk_events` now builds."""
    return [{"slug": "mlb-nym-wsh-2026-09-26",
             "title": "NYM Mets vs WSH Nationals",
             "league": "mlb", "markets": rows}]


@pytest.fixture(autouse=True)
def _fresh_index():
    loop._TYPE_INDEX["built_from"] = None
    loop._TYPE_INDEX["by_slug"] = {}
    yield
    loop._TYPE_INDEX["built_from"] = None
    loop._TYPE_INDEX["by_slug"] = {}


def _read(monkeypatch, rows, slug):
    from sportsassets import pmus
    monkeypatch.setattr(pmus, "list_desk_events", lambda: _board(rows))
    return loop.venue_market_type(slug)


def test_the_venues_own_type_is_read_off_the_board(monkeypatch):
    got = _read(monkeypatch, [
        {"us_slug": "aec-mlb-nym-wsh-2026-09-26", "kind": "aec",
         "price": 0.5,
         "sports_market_type_v2": "SPORTS_MARKET_TYPE_MONEYLINE",
         "sports_market_type": None,
         "team": "Mets", "team_id": 3011}],
        "aec-mlb-nym-wsh-2026-09-26")
    assert got["available"] is True
    assert got["sports_market_type_v2"] == "SPORTS_MARKET_TYPE_MONEYLINE"
    assert got["team"] == "Mets" and got["team_id"] == 3011
    assert got["source"] == "pmus.list_desk_events"


def test_the_three_way_soccer_type_is_the_observed_one(monkeypatch):
    got = _read(monkeypatch, [
        {"us_slug": "atc-cnl-jam-gtm-2026-09-25-draw", "kind": "atc",
         "price": 0.3,
         "sports_market_type_v2": "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME",
         "sports_market_type": "soccer_team_full_time_winner",
         "team": None, "team_id": None}],
        "atc-cnl-jam-gtm-2026-09-25-draw")
    assert got["sports_market_type_v2"] in V.FULL_MATCH_TYPES_V2
    assert got["sports_market_type"] == "soccer_team_full_time_winner"


def test_a_row_without_the_field_is_unavailable_and_says_which_remedy(
        monkeypatch):
    got = _read(monkeypatch, [
        {"us_slug": "aec-mlb-nym-wsh-2026-09-26", "kind": "aec",
         "price": 0.5, "sports_market_type_v2": None,
         "sports_market_type": None, "team": None, "team_id": None}],
        "aec-mlb-nym-wsh-2026-09-26")
    assert got["available"] is False
    assert got["sports_market_type_v2"] is None
    # the two remedies are different and the message names both
    assert "venue omits it" in got["why"]
    assert "adapter is still dropping it" in got["why"]


def test_a_slug_absent_from_the_board_is_not_a_missing_field(monkeypatch):
    got = _read(monkeypatch, [
        {"us_slug": "aec-mlb-nym-wsh-2026-09-26", "kind": "aec",
         "price": 0.5,
         "sports_market_type_v2": "SPORTS_MARKET_TYPE_MONEYLINE",
         "sports_market_type": None, "team": None, "team_id": None}],
        "aec-mlb-lad-sf-2026-09-26")
    assert got["available"] is False
    assert "no market row for this slug" in got["why"]


def test_a_board_read_that_raises_is_reported_not_swallowed(monkeypatch):
    from sportsassets import pmus

    def _boom():
        raise RuntimeError("venue blind")

    monkeypatch.setattr(pmus, "list_desk_events", _boom)
    got = loop.venue_market_type("aec-mlb-nym-wsh-2026-09-26")
    assert got["available"] is False
    assert got["error"] == "RuntimeError"


def test_the_index_is_rebuilt_when_the_board_moves(monkeypatch):
    from sportsassets import pmus
    a = [{"us_slug": "aec-a-2026-09-26", "kind": "aec", "price": 0.5,
          "sports_market_type_v2": "SPORTS_MARKET_TYPE_MONEYLINE",
          "sports_market_type": None, "team": None, "team_id": None}]
    b = a + [{"us_slug": "aec-b-2026-09-26", "kind": "aec", "price": 0.5,
              "sports_market_type_v2": "SPORTS_MARKET_TYPE_SPREAD",
              "sports_market_type": None, "team": None, "team_id": None}]
    monkeypatch.setattr(pmus, "list_desk_events", lambda: _board(a))
    assert loop.venue_market_type("aec-b-2026-09-26")["available"] is False
    monkeypatch.setattr(pmus, "list_desk_events", lambda: _board(b))
    got = loop.venue_market_type("aec-b-2026-09-26")
    assert got["sports_market_type_v2"] == "SPORTS_MARKET_TYPE_SPREAD"


def test_the_adapter_actually_retains_the_field(monkeypatch):
    """The source of the whole problem, pinned at the adapter.

    `list_desk_events` documented `kind` as the slug-grammar prefix and
    carried nothing else. If a future edit drops the venue's own field
    again, the period rule goes back to refusing everything and this says
    why in one line.
    """
    import inspect

    from sportsassets import pmus
    src = inspect.getsource(pmus.list_desk_events)
    assert "sports_market_type_v2" in src
    # and the builders that fill it
    mod = inspect.getsource(pmus)
    assert mod.count('"sports_market_type_v2":') >= 2
    assert 'm.get("sportsMarketTypeV2")' in mod
