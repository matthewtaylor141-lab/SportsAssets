"""C7 (2026-09-07): the game by the venue's kickoff instant, the club by
the venue's team record. The venue dates his Liga Portugal games
2026-09-05 where his slugs say 2026-09-06 and names the clubs otherwise,
so every date-keyed arm refuses by design (test_c6_names.py, the Liga
Portugal class); what both sides STATE is the instant -- the venue's
gameStartTime on every market, his CLOB record's game_start_time in
market_starts -- and the venue's own team record binds code -> club.

The venue's rows are the engine-diagnostic PREMAP-TEAM read (14:40:49Z,
run 34134258854; the M6 brief's appendix) verbatim where it prints them
-- the -gil, -scl, -mil, -col, -mnu team records, the instants, the tsc
row -- and the league-rows-por read (14:15Z, league_rows_por_1415.log)
for the questions; the OTHER club's record on each event (-acv, -rav,
-per, -hua, -eve, -vit, -cas) is an EXPECTATION by the attested shape
(abbreviation = the identifier's code, name = the P4 subject of that
row's verbatim question, no id read). His rows are the log's. Driven
with fakes: no venue, no database, no CLOB (conftest refuses it)."""
from __future__ import annotations

import asyncio
import inspect
import pathlib
import re
import time
from datetime import datetime, timezone

import pytest

from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import premap

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
D, D5 = "2026-09-06", "2026-09-05"
FT = "soccer_team_full_time_winner"
TOTAL = "soccer_team_full_game_total"
UTC = timezone.utc

# the instants, verbatim (gameStartTime); vit-cas is not in the appendix:
# gil-acv's 19:30Z is taken as its shape (the brief)
GIL_ACV_AT = "2026-09-06T19:30:00Z"
SCL_RAV_AT = "2026-09-06T14:30:00Z"
VIT_CAS_AT = "2026-09-06T19:30:00Z"
PER_MIL_AT = "2026-09-06T23:10:00Z"
HUA_COL_AT = "2026-09-06T20:00:00Z"
EVE_MNU_AT = "2026-09-06T13:00:00Z"


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _team(abbr: str, name: str, lg: str, **extra) -> dict:
    return {"abbreviation": abbr, "name": name, "league": lg, **extra}


# the team records the probe printed, verbatim
GIL = _team("gil", "Gil Vicente Barcelos", "ligpor", alias="Gil Vicente Barcelos", id=25978,
            providerIds=[{"ODDSPAPI": 3010}], safeName="Gil Vicente Barcelos")
SCL = _team("scl", "Santa Clara Azores", "ligpor", id=25984, safeName="Santa Clara Azores")
MIL = _team("mil", "Millonarios FC", "lco", alias="", id=17080,
            providerIds=[{"SPORTSDATAIO": 1201}], safeName="")
COL = _team("col", "CSD Colo-Colo", "pdc", id=17150, safeName="")
MNU = _team("mnu", "Manchester United FC", "epl", alias="The Red Devils", id=2694,
            providerIds=[{"SPORTSDATAIO": 517}, {"SPORTRADAR": "sr:competitor:35"}],
            record="1-1-1", safeName="Manchester United")
# the other club of each event: the attested shape, an expectation
ACV = _team("acv", "Academico de Viseu FC", "ligpor")
RAV = _team("rav", "Rio Ave FC", "ligpor")
VIT = _team("vit", "Vitoria SC Guimaraes", "ligpor")
CAS = _team("cas", "Casa Pia Lisbon", "ligpor")
PER = _team("per", "Deportivo Pereira", "lco")
HUA = _team("hua", "CD Huachipato", "pdc")
EVE = _team("eve", "Everton FC", "epl")


def _ml(ident: str, q: str, start: str, team: dict, **market) -> dict:
    """A per-team market as the probe prints it: both yes/no sides carry
    the SUBJECT club's record (ordering home on Yes, away on No -- read
    by nothing), teamId beside it, the instant and the type."""
    tid = team.get("id")
    return {"slug": ident, "question": q, "gameStartTime": start, "sportsMarketType": FT,
            **market, "marketSides": [
                {"identifier": ident, "description": "Yes", "long": True,
                 "team": dict(team, ordering="home"), "teamId": tid},
                {"identifier": ident, "description": "No", "long": False,
                 "team": dict(team, ordering="away"), "teamId": tid}]}


def _draw(ident: str, q: str, start: str) -> dict:
    """The draw market: team=null on both sides (the probe); its
    sportsMarketType is not in the appendix and is left unstated."""
    return {"slug": ident, "question": q, "gameStartTime": start, "marketSides": [
        {"identifier": ident, "description": "Yes", "long": True, "team": None},
        {"identifier": ident, "description": "No", "long": False, "team": None}]}


def _yn_market(ident: str, q: str, start: str, typ: str) -> dict:
    """A yes/no family market (the live sweep's asc / astatc shape:
    yes=LONG, no=SHORT, team=null); its sportsMarketType is not in the
    appendix and is a placeholder no rule reads."""
    return {"slug": ident, "question": q, "gameStartTime": start, "sportsMarketType": typ,
            "marketSides": [
                {"identifier": ident, "description": "Yes", "long": True, "team": None},
                {"identifier": ident, "description": "No", "long": False, "team": None}]}


def _total(ident: str, q: str, start: str, line: str) -> dict:
    return {"slug": ident, "question": q, "gameStartTime": start, "sportsMarketType": TOTAL,
            "marketSides": [
                {"identifier": ident, "description": f"Over {line}", "long": True, "team": None},
                {"identifier": ident, "description": f"Under {line}", "long": False, "team": None}]}


P4 = "Will {a} win against {b} in the {lg} match scheduled for {d}?"
DRAWQ = "Will the {lg} match {a} vs {b} scheduled for {d} end in a draw?"


def _game(lg: str, phrase: str, d: str, qd: str, A: dict, B: dict, start: str) -> list[dict]:
    """An event's three full-game rows by the venue's template
    (identifier date d, question date qd, instant start)."""
    e = f"{lg}-{A['abbreviation']}-{B['abbreviation']}-{d}"
    return [_ml(f"atc-{e}-{A['abbreviation']}", P4.format(a=A["name"], b=B["name"], lg=phrase, d=qd), start, A),
            _ml(f"atc-{e}-{B['abbreviation']}", P4.format(a=B["name"], b=A["name"], lg=phrase, d=qd), start, B),
            _draw(f"atc-{e}-draw", DRAWQ.format(a=A["name"], b=B["name"], lg=phrase, d=qd), start)]


