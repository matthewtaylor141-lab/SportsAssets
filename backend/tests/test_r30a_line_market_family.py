"""R30A P0 INCIDENT -- LINE MARKETS (SPREAD, TOTAL, TEAM TOTAL), PRICED ONLY
WHERE THE PAYOFF IS PROVEN EQUIVALENT (bettor_market_family).

Owner decisions 2026-10-04: "Only compare prices when the payoff functions are
genuinely equivalent"; half-point lines only unless push handling is proven
equivalent; de-vig each line market on its own two-way pair; exact line match
required (LINE_DOES_NOT_MATCH otherwise).

PRODUCTION-SHAPED INPUTS, NOTHING HAND-TYPED WHERE A RECORD EXISTS:
  * the venue side is the venue's OWN listing of 22 line contracts
    (tests/fixtures/pmus_line_listings_2026_10_04.json, fetch-docs runs
    37235387633 / 37235391116) turned into catalogue rows by the REAL catalogue
    writer (`workers.premap._market_rows`), with the sides' shared identifier
    the venue states for this family (premap._asc_yes_no);
  * the book side is Pinnacle's published rules page, verbatim
    (tests/fixtures/pinnacle_line_rules_2026_10_04.json, fetch-docs run
    37234815185, page sha256 63d64321...);
  * the Pinnacle markets are the raw record shape the WS capture observed
    (`markets[].key/type/period/side/isAlternate/prices[designation, price,
    points]`, test_pinnapi_feed_ownership.OBSERVED_LIVE_UPD), applied to the
    REAL FeedCache. ALL PRICES ARE SYNTHETIC.

  §1 citation integrity (every quote verbatim, the page identity pinned)
  §2 the 22 listings: proven families establish from their own words, the
     rest refuse by their precise reason
  §3 the contract: half-point only, sides, team, slug agreement
  §4 Pinnacle's market at the identical line, on the real cache
  §5 the de-vig on the pair's own two outcomes; the supported set pinned
  §6 the completed-game policy's line match
  §7 END TO END on Postgres: a WS change prices a run line through the REAL
     cycle and the completed-game paper policy enters it
"""
from __future__ import annotations

import copy
import json
import pathlib
import time

import pytest

from sportsassets import bettor_market_family as MF
from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import pinnapi_feed as F
from sportsassets.workers import premap as pm

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
RULES = json.loads((FIX / "pinnacle_line_rules_2026_10_04.json").read_text())
LISTINGS = json.loads((FIX / "pmus_line_listings_2026_10_04.json")
                      .read_text())
MARKETS = {m["slug"]: m for m in LISTINGS["markets"]}


def _rows(slug):
    """The catalogue rows the REAL writer produces from the venue listing.
    The listing subset omits the side identifier; the venue states it equal
    to the market slug on both sides of these families (premap._asc_yes_no,
    'sides share the identifier')."""
    m = copy.deepcopy(MARKETS[slug])
    for s in m["marketSides"]:
        s["identifier"] = m["slug"]
    ev = {"slug": slug.split("-", 1)[1].rsplit("-", 2)[0],
          "title": m["question"]}
    return pm._market_rows(ev, m)


def _participants(spread_slug, *, pinnacle_home_ordering="home"):
    """Each Pinnacle designation's venue team record, as the discovery
    receipt carries it -- here read off the venue's own spread listing of
    the event (the side's team record), Pinnacle's home being the venue's
    `ordering: home` team unless stated otherwise."""
    out = {}
    for s in MARKETS[spread_slug]["marketSides"]:
        t = s.get("team") or {}
        des = ("home" if t.get("ordering") == pinnacle_home_ordering
               else "away")
        out[des] = {"team_name": pm._norm(t.get("name")) or None,
                    "team_safe_name": pm._norm(t.get("safeName")) or None,
                    "team_abbr": t.get("abbreviation"),
                    "team_id": t.get("id")}
    return out


NFL_SPREAD = "asc-nfl-det-car-2026-10-04-neg-10pt5"
EVENT_SPREAD = {
    "nfl-det-car": NFL_SPREAD,
    "cfb-soumis-troy": "asc-cfb-soumis-troy-2026-10-06-neg-10pt5",
    "nhl-uta-nyr": "asc-nhl-uta-nyr-2026-10-04-neg-1pt5",
    "nba-gs-lac": "asc-nba-gs-lac-2026-10-04-neg-2pt5",
    "mlb-atl-lad": "asc-mlb-atl-lad-2026-10-04-neg-1pt5",
    "unl-cyp-lat": "asc-unl-cyp-lat-2026-10-05-neg-1pt5",
    "intf-uga-cdr": "asc-intf-uga-cdr-2026-10-05-neg-1pt5",
    "atp-alikac-grilom": "asc-atp-alikac-grilom-2026-10-04-gs-neg-1pt5",
}


def _event_of(slug):
    for k, v in EVENT_SPREAD.items():
        if k in slug:
            return v
    raise KeyError(slug)


# ═════════════════════════════════════════════════════════════════════
# §1 CITATION INTEGRITY
# ═════════════════════════════════════════════════════════════════════

def test_every_book_quote_is_the_publishers_own_words():
    for key, (section, quote) in MF.BOOK.items():
        lines = RULES["sections"][section]
        assert any(quote in line for line in lines), (key, quote)
    assert MF.BOOK_CAPTURE["page_sha256"] == RULES["page_sha256"]
    assert MF.BOOK_CAPTURE["retrieved_at"] == RULES["retrieved_at"]
    assert MF.BOOK_CAPTURE["run_id"] == RULES["captured_in"]["run_id"]
    # the reader window is why soccer and tennis refuse: stated by the capture
    assert "Tennis" in RULES["reader_window"]
    assert "Soccer Market Rules" in RULES["reader_window"]


def test_the_venue_capture_is_the_listing_reads():
    for k, read in MF.VENUE_CAPTURE["reads"].items():
        assert read["run_id"] == LISTINGS["_reads"][k]["run_id"]
        assert read["response_sha256"] == \
            LISTINGS["_reads"][k]["response_sha256"]
    for spec in MF.EQUIVALENCE.values():
        for slug in spec["venue_examples"]:
            assert slug in MARKETS, slug
    # every listed type the venue was read carrying has a family entry
    for m in LISTINGS["markets"]:
        assert m["sportsMarketType"] in MF.VENUE_LINE_TYPES, m["slug"]
    # the disclosed exceptional terms quote the venue's own listings verbatim
    prose = " ".join(m["description"] for m in LISTINGS["markets"])
    for spec in MF.EQUIVALENCE.values():
        for x in spec["exceptional"]:
            assert x["venue_terms"] in prose, x["condition"]
            assert x["book"] in MF.BOOK


# ═════════════════════════════════════════════════════════════════════
# §2 THE 22 LISTINGS
# ═════════════════════════════════════════════════════════════════════

