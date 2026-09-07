"""C6 (2026-09-07): college football -- the venue's own team field binds
mascot, code and school.

The venue rows are verbatim: cfb_venue_1353.log / c4_rows_0305.log (the
aec-cfb-lou-miss / wisc-nd / scarst-flam rows, sides cardinals/rebels,
badgers/fighting irish, bulldogs/rattlers, us_premap.event_title
'Louisville vs. Ole Miss' ...; the asc full-game rows 'Will the
Cardinals cover -6.5 vs the Rebels in Cardinals vs. Rebels?'; the atc
`winner-<seg>-<code>` rows 'Will Louisville win the first half?' under
`-lou`, 'Will Notre Dame win the first half?' under `-nd`, 'Will the
first half end tied?' under `-draw`). The SDK `team` dicts are the
PREMAP-TEAM probe's lines (engine-diagnostic run 34131932442, 14:15:54Z),
verbatim for lou-miss and smu-flst; the probe printed no dict for
wisc-nd / scarst-flam, so those fixtures are built in the probe's shape
(abbreviation = the slug code, safeName = the school the venue's own
winner rows / event_title state) and say so. His rows are
cfb_his_1353.log / c4_rows_0305.log: `cfb-lou-miss-2026-09-06-spread-
home-6pt5` outcome 'Louisville', title 'Spread: Ole Miss (-6.5)', event
title 'Louisville vs. Ole Miss' (oi 1); the moneyline cfb-wisc-nd
'Notre Dame' (oi 1) / 'Wisconsin' (oi 0); scarst-flam 7pt5 'South
Carolina State' / 'Spread: South Carolina State (-7.5)'.

The rules, and nothing else (docs/mirror-coverage.md, the C6 section):
C6c-store, the sweep keeps the team field; C6a, the winner rows witness
code -> school; C6c, the aec side's team_abbr == code AND team_safe_name
names the school certifies the subject; C6b, his event title witnesses
the pair where his title names his own team; C6-ML, the moneyline class
certifies the side from the same field and admits the winner row as
the code's contract. Identity only, fail closed.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import timezone

import pytest

from sportsassets import map_lane
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import premap
from tests.test_c1_round2 import _cert_env, _live_tick
from tests.test_c1_round3 import _PremapPool, _row
from tests.test_c4_cfb_spreads import _Pool, _short, _state
from tests.test_mirror_live_worker import _pool as _live_pool
from tests.test_mirror_maps_the_copy_lane import _Venue, _with_venue

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
D = "2026-09-06"
LM, WN, SF, SM = f"cfb-lou-miss-{D}", f"cfb-wisc-nd-{D}", f"cfb-scarst-flam-{D}", "cfb-smu-flst-2026-09-07"
AEC_LM, AEC_WN, AEC_SF, AEC_SM = ("aec-" + s for s in (LM, WN, SF, SM))
EVENTS = {AEC_LM: "Louisville vs. Ole Miss", AEC_WN: "Wisconsin vs. Notre Dame",
          AEC_SF: "South Carolina State vs. Florida A&M", AEC_SM: "SMU vs. Florida State"}
_REAL_CONTRACT_CANDIDATES = ml._contract_candidates

# PREMAP-TEAM, verbatim (14:15:54Z)
TEAM_SMU = {"abbreviation": "smu", "alias": "Mustangs", "conference": "Atlantic Coast",
            "displayAbbreviation": "SMU", "id": 1067, "league": "cfb", "name": "Mustangs",
            "ordering": "away", "ranking": "19", "record": "0-0", "safeName": "SMU"}
TEAM_FLST = {"abbreviation": "flst", "alias": "Seminoles", "displayAbbreviation": "FSU", "id": 1090,
             "league": "cfb", "name": "Seminoles", "ordering": "home", "record": "1-0",
             "safeName": "Florida State"}
TEAM_LOU = {"abbreviation": "lou", "alias": "Cardinals", "displayAbbreviation": "LOU", "id": 1085,
            "league": "cfb", "name": "Cardinals", "ordering": "away", "ranking": "24", "record": "0-1",
            "safeName": "Louisville"}
TEAM_MISS = {"abbreviation": "miss", "alias": "Rebels", "displayAbbreviation": "MISS", "id": 1258,
             "league": "cfb", "name": "Rebels", "ordering": "home", "ranking": "9", "record": "1-0",
             "safeName": "Ole Miss"}
# built in the probe's shape (the probe printed no wisc-nd / scarst-flam
# dict): abbreviation = the slug code, safeName = the school the venue's
# own winner rows ('Will Notre Dame win the first half?') and event_title
# ('South Carolina State vs. Florida A&M') state, name = the aec side
TEAM_WISC = {"abbreviation": "wisc", "alias": "Badgers", "id": 1, "league": "cfb", "name": "Badgers",
             "ordering": "away", "safeName": "Wisconsin"}
TEAM_ND = {"abbreviation": "nd", "alias": "Fighting Irish", "id": 2, "league": "cfb",
           "name": "Fighting Irish", "ordering": "home", "safeName": "Notre Dame"}
TEAM_SCS = {"abbreviation": "scarst", "alias": "Bulldogs", "id": 3, "league": "cfb", "name": "Bulldogs",
            "ordering": "away", "safeName": "South Carolina State"}
TEAM_FAMU = {"abbreviation": "flam", "alias": "Rattlers", "id": 4, "league": "cfb", "name": "Rattlers",
             "ordering": "home", "safeName": "Florida A&M"}

Q_LM = "Who will win in the upcoming football event Cardinals vs Rebels scheduled for September 6, 2026 at 11:30 PM UTC?"
Q_WN = "Who will win in the upcoming football event Badgers vs Fighting Irish scheduled for September 6, 2026 at 11:30 PM UTC?"
Q_SF = "Who will win in the upcoming football event Bulldogs vs Rattlers scheduled for September 6, 2026 at 7:00 PM UTC?"
Q_SM = "Who will win in the upcoming football event Mustangs vs Seminoles scheduled for September 7, 2026 at 11:30 PM UTC?"
SEGS = {"1h": "first half", "1q": "first quarter", "2h": "second half", "2q": "second quarter",
        "3q": "third quarter", "4q": "fourth quarter"}


def _side(ident, desc, team, long):
    return {"identifier": ident, "description": desc, "long": long, "team": team,
            "teamId": team.get("id") if isinstance(team, dict) else None}


def _aec(ident, q, a, b, ta, tb, **extra):
    return {"slug": ident, "question": q, "closed": False,
            "marketSides": [_side(ident, a, ta, True), _side(ident, b, tb, False)], **extra}


def _asc(ident, q):
    line = ident.rsplit("-", 1)[-1].replace("pt", ".")
    return {"slug": ident, "question": q, "closed": False, "marketSides": [
        _side(ident, line, None, True), _side(ident, line, None, False)]}


def _atc(ident, q):
    # the winner rows' shape, verbatim: yes/no sides, team=null on both
    return {"slug": ident, "question": q, "closed": False,
            "marketSides": [_side(ident, "Yes", None, True), _side(ident, "No", None, False)]}


def _winner_rows(ev, codes, schools):
    out = []
    for seg, word in SEGS.items():
        out.append((ev, _atc(f"atc-{ev[4:]}-winner-{seg}-draw", f"Will the {word} end tied?")))
        for c, s in zip(codes, schools):
            out.append((ev, _atc(f"atc-{ev[4:]}-winner-{seg}-{c}", f"Will {s} win the {word}?")))
    return out


def _spreads(ev, ma, mb, lines):
    out = []
    for ln in lines:
        L = ln.replace("pt", ".")
        out.append((ev, _asc(f"asc-{ev[4:]}-neg-{ln}", f"Will the {ma} cover -{L} vs the {mb} in {ma} vs. {mb}?")))
        out.append((ev, _asc(f"asc-{ev[4:]}-pos-{ln}", f"Will the {ma} cover {L} vs the {mb} in {ma} vs. {mb}?")))
    return out


AEC_LM_MK = _aec(AEC_LM, Q_LM, "Cardinals", "Rebels", TEAM_LOU, TEAM_MISS)
AEC_WN_MK = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", TEAM_WISC, TEAM_ND)
AEC_SF_MK = _aec(AEC_SF, Q_SF, "Bulldogs", "Rattlers", TEAM_SCS, TEAM_FAMU)
AEC_SM_MK = _aec(AEC_SM, Q_SM, "Mustangs", "Seminoles", TEAM_SMU, TEAM_FLST)
LM_LINES = ("1pt5", "2pt5", "6pt5", "7pt5", "13pt5")
WN_LINES = ("13pt5", "21pt5", "24pt5")


def _venue_lm(aec=AEC_LM_MK, winner=True):
    out = _spreads(AEC_LM, "Cardinals", "Rebels", LM_LINES)
    if winner:
        out += _winner_rows(AEC_LM, ("lou", "miss"), ("Louisville", "Ole Miss"))
    if aec is not None:
        out.append((AEC_LM, aec))
    return out


def _venue_wn(aec=AEC_WN_MK, winner=True):
    out = _spreads(AEC_WN, "Badgers", "Fighting Irish", WN_LINES)
    if winner:
        out += _winner_rows(AEC_WN, ("wisc", "nd"), ("Wisconsin", "Notre Dame"))
    if aec is not None:
        out.append((AEC_WN, aec))
    return out


def _venue_sf(aec=AEC_SF_MK):
    # no atc rows on this event (cfb-venue 13:53Z: aec 2, asc 48, tsc 14)
    out = _spreads(AEC_SF, "Bulldogs", "Rattlers", ("7pt5", "10pt5"))
    if aec is not None:
        out.append((AEC_SF, aec))
    return out


def _board(venue) -> list[dict]:
    rows: list[dict] = []
    for ev, m in venue:
        keys = premap.event_keys_for(EVENTS[ev], ev)
        for r in premap._market_rows({"slug": ev, "title": EVENTS[ev]}, m):
            r["event_keys"] = keys
            rows.append(r)
    return rows


def _resolve(rows, slug, title, outcome, event_title, state=None):
    return asyncio.run(premap.resolve(_Pool(rows, state), title, event_title, outcome, slug))


def _explain(rows, slug, title, outcome, event_title, state=None):
    return asyncio.run(premap.resolve_explain(_Pool(rows, state), title, event_title, outcome, slug))


EV_LM, EV_WN, EV_SF = EVENTS[AEC_LM], EVENTS[AEC_WN], EVENTS[AEC_SF]


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


# --------------------------------------------- (a) the sweep stores the field

class TestTheSweepStoresTheTeamField:
    def test_the_probes_payload_rows(self):
        rows = premap._market_rows({"slug": AEC_LM, "title": EV_LM}, AEC_LM_MK)
        assert [(r["side_norm"], r["team_abbr"], r["team_safe_name"], r["team_name"], r["team_id"], r["team_league"])
                for r in rows] == [("cardinals", "lou", "louisville", "cardinals", 1085, "cfb"),
                                   ("rebels", "miss", "ole miss", "rebels", 1258, "cfb")]
        rows = premap._market_rows({"slug": AEC_SM, "title": EVENTS[AEC_SM]}, AEC_SM_MK)
        assert [(r["side_norm"], r["team_abbr"], r["team_safe_name"], r["team_id"]) for r in rows] == [
            ("mustangs", "smu", "smu", 1067), ("seminoles", "flst", "florida state", 1090)]
        assert all(r["intent"] == (LONG if r["side_norm"] == "mustangs" else SHORT) for r in rows)
        # the atc winner rows: team=null on both sides -> NULL, the yes/no rows as before
        rows = premap._market_rows({"slug": AEC_WN, "title": EV_WN},
                                   _atc(f"atc-{WN}-winner-1h-nd", "Will Notre Dame win the first half?"))
        assert [(r["side_norm"], r["intent"]) for r in rows] == [("yes", LONG), ("no", SHORT)]
        assert all(r["team_abbr"] is None and r["team_safe_name"] is None and r["team_id"] is None
                   and r["team_name"] is None and r["team_league"] is None for r in rows)
        # the asc rows of the fixtures carry no team either
        rows = premap._market_rows({"slug": AEC_LM, "title": EV_LM},
                                   _asc(f"asc-{LM}-pos-6pt5", "Will the Cardinals cover 6.5 vs the Rebels in Cardinals vs. Rebels?"))
        assert [r["side_norm"] for r in rows] == ["yes", "no"] and all(r["team_abbr"] is None for r in rows)

    def test_the_market_level_fields_and_the_unreadable_shapes(self):
        m = dict(AEC_LM_MK, gameStartTime="2026-09-06T23:30:00Z", sportsMarketType="MONEYLINE")
        rows = premap._market_rows({"slug": AEC_LM, "title": EV_LM}, m)
        gs = rows[0]["game_start"]
        assert gs.tzinfo is not None and gs.astimezone(timezone.utc).isoformat() == "2026-09-06T23:30:00+00:00"
        assert rows[0]["sports_type"] == "MONEYLINE"
        # nothing derived: an unreadable field is None, never a guess
        t = premap._side_team({"team": {"abbreviation": " LOU ", "safeName": "Louisville", "id": "x"},
                               "teamId": "1085"}, {"gameStartTime": "not a date"})
        assert (t["team_abbr"], t["team_safe_name"], t["team_id"], t["game_start"], t["sports_type"]) == \
            ("lou", "louisville", 1085, None, None)
        assert premap._side_team({"team": "Cardinals"}, {})["team_abbr"] is None
        assert premap._side_team({"team": {"id": True}}, {})["team_id"] is None
        assert premap._side_team({}, {"gameStartTime": 1789000000})["game_start"].tzinfo is not None
        # the mascot is never stored as the school
        assert premap._side_team({"team": {"name": "Cardinals", "alias": "Cardinals"}}, {})["team_safe_name"] is None

    def test_the_upsert_writes_the_seven_columns(self, monkeypatch):
        calls = []

        class _P:
            def __init__(self, n=7):
                self.n, self.probes = n, 0

            async def fetchval(self, sql, *a):
                assert "information_schema.columns" in sql and sorted(a[0]) == sorted(premap._TEAM_COLUMNS)
                self.probes += 1
                if isinstance(self.n, Exception):
                    raise self.n
                return self.n

            async def execute(self, sql, *a):
                calls.append((sql, a))

        monkeypatch.setattr(premap, "_TEAM_COLS_STATE", {"present": None, "at": 0.0})
        p = _P()
        r = premap._market_rows({"slug": AEC_LM, "title": EV_LM},
                                dict(AEC_LM_MK, sportsMarketType="MONEYLINE"))[0]
        asyncio.run(premap._upsert(p, r, ["k"]))
        sql, a = calls[0]
        for col in ("team_abbr", "team_name", "team_safe_name", "team_id", "team_league", "game_start", "sports_type"):
            assert col in sql and f"{col}=$" in sql, col     # written, and rewritten on conflict
        assert len(a) == 18
        assert a[11:18] == ("lou", "cardinals", "louisville", 1085, "cfb", None, "MONEYLINE")
        r = premap._market_rows({"slug": AEC_WN, "title": EV_WN},
                                _atc(f"atc-{WN}-winner-1h-nd", "Will Notre Dame win the first half?"))[0]
        asyncio.run(premap._upsert(p, r, ["k"]))
        assert calls[1][1][11:18] == (None, None, None, None, None, None, None)
        assert p.probes == 1, "the columns are probed once per process"

    def test_the_deploy_window(self, monkeypatch):
        # M5 review MEDIUM-4: a database without 055 (the workers never
        # migrate) writes and reads the pre-C6 shape; nothing raises
        calls = []

        class _P:
            def __init__(self, n):
                self.n, self.probes = n, 0

            async def fetchval(self, sql, *a):
                self.probes += 1
                if isinstance(self.n, Exception):
                    raise self.n
                return self.n

            async def execute(self, sql, *a):
                calls.append((sql, a))

        for n in (0, 5, None, RuntimeError("unreadable")):
            monkeypatch.setattr(premap, "_TEAM_COLS_STATE", {"present": None, "at": 0.0})
            p = _P(n)
            r = premap._market_rows({"slug": AEC_LM, "title": EV_LM}, AEC_LM_MK)[0]
            asyncio.run(premap._upsert(p, r, ["k"]))
            sql, a = calls[-1]
            assert "team_abbr" not in sql and len(a) == 11, n
            assert asyncio.run(premap.team_select_cols(p)) == ""
            assert premap.TEAM_SELECT_COLS == "team_abbr, team_safe_name, "
            # absent is asked again after the re-probe window, present is final
            assert p.probes == 1
            premap._TEAM_COLS_STATE["at"] -= premap._TEAM_COLS_REPROBE_S + 1
            p.n = 7
            assert asyncio.run(premap.team_select_cols(p)) == premap.TEAM_SELECT_COLS and p.probes == 2
            p.n = RuntimeError("later")
            assert asyncio.run(premap.team_select_cols(p)) == premap.TEAM_SELECT_COLS and p.probes == 2
        # the readers add the fragment the probe chose to the pinned SELECT
        for fn in (premap.resolve, premap.resolve_explain):
            src = inspect.getsource(fn)
            assert "await team_select_cols(pool)" in src
            assert "intent, signed, event_slug, market_slug FROM us_premap" in src
        # the sweep ensures the columns itself (the `signed` precedent), then re-probes
        src = inspect.getsource(premap._ensure_table)
        for col, typ in (("team_abbr", "text"), ("team_safe_name", "text"), ("team_id", "bigint"),
                         ("game_start", "timestamptz"), ("sports_type", "text")):
            assert f'("{col}", "{typ}")' in src, col
        assert "ADD COLUMN IF NOT EXISTS {_col} {_typ}" in src and '_TEAM_COLS_STATE["present"] = None' in src


# ---------------------------------------------- C6a: the venue's code witness

class TestTheCodeWitness:
    def test_the_verbatim_rows(self):
        assert map_lane.code_school(_board(_venue_wn()), "cfb", "wisc", "nd", D) == \
            ({"wisc": "wisconsin", "nd": "notre dame"}, None)
        assert map_lane.code_school(_board(_venue_lm()), "cfb", "lou", "miss", D) == \
            ({"lou": "louisville", "miss": "ole miss"}, None)
        # no atc rows (scarst-flam): no witness, today's rules stand
        assert map_lane.code_school(_board(_venue_sf()), "cfb", "scarst", "flam", D) == (None, None)
        # one code witnessed only: no witness
        rows = _board([(AEC_WN, _atc(f"atc-{WN}-winner-1h-nd", "Will Notre Dame win the first half?"))])
        assert map_lane.code_school(rows, "cfb", "wisc", "nd", D) == (None, None)
        # the draw rows and the prop rows state nothing
        assert map_lane.winner_school("Will the first half end tied?") is None
        assert map_lane.winner_school("Notre Dame 1H winner") is None
        assert map_lane.winner_school("Will Notre Dame win the first half?") == "notre dame"
        assert map_lane.winner_school("Will Florida A&M win the fourth quarter?") == "florida a m"

    def test_the_venue_contradicting_itself(self):
        rows = _board(_winner_rows(AEC_WN, ("wisc", "nd"), ("Wisconsin", "Notre Dame")))
        rows.append({"identifier": f"atc-{WN}-winner-4q-nd", "question": "Will North Dakota win the fourth quarter?"})
        assert map_lane.code_school(rows, "cfb", "wisc", "nd", D) == (None, "spread:code-witness-disagree")
        rows = _board(_winner_rows(AEC_WN, ("wisc", "nd"), ("Notre Dame", "Notre Dame")))
        assert map_lane.code_school(rows, "cfb", "wisc", "nd", D) == (None, "spread:code-witness-same")
        # token-set equality, never a prefix: 'Ole Miss' beside 'Mississippi State' are two schools
        assert map_lane.same_name("ole miss", "mississippi state") is False
        assert map_lane.same_name("notre dame", "notre dame") and not map_lane.same_name("notre dame", "north dakota")


# ------------------------------------------------- (b) (c) (g) the spreads

class TestTheMascotSpreadThroughTheTeamField:
    def test_lou_miss_6pt5_louisville(self, armed):
        # c4_rows_0305.log: 'Louisville' (oi 1), title 'Spread: Ole Miss (-6.5)',
        # event title 'Louisville vs. Ole Miss' -- Louisville +6.5 = the
        # Cardinals (a) cover +6.5: pos-6pt5 YES, the venue's long side
        rows = _board(_venue_lm())
        h = _resolve(rows, f"{LM}-spread-home-6pt5", "Spread: Ole Miss (-6.5)", "Louisville", EV_LM)
        assert _short(h) == (f"asc-{LM}-pos-6pt5", "yes", LONG, "premap_spread_code")
        ex = _explain(rows, f"{LM}-spread-home-6pt5", "Spread: Ole Miss (-6.5)", "Louisville", EV_LM)
        assert ex["step"] == "resolves" and ex["matched_by"] == "premap_spread_code"
        c3 = ex["c3"]
        assert c3["code_school"] == {"lou": "louisville", "miss": "ole miss"}
        assert c3["code_hits"] == ["lou"] and c3["title_hits"] == ["miss"]
        assert c3["subject"] == {"certified": "cardinals", "code": "lou", "via": "team"}
        assert c3["his_team"] == "a" and c3["his_signed"] == "+6.5"
        # the state holds no record for the market: the team field certified it
        assert _short(_resolve(rows, f"{LM}-spread-home-6pt5", "Spread: Ole Miss (-6.5)", "Louisville", EV_LM,
                               _state())) == (f"asc-{LM}-pos-6pt5", "yes", LONG, "premap_spread_code")
        # without the winner rows the school for each position is his own
        # word (the pair witness's reading): the same map
        rows = _board(_venue_lm(winner=False))
        h = _resolve(rows, f"{LM}-spread-home-6pt5", "Spread: Ole Miss (-6.5)", "Louisville", EV_LM)
        assert _short(h) == (f"asc-{LM}-pos-6pt5", "yes", LONG, "premap_spread_code")
        ex = _explain(rows, f"{LM}-spread-home-6pt5", "Spread: Ole Miss (-6.5)", "Louisville", EV_LM)
        assert "code_school" not in ex["c3"] and ex["c3"]["subject"]["via"] == "team"

    def test_lou_miss_ole_miss_through_his_event_title(self, armed):
        # 'Ole Miss' / 'Spread: Ole Miss (-1.5)': his title names his own team
        # (C4: pair-unwitnessed); his event title 'Louisville vs. Ole Miss'
        # is the second name -- Ole Miss -1.5 = b gives: pos-1pt5 NO
        rows = _board(_venue_lm())
        h = _resolve(rows, f"{LM}-spread-home-1pt5", "Spread: Ole Miss (-1.5)", "Ole Miss", EV_LM)
        assert _short(h) == (f"asc-{LM}-pos-1pt5", "no", SHORT, "premap_spread_code")
        ex = _explain(rows, f"{LM}-spread-home-1pt5", "Spread: Ole Miss (-1.5)", "Ole Miss", EV_LM)
        assert ex["c3"]["pair_via"] == "event" and ex["c3"]["event_hits"] == [["lou"], ["miss"]]
        assert ex["c3"]["his_team"] == "b" and ex["c3"]["his_signed"] == "-1.5"
        # no event title (lou-miss 13.5/4.5/7.5, markets.event_title NULL): unchanged
        assert _resolve(rows, f"{LM}-spread-home-13pt5", "Spread: Ole Miss (-13.5)", "Ole Miss", None) is None
        ex = _explain(rows, f"{LM}-spread-home-13pt5", "Spread: Ole Miss (-13.5)", "Ole Miss", None)
        assert ex["split"] == "spread:pair-unwitnessed"
        # an event title naming one position twice
        for ev in ("Ole Miss vs. Ole Miss", "Louisville vs. Louisville", "Louisville vs. Mississippi State"):
            assert _resolve(rows, f"{LM}-spread-home-1pt5", "Spread: Ole Miss (-1.5)", "Ole Miss", ev) is None
            ex = _explain(rows, f"{LM}-spread-home-1pt5", "Spread: Ole Miss (-1.5)", "Ole Miss", ev)
            assert ex["split"] in ("spread:event-collision", "spread:pair-unwitnessed"), ev
        ex = _explain(rows, f"{LM}-spread-home-1pt5", "Spread: Ole Miss (-1.5)", "Ole Miss", "Ole Miss vs. Ole Miss")
        assert ex["split"] == "spread:event-collision"
        # an event title of another game: its names read no position
        ex = _explain(rows, f"{LM}-spread-home-1pt5", "Spread: Ole Miss (-1.5)", "Ole Miss", "Baylor vs. Auburn")
        assert ex["split"] == "spread:pair-unwitnessed" and ex["c3"]["event_hits"] == [[], []]

    def test_the_witness_replaces_the_prefix(self, armed):
        # 'Mississippi State' / 'Spread: Ole Miss (-7.5)': under the code
        # rules both names read miss (C4: code-collision); under the
        # venue's witness 'Mississippi State' names NO school: code-unnamed
        rows = _board(_venue_lm())
        ex = _explain(rows, f"{LM}-spread-home-7pt5", "Spread: Ole Miss (-7.5)", "Mississippi State", EV_LM)
        assert ex["split"] == "spread:code-unnamed" and ex["c3"]["code_hits"] == []
        rows = _board(_venue_lm(winner=False))
        ex = _explain(rows, f"{LM}-spread-home-7pt5", "Spread: Ole Miss (-7.5)", "Mississippi State", EV_LM)
        assert ex["split"] == "spread:code-collision"
        # a name matching both schools cannot happen by token sets; pinned
        # on the rule directly
        his = premap.c3_his(f"{LM}-spread-home-7pt5")
        trace: dict = {}
        board = _board(_venue_lm())
        assert premap._c4_subject_by_code(his, "cfb", ("cardinals", "rebels"), "louisville", "louisville",
                                          board, _state(), trace, None) is None
        assert trace["refusal"] == "spread:pair-unwitnessed"
        # the venue disagreeing with itself trips the chain before any name is read
        board.append({"identifier": f"atc-{LM}-winner-4q-lou", "question": "Will Ole Miss win the fourth quarter?",
                      "side_norm": "yes", "event_keys": board[0]["event_keys"]})
        ex = _explain(board, f"{LM}-spread-home-6pt5", "Spread: Ole Miss (-6.5)", "Louisville", EV_LM)
        assert ex["split"] == "spread:code-witness-disagree"

    def test_wisc_nd_through_the_two_letter_code(self, armed):
        # c4_rows_0305.log: 'Notre Dame' / 'Spread: Notre Dame (-13.5)' / event
        # 'Wisconsin vs. Notre Dame' (C4: code-unmatched, `nd` unreadable);
        # 'Wisconsin' / 'Spread: Notre Dame (-21.5)' (C4: title-unreadable)
        rows = _board(_venue_wn())
        h = _resolve(rows, f"{WN}-spread-home-13pt5", "Spread: Notre Dame (-13.5)", "Notre Dame", EV_WN)
        assert _short(h) == (f"asc-{WN}-pos-13pt5", "no", SHORT, "premap_spread_code")
        ex = _explain(rows, f"{WN}-spread-home-13pt5", "Spread: Notre Dame (-13.5)", "Notre Dame", EV_WN)
        assert ex["c3"]["code_hits"] == ["nd"] and ex["c3"]["pair_via"] == "event"
        assert ex["c3"]["subject"] == {"certified": "badgers", "code": "wisc", "via": "team"}
        h = _resolve(rows, f"{WN}-spread-home-21pt5", "Spread: Notre Dame (-21.5)", "Wisconsin", EV_WN)
        assert _short(h) == (f"asc-{WN}-pos-21pt5", "yes", LONG, "premap_spread_code")
        ex = _explain(rows, f"{WN}-spread-home-21pt5", "Spread: Notre Dame (-21.5)", "Wisconsin", EV_WN)
        assert ex["c3"]["code_hits"] == ["wisc"] and ex["c3"]["title_hits"] == ["nd"]
        # without the winner rows `nd` is unreadable, exactly as C4 pinned
        rows = _board(_venue_wn(winner=False))
        ex = _explain(rows, f"{WN}-spread-home-13pt5", "Spread: Notre Dame (-13.5)", "Notre Dame", EV_WN)
        assert ex["split"] == "spread:code-unmatched"
        ex = _explain(rows, f"{WN}-spread-home-21pt5", "Spread: Notre Dame (-21.5)", "Wisconsin", EV_WN)
        assert ex["split"] == "spread:title-unreadable"

    def test_scarst_flam_maps_through_the_team_field_alone(self, armed):
        # no atc rows on the event: the code rules (the split rule) read his
        # words, his event title witnesses the pair, the team field certifies
        rows = _board(_venue_sf())
        h = _resolve(rows, f"{SF}-spread-away-7pt5", "Spread: South Carolina State (-7.5)", "South Carolina State", EV_SF)
        assert _short(h) == (f"asc-{SF}-neg-7pt5", "yes", LONG, "premap_spread_code")
        ex = _explain(rows, f"{SF}-spread-away-7pt5", "Spread: South Carolina State (-7.5)", "South Carolina State", EV_SF)
        assert ex["c3"]["subject"] == {"certified": "bulldogs", "code": "scarst", "via": "team"}
        assert ex["c3"]["event_hits"] == [["scarst"], ["flam"]] and "code_school" not in ex["c3"]
        h = _resolve(rows, f"{SF}-spread-away-7pt5", "Spread: South Carolina State (-7.5)", "Florida A&M", EV_SF)
        assert _short(h) == (f"asc-{SF}-neg-7pt5", "no", SHORT, "premap_spread_code")
        # the team field naming another school for the code: uncertified
        aec = _aec(AEC_SF, Q_SF, "Bulldogs", "Rattlers", dict(TEAM_SCS, safeName="South Carolina"), TEAM_FAMU)
        ex = _explain(_board(_venue_sf(aec)), f"{SF}-spread-away-7pt5", "Spread: South Carolina State (-7.5)",
                      "South Carolina State", EV_SF)
        assert ex["split"] == "spread:subject-uncertified"
        assert ex["c3"]["subject"] == {"team-school": "south carolina", "code": "scarst", "want": "south carolina state"}


# ------------------------------------- (d) (e) the venue contradicting itself

class TestTheSubjectRefusals:
    S, T, OUT = f"{LM}-spread-home-6pt5", "Spread: Ole Miss (-6.5)", "Louisville"

    def test_a_side_bound_to_the_other_code_is_a_conflict(self, armed):
        # side[0] 'Cardinals' carrying abbreviation 'miss' / 'Ole Miss': the
        # venue's own row binds the subject's mascot to b -- nothing maps
        aec = _aec(AEC_LM, Q_LM, "Cardinals", "Rebels", TEAM_MISS, TEAM_LOU)
        rows = _board(_venue_lm(aec))
        assert _resolve(rows, self.S, self.T, self.OUT, EV_LM) is None
        ex = _explain(rows, self.S, self.T, self.OUT, EV_LM)
        assert ex["split"] == "spread:subject-conflict"
        assert ex["c3"]["subject"] == {"certified": "cardinals", "code": "miss", "slot": 0, "via": "team"}
        # the other fill on the same row maps nothing either
        assert _resolve(rows, f"{LM}-spread-home-1pt5", "Spread: Ole Miss (-1.5)", "Ole Miss", EV_LM) is None
        # both sides claiming one code: the opponent's mascot bound to a
        aec = _aec(AEC_LM, Q_LM, "Cardinals", "Rebels", TEAM_LOU, dict(TEAM_MISS, abbreviation="lou", safeName="Louisville"))
        ex = _explain(_board(_venue_lm(aec)), self.S, self.T, self.OUT, EV_LM)
        assert ex["split"] == "spread:subject-conflict" and ex["c3"]["subject"]["slot"] == 1

    def test_team_absent_unnamed_or_another_school(self, armed):
        # one side states no team: half a binding
        aec = _aec(AEC_LM, Q_LM, "Cardinals", "Rebels", TEAM_LOU, None)
        ex = _explain(_board(_venue_lm(aec)), self.S, self.T, self.OUT, EV_LM)
        assert ex["split"] == "spread:subject-uncertified" and ex["c3"]["subject"] == "team-absent"
        # no team on either side, no record: C4's refusal, the team named absent
        aec = _aec(AEC_LM, Q_LM, "Cardinals", "Rebels", None, None)
        ex = _explain(_board(_venue_lm(aec)), self.S, self.T, self.OUT, EV_LM)
        assert ex["split"] == "spread:subject-uncertified"
        assert ex["c3"]["subject"] == "grammar-uncertified" and ex["c3"]["team"] == "absent"
        # the class's record still certifies a row that carries no team (C4)
        rec = {"outcome_desc": "Cardinals", "side_index": 0, "his_slug": LM, "at": 1.0}
        h = _resolve(_board(_venue_lm(aec)), self.S, self.T, self.OUT, EV_LM, _state({AEC_LM: rec}))
        assert _short(h) == (f"asc-{LM}-pos-6pt5", "yes", LONG, "premap_spread_code")
        # an abbreviation that is neither code
        aec = _aec(AEC_LM, Q_LM, "Cardinals", "Rebels", dict(TEAM_LOU, abbreviation="LOUI"), TEAM_MISS)
        ex = _explain(_board(_venue_lm(aec)), self.S, self.T, self.OUT, EV_LM)
        assert ex["c3"]["subject"] == {"team-unnamed": "loui", "mascot": "cardinals"}
        # the venue's school disagreeing with its own winner rows
        aec = _aec(AEC_LM, Q_LM, "Cardinals", "Rebels", dict(TEAM_LOU, safeName="Louisiana"), TEAM_MISS)
        ex = _explain(_board(_venue_lm(aec)), self.S, self.T, self.OUT, EV_LM)
        assert ex["split"] == "spread:subject-uncertified"
        assert ex["c3"]["subject"] == {"team-school": "louisiana", "code": "lou", "want": "louisville"}
        # the mascot is never the school: a team dict with name/alias only
        aec = _aec(AEC_LM, Q_LM, "Cardinals", "Rebels", {"abbreviation": "lou", "name": "Cardinals", "alias": "Cardinals"},
                   {"abbreviation": "miss", "name": "Rebels", "alias": "Rebels"})
        ex = _explain(_board(_venue_lm(aec)), self.S, self.T, self.OUT, EV_LM)
        assert ex["c3"]["subject"]["team-school"] == ""
        # the class's rails stand: tripped or unreadable refuses before the row is read
        ex = _explain(_board(_venue_lm()), self.S, self.T, self.OUT, EV_LM, _state(tripped=True))
        assert ex["c3"]["subject"] == "grammar-tripped"

    def test_a_class_record_contradicting_the_row(self, armed):
        # M5 review MEDIUM-3: the record certifies Cardinals at position 1
        # while the row binds Cardinals to lou (0): two venue statements
        # that disagree -- conflict, nothing maps; an agreeing record, or
        # one of another shape, changes nothing
        rows = _board(_venue_lm())
        rec = {"outcome_desc": "Cardinals", "side_index": 1, "his_slug": LM, "at": 1.0}
        assert _resolve(rows, self.S, self.T, self.OUT, EV_LM, _state({AEC_LM: rec})) is None
        ex = _explain(rows, self.S, self.T, self.OUT, EV_LM, _state({AEC_LM: rec}))
        assert ex["split"] == "spread:subject-conflict"
        assert ex["c3"]["subject"] == {"record-team": "cardinals", "record": 1, "code": "lou"}
        rec = {"outcome_desc": "Rebels", "side_index": 0, "his_slug": LM, "at": 1.0}
        assert _explain(rows, self.S, self.T, self.OUT, EV_LM, _state({AEC_LM: rec}))["split"] == "spread:subject-conflict"
        for rec in ({"outcome_desc": "Cardinals", "side_index": 0, "his_slug": LM, "at": 1.0},
                    {"outcome_desc": "Rebels", "side_index": 1, "his_slug": LM, "at": 1.0},
                    {"outcome_desc": "Bears", "side_index": 1, "his_slug": LM, "at": 1.0}, "Cardinals"):
            h = _resolve(rows, self.S, self.T, self.OUT, EV_LM, _state({AEC_LM: rec}))
            assert _short(h) == (f"asc-{LM}-pos-6pt5", "yes", LONG, "premap_spread_code"), rec


# ------------------------------------------------- (f) the moneyline class

MK_WN = dict(AEC_WN_MK, title=EV_WN)
ATC_WN_1H_ND = f"atc-{WN}-winner-1h-nd"


def _con(slug, q):
    return {slug: dict(_atc(slug, q), title=q)}


class TestTheMoneyline:
    def test_the_side_from_the_team_field(self, armed):
        hit, why = map_lane.aec_code_side(WN, "Notre Dame", MK_WN, 1)
        assert why is None and (hit["outcome"], hit["intent"], hit["side_index"]) == ("Fighting Irish", SHORT, 1)
        hit, why = map_lane.aec_code_side(WN, "Wisconsin", MK_WN, 0)
        assert why is None and (hit["outcome"], hit["intent"], hit["side_index"]) == ("Badgers", LONG, 0)
        # the index still decides against a swapped feed
        assert map_lane.aec_code_side(WN, "Notre Dame", MK_WN, 0)[1] == "side_code_conflict"
        assert map_lane.pair_agrees(WN, 1, "Wisconsin", 0) is None and map_lane.pair_agrees(WN, 0, "Notre Dame", 1) is None
        # a mascot-only payload (no team): unmatched, as today
        bare = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", None, None)
        assert map_lane.aec_code_side(WN, "Notre Dame", bare, 1)[1] == "side_code_unmatched"
        # the venue's code at the other side index: its order is not the slug's
        swapped = _aec(AEC_WN, Q_WN, "Fighting Irish", "Badgers", TEAM_ND, TEAM_WISC)
        assert map_lane.aec_code_side(WN, "Notre Dame", swapped, 1)[1] == "side_code_conflict"
        # the school, never the mascot; a strange school is unmatched
        assert map_lane.aec_code_side(WN, "Fighting Irish", MK_WN, 1)[1] == "side_code_unmatched"
        assert map_lane.aec_code_side(WN, "North Dakota", MK_WN, 1)[1] == "side_code_unmatched"
        # both sides naming his school: ambiguous
        twice = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", dict(TEAM_WISC, safeName="Notre Dame"), TEAM_ND)
        assert map_lane.aec_code_side(WN, "Notre Dame", twice, 1)[1] == "side_code_ambiguous"
        # half a binding (M5 review MEDIUM-1): one side with no team is none
        half = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", None, TEAM_ND)
        assert map_lane.aec_code_side(WN, "Notre Dame", half, 1)[1] == "side_code_unmatched"
        half = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", {"safeName": "Wisconsin"}, TEAM_ND)
        assert map_lane.aec_code_side(WN, "Notre Dame", half, 1)[1] == "side_code_unmatched"
        # college football only for now (M5 review LOW-2): the same shape
        # under another league code reads nothing
        soc = dict(_aec("aec-epl-eve-mnu-2026-09-06", "Everton vs Man United", "Toffees", "Red Devils",
                        {"abbreviation": "eve", "safeName": "Everton"}, {"abbreviation": "mnu", "safeName": "Manchester United"}),
                   title="Everton vs. Manchester United")
        assert map_lane.aec_code_side("epl-eve-mnu-2026-09-06", "Manchester United", soc, 1)[1] == "side_code_unmatched"
        # lou-miss, the probe's own dicts: 'Louisville' by the code rule, 'Ole
        # Miss' by the whole word -- the team field is not reached, same answer
        mk = dict(AEC_LM_MK, title=EV_LM)
        assert map_lane.aec_code_side(LM, "Ole Miss", mk, 1)[0]["outcome"] == "Rebels"

    def test_the_shadow_maps_it_as_grammar(self, monkeypatch, armed):
        from tests.test_mirror_maps_the_copy_lane import _fill
        from tests.test_mirror_shadow import CID
        from tests.test_mirror_shadow import _Pool as _ShadowPool

        fills = [_fill("tok-nd", "BUY", 1000.0, 0.85, 1000, market_title=EV_WN, event_title=EV_WN,
                       event_slug=WN, market_slug=WN, outcome="Notre Dame", outcome_index=1),
                 _fill("tok-wisc", "BUY", 10.0, 0.15, 1100, market_title=EV_WN, event_title=EV_WN,
                       event_slug=WN, market_slug=WN, outcome="Wisconsin", outcome_index=0)]
        venue = _Venue({AEC_WN: MK_WN})
        _with_venue(monkeypatch, venue)
        monkeypatch.setattr(ms, "_map_cache", {})
        out: dict = {}
        m = asyncio.run(ms.map_market(_ShadowPool(fills=fills, mapped=False), fills, venue,
                                      whale="rn1", condition_id=CID, out=out))
        assert m == {"us_slug": AEC_WN, "long_asset": "tok-wisc", "other_asset": "tok-nd", "source": "grammar"}
        assert "refusal" not in out and out["grammar"]["his_outcome"] == "Notre Dame", out["grammar"]
        # the mascot-only payload: 'Notre Dame' is unmatched (nd unreadable),
        # and the market maps as it did under C1 -- through Wisconsin's code
        # and the pair rule, the sibling naming neither code -- so the
        # class's record of his outcome is Wisconsin's; the live class then
        # stays grammar_echo_unverified on that payload (test_unverified_as_today)
        monkeypatch.setattr(ms, "_map_cache", {})
        venue = _Venue({AEC_WN: dict(_aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", None, None), title=EV_WN)})
        _with_venue(monkeypatch, venue)
        out = {}
        m = asyncio.run(ms.map_market(_ShadowPool(fills=fills, mapped=False), fills, venue,
                                      whale="rn1", condition_id=CID, out=out))
        assert m == {"us_slug": AEC_WN, "long_asset": "tok-wisc", "other_asset": "tok-nd", "source": "grammar"}
        assert out["grammar"]["his_outcome"] == "Wisconsin" and out["grammar"]["side_index"] == 0
        # a Notre Dame fill alone on that payload: unmatched, and no sibling
        # to resolve it
        monkeypatch.setattr(ms, "_map_cache", {})
        out = {}
        m = asyncio.run(ms.map_market(_ShadowPool(fills=fills[:1], mapped=False), fills[:1], venue,
                                      whale="rn1", condition_id=CID, out=out))
        assert m is None and out["refusal"] == "side_code_unmatched"

    def test_the_winner_row_is_the_codes_contract(self, armed):
        rows = [_row(f"atc-{WN}-winner-{seg}-{c}", f"Will {s} win the {w}?")
                for seg, w in SEGS.items() for c, s in (("nd", "Notre Dame"), ("wisc", "Wisconsin"))]
        rows += [_row(f"atc-{WN}-winner-{seg}-draw", f"Will the {w} end tied?") for seg, w in SEGS.items()]
        run = lambda *a: asyncio.run(ml._contract_candidates(_PremapPool(rows), *a))  # noqa: E731
        assert run(WN, 1, "Fighting Irish", "Badgers") == [ATC_WN_1H_ND]
        assert run(WN, 0, "Badgers", "Fighting Irish") == [f"atc-{WN}-winner-1h-wisc"]
        # only the other code's rows for i=1: nothing; the draw row never
        only_wisc = [r for r in rows if r["identifier"].endswith("-wisc") or r["identifier"].endswith("-draw")]
        assert asyncio.run(ml._contract_candidates(_PremapPool(only_wisc), WN, 1, "Fighting Irish", "Badgers")) == []
        # the venue disagreeing with itself: every row, so the caller refuses
        rows2 = rows + [_row(f"atc-{WN}-winner-ot-nd", "Will North Dakota win the first half?")]
        assert len(asyncio.run(ml._contract_candidates(_PremapPool(rows2), WN, 1, "Fighting Irish", "Badgers"))) > 1
        # a plain per-side contract, where the venue lists one, stands alone
        rows3 = rows + [_row(f"atc-{WN}-nd", "Will the Fighting Irish win?")]
        assert asyncio.run(ml._contract_candidates(_PremapPool(rows3), WN, 1, "Fighting Irish", "Badgers")) == [f"atc-{WN}-nd"]
        # college football only for now: the same rows under another league
        soc = [_row("atc-epl-eve-mnu-2026-09-06-winner-1h-mnu", "Will Manchester United win the first half?")]
        assert asyncio.run(ml._contract_candidates(_PremapPool(soc), "epl-eve-mnu-2026-09-06", 1, "Red Devils", "Toffees")) == []

    def test_the_truth_reads_the_team_field(self, armed):
        con = _con(ATC_WN_1H_ND, "Will Notre Dame win the first half?")[ATC_WN_1H_ND]
        assert map_lane.grammar_truth(con, MK_WN, 1, code="nd", school="Notre Dame")[0] == "ok"
        assert map_lane.grammar_truth(con, MK_WN, 1, code="nd")[0] == "ok"
        # without the code the winner row reads as before: unverified, no trip
        assert map_lane.grammar_truth(con, MK_WN, 1)[0] == "unverified"
        # the contract slug is exactly the aec head's winner row (M5 review
        # LOW-1): another date / event / a suffix read never certifies
        for other in ("atc-cfb-wisc-nd-2026-09-13-winner-1h-nd", "atc-cfb-wisc-ndst-2026-09-06-winner-1h-nd",
                      f"atc-{WN}-winner-1h-ot-nd", f"atc-{WN}-nd", f"atc-{WN}-winner-nd"):
            oc = _con(other, "Will Notre Dame win the first half?")[other]
            assert map_lane.grammar_truth(oc, MK_WN, 1, code="nd", school="Notre Dame")[0] == "unverified", other
        # half a binding (MEDIUM-1): the other side stating no team
        half = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", None, TEAM_ND)
        v, d = map_lane.grammar_truth(con, half, 1, code="nd", school="Notre Dame")
        assert v == "unverified" and "half a binding" in d
        # a plain contract never overrides the side's own code (MEDIUM-2)
        plain = {"slug": f"atc-{WN}-nd", "outcome": "Fighting Irish", "title": "Fighting Irish"}
        wrong = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", TEAM_WISC, dict(TEAM_ND, abbreviation="wisc"))
        assert map_lane.grammar_truth(plain, wrong, 1, code="nd")[0] == "mismatch"
        dup = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", dict(TEAM_WISC, abbreviation="nd"), TEAM_ND)
        assert map_lane.grammar_truth(plain, dup, 1, code="nd")[0] == "mismatch"
        strange = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", TEAM_WISC, dict(TEAM_ND, abbreviation="xx"))
        assert map_lane.grammar_truth(plain, strange, 1, code="nd")[0] == "ok", "neither code: not evidence"
        assert map_lane.grammar_truth(plain, wrong, 1)[0] == "ok", "no code given: the C1 read as before"
        # his outcome disagreeing, the school disagreeing, the code at the
        # other side, the draw row: mismatch / unverified, never ok
        assert map_lane.grammar_truth(con, MK_WN, 1, code="nd", school="North Dakota")[0] == "mismatch"
        nd_bad = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", TEAM_WISC, dict(TEAM_ND, safeName="North Dakota"))
        assert map_lane.grammar_truth(con, nd_bad, 1, code="nd", school="Notre Dame")[0] == "mismatch"
        assert map_lane.grammar_truth(con, MK_WN, 0, code="wisc")[0] == "unverified"       # not the wisc row
        draw = _con(f"atc-{WN}-winner-1h-draw", "Will the first half end tied?")[f"atc-{WN}-winner-1h-draw"]
        assert map_lane.grammar_truth(draw, MK_WN, 1, code="nd")[0] == "unverified"
        bare = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", None, None)
        v, d = map_lane.grammar_truth(con, bare, 1, code="nd", school="Notre Dame")
        assert v == "unverified" and "states no team" in d
        # the mascot is never the school: name/alias only is a mismatch
        mascot_only = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", TEAM_WISC,
                           {"abbreviation": "nd", "name": "Notre Dame", "alias": "Notre Dame"})
        assert map_lane.grammar_truth(con, mascot_only, 1, code="nd")[0] == "mismatch"
        # the C1 pins' plain contract reads exactly as before
        plain = {"slug": f"atc-{WN}-nd", "outcome": "Fighting Irish", "title": "Fighting Irish"}
        assert map_lane.grammar_truth(plain, MK_WN, 1, code="nd")[0] == "ok"
        assert map_lane.grammar_truth(plain, MK_WN, 0, code="wisc")[0] == "mismatch"

    def _admission(self, monkeypatch, market, contract, i=1, desc="Fighting Irish", his_outcome="Notre Dame",
                   rows=None):
        _cert_env(monkeypatch)
        monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")
        table = {AEC_WN: market, **contract}
        g = {"his_slug": WN, "side_index": i, "outcome_desc": desc, "intent": SHORT if i == 1 else LONG,
             "slug": AEC_WN, "asset": "tok-nd", "his_outcome": his_outcome}
        pool = _live_pool(fills=[], mapped=False)
        t = _live_tick(monkeypatch, pool, _Venue(table))
        rows = rows if rows is not None else [
            _row(ATC_WN_1H_ND, "Will Notre Dame win the first half?"),
            _row(f"atc-{WN}-winner-1h-wisc", "Will Wisconsin win the first half?"),
            _row(f"atc-{WN}-winner-1h-draw", "Will the first half end tied?"),
            _row(f"atc-{WN}-winner-1q-nd", "Will Notre Dame win the first quarter?")]

        async def _cands(p, his_slug, j, d, other_desc="", _rows=rows):
            return await _REAL_CONTRACT_CANDIDATES(_PremapPool(_rows), his_slug, j, d, other_desc)

        monkeypatch.setattr(ml, "_contract_candidates", _cands)
        why = asyncio.run(ml._grammar_admission(t, "rn1", AEC_WN, g))
        ml._current_stats = None
        return why, pool.state["mirror_grammar_echo"], t.pmus.slug_calls

    def test_the_class_certifies_on_the_winner_row(self, monkeypatch):
        why, st, calls = self._admission(monkeypatch, MK_WN, _con(ATC_WN_1H_ND, "Will Notre Dame win the first half?"))
        assert why is None and st["ok"] == 1 and st["tripped"] is False
        assert ATC_WN_1H_ND in calls
        rec = st["certified"][AEC_WN]
        assert (rec["outcome_desc"], rec["side_index"], rec["his_slug"]) == ("Fighting Irish", 1, WN)
        assert st["pending"][AEC_WN]["outcome_desc"] == "Fighting Irish"
        assert "names 'notre dame'" in st["last"]["detail"]

    def test_any_disagreement_trips(self, monkeypatch):
        con = _con(ATC_WN_1H_ND, "Will Notre Dame win the first half?")
        # the side's school is not the winner row's
        bad = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", TEAM_WISC, dict(TEAM_ND, safeName="North Dakota"))
        why, st, _ = self._admission(monkeypatch, bad, con)
        assert why == "side_echo_mismatch" and st["tripped"] is True and not st.get("certified")
        # his outcome is not the venue's school
        why, st, _ = self._admission(monkeypatch, MK_WN, con, his_outcome="North Dakota")
        assert why == "side_echo_mismatch" and st["tripped"] is True
        # the winner row names the other school
        why, st, _ = self._admission(monkeypatch, MK_WN, _con(ATC_WN_1H_ND, "Will Wisconsin win the first half?"))
        assert why == "side_echo_mismatch" and st["tripped"] is True

    def test_unverified_as_today(self, monkeypatch):
        con = _con(ATC_WN_1H_ND, "Will Notre Dame win the first half?")
        # the mascot-only payload (no team): unverified, nothing tripped
        bare = dict(_aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", None, None), title=EV_WN)
        why, st, _ = self._admission(monkeypatch, bare, con)
        assert why == "grammar_echo_unverified" and st["unverified"] == 1 and st["tripped"] is False
        assert not st.get("certified")
        # the venue's winner rows naming two schools for nd: more than one fits
        rows = [_row(ATC_WN_1H_ND, "Will Notre Dame win the first half?"),
                _row(f"atc-{WN}-winner-2h-nd", "Will North Dakota win the second half?")]
        why, st, _ = self._admission(monkeypatch, MK_WN, con, rows=rows)
        assert why == "grammar_echo_unverified" and "2 per-side contracts fit" in st["last"]["detail"]
        # only the other code's rows: nothing fits
        rows = [_row(f"atc-{WN}-winner-1h-wisc", "Will Wisconsin win the first half?")]
        why, st, _ = self._admission(monkeypatch, MK_WN, con, rows=rows)
        assert why == "grammar_echo_unverified" and "0 per-side contracts fit" in st["last"]["detail"]


# ------------------------------------------------------- (h) the seams

class TestTheSeams:
    def test_off_the_switch_nothing_here_runs(self, monkeypatch):
        monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)
        with_team = _board(_venue_lm())
        bare = _board(_venue_lm(_aec(AEC_LM, Q_LM, "Cardinals", "Rebels", None, None)))
        for slug, title, outcome in ((f"{LM}-spread-home-6pt5", "Spread: Ole Miss (-6.5)", "Louisville"),
                                     (f"{LM}-spread-home-1pt5", "Spread: Ole Miss (-1.5)", "Ole Miss")):
            assert _resolve(with_team, slug, title, outcome, EV_LM) is None
            a = _explain(with_team, slug, title, outcome, EV_LM)
            b = _explain(bare, slug, title, outcome, EV_LM)
            assert a == b and a["step"] == "no_side_match" and "c3" not in a and a["c3_on"] is False

    def test_off_the_switch_the_moneyline_arm_does_not_run(self, monkeypatch):
        # M5 review HIGH-1: the three C6-ML sites are dark without the
        # identity switch -- the moneyline reads exactly as C1 did
        monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)
        hit, why = map_lane.aec_code_side(WN, "Notre Dame", MK_WN, 1)
        assert hit is None and why == "side_code_unmatched"
        rows = [_row(ATC_WN_1H_ND, "Will Notre Dame win the first half?")]
        assert asyncio.run(ml._contract_candidates(_PremapPool(rows), WN, 1, "Fighting Irish", "Badgers")) == []
        con = _con(ATC_WN_1H_ND, "Will Notre Dame win the first half?")[ATC_WN_1H_ND]
        assert map_lane.grammar_truth(con, MK_WN, 1, code="nd", school="Notre Dame")[0] == "unverified"
        plain = {"slug": f"atc-{WN}-nd", "outcome": "Fighting Irish", "title": "Fighting Irish"}
        wrong = _aec(AEC_WN, Q_WN, "Badgers", "Fighting Irish", TEAM_WISC, dict(TEAM_ND, abbreviation="wisc"))
        assert map_lane.grammar_truth(plain, wrong, 1, code="nd")[0] == "ok"
        # and _team_hits itself is never reached: a market under the switch
        # maps, the same market without it is unmatched (C1's own verdict)
        monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")
        assert map_lane.aec_code_side(WN, "Notre Dame", MK_WN, 1)[1] is None

    def test_the_pick_stays_pure(self):
        for fn in (premap.c3_pick, premap._c3_pick_spread, premap._c4_subject_by_code,
                   premap._c4_subject_certified, premap._c6_team_subject, premap._c6_pair_by_event,
                   map_lane.code_school, map_lane._team_hits, map_lane._team_truth):
            src = inspect.getsource(fn)
            for forbidden in ("await", "pool", "_get_client", "os.getenv", "SequenceMatcher", "ordering"):
                assert forbidden not in src, (fn.__name__, forbidden)
        # the mascot fields are never read as the school
        for fn in (premap._c6_team_subject, map_lane.side_team, map_lane._team_truth, map_lane._team_hits):
            src = inspect.getsource(fn)
            assert '"alias"' not in src and 'get("name")' not in src, fn.__name__
        for fn in (premap.resolve, premap.resolve_explain):
            assert "his_event_title=event_title" in inspect.getsource(fn)
            assert "await team_select_cols(pool)" in inspect.getsource(fn)
        assert "team_abbr, team_safe_name" in premap.TEAM_SELECT_COLS
        # the three C6-ML sites read the switch (HIGH-1) and the league (LOW-2)
        assert 'lg == "cfb" and _identity_on()' in inspect.getsource(map_lane.aec_code_side)
        assert 'lg == "cfb" and map_lane._identity_on()' in inspect.getsource(ml._contract_candidates)
        src = inspect.getsource(map_lane.grammar_truth)
        assert "aec_head(m) if code and _identity_on() else None" in src and 'head[0] == "cfb"' in src

    def test_the_refusal_names(self):
        src = inspect.getsource(premap._c4_subject_by_code) + inspect.getsource(premap._c6_pair_by_event) \
            + inspect.getsource(premap._c6_team_subject) + inspect.getsource(map_lane.code_school) \
            + inspect.getsource(premap._c4_subject_certified)
        for name in ("spread:code-witness-disagree", "spread:code-witness-same", "spread:code-unnamed",
                     "spread:code-both", "spread:event-collision", "spread:pair-unwitnessed",
                     "spread:subject-uncertified", "spread:subject-conflict", "team-absent"):
            assert name in src, name


def test_the_migration_adds_the_columns_nullable():
    import pathlib

    from sportsassets.scripts import migrate

    p = pathlib.Path(migrate.MIGRATIONS_DIR).joinpath("055_us_premap_team.sql")
    assert p.exists()
    files = [f.name for f in sorted(pathlib.Path(migrate.MIGRATIONS_DIR).glob("*.sql"))]
    assert files.index("053_copy_probes_probe_at_idx.sql") < files.index("055_us_premap_team.sql")
    assert sum(1 for f in files if f.startswith("055_")) == 1
    sql = p.read_text()
    assert sql.splitlines()[0].startswith("-- 055: THE VENUE'S OWN TEAM FIELD ON us_premap")
    stmts = [ln for ln in sql.splitlines() if ln.startswith("ALTER TABLE")]
    assert len(stmts) == 7 and all(ln.startswith("ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS ") for ln in stmts)
    cols = {ln.split()[8]: ln.split()[9].rstrip(";") for ln in stmts}
    assert cols == {"team_abbr": "text", "team_name": "text", "team_safe_name": "text", "team_id": "bigint",
                    "team_league": "text", "game_start": "timestamptz", "sports_type": "text"}
    assert "NOT NULL" not in sql and "DEFAULT" not in sql
