"""A HELD CONTRACT'S EVENT IS GROUPED WHATEVER ITS START (closeout).

Production 2026-10-06 (research p1_xavier_packet_crosstab): 16 of 127 open
PAPER groups -- NFL money lines on games more than 96 h out -- refused every
Xavier review STRUCTURED_PARTICIPANTS_NOT_TWO. The held read grouped the
event's catalogue rows under the census's start window (now-6h, now+96h],
found none, fell back to the contract's own single row, and one team is not
two. The held event read keeps every realism filter and drops only the
window; the census keeps its population."""
from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest

from sportsassets import pinnapi_census as C
from sportsassets import pinnapi_feed_runtime as FR

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


def test_the_census_keeps_its_window_and_the_held_read_drops_only_it():
    assert "96 hours" in C._base_where()
    held = C._base_where(horizon=False)
    assert "interval" not in held
    # every realism filter survives
    for line in C._base_where().splitlines()[2:]:
        assert line.strip() in held
    assert "interval" not in FR.held_event_sql()


@pg
def test_an_nfl_event_eight_days_out_groups_both_teams():
    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            for side, team in (("a", "Dallas Cowboys"),
                               ("b", "Tampa Bay Buccaneers")):
                slug = "aec-nfl-tb-dal-h-%s" % side
                await c.execute(
                    "INSERT INTO us_premap (identifier, event_slug, "
                    " market_slug, kind, side_norm, intent, sports_type, "
                    " listing_state, listing_state_source, updated_at, "
                    " game_start, team_id, team_league, team_name) VALUES "
                    " ($1, 'aec-nfl-tb-dal-h', $1, 'side', $2, 'BUY_LONG', "
                    " 'football_team_full_game_winner', 'PREGAME', "
                    " 'VENUE_LIVE_FLAG', now(), now() + interval '8 days', "
                    " $3, 'nfl', $4)", slug, side, 11 if side == "a" else 12,
                    team)
            census = await c.fetch(
                "SELECT team_name FROM us_premap WHERE event_slug = "
                "'aec-nfl-tb-dal-h' AND " + C._base_where())
            held = await c.fetch(FR.held_event_sql(), "aec-nfl-tb-dal-h")
            return census, held
        finally:
            await tr.rollback()
            await c.close()
    census, held = asyncio.run(go())
    assert census == []                       # outside the census window
    teams, leagues, starts = C.group_event([dict(r) for r in held])
    assert sorted(teams) == ["Dallas Cowboys", "Tampa Bay Buccaneers"]
    assert len(leagues) == 1 and len(starts) == 1