EXPECTED = {
    # proven: the contract's own words, the book's cited grading
    "asc-nfl-det-car-2026-10-04-neg-10pt5": (True, MF.GP_FOOTBALL),
    "asc-nfl-det-car-2026-10-04-neg-13pt5": (True, MF.GP_FOOTBALL),
    "tsc-nfl-det-car-2026-10-04-total-22pt5": (True, MF.GP_FOOTBALL),
    "tsc-nfl-det-car-2026-10-04-tt-car-10pt5": (True, MF.GP_FOOTBALL),
    "asc-cfb-soumis-troy-2026-10-06-neg-10pt5": (True, MF.GP_FOOTBALL),
    "tsc-cfb-soumis-troy-2026-10-06-total-22pt5": (True, MF.GP_FOOTBALL),
    "tsc-cfb-soumis-troy-2026-10-06-tt-soumis-10pt5": (True, MF.GP_FOOTBALL),
    "asc-nhl-uta-nyr-2026-10-04-neg-1pt5": (True, MF.GP_HOCKEY),
    "asc-nhl-uta-nyr-2026-10-04-neg-2pt5": (True, MF.GP_HOCKEY),
    "tsc-nhl-uta-nyr-2026-10-04-2pt5": (True, MF.GP_HOCKEY),
    "tsc-nhl-uta-nyr-2026-10-04-tt-nyr-0pt5": (True, MF.GP_HOCKEY),
    "asc-nba-gs-lac-2026-10-04-neg-2pt5": (True, MF.GP_BASKETBALL),
    "tsc-nba-gs-lac-2026-10-04-221pt5": (True, MF.GP_BASKETBALL),
    "asc-mlb-atl-lad-2026-10-04-neg-1pt5": (True, MF.GP_BASEBALL),
    "tsc-mlb-atl-lad-2026-10-04-6pt5": (True, MF.GP_BASEBALL),
    "tsc-mlb-atl-lad-2026-10-04-tt-atl-1pt5": (True, MF.GP_BASEBALL),
    # not proven, each by its own precise reason (never "unsupported")
    "asc-unl-cyp-lat-2026-10-05-neg-1pt5":
        (False, MF.R_BOOK_MARKET_RULES_NOT_CAPTURED),
    "tsc-unl-cyp-lat-2026-10-05-0pt5":
        (False, MF.R_BOOK_MARKET_RULES_NOT_CAPTURED),
    "asc-intf-uga-cdr-2026-10-05-neg-1pt5":
        (False, MF.R_BOOK_MARKET_RULES_NOT_CAPTURED),
    "tsc-intf-uga-cdr-2026-10-05-0pt5":
        (False, MF.R_BOOK_MARKET_RULES_NOT_CAPTURED),
    "asc-atp-alikac-grilom-2026-10-04-gs-neg-1pt5":
        (False, MF.R_BOOK_SPORT_NOT_CAPTURED),
    "tsc-atp-alikac-grilom-2026-10-04-tg-17pt5":
        (False, MF.R_BOOK_SPORT_NOT_CAPTURED),
}


def test_all_22_listings_are_classified_from_their_own_words():
    assert set(EXPECTED) == set(MARKETS), "every captured listing judged"
    for slug, (ok, what) in EXPECTED.items():
        parts = _participants(_event_of(slug))
        c = MF.market_contract(_rows(slug), participants=parts)
        if not ok:
            assert c["ok"] is False and c["refusal"] == what, (slug, c)
            # the proof refuses by the same name from the contract alone
            p = MF.prove(contract=c, venue_text=MARKETS[slug]["description"],
                         participants=parts)
            assert p["established"] is False and p["refusal"] == what
            continue
        assert c["ok"] is True, (slug, c)
        p = MF.prove(contract=c, venue_text=MARKETS[slug]["description"],
                     participants=parts)
        assert p["established"] is True, (slug, p)
        assert p["period"] == what
        assert MF.book_grading(c["sport"], c["family"])["period"] == what
        # both sides cited; the exceptional terms disclosed, never "proven"
        assert p["book"] and all(b["page_sha256"] == RULES["page_sha256"]
                                 for b in p["book"])
        assert p["exceptional"]["status"] == "DISCLOSED_NOT_EQUIVALENT"
        assert p["exceptional"]["divergences"]


def test_the_spread_proposition_and_the_team_totals_team():
    parts = _participants(NFL_SPREAD)
    # the venue lists DET as the away side covering -10.5
    c = MF.market_contract(_rows(NFL_SPREAD), participants=parts)
    assert (c["family"], c["line"], c["designation"]) == \
        (MF.SPREAD, -10.5, "away")
    assert [i["intent"] for i in c["instruments"]] == [MF.INTENT_LONG,
                                                      MF.INTENT_SHORT]
    # the team total's team: CAR from the slug token AND from its own text
    tt = "tsc-nfl-det-car-2026-10-04-tt-car-10pt5"
    c = MF.market_contract(_rows(tt), participants=parts)
    assert (c["family"], c["line"], c["designation"]) == \
        (MF.TEAM_TOTAL, 10.5, "home")
    p = MF.prove(contract=c, venue_text=MARKETS[tt]["description"],
                 participants=parts)
    assert p["established"] and p["designation"] == "home"
    # NHL: 'NYR Rangers' is the venue's own abbreviation + name rendering
    nhl = "tsc-nhl-uta-nyr-2026-10-04-tt-nyr-0pt5"
    nparts = _participants(EVENT_SPREAD["nhl-uta-nyr"])
    c = MF.market_contract(_rows(nhl), participants=nparts)
    assert c["ok"] and c["designation"] == "home", c
    p = MF.prove(contract=c, venue_text=MARKETS[nhl]["description"],
                 participants=nparts)
    assert p["established"] and p["designation"] == "home"
    # Pinnacle's own home/away decides the designation, not the venue's
    swapped = _participants(NFL_SPREAD, pinnacle_home_ordering="away")
    c = MF.market_contract(_rows(NFL_SPREAD), participants=swapped)
    assert c["designation"] == "home"


def test_a_text_that_contradicts_or_differs_is_never_proven():
    parts = _participants(NFL_SPREAD)
    c = MF.market_contract(_rows(NFL_SPREAD), participants=parts)
    text = MARKETS[NFL_SPREAD]["description"]
    p = MF.prove(contract=c, venue_text=text.replace(
        "Overtime is included if played.", "Overtime is not included."),
        participants=parts)
    assert p["refusal"] == MF.R_VENUE_TEXT_CONFLICTS
    p = MF.prove(contract=c, venue_text=text.replace(
        "Overtime is included if played. ", ""), participants=parts)
    assert p["refusal"] == MF.R_VENUE_TEXT_UNRECOGNISED
    p = MF.prove(contract=c, venue_text=text.replace("-10.5", "-9.5"),
                 participants=parts)
    assert p["refusal"] == MF.R_VENUE_TEXT_LINE
    p = MF.prove(contract=c, venue_text=text.replace(
        "Yes if Detroit Lions covers", "Yes if Carolina Panthers covers"),
        participants=parts)
    assert p["refusal"] == MF.R_VENUE_TEXT_TEAM
    p = MF.prove(contract=c, venue_text="", participants=parts)
    assert p["refusal"] == MF.R_VENUE_TEXT_ABSENT
    # hockey without the shootout sentence is not the book's grading
    nhl = EVENT_SPREAD["nhl-uta-nyr"]
    np_ = _participants(nhl)
    nc = MF.market_contract(_rows(nhl), participants=np_)
    p = MF.prove(contract=nc, venue_text=MARKETS[nhl]["description"].replace(
        "If a shootout determines the winner, the shootout will count as one "
        "goal for the winning team. ", ""), participants=np_)
    assert p["refusal"] == MF.R_VENUE_TEXT_UNRECOGNISED


# ═════════════════════════════════════════════════════════════════════
# §3 THE CONTRACT
# ═════════════════════════════════════════════════════════════════════