# the venue, by event slug -> (event title, markets); the ligpor questions
# are the 14:15Z log's verbatim text (dated Sep 5, 2026), the lco / pdc /
# epl questions the reads test_c6_names / gap_soccer print
VENUE = {
    f"ligpor-gil-acv-{D5}": ("Gil Vicente Barcelos vs. Academico de Viseu FC", [
        _ml(f"atc-ligpor-gil-acv-{D5}-gil",
            "Will Gil Vicente Barcelos win against Academico de Viseu FC in the Liga Portugal "
            "match scheduled for Sep 5, 2026?", GIL_ACV_AT, GIL),
        _ml(f"atc-ligpor-gil-acv-{D5}-acv",
            "Will Academico de Viseu FC win against Gil Vicente Barcelos in the Liga Portugal "
            "match scheduled for Sep 5, 2026?", GIL_ACV_AT, ACV),
        _draw(f"atc-ligpor-gil-acv-{D5}-draw",
              "Will the Liga Portugal match Gil Vicente Barcelos vs Academico de Viseu FC scheduled "
              "for Sep 5, 2026 end in a draw?", GIL_ACV_AT),
        # the total, the spread and the btts row by the templates the epl
        # rows attest (gap_soccer §1; expectations, the venue lists none
        # of them on ligpor in the reads)
        _total(f"tsc-ligpor-gil-acv-{D5}-2pt5", "Will the total in GIL vs ACV be more than 2.5?",
               GIL_ACV_AT, "2.5"),
        _yn_market(f"asc-ligpor-gil-acv-{D5}-neg-1pt5",
                   "Will the Gil Vicente Barcelos cover -1.5 vs the Academico de Viseu FC in GIL vs ACV?",
                   GIL_ACV_AT, "soccer_team_full_game_spread"),
        _yn_market(f"astatc-ligpor-gil-acv-{D5}-btts",
                   "Will both teams score in the match between Gil Vicente Barcelos and Academico de "
                   "Viseu FC on 2026-09-05 3:30PM ET?", GIL_ACV_AT, "soccer_team_full_game_btts")]),
    f"ligpor-scl-rav-{D5}": ("Santa Clara Azores vs. Rio Ave FC", [
        _ml(f"atc-ligpor-scl-rav-{D5}-scl",
            "Will Santa Clara Azores win against Rio Ave FC in the Liga Portugal match scheduled "
            "for Sep 5, 2026?", SCL_RAV_AT, SCL),
        _ml(f"atc-ligpor-scl-rav-{D5}-rav",
            "Will Rio Ave FC win against Santa Clara Azores in the Liga Portugal match scheduled "
            "for Sep 5, 2026?", SCL_RAV_AT, RAV),
        _draw(f"atc-ligpor-scl-rav-{D5}-draw",
              "Will the Liga Portugal match Santa Clara Azores vs Rio Ave FC scheduled for Sep 5, "
              "2026 end in a draw?", SCL_RAV_AT)]),
    f"ligpor-vit-cas-{D5}": ("Vitoria SC Guimaraes vs. Casa Pia Lisbon", [
        _ml(f"atc-ligpor-vit-cas-{D5}-vit",
            "Will Vitoria SC Guimaraes win against Casa Pia Lisbon in the Liga Portugal match "
            "scheduled for Sep 5, 2026?", VIT_CAS_AT, VIT),
        _ml(f"atc-ligpor-vit-cas-{D5}-cas",
            "Will Casa Pia Lisbon win against Vitoria SC Guimaraes in the Liga Portugal match "
            "scheduled for Sep 5, 2026?", VIT_CAS_AT, CAS),
        _draw(f"atc-ligpor-vit-cas-{D5}-draw",
              "Will the Liga Portugal match Vitoria SC Guimaraes vs Casa Pia Lisbon scheduled for "
              "Sep 5, 2026 end in a draw?", VIT_CAS_AT)]),
    f"lco-per-mil-{D}": ("Deportivo Pereira vs. Millonarios FC",
                         _game("lco", "Liga Colombia", D, "Sep 6, 2026", PER, MIL, PER_MIL_AT)),
    f"pdc-hua-col-{D}": ("CD Huachipato vs. CSD Colo-Colo",
                         _game("pdc", "Primera Chile", D, "Sep 6, 2026", HUA, COL, HUA_COL_AT)),
    f"epl-eve-mnu-{D}": ("Everton FC vs. Manchester United FC",
                         _game("epl", "Premier League", D, "Sep 6, 2026", EVE, MNU, EVE_MNU_AT) + [
                             _total(f"tsc-epl-eve-mnu-{D}-2pt5",
                                    "Will the total in EVE vs MNU be more than 2.5?", EVE_MNU_AT, "2.5")]),
}

# his feed, verbatim (the 14:15Z log, statement 1; test_c6_names FEED):
# key -> (market_slug, market_title, markets.event_title, condition_id)
GIL_ACV, CDS_RIO, GUI_CAS = ("Gil Vicente FC vs. Académico de Viseu FC", "CD Santa Clara vs. Rio Ave FC",
                             "Vitória SC vs. Casa Pia AC")
FEED = {
    "gil": (f"por-gil-acv-{D}-gil", "Will Gil Vicente FC win on 2026-09-06?", GIL_ACV, "0xgilacv"),
    "acv": (f"por-gil-acv-{D}-acv", "Will Académico de Viseu FC win on 2026-09-06?", GIL_ACV, "0xgilacv2"),
    "ga-draw": (f"por-gil-acv-{D}-draw", "Will Gil Vicente FC vs. Académico de Viseu FC end in a draw?",
                GIL_ACV, "0xgilacv3"),
    "ga-total": (f"por-gil-acv-{D}-total-2pt5", "Gil Vicente FC vs. Académico de Viseu FC: O/U 2.5",
                 GIL_ACV, "0xgilacv4"),
    "ga-total3": (f"por-gil-acv-{D}-total-3pt5", "Gil Vicente FC vs. Académico de Viseu FC: O/U 3.5",
                  GIL_ACV, "0xgilacv5"),
    "ga-spread": (f"por-gil-acv-{D}-spread-away-1pt5", "Spread: Académico de Viseu FC (+1.5)", GIL_ACV,
                  "0xgilacv6"),
    "ga-btts": (f"por-gil-acv-{D}-btts", "Gil Vicente FC vs. Académico de Viseu FC: Both Teams to Score",
                GIL_ACV, "0xgilacv7"),
    "cds": (f"por-cds-rio-{D}-cds", "Will CD Santa Clara win on 2026-09-06?", CDS_RIO, "0xcdsrio"),
    "rio": (f"por-cds-rio-{D}-rio", "Will Rio Ave FC win on 2026-09-06?", CDS_RIO, "0xcdsrio2"),
    "cr-draw": (f"por-cds-rio-{D}-draw", "Will CD Santa Clara vs. Rio Ave FC end in a draw?", CDS_RIO,
                "0xcdsrio3"),
    "cas": (f"por-gui-cas-{D}-cas", "Will Casa Pia AC win on 2026-09-06?", GUI_CAS, "0xguicas"),
    "gui": (f"por-gui-cas-{D}-gui", "Will Vitória SC win on 2026-09-06?", GUI_CAS, "0xguicas2"),
    "gc-draw": (f"por-gui-cas-{D}-draw", "Will Vitória SC vs. Casa Pia AC end in a draw?", GUI_CAS,
                "0xguicas3"),
    "mif": (f"col1-dep-mif-{D}-mif", "Will Millonarios FC win on 2026-09-06?",
            "Deportivo Pereira vs. Millonarios FC", "0xdepmif"),
    "csc": (f"chi1-cdh-csc-{D}-csc", "Will CSD Colo-Colo win on 2026-09-06?",
            "CD Huachipato vs. CSD Colo-Colo", "0xcdhcsc"),
    "mun": (f"epl-eve-mun-{D}-mun", "Will Manchester United FC win on 2026-09-06?",
            "Everton FC vs. Manchester United FC", "0xevemun"),
}
# his instants, as market_starts holds them: the CLOB record's game_start_time
STARTS = {
    "0xgilacv": _at(GIL_ACV_AT), "0xgilacv2": _at(GIL_ACV_AT), "0xgilacv3": _at(GIL_ACV_AT),
    "0xgilacv4": _at(GIL_ACV_AT), "0xgilacv5": _at(GIL_ACV_AT), "0xgilacv6": _at(GIL_ACV_AT),
    "0xgilacv7": _at(GIL_ACV_AT),
    "0xcdsrio": _at(SCL_RAV_AT), "0xcdsrio2": _at(SCL_RAV_AT), "0xcdsrio3": _at(SCL_RAV_AT),
    "0xguicas": _at(VIT_CAS_AT), "0xguicas2": _at(VIT_CAS_AT), "0xguicas3": _at(VIT_CAS_AT),
    "0xdepmif": _at(PER_MIL_AT), "0xcdhcsc": _at(HUA_COL_AT), "0xevemun": _at(EVE_MNU_AT),
}

V_GIL, V_ACV, V_GA_DRAW = (f"atc-ligpor-gil-acv-{D5}-gil", f"atc-ligpor-gil-acv-{D5}-acv",
                           f"atc-ligpor-gil-acv-{D5}-draw")
V_SCL, V_RAV, V_SR_DRAW = (f"atc-ligpor-scl-rav-{D5}-scl", f"atc-ligpor-scl-rav-{D5}-rav",
                           f"atc-ligpor-scl-rav-{D5}-draw")
V_GA_TOTAL = f"tsc-ligpor-gil-acv-{D5}-2pt5"
V_GA_SPREAD, V_GA_BTTS = f"asc-ligpor-gil-acv-{D5}-neg-1pt5", f"astatc-ligpor-gil-acv-{D5}-btts"
V_MIL, V_COL = f"atc-lco-per-mil-{D}-mil", f"atc-pdc-hua-col-{D}-col"
GIL_MINUTE = "2026-09-06T19:30Z"
EXCL_GIL = {"gil vicente fc": "gil vicente barcelos"}
EXCL_CDS = {"cd santa clara": "santa clara azores"}
UNKNOWN, NO_EVENT, AMBIG, UNWIT, SHEAR, CONFLICT, TYPE = (
    "kick:unknown", "kick:no-event", "kick:ambiguous", "kick:club-unwitnessed", "kick:title-shear",
    "kick:code-conflict", "kick:type")


