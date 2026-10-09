"""RC6 LANE D2: THE VENUE'S SLUG GRAMMAR IN THE MARKET PLANE, ON PRODUCTION
ROWS.

populate.league_of read the SECOND segment of every slug. The venue's event
slug carries its league FIRST (`cfb-airf-nill-2026-10-10`; research-sql run
37871119335 W8: every one of the top 60 first segments of the 74,288 active
PMUS event ids is a league code), so every listed game's registry
`competition` was a team code, every outright's a futures subject ('2027',
'wins', ...: 10,846 outrights left SPORT_NOT_NORMALIZED) and 1,343 BTC price
markets escaped the NON_SPORTS exclusion that names them by their own code.
Evidence only: nothing here can make a contract proven or priceable.
"""
from __future__ import annotations

import json
import pathlib
import time

import pytest

from sportsassets import copy_sports as CS
from sportsassets import venue_catalogue as VC
from sportsassets.market_plane import ontology as O
from sportsassets.market_plane import populate as POP

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
ROWS = json.loads((FIX / "pmus_line_terms_rows_2026_10_09.json")
                  .read_text())["rows"]


# ═════════════════════════════════════════════════════════════════════
# 1. THE VENUE'S SLUG GRAMMAR
# ═════════════════════════════════════════════════════════════════════

#: (event id as production holds it, the venue league it names) --
#: research-sql 37871119335 W8 and 37871400151 F4 / F5, verbatim
EVENT_IDS = (
    ("cfb-airf-nill-2026-10-10", "cfb"),
    ("nfl-ari-lar-2026-10-18", "nfl"),
    ("nhl-ana-cgy-2026-10-10", "nhl"),
    ("mlb-cle-cws-2026-10-08", "mlb"),
    ("bun-aug-fcb-2026-10-10", "bun"),
    ("atp-abeshe-dangli-2026-10-09", "atp"),
    ("bun-2027-05-22-relegation", "bun"),
    ("cfb-wins-ou-2026-11-28", "cfb"),
    ("dpwt-deespa-2026-10-08-w", "dpwt"),
    ("nfl-wk5-2026-10-12-kmostfpts", "nfl"),
    ("btc-range-hr-2026-10-09-0200z", "btc"),
    ("btc-above-hr-2026-10-08-2300z", "btc"),
    ("wbc-bantamw-2026-12-31-champ", "wbc"),
    ("uscpi-september-mom-2026-10-14", "uscpi"),
)


@pytest.mark.parametrize("event_id,league", EVENT_IDS)
def test_the_league_is_the_event_slugs_first_segment(event_id, league):
    assert POP.league_of(event_id) == league
    # the rule the repository already states twice for the venue's slugs
    assert VC.league_of(event_id) == league
    assert CS.league_of(event_id) == league


def test_a_market_slug_still_reads_the_segment_after_its_kind():
    assert POP.league_of("aec-mlb-nyy-bos-2026-09-24") == "mlb"
    assert POP.league_of("asc-cfb-airf-nill-2026-10-10-neg-10pt5") == "cfb"
    assert POP.league_of(
        "astatc-atp-nunbor-tometc-2026-08-06-es-0-2") == "atp"
    assert POP.league_of(None, "NFL") == "nfl"
    assert POP.league_of("", None) is None
    assert POP.MARKET_KIND_PREFIXES == frozenset(CS._KINDS)


def test_production_rows_carried_a_team_code_as_their_league():
    """The defect, on the rows: production's `competition` was the event
    slug's SECOND segment (a team); the venue's league is its first."""
    read = [r for r in ROWS if "competition_as_read" in r]
    assert len(read) == 20
    for r in read:
        first, second = r["event_id"].split("-")[:2]
        assert r["competition_as_read"] == second
        assert POP.league_of(r["event_id"]) == first != second


def _row(event_slug, sports_type, slug=None):
    return {"market_slug": slug or "m-" + event_slug,
            "event_slug": event_slug, "sports_type": sports_type,
            "team_league": None, "sides": []}


def test_outrights_map_by_their_own_league_code():
    now = time.time()
    for ev, sport in (("nfl-wins-ou-2027-01-10", "football"),
                      ("cfb-2026-11-28-mostpasstds", "football"),
                      ("bun-2027-05-22-relegation", "soccer"),
                      ("epl-title-2027-05-30-w", "soccer"),
                      ("nhl-westconf-2027-05-19-w", "hockey"),
                      ("dpwt-deespa-2026-10-08-w", "golf"),
                      ("mlb-wsleague-2026-11-01-w", "baseball")):
        c = POP.contract_row(_row(ev, "futures"), now=now)
        assert c["competition"] == ev.split("-")[0]
        assert c["sport"] == sport and c["family"] == "OUTRIGHT"
        assert c["ontology"]["gaps"] == []
        assert c["ontology"]["sport_basis"] == "VENUE_LEAGUE_CODE"


def test_non_sports_codes_are_excluded_by_name():
    now = time.time()
    for ev, st in (("btc-range-hr-2026-10-09-0200z", None),
                   ("btc-above-hr-2026-10-08-2300z", None),
                   ("uscpi-september-mom-2026-10-14", "futures"),
                   ("usfed-hike2-2026-12-31", "futures"),
                   ("ecb-2026-10-29", "futures"),
                   ("nobel-peace-2026-10-09", "futures"),
                   ("temp-laxhigh-2026-10-09", "futures")):
        assert POP.contract_row(_row(ev, st), now=now) is None, ev


def test_an_ambiguous_league_code_is_a_named_gap_not_a_sport():
    # (RC6.2, p-coverage) a weight-class slug now resolves the ambiguity
    # (tests/test_rc62_coverage_ontology.py: every production wbc row is a
    # boxing title future); the code ALONE still never names a sport
    c = POP.contract_row(_row("wbc-usa-jpn-2026-03-17", "futures"),
                         now=time.time())
    assert c["sport"] is None
    assert c["ontology"]["gaps"] == ["LEAGUE_CODE_AMBIGUOUS"]
    assert "wbc" not in O.LEAGUE_SPORT
    b = POP.contract_row(_row("wbc-bantamw-2026-12-31-champ", "futures"),
                         now=time.time())
    assert b["sport"] == "boxing" and b["ontology"]["gaps"] == []


def test_ncaa_soccer_codes_read_soccer():
    """F3: every typed ncaaws / ncaams row is a soccer market."""
    for code in ("ncaaws", "ncaams"):
        assert O.LEAGUE_SPORT[code] == "soccer"
        p = O.parse_market_type(venue="POLYMARKET_US", contract_id="x",
                                sports_market_type="futures",
                                competition=code)
        assert p["meaning"]["sport"] == "soccer"


def test_a_typed_rows_sport_still_comes_from_its_market_type():
    c = POP.contract_row(_row("cfb-airf-nill-2026-10-10",
                              "football_team_full_game_spread"),
                         now=time.time())
    assert (c["competition"], c["sport"], c["family"]) == \
        ("cfb", "football", "MARGIN")
    assert c["ontology"]["sport_basis"] == "VENUE_MARKET_TYPE"
