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


def test_the_lane_cannot_reach_the_type_and_says_so_plainly():
    """THE GUARD IS NOT WIDENED, AND THE BLOCKER IS NAMED.

    `test_ext_shadow_cannot_fund` permits exactly `book_read` and
    `_get_client` off `pmus` in this lane, so it cannot acquire a venue
    capability by accident. An earlier version of `venue_market_type`
    called `pmus.list_desk_events()` -- a cache read, not a venue call --
    and the guard refused it. A control that only holds when the thing it
    blocks looks dangerous is not a control, so the reader reports the
    absence instead.
    """
    got = loop.venue_market_type("aec-mlb-nym-wsh-2026-09-26")
    assert got["available"] is False
    assert got["source"] == "NOT_AVAILABLE_TO_THIS_LANE"
    assert got["sports_market_type_v2"] is None
    assert "book_read" in got["why"] and "_get_client" in got["why"]
    assert "us_premap" in got["remedy"]
    assert "venue-competitions" in got["inspectable_at"]


def test_the_lane_takes_only_the_two_permitted_names_off_pmus():
    """Asserted here too, beside the reader, so the reason travels with it.

    Over the AST, not the text: a regex over source counts the names
    written in COMMENTS, and this function's docstring names
    `list_desk_events` precisely to explain why it is not called.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(loop))
    used = {node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in ("pmus", "_p", "_pmus")}
    assert used <= {"book_read", "_get_client", "_desk_cache"}, sorted(used)
    assert "list_desk_events" not in used


def test_the_period_rule_therefore_refuses_by_that_name():
    """The truthful consequence: scope is unresolved and it says which
    field is missing, rather than falling back to the identifier."""
    got = V.period_of_venue_slug(
        "aec-mlb-nym-wsh-2026-09-26", kind="side",
        event_slug="mlb-nym-wsh-2026-09-26",
        event_title="NYM Mets vs WSH Nationals",
        sports_market_type_v2=None, sports_market_type=None)
    assert got["period"] is None
    assert got["refusals"] == [V.R_TYPE_NOT_RETAINED]


def test_the_adapter_still_retains_the_field_for_inspection():
    """Retained on the board and inspectable, even though the lane may not
    read it -- those are two different facts and both are true."""
    import inspect

    from sportsassets import pmus
    src = inspect.getsource(pmus.list_desk_events)
    assert "sports_market_type_v2" in src
    mod = inspect.getsource(pmus)
    assert mod.count('"sports_market_type_v2":') >= 2
    assert 'm.get("sportsMarketTypeV2")' in mod