def _board(venue: dict | None = None, extra: dict | None = None) -> list[dict]:
    """Rows keyed as the sweep keys them: the event's keys, both clubs'
    kickoff keys (premap.venue_kick_keys over the event's rows), and
    each row's own name keys (premap.keys_for_row)."""
    rows: list[dict] = []
    for src in (venue if venue is not None else VENUE, extra or {}):
        for ev_slug, (ev_title, mkts) in src.items():
            ev_rows = [r for m in mkts
                       for r in premap._market_rows({"slug": ev_slug, "title": ev_title}, m)]
            keys = sorted(set(premap.event_keys_for(ev_title, ev_slug)) | premap.venue_kick_keys(ev_rows))
            for r in ev_rows:
                r["event_keys"] = premap.keys_for_row(keys, r)
                rows.append(r)
    return rows


class _Pool:
    """us_premap by key intersection (and the census probe's identifier
    pattern); market_starts by condition_id (the edge_marks read); the
    trades / markets titles by slug (the C5 / C6-N read)."""

    def __init__(self, rows, starts: dict | None = None, titles: dict | None = None, *,
                 all_rows: bool = False):
        self.rows = rows
        self.starts = STARTS if starts is None else starts
        self.titles = titles or {}
        self.all_rows = all_rows          # every row answers the key fetch: the arms alone decide
        self.reads: list[str] = []
        self.writes: list[str] = []

    async def fetch(self, sql, *a):
        if "us_premap" in sql:
            self.reads.append("us_premap")
            if "identifier ~" in sql:
                return [dict(r) for r in self.rows if re.search(a[0], r["identifier"])]
            if "game_start >=" in sql:
                self.reads.append("at_instant")
                assert "interval '1 minute'" in sql and a[0].second == 0
                return [dict(r) for r in self.rows if premap._kick_minute(r["game_start"]) ==
                        premap._kick_minute(a[0]) and r["sports_type"] == a[1]]
            k = set(a[0])
            return [dict(r) for r in self.rows if self.all_rows or set(r["event_keys"]) & k]
        assert "trades" in sql and "markets" in sql, sql
        self.reads.append("titles")
        return [{"t": t} for t in self.titles.get(a[0], [])]

    async def fetchval(self, sql, *a):
        """C6's column probe (the seven 055 columns are present here);
        the grammar class's state read answers nothing."""
        if "information_schema.columns" in sql:
            assert sorted(a[0]) == sorted(premap._TEAM_COLUMNS)
            return len(premap._TEAM_COLUMNS)
        return None

    async def fetchrow(self, sql, *a):
        assert "market_starts" in sql, sql
        self.reads.append("market_starts")
        v = self.starts.get(a[0], "absent")
        if v == "absent":
            return None
        if v == "err":
            return {"game_start": None, "err": "ReadTimeout", "fetched_ts": time.time()}
        return {"game_start": v, "err": None, "fetched_ts": time.time()}

    async def execute(self, sql, *a):
        self.writes.append(" ".join(str(sql).split())[:40])


def _resolve(rows, key, oc, *, ev="keep", pool=None, cid="keep"):
    slug, title, evt, condition = FEED[key]
    p = pool or _Pool(rows)
    return asyncio.run(premap.resolve(p, title, evt if ev == "keep" else ev, oc, slug,
                                      condition_id=condition if cid == "keep" else cid))


def _explain(rows, key, oc, *, ev="keep", pool=None, cid="keep", fetch=True):
    slug, title, evt, condition = FEED[key]
    p = pool or _Pool(rows)
    return asyncio.run(premap.resolve_explain(p, title, evt if ev == "keep" else ev, oc, slug,
                                              condition_id=condition if cid == "keep" else cid,
                                              fetch_kick=fetch))


def _short(h):
    if not h:
        return None
    return (h["market_slug"], h["outcome"], h["intent"], h["matched_by"])


def _pick(rows, key, oc, *, ev="keep", kick="keep"):
    slug, title, evt, condition = FEED[key]
    tr: dict = {}
    out = premap._yn_pick(rows, oc, title, slug, evt if ev == "keep" else ev, tr,
                          kick=STARTS[condition] if kick == "keep" else kick)
    return out, tr


@pytest.fixture(autouse=True)
def _columns_reprobed(monkeypatch):
    """C6's per-process probe of the 055 columns starts fresh in every
    test (as test_c6_cfb_team resets it): the fake pool answers it."""
    monkeypatch.setattr(premap, "_TEAM_COLS_STATE", {"present": None, "at": 0.0})


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


@pytest.fixture
def dark(monkeypatch):
    monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)


def _with(rows, ident, **fields):
    return [dict(r, **fields) if r["identifier"] == ident else dict(r) for r in rows]


# ------------------------------------------------------- C7-store

class TestTheStore:
    def test_the_row_builder_keeps_the_teams_record_the_instant_and_the_type(self):
        rows = premap._market_rows({"slug": f"ligpor-gil-acv-{D5}", "title": ""}, VENUE[f"ligpor-gil-acv-{D5}"][1][0])
        assert [r["side_norm"] for r in rows] == ["yes", "no"]
        for r in rows:
            # the seven columns of migration 055 (C6's store, shared): the
            # club under team_name, the venue's numeric id, the instant
            assert (r["team_abbr"], r["team_name"], r["team_id"], r["team_league"]) == \
                ("gil", "gil vicente barcelos", 25978, "ligpor")
            assert r["team_safe_name"] == "gil vicente barcelos"
            assert r["game_start"] == datetime(2026, 9, 6, 19, 30, tzinfo=UTC)
            assert r["sports_type"] == FT
            assert "ordering" not in r and "alias" not in r
        # the -mil record: alias and safeName empty, the name the club
        mil = premap._market_rows({"slug": "", "title": ""}, VENUE[f"lco-per-mil-{D}"][1][1])
        assert mil[0]["team_name"] == "millonarios fc" and mil[0]["team_id"] == 17080
        assert mil[0]["team_safe_name"] is None
        # the fold the arms read club names with, on the record too
        t = premap._side_team({"team": {"name": "Tromsø IL", "safeName": "Tromsø"}}, {})
        assert (t["team_name"], t["team_safe_name"]) == ("tromso il", "tromso")
        # the tsc row and the draw row: team=null, the instant and type as stated
        tsc = premap._market_rows({"slug": "", "title": ""}, VENUE[f"epl-eve-mnu-{D}"][1][3])
        assert tsc[0]["team_abbr"] is None and tsc[0]["team_name"] is None and tsc[0]["team_id"] is None
        assert tsc[0]["sports_type"] == TOTAL and premap._kick_minute(tsc[0]["game_start"]) == "2026-09-06T13:00Z"
        drw = premap._market_rows({"slug": "", "title": ""}, VENUE[f"ligpor-gil-acv-{D5}"][1][2])
        assert drw[0]["team_name"] is None and drw[0]["sports_type"] is None
        assert premap._kick_minute(drw[0]["game_start"]) == GIL_MINUTE
        # an expectation record without an id stores no id
        acv = premap._market_rows({"slug": "", "title": ""}, VENUE[f"ligpor-gil-acv-{D5}"][1][1])
        assert acv[0]["team_id"] is None and acv[0]["team_abbr"] == "acv"

    def test_an_instant_is_read_only_as_an_instant(self):
        assert premap._parse_instant("2026-09-06T19:30:00Z") == datetime(2026, 9, 6, 19, 30, tzinfo=UTC)
        assert premap._parse_instant("2026-09-06T19:30:00+00:00") == datetime(2026, 9, 6, 19, 30, tzinfo=UTC)
        assert premap._parse_instant("2026-09-06T15:30:00-04:00") == datetime(2026, 9, 6, 19, 30, tzinfo=UTC)
        # one parser (C6's _game_start, the merge): a naive time -- a
        # date alone, a clock without an offset -- is no instant; an
        # epoch is one
        for bad in (None, "", "Sep 6, 2026", True, "2026-09-06", "2026-09-06T19:30:00"):
            assert premap._parse_instant(bad) is None, bad
            assert premap._side_team({}, {"gameStartTime": bad})["game_start"] is None, bad
        assert premap._kick_minute(1789000000) == "2026-09-10T00:26Z"
        # a naive datetime (no stored instant is naive) is no instant
        assert premap._parse_instant(datetime(2026, 9, 6, 19, 30)) is None
        assert premap._kick_minute(datetime(2026, 9, 6, 19, 30, 59, tzinfo=UTC)) == GIL_MINUTE

    def test_the_upsert_the_table_and_the_readers_carry_the_columns_c7_reads(self):
        """One store (migration 055, C6's): C7 adds no column, and the
        readers SELECT its four columns beside C6's through the same
        probe, so a database without 055 never reads
        premap_query_failed."""
        cols = ("team_abbr", "team_name", "team_id", "team_league", "game_start", "sports_type")
        src = inspect.getsource(premap._upsert)
        for c in cols:
            assert c in src.split("VALUES")[-1].split("ON CONFLICT")[0] or c in src, c
            assert f"{c}=$" in src and f'r.get("{c}")' in src, c
        table = inspect.getsource(premap._ensure_table)
        for c in cols:
            assert f'("{c}", ' in table, c
        # one SELECT: C6's fragment carries the four names C7 reads, and
        # every name in it is one the probe asks information_schema for
        for c in ("team_name", "team_league", "game_start", "sports_type"):
            assert f"{c}, " in premap.TEAM_SELECT_COLS, c
        assert set(premap.TEAM_SELECT_COLS.replace(",", " ").split()) <= set(premap._TEAM_COLUMNS)
        for fn in (premap.resolve, premap.resolve_explain):
            src = inspect.getsource(fn)
            assert "await team_select_cols(pool) +" in src and "kick_select_cols" not in src, fn
            assert '"intent, signed, event_slug, market_slug FROM us_premap "' in src, fn
        mig = pathlib.Path(premap.__file__).resolve().parents[2] / "migrations"
        assert not list(mig.glob("*kickoff*")), "C7 adds no migration: 055 carries every column it reads"
        text = mig.joinpath("055_us_premap_team.sql").read_text()
        for c, typ in zip(cols, ("text", "text", "bigint", "text", "timestamptz", "text")):
            assert f"ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS {c} {typ};" in text, c

    def test_without_the_055_columns_the_readers_select_nothing_of_c7s(self, armed):
        rows = _board()

        class _NoCols(_Pool):
            async def fetchval(self, sql, *a):
                return 0

        p = _NoCols(rows)
        assert asyncio.run(premap.team_select_cols(p)) == ""
        # the fake answers the fetch with its rows regardless of the
        # column list: C7 reads the rows as they come
        assert _resolve(rows, "gil", "Yes", pool=p) is not None