def test_half_point_lines_only():
    assert MF.half_point(-10.5) and MF.half_point(221.5) and \
        MF.half_point(0.5)
    for v in (-3, 3.0, 47, -0.25, 1.75, None, "x", 0):
        assert not MF.half_point(v), v
    parts = _participants(NFL_SPREAD)
    rows = _rows(NFL_SPREAD)
    for r in rows:                         # the same market at a whole line
        r["signed"] = r["signed"].replace("10.5", "3")
        r["market_slug"] = r["market_slug"].replace("neg-10pt5", "neg-3pt0")
    c = MF.market_contract(rows, participants=parts)
    assert c["ok"] is False and c["refusal"] == MF.R_NOT_HALF_POINT


def test_the_contract_refuses_unread_sides_teams_and_slug_disagreement():
    parts = _participants(NFL_SPREAD)
    rows = _rows(NFL_SPREAD)
    assert MF.market_contract(rows[:1], participants=parts)["refusal"] == \
        MF.R_CONTRACT_SIDES
    bad = [dict(r) for r in rows]
    bad[0]["intent"] = bad[1]["intent"]
    assert MF.market_contract(bad, participants=parts)["refusal"] == \
        MF.R_CONTRACT_SIDES
    stranger = {"home": parts["home"],
                "away": dict(parts["away"], team_id=999,
                             team_name="green bay packers")}
    assert MF.market_contract(rows, participants=stranger)["refusal"] == \
        MF.R_CONTRACT_TEAM
    lie = [dict(r, market_slug=r["market_slug"].replace("10pt5", "11pt5"))
           for r in rows]
    assert MF.market_contract(lie, participants=parts)["refusal"] == \
        MF.R_CONTRACT_SLUG_LINE
    winner = [dict(r, sports_type="football_team_full_game_winner")
              for r in rows]
    assert MF.market_contract(winner, participants=parts)["refusal"] == \
        MF.R_NOT_A_LINE_TYPE
    odd = [dict(r, sports_type="football_team_first_half_spread")
           for r in rows]
    assert MF.market_contract(odd, participants=parts)["refusal"] == \
        MF.R_LINE_TYPE_UNKNOWN


# ═════════════════════════════════════════════════════════════════════
# §4 PINNACLE'S MARKET AT THE IDENTICAL LINE, ON THE REAL CACHE
# ═════════════════════════════════════════════════════════════════════

FID = 1637712345
START = "2026-10-05T00:20:00Z"


def _spread(home_pts, hp, ap, *, alt=False):
    return {"key": "s;0;s;%s" % home_pts, "type": "spread", "period": 0,
            "status": "open", "isAlternate": alt,
            "prices": [{"designation": "home", "price": hp,
                        "points": home_pts},
                       {"designation": "away", "price": ap,
                        "points": -home_pts}]}


def _total(pts, op, up, *, alt=False):
    return {"key": "s;0;ou;%s" % pts, "type": "total", "period": 0,
            "status": "open", "isAlternate": alt,
            "prices": [{"designation": "over", "price": op, "points": pts},
                       {"designation": "under", "price": up,
                        "points": pts}]}


def _team_total(side, pts, op, up):
    return {"key": "s;0;tt;%s" % side, "type": "team_total", "period": 0,
            "status": "open", "side": side,
            "prices": [{"designation": "over", "price": op, "points": pts},
                       {"designation": "under", "price": up,
                        "points": pts}]}


NFL_MARKETS = [
    {"key": "s;0;m", "type": "moneyline", "period": 0, "status": "open",
     "prices": [{"designation": "home", "price": 420},
                {"designation": "away", "price": -560}]},
    # Pinnacle lists CAR home (+10.5 main), DET away (-10.5)
    _spread(10.5, -105, -105), _spread(13.5, -140, 120, alt=True),
    _spread(9.5, 110, -130, alt=True),
    _total(22.5, -108, -112), _total(24.5, 105, -125, alt=True),
    _team_total("home", 10.5, -115, -105), _team_total("away", 20.5, -110,
                                                       -110)]


def nfl_cache(*, markets=None, changed=True, change_age_s=1.0):
    """A synced epoch: a snapshot (first sight, age unknown), then -- when
    `changed` -- an authoritative prematch_markets list that MOVES every
    price, so each market has an observed change `change_age_s` ago."""
    markets = copy.deepcopy(markets or NFL_MARKETS)
    c = F.FeedCache()
    ep = c.new_connection([("prematch", 5)])
    now = time.time() * 1000.0
    rec = {"id": FID, "type": "matchup", "startTime": START, "isLive": False,
           "units": "Regular", "league": {"id": 889, "name": "NFL"},
           "participants": [{"name": "Carolina Panthers",
                             "alignment": "home"},
                            {"name": "Detroit Lions", "alignment": "away"}],
           "markets": copy.deepcopy(markets)}
    first = copy.deepcopy(rec)
    for m in first["markets"]:
        for p in m["prices"]:
            p["price"] = p["price"] + (1 if p["price"] > 0 else -1)
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 5,
             "ts": now - 120_000, "events": [first]}, epoch=ep,
            received_ms=now - 120_000)
    if changed:
        c.apply({"type": "prematch_markets", "matchup_id": FID,
                 "sport_id": 5, "ts": now - change_age_s * 1000.0,
                 "data": copy.deepcopy(markets)}, epoch=ep, received_ms=now)
    return c


def _contract(slug, parts=None):
    parts = parts or _participants(NFL_SPREAD)
    c = MF.market_contract(_rows(slug), participants=parts)
    assert c["ok"], c
    return c


def test_the_identical_line_and_side_is_read_never_a_neighbour():
    cache = nfl_cache()
    now = time.time() * 1000.0
    c = _contract(NFL_SPREAD)             # DET -10.5, Pinnacle's away
    pair = MF.pinnacle_pair(cache, fixture_id=FID, contract=c,
                            evaluated_ms=now)
    assert pair["ok"], pair
    assert pair["key"] == "s;0;s;10.5" and pair["alternate"] is False
    assert pair["selection"] == "Detroit Lions -10.5"
    assert pair["other"] == "Carolina Panthers +10.5"
    assert pair["designations"] == {"Detroit Lions -10.5": "away",
                                    "Carolina Panthers +10.5": "home"}
    assert pair["outcomes"]["Detroit Lions -10.5"] == \
        pytest.approx(F.american_to_decimal(-105))
    # the -13.5 rung matches Pinnacle's ALTERNATE at exactly that line
    alt = _contract("asc-nfl-det-car-2026-10-04-neg-13pt5")
    pa = MF.pinnacle_pair(cache, fixture_id=FID, contract=alt,
                          evaluated_ms=now)
    assert pa["ok"] and pa["key"] == "s;0;s;13.5" and pa["alternate"]
    # a line Pinnacle does not quote: the de-vig's own code, never the
    # nearest line
    off = dict(c, line=-11.5)
    po = MF.pinnacle_pair(cache, fixture_id=FID, contract=off,
                          evaluated_ms=now)
    assert po["ok"] is False and po["refusal"] == "LINE_DOES_NOT_MATCH"
    assert po["pinnacle_lines_of_this_family"] == [-13.5, -10.5, -9.5]


