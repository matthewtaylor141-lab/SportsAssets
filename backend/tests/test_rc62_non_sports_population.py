"""RC6.2 LANE p-coverage (REWORK): NO SPORTS MARKET LEAVES THE POPULATION BY
ITS VENUE CODE.

THE DEFECT (in RC6.1, b3f1b0cd, the release candidate). market_plane.populate.
contract_row dropped every catalogue market whose venue league code was in
ontology.NON_SPORTS_LEAGUES BEFORE it read the market type. RC6.1 began to
read that code off the event slug's FIRST segment, which matched `gtasc` --
a code the set listed as non-sports and the venue uses only for Guatemalan
league SOCCER. Production, 2026-10-09T17:26Z (research-sql run 37966271096,
tests/fixtures/pmus_non_sports_population_2026_10_09.json):

  P1  gtasc: 6 catalogue markets, every one typed soccer_team_full_time_
      winner, 6 listed active (Quiche FC vs. Huehuetecos FC, Nueva Santa
      Rosa Cdf vs. San Benito FC); every other code of the set lists only
      untyped or `futures` rows (BTC, central banks, CPI, Nobel, Netflix,
      temperature), and `box` / `pol` list nothing at all
  P4  production (732cc0c6, second-segment code) holds those 6 gtasc rows as
      active soccer money lines with no ontology gap -- RC6.1 removes them
  G1  a PinnAPI-valued gtasc soccer money line (atc-gtasc-sac-dep-2026-10-07-
      sac, "CSD Sacachispas vs. Deportivo Fraijanes"; run 37946119133)

THE FIX. (1) gtasc reads soccer (LEAGUE_SPORT) and is no longer in the set.
(2) ONE exclusion rule, ontology.excluded_as_non_sports: the code must be in
the set AND the market's own type must name no sport -- a typed soccer /
baseball / ... row is never dropped by its code (the rule `pdc` already
follows). (3) A HELD or CANDIDATE market is never excluded: it is required,
and it keeps its own catalogue row rather than the NOT_IN_CURRENT_CATALOGUE
stub the required pass writes for a market the catalogue does not list.
The waterfall and the venue's own active count read the same rule.

Nothing here can make a contract PROVEN or PRICEABLE; the change only puts
sports markets back in the denominator they were removed from.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import pathlib
import time

import pytest

from sportsassets.market_plane import ontology as O
from sportsassets.market_plane import populate as POP
from sportsassets.market_plane import waterfall as WF

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
PROD = json.loads((FIX / "pmus_non_sports_population_2026_10_09.json")
                  .read_text())
GTASC = [dict(zip(PROD["P2_gtasc_markets"]["columns"], r))
         for r in PROD["P2_gtasc_markets"]["rows"]]
P1 = [dict(zip(PROD["P1_non_sports_codes"]["columns"], r))
      for r in PROD["P1_non_sports_codes"]["rows"]]
G1 = PROD["G1_valued_gtasc_money_line"]
NOW = time.time()
DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


def _cat(market_slug, event_slug, sports_type, *, title=None,
         state="PREGAME"):
    """A catalogue row (CATALOGUE_SQL's shape) as the venue lists it."""
    return {"market_slug": market_slug, "event_slug": event_slug,
            "event_title": title, "sports_type": sports_type,
            "listing_state": state, "updated_at": NOW,
            "game_start": NOW + 20 * 3600, "sides": []}


def _p1_type(head):
    return None if head == "<none>" else head


# ═════════════════════════════════════════════════════════════════════
# 1. gtasc IS SOCCER: every market production lists under it is a row
# ═════════════════════════════════════════════════════════════════════

def test_the_production_gtasc_markets_are_all_typed_soccer():
    assert len(GTASC) == 6
    assert {r["sports_type"] for r in GTASC} == {
        "soccer_team_full_time_winner"}
    row = next(r for r in P1 if r["code"] == "gtasc")
    assert (row["names_sport"], row["head"], row["markets"],
            row["listed_active"]) == (True, "soccer", 6, 6)


@pytest.mark.parametrize("m", GTASC, ids=lambda m: m["market_slug"])
def test_every_listed_gtasc_market_is_a_soccer_registry_row(m):
    c = POP.contract_row(_cat(m["market_slug"], m["event_slug"],
                              m["sports_type"], title=m["title"]), now=NOW)
    assert c is not None, "a listed soccer money line left the registry"
    assert (c["sport"], c["competition"], c["family"], c["period"]) == (
        "soccer", "gtasc", "WINNER", "FULL_EVENT")
    assert c["ontology"]["gaps"] == [] and c["active"] is True
    assert c["event_id"] == m["event_slug"]


def test_the_valued_gtasc_money_line_stays_in_the_population():
    c = POP.contract_row(_cat(G1["market_slug"], G1["event_slug"],
                              G1["sports_type"], title=G1["title"]), now=NOW)
    assert c is not None and c["sport"] == "soccer"
    assert c["ontology"]["sport_basis"] == "VENUE_MARKET_TYPE"


def test_an_untyped_gtasc_market_reads_soccer_from_its_code():
    c = POP.contract_row(_cat("gtasc-apertura-2026-12-20-champ",
                              "gtasc-apertura-2026-12-20", "futures"),
                         now=NOW)
    assert c is not None and c["sport"] == "soccer"
    assert c["ontology"]["sport_basis"] == "VENUE_LEAGUE_CODE"
    assert "gtasc" in O.LEAGUE_SPORT and "gtasc" not in O.NON_SPORTS_LEAGUES


# ═════════════════════════════════════════════════════════════════════
# 2. THE ONE RULE: a code excludes only a row whose type names no sport
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("code", sorted(O.NON_SPORTS_LEAGUES))
@pytest.mark.parametrize("mt", ("soccer_team_full_time_winner",
                                "baseball_team_full_game_winner",
                                "table_tennis_match_winner",
                                "basketball_game_total_points"))
def test_a_sports_typed_row_is_never_dropped_by_its_code(code, mt):
    ev = "%s-aaa-bbb-2026-10-10" % code
    c = POP.contract_row(_cat("atc-%s-a" % ev, ev, mt), now=NOW)
    assert c is not None, "%s row dropped by code %s" % (mt, code)
    assert c["sport"] == O.market_type_sport(mt)[0]
    assert O.excluded_as_non_sports(code, mt) is False


@pytest.mark.parametrize("row", [r for r in P1 if not r["names_sport"]],
                         ids=lambda r: r["code"])
def test_the_production_non_sports_rows_are_still_excluded(row):
    """Every untyped / futures row of the set production lists (P1)."""
    mt = _p1_type(row["head"])
    ev = row["sample_event"]
    assert POP.contract_row(_cat("m-" + ev, ev, mt), now=NOW) is None
    assert O.excluded_as_non_sports(row["code"], mt) is True


def test_a_type_naming_no_sport_does_not_rescue_a_non_sports_code():
    for mt in (None, "", "futures", "moneyline", "crypto_price_range"):
        assert O.excluded_as_non_sports("btc", mt) is True
    # a code outside the set is never excluded, typed or not
    for mt in (None, "futures", "soccer_team_full_time_winner"):
        assert O.excluded_as_non_sports("epl", mt) is False
        assert O.excluded_as_non_sports(None, mt) is False


def test_market_type_sport_is_parse_market_types_sport():
    """The shared head reader is the one parse_market_type uses."""
    types = ["soccer_team_full_time_winner", "table_tennis_set_2_winner",
             "tennis_set_2_winner", "americanfootball_x", "icehockey_y",
             "nflx_thing", "mlb", "f1_race_winner", "futures", "moneyline",
             None, "", "ufc_method_of_victory", "esports_map_winner_1",
             "Soccer-Team-Full-Time-Winner"]
    for mt in types:
        p = O.parse_market_type(venue="POLYMARKET_US", contract_id="x",
                                sports_market_type=mt)
        want = p["meaning"]["sport"] if p["sport_basis"] == \
            "VENUE_MARKET_TYPE" else None
        assert O.market_type_sport(mt)[0] == want, mt


# ═════════════════════════════════════════════════════════════════════
# 3. A REQUIRED MARKET IS NEVER EXCLUDED
# ═════════════════════════════════════════════════════════════════════

def test_a_held_market_keeps_its_own_catalogue_row_whatever_its_code():
    """No open PAPER position sits on a set code today (P3: 0 rows); the
    rule is structural: a held market is required, so it is in the
    registry with its catalogue row, never the NOT_IN_CURRENT_CATALOGUE
    stub the required pass writes for an unlisted one."""
    ev = "btc-updown-1h-2026-10-09-1700z"
    slug = "cpc-%s-up" % ev
    assert POP.contract_row(_cat(slug, ev, None), now=NOW) is None
    held = POP.contract_row(_cat(slug, ev, None), now=NOW,
                            held=frozenset({slug}))
    assert held is not None
    assert (held["priority"], held["required_reason"]) == (
        POP.P_HELD, "OPEN_PAPER_POSITION")
    assert held["event_id"] == ev and held["active"] is True
    assert "NOT_IN_CURRENT_CATALOGUE" not in held["ontology"]["gaps"]
    cand = POP.contract_row(_cat(slug, ev, None), now=NOW,
                            candidates=frozenset({slug}))
    assert cand is not None and \
        cand["required_reason"] == "EVALUATED_CANDIDATE"


# ═════════════════════════════════════════════════════════════════════
# 4. THE WATERFALL READS THE SAME RULE
# ═════════════════════════════════════════════════════════════════════

def test_the_waterfall_never_names_a_sports_typed_row_non_sports():
    for m in GTASC:
        c = POP.contract_row(_cat(m["market_slug"], m["event_slug"],
                                  m["sports_type"]), now=NOW)
        k = WF.classify(c)
        assert k["family"] != WF.X_NON_SPORTS
        assert (k["tier"], k["family"]) == (WF.TARGET_A, WF.MONEYLINE)
    typed = POP.contract_row(_cat("atc-temp-a-b-2026-10-10-a",
                                  "temp-a-b-2026-10-10",
                                  "soccer_team_full_time_winner"), now=NOW)
    assert WF.classify(typed)["family"] == WF.MONEYLINE
    held = POP.contract_row(_cat("cpc-btc-x-1", "btc-x", None), now=NOW,
                            held=frozenset({"cpc-btc-x-1"}))
    assert (WF.classify(held)["tier"], WF.classify(held)["family"]) == (
        WF.EXCLUDED, WF.X_NON_SPORTS)


# ═════════════════════════════════════════════════════════════════════
# 5. ON POSTGRES: the pass, the venue count and the SQL head test
# ═════════════════════════════════════════════════════════════════════

#: every market type the production fixtures hold, plus the edge cases
def _all_types():
    seg = json.loads((FIX / "pmus_market_types_and_segment_rules_2026_10_09"
                      ".json").read_text())
    ts = {r[0] for r in seg["market_types"]}
    ts |= {"table_tennis", "table_tennisx", "tabletennis_x", "tennis",
           "tennisx_y", "nflx", "f1", "f12_y", "ufc", "Soccer-Team-Winner",
           " soccer_team_winner ", "moneyline", "futures", "", "mlb_",
           "americanfootball_spread", "icehockey_total", "efootball_x",
           "pickleball_match_winner", "crypto_up"}
    return sorted(ts)


@pg
def test_on_postgres_the_sql_head_test_is_the_python_one():
    import asyncpg

    async def go():
        c = await asyncpg.connect(DSN)
        try:
            for mt in _all_types():
                got = await c.fetchval(
                    "SELECT replace(lower(btrim(coalesce($1::text, ''))), "
                    "'-', '_') ~ $2", mt, O.NAMES_SPORT_SQL_REGEX)
                assert got == (O.market_type_sport(mt)[0] is not None), mt
        finally:
            await c.close()
    asyncio.run(go())


async def _seed(c, slug, *, event, sports_type, title=None):
    now = _dt.datetime.now(_dt.timezone.utc)
    for intent, side in (("ORDER_INTENT_BUY_LONG", "a"),
                         ("ORDER_INTENT_BUY_SHORT", "b")):
        await c.execute(
            "INSERT INTO us_premap (identifier, event_slug, event_title, "
            " market_slug, kind, side_norm, intent, sports_type, "
            " listing_state, listing_state_source, updated_at, game_start) "
            " VALUES ($1,$2,$3,$4,'side',$5,$6,$7,'PREGAME', "
            " 'VENUE_LIVE_FLAG', $8, $9)",
            "%s-%s" % (slug, side), event, title, slug, side, intent,
            sports_type, now, now + _dt.timedelta(hours=20))


@pg
def test_on_postgres_a_full_pass_keeps_gtasc_and_counts_it_as_the_venue_does(
        monkeypatch):
    import asyncpg
    from sportsassets.workers import universal_market_plane as W
    btc_held = "cpc-btc-updown-1h-2026-10-09-1700z-up"

    async def held_read(conn):
        return {btc_held}, set(), True
    monkeypatch.setattr(POP, "required_sets_read", held_read)

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            await c.execute("DELETE FROM us_premap")
            for m in GTASC:
                await _seed(c, m["market_slug"], event=m["event_slug"],
                            sports_type=m["sports_type"], title=m["title"])
            await _seed(c, "cpc-btc-range-hr-2026-10-09-0200z-1",
                        event="btc-range-hr-2026-10-09-0200z",
                        sports_type=None)
            await _seed(c, "m-nobel-peace-2026-10-09",
                        event="nobel-peace-2026-10-09", sports_type="futures")
            await _seed(c, btc_held, event="btc-updown-1h-2026-10-09-1700z",
                        sports_type=None)
            now = time.time()
            pop = await POP.populate(c, since=0.0, now=now, full=True)
            assert pop["excluded"] == {"btc": 1, "nobel": 1}
            assert "gtasc" not in pop["excluded"]
            reg = {r["contract_id"]: dict(r) for r in await c.fetch(
                "SELECT contract_id, sport, competition, event_id, ontology, "
                "       required_reason, priority FROM market_plane_registry "
                " WHERE active")}
            for m in GTASC:
                r = reg[m["market_slug"]]
                assert (r["sport"], r["competition"], r["event_id"]) == (
                    "soccer", "gtasc", m["event_slug"])
            # the held BTC market is its catalogue row, not a stub
            h = reg[btc_held]
            assert (h["required_reason"], h["priority"], h["event_id"]) == (
                "OPEN_PAPER_POSITION", POP.P_HELD,
                "btc-updown-1h-2026-10-09-1700z")
            assert "NOT_IN_CURRENT_CATALOGUE" not in json.loads(
                h["ontology"])["gaps"]
            assert pop["required_added"] == 0
            # the venue's own active count: the 6 soccer markets, none of
            # the 3 untyped non-sports ones
            assert await W.venue_active_count(c, now=now) == len(GTASC)
        finally:
            await tr.rollback()
            await c.close()
    asyncio.run(go())