# ------------------------------------------------------- C7-key

class TestTheKickoffKey:
    def test_every_row_of_the_event_carries_both_clubs_at_the_instant(self):
        rows = _board()
        by = {(r["identifier"], r["side_norm"]): r for r in rows}
        both = {f"gil vicente barcelos@{GIL_MINUTE}", f"academico de viseu fc@{GIL_MINUTE}"}
        for ident, side in ((V_GIL, "yes"), (V_ACV, "no"), (V_GA_DRAW, "yes"), (V_GA_TOTAL, "over 2 5")):
            assert both <= set(by[(ident, side)]["event_keys"]), ident
        assert f"santa clara azores@{GIL_MINUTE}" not in by[(V_GIL, "yes")]["event_keys"]
        assert "santa clara azores@2026-09-06T14:30Z" in by[(V_SR_DRAW, "no")]["event_keys"]
        # a row typed otherwise, or without a record, emits none
        assert premap.venue_kick_keys([dict(by[(V_GIL, "yes")], sports_type=TOTAL)]) == set()
        assert premap.venue_kick_keys([dict(by[(V_GIL, "yes")], team_name=None)]) == set()
        assert premap.venue_kick_keys([dict(by[(V_GIL, "yes")], game_start=None)]) == set()

    def test_his_side_keys_his_anchor_and_his_titles_sides_at_his_instant(self):
        slug, title, ev, cid = FEED["gil"]
        assert premap.his_kick_keys(title, ev, slug, STARTS[cid]) == \
            [f"academico de viseu fc@{GIL_MINUTE}", f"gil vicente fc@{GIL_MINUTE}"]
        assert premap.his_kick_keys(FEED["mif"][1], None, FEED["mif"][0], STARTS["0xdepmif"]) == \
            ["millonarios fc@2026-09-06T23:10Z"]
        assert premap.his_kick_keys(FEED["ga-draw"][1], None, FEED["ga-draw"][0], STARTS[cid]) == \
            [f"academico de viseu fc@{GIL_MINUTE}", f"gil vicente fc@{GIL_MINUTE}"]
        assert premap.his_kick_keys(FEED["ga-total"][1], None, FEED["ga-total"][0], STARTS[cid]) == \
            [f"academico de viseu fc@{GIL_MINUTE}", f"gil vicente fc@{GIL_MINUTE}"]
        assert premap.his_kick_keys(title, ev, slug, None) == []
        assert premap.his_kick_keys(title, ev, "por-gil-acv-gil", STARTS[cid]) == []
        # admitted by the dated filter as an "@" key: an instant is game agreement
        assert premap._dated_admissible({f"gil vicente fc@{GIL_MINUTE}"}, D) == {f"gil vicente fc@{GIL_MINUTE}"}

    def test_the_sweep_writes_both_clubs_keys_on_every_row(self):
        src = inspect.getsource(premap.refresh)
        assert src.count("venue_kick_keys(rows)") == 2
        assert src.count("_upsert(pool, r, keys_for_row(keys, r))") == 2

    def test_the_instant_is_read_from_market_starts_once_per_call(self, armed):
        rows = _board()
        pool = _Pool(rows)
        h = _resolve(rows, "gil", "Yes", pool=pool)
        assert h and pool.reads == ["market_starts", "us_premap"] and pool.writes == []
        # no condition id, or the switch off: nothing is read
        pool = _Pool(rows)
        assert _resolve(rows, "gil", "Yes", pool=pool, cid=None) is None
        assert "market_starts" not in pool.reads


# ------------------------------------------------------- Liga Portugal, the verbatim rows