def test_totals_and_team_totals_are_matched_by_line_and_team():
    cache = nfl_cache()
    now = time.time() * 1000.0
    tot = _contract("tsc-nfl-det-car-2026-10-04-total-22pt5")
    p = MF.pinnacle_pair(cache, fixture_id=FID, contract=tot,
                         evaluated_ms=now)
    assert p["ok"] and p["key"] == "s;0;ou;22.5"
    assert (p["selection"], p["other"]) == ("Over 22.5", "Under 22.5")
    tt = _contract("tsc-nfl-det-car-2026-10-04-tt-car-10pt5")
    p = MF.pinnacle_pair(cache, fixture_id=FID, contract=tt,
                         evaluated_ms=now)
    assert p["ok"] and p["key"] == "s;0;tt;home", p
    assert p["selection"] == "Carolina Panthers Over 10.5"
    # the other team's team total is a different market (line 20.5 there)
    p = MF.pinnacle_pair(cache, fixture_id=FID,
                         contract=dict(tt, designation="away"),
                         evaluated_ms=now)
    assert p["refusal"] == "LINE_DOES_NOT_MATCH"


def test_the_30s_rule_and_an_unobserved_change_still_refuse():
    c = _contract(NFL_SPREAD)
    now = time.time() * 1000.0
    stale = nfl_cache(change_age_s=45.0)
    p = MF.pinnacle_pair(stale, fixture_id=FID, contract=c, evaluated_ms=now)
    assert p["refusal"] == F.R_STALE
    unseen = nfl_cache(changed=False)
    p = MF.pinnacle_pair(unseen, fixture_id=FID, contract=c, evaluated_ms=now)
    assert p["refusal"] == F.R_NO_CHANGE_TIME, \
        "first sight in a snapshot is never a guessed age"


def test_a_pair_that_is_not_two_way_at_mirrored_points_is_refused():
    now = time.time() * 1000.0
    c = _contract(NFL_SPREAD)
    broken = copy.deepcopy(NFL_MARKETS)
    broken[1]["prices"][0]["points"] = 9.5       # home no longer +10.5
    p = MF.pinnacle_pair(nfl_cache(markets=broken), fixture_id=FID,
                         contract=c, evaluated_ms=now)
    assert p["refusal"] == MF.R_PAIR_NOT_MIRRORED
    one = copy.deepcopy(NFL_MARKETS)
    one[1]["prices"] = one[1]["prices"][1:]
    p = MF.pinnacle_pair(nfl_cache(markets=one), fixture_id=FID,
                         contract=c, evaluated_ms=now)
    assert p["refusal"] in (MF.R_PAIR_NOT_MIRRORED, MF.R_PAIR_INCOMPLETE)
    nofam = [m for m in NFL_MARKETS if m["type"] != "spread"]
    p = MF.pinnacle_pair(nfl_cache(markets=nofam), fixture_id=FID,
                         contract=c, evaluated_ms=now)
    assert p["refusal"] == MF.R_NO_PINNACLE_FAMILY


def test_the_recheck_refuses_a_moved_price():
    cache = nfl_cache()
    now = time.time() * 1000.0
    c = _contract(NFL_SPREAD)
    pair = MF.pinnacle_pair(cache, fixture_id=FID, contract=c,
                            evaluated_ms=now)
    assert MF.validate_pair(cache, pair, evaluated_ms=now)["ok"] is True
    moved = copy.deepcopy(NFL_MARKETS)
    moved[1]["prices"][0]["price"] = -120
    cache.apply({"type": "prematch_markets", "matchup_id": FID,
                 "sport_id": 5, "ts": now, "data": moved},
                epoch=cache.authority.epoch, received_ms=now)
    got = MF.validate_pair(cache, pair, evaluated_ms=now + 1)
    assert got == {"ok": False, "reason": MF.R_INPUT_CHANGED}


# ═════════════════════════════════════════════════════════════════════
# §5 THE DE-VIG ON THE PAIR'S OWN TWO OUTCOMES
# ═════════════════════════════════════════════════════════════════════

def test_each_line_is_devigged_on_its_own_pair_at_the_identical_line():
    cache = nfl_cache()
    now = time.time()
    c = _contract(NFL_SPREAD)
    pair = MF.pinnacle_pair(cache, fixture_id=FID, contract=c,
                            evaluated_ms=now * 1000.0)
    contract = {"sport_family": "football", "market": "spread",
                "selection": pair["selection"], "event_key": "pinnapi:1",
                "period": "FULL_GAME", "line": c["line"],
                "settlement_rule": MF.GP_FOOTBALL}
    quote = {"book": devig.BOOK, "outcomes": pair["outcomes"],
             "observed_at": pair["observed_at"], "event_key": "pinnapi:1",
             "period": "FULL_GAME", "line": pair["line"],
             "settlement_rule": MF.GP_FOOTBALL}
    v = devig.valuation(contract=contract, quote=quote, now=now)
    assert v["refusals"] == [] and v["expected_outcomes"] == 2
    names = sorted(pair["outcomes"])
    want = dict(zip(names, devig.devig([pair["outcomes"][n]
                                        for n in names])))
    assert v["probability"] == pytest.approx(want["Detroit Lions -10.5"])
    assert sum(v["devigged"].values()) == pytest.approx(1.0)
    # a different line on the quote is the de-vig's LINE_DOES_NOT_MATCH
    v = devig.valuation(contract=contract, quote=dict(quote, line=-11.5),
                        now=now)
    assert v["refusals"] == ["LINE_DOES_NOT_MATCH"]


def test_the_supported_line_set_is_exactly_the_proven_set():
    lines = {k for k in devig.SUPPORTED if k[1] in MF.LINE_FAMILIES}
    assert lines == set(MF.PROVEN)
    assert all(devig.SUPPORTED[k] == 2 for k in lines)
    for k in MF.NOT_PROVEN:
        assert k not in devig.SUPPORTED, k
    # the money lines are untouched
    assert devig.SUPPORTED[("baseball", "h2h")] == 2
    assert devig.SUPPORTED[("soccer", "h2h")] == 3
    assert ("football", "h2h") not in devig.SUPPORTED


# ═════════════════════════════════════════════════════════════════════
# §6 THE COMPLETED-GAME POLICY'S LINE MATCH
# ═════════════════════════════════════════════════════════════════════

def _cg_inputs(slug=NFL_SPREAD, *, text=None, line=None):
    from sportsassets.agents import derek_policy as DP
    parts = _participants(_event_of(slug))
    c = MF.market_contract(_rows(slug), participants=parts)
    text = MARKETS[slug]["description"] if text is None else text
    proof = MF.prove(contract=c, venue_text=text, participants=parts)
    sel = "Detroit Lions -10.5"
    row = {"id": 1, "experiment_id": "EXT_PINNACLE_DEVIG_V1_SHADOW",
           "venue": "PMUS", "condition_id": None, "us_market_slug": slug,
           "event_key": "pinnapi:%d" % FID, "buy_intent": MF.INTENT_LONG,
           "payout_event": sel, "probability_event": sel,
           "contract_selection": sel, "payout_is_complement": False,
           "sport_family": c["sport"], "market": c["family"],
           "line": c["line"] if line is None else line,
           "period": "FULL_GAME", "probability": 0.55,
           "provider": "pinnapi.com/raw-websocket", "outcome_books": 1,
           # what the lane writes on a PinnAPI-only line valuation: the
           # multi-book floor (exempted for PinnAPI's sole authority) and
           # the strict lane's exceptional-terms refusal
           "refusals": ["OUTCOME_DEPTH_BELOW_FLOOR",
                        MF.R_EXCEPTIONAL_DIFFER],
           "settlement_comparison": json.dumps({
               "compatibility": "INCOMPATIBLE_EXCEPTIONAL_TERMS",
               "blockers": [MF.R_EXCEPTIONAL_DIFFER],
               "line_market": {"contract": {k: c.get(k) for k in (
                   "market_slug", "sport", "family", "line", "designation",
                   "team", "sports_type")},
                   "participants": parts, "proof": proof},
               "venue_rules_text": text,
               "venue_rules_sha256": MF.rules_sha256(text)}),
           "decided_at": time.time(), "observed_at": time.time(),
           "received_at": time.time()}
    return DP.candidate_from_row(row), row


