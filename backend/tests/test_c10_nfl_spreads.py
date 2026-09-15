"""C10 (2026-09-10, coverage lane C10): the NFL spread binds by the venue's
OWN team field -- every line the venue lists, both outcomes (the NO side
a BUY_SHORT), for every NFL game; nothing on cfb changes.

Owner (~02:5xZ): "Also need to make sure future games in nfl map spreads,
alternate spreads, shorts of spreads, etc." C9 (docs section 72) bound
the NFL moneyline and totals and left every spread refused
no_side_match:spread:names-unreadable: his feed says 'Seahawks', the
venue's asc question 'Seattle Seahawks', token-set equality is the bar,
and the C4 chain read the venue's full names as codes. The shadow at
02:31Z (hard2/nflrows_0231.txt table 1, lines 1086-1093): his five
nfl-ne-sea-2026-09-10-spread-home-<L> rows 'unmapped', explained
no_side_match:spread:names-unreadable (3.5, 1.5, re-judged after C9) or
no_key_intersection (2.5, 6.5, 4.5: the 900 s memo not yet re-judged).

THE VENUE'S OWN WORDS (hard2/nflteam_0256_tables.txt, the 'nfl-team'
preset at 02:55:42Z, VERBATIM below): TABLE 1 (lines 4-33) -- the 30
aec-nfl sides of the 15 Sep 10-15 games with the C6 team columns as
migration 055 stores them: side_norm the MASCOT, team_abbr THE SLUG
CODE, team_name the FULL NAME (city + mascot), team_safe_name the CITY
('los angeles c' / 'los angeles r' / 'new york g' / 'new york j' for the
shared cities), team_id, team_league 'nfl', sports_type, game_start, the
question's first 60 characters; TABLE 2 (lines 36-96) -- the 60
asc-nfl full-game 3.5 sides: on EVERY row the 'yes' side (BUY_LONG)
states the question's SUBJECT (slug code a) and the 'no' side
(BUY_SHORT) its opponent (b), the question's first 110 characters;
TABLE 3 (lines 100-129) -- the venue's 30 distinct NFL team records.
This is NOT the cfb shape of docs section 23 (there team_name is the
mascot and safeName the school): C9's TEAM_NE / TEAM_SEA were built in
the cfb shape and marked synthetic; the fixtures here are built in the
REAL shape from table 1 and asserted EQUAL to the tables. The venue's
raw SDK strings are not on file (the sweep stores the fold), so the
fixture's raw values are the tables' words in title case and the STORED
fold is what is asserted. The aec question is COMPLETED past the file's
60-character cut by the venue's attested template (docs section 23's
aec wording) with the clock of the row's own game_start (table 1); the
asc questions past the 110-character cut by section 10.1's template --
every completed string is marked COMPLETED where it is built. The
ne-sea neg / pos rows on every line of hard2/nflrows_0041.txt table 4
(rows 499-570: 0.5, 1.5 .. 10.5, 13.5, 14.5, 16.5, 17.5, 19.5, 20.5,
21.5) carry the team dicts exactly as table 2 states them for 3.5.

HIS ROWS (table 4, lines 133-180, the 48 NFL spread rows over 14 days):
the ne-sea game he is trading now, nfl-ne-sea-2026-09-10-spread-home-
3pt5 'Spread: Seahawks (-3.5)' outcomes Patriots/Seahawks (line 133),
2pt5 (135), 1pt5 (136), 6pt5 (137), 4pt5 (147); the August rows' other
shapes: 'spread-away-<L>' slugs (nfl-tb-jax-2026-08-28-spread-away-3pt5
'Spread: TB (-3.5)' outcome JAX, line 138), ABBREVIATION outcomes and
titles ('Spread: LAC (-3.5)' LA/LAC 134; 'Spread: NE (-1.5)' CLE/NE 140;
'Spread: BAL (-3.5)' BAL/WAS 141; 'Spread: IND (-3.5)' DET/IND 146),
a mascot title beside abbreviation outcomes ('Spread: Seahawks (-1.5)'
outcome Chiefs 158 is mascot/mascot; 'Spread: KC (-1.5)' outcome SEA
178 is abbreviation/abbreviation). Every line is a half line.

THE RULE, EXACTLY (docs/mirror-coverage.md section 74): inside
_c3_pick_spread, when the name step reads nothing and his league token
is literally 'nfl' (cfb keeps the C4 / C6 chain byte for byte),
premap._c10_team_subject certifies both positions from the aec row's
own team field -- a side's POSITION is its team_abbr's index in (a, b),
never its listing order; his outcome / his title's team read a side by
token-set equality with side_norm or team_name, or exact equality with
team_abbr; the question's subject must be the side at a; then C3's sign
table, then premap._c10_side_team holds the chosen asc side to its own
team_abbr; then C3's intent check, line shear and sign shear byte for
byte. matched_by premap_spread_team; every disagreement a named split.
All of it behind PREMAP_YN_IDENTITY with the rest of C3 (off: 0b24564).
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import re
from datetime import datetime, timezone

import pytest

from sportsassets import map_lane, pmus
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import premap
from tests.test_c9_nfl_board import (  # noqa: F401 -- the C9 fixtures this lane extends
    AEC, HIS_DATE, HIS_ML, HIS_TITLE, SIDE_TABLE, SPREAD_LINES, VENUE_DATE, _board, _L, _NflPool,
)
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, N, NOW, _Venue, _armed, _census, _fill, _mkt, _places, _ratio_fills, _shorts_on, _tick,
)

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
LABEL = "premap_spread_team"

# ---------------------------------------------------------------------
# hard2/nflteam_0256_tables.txt TABLE 3 (lines 100-129), verbatim: the
# venue's 30 distinct NFL team records (team_abbr, team_name,
# team_safe_name, team_id, team_league)
TABLE_3 = [
    ("ari", "arizona cardinals", "arizona", 48, "nfl"),
    ("atl", "atlanta falcons", "atlanta", 49, "nfl"),
    ("bal", "baltimore ravens", "baltimore", 50, "nfl"),
    ("buf", "buffalo bills", "buffalo", 51, "nfl"),
    ("car", "carolina panthers", "carolina", 52, "nfl"),
    ("chi", "chicago bears", "chicago", 53, "nfl"),
    ("cin", "cincinnati bengals", "cincinnati", 54, "nfl"),
    ("cle", "cleveland browns", "cleveland", 55, "nfl"),
    ("dal", "dallas cowboys", "dallas", 56, "nfl"),
    ("det", "detroit lions", "detroit", 58, "nfl"),
    ("gb", "green bay packers", "green bay", 59, "nfl"),
    ("hou", "houston texans", "houston", 60, "nfl"),
    ("ind", "indianapolis colts", "indianapolis", 61, "nfl"),
    ("jax", "jacksonville jaguars", "jacksonville", 62, "nfl"),
    ("lac", "los angeles chargers", "los angeles c", 65, "nfl"),
    ("lar", "los angeles rams", "los angeles r", 64, "nfl"),
    ("lv", "las vegas raiders", "las vegas", 66, "nfl"),
    ("mia", "miami dolphins", "miami", 67, "nfl"),
    ("min", "minnesota vikings", "minnesota", 68, "nfl"),
    ("ne", "new england patriots", "new england", 69, "nfl"),
    ("no", "new orleans saints", "new orleans", 70, "nfl"),
    ("nyg", "new york giants", "new york g", 71, "nfl"),
    ("nyj", "new york jets", "new york j", 72, "nfl"),
    ("phi", "philadelphia eagles", "philadelphia", 73, "nfl"),
    ("pit", "pittsburgh steelers", "pittsburgh", 74, "nfl"),
    ("sea", "seattle seahawks", "seattle", 75, "nfl"),
    ("sf", "san francisco 49ers", "san francisco", 76, "nfl"),
    ("tb", "tampa bay buccaneers", "tampa bay", 77, "nfl"),
    ("ten", "tennessee titans", "tennessee", 78, "nfl"),
    ("was", "washington commanders", "washington", 79, "nfl"),
]
assert len(TABLE_3) == 30, "the file's (30 rows)"
RECORD = {abbr: (name, safe, tid) for abbr, name, safe, tid, _lg in TABLE_3}

# TABLE 1 (lines 4-33), verbatim: the 30 aec-nfl sides -- identifier,
# side_norm, team_abbr, team_name, team_safe_name, team_id, team_league,
# sports_type, game_start, the question's first 60 characters
FULL_GAME = "football_team_full_game_winner"
TABLE_1 = [
    ("aec-nfl-ari-lac-2026-09-13", "cardinals", "ari", "arizona cardinals", "arizona", 48, "nfl", FULL_GAME, "2026-09-13 20:25:00+00", "Who will win in the upcoming football event Arizona Cardinal"),
    ("aec-nfl-ari-lac-2026-09-13", "chargers", "lac", "los angeles chargers", "los angeles c", 65, "nfl", FULL_GAME, "2026-09-13 20:25:00+00", "Who will win in the upcoming football event Arizona Cardinal"),
    ("aec-nfl-atl-pit-2026-09-13", "falcons", "atl", "atlanta falcons", "atlanta", 49, "nfl", FULL_GAME, "2026-09-13 17:00:00+00", "Who will win in the upcoming football event Atlanta Falcons "),
    ("aec-nfl-atl-pit-2026-09-13", "steelers", "pit", "pittsburgh steelers", "pittsburgh", 74, "nfl", FULL_GAME, "2026-09-13 17:00:00+00", "Who will win in the upcoming football event Atlanta Falcons "),
    ("aec-nfl-bal-ind-2026-09-13", "colts", "ind", "indianapolis colts", "indianapolis", 61, "nfl", FULL_GAME, "2026-09-13 17:00:00+00", "Who will win in the upcoming football event Baltimore Ravens"),
    ("aec-nfl-bal-ind-2026-09-13", "ravens", "bal", "baltimore ravens", "baltimore", 50, "nfl", FULL_GAME, "2026-09-13 17:00:00+00", "Who will win in the upcoming football event Baltimore Ravens"),
    ("aec-nfl-buf-hou-2026-09-13", "bills", "buf", "buffalo bills", "buffalo", 51, "nfl", FULL_GAME, "2026-09-13 17:00:00+00", "Who will win in the upcoming football event Buffalo Bills vs"),
    ("aec-nfl-buf-hou-2026-09-13", "texans", "hou", "houston texans", "houston", 60, "nfl", FULL_GAME, "2026-09-13 17:00:00+00", "Who will win in the upcoming football event Buffalo Bills vs"),
    ("aec-nfl-chi-car-2026-09-13", "bears", "chi", "chicago bears", "chicago", 53, "nfl", FULL_GAME, "2026-09-13 17:00:00+00", "Who will win in the upcoming football event Chicago Bears vs"),
    ("aec-nfl-chi-car-2026-09-13", "panthers", "car", "carolina panthers", "carolina", 52, "nfl", FULL_GAME, "2026-09-13 17:00:00+00", "Who will win in the upcoming football event Chicago Bears vs"),
    ("aec-nfl-cle-jax-2026-09-13", "browns", "cle", "cleveland browns", "cleveland", 55, "nfl", FULL_GAME, "2026-09-13 17:00:00+00", "Who will win in the upcoming football event Cleveland Browns"),
    ("aec-nfl-cle-jax-2026-09-13", "jaguars", "jax", "jacksonville jaguars", "jacksonville", 62, "nfl", FULL_GAME, "2026-09-13 17:00:00+00", "Who will win in the upcoming football event Cleveland Browns"),
    ("aec-nfl-dal-nyg-2026-09-13", "cowboys", "dal", "dallas cowboys", "dallas", 56, "nfl", FULL_GAME, "2026-09-14 00:20:00+00", "Who will win in the upcoming football event Dallas Cowboys v"),
    ("aec-nfl-dal-nyg-2026-09-13", "giants", "nyg", "new york giants", "new york g", 71, "nfl", FULL_GAME, "2026-09-14 00:20:00+00", "Who will win in the upcoming football event Dallas Cowboys v"),
    ("aec-nfl-gb-min-2026-09-13", "packers", "gb", "green bay packers", "green bay", 59, "nfl", FULL_GAME, "2026-09-13 20:25:00+00", "Who will win in the upcoming football event Green Bay Packer"),
    ("aec-nfl-gb-min-2026-09-13", "vikings", "min", "minnesota vikings", "minnesota", 68, "nfl", FULL_GAME, "2026-09-13 20:25:00+00", "Who will win in the upcoming football event Green Bay Packer"),
    ("aec-nfl-mia-lv-2026-09-13", "dolphins", "mia", "miami dolphins", "miami", 67, "nfl", FULL_GAME, "2026-09-13 20:25:00+00", "Who will win in the upcoming football event Miami Dolphins v"),
    ("aec-nfl-mia-lv-2026-09-13", "raiders", "lv", "las vegas raiders", "las vegas", 66, "nfl", FULL_GAME, "2026-09-13 20:25:00+00", "Who will win in the upcoming football event Miami Dolphins v"),
    ("aec-nfl-ne-sea-2026-09-09", "patriots", "ne", "new england patriots", "new england", 69, "nfl", FULL_GAME, "2026-09-10 00:20:00+00", "Who will win in the upcoming football event New England Patr"),
    ("aec-nfl-ne-sea-2026-09-09", "seahawks", "sea", "seattle seahawks", "seattle", 75, "nfl", FULL_GAME, "2026-09-10 00:20:00+00", "Who will win in the upcoming football event New England Patr"),
    ("aec-nfl-no-det-2026-09-13", "lions", "det", "detroit lions", "detroit", 58, "nfl", FULL_GAME, "2026-09-13 17:00:00+00", "Who will win in the upcoming football event New Orleans Sain"),
    ("aec-nfl-no-det-2026-09-13", "saints", "no", "new orleans saints", "new orleans", 70, "nfl", FULL_GAME, "2026-09-13 17:00:00+00", "Who will win in the upcoming football event New Orleans Sain"),
    ("aec-nfl-nyj-ten-2026-09-13", "jets", "nyj", "new york jets", "new york j", 72, "nfl", FULL_GAME, "2026-09-13 17:00:00+00", "Who will win in the upcoming football event New York Jets vs"),
    ("aec-nfl-nyj-ten-2026-09-13", "titans", "ten", "tennessee titans", "tennessee", 78, "nfl", FULL_GAME, "2026-09-13 17:00:00+00", "Who will win in the upcoming football event New York Jets vs"),
    ("aec-nfl-sf-lar-2026-09-10", "49ers", "sf", "san francisco 49ers", "san francisco", 76, "nfl", FULL_GAME, "2026-09-11 00:35:00+00", "Who will win in the upcoming football event San Francisco 49"),
    ("aec-nfl-sf-lar-2026-09-10", "rams", "lar", "los angeles rams", "los angeles r", 64, "nfl", FULL_GAME, "2026-09-11 00:35:00+00", "Who will win in the upcoming football event San Francisco 49"),
    ("aec-nfl-tb-cin-2026-09-13", "bengals", "cin", "cincinnati bengals", "cincinnati", 54, "nfl", FULL_GAME, "2026-09-13 17:00:00+00", "Who will win in the upcoming football event Tampa Bay Buccan"),
    ("aec-nfl-tb-cin-2026-09-13", "buccaneers", "tb", "tampa bay buccaneers", "tampa bay", 77, "nfl", FULL_GAME, "2026-09-13 17:00:00+00", "Who will win in the upcoming football event Tampa Bay Buccan"),
    ("aec-nfl-was-phi-2026-09-13", "commanders", "was", "washington commanders", "washington", 79, "nfl", FULL_GAME, "2026-09-13 20:25:00+00", "Who will win in the upcoming football event Washington Comma"),
    ("aec-nfl-was-phi-2026-09-13", "eagles", "phi", "philadelphia eagles", "philadelphia", 73, "nfl", FULL_GAME, "2026-09-13 20:25:00+00", "Who will win in the upcoming football event Washington Comma"),
]
assert len(TABLE_1) == 30, "the file's (30 rows)"

# TABLE 2 (lines 36-96), verbatim: the 60 asc-nfl 3.5 sides --
# identifier, side_norm, intent, line, team_abbr, team_name,
# team_safe_name, the question's first 110 characters
TABLE_2 = [
    ("asc-nfl-ari-lac-2026-09-13-neg-3pt5", "no", SHORT, "3.5", "lac", "los angeles chargers", "los angeles c", "Will the Arizona Cardinals cover -3.5 vs the Los Angeles Chargers in Arizona Cardinals vs. Los Angeles Charger"),
    ("asc-nfl-ari-lac-2026-09-13-neg-3pt5", "yes", LONG, "3.5", "ari", "arizona cardinals", "arizona", "Will the Arizona Cardinals cover -3.5 vs the Los Angeles Chargers in Arizona Cardinals vs. Los Angeles Charger"),
    ("asc-nfl-ari-lac-2026-09-13-pos-3pt5", "no", SHORT, "3.5", "lac", "los angeles chargers", "los angeles c", "Will the Arizona Cardinals cover 3.5 vs the Los Angeles Chargers in Arizona Cardinals vs. Los Angeles Chargers"),
    ("asc-nfl-ari-lac-2026-09-13-pos-3pt5", "yes", LONG, "3.5", "ari", "arizona cardinals", "arizona", "Will the Arizona Cardinals cover 3.5 vs the Los Angeles Chargers in Arizona Cardinals vs. Los Angeles Chargers"),
    ("asc-nfl-atl-pit-2026-09-13-neg-3pt5", "no", SHORT, "3.5", "pit", "pittsburgh steelers", "pittsburgh", "Will the Atlanta Falcons cover -3.5 vs the Pittsburgh Steelers in Atlanta Falcons vs. Pittsburgh Steelers?"),
    ("asc-nfl-atl-pit-2026-09-13-neg-3pt5", "yes", LONG, "3.5", "atl", "atlanta falcons", "atlanta", "Will the Atlanta Falcons cover -3.5 vs the Pittsburgh Steelers in Atlanta Falcons vs. Pittsburgh Steelers?"),
    ("asc-nfl-atl-pit-2026-09-13-pos-3pt5", "no", SHORT, "3.5", "pit", "pittsburgh steelers", "pittsburgh", "Will the Atlanta Falcons cover 3.5 vs the Pittsburgh Steelers in Atlanta Falcons vs. Pittsburgh Steelers?"),
    ("asc-nfl-atl-pit-2026-09-13-pos-3pt5", "yes", LONG, "3.5", "atl", "atlanta falcons", "atlanta", "Will the Atlanta Falcons cover 3.5 vs the Pittsburgh Steelers in Atlanta Falcons vs. Pittsburgh Steelers?"),
    ("asc-nfl-bal-ind-2026-09-13-neg-3pt5", "no", SHORT, "3.5", "ind", "indianapolis colts", "indianapolis", "Will the Baltimore Ravens cover -3.5 vs the Indianapolis Colts in Baltimore Ravens vs. Indianapolis Colts?"),
    ("asc-nfl-bal-ind-2026-09-13-neg-3pt5", "yes", LONG, "3.5", "bal", "baltimore ravens", "baltimore", "Will the Baltimore Ravens cover -3.5 vs the Indianapolis Colts in Baltimore Ravens vs. Indianapolis Colts?"),
    ("asc-nfl-bal-ind-2026-09-13-pos-3pt5", "no", SHORT, "3.5", "ind", "indianapolis colts", "indianapolis", "Will the Baltimore Ravens cover 3.5 vs the Indianapolis Colts in Baltimore Ravens vs. Indianapolis Colts?"),
    ("asc-nfl-bal-ind-2026-09-13-pos-3pt5", "yes", LONG, "3.5", "bal", "baltimore ravens", "baltimore", "Will the Baltimore Ravens cover 3.5 vs the Indianapolis Colts in Baltimore Ravens vs. Indianapolis Colts?"),
    ("asc-nfl-buf-hou-2026-09-13-neg-3pt5", "no", SHORT, "3.5", "hou", "houston texans", "houston", "Will the Buffalo Bills cover -3.5 vs the Houston Texans in Buffalo Bills vs. Houston Texans?"),
    ("asc-nfl-buf-hou-2026-09-13-neg-3pt5", "yes", LONG, "3.5", "buf", "buffalo bills", "buffalo", "Will the Buffalo Bills cover -3.5 vs the Houston Texans in Buffalo Bills vs. Houston Texans?"),
    ("asc-nfl-buf-hou-2026-09-13-pos-3pt5", "no", SHORT, "3.5", "hou", "houston texans", "houston", "Will the Buffalo Bills cover 3.5 vs the Houston Texans in Buffalo Bills vs. Houston Texans?"),
    ("asc-nfl-buf-hou-2026-09-13-pos-3pt5", "yes", LONG, "3.5", "buf", "buffalo bills", "buffalo", "Will the Buffalo Bills cover 3.5 vs the Houston Texans in Buffalo Bills vs. Houston Texans?"),
    ("asc-nfl-chi-car-2026-09-13-neg-3pt5", "no", SHORT, "3.5", "car", "carolina panthers", "carolina", "Will the Chicago Bears cover -3.5 vs the Carolina Panthers in Chicago Bears vs. Carolina Panthers?"),
    ("asc-nfl-chi-car-2026-09-13-neg-3pt5", "yes", LONG, "3.5", "chi", "chicago bears", "chicago", "Will the Chicago Bears cover -3.5 vs the Carolina Panthers in Chicago Bears vs. Carolina Panthers?"),
    ("asc-nfl-chi-car-2026-09-13-pos-3pt5", "no", SHORT, "3.5", "car", "carolina panthers", "carolina", "Will the Chicago Bears cover 3.5 vs the Carolina Panthers in Chicago Bears vs. Carolina Panthers?"),
    ("asc-nfl-chi-car-2026-09-13-pos-3pt5", "yes", LONG, "3.5", "chi", "chicago bears", "chicago", "Will the Chicago Bears cover 3.5 vs the Carolina Panthers in Chicago Bears vs. Carolina Panthers?"),
    ("asc-nfl-cle-jax-2026-09-13-neg-3pt5", "no", SHORT, "3.5", "jax", "jacksonville jaguars", "jacksonville", "Will the Cleveland Browns cover -3.5 vs the Jacksonville Jaguars in Cleveland Browns vs. Jacksonville Jaguars?"),
    ("asc-nfl-cle-jax-2026-09-13-neg-3pt5", "yes", LONG, "3.5", "cle", "cleveland browns", "cleveland", "Will the Cleveland Browns cover -3.5 vs the Jacksonville Jaguars in Cleveland Browns vs. Jacksonville Jaguars?"),
    ("asc-nfl-cle-jax-2026-09-13-pos-3pt5", "no", SHORT, "3.5", "jax", "jacksonville jaguars", "jacksonville", "Will the Cleveland Browns cover 3.5 vs the Jacksonville Jaguars in Cleveland Browns vs. Jacksonville Jaguars?"),
    ("asc-nfl-cle-jax-2026-09-13-pos-3pt5", "yes", LONG, "3.5", "cle", "cleveland browns", "cleveland", "Will the Cleveland Browns cover 3.5 vs the Jacksonville Jaguars in Cleveland Browns vs. Jacksonville Jaguars?"),
    ("asc-nfl-dal-nyg-2026-09-13-neg-3pt5", "no", SHORT, "3.5", "nyg", "new york giants", "new york g", "Will the Dallas Cowboys cover -3.5 vs the New York Giants in Dallas Cowboys vs. New York Giants?"),
    ("asc-nfl-dal-nyg-2026-09-13-neg-3pt5", "yes", LONG, "3.5", "dal", "dallas cowboys", "dallas", "Will the Dallas Cowboys cover -3.5 vs the New York Giants in Dallas Cowboys vs. New York Giants?"),
    ("asc-nfl-dal-nyg-2026-09-13-pos-3pt5", "no", SHORT, "3.5", "nyg", "new york giants", "new york g", "Will the Dallas Cowboys cover 3.5 vs the New York Giants in Dallas Cowboys vs. New York Giants?"),
    ("asc-nfl-dal-nyg-2026-09-13-pos-3pt5", "yes", LONG, "3.5", "dal", "dallas cowboys", "dallas", "Will the Dallas Cowboys cover 3.5 vs the New York Giants in Dallas Cowboys vs. New York Giants?"),
    ("asc-nfl-gb-min-2026-09-13-neg-3pt5", "no", SHORT, "3.5", "min", "minnesota vikings", "minnesota", "Will the Green Bay Packers cover -3.5 vs the Minnesota Vikings in Green Bay Packers vs. Minnesota Vikings?"),
    ("asc-nfl-gb-min-2026-09-13-neg-3pt5", "yes", LONG, "3.5", "gb", "green bay packers", "green bay", "Will the Green Bay Packers cover -3.5 vs the Minnesota Vikings in Green Bay Packers vs. Minnesota Vikings?"),
    ("asc-nfl-gb-min-2026-09-13-pos-3pt5", "no", SHORT, "3.5", "min", "minnesota vikings", "minnesota", "Will the Green Bay Packers cover 3.5 vs the Minnesota Vikings in Green Bay Packers vs. Minnesota Vikings?"),
    ("asc-nfl-gb-min-2026-09-13-pos-3pt5", "yes", LONG, "3.5", "gb", "green bay packers", "green bay", "Will the Green Bay Packers cover 3.5 vs the Minnesota Vikings in Green Bay Packers vs. Minnesota Vikings?"),
    ("asc-nfl-mia-lv-2026-09-13-neg-3pt5", "no", SHORT, "3.5", "lv", "las vegas raiders", "las vegas", "Will the Miami Dolphins cover -3.5 vs the Las Vegas Raiders in Miami Dolphins vs. Las Vegas Raiders?"),
    ("asc-nfl-mia-lv-2026-09-13-neg-3pt5", "yes", LONG, "3.5", "mia", "miami dolphins", "miami", "Will the Miami Dolphins cover -3.5 vs the Las Vegas Raiders in Miami Dolphins vs. Las Vegas Raiders?"),
    ("asc-nfl-mia-lv-2026-09-13-pos-3pt5", "no", SHORT, "3.5", "lv", "las vegas raiders", "las vegas", "Will the Miami Dolphins cover 3.5 vs the Las Vegas Raiders in Miami Dolphins vs. Las Vegas Raiders?"),
    ("asc-nfl-mia-lv-2026-09-13-pos-3pt5", "yes", LONG, "3.5", "mia", "miami dolphins", "miami", "Will the Miami Dolphins cover 3.5 vs the Las Vegas Raiders in Miami Dolphins vs. Las Vegas Raiders?"),
    ("asc-nfl-ne-sea-2026-09-09-neg-3pt5", "no", SHORT, "3.5", "sea", "seattle seahawks", "seattle", "Will the New England Patriots cover -3.5 vs the Seattle Seahawks in New England Patriots vs. Seattle Seahawks?"),
    ("asc-nfl-ne-sea-2026-09-09-neg-3pt5", "yes", LONG, "3.5", "ne", "new england patriots", "new england", "Will the New England Patriots cover -3.5 vs the Seattle Seahawks in New England Patriots vs. Seattle Seahawks?"),
    ("asc-nfl-ne-sea-2026-09-09-pos-3pt5", "no", SHORT, "3.5", "sea", "seattle seahawks", "seattle", "Will the New England Patriots cover 3.5 vs the Seattle Seahawks in New England Patriots vs. Seattle Seahawks?"),
    ("asc-nfl-ne-sea-2026-09-09-pos-3pt5", "yes", LONG, "3.5", "ne", "new england patriots", "new england", "Will the New England Patriots cover 3.5 vs the Seattle Seahawks in New England Patriots vs. Seattle Seahawks?"),
    ("asc-nfl-no-det-2026-09-13-neg-3pt5", "no", SHORT, "3.5", "det", "detroit lions", "detroit", "Will the New Orleans Saints cover -3.5 vs the Detroit Lions in New Orleans Saints vs. Detroit Lions?"),
    ("asc-nfl-no-det-2026-09-13-neg-3pt5", "yes", LONG, "3.5", "no", "new orleans saints", "new orleans", "Will the New Orleans Saints cover -3.5 vs the Detroit Lions in New Orleans Saints vs. Detroit Lions?"),
    ("asc-nfl-no-det-2026-09-13-pos-3pt5", "no", SHORT, "3.5", "det", "detroit lions", "detroit", "Will the New Orleans Saints cover 3.5 vs the Detroit Lions in New Orleans Saints vs. Detroit Lions?"),
    ("asc-nfl-no-det-2026-09-13-pos-3pt5", "yes", LONG, "3.5", "no", "new orleans saints", "new orleans", "Will the New Orleans Saints cover 3.5 vs the Detroit Lions in New Orleans Saints vs. Detroit Lions?"),
    ("asc-nfl-nyj-ten-2026-09-13-neg-3pt5", "no", SHORT, "3.5", "ten", "tennessee titans", "tennessee", "Will the New York Jets cover -3.5 vs the Tennessee Titans in New York Jets vs. Tennessee Titans?"),
    ("asc-nfl-nyj-ten-2026-09-13-neg-3pt5", "yes", LONG, "3.5", "nyj", "new york jets", "new york j", "Will the New York Jets cover -3.5 vs the Tennessee Titans in New York Jets vs. Tennessee Titans?"),
    ("asc-nfl-nyj-ten-2026-09-13-pos-3pt5", "no", SHORT, "3.5", "ten", "tennessee titans", "tennessee", "Will the New York Jets cover 3.5 vs the Tennessee Titans in New York Jets vs. Tennessee Titans?"),
    ("asc-nfl-nyj-ten-2026-09-13-pos-3pt5", "yes", LONG, "3.5", "nyj", "new york jets", "new york j", "Will the New York Jets cover 3.5 vs the Tennessee Titans in New York Jets vs. Tennessee Titans?"),
    ("asc-nfl-sf-lar-2026-09-10-neg-3pt5", "no", SHORT, "3.5", "lar", "los angeles rams", "los angeles r", "Will the San Francisco 49ers cover -3.5 vs the Los Angeles Rams in San Francisco 49ers vs. Los Angeles Rams?"),
    ("asc-nfl-sf-lar-2026-09-10-neg-3pt5", "yes", LONG, "3.5", "sf", "san francisco 49ers", "san francisco", "Will the San Francisco 49ers cover -3.5 vs the Los Angeles Rams in San Francisco 49ers vs. Los Angeles Rams?"),
    ("asc-nfl-sf-lar-2026-09-10-pos-3pt5", "no", SHORT, "3.5", "lar", "los angeles rams", "los angeles r", "Will the San Francisco 49ers cover 3.5 vs the Los Angeles Rams in San Francisco 49ers vs. Los Angeles Rams?"),
    ("asc-nfl-sf-lar-2026-09-10-pos-3pt5", "yes", LONG, "3.5", "sf", "san francisco 49ers", "san francisco", "Will the San Francisco 49ers cover 3.5 vs the Los Angeles Rams in San Francisco 49ers vs. Los Angeles Rams?"),
    ("asc-nfl-tb-cin-2026-09-13-neg-3pt5", "no", SHORT, "3.5", "cin", "cincinnati bengals", "cincinnati", "Will the Tampa Bay Buccaneers cover -3.5 vs the Cincinnati Bengals in Tampa Bay Buccaneers vs. Cincinnati Beng"),
    ("asc-nfl-tb-cin-2026-09-13-neg-3pt5", "yes", LONG, "3.5", "tb", "tampa bay buccaneers", "tampa bay", "Will the Tampa Bay Buccaneers cover -3.5 vs the Cincinnati Bengals in Tampa Bay Buccaneers vs. Cincinnati Beng"),
    ("asc-nfl-tb-cin-2026-09-13-pos-3pt5", "no", SHORT, "3.5", "cin", "cincinnati bengals", "cincinnati", "Will the Tampa Bay Buccaneers cover 3.5 vs the Cincinnati Bengals in Tampa Bay Buccaneers vs. Cincinnati Benga"),
    ("asc-nfl-tb-cin-2026-09-13-pos-3pt5", "yes", LONG, "3.5", "tb", "tampa bay buccaneers", "tampa bay", "Will the Tampa Bay Buccaneers cover 3.5 vs the Cincinnati Bengals in Tampa Bay Buccaneers vs. Cincinnati Benga"),
    ("asc-nfl-was-phi-2026-09-13-neg-3pt5", "no", SHORT, "3.5", "phi", "philadelphia eagles", "philadelphia", "Will the Washington Commanders cover -3.5 vs the Philadelphia Eagles in Washington Commanders vs. Philadelphia"),
    ("asc-nfl-was-phi-2026-09-13-neg-3pt5", "yes", LONG, "3.5", "was", "washington commanders", "washington", "Will the Washington Commanders cover -3.5 vs the Philadelphia Eagles in Washington Commanders vs. Philadelphia"),
    ("asc-nfl-was-phi-2026-09-13-pos-3pt5", "no", SHORT, "3.5", "phi", "philadelphia eagles", "philadelphia", "Will the Washington Commanders cover 3.5 vs the Philadelphia Eagles in Washington Commanders vs. Philadelphia "),
    ("asc-nfl-was-phi-2026-09-13-pos-3pt5", "yes", LONG, "3.5", "was", "washington commanders", "washington", "Will the Washington Commanders cover 3.5 vs the Philadelphia Eagles in Washington Commanders vs. Philadelphia "),
]
assert len(TABLE_2) == 60, "the file's (60 rows)"

# the 15 games of table 1: aec identifier -> (a, b, game_start)
GAMES: dict[str, tuple[str, str, str]] = {}
for _ident, _sn, _abbr, _name, _safe, _tid, _lg, _st, _gs, _q in TABLE_1:
    _m = re.match(r"^aec-nfl-([a-z0-9]+)-([a-z0-9]+)-(\d{4}-\d{2}-\d{2})$", _ident)
    GAMES[_ident] = (_m.group(1), _m.group(2), _gs)
    assert _abbr in (_m.group(1), _m.group(2)) and RECORD[_abbr] == (_name, _safe, _tid)
assert len(GAMES) == 15, "the 15 Sep 10-15 games"
# the venue's side description (the mascot) per team: table 1's side_norm
MASCOT = {abbr: sn for _i, sn, abbr, *_rest in TABLE_1}
assert len(MASCOT) == 30


def _title(s: str) -> str:
    """The fixture's raw SDK string for a stored fold (the raw strings
    are not on file; the stored fold is what the tests assert)."""
    return " ".join(w if w[0].isdigit() else w[0].upper() + w[1:] for w in s.split())


def _team(abbr: str) -> dict:
    """The SDK `team` dict of one venue record in the REAL nfl shape
    (table 3): abbreviation = the slug code, name = the FULL NAME (city
    + mascot), safeName = the city, id, league."""
    name, safe, tid = RECORD[abbr]
    return {"abbreviation": abbr, "name": _title(name), "safeName": _title(safe), "id": tid, "league": "nfl"}


def _side(ident, desc, long, team):
    return {"identifier": ident, "description": desc, "long": long, "team": team,
            "teamId": team.get("id") if isinstance(team, dict) else None}


def _clock(game_start: str) -> str:
    d = datetime.strptime(game_start, "%Y-%m-%d %H:%M:%S+00").replace(tzinfo=timezone.utc)
    h = d.hour % 12 or 12
    return f"{d.strftime('%B')} {d.day}, {d.year} at {h}:{d.strftime('%M')} {'AM' if d.hour < 12 else 'PM'} UTC"


def _aec_q(a: str, b: str, game_start: str) -> str:
    # COMPLETED past the file's 60-character cut by the venue's attested
    # template (docs section 23's aec wording) with the row's own game_start
    return (f"Who will win in the upcoming football event {_title(RECORD[a][0])} vs {_title(RECORD[b][0])} "
            f"scheduled for {_clock(game_start)}?")


def _asc_q(a: str, b: str, sign: str, L: str) -> str:
    # COMPLETED past the file's 110-character cut by section 10.1's template
    A, B = _title(RECORD[a][0]), _title(RECORD[b][0])
    return f"Will the {A} cover {sign}{L} vs the {B} in {A} vs. {B}?"


ABSENT = "absent"      # the sentinel for 'this side states no team dict'


def _aec_market(ident, ta=None, tb=None, descs=None, q=None):
    a, b, gs = GAMES[ident]
    ta = _team(a) if ta is None else (None if ta == ABSENT else ta)
    tb = _team(b) if tb is None else (None if tb == ABSENT else tb)
    da, db = descs or (_title(MASCOT[a]), _title(MASCOT[b]))
    return {"slug": ident, "question": q or _aec_q(a, b, gs), "closed": False,
            "gameStartTime": gs.replace(" ", "T").replace("+00", "Z"), "sportsMarketType": FULL_GAME,
            "marketSides": [_side(ident, da, True, ta), _side(ident, db, False, tb)]}


def _asc_market(ident, a, b, sg, L, ta=None, tb=None, q=None):
    # the yes side (the venue's long marker) states the SUBJECT's team (a),
    # the no side its opponent's (b) -- table 2, every row
    ta = _team(a) if ta is None else ta
    tb = _team(b) if tb is None else tb
    return {"slug": ident, "question": q or _asc_q(a, b, "-" if sg == "neg" else "", L), "closed": False,
            "marketSides": [_side(ident, L, True, ta), _side(ident, L, False, tb)]}


def _game_markets(ident, lines=("3pt5",), aec=True, aec_kw=None):
    a, b, _gs = GAMES[ident]
    head = ident[len("aec-"):]
    out = []
    if aec:
        out.append(_aec_market(ident, **(aec_kw or {})))
    for ln in lines:
        for sg in ("neg", "pos"):
            out.append(_asc_market(f"asc-{head}-{sg}-{ln}", a, b, sg, _L(ln)))
    return out


def _ne_sea(lines=SPREAD_LINES, aec=True, aec_kw=None):
    """The ne-sea event: the aec row (table 1 lines 22-23) and the neg /
    pos rows on every line of nflrows_0041 table 4 with the team dicts
    exactly as table 2 states them for 3.5."""
    return _game_markets(AEC, lines=lines, aec=aec, aec_kw=aec_kw)


def _whole_board():
    """All 15 games: the aec row and the 3.5 neg / pos rows (tables 1, 2)."""
    out = []
    for ident in GAMES:
        out.extend(_game_markets(ident))
    return out


# the SELECT the two resolvers compose (premap.resolve / resolve_explain):
# the fixed columns, TEAM_SELECT_COLS when the C6 columns are present
BASE_COLS = ("identifier", "side_norm", "kind", "line", "question", "event_title",
             "intent", "signed", "event_slug", "market_slug")
TEAM_COLS = tuple(c.strip() for c in premap.TEAM_SELECT_COLS.split(",") if c.strip())


class _Pool:
    """us_premap by event keys, projected to exactly the columns the
    resolvers SELECT (the seven C6 columns of migration 055 PRESENT:
    information_schema answers 7, the shape the live database has --
    the C9 fixture's 0 is 'the columns absent'); the grammar class's
    state key absent; the probe read answered by the identifiers."""

    def __init__(self, rows, cols=len(premap._TEAM_COLUMNS)):
        self.rows, self.cols = rows, cols
        premap._TEAM_COLS_STATE.update(present=None, at=0.0)

    def _project(self, r):
        keep = BASE_COLS + (TEAM_COLS if self.cols == len(premap._TEAM_COLUMNS) else ())
        return {k: r.get(k) for k in keep}

    async def fetch(self, sql, *a):
        if "identifier ~ $1::text" in sql:
            return [{"identifier": r["identifier"]} for r in self.rows if re.search(a[0], r["identifier"])]
        if "us_premap" in sql and "event_keys &&" in sql:
            k = set(a[0])
            for col in (TEAM_COLS if self.cols == len(premap._TEAM_COLUMNS) else ()):
                assert col in sql, col
            return [self._project(r) for r in self.rows if set(r["event_keys"]) & k]
        return []

    async def fetchval(self, sql, *a):
        if "information_schema" in sql:
            return self.cols
        return None


def _resolve(rows, slug, title, outcome, event_title=None, cols=7):
    return asyncio.run(premap.resolve(_Pool(rows, cols), title, event_title, outcome, slug))


def _explain(rows, slug, title, outcome, event_title=None, cols=7):
    return asyncio.run(premap.resolve_explain(_Pool(rows, cols), title, event_title, outcome, slug))


def _short(h):
    return None if not h else (h["market_slug"], h["outcome"], h["intent"], h["matched_by"])


def _pick(rows, slug, title, outcome, event_title=None):
    """The pure pick on the venue's own identifiers (his slug already
    dated as the venue dates it), the trace returned beside the hit."""
    tr: dict = {}
    hit = premap.c3_pick([r for r in rows if r["identifier"].startswith("asc-")], outcome, title, slug, tr,
                         board=rows, cert={}, his_event_title=event_title)
    return hit, tr


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.delenv(premap.PREMAP_NFL_DATE_TOL_ENV, raising=False)
    monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)
    premap._TEAM_COLS_STATE.update(present=None, at=0.0)
    yield
    premap._TEAM_COLS_STATE.update(present=None, at=0.0)


# ------------------------------------------- (A) the venue's own words

class TestTheVenuesOwnWords:
    def test_c10_the_aec_rows_equal_table_1(self):
        rows = _board([_aec_market(i) for i in GAMES])
        built = sorted((r["identifier"], r["side_norm"], r["team_abbr"], r["team_name"], r["team_safe_name"],
                        r["team_id"], r["team_league"], r["sports_type"],
                        r["game_start"].strftime("%Y-%m-%d %H:%M:%S+00"), r["question"][:60]) for r in rows)
        assert built == sorted(TABLE_1)
        for r in rows:
            assert r["game_start"].tzinfo is not None and r["intent"] in (LONG, SHORT)

    def test_c10_the_asc_rows_equal_table_2(self):
        rows = _board(_whole_board())
        asc = [r for r in rows if r["identifier"].startswith("asc-")]
        built = sorted((r["identifier"], r["side_norm"], r["intent"], r["line"], r["team_abbr"], r["team_name"],
                        r["team_safe_name"], r["question"][:110]) for r in asc)
        assert built == sorted(TABLE_2)
        # every yes side states the subject's code (a), every no side the opponent's (b)
        for r in asc:
            a, b, _gs = GAMES["aec-" + r["identifier"][len("asc-"):].rsplit("-", 2)[0]]
            assert r["team_abbr"] == (a if r["side_norm"] == "yes" else b), r["identifier"]
            assert r["team_league"] == "nfl" and r["team_id"] == RECORD[r["team_abbr"]][2]

    def test_c10_the_team_records_equal_table_3_and_the_shape_is_not_cfbs(self):
        rows = _board(_whole_board())
        recs = sorted({(r["team_abbr"], r["team_name"], r["team_safe_name"], r["team_id"], r["team_league"])
                       for r in rows if r["team_abbr"]})
        assert recs == sorted(TABLE_3)
        # the nfl shape: team_name is the FULL name and the side's description
        # is its tail (the mascot); the city is never a name of his
        for r in rows:
            if r["identifier"].startswith("aec-"):
                assert r["team_name"].split()[-len(r["side_norm"].split()):] == r["side_norm"].split()
                assert r["team_name"] != r["side_norm"] and not pmus._yn_name_match(r["team_safe_name"], r["team_name"])
        # the ne-sea ladder carries every line of nflrows_0041 table 4
        ne = _board(_ne_sea())
        assert sorted({r["identifier"] for r in ne if r["identifier"].startswith("asc-")}) == sorted(
            f"asc-nfl-ne-sea-{VENUE_DATE}-{sg}-{ln}" for sg in ("neg", "pos") for ln in SPREAD_LINES)
        assert len(ne) == 2 + 2 * 2 * len(SPREAD_LINES) == 74

    def test_c10_the_resolvers_select_carries_the_team_columns_but_not_team_id(self):
        assert TEAM_COLS == ("team_abbr", "team_safe_name", "team_name", "team_league", "game_start", "sports_type")
        assert "team_id" not in premap.TEAM_SELECT_COLS
        src = inspect.getsource(premap._c10_team_subject) + inspect.getsource(premap._c10_side_team)
        assert "team_id" not in src and "team_safe_name" not in src.replace("(team_safe_name)", "")


# ------------------------------------------------ (B) THE SIDE TABLE

class TestTheSideTable:
    def test_c10_his_five_ne_sea_rows_bind_both_outcomes(self, armed):
        rows = _board(_ne_sea())
        for (tail, outcome), (ident, side, intent) in SIDE_TABLE.items():
            slug = f"{HIS_ML}-{tail}"
            L = _L(tail.rsplit("-", 1)[-1])
            title = f"Spread: Seahawks (-{L})"
            for ev in (None, HIS_TITLE):
                hit = _resolve(rows, slug, title, outcome, ev)
                assert _short(hit) == (ident, side, intent, LABEL), (tail, outcome, ev)
                assert hit["date_shift"] == {"his": HIS_DATE, "venue": VENUE_DATE}
                assert "-neg-" not in hit["market_slug"]
                ex = _explain(rows, slug, title, outcome, ev)
                assert ex["step"] == "resolves" and ex["matched_by"] == LABEL, ex
                c3 = ex["c3"]
                assert c3["subject"] == {"certified": "patriots", "code": "ne", "via": "team"}
                assert c3["subject_via"] == "team" and c3["admitted"] == ident and c3["side"] == side
                assert c3["team"] == {"a": {"abbr": "ne", "side_norm": "patriots", "team_name": "new england patriots"},
                                      "b": {"abbr": "sea", "side_norm": "seahawks", "team_name": "seattle seahawks"}}
                assert c3["side_team"] == ("sea" if outcome == "Seahawks" else "ne")
                assert c3["his_team"] == ("b" if outcome == "Seahawks" else "a")
                assert c3["his_signed"] == (("-" if outcome == "Seahawks" else "+") + L)
                assert c3["venue_names"] == ["new england patriots", "seattle seahawks"]
                assert "code_hits" not in c3 and "code_school" not in c3
        # the pure pick, on the venue's own identifiers, the same verdict
        hit, tr = _pick(rows, f"nfl-ne-sea-{VENUE_DATE}-spread-home-3pt5", "Spread: Seahawks (-3.5)", "Seahawks")
        assert hit["identifier"] == f"asc-nfl-ne-sea-{VENUE_DATE}-pos-3pt5" and hit["side_norm"] == "no"
        assert hit["intent"] == SHORT and hit["matched_by"] == LABEL and tr["side_team"] == "sea"

    def test_c10_every_line_the_venue_lists_binds_and_an_unlisted_one_refuses_line_absent(self, armed):
        rows = _board(_ne_sea())
        for ln in SPREAD_LINES:
            L = _L(ln)
            want = f"asc-nfl-ne-sea-{VENUE_DATE}-pos-{ln}"
            assert _short(_resolve(rows, f"{HIS_ML}-spread-home-{ln}", f"Spread: Seahawks (-{L})", "Seahawks")) == (want, "no", SHORT, LABEL), ln
            assert _short(_resolve(rows, f"{HIS_ML}-spread-home-{ln}", f"Spread: Seahawks (-{L})", "Patriots")) == (want, "yes", LONG, LABEL), ln
        # a line the venue does not list (11.5, 12.5, 15.5, 18.5 are not on
        # table 4's ladder): C3's own refusal as today, the shift on the trace
        for ln in ("11pt5", "12pt5", "15pt5", "18pt5"):
            L = _L(ln)
            assert _resolve(rows, f"{HIS_ML}-spread-home-{ln}", f"Spread: Seahawks (-{L})", "Seahawks") is None
            ex = _explain(rows, f"{HIS_ML}-spread-home-{ln}", f"Spread: Seahawks (-{L})", "Seahawks")
            assert (ex["step"], ex["split"]) == ("no_side_match", "spread:line-absent"), ln
            assert ex["date_shift"] == {"his": HIS_DATE, "venue": VENUE_DATE}
        # a whole-number line stays C3's own refusal (the venue lists none)
        ex = _explain(rows, f"{HIS_ML}-spread-home-3", "Spread: Seahawks (-3)", "Seahawks")
        assert (ex["step"], ex["split"]) == ("no_side_match", "spread:whole-number")
        # the title's line must be his slug's (C3's shear, unchanged)
        ex = _explain(rows, f"{HIS_ML}-spread-home-3pt5", "Spread: Seahawks (-2.5)", "Seahawks")
        assert (ex["step"], ex["split"]) == ("no_side_match", "spread:title-shear")

    def test_c10_the_abbreviation_outcome_and_title_bind_by_team_abbr(self, armed):
        # his feed's August shape ('Spread: NE (-1.5)' outcomes CLE/NE, table
        # 4 line 140; 'Spread: KC (-1.5)' outcome SEA, line 178) on the ne-sea
        # rows: his abbreviation equals the venue's team_abbr, lower-cased,
        # exactly -- never a prefix
        rows = _board(_ne_sea())
        slug = f"{HIS_ML}-spread-home-3pt5"
        want = f"asc-nfl-ne-sea-{VENUE_DATE}-pos-3pt5"
        assert _short(_resolve(rows, slug, "Spread: SEA (-3.5)", "SEA")) == (want, "no", SHORT, LABEL)
        assert _short(_resolve(rows, slug, "Spread: SEA (-3.5)", "NE")) == (want, "yes", LONG, LABEL)
        # mixed: a mascot title beside an abbreviation outcome, and the reverse
        assert _short(_resolve(rows, slug, "Spread: Seahawks (-3.5)", "NE")) == (want, "yes", LONG, LABEL)
        assert _short(_resolve(rows, slug, "Spread: SEA (-3.5)", "Patriots")) == (want, "yes", LONG, LABEL)
        # the title naming his own team: the same row, the other side of the
        # sign (his 'Spread: Patriots (-3.5)' / 'Patriots' is the neg-3pt5 YES)
        neg = f"asc-nfl-ne-sea-{VENUE_DATE}-neg-3pt5"
        assert _short(_resolve(rows, slug, "Spread: Patriots (-3.5)", "Patriots")) == (neg, "yes", LONG, LABEL)
        assert _short(_resolve(rows, slug, "Spread: NE (-3.5)", "Seahawks")) == (neg, "no", SHORT, LABEL)
        # the full name as his outcome is C3's own NAME path (the question
        # names it): with a full-name title it binds as premap_spread, with a
        # mascot title it refuses spread:title-unreadable as today -- the
        # team reader runs only when the name step reads nothing (his feed
        # never writes the full name: table 4)
        assert _short(_resolve(rows, slug, "Spread: Seattle Seahawks (-3.5)", "Seattle Seahawks")) == (want, "no", SHORT, "premap_spread")
        assert _explain(rows, slug, "Spread: Seahawks (-3.5)", "Seattle Seahawks")["split"] == "spread:title-unreadable"

    def test_c10_the_spread_away_slug_binds_the_same_way_the_sign_comes_from_the_title(self, armed):
        # his 'spread-away-<L>' slugs (table 4 lines 138-140, 148, 156, 158-160,
        # 163, 166, 174-175, 179): the feed's home/away token is never read as
        # a position (c3_his: "never read as a position -- his team is his
        # outcome"); the sign is the TITLE's
        rows = _board(_ne_sea())
        for shape in ("home", "away"):
            slug = f"{HIS_ML}-spread-{shape}-3pt5"
            assert _short(_resolve(rows, slug, "Spread: Seahawks (-3.5)", "Seahawks")) == (
                f"asc-nfl-ne-sea-{VENUE_DATE}-pos-3pt5", "no", SHORT, LABEL), shape
            assert _short(_resolve(rows, slug, "Spread: Seahawks (-3.5)", "Patriots")) == (
                f"asc-nfl-ne-sea-{VENUE_DATE}-pos-3pt5", "yes", LONG, LABEL), shape
            assert _short(_resolve(rows, slug, "Spread: Patriots (-3.5)", "Patriots")) == (
                f"asc-nfl-ne-sea-{VENUE_DATE}-neg-3pt5", "yes", LONG, LABEL), shape
        assert premap.c3_his(f"{HIS_ML}-spread-away-3pt5")["side"] == "away"
        assert '"side"' not in inspect.getsource(premap._c10_team_subject)

    def test_c10_the_no_side_is_buy_short_the_shorts_machinerys_intent(self, armed):
        assert premap._YN_IDENTITY_INTENT == {"yes": LONG, "no": SHORT}
        rows = _board(_ne_sea())
        hit = _resolve(rows, f"{HIS_ML}-spread-home-3pt5", "Spread: Seahawks (-3.5)", "Seahawks")
        assert hit["intent"] == SHORT == ml.ORDER_INTENT_SHORT and hit["outcome"] == "no"
        # the mapper's step 2 (docs section 7.1): source 'premap', his
        # Seahawks token the other (short-side) asset, his Patriots the long
        fills = [{"asset": "tok-sea", "market_slug": f"{HIS_ML}-spread-home-3pt5", "market_title": "Spread: Seahawks (-3.5)",
                  "event_title": None, "outcome": "Seahawks", "outcome_index": 1, "side": "BUY", "size": 10.0, "price": 0.5},
                 {"asset": "tok-ne", "market_slug": f"{HIS_ML}-spread-home-3pt5", "market_title": "Spread: Seahawks (-3.5)",
                  "event_title": None, "outcome": "Patriots", "outcome_index": 0, "side": "BUY", "size": 1.0, "price": 0.5}]
        m = asyncio.run(ms.map_market(_Pool(rows), fills))
        assert m == {"us_slug": f"asc-nfl-ne-sea-{VENUE_DATE}-pos-3pt5", "long_asset": "tok-ne", "other_asset": "tok-sea",
                     "source": "premap"}


# ------------------------------------------------ (C) THE BOARD TABLE

def _board_table():
    """For each of the 15 games on the 3.5 line, both signs of a SYNTHETIC
    title ('Spread: <b mascot> (-3.5)' and 'Spread: <a mascot> (-3.5)',
    the venue's rows verbatim) and both mascot outcomes: the identifier and
    side C3's table names -- b with -L -> pos-L no; a with +L -> pos-L
    yes; a with -L -> neg-L yes; b with +L -> neg-L no."""
    out = []
    for ident, (a, b, _gs) in GAMES.items():
        head = ident[len("aec-"):]
        ma, mb = _title(MASCOT[a]), _title(MASCOT[b])
        for titled, cells in ((mb, ((mb, "pos", "no", SHORT, b), (ma, "pos", "yes", LONG, a))),
                              (ma, ((ma, "neg", "yes", LONG, a), (mb, "neg", "no", SHORT, b)))):
            for outcome, sg, side, intent, code in cells:
                out.append((head, f"Spread: {titled} (-3.5)", outcome, f"asc-{head}-{sg}-3pt5", side, intent, code))
    return out


BOARD_TABLE = _board_table()
assert len(BOARD_TABLE) == 60


class TestTheBoardTable:
    def test_c10_every_game_of_the_sunday_board_binds_on_the_four_cells(self, armed):
        rows = _board(_whole_board())
        for head, title, outcome, ident, side, intent, code in BOARD_TABLE:
            a, b, date = head.split("-")[1], head.split("-")[2], head.split("-", 3)[3]
            # his slug on his own feed's date (the venue's date on 14 of the
            # 15 games; ne-sea and sf-lar read one day off, C9's shift)
            his_date = HIS_DATE if head == f"nfl-ne-sea-{VENUE_DATE}" else date
            slug = f"nfl-{a}-{b}-{his_date}-spread-home-3pt5"
            hit = _resolve(rows, slug, title, outcome)
            assert _short(hit) == (ident, side, intent, LABEL), (head, title, outcome)
            ex = _explain(rows, slug, title, outcome)
            assert ex["step"] == "resolves" and ex["c3"]["side_team"] == code, (head, title, outcome)
            assert ex["c3"]["subject"] == {"certified": MASCOT[a], "code": a, "via": "team"}
            # the asc side witness agrees: the chosen row's own team_abbr is his code
            row = next(r for r in rows if r["identifier"] == ident and r["side_norm"] == side)
            assert row["team_abbr"] == code and row["intent"] == intent

    def test_c10_the_abbreviation_outcome_on_a_sunday_game(self, armed):
        # 'TB' on tb-cin (his August 'Spread: TB (-3.5)' shape, table 4 line 138)
        rows = _board(_whole_board())
        slug = "nfl-tb-cin-2026-09-13-spread-home-3pt5"
        assert _short(_resolve(rows, slug, "Spread: CIN (-3.5)", "TB")) == ("asc-nfl-tb-cin-2026-09-13-pos-3pt5", "yes", LONG, LABEL)
        assert _short(_resolve(rows, slug, "Spread: CIN (-3.5)", "CIN")) == ("asc-nfl-tb-cin-2026-09-13-pos-3pt5", "no", SHORT, LABEL)
        assert _short(_resolve(rows, slug, "Spread: TB (-3.5)", "JAX")) is None       # not this game's team
        assert _explain(rows, slug, "Spread: TB (-3.5)", "JAX")["split"] == "spread:team-outcome-unread"
        # the shared-city records: 'Chargers' / 'LAC' / 'Los Angeles Chargers'
        # bind ari-lac; 'Los Angeles C' (the city, team_safe_name) never does
        slug = "nfl-ari-lac-2026-09-13-spread-home-3pt5"
        for on in ("Chargers", "LAC"):
            assert _short(_resolve(rows, slug, "Spread: Cardinals (-3.5)", on)) == ("asc-nfl-ari-lac-2026-09-13-neg-3pt5", "no", SHORT, LABEL), on
        assert _explain(rows, slug, "Spread: Cardinals (-3.5)", "Los Angeles Chargers")["split"] == "spread:title-unreadable"
        assert _explain(rows, slug, "Spread: Cardinals (-3.5)", "Los Angeles C")["split"] == "spread:team-outcome-unread"
        assert _explain(rows, slug, "Spread: Cardinals (-3.5)", "Los Angeles")["split"] == "spread:team-outcome-unread"

    def test_c10_his_la_against_the_venues_lar_reads_nothing(self, armed):
        # DOES NOT FIX: his 'LA' (table 4 line 134, nfl-la-lac) is not the
        # venue's 'lar'; on the sf-lar rows the outcome 'LA' reads no side.
        # (His slug 'nfl-la-lac' never meets the venue's keys anyway.)
        rows = _board(_whole_board())
        slug = "nfl-sf-lar-2026-09-11-spread-home-3pt5"
        assert _short(_resolve(rows, slug, "Spread: Rams (-3.5)", "Rams")) == ("asc-nfl-sf-lar-2026-09-10-pos-3pt5", "no", SHORT, LABEL)
        assert _short(_resolve(rows, slug, "Spread: LAR (-3.5)", "SF")) == ("asc-nfl-sf-lar-2026-09-10-pos-3pt5", "yes", LONG, LABEL)
        ex = _explain(rows, slug, "Spread: Rams (-3.5)", "LA")
        assert (ex["step"], ex["split"]) == ("no_side_match", "spread:team-outcome-unread")
        assert ex["date_shift"] == {"his": "2026-09-11", "venue": "2026-09-10"}
        # on his own date the same rows bind with no shift (the reader never
        # depends on C9's shift; table 4 carries no sf-lar row of his)
        hit = _resolve(rows, "nfl-sf-lar-2026-09-10-spread-home-3pt5", "Spread: Rams (-3.5)", "Rams")
        assert _short(hit) == ("asc-nfl-sf-lar-2026-09-10-pos-3pt5", "no", SHORT, LABEL) and "date_shift" not in hit

    def test_c10_the_saints_abbreviation_no_never_reaches_the_reader(self, armed):
        # DOES NOT FIX (review MEDIUM-1): his feed writes the New Orleans
        # Saints' abbreviation as 'NO' (nflteam_0256 table 4 line 165 'Spread:
        # DAL (-3.5)' DAL/NO, line 167 'Spread: DAL (-1.5)' NO), and C3's own
        # outcome guard reads a bare yes/no word as no team at all
        # (spread:outcome) BEFORE the team-field reader runs -- fail closed,
        # nothing bound, on either identity setting (with the switch off the
        # wording arm's yes/no branch refuses it too); the title 'Spread: NO
        # (-L)' is not a yes/no word and reads the Saints by team_abbr
        rows = _board(_whole_board())
        slug = "nfl-no-det-2026-09-13-spread-home-3pt5"
        for on in ("NO", "No", "no"):
            assert _resolve(rows, slug, "Spread: Lions (-3.5)", on) is None, on
            ex = _explain(rows, slug, "Spread: Lions (-3.5)", on)
            assert (ex["step"], ex["split"]) == ("no_side_match", "spread:outcome"), on
        assert _short(_resolve(rows, slug, "Spread: NO (-3.5)", "Lions")) == ("asc-nfl-no-det-2026-09-13-neg-3pt5", "no", SHORT, LABEL)
        assert _short(_resolve(rows, slug, "Spread: NO (-3.5)", "DET")) == ("asc-nfl-no-det-2026-09-13-neg-3pt5", "no", SHORT, LABEL)
        assert _short(_resolve(rows, slug, "Spread: Lions (-3.5)", "Saints")) == ("asc-nfl-no-det-2026-09-13-pos-3pt5", "yes", LONG, LABEL)
        # the live lane's step 2 on his two tokens: the DET token binds by its
        # own abbreviation and the NO token is the OTHER side by _choose_long's
        # elimination (the C1 two-token rule, not a reading of 'NO'); the NO
        # token alone maps nothing
        fills = [{"asset": "tok-no", "market_slug": slug, "market_title": "Spread: Lions (-3.5)", "event_title": None,
                  "outcome": "NO", "outcome_index": 0, "side": "BUY", "size": 10.0, "price": 0.5},
                 {"asset": "tok-det", "market_slug": slug, "market_title": "Spread: Lions (-3.5)", "event_title": None,
                  "outcome": "DET", "outcome_index": 1, "side": "BUY", "size": 1.0, "price": 0.5}]
        assert asyncio.run(ms.map_market(_Pool(rows), fills)) == {
            "us_slug": "asc-nfl-no-det-2026-09-13-pos-3pt5", "long_asset": "tok-no", "other_asset": "tok-det", "source": "premap"}
        assert asyncio.run(ms.map_market(_Pool(rows), fills[:1])) is None
        os.environ.pop(premap.PREMAP_YN_IDENTITY_ENV, None)
        assert _resolve(rows, slug, "Spread: Lions (-3.5)", "NO") is None
        assert _explain(rows, slug, "Spread: Lions (-3.5)", "NO")["step"] == "no_side_match"


# ------------------------------------------------------ (D) FAIL CLOSED

SLUG_35 = f"{HIS_ML}-spread-home-3pt5"
TITLE_35 = "Spread: Seahawks (-3.5)"


def _refused(rows, split, title=TITLE_35, outcome="Seahawks", slug=SLUG_35, cols=7):
    assert _resolve(rows, slug, title, outcome, cols=cols) is None, split
    ex = _explain(rows, slug, title, outcome, cols=cols)
    assert (ex["step"], ex["split"]) == ("no_side_match", split), (split, ex.get("c3"))
    assert ex["c3"]["refusal"] == split and "admitted" not in ex["c3"] and "matched_by" not in ex
    # the shadow's text through explain_unmapped
    ctx = {"title": title, "event_title": None, "outcome": outcome, "his_slug": slug}
    assert asyncio.run(ms.explain_unmapped(_Pool(rows, cols), ctx)) == f"no_side_match:{split}"
    return ex["c3"]


class TestFailClosed:
    def test_c10_the_identity_switch_off_is_todays_refusal_byte_for_byte(self):
        rows = _board(_ne_sea())
        os.environ.pop(premap.PREMAP_YN_IDENTITY_ENV, None)
        assert _resolve(rows, SLUG_35, TITLE_35, "Seahawks") is None
        ex = _explain(rows, SLUG_35, TITLE_35, "Seahawks")
        assert ex["step"] == "no_side_match" and "c3" not in ex and ex["c3_on"] is False
        assert ex["date_shift"] == {"his": HIS_DATE, "venue": VENUE_DATE}
        assert not str(ex.get("split") or "").startswith("spread:")
        for outcome in ("Patriots", "NE", "SEA"):
            assert _resolve(rows, SLUG_35, TITLE_35, outcome) is None

    def test_c10_the_aec_row_absent_or_its_columns_absent_is_team_absent(self, armed):
        # the sweep has not written the moneyline row (or it is delisted)
        rows = _board(_ne_sea(aec=False))
        c3 = _refused(rows, "spread:team-absent")
        assert c3["aec"] == {"identifier": AEC, "sides": []}
        # the C6 columns absent on this database (information_schema 0: the
        # pre-055 SELECT, the rows carry no team field)
        rows = _board(_ne_sea())
        c3 = _refused(rows, "spread:team-absent", cols=0)
        assert c3["aec"]["sides"] == ["patriots", "seahawks"]
        _refused(rows, "spread:team-absent", outcome="Patriots", cols=0)

    def test_c10_a_side_without_a_team_or_a_name_is_team_absent(self, armed):
        # one side states no team dict
        rows = _board(_ne_sea(aec_kw={"tb": ABSENT}))
        assert [r["team_abbr"] for r in rows if r["identifier"] == AEC] == ["ne", None]
        _refused(rows, "spread:team-absent")
        rows = _board(_ne_sea(aec_kw={"ta": ABSENT}))
        _refused(rows, "spread:team-absent", outcome="Patriots")
        # a dict without the abbreviation, or without the name
        rows = _board(_ne_sea(aec_kw={"tb": {k: v for k, v in _team("sea").items() if k != "abbreviation"}}))
        _refused(rows, "spread:team-absent")
        rows = _board(_ne_sea(aec_kw={"ta": {k: v for k, v in _team("ne").items() if k != "name"}}))
        _refused(rows, "spread:team-absent")
        # a side with an empty description falls out of _market_rows itself
        # (no row): the aec row then has ONE side -> team-unnamed
        mk = _ne_sea()
        mk[0]["marketSides"][1]["description"] = ""
        rows = _board(mk)
        assert [r["side_norm"] for r in rows if r["identifier"] == AEC] == ["patriots"]
        _refused(rows, "spread:team-unnamed")

    def test_c10_abbreviations_not_exactly_his_two_codes_is_team_unnamed(self, armed):
        # both sides stating one code
        rows = _board(_ne_sea(aec_kw={"tb": _team("ne")}))
        c3 = _refused(rows, "spread:team-unnamed")
        assert c3["team_abbrs"] == ["ne", "ne"]
        # a code that is neither of his ('nwe', the shape C9 marked synthetic)
        rows = _board(_ne_sea(aec_kw={"ta": dict(_team("ne"), abbreviation="nwe")}))
        c3 = _refused(rows, "spread:team-unnamed")
        assert c3["team_abbrs"] == ["nwe", "sea"]
        # a third team's code on a side
        rows = _board(_ne_sea(aec_kw={"tb": _team("kc") if "kc" in RECORD else dict(_team("sea"), abbreviation="kc")}))
        _refused(rows, "spread:team-unnamed")

    def test_c10_the_swapped_aec_dicts_are_the_venue_contradicting_itself(self, armed):
        # the sea dict on the Patriots side and the ne dict on the Seahawks
        # side: each side's description is not the tail of its own record's
        # full name -- refused BEFORE any position is read, so nothing can
        # ride the sign table on the wrong side
        rows = _board(_ne_sea(aec_kw={"ta": _team("sea"), "tb": _team("ne")}))
        for outcome in ("Seahawks", "Patriots", "NE", "SEA"):
            c3 = _refused(rows, "spread:team-mascot-conflict", outcome=outcome)
            assert "team_hits" not in c3 and "subject" not in c3
        # one side's record naming another team (the Seahawks side carrying
        # the Rams' record with the sea abbreviation): the same contradiction
        rows = _board(_ne_sea(aec_kw={"tb": dict(_team("lar"), abbreviation="sea")}))
        _refused(rows, "spread:team-mascot-conflict")
        # the venue's own sides in the OTHER listing order bind exactly the
        # same (a position is the abbreviation's index, never the listing)
        mk = _ne_sea()
        mk[0]["marketSides"].reverse()
        rows = _board(mk)
        assert [r["side_norm"] for r in rows if r["identifier"] == AEC] == ["seahawks", "patriots"]
        assert _short(_resolve(rows, SLUG_35, TITLE_35, "Seahawks")) == (f"asc-nfl-ne-sea-{VENUE_DATE}-pos-3pt5", "no", SHORT, LABEL)
        assert _short(_resolve(rows, SLUG_35, TITLE_35, "Patriots")) == (f"asc-nfl-ne-sea-{VENUE_DATE}-pos-3pt5", "yes", LONG, LABEL)

    def test_c10_his_outcome_reading_neither_side_or_both(self, armed):
        rows = _board(_ne_sea())
        # the city (team_safe_name is never read), a prefix of the full name,
        # a nickname, a dictionary word, his 'LA', the yes/no words, nothing
        for on in ("Seattle", "New England", "Seattle S", "Hawks", "Pats", "Se", "LA", "Cowboys", "seahawks patriots"):
            c3 = _refused(rows, "spread:team-outcome-unread", outcome=on)
            assert c3["team_hits"] == [], on
        for on in ("Yes", "No", ""):
            ex = _explain(rows, SLUG_35, TITLE_35, on)
            assert ex["c3"]["refusal"] == "spread:outcome"
        # 'seattle' against 'seattle seahawks' is NOT a match (never a prefix)
        assert not pmus._yn_name_match("seattle", "seattle seahawks")
        assert not premap._c10_side_reads("seattle", "seahawks", "seattle seahawks", "sea")
        assert premap._c10_side_reads("seahawks", "seahawks", "seattle seahawks", "sea")
        assert premap._c10_side_reads("seattle seahawks", "seahawks", "seattle seahawks", "sea")
        assert premap._c10_side_reads("sea", "seahawks", "seattle seahawks", "sea")
        assert not premap._c10_side_reads("se", "seahawks", "seattle seahawks", "sea")
        assert not premap._c10_side_reads("", "seahawks", "seattle seahawks", "sea")
        # both sides read (SYNTHETIC: the venue listing one mascot on both
        # sides -- a contradiction no real row shows): ambiguous, nothing bound
        mk = _ne_sea(aec_kw={"descs": ("Seahawks", "Seahawks"), "ta": dict(_team("ne"), name="New England Seahawks")})
        rows = _board(mk)
        c3 = _refused(rows, "spread:team-outcome-ambiguous")
        assert c3["team_hits"] == ["ne", "sea"]

    def test_c10_his_titles_team_reading_neither_side_or_both(self, armed):
        rows = _board(_ne_sea())
        for title in ("Spread: Seattle (-3.5)", "Spread: Hawks (-3.5)", "Spread: LA (-3.5)", "Spread: Chiefs (-3.5)"):
            c3 = _refused(rows, "spread:team-title-unread", title=title, outcome="Seahawks")
            assert c3["team_hits"] == ["sea"] and c3["title_hits"] == []
        mk = _ne_sea(aec_kw={"descs": ("Seahawks", "Seahawks"), "ta": dict(_team("ne"), name="New England Seahawks")})
        rows = _board(mk)
        c3 = _refused(rows, "spread:team-title-ambiguous", title=TITLE_35, outcome="NE")
        assert c3["team_hits"] == ["ne"] and c3["title_hits"] == ["ne", "sea"]

    def test_c10_the_question_naming_b_as_the_subject_is_subject_conflict(self, armed):
        # the venue's question with the Seahawks in the subject slot on the
        # ne-sea identifier (SYNTHETIC: the grammar puts a there on every row
        # on file): the venue contradicting the grammar, nothing rides the
        # sign table
        mk = _ne_sea(lines=("3pt5",))
        for m in mk:
            if m["slug"].startswith("asc-"):
                sign = "-" if "-neg-" in m["slug"] else ""
                m["question"] = _asc_q("sea", "ne", sign, "3.5")
        rows = _board(mk)
        for outcome in ("Seahawks", "Patriots"):
            c3 = _refused(rows, "spread:subject-conflict", outcome=outcome)
            assert c3["subject"] == {"certified": "seattle seahawks", "code": ["sea"], "slot": 0, "via": "team"}
            assert c3["venue_names"] == ["seattle seahawks", "new england patriots"]
        # a question name reading no side (the venue's question naming a team
        # its aec row does not carry): team-question-unread
        mk = _ne_sea(lines=("3pt5",))
        for m in mk:
            if m["slug"].startswith("asc-"):
                m["question"] = m["question"].replace("Seattle Seahawks", "Seattle Hawks")
        rows = _board(mk)
        c3 = _refused(rows, "spread:team-question-unread")
        assert c3["question_name"] == "seattle hawks"
        # both sides named by one question name (SYNTHETIC) is the conflict
        mk = _ne_sea(lines=("3pt5",), aec_kw={"tb": dict(_team("sea"), name="New England Patriots"), "descs": ("Patriots", "Patriots")})
        rows = _board(mk)
        c3 = _refused(rows, "spread:subject-conflict", title="Spread: NE (-3.5)", outcome="NE")
        assert c3["subject"]["code"] == ["ne", "sea"] and c3["subject"]["slot"] == 0

    def test_c10_the_asc_side_stating_the_other_code_or_no_team(self, armed):
        # the chosen asc side (pos-3pt5 'no' for his Seahawks) carrying the
        # Patriots' record: the row contradicts the sign table
        mk = _ne_sea(lines=("3pt5",))
        pos = next(m for m in mk if m["slug"].endswith("-pos-3pt5"))
        pos["marketSides"][1]["team"] = _team("ne")
        rows = _board(mk)
        c3 = _refused(rows, "spread:side-team-conflict")
        assert c3["side_team"] == "ne" and c3["subject"] == {"certified": "patriots", "code": "ne", "via": "team"}
        # and his Patriots on the same row's yes side (team ne, untouched) still binds
        assert _short(_resolve(rows, SLUG_35, TITLE_35, "Patriots")) == (f"asc-nfl-ne-sea-{VENUE_DATE}-pos-3pt5", "yes", LONG, LABEL)
        # the chosen side stating no team at all
        mk = _ne_sea(lines=("3pt5",))
        pos = next(m for m in mk if m["slug"].endswith("-pos-3pt5"))
        pos["marketSides"][1]["team"] = None
        rows = _board(mk)
        c3 = _refused(rows, "spread:side-team-absent")
        assert c3["side_team"] is None
        # the pure witness
        assert premap._c10_side_team({"team_abbr": "sea"}, "sea", {}) is None
        assert premap._c10_side_team({"team_abbr": "SEA "}, "sea", {}) is None
        assert premap._c10_side_team({"team_abbr": "ne"}, "sea", {}) == "spread:side-team-conflict"
        assert premap._c10_side_team({"team_abbr": None}, "sea", {}) == "spread:side-team-absent"
        assert premap._c10_side_team({}, "sea", {}) == "spread:side-team-absent"

    def test_c10_c3s_own_refusals_stand_after_the_witness(self, armed):
        # the intent wrong on the chosen row, the line sheared, the sign sheared
        mk = _ne_sea(lines=("3pt5",))
        pos = next(m for m in mk if m["slug"].endswith("-pos-3pt5"))
        pos["marketSides"][0]["long"], pos["marketSides"][1]["long"] = False, True   # markers swapped
        rows = _board(mk)
        ex = _explain(rows, SLUG_35, TITLE_35, "Seahawks")
        # the swapped markers make 'no' the yes-marked side's team (ne): the
        # side witness refuses first
        assert ex["split"] == "spread:side-team-conflict"
        mk = _ne_sea(lines=("3pt5",))
        pos = next(m for m in mk if m["slug"].endswith("-pos-3pt5"))
        pos["question"] = _asc_q("ne", "sea", "-", "3.5")             # a neg question on the pos row
        rows = _board(mk)
        _refused(rows, "spread:sign-shear")
        mk = _ne_sea(lines=("3pt5",))
        pos = next(m for m in mk if m["slug"].endswith("-pos-3pt5"))
        for s in pos["marketSides"]:
            s["description"] = "2.5"                                    # the side's line sheared
        rows = _board(mk)
        _refused(rows, "spread:line-shear")
        # the chosen side's STORED intent contradicting its marker (a stale
        # column): C3's own intent check, reached after the side witness --
        # review: the mutant dropping it survived this file (killed by
        # test_c3_derivatives alone), so the nfl path pins it here
        rows = _board(_ne_sea(lines=("3pt5",)))
        for r in rows:
            if r["identifier"].endswith("-pos-3pt5") and r["side_norm"] == "no":
                r["intent"] = LONG
        c3 = _refused(rows, "spread:intent")
        assert c3["side_team"] == "sea"
        assert _short(_resolve(rows, SLUG_35, TITLE_35, "Patriots")) == (f"asc-nfl-ne-sea-{VENUE_DATE}-pos-3pt5", "yes", LONG, LABEL)

    def test_c10_the_reversed_pair_is_not_fetched_and_two_dates_refuse(self, armed):
        rows = _board([_aec_market(AEC)] + _ne_sea(aec=False))
        for r in rows:
            r["identifier"] = r["identifier"].replace("ne-sea", "sea-ne")
            r["event_keys"] = [k.replace("ne-sea", "sea-ne") for k in r["event_keys"]]
        assert _resolve(rows, SLUG_35, TITLE_35, "Seahawks") is None
        assert _explain(rows, SLUG_35, TITLE_35, "Seahawks")["step"] == "no_key_intersection"
        # C9's date ambiguity holds in front of everything here
        two = _board(_ne_sea(lines=("3pt5",))) + _board(_venue_dated("2026-09-11"))
        assert _resolve(two, SLUG_35, TITLE_35, "Seahawks") is None
        assert _explain(two, SLUG_35, TITLE_35, "Seahawks")["step"] == "date_ambiguous"

    def test_c10_cfb_keeps_the_c4_chain_byte_for_byte(self, armed):
        # the same rows under the league token 'cfb' (SYNTHETIC: a cfb board
        # in the nfl shape, built to prove the gate): the C4 chain runs and
        # refuses as C9 pinned -- the venue's full names read as codes
        mk = _ne_sea(lines=("3pt5",))
        for m in mk:
            m["slug"] = m["slug"].replace("-nfl-", "-cfb-")
            for s in m["marketSides"]:
                s["identifier"] = s["identifier"].replace("-nfl-", "-cfb-")
        rows = _board(mk)
        slug = f"cfb-ne-sea-{VENUE_DATE}-spread-home-3pt5"
        assert _resolve(rows, slug, TITLE_35, "Seahawks") is None
        ex = _explain(rows, slug, TITLE_35, "Seahawks")
        assert (ex["step"], ex["split"]) == ("no_side_match", "spread:names-unreadable")
        assert "team" not in ex["c3"] and "side_team" not in ex["c3"]
        # the gate is the literal league token
        src = inspect.getsource(premap._c3_pick_spread)
        assert 'elif his["lg"] == C10_TEAM_LEAGUE:' in src and premap.C10_TEAM_LEAGUE == "nfl"
        assert src.index('elif his["lg"] == C10_TEAM_LEAGUE:') < src.index("_c4_subject_by_code(his, code, names")
        assert 'if label == C10_SPREAD_LABEL:' in src and premap.C10_SPREAD_LABEL == LABEL
        assert src.index("_c10_side_team(r, ") < src.index('trace["refusal"] = "spread:intent"')

    def test_c10_the_untouched_arms_hash_as_0b24564(self):
        def h(fn):
            return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]
        assert h(premap.match_side) == "6894ea0ebb90cefc"
        assert h(premap._c3_pick_total) == "c07814e34a74b0b6"
        assert h(premap._c4_subject_by_code) == "6a7ae0f56c7ae4e2"
        assert h(premap._c4_subject_certified) == "414fa5294b803fb5"
        assert h(premap._c6_team_subject) == "403035d6c15a74bb"
        assert h(premap._c6_pair_by_event) == "51f38d1e3880f2ec"
        assert h(premap.c3_pick) == "4f50d86eea6f7a7c"
        assert h(premap._c3_code) == "bf590f0fbe5f509f"
        assert h(premap._market_rows) == "21dd4006b77a4fa5" and h(premap._side_team) == "612e8b36fd709246"
        assert h(premap.yn_identity_on) == "66fca2a5f93747a6"
        assert h(map_lane._team_hits) == "964c90ddbeb5cad5" and h(map_lane._team_truth) == "afc42ffbbba39aad"
        assert h(map_lane.code_reads) == "bf19b4c22e50d1cb" and h(map_lane.code_school) == "053b1c8df20bd415"
        assert h(map_lane.aec_code_side) == "92f46f8ca9a6e77f" and h(map_lane.grammar_truth) == "943bc2e051752c0e"
        # the one that moved, and why (test_c9_nfl_board pins the same value)
        assert h(premap._c3_pick_spread) == "354668be1edc3d2d"


def _venue_dated(date):
    from tests.test_c9_nfl_board import _venue
    return _venue(date=date, spreads=("3pt5",), totals=())


# --------------------------------------------- (E) the seams and names

class TestTheSeams:
    def test_c10_the_reader_is_pure_and_reads_no_code_rule(self):
        for fn in (premap._c10_team_subject, premap._c10_side_team, premap._c10_side_reads,
                   premap._c3_pick_spread, premap.c3_pick):
            src = inspect.getsource(fn)
            for forbidden in ("await", "pool", "_get_client", "os.getenv", "SequenceMatcher", "ordering",
                              "code_reads", "code_hits", "code_school", "map_lane", "cert.get", "safeName",
                              '"alias"', 'get("name")'):
                assert forbidden not in src, (fn.__name__, forbidden)
        src = inspect.getsource(premap._c10_team_subject)
        assert "(a, b).index(abbr)" in src, "a position is the abbreviation's index, never the listing order"
        assert 'trace["refusal"] = "spread:team-mascot-conflict"' in src
        assert "tt[-len(st):] != st" in src, "the description is the tail of its own record's full name"
        assert 'trace["subject"] = {"certified": by_pos[0][1], "code": a, "via": "team"}' in src

    def test_c10_the_names_no_census_word_no_rule_no_migration(self):
        src = inspect.getsource(premap._c10_team_subject) + inspect.getsource(premap._c10_side_team)
        for name in ("spread:team-absent", "spread:team-unnamed", "spread:team-mascot-conflict",
                     "spread:team-outcome-unread", "spread:team-outcome-ambiguous", "spread:team-title-unread",
                     "spread:team-title-ambiguous", "spread:team-question-unread", "spread:subject-conflict",
                     "spread:side-team-conflict", "spread:side-team-absent"):
            assert name in src, name
        assert premap.C10_SPREAD_LABEL == "premap_spread_team" and premap.C10_TEAM_LEAGUE == "nfl"
        # no census name, no plan field, no decision word, no rail, no new switch
        live = inspect.getsource(ml)
        # (review: the FILL lanes write their own C1x tokens into this file --
        # C14 / C15 / C16 are there today -- so the pin is on this lane's
        # names, never on the bare token)
        assert "premap_spread_team" not in live and "_c10_" not in live and "team-absent" not in live
        assert not any("team" in k and "spread" in k for k in ml.CENSUS_KEYS)
        assert "NFL" not in inspect.getsource(rules) and "C10" not in inspect.getsource(rules)
        assert "premap_spread_team" not in inspect.getsource(ms) and "_c10_" not in inspect.getsource(ms)
        assert "C10" not in inspect.getsource(map_lane)
        assert "PREMAP_C10" not in inspect.getsource(premap) and "c10_on" not in inspect.getsource(premap)
        import pathlib
        from sportsassets.scripts import migrate
        files = [f.name for f in sorted(pathlib.Path(migrate.MIGRATIONS_DIR).glob("*.sql"))]
        assert files[-1].startswith("064_"), files[-1]  # re-pinned 2026-09-12 (run 83.3): run 83.3's 064 is the newest; this lane still adds none
        # the docs section, in the house header form, and its words
        doc = (pathlib.Path(__file__).resolve().parents[2] / "docs" / "mirror-coverage.md").read_text()
        assert re.search(r"^## \d+\. C10 -- .* \(2026-09-10, coverage lane C10\)", doc, re.M), "the C10 section header"
        sec = doc[doc.index(". C10 -- "):]
        for word in ("premap_spread_team", "_c10_team_subject", "_c10_side_team", "spread:team-absent",
                     "spread:team-unnamed", "spread:team-mascot-conflict", "spread:team-outcome-unread",
                     "spread:team-outcome-ambiguous", "spread:team-title-unread", "spread:team-title-ambiguous",
                     "spread:team-question-unread", "spread:subject-conflict", "spread:side-team-conflict",
                     "spread:side-team-absent", "PREMAP_YN_IDENTITY", "THE SIDE TABLE", "THE BOARD TABLE",
                     "DOES NOT FIX", "BUY_SHORT", "nflteam_0256_tables.txt"):
            assert word in sec, word

    def test_c10_the_trace_shape_on_a_binding(self, armed):
        hit, tr = _pick(_board(_ne_sea(lines=("3pt5",))), f"nfl-ne-sea-{VENUE_DATE}-spread-home-3pt5", TITLE_35, "Seahawks")
        assert hit is not None
        assert tr["subject_via"] == "team" and tr["matched_by"] == LABEL
        assert tr["subject"] == {"certified": "patriots", "code": "ne", "via": "team"}
        assert tr["team"]["a"] == {"abbr": "ne", "side_norm": "patriots", "team_name": "new england patriots"}
        assert tr["team"]["b"] == {"abbr": "sea", "side_norm": "seahawks", "team_name": "seattle seahawks"}
        assert tr["team_hits"] == ["sea"] and tr["title_hits"] == ["sea"] and tr["side_team"] == "sea"
        assert tr["aec"] == {"identifier": AEC, "sides": ["patriots", "seahawks"]}
        assert (tr["admitted"], tr["side"], tr["his_team"], tr["his_signed"]) == (
            f"asc-nfl-ne-sea-{VENUE_DATE}-pos-3pt5", "no", "b", "-3.5")


# ----------------------------------------- (F) the worker world: a book

SPREAD_SLUG = f"{HIS_ML}-spread-home-3pt5"
POS_35 = f"asc-nfl-ne-sea-{VENUE_DATE}-pos-3pt5"


def _spread_fill(asset, outcome, index, size=300.0, px=0.31):
    """His BUY on one token of the spread market. The short world's price
    is the fixture's own (test_mirror_live_worker._short_world: his other-
    token BUY at 0.72, so his level for our short is 1 - 0.72 = 0.28 in
    long space against the fixture venue's 0.32 ask)."""
    return _fill(asset, "BUY", size, px, NOW - 3000, market_slug=SPREAD_SLUG, event_slug=HIS_ML,
                 market_title=TITLE_35, outcome=outcome, outcome_index=index)


class _SpreadPool(_NflPool):
    """C9's worker-world pool with the C6 columns PRESENT (the live
    database's shape): information_schema answers 7, and the rows are
    projected to the resolvers' SELECT."""

    def __init__(self, rows, **kw):
        super().__init__(rows, **kw)
        premap._TEAM_COLS_STATE.update(present=None, at=0.0)

    async def fetch(self, sql, *a):
        if "us_premap" in sql and "event_keys &&" in sql:
            k = set(a[0])
            keep = BASE_COLS + TEAM_COLS
            return [{c: r.get(c) for c in keep} for r in self.premap_rows if set(r["event_keys"]) & k]
        return await super().fetch(sql, *a)

    async def fetchval(self, sql, *a):
        if "information_schema" in sql:
            return len(premap._TEAM_COLUMNS)
        return await super().fetchval(sql, *a)


def _fresh(monkeypatch):
    monkeypatch.setattr(ms, "_map_cache", {})
    ml._unmapped_until.clear()
    ml._unmapped_memo.clear()
    ml._terminal_until.clear()


def test_c10_his_seahawks_fill_opens_a_buy_short_book_on_the_pos_row(monkeypatch):
    """His BUY of the Seahawks token on 'Spread: Seahawks (-3.5)' maps to
    asc-nfl-ne-sea-2026-09-09-pos-3pt5 side 'no' BUY_SHORT (THE SIDE
    TABLE); the live lane takes it through the same candidate path every
    premap binding takes and the shorts machinery opens a short book:
    intent BUY_SHORT, the standing row claiming his (short-side) token
    with BUY_SHORT in its preview, the target negative, one SELL-side
    rest carrying the BUY_SHORT intent, source premap, the quote read on
    the venue's own identifier."""
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")
    _shorts_on(monkeypatch)
    _fresh(monkeypatch)
    p = _SpreadPool(_board(_ne_sea(lines=("3pt5",))), fills=[_spread_fill(N, "Seahawks", 1, px=0.72)],
                    snap={M: 0.0, N: 300.0}, snap_at=NOW - 40, ratio_fills=_ratio_fills())
    v = _Venue()
    st = _tick(p, v, http=_mkt(0.0, 300.0))
    assert _census(st, "unmapped") == 0 and _census(st, "map_source_unverified") == 0, st["census"]
    assert _census(st, "short_open") == 1 and _census(st, "short_side_refused") == 0, st["census"]
    assert p.books, "the short book opened"
    b = next(iter(p.books.values()))
    assert b["us_market_slug"] == POS_35 and b["map_source"] == "premap"
    assert b["intent"] == SHORT and b["target"] == -300 and b["ratio"] == 1.0
    assert b["long_asset"] == M and b["other_asset"] == N
    row = p.rows[b["standing_row_id"]]
    assert row["asset"] == N and row["raw"]["preview"]["intent"] == SHORT
    placed = _places(v)
    assert len(placed) == 1 and placed[0][1] == POS_35 and placed[0][6] == SHORT, placed
    o = next(iter(p.orders.values()))
    assert (o["side"], o["intent"], o["kind"]) == (rules.SELL, SHORT, "increase")
    assert any(c == ("bbo", POS_35) for c in v.calls), "the quote read on the venue's own identifier"
    assert not any(r.get("refusal") == "unmapped" for r in p.cand_refusals)


def test_c10_his_patriots_fill_opens_a_buy_long_book_on_the_same_rows_yes_side(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")
    _fresh(monkeypatch)
    p = _SpreadPool(_board(_ne_sea(lines=("3pt5",))), fills=[_spread_fill(M, "Patriots", 0)],
                    snap={M: 300.0, N: 0.0}, snap_at=NOW - 40, ratio_fills=_ratio_fills())
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "unmapped") == 0 and p.books, st["census"]
    b = next(iter(p.books.values()))
    assert b["us_market_slug"] == POS_35 and b["long_asset"] == M and b["map_source"] == "premap"
    assert b.get("intent") in (LONG, None) and b["target"] == 300
    placed = [o for o in p.orders.values()]
    assert placed and all(o["side"] == "BUY_LONG" for o in placed) and sum(o["qty"] for o in placed) == 300
    assert any(c == ("bbo", POS_35) for c in v.calls)


def test_c10_with_the_identity_switch_off_the_candidate_stays_unmapped(monkeypatch):
    monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)
    _shorts_on(monkeypatch)
    _fresh(monkeypatch)
    p = _SpreadPool(_board(_ne_sea(lines=("3pt5",))), fills=[_spread_fill(N, "Seahawks", 1, px=0.72)],
                    snap={M: 0.0, N: 300.0}, snap_at=NOW - 40, ratio_fills=_ratio_fills())
    v = _Venue()
    st = _tick(p, v, http=_mkt(0.0, 300.0))
    assert _census(st, "unmapped") == 1 and not p.books and not p.orders
    # and on a board whose aec sides carry no team (C9's fixture) the same
    # candidate stays unmapped under the switch: fail closed
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")
    _fresh(monkeypatch)
    from tests.test_c9_nfl_board import _venue as _c9_venue
    p = _SpreadPool(_board(_c9_venue(spreads=("3pt5",), totals=())), fills=[_spread_fill(N, "Seahawks", 1, px=0.72)],
                    snap={M: 0.0, N: 300.0}, snap_at=NOW - 40, ratio_fills=_ratio_fills())
    st = _tick(p, _Venue(), http=_mkt(0.0, 300.0))
    assert _census(st, "unmapped") == 1 and not p.books and not p.orders