class TestLigaPortugalByTheInstant:
    def test_b_gil_vicente_by_exclusion(self, armed):
        """His 'Gil Vicente FC' is not the venue's 'Gil Vicente Barcelos';
        'Académico de Viseu FC' IS its 'Academico de Viseu FC' (fold), at
        19:30Z, on the one event listing both -- so the -gil row's club
        is his by exclusion."""
        rows = _board()
        h = _resolve(rows, "gil", "Yes")
        assert _short(h) == (V_GIL, "yes", LONG, "premap_kickoff")
        assert h["club_by_exclusion"] == EXCL_GIL and h["witness"] == V_ACV
        assert h["game_start"] == GIL_MINUTE and h["code_pair"] == {"gil": "gil", "acv": "acv"}
        assert h["league_alias"] == "por->ligpor" and "code_translated" not in h
        assert _short(_resolve(rows, "gil", "No")) == (V_GIL, "no", SHORT, "premap_kickoff")
        ex = _explain(rows, "gil", "Yes")
        assert ex["step"] == "resolves" and ex["detail"] == V_GIL
        assert ex["matched_by"] == "premap_kickoff" and ex["club_by_exclusion"] == EXCL_GIL
        assert ex["witness"] == V_ACV and ex["game_start"] == GIL_MINUTE
        assert ex["yn_c7"]["his_game_start"] == GIL_MINUTE and ex["yn_c7"]["why"] == "ok"
        _out, tr = _pick(rows, "gil", "Yes")
        assert tr["witness_src"] == "exclusion" and tr["events"] == [f"ligpor-gil-acv-{D5}", f"ligpor-vit-cas-{D5}"]

    def test_c_academico_by_its_own_record(self, armed):
        rows = _board()
        h = _resolve(rows, "acv", "Yes")
        assert _short(h) == (V_ACV, "yes", LONG, "premap_kickoff")
        assert h["witness"] == V_ACV and h["club_by_exclusion"] == EXCL_GIL
        assert _pick(rows, "acv", "No")[1]["witness_src"] == "record"
        assert _short(_resolve(rows, "acv", "No")) == (V_ACV, "no", SHORT, "premap_kickoff")

    def test_d_the_draw_of_the_witnessed_event(self, armed):
        rows = _board()
        y, n = _resolve(rows, "ga-draw", "Yes"), _resolve(rows, "ga-draw", "No")
        assert _short(y) == (V_GA_DRAW, "yes", LONG, "premap_kickoff")
        assert _short(n) == (V_GA_DRAW, "no", SHORT, "premap_kickoff")
        assert y["club_by_exclusion"] == EXCL_GIL and y["witness"] == V_ACV
        assert _pick(rows, "ga-draw", "Yes")[1]["draw_witness"] == "event"
        # no event title: his draw title names both sides (C3's witness)
        h = _resolve(rows, "ga-draw", "Yes", ev=None)
        assert _short(h) == (V_GA_DRAW, "yes", LONG, "premap_kickoff")
        assert _pick(rows, "ga-draw", "Yes", ev=None)[1]["draw_witness"] == "title"

    def test_e_santa_clara_rio_ave_no_code_shared(self, armed):
        """'Rio Ave FC' identical at 14:30Z; 'CD Santa Clara' is the
        venue's 'Santa Clara Azores' by exclusion -- both codes differ
        (cds-rio vs scl-rav), the names lane refused yn:names-mismatch
        on the date-forced fixture and yn:no-row on the real dates."""
        rows = _board()
        h = _resolve(rows, "cds", "Yes")
        assert _short(h) == (V_SCL, "yes", LONG, "premap_kickoff")
        assert h["club_by_exclusion"] == EXCL_CDS and h["witness"] == V_RAV
        assert h["code_pair"] == {"cds": "scl", "rio": "rav"}
        assert _short(_resolve(rows, "rio", "No")) == (V_RAV, "no", SHORT, "premap_kickoff")
        assert _short(_resolve(rows, "cr-draw", "Yes")) == (V_SR_DRAW, "yes", LONG, "premap_kickoff")
        assert _pick(rows, "rio", "No")[1]["witness_src"] == "record"

    def test_a_casa_pia_is_refused_by_design(self, armed):
        """'Vitoria SC Guimaraes' is not 'Vitória SC', 'Casa Pia Lisbon'
        is not 'Casa Pia AC': at 19:30Z the venue lists vit-cas and
        gil-acv, and neither names a club of his -- kick:club-unwitnessed,
        never a partial match."""
        rows = _board()
        venue_names = {f"ligpor-gil-acv-{D5}": ["gil vicente barcelos", "academico de viseu fc"],
                       f"ligpor-vit-cas-{D5}": ["vitoria sc guimaraes", "casa pia lisbon"]}
        for key in ("cas", "gui", "gc-draw"):
            for oc in ("Yes", "No"):
                assert _resolve(rows, key, oc) is None, key
            # no kickoff key of his meets a row (neither club is the
            # venue's): the lookup finds nothing, and the census reads
            # what the venue lists at his instant
            ex = _explain(rows, key, "Yes")
            assert (ex["step"], ex["split"], ex["rows"]) == ("no_key_intersection", UNWIT, 0), key
            assert ex["yn_c7"]["refusal"] == UNWIT
            assert ex["yn_c7"]["at_instant"] == [V_ACV, V_GIL, f"atc-ligpor-vit-cas-{D5}-cas",
                                                  f"atc-ligpor-vit-cas-{D5}-vit"], key
            # handed the rows, the arm names the same refusal from them
            _out, tr = _pick(rows, key, "Yes")
            assert tr["refusal"] == UNWIT and tr["names_refusal"] == "yn:no-row", key
            assert tr["venue_names"] == venue_names, key
        ctx = {"title": FEED["cas"][1], "event_title": GUI_CAS, "outcome": "Yes",
               "his_slug": FEED["cas"][0], "condition_id": FEED["cas"][3]}
        assert asyncio.run(ms.explain_unmapped(_Pool(rows), ctx)) == f"no_key_intersection:{UNWIT}"
        # the rows on the board (every row answering the fetch): the
        # arm refuses from them, the same name
        ex = _explain(rows, "cas", "Yes", pool=_Pool(rows, all_rows=True))
        assert (ex["step"], ex["split"]) == ("no_side_match", UNWIT)
        assert ex["yn_c7"]["arm"]["venue_names"] == venue_names
        # with no event title the names lane's yn:names-unwitnessed stays
        # the split (names_resolve's stored-title witness still reads
        # after it); C7's reading rides beside it
        ctx["event_title"] = None
        assert asyncio.run(ms.explain_unmapped(_Pool(rows, all_rows=True), ctx)) == \
            "no_side_match:yn:names-unwitnessed"
        ex = _explain(rows, "cas", "Yes", ev=None, pool=_Pool(rows, all_rows=True))
        assert ex["yn_c2"]["kick"]["refusal"] == UNWIT and ex["yn_c7"]["arm"]["refusal"] == UNWIT
        assert ex["yn_c6"] == {"refusal": "yn:names-unwitnessed", "why": "no-stored-title"}

    def test_the_por_rows_of_test_c6_names_answer_as_before_without_an_instant(self, armed):
        """M3's pins hold: no condition id, no kickoff key, the rows
        never fetched; handed the rows, _yn_pick reads yn:no-row."""
        rows = _board()
        for key in ("gil", "acv", "ga-draw", "cds", "rio", "cr-draw", "cas", "gui", "gc-draw"):
            assert _resolve(rows, key, "Yes", cid=None) is None, key
            ex = _explain(rows, key, "Yes", cid=None)
            assert (ex["step"], ex["split"]) == ("no_key_intersection", "venue:league-unlisted"), key
            assert ex["yn_c7"] == {"his_game_start": None, "why": "no-condition-id", "kick_keys": []}, key
            _out, tr = _pick(rows, key, "Yes", kick=None)
            assert tr["refusal"] == "yn:no-row" and "kick" not in tr, key


# ------------------------------------------------------- every refusal, by name