def test_the_completed_game_policy_matches_a_proven_line():
    from sportsassets.agents import paper_benchmark as PB
    cand, row = _cg_inputs()
    m = PB.completed_game_match(cand, row)
    assert m["established"] is True, m["refusals"]
    names = {c["check"]: c for c in m["checks"]}
    assert names["market_and_line"]["passed"]
    gp = names["ordinary_completion_grading_period"]
    assert gp["passed"] and gp["book"]["period"] == MF.GP_FOOTBALL
    exc = m["exceptional_terms"]
    assert exc["status"] == \
        "DISCLOSED_RESEARCH_RISK_NOT_SETTLEMENT_COMPATIBILITY"
    assert {d["condition"] for d in exc["line_divergences"]} >= {
        "POSTPONED_OR_SUSPENDED_AND_NOT_COMPLETED"}


def test_the_completed_game_policy_re_proves_and_refuses_by_name():
    from sportsassets.agents import paper_benchmark as PB
    text = MARKETS[NFL_SPREAD]["description"]
    cand, row = _cg_inputs(text=text.replace(
        "Overtime is included if played.", "Overtime is not included."))
    m = PB.completed_game_match(cand, row)
    assert m["established"] is False
    assert MF.R_VENUE_TEXT_CONFLICTS in m["refusals"]
    # a row whose line is not the contract's own
    cand, row = _cg_inputs(line=-9.5)
    m = PB.completed_game_match(cand, row)
    assert PB.R_MARKET in m["refusals"]
    # soccer: the family is not proven; the policy says why
    cand, row = _cg_inputs("asc-unl-cyp-lat-2026-10-05-neg-1pt5")
    m = PB.completed_game_match(cand, row)
    assert m["established"] is False
    assert MF.R_BOOK_MARKET_RULES_NOT_CAPTURED in m["refusals"]


# ═════════════════════════════════════════════════════════════════════
# §7 END TO END ON POSTGRES: WS CHANGE -> REAL CYCLE -> RUN LINE -> ENTER
# ═════════════════════════════════════════════════════════════════════
#
# The reactive harness of tests/test_pinnapi_reactive_integration (the same
# substitutions at the same transport boundaries: provider fetches, the
# venue's listing/book calls, venue pacing, the paper market-data transport),
# with the synthetic MLB fixture's RUN LINE listed beside its money line. The
# fixture is discovered PinnAPI-natively (no metered request), one WS frame
# moves the money line (no edge) and the run line (an edge), and the REAL
# cycle prices the run line on its own two-way pair at the identical line;
# the completed-game paper policy decides it with its own book read. ALL
# PRICES ARE SYNTHETIC. Scratch paper accounts only; no funded switch.

from sportsassets import bettor_paper_guard as G                 # noqa: E402
from sportsassets import bettor_paper_session as S               # noqa: E402
from sportsassets import pinnapi_discovery as PD                 # noqa: E402
from sportsassets import pinnapi_feed_runtime as FR              # noqa: E402
from sportsassets import pinnapi_reactive as R                   # noqa: E402
from sportsassets import pmus                                    # noqa: E402
from sportsassets import venue_pace                              # noqa: E402
from sportsassets.agents import paper_benchmark as PB            # noqa: E402
from sportsassets.agents import paper_derek as PDK               # noqa: E402
from sportsassets.agents import paper_runtime as PR              # noqa: E402
from sportsassets.agents import runtime as RT                    # noqa: E402
from sportsassets.workers import ext_pinnacle_loop as loop       # noqa: E402

from tests import paper_harness as H                             # noqa: E402
from tests import paper_live_fixture as PL                       # noqa: E402
from tests import test_pinnapi_reactive_integration as RI        # noqa: E402
from tests import test_venue_native_identity_matches_the_measured_fixture \
    as VN                                                        # noqa: E402

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

#: The venue's recorded MLB run-line wording (the listing of
#: asc-mlb-atl-lad-2026-10-04-neg-1pt5), with this SYNTHETIC fixture's teams
#: substituted -- the same substitution RI.VENUE_PROSE makes for the money line.
LINE_PROSE = (
    "This market will settle to Yes if the Colorado Rockies cover a -1.5 run "
    "spread in the Colorado Rockies vs Miami Marlins MLB game scheduled for "
    "the listed date. Extra innings are included if played. If the game is "
    "shortened but an official final result is declared, the market will "
    "settle based on that result. If the game is delayed, postponed, or "
    "suspended and not rescheduled to a date within two weeks of the "
    "originally scheduled date, the market will settle to the last fair "
    "market price. Outcome sourced from MLB.")


