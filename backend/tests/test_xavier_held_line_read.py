"""XAVIER'S HELD LINE POSITION IS RE-MEASURED BY THE ENTRY'S OWN PRICING
(closeout, production 2026-10-06).

About 30 of 127 open PAPER groups -- spreads, totals and team totals entered
by the line-market lane -- refused every review
HELD_VENUE_TYPE_NOT_PROVED_FULL_GAME_MONEYLINE: the held read knew only the
money line, so the packet's probability was never fresh and no management
action could ever be ranked. The held read now re-runs the entry's pricing
on the in-process cache: bettor_market_family.pinnacle_pair at the IDENTICAL
half-point line on the IDENTICAL side (the cache's one read path, the
unchanged 30 s rule) de-vigged by bettor_pinnacle_devig, for the payout
outcome the entry valuation names. ALL PRICES ARE SYNTHETIC (the real
FeedCache, parser and de-vig)."""
from __future__ import annotations

import calendar
import time

from sportsassets import bettor_market_family as MF
from sportsassets import pinnapi_census as C
from sportsassets import pinnapi_feed_runtime as FR
from tests.test_r30a_line_market_family import FID, START, nfl_cache

START_EPOCH = float(calendar.timegm(time.strptime(START,
                                                  "%Y-%m-%dT%H:%M:%SZ")))


def _row(sports_type, *, slug="aec-nfl-det-car-2026-10-04-spread",
         team="Detroit Lions"):
    return {"identifier": slug, "event_slug": "aec-nfl-det-car-2026-10-04",
            "sports_type": sports_type, "team_name": team,
            "team_league": "nfl", "game_start": START_EPOCH}


def _event_rows(row):
    base = {"event_slug": row["event_slug"], "team_league": "nfl",
            "sports_type": "football_team_full_game_winner",
            "game_start": START_EPOCH}
    return [dict(base, team_name="Carolina Panthers"),
            dict(base, team_name="Detroit Lions"), row]


def _q(row, *, payout, line, cache=None, key=None, at=None, **kw):
    cache = cache or nfl_cache()
    args = dict(event_rows=_event_rows(row), payout_event=payout,
                payout_is_complement=False,
                at=time.time() if at is None else at, max_age_s=30.0,
                sport_ids=[5], synced=True,
                view=C.feed_event_view(cache), cache=cache, entry_line=line,
                entry_event_key=key)
    args.update(kw)
    return FR.held_line_quote(row, **args)


def test_a_held_spread_is_priced_on_its_own_line_and_side():
    out = _q(_row("football_team_full_game_spread"),
             payout="Detroit Lions -10.5", line=-10.5)
    assert out["ok"], out
    assert out["market_key"] == "s;0;s;10.5"
    assert out["designation"] == "away"
    assert out["identity_basis"] == FR.IDENTITY_EXACT
    assert out["devig"]["outcomes"] == 2
    # -105 / -105: a fair coin after the vig
    assert abs(out["p"] - 0.5) < 1e-9
    assert out["provenance"].get("change_ms") is not None


def test_the_short_side_is_the_other_outcome_never_a_complement_guess():
    out = _q(_row("football_team_full_game_spread"),
             payout="Carolina Panthers +10.5", line=-10.5)
    assert out["ok"], out
    assert out["designation"] == "home"


def test_a_held_total_and_team_total():
    tot = _q(_row("football_team_full_game_total", team=None),
             payout="Under 22.5", line=22.5)
    assert tot["ok"], tot
    assert tot["market_key"] == "s;0;ou;22.5"
    assert tot["designation"] == "under" and tot["p"] > 0.5
    tt = _q(_row("football_team_points_full_game_total", team=None),
            payout="Carolina Panthers Over 10.5", line=10.5)
    assert tt["ok"], tt
    assert tt["family"] == MF.TEAM_TOTAL


def test_a_neighbouring_line_is_never_used():
    out = _q(_row("football_team_full_game_spread"),
             payout="Detroit Lions -11.5", line=-11.5)
    assert not out["ok"]
    assert out["reason"] == MF.R_LINE_MISMATCH


def test_an_outcome_that_is_not_one_pinnacle_name_refuses():
    out = _q(_row("football_team_full_game_spread"),
             payout="Lions -10.5", line=-10.5)
    assert not out["ok"] and out["reason"] == FR.R_HELD_LINE_OUTCOME


def test_no_entry_line_refuses_by_name():
    out = _q(_row("football_team_full_game_spread"),
             payout="Detroit Lions -10.5", line=None)
    assert not out["ok"]
    assert out["reason"] == FR.R_HELD_LINE_NOT_ENTRY_LINE


def test_a_quiet_line_is_not_fresh_the_30s_rule_is_unchanged():
    cache = nfl_cache(change_age_s=45.0)
    out = _q(_row("football_team_full_game_spread"),
             payout="Detroit Lions -10.5", line=-10.5, cache=cache)
    assert not out["ok"]
    assert out["reason"] not in (None, FR.R_HELD_LINE_OUTCOME)


def test_the_start_must_agree_and_the_entry_fixture_anchors_identity():
    row = dict(_row("football_team_full_game_spread"),
               game_start=START_EPOCH + 6 * 3600)
    late = _q(row, payout="Detroit Lions -10.5", line=-10.5,
              event_rows=[row], key="pinnapi:%s" % FID)
    assert not late["ok"]
    assert late["reason"] == FR.R_HELD_TIME_UNPROVED
    # venue rows that do not group to two teams: the entry's proven fixture
    lone = _row("football_team_full_game_spread")
    anchored = _q(lone, payout="Detroit Lions -10.5", line=-10.5,
                  event_rows=[lone], key="pinnapi:%s" % FID)
    assert anchored["ok"], anchored
    assert anchored["identity_basis"] == FR.IDENTITY_ENTRY_FIXTURE
    unanchored = _q(lone, payout="Detroit Lions -10.5", line=-10.5,
                    event_rows=[lone])
    assert not unanchored["ok"]


def test_a_winner_type_never_enters_the_line_read():
    out = _q(_row("football_team_full_game_winner"),
             payout="Detroit Lions", line=-10.5)
    assert not out["ok"]
    assert out["reason"] == FR.R_HELD_TYPE_UNPROVED