class TestEveryRefusalIsNamed:
    def test_f_his_instant_unknown(self, armed):
        rows = _board()
        for starts in ({}, {"0xgilacv": "err"}, {"0xgilacv": None}):
            pool = _Pool(rows, starts=starts)
            assert _resolve(rows, "gil", "Yes", pool=pool) is None, starts
            # no kickoff key, no key of his meets: the lookup finds
            # nothing, and the census names what was not read
            ex = _explain(rows, "gil", "Yes", pool=_Pool(rows, starts=starts))
            assert (ex["step"], ex["split"]) == ("no_key_intersection", UNKNOWN), starts
            assert ex["yn_c7"]["refusal"] == UNKNOWN and ex["yn_c7"]["his_game_start"] is None
            assert ex["yn_c7"]["why"] == ("no-game-start" if starts.get("0xgilacv", "x") is None else "unread")
        # the venue's rows on the board (every row answering the fetch)
        # and his instant unread: the class C7 exists for, refused by the
        # name of what was not read
        ex = _explain(rows, "gil", "Yes", pool=_Pool(rows, starts={}, all_rows=True))
        assert (ex["step"], ex["split"]) == ("no_side_match", UNKNOWN)
        assert ex["yn_c2"]["refusal"] == "yn:no-row" and "kick" not in ex["yn_c2"]
        # a board without soccer rows keeps the earlier arm's name
        plain = [dict(r, sports_type=None) for r in rows]
        ex = _explain(plain, "cds", "Yes", pool=_Pool(plain, starts={}, all_rows=True))
        assert ex["split"] == "yn:no-row"
        # the live path reads market_starts and nothing else (review H3):
        # no venue read, no write, no guess; his other keys still ask
        pool = _Pool(rows, starts={})
        _resolve(rows, "gil", "Yes", pool=pool)
        assert pool.reads == ["market_starts", "us_premap"] and pool.writes == []
        # the census's explain (fetch_kick=False) reads the same way
        ex = _explain(rows, "gil", "Yes", pool=_Pool(rows, starts={}), fetch=False)
        assert ex["yn_c7"]["why"] == "unread" and ex["split"] == UNKNOWN
        # the shadow's re-judge (fetch_kick) fills the table through
        # edge_marks: the failed venue read is recorded there, once
        pool = _Pool(rows, starts={})
        _explain(rows, "gil", "Yes", pool=pool)
        assert len(pool.writes) == 1 and pool.writes[0].startswith("INSERT INTO market_starts")

    def test_g_an_instant_off_by_one_minute_is_another_game(self, armed):
        rows = _board()
        late = {"0xgilacv": _at("2026-09-06T19:31:00Z")}
        assert _resolve(rows, "gil", "Yes", pool=_Pool(rows, starts=late)) is None
        # no kickoff key meets at 19:31Z, nothing listed at 19:31Z
        ex = _explain(rows, "gil", "Yes", pool=_Pool(rows, starts=late))
        assert (ex["step"], ex["split"]) == ("no_key_intersection", "venue:league-unlisted")
        assert ex["yn_c7"]["at_instant"] == [] and "refusal" not in ex["yn_c7"]
        # the rows on the board (every row answering the fetch): the arm refuses
        ex = _explain(rows, "gil", "Yes", pool=_Pool(rows, starts=late, all_rows=True))
        assert (ex["step"], ex["split"]) == ("no_side_match", NO_EVENT)
        assert ex["yn_c7"]["arm"]["events"] == [] and ex["yn_c7"]["arm"]["names_refusal"] == "yn:no-row"
        _out, tr = _pick(rows, "cds", "Yes", kick=_at("2026-09-06T14:31:00Z"))
        assert tr["refusal"] == NO_EVENT and tr["events"] == []
        # a second's difference within the minute is the same instant
        assert _pick(rows, "cds", "Yes", kick=_at("2026-09-06T14:30:45Z"))[0]

    def test_h_two_events_at_the_instant_naming_rio_ave(self, armed):
        twin = {f"ligpor-rav-bra-{D5}": ("Rio Ave FC vs. SC Braga", _game(
            "ligpor", "Liga Portugal", D5, "Sep 5, 2026", RAV, _team("bra", "SC Braga", "ligpor"), SCL_RAV_AT))}
        rows = _board(extra=twin)
        for key in ("cds", "rio", "cr-draw"):
            assert _resolve(rows, key, "Yes") is None, key
            ex = _explain(rows, key, "Yes")
            assert ex["split"] == AMBIG, key
            assert ex["yn_c7"]["arm"]["candidates"] == [f"ligpor-rav-bra-{D5}", f"ligpor-scl-rav-{D5}"]

    def test_i_a_record_disagreeing_with_its_identifier_maps_nothing(self, armed):
        rows = _with(_board(), V_SCL, team_abbr="rav")
        for key in ("cds", "rio", "cr-draw"):
            assert _resolve(rows, key, "Yes") is None, key
            ex = _explain(rows, key, "Yes")
            assert ex["split"] == CONFLICT and ex["yn_c7"]["arm"]["venue_row"] == V_SCL, key
        # the record's league disagreeing with the identifier's: the same
        rows = _with(_board(), V_RAV, team_league="lpb")
        assert _explain(rows, "rio", "Yes")["split"] == CONFLICT
        # the question naming one club and the record another: the same
        rows = [dict(r, team_name="rio ave fc") if r["identifier"] == V_SCL and r["side_norm"] == "no" else dict(r)
                for r in _board()]
        ex = _explain(rows, "cds", "Yes")
        assert ex["split"] == CONFLICT and ex["yn_c7"]["arm"]["why"] == "question"
        rows = _with(_board(), V_ACV, question=P4.format(a="SC Braga", b="Gil Vicente Barcelos",
                                                          lg="Liga Portugal", d="Sep 5, 2026"))
        _out, tr = _pick(rows, "gil", "Yes")
        assert (tr["refusal"], tr["why"], tr["venue_row"]) == (CONFLICT, "question", V_ACV)

    def test_j_a_womens_twin_is_screened_at_the_league_slot(self, armed):
        w = {f"ligporw-gil-acv-{D5}": ("Gil Vicente Barcelos vs. Academico de Viseu FC", _game(
            "ligporw", "Liga Portugal Feminina", D5, "Sep 5, 2026", dict(GIL, league="ligporw"),
            dict(ACV, league="ligporw"), GIL_ACV_AT))}
        rows = _board(extra=w)
        h = _resolve(rows, "gil", "Yes")
        assert _short(h) == (V_GIL, "yes", LONG, "premap_kickoff")
        _out, tr = _pick(rows, "gil", "Yes")
        for code in ("gil", "acv"):
            assert tr["screened"].count(f"atc-ligporw-gil-acv-{D5}-{code}:yn:league-slot") == 2, code
        assert tr["events"] == [f"ligpor-gil-acv-{D5}", f"ligpor-vit-cas-{D5}"] and "partial" not in tr
        # a twin with the same league phrase at the same instant is ambiguous
        x = {f"ligporx-gil-acv-{D5}": ("Gil Vicente Barcelos vs. Academico de Viseu FC", _game(
            "ligporx", "Liga Portugal", D5, "Sep 5, 2026", dict(GIL, league="ligporx"),
            dict(ACV, league="ligporx"), GIL_ACV_AT))}
        assert _explain(_board(extra=x), "gil", "Yes")["split"] == AMBIG

    def test_kick_type_a_row_that_is_not_the_full_time_winner(self, armed):
        rows = [dict(r, sports_type="soccer_team_first_half_winner") if r["identifier"] in (V_SCL, V_RAV)
                else dict(r) for r in _board()]
        assert _resolve(rows, "cds", "Yes") is None
        assert _explain(rows, "cds", "Yes")["split"] == TYPE
        # a lone per-team row at the instant witnesses nothing
        rows = [r for r in _board() if r["identifier"] != V_RAV]
        _out, tr = _pick(rows, "cds", "Yes")
        assert tr["refusal"] == NO_EVENT and tr["partial"] == [f"ligpor-scl-rav-{D5}"]

    def test_title_shear_and_position(self, armed):
        rows = _board()
        slug, _t, ev, cid = FEED["gil"]
        # his title naming the other club: the names lane names the
        # shear first (yn:names-shear) and C7 never runs
        tr: dict = {}
        premap._yn_pick(rows, "Yes", FEED["acv"][1], slug, ev, tr, kick=STARTS[cid])
        assert tr["refusal"] == "yn:names-shear" and "kick" not in tr
        # the arm itself, handed that title: kick:title-shear
        t2: dict = {}
        parts = premap._yn_slug_parts(slug)
        assert premap._c7_kick_pick(rows, parts, "yes", LONG, "academico de viseu fc", ev, STARTS[cid], t2) == []
        assert (t2["refusal"], t2["why"]) == (SHEAR, "position")
        t3: dict = {}
        assert premap._c7_kick_pick(rows, parts, "yes", LONG, "sc braga", ev, STARTS[cid], t3) == []
        assert t3["refusal"] == SHEAR and "why" not in t3
        # the venue ordering the pair otherwise (acv-gil): the row at his
        # tail's position is the other club's
        rev = {f"ligpor-acv-gil-{D5}": ("Academico de Viseu FC vs. Gil Vicente Barcelos", _game(
            "ligpor", "Liga Portugal", D5, "Sep 5, 2026", ACV, GIL, GIL_ACV_AT))}
        rows = _board({k: v for k, v in VENUE.items() if "gil-acv" not in k}, extra=rev)
        assert _resolve(rows, "gil", "Yes") is None
        ex = _explain(rows, "gil", "Yes")
        assert ex["split"] == UNWIT and ex["yn_c7"]["arm"]["why"] == "position"
        assert _explain(rows, "ga-draw", "Yes")["split"] == SHEAR

    def test_the_gate_and_the_veto_still_read_the_row(self, armed):
        rows = _board()
        # a market whose intents are not the venue's own for yes/no is
        # not a per-team market as the venue lists it (C5's reading):
        # the event is partial and witnesses nothing
        swapped = [dict(r, intent=SHORT if r["intent"] == LONG else LONG) if r["identifier"] == V_GIL
                   else dict(r) for r in rows]
        assert _explain(swapped, "gil", "Yes")["split"] == NO_EVENT
        assert _pick(swapped, "gil", "Yes")[1]["partial"] == [f"ligpor-gil-acv-{D5}"]
        # a lined per-team row is no per-team market as the venue lists
        # it: screened before the line veto ever sees it
        lined = _with(rows, V_GIL, line="1.5")
        assert _resolve(lined, "gil", "Yes") is None
        assert _explain(lined, "gil", "Yes")["split"] == NO_EVENT
        assert _pick(lined, "gil", "Yes")[1]["partial"] == [f"ligpor-gil-acv-{D5}"]
        # the identity veto still reads the hit's identifier (c2_ids)
        assert "_yn_identity_ok(r)" in inspect.getsource(premap.match_side)
        # a terse question, a question dated otherwise than its own identifier: screened
        terse = _with(rows, V_GIL, question="Will Gil Vicente Barcelos win?")
        assert _explain(terse, "gil", "Yes")["split"] == NO_EVENT
        # a question dated otherwise than its own identifier: screened,
        # the event partial, and the one event left at 19:30Z (vit-cas)
        # names no club of his
        later = _with(rows, V_GIL, question=VENUE[f"ligpor-gil-acv-{D5}"][1][0]["question"].replace("Sep 5", "Sep 6"))
        _out, tr = _pick(later, "gil", "Yes")
        assert tr["refusal"] == UNWIT and tr["screened"] == [f"{V_GIL}:yn:qdate", f"{V_GIL}:yn:qdate"]
        assert tr["partial"] == [f"ligpor-gil-acv-{D5}"] and tr["events"] == [f"ligpor-vit-cas-{D5}"]

    def test_the_exclusion_never_binds_a_contradiction(self, armed):
        """Review M1: his stated name for the OTHER club must share a
        name with the venue's record: one side's tokens beyond legal-form
        furniture a subset of the other's. 'Sporting CP' / 'Sporting
        Braga' share only 'sporting' and 'Sporting CP' / 'Boavista FC'
        share nothing: a data error in one feed, kick:club-unwitnessed."""
        far = _team("far", "SC Farense", "ligpor")
        title, ev, slug = ("Will Sporting CP win on 2026-09-06?", "Sporting CP vs. SC Farense",
                           f"por-scp-far-{D}-scp")
        near = {f"ligpor-spb-far-{D5}": ("Sporting Braga vs. SC Farense", _game(
            "ligpor", "Liga Portugal", D5, "Sep 5, 2026", _team("spb", "Sporting Braga", "ligpor"), far,
            GIL_ACV_AT))}
        tr: dict = {}
        # v2: a shared word is not a name -- 'Sporting CP' is not 'Sporting
        # Braga' (one side's tokens must be a subset of the other's)
        assert premap._yn_pick(_board({}, extra=near), "Yes", title, slug, ev, tr, kick=_at(GIL_ACV_AT)) == []
        assert (tr["refusal"], tr["why"], tr["his_club"], tr["venue_club"]) == (UNWIT, "names", "sporting cp",
                                                                                "sporting braga")
        apart = {f"ligpor-boa-far-{D5}": ("Boavista FC vs. SC Farense", _game(
            "ligpor", "Liga Portugal", D5, "Sep 5, 2026", _team("boa", "Boavista FC", "ligpor"), far,
            GIL_ACV_AT))}
        tr = {}
        assert premap._yn_pick(_board({}, extra=apart), "Yes", title, slug, ev, tr, kick=_at(GIL_ACV_AT)) == []
        assert (tr["refusal"], tr["why"], tr["his_club"], tr["venue_club"]) == (UNWIT, "names", "sporting cp",
                                                                                "boavista fc")
        # furniture alone is no shared token ('FC' / 'FC')
        tr = {}
        assert premap._yn_pick(_board({}, extra=apart), "Yes", "Will Porto FC win on 2026-09-06?",
                               slug, "Porto FC vs. SC Farense", tr, kick=_at(GIL_ACV_AT)) == []
        assert (tr["refusal"], tr["why"]) == (UNWIT, "names")
        # no name of his for the other club (no event title): nothing to veto
        tr = {}
        assert premap._yn_pick(_board({}, extra=apart), "Yes", "Will SC Farense win on 2026-09-06?",
                               f"por-boa-far-{D}-far", None, tr, kick=_at(GIL_ACV_AT))
        assert tr["club_by_exclusion"] == {"boa": "boavista fc"}

    def test_the_wording_arms_row_answers_to_the_instant_and_the_stem(self, armed):
        """Review H1 / H2, the census's reading: the tsc row the kickoff
        key fetched is refused by the guard (yn_c7.wording) and the
        family ride names the same refusal."""
        rows = _with(_board(), V_GA_TOTAL, game_start=_at("2026-09-06T19:31:00Z"))
        ex = _explain(rows, "ga-total", "Over")
        assert ex["step"] == "no_side_match" and ex["split"] == NO_EVENT
        assert ex["yn_c7"]["wording"] == {"refusal": NO_EVENT, "why": "family-row", "admitted": V_GA_TOTAL}
        rows = _with(_board(), V_GA_TOTAL, game_start=None)
        assert _resolve(rows, "ga-total", "Over") is None
        assert _explain(rows, "ga-total", "Over")["yn_c7"]["wording"]["why"] == "family-row"
        # the certified case records the stem and the exclusion, maps as before
        ex = _explain(_board(), "ga-total", "Over")
        # (his kickoff keys fetch gil-acv's rows alone: vit-cas names no club of his)
        assert ex["step"] == "resolves" and ex["yn_c7"]["wording"] == {
            "foreign_stem": f"ligpor-gil-acv-{D5}", "certified": f"ligpor-gil-acv-{D5}",
            "club_by_exclusion": EXCL_GIL, "events": [f"ligpor-gil-acv-{D5}"]}
        # a row of HIS OWN stem with no stated instant is untouched (every
        # other sport's rows, swept before 055)
        own = {f"por-gil-acv-{D}": (GIL_ACV, [_total(f"tsc-por-gil-acv-{D}-2pt5",
                                                     "Will the total in GIL vs ACV be more than 2.5?", "", "2.5")])}
        rows = _board({}, extra=own)
        assert _resolve(rows, "ga-total", "Over")["market_slug"] == f"tsc-por-gil-acv-{D}-2pt5"

    def test_never_reused_for_another_instant(self, armed):
        rows = _board()
        h = asyncio.run(premap.resolve(_Pool(rows, starts={"0xnext": _at("2026-09-13T19:30:00Z")}),
                                       "Will Gil Vicente FC win on 2026-09-13?", GIL_ACV, "Yes",
                                       "por-gil-acv-2026-09-13-gil", condition_id="0xnext"))
        assert h is None
        ex = asyncio.run(premap.resolve_explain(_Pool(rows, starts={"0xnext": _at("2026-09-13T19:30:00Z")}),
                                                "Will Gil Vicente FC win on 2026-09-13?", GIL_ACV, "Yes",
                                                "por-gil-acv-2026-09-13-gil", condition_id="0xnext"))
        assert ex["step"] == "no_key_intersection" and ex["yn_c7"]["his_game_start"] == "2026-09-13T19:30Z"

    def test_the_refusal_names_are_closed_and_the_arm_is_pure(self):
        src = inspect.getsource(premap)
        names = {m.group(1) for m in re.finditer(r'"(kick:[a-z-]+)"', src)}
        assert names == {UNKNOWN, NO_EVENT, AMBIG, UNWIT, SHEAR, CONFLICT, TYPE}
        for fn in (premap._c7_kick_pick, premap._c7_kick_draw_pick, premap._c7_certify, premap._c7_events,
                   premap._c7_team_row, premap._c7_merged):
            s = inspect.getsource(fn)
            for forbidden in ("await", "pool", "_get_client", "os.getenv", "ordering", "safeName", "alias"):
                assert forbidden not in s, (fn.__name__, forbidden)
            for lit in ("'ligpor'", '"ligpor"', "'gil'", '"gil"', "barcelos", "'por'", '"por"'):
                assert lit not in s, (fn.__name__, lit)
        # the names lane's own functions are untouched by C7 (M3's lane, built beside)
        for fn in (premap._yn_names_pick, premap._yn_names_draw_pick, premap.names_resolve, premap._yn_draw_pick):
            assert "kick" not in inspect.getsource(fn), fn.__name__