class _LineVenue:
    """RI._Venue over SEVERAL markets: the collector's listing / book client
    and the paper transport, over one book state per slug."""

    def __init__(self, books, prose):
        self.books, self.prose = books, prose
        self.calls = {"list": 0, "book": 0, "paper": 0}

    @property
    def markets(self):
        return self

    def list(self, params=None):
        self.calls["list"] += 1
        want = list((params or {}).get("slug") or [])
        return {"markets": [{"slug": s, "description": self.prose[s],
                             "marketType": "MARKET_TYPE_SPORTS",
                             "orderPriceMinTickSize": "0.01"}
                            for s in want if s in self.prose]}

    def book(self, slug):
        self.calls["book"] += 1
        if slug not in self.books:
            return {}
        bids, offers = self.books[slug]

        def lvl(p, q):
            return {"px": {"value": "%.2f" % p, "currency": "USD"},
                    "qty": str(q)}
        return {"marketData": {
            "offers": [lvl(p, q) for p, q in offers],
            "bids": [lvl(p, q) for p, q in bids],
            "transactTime": RI._dt.datetime.fromtimestamp(
                time.time() - 1.0, RI._dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"}}

    def retrieve_by_slug(self, slug):
        return {"market": {"slug": slug, "marketSides": []}}

    def __call__(self, slug):
        self.calls["paper"] += 1
        if slug not in self.books:
            return {"marketData": None, "error": "NO_BOOK_FIXTURE",
                    "observed_at": time.time()}
        bids, offers = self.books[slug]
        return {"marketData": H.md(bids=bids, offers=offers),
                "observed_at": time.time() - 1.0}


def _run_line(game):
    slug = "asc-%s-neg-1pt5" % game.event_slug
    side = {"identifier": slug}
    return slug, {
        "slug": slug, "sportsMarketType": "baseball_team_full_game_spread",
        "question": ("Will the Colorado Rockies cover -1.5 vs the Miami "
                     "Marlins in COL Rockies vs. MIA Marlins?"),
        "gameStartTime": RI._iso(game.start), "closed": False,
        "marketSides": [
            dict(side, description="-1.50", long=True,
                 team={"abbreviation": "col", "name": "Colorado Rockies",
                       "safeName": "Colorado", "league": "mlb", "id": 115}),
            dict(side, description="+1.50", long=False,
                 team={"abbreviation": "mia", "name": "Miami Marlins",
                       "safeName": "Miami", "league": "mlb", "id": 146})]}


def _ws_markets(ml, spread):
    """Pinnacle's full-game money line and run line for the fixture: the
    Rockies (Pinnacle's AWAY) give 1.5, the Marlins get it."""
    return [{"key": F.FULL_GAME_MONEYLINE_KEY, "type": "moneyline",
             "period": 0, "status": "open",
             "prices": [{"designation": "home", "price": ml[0]},
                        {"designation": "away", "price": ml[1]}]},
            {"key": "s;0;s;1.5", "type": "spread", "period": 0,
             "status": "open", "isAlternate": False,
             "prices": [{"designation": "home", "price": spread[0],
                         "points": 1.5},
                        {"designation": "away", "price": spread[1],
                         "points": -1.5}]}]


@pytest.fixture
async def line_env(monkeypatch, new_strategies_off):
    import random
    import types
    e = types.SimpleNamespace()
    e.t0 = time.time()
    e.conn = await H.connect()
    e.pool = e.task = None
    e.game = VN.Game()
    e.eid = random.randint(2 * 10**8, 3 * 10**8)
    e.line_slug, line_market = _run_line(e.game)
    await RI._ensure_schema(e.conn)
    await RI._clean(e.conn, e.game, e.eid)
    await e.conn.execute("DELETE FROM external_valuations "
                         " WHERE us_market_slug=$1", e.line_slug)
    await VN.seed_venue(e.conn, e.game)
    ev = e.game.venue_event()
    keys = pm.event_keys_for(ev["title"], ev["slug"])
    for r in pm._market_rows(ev, line_market):
        await pm._upsert(e.conn, r, pm.keys_for_row(keys, r))
    e.acct = await PL.new_account(e.conn, "linelane", now=e.t0)
    e.venue = _LineVenue(
        books={e.game.us_slug: ([(0.50, 400)], [(0.52, 400)]),
               e.line_slug: ([(0.40, 400)], [(0.52, 400)])},
        prose={e.game.us_slug: RI.VENUE_PROSE, e.line_slug: LINE_PROSE})
    monkeypatch.setattr(PR, "DEFAULT_ACCOUNT_ID", e.acct["account_id"])
    monkeypatch.setitem(PR._CLIENT, "client",
                        G.PaperMarketDataClient(e.venue))
    monkeypatch.setattr(RT, "paper_pass_hook",
                        lambda **kw: {"scheduled": False})
    PDK._CONTEXT_CACHE.clear()
    PB._CONTEXT_CACHE.clear()
    monkeypatch.setenv(S.ENV_FLAG, "on")
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv("PINNAPI_REACTIVE_PAPER", "on")
    e.controls = {}
    for key, on in ((PB.CG_POLICY["control_key"], True),
                    (PB.CONTROL_KEY, False)):
        e.controls[key] = await e.conn.fetchval(
            "SELECT enabled FROM paper_control WHERE control_key=$1", key)
        await e.conn.execute("UPDATE paper_control SET enabled=$2 WHERE "
                             " control_key=$1", key, on)
    e.fetches = []
    VN.substitute(monkeypatch, slugs=[e.game.us_slug],
                  odds_by_sport={"baseball_mlb": lambda now: []})
    real_fetch = loop.fetch_odds

    async def counted_fetch(sport_key, **kw):
        e.fetches.append(sport_key)
        return await real_fetch(sport_key, **kw)
    monkeypatch.setattr(loop, "fetch_odds", counted_fetch)
    monkeypatch.setattr(pmus, "_get_client", lambda: e.venue)
    monkeypatch.setattr(venue_pace, "pace", lambda *a, **k: 0.0)
    loop.rules_cache_reset()
    # the feed this process owns: the fixture first seen in a snapshot
    e.cache = F.FeedCache()
    ep = e.cache.new_connection([("prematch", RI.SPORT_ID)])
    now = time.time()
    e.cache.apply({"type": "snapshot", "stream": "prematch",
                   "sport_id": RI.SPORT_ID, "ts": (now - 60) * 1000,
                   "events": [{"id": e.eid, "startTime": e.game.commence,
                               "isLive": False, "units": "Regular",
                               "league": {"id": 246, "name": "MLB"},
                               "participants": [
                                   {"name": VN.HOME, "alignment": "home"},
                                   {"name": VN.AWAY, "alignment": "away"}],
                               "markets": _ws_markets((120, -140),
                                                      (130, -150))}]},
                  epoch=ep, received_ms=(now - 60) * 1000 + 5)
    assert e.cache.authority.synced
    monkeypatch.setitem(FR._STATE, "owner",
                        types.SimpleNamespace(cache=e.cache))
    monkeypatch.setitem(FR._STATE, "runtime_id", RI.RUNTIME_ID)
    monkeypatch.setattr(R, "ACTIVE", None)
    try:
        yield e
    finally:
        try:
            await R.stop(e.task)
        finally:
            if e.pool is not None:
                await e.pool.close()
            loop.rules_cache_reset()
            for key, was in e.controls.items():
                if was is not None:
                    await e.conn.execute(
                        "UPDATE paper_control SET enabled=$2 WHERE "
                        " control_key=$1", key, was)
            await PL.drop_today_run(e.conn, e.t0)
            async with e.conn.transaction():
                await e.conn.execute(
                    "SET LOCAL session_replication_role = replica")
                await e.conn.execute(
                    "DELETE FROM external_valuations WHERE "
                    " us_market_slug=$1", e.line_slug)
                await e.conn.execute(
                    "DELETE FROM venue_fixture_event_keys WHERE "
                    " venue_event_slug=$1", e.game.event_slug)
            await RI._clean(e.conn, e.game, e.eid)
            await e.conn.close()


@pg
async def test_a_ws_change_prices_the_run_line_and_the_policy_enters_it(
        line_env):
    import asyncpg
    e = line_env
    e.pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=4)
    e.task = R.start(e.pool, cycle=loop.cycle)
    assert e.task is not None
    # PINNAPI-NATIVE DISCOVERY: the venue's own team records, both contracts
    rows = [dict(r) for r in await e.conn.fetch(
        PD.venue_events_sql([RI.SPORT_ID]))]
    out = PD.discover(e.cache.events, rows, sport_ids=[RI.SPORT_ID])
    (rcpt,) = [r for r in out["receipts"] if r["fixture_id"] == e.eid]
    assert rcpt["state"] == PD.MATCHED, rcpt
    assert rcpt["venue_records"]["away"]["team_id"] == 115     # Rockies
    assert out["by_venue_event"][e.game.event_slug]["fixture_id"] == e.eid
    (seed,) = [s for s in out["seeds"]
               if s["pinnapi_native"]["fixture_id"] == e.eid]
    assert R.register(seed, sport_key=PD.sport_key_for("baseball"),
                      family="baseball", received_at=time.time(),
                      native=True) == "SEEDED"
    # ONE WS FRAME: the money line moves to even (no edge at 0.52), the run
    # line moves to Rockies -1.5 at -170 / Marlins +1.5 at +150 (~0.61)
    now = time.time()
    e.cache.apply({"type": "prematch_markets", "matchup_id": e.eid,
                   "sport_id": RI.SPORT_ID, "ts": (now - 0.2) * 1000,
                   "data": _ws_markets((-105, -105), (150, -170))},
                  epoch=e.cache.authority.epoch, received_ms=now * 1000)
    attempts = await RI._wait_terminal(e)
    assert [a["state"] for a in attempts] == ["COMPLETED"], attempts
    detail = attempts[0]["detail"]
    assert detail["deadline_s"] == R.ACTIVE.deadline
    lm = detail["result"]["line_markets"]
    assert lm["by_state"].get("PINNACLE_LINE_MATCHED_MAIN") == 1, lm
    assert lm["instruments_evaluated"] == 2, lm
    assert lm["by_sport_family_state"][
        "baseball|spread|LINE_VALUATION_RECORDED_CALIBRATION_ONLY"] == 2, lm
    assert e.fetches == [], "no metered discovery request"

    vs = {v["contract_selection"]: dict(v) for v in await e.conn.fetch(
        "SELECT * FROM external_valuations WHERE us_market_slug=$1 "
        " ORDER BY id", e.line_slug)}
    assert set(vs) == {"Colorado Rockies -1.5", "Miami Marlins +1.5"}
    long_ = vs["Colorado Rockies -1.5"]
    assert (long_["market"], long_["line"], long_["sport_family"]) == \
        ("spread", -1.5, "baseball")
    assert long_["buy_intent"] == MF.INTENT_LONG
    assert long_["provider"] == "pinnapi.com/raw-websocket"
    assert vs["Miami Marlins +1.5"]["buy_intent"] == MF.INTENT_SHORT
    # the pair's OWN two-way de-vig at the identical line
    pair = {"Colorado Rockies -1.5": F.american_to_decimal(-170),
            "Miami Marlins +1.5": F.american_to_decimal(150)}
    names = sorted(pair)
    want = dict(zip(names, devig.devig([pair[n] for n in names])))
    assert long_["probability"] == pytest.approx(
        want["Colorado Rockies -1.5"])
    assert vs["Miami Marlins +1.5"]["probability"] == pytest.approx(
        want["Miami Marlins +1.5"])
    assert MF.R_EXCEPTIONAL_DIFFER in long_["refusals"]
    scmp = H.j(long_["settlement_comparison"])
    assert scmp["line_market"]["proof"]["established"] is True
    assert scmp["line_market"]["proof"]["period"] == MF.GP_BASEBALL
    assert scmp["reference_input"]["decision_check"]["ok"] is True
    assert scmp["reference_input"]["market_key"] == "s;0;s;1.5"
    # one fixture: the money line's and the run line's event keys are one
    h2h = await e.conn.fetchval(
        "SELECT event_key FROM external_valuations WHERE us_market_slug=$1"
        " ORDER BY id LIMIT 1", e.game.us_slug)
    assert long_["event_key"] == h2h == "pinnapi:%d" % e.eid

    ds = {d["valuation_id"]: d for d in RI._cg(await RI._decisions(
        e, [v["id"] for v in vs.values()]))}
    d_long = ds[long_["id"]]
    assert d_long["verdict"] == "ENTER", (d_long["refusal"],
                                          d_long["refusals"])
    orders = await e.conn.fetchval(
        "SELECT count(*) FROM paper_orders WHERE decision_id = $1",
        d_long["decision_id"])
    assert orders == 1
    # the short side carries no edge at 1 - 0.40 against ~0.39: refused on
    # the economics, never on the line's settlement
    d_short = ds[vs["Miami Marlins +1.5"]["id"]]
    assert d_short["verdict"] != "ENTER"
    assert not any("LINE" in str(x) or "GRADING" in str(x)
                   for x in (d_short["refusals"] or [])), d_short["refusals"]


# ═════════════════════════════════════════════════════════════════════
# §8 THE LINE CENSUS AND THE LANE'S BOUNDS
# ═════════════════════════════════════════════════════════════════════

NFL_EVENT = "nfl-det-car-2026-10-04"
NFL_SLUGS = [s for s in MARKETS if "-nfl-det-car-" in s]


def _nfl_rows():
    out = []
    for slug in NFL_SLUGS:
        for r in _rows(slug):
            out.append(dict(r, event_slug=NFL_EVENT))
    return out


def test_the_census_counts_every_line_contract_of_a_matched_fixture():
    ident = {NFL_EVENT: {"fixture_id": FID,
                         "venue_records": _participants(NFL_SPREAD)}}
    got = MF.census(_nfl_rows(), ident, nfl_cache(),
                    now_ms=time.time() * 1000.0)
    assert got["venue_events"] == 1 and got["contracts"] == len(NFL_SLUGS)
    s = got["by_sport_family_state"]
    assert s["football|spread|EXACT_PINNACLE_LINE_FRESH_NOW_MAIN"] == 1
    assert s["football|spread|EXACT_PINNACLE_LINE_FRESH_NOW_ALTERNATE"] == 1
    assert s["football|total|EXACT_PINNACLE_LINE_FRESH_NOW_MAIN"] == 1
    assert s["football|team_total|EXACT_PINNACLE_LINE_FRESH_NOW_MAIN"] == 1
    # held but unchanged for longer than the 30 s rule: counted apart, never
    # read as "no Pinnacle line"
    old = MF.census(_nfl_rows(), ident, nfl_cache(change_age_s=90.0),
                    now_ms=time.time() * 1000.0)
    assert old["states"].get("EXACT_PINNACLE_LINE_HELD_NOT_FRESH_NOW_MAIN") \
        == 3
    # a fixture discovery did not match prices nothing; its contracts are
    # counted by the contract's own refusal (no participants to bind)
    none = MF.census(_nfl_rows(), {}, nfl_cache(),
                     now_ms=time.time() * 1000.0)
    assert none["contracts"] == len(NFL_SLUGS)
    assert none["states"].get(MF.R_CONTRACT_TEAM) == 2


def test_a_metered_event_finds_its_fixture_through_the_discovery_index(
        monkeypatch):
    from sportsassets import pinnapi_discovery as PD
    from sportsassets import pinnapi_feed_runtime as FR
    from sportsassets.workers import ext_pinnacle_loop as loop
    ident = {"fixture_id": FID, "venue_records": _participants(NFL_SPREAD),
             "venue_event_slug": NFL_EVENT}
    monkeypatch.setitem(FR._STATE, "discovery",
                        {"by_venue_event": {NFL_EVENT: ident}})
    assert PD.identity_for(NFL_EVENT)["fixture_id"] == FID
    assert PD.identity_for("nfl-x-y-2026-10-04") is None
    ev = {"id": "odds-1", "home_team": "Carolina Panthers",
          "away_team": "Detroit Lions"}
    job = loop.line_job_for(ev, sport_key="americanfootball_nfl",
                            family="football", venue_event_slug=NFL_EVENT)
    assert job["identity"]["fixture_id"] == FID
    assert job["provider_event_id"] == "odds-1"
    assert loop.line_job_for(ev, sport_key="x", family="football",
                             venue_event_slug=None) is None


@pg
async def test_the_lane_defers_by_name_and_never_past_a_ws_deadline(
        monkeypatch):
    import types as _t
    from sportsassets.workers import ext_pinnacle_loop as loop
    conn = await H.connect()
    tx = conn.transaction()
    await tx.start()
    try:
        await pm._ensure_table(conn)
        ev_slug = "nfl-det-car-r30a-%d" % int(time.time())
        for r in _nfl_rows():
            r = dict(r, event_slug=ev_slug)
            await pm._upsert(conn, r, [])
        cache = nfl_cache()
        monkeypatch.setitem(FR._STATE, "owner", _t.SimpleNamespace(
            cache=cache))
        ident = {"fixture_id": FID,
                 "venue_records": _participants(NFL_SPREAD)}
        job = loop.line_job_for({"id": "pinnapi:%d" % FID},
                                sport_key="pinnapi_football",
                                family="football", venue_event_slug=ev_slug,
                                identity=ident)
        common = dict(fee_fn=lambda *a, **k: 0.0, open_book=[],
                      ev_measurable=None, calibration=None, research={})
        # a WS evaluation whose deadline is unknown never reads a line
        rep = await loop.line_market_pass(
            conn, jobs=[job], stream_seed={"trigger": {},
                                           "valuation_ids": []}, **common)
        assert rep["instrument_cap"] == 0
        # 4 exact Pinnacle lines (main spread, alt spread, total, team total)
        # x 2 sides
        assert rep["instruments_eligible"] == 8, rep
        assert rep["by_state"][loop.R_LINE_DEFERRED_WS] == 8
        assert rep["venue_reads"] == 0
        # a deadline already spent: deferred by name, nothing read
        rep = await loop.line_market_pass(
            conn, jobs=[job], stream_seed={
                "trigger": {"evaluation_started_at": time.time() - 11.0,
                            "deadline_s": 12}, "valuation_ids": []},
            **common)
        assert rep["instrument_cap"] == loop.MAX_LINE_INSTRUMENTS_PER_WS_EVALUATION
        assert rep["by_state"][loop.R_LINE_DEFERRED_WS] == 8
        assert rep["venue_reads"] == 0
        # the lane's own counts name every contract's state
        assert rep["by_sport_family_state"][
            "football|spread|PINNACLE_LINE_MATCHED_MAIN"] == 1
        assert rep["by_sport_family_state"][
            "football|spread|PINNACLE_LINE_MATCHED_ALTERNATE"] == 1
        # a fixture discovery never matched: no line is read, said so
        rep = await loop.line_market_pass(
            conn, jobs=[dict(job, identity=None)], stream_seed=None,
            **common)
        assert rep["by_state"] == {loop.R_LINE_NO_IDENTITY: 1}
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_the_census_read_feeds_the_census_on_postgres():
    from sportsassets import pinnapi_discovery as PD
    conn = await H.connect()
    tx = conn.transaction()
    await tx.start()
    try:
        await pm._ensure_table(conn)
        ev_slug = "nfl-det-car-r30a-c-%d" % int(time.time())
        for r in _nfl_rows():
            await pm._upsert(conn, dict(r, event_slug=ev_slug), [])
        rows = [dict(r) for r in await conn.fetch(
            PD.line_rows_sql(), [ev_slug], list(MF.VENUE_LINE_TYPES),
            int(PD.RESEEN_WITHIN_S), int(PD.MAX_LINE_ROWS))]
        assert len(rows) == 2 * len(NFL_SLUGS)
        got = MF.census(rows, {ev_slug: {
            "fixture_id": FID, "venue_records": _participants(NFL_SPREAD)}},
            nfl_cache(), now_ms=time.time() * 1000.0)
        assert got["states"]["EXACT_PINNACLE_LINE_FRESH_NOW_MAIN"] == 3
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_the_discovery_pass_seeds_and_counts_the_line_contracts(
        line_env, monkeypatch):
    """`pinnapi_feed_runtime._discovery_once` on Postgres: one bounded venue
    read matches the fixture, a second counts its line contracts against the
    cache (the census), and the heartbeat digest stays bounded."""
    import asyncpg
    import types as _t
    e = line_env
    e.pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=2)
    monkeypatch.setitem(FR._STATE, "owner", _t.SimpleNamespace(
        cache=e.cache, sport_ids=[RI.SPORT_ID]))
    now = time.time()
    e.cache.apply({"type": "prematch_markets", "matchup_id": e.eid,
                   "sport_id": RI.SPORT_ID, "ts": (now - 0.2) * 1000,
                   "data": _ws_markets((-105, -105), (150, -170))},
                  epoch=e.cache.authority.epoch, received_ms=now * 1000)
    out = await FR._discovery_once(e.pool)
    assert out["states"][PD.MATCHED] >= 1
    assert e.game.event_slug in out["by_venue_event"]
    # no scheduler in this process: every seed says so by name
    assert out["registered"].get("NO_SCHEDULER", 0) >= 1
    lc = out["line_census"]
    assert lc["rows_truncated"] is False
    assert lc["by_sport_family_state"][
        "baseball|spread|EXACT_PINNACLE_LINE_FRESH_NOW_MAIN"] == 1, lc
    dg = PD.digest(out)
    assert "receipts" not in dg and "by_venue_event" not in dg
    assert dg["line_census"]["contracts"] >= 1
    json.dumps(dg, default=str)


@pg
async def test_the_scheduled_cycle_prices_a_metered_events_line_contracts(
        line_env, monkeypatch):
    """The SCHEDULED cycle (no WS trigger): a metered discovery event the
    venue-native resolver maps reaches the line lane through the PinnAPI
    discovery index of its venue event, under the cycle's own bound."""
    import types as _t
    e = line_env
    rows = [dict(r) for r in await e.conn.fetch(
        PD.venue_events_sql([RI.SPORT_ID]))]
    disc = PD.discover(e.cache.events, rows, sport_ids=[RI.SPORT_ID])
    assert e.game.event_slug in disc["by_venue_event"]
    monkeypatch.setitem(FR._STATE, "discovery", disc)
    VN.substitute(monkeypatch, slugs=[e.game.us_slug],
                  odds_by_sport={"baseball_mlb": lambda now: [
                      VN.odds_event(e.game, "ln%d" % e.eid, at=now)]})
    monkeypatch.setattr(pmus, "_get_client", lambda: e.venue)
    monkeypatch.setitem(FR._STATE, "owner", _t.SimpleNamespace(
        cache=e.cache))
    now = time.time()
    e.cache.apply({"type": "prematch_markets", "matchup_id": e.eid,
                   "sport_id": RI.SPORT_ID, "ts": (now - 0.2) * 1000,
                   "data": _ws_markets((-105, -105), (150, -170))},
                  epoch=e.cache.authority.epoch, received_ms=now * 1000)
    out = await loop.cycle(e.conn)
    assert out.get("ran") is True, out.get("why")
    lm = out["line_markets"]
    assert lm["instrument_cap"] == loop.MAX_LINE_INSTRUMENTS_PER_CYCLE
    assert lm["jobs"] == 1 and lm["instruments_evaluated"] == 2, lm
    assert lm["by_sport_family_state"][
        "baseball|spread|LINE_VALUATION_RECORDED_CALIBRATION_ONLY"] == 2
    vals = await e.conn.fetch(
        "SELECT contract_selection, event_key FROM external_valuations "
        " WHERE us_market_slug=$1", e.line_slug)
    assert {v["contract_selection"] for v in vals} == {
        "Colorado Rockies -1.5", "Miami Marlins +1.5"}
    h2h = await e.conn.fetchval(
        "SELECT event_key FROM external_valuations WHERE us_market_slug=$1"
        " ORDER BY id LIMIT 1", e.game.us_slug)
    assert {v["event_key"] for v in vals} == {h2h}, "one fixture, one key"