# ------------------------------------------------------- the earlier arms run first

class TestTheEarlierArmsRunFirst:
    def test_k_the_names_lane_maps_millonarios_and_colo_colo_and_c7_never_runs(self, armed):
        rows = _board()
        for key, want in (("mif", V_MIL), ("csc", V_COL)):
            pool = _Pool(rows)
            h = _resolve(rows, key, "Yes", pool=pool)
            assert _short(h) == (want, "yes", LONG, "premap_names"), key
            assert "club_by_exclusion" not in h and "game_start" not in h
            _out, tr = _pick(rows, key, "Yes")
            assert tr["matched_by"] == "premap_names" and "kick" not in tr and "game_start" not in tr

    def test_k_with_no_event_title_c7_maps_them_by_the_instant_and_the_anchor(self, armed):
        """The names lane refuses yn:names-unwitnessed (no event title,
        nothing stored): his title's club is the venue's record at his
        instant, at his tail's position; the other club is bound by
        exclusion to his slug's other code."""
        rows = _board()
        pool = _Pool(rows)
        h = _resolve(rows, "mif", "Yes", ev=None, pool=pool)
        assert _short(h) == (V_MIL, "yes", LONG, "premap_kickoff")
        assert h["club_by_exclusion"] == {"dep": "deportivo pereira"} and h["witness"] == V_MIL
        assert h["code_pair"] == {"dep": "per", "mif": "mil"} and h["league_alias"] == "col1->lco"
        assert "titles" not in pool.reads                 # the stored-title witness was not needed
        h = _resolve(rows, "csc", "No", ev=None)
        assert _short(h) == (V_COL, "no", SHORT, "premap_kickoff")
        assert h["club_by_exclusion"] == {"cdh": "cd huachipato"}
        _out, tr = _pick(rows, "mif", "Yes", ev=None)
        assert tr["matched_by"] == "premap_kickoff" and tr["witness_src"] == "record"
        # and with the instant unknown the names lane's answer stands, and
        # names_resolve's stored-title witness still reads after it
        pool = _Pool(rows, starts={}, titles={f"col1-dep-mif-{D}-dep": ["Will Deportivo Pereira win on 2026-09-06?"]})
        h = _resolve(rows, "mif", "Yes", ev=None, pool=pool)
        assert _short(h) == (V_MIL, "yes", LONG, "premap_names") and "titles" in pool.reads
        _out, tr = _pick(rows, "mif", "Yes", ev=None, kick=_at("2026-09-06T23:11:00Z"))
        assert tr["refusal"] == "yn:names-unwitnessed" and tr["kick"]["refusal"] == NO_EVENT

    def test_c5_owns_a_shared_code_and_c7_stands_aside(self, armed):
        rows = _board()
        h = _resolve(rows, "mun", "Yes")
        assert _short(h) == (f"atc-epl-eve-mnu-{D}-mnu", "yes", LONG, "premap_identity")
        assert h["code_translated"] == {"mun": "mnu"} and "club_by_exclusion" not in h
        _out, tr = _pick(rows, "mun", "Yes")
        assert tr["refusal"] == "yn:no-row" and "kick" not in tr

    def test_his_own_identifier_refused_is_never_named_around(self, armed):
        own = {f"por-gil-acv-{D}": (GIL_ACV, [_ml(f"atc-por-gil-acv-{D}-gil", P4.format(
            a="Gil Vicente Barcelos", b="Academico de Viseu FC", lg="Liga Portugal Feminina", d="Sep 6, 2026"),
            GIL_ACV_AT, GIL)])}
        rows = _board(extra=own)
        assert _resolve(rows, "gil", "Yes") is None
        assert _explain(rows, "gil", "Yes")["split"] == "yn:league-slot"


# ------------------------------------------------------- the families ride the certified stem

class TestTheFamiliesRideTheStem:
    def test_the_full_game_total_maps_through_the_wording_arm_once_fetched(self, armed):
        """The kickoff key is a lookup: once the tsc row is fetched by
        his instant, the full-game total maps as every full-game total
        does (§11: the wording arm first, matched_by 'premap')."""
        rows = _board()
        h = _resolve(rows, "ga-total", "Over")
        assert _short(h) == (V_GA_TOTAL, "over 2 5", LONG, "premap")
        assert "club_by_exclusion" not in h
        assert _short(_resolve(rows, "ga-total", "Under")) == (V_GA_TOTAL, "under 2 5", SHORT, "premap")
        # an unlisted line: the C3 lane's own name on the certified stem
        assert _resolve(rows, "ga-total3", "Over") is None
        ex = _explain(rows, "ga-total3", "Over")
        assert ex["split"] == "total:line-absent" and ex["yn_c7"]["c3"]["refusal"] == "total:line-absent"
        assert ex["yn_c7"]["c3"]["slug"] == f"ligpor-gil-acv-{D5}-total-3pt5"
        # no instant: nothing of his meets the rows
        assert _resolve(rows, "ga-total", "Over", cid=None) is None
        assert _explain(rows, "ga-total", "Over", cid=None)["step"] == "no_key_intersection"

    def test_the_spread_rides_the_certified_stem(self, armed):
        """A spread is C3's own pick (the wording arm never names a team
        outcome on a yes/no row): the stem certified at 19:30Z, his
        slug rewritten to it, the spread lane's names / sign / line
        deciding as today."""
        rows = _board()
        h = _resolve(rows, "ga-spread", "Académico de Viseu FC")
        assert _short(h) == (V_GA_SPREAD, "no", SHORT, "premap_spread")
        assert h["club_by_exclusion"] == EXCL_GIL and h["witness"] == V_ACV and h["game_start"] == GIL_MINUTE
        assert h["league_alias"] == "por->ligpor" and h["code_pair"] == {"gil": "gil", "acv": "acv"}
        ex = _explain(rows, "ga-spread", "Académico de Viseu FC")
        assert ex["step"] == "resolves" and ex["detail"] == V_GA_SPREAD
        assert ex["yn_c7"]["c3"]["slug"] == f"ligpor-gil-acv-{D5}-spread-away-1pt5"
        assert ex["yn_c7"]["c3"]["label"] == "kickoff-certified" and ex["yn_c7"]["c3"]["lane"]["side"] == "no"
        # the btts row names 'Gil Vicente Barcelos' where his title says
        # 'Gil Vicente FC': the family lane's own witness refuses by name
        assert _resolve(rows, "ga-btts", "Yes") is None
        ex = _explain(rows, "ga-btts", "Yes")
        assert ex["split"] == "btts:names" and ex["yn_c7"]["c3"]["refusal"] == "btts:names"

    def test_the_family_row_at_another_instant_refuses(self, armed):
        rows = _with(_board(), V_GA_SPREAD, game_start=_at("2026-09-06T19:31:00Z"))
        assert _resolve(rows, "ga-spread", "Académico de Viseu FC") is None
        ex = _explain(rows, "ga-spread", "Académico de Viseu FC")
        assert ex["split"] == NO_EVENT and ex["yn_c7"]["c3"]["why"] == "family-row"


# ------------------------------------------------------- the mirror, the switch

class TestTheMirrorAndTheSwitch:
    def test_the_shadow_hands_the_condition_to_the_resolver(self):
        ctx = ms._first_context([{"market_title": "t", "market_slug": FEED["gil"][0], "condition_id": "0xgilacv"}])
        assert ctx["condition_id"] == "0xgilacv"
        src = inspect.getsource(ms.explain_unmapped)
        assert 'condition_id=ctx.get("condition_id")' in src
        src = inspect.getsource(ms.map_market)
        assert "condition_id=condition_id" in src.split("_premap.resolve(")[1]

    def test_dark_every_row_answers_none_and_nothing_is_read(self, dark):
        rows = _board()
        for key in ("gil", "acv", "ga-draw", "cds", "rio", "cr-draw", "ga-total", "ga-spread"):
            pool = _Pool(rows)
            assert _resolve(rows, key, "Yes", pool=pool) is None, key
            assert "market_starts" not in pool.reads
            ex = _explain(rows, key, "Yes")
            assert "yn_c7" not in ex and ex["step"] == "no_key_intersection", key
        # the names lane's own dark reading is untouched (its keys meet)
        pool = _Pool(rows)
        assert _resolve(rows, "mif", "Yes", pool=pool) is None and "market_starts" not in pool.reads
        ex = _explain(rows, "mif", "Yes")
        assert "yn_c7" not in ex and ex["split"] == "yn:would-resolve"
        assert ex["yn_c2"]["matched_by"] == "premap_names" and "kick" not in ex["yn_c2"]
