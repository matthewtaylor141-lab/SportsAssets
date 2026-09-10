"""C9 (2026-09-10, coverage lane C9): the NFL board binds -- the football
date key, the football set, the moneyline and the totals by the venue's
own words; the spreads refused by name.

Owner (~00:37Z): "I also see that he has a ton of NFL trades and we have
zero of them mirrored". His rows (hard2/nflrows_0039.txt table 1, rows
447-458): twelve markets on ONE game, nfl-ne-sea-2026-09-10 (title
'Patriots vs. Seahawks', outcomes Patriots/Seahawks: the moneyline 89
fills $22,706; the totals total-41pt5 / 44pt5 / 43pt5 / 47pt5 / 50pt5 /
62pt5, titles 'Patriots vs. Seahawks: O/U <L>', outcomes Over/Under; the
spreads spread-home-3pt5 / 2pt5 / 6pt5 / 1pt5 / 4pt5, titles 'Spread:
Seahawks (-<L>)', outcomes Patriots/Seahawks), every one 'unmapped: no
US market for his tokens', the moneyline's explain
no_key_intersection:exact:404:3, the rest no_key_intersection.

The venue (hard2/nflrows_0041.txt table 4, rows 497-586, VERBATIM below
as `TABLE_4`): the SAME game under league code 'nfl' with the SAME two
team codes in the SAME order, dated 2026-09-09 -- aec-nfl-ne-sea-2026-
09-09 sides patriots / seahawks, asc-nfl-ne-sea-2026-09-09-neg-<L> /
-pos-<L> sides yes / no ('Will the New England Patriots cover -3.5 vs
the Seattle Seahawks in New England Patriots vs. Seattle Seahawks?'),
tsc-nfl-ne-sea-2026-09-09-total-<L> sides over / under ('Will the total
in New England Patriots vs. Seattle Seahawks be more than 24.5?'). The
file prints `left(question, 100)`: the aec and asc questions are cut at
100 characters (the strings end 'scheduled for S' / 'in New England
Patriots vs. Seattle'); the fixtures COMPLETE them by the venue's own
attested template (docs/mirror-coverage.md section 10.1's asc wording
'Will the Cougars cover -24.5 vs the Huskies in Cougars vs. Huskies?';
section 23's aec wording 'Who will win in the upcoming football event
Cardinals vs Rebels scheduled for September 6, 2026 at 11:30 PM UTC?')
and the kickoff the lane names (2026-09-11 ~00:20Z) -- the stored line
'20' on both aec rows is the clock's minutes as the sweep stamps them,
and the completed question reproduces exactly that stamp
(test_c9_the_verbatim_rows_are_the_sweeps_own_rows). Every completed
string is marked COMPLETED where it is built. No NFL `team` dict is on
file (the PREMAP-TEAM probe ran on cfb alone), so the C6-ML fixtures
below are built in the probe's shape and say so.

THE RULES, EXACTLY (docs/mirror-coverage.md section 72):
- premap.nfl_date_keys, added to HIS key set by resolve and
  resolve_explain: his kindless nfl slug also carries the kindless key
  and the R1 pair key on the two calendar-adjacent dates; no other
  league, no kind-prefixed slug, no unreadable date; OFF under
  PREMAP_NFL_DATE_TOL=off. event_keys_for is 6509878 byte for byte: the
  sweep calls it on the venue's KINDLESS event slug (review CRITICAL-1),
  so a key built there would shift the venue's rows too.
- premap.nfl_date_read (resolve and resolve_explain): his pair under ONE
  adjacent date -> every arm reads his slug as the venue dates it,
  `date_shift` {his, venue}; under MORE THAN ONE date -> the step
  `date_ambiguous`, `date_candidates`, nothing bound.
- map_lane.FOOTBALL_LEAGUES = {cfb, nfl}: the two C6-ML sites read it;
  mirror_live's winner-row admission stays cfb (E28 / E29 own that file).
- the league_alias_probe asks '^(atc|aec)-'.
- the moneyline binds through the wording arm (side_norm == his outcome,
  the aec row's clock line authenticated); the totals through the
  wording arm's over/under branch on the slug's line; the SPREADS refuse
  by name -- `no_side_match:spread:names-unreadable`: the venue's
  question names 'Seattle Seahawks', his feed 'Seahawks', and token-set
  equality (pmus._yn_name_match) is the bar; the C4 chain reads the
  venue's names as codes and defers to C3's own refusal. The
  attribution the next lane must certify is pinned from the venue's
  words (THE SIDE TABLE below), never bound here.
- C10 (2026-09-10, docs section 74) then bound the spreads from the aec
  row's own team field; on THIS file's team-less board the spread
  refusal under the switch is `spread:team-absent` (re-pinned below),
  the binding itself in tests/test_c10_nfl_spreads.py.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import subprocess
import sys

import pytest

from sportsassets import map_lane
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import premap
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, N, NOW, _Pool as _LivePool, _Venue, _armed, _census, _fill, _ratio_fills, _tick,
)

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
HIS_DATE, VENUE_DATE = "2026-09-10", "2026-09-09"
HIS_ML = f"nfl-ne-sea-{HIS_DATE}"
HIS_TITLE = "Patriots vs. Seahawks"
AEC = f"aec-nfl-ne-sea-{VENUE_DATE}"

# hard2/nflrows_0041.txt table 4, rows 497-586: (identifier, side_norm,
# line, question) verbatim, the questions as the file cut them at 100
# characters
Q_AEC_CUT = "Who will win in the upcoming football event New England Patriots vs Seattle Seahawks scheduled for S"
Q_TSC = "Will the total in New England Patriots vs. Seattle Seahawks be more than {L}?"
SPREAD_LINES = ("0pt5", "10pt5", "13pt5", "14pt5", "16pt5", "17pt5", "19pt5", "1pt5", "20pt5", "21pt5",
                "2pt5", "3pt5", "4pt5", "5pt5", "6pt5", "7pt5", "8pt5", "9pt5")
TOTAL_LINES = ("24pt5", "26pt5", "28pt5", "30pt5", "32pt5", "34pt5", "36pt5", "38pt5")


def _L(tok: str) -> str:
    return tok.replace("pt", ".")


def _asc_q_cut(sign: str, L: str) -> str:
    # the file's 100-character cut of the asc question
    q = f"Will the New England Patriots cover {sign}{L} vs the Seattle Seahawks in New England Patriots vs. Seattle Seahawks?"
    return q[:100]


TABLE_4: list[tuple[str, str, str, str]] = [(AEC, "patriots", "20", Q_AEC_CUT), (AEC, "seahawks", "20", Q_AEC_CUT)]
for _sg, _sign in (("neg", "-"), ("pos", "")):
    for _ln in SPREAD_LINES:
        for _side in ("no", "yes"):
            TABLE_4.append((f"asc-nfl-ne-sea-{VENUE_DATE}-{_sg}-{_ln}", _side, _L(_ln), _asc_q_cut(_sign, _L(_ln))))
for _ln in TOTAL_LINES:
    for _side in ("over", "under"):
        TABLE_4.append((f"tsc-nfl-ne-sea-{VENUE_DATE}-total-{_ln}", _side, _L(_ln), Q_TSC.format(L=_L(_ln))))
assert len(TABLE_4) == 90, "the file's (90 rows)"

# COMPLETED by the venue's own template (the module docstring): the aec
# question to the kickoff clock, the asc questions to the trailing matchup
Q_AEC = ("Who will win in the upcoming football event New England Patriots vs Seattle Seahawks "
         "scheduled for September 11, 2026 at 12:20 AM UTC?")


def _asc_q(sign: str, L: str) -> str:
    return (f"Will the New England Patriots cover {sign}{L} vs the Seattle Seahawks "
            f"in New England Patriots vs. Seattle Seahawks?")


def _side(ident, desc, long, team=None):
    return {"identifier": ident, "description": desc, "long": long, "team": team,
            "teamId": team.get("id") if isinstance(team, dict) else None}


def _aec(ident, q, ta=None, tb=None):
    return {"slug": ident, "question": q, "closed": False,
            "marketSides": [_side(ident, "Patriots", True, ta), _side(ident, "Seahawks", False, tb)]}


def _asc(ident, q, L):
    return {"slug": ident, "question": q, "closed": False,
            "marketSides": [_side(ident, L, True), _side(ident, L, False)]}


def _tsc(ident, q):
    # the 1h rows' shape (nflrows_0041 row 596, question 'Will the total in
    # New England Patriots vs. Seattle Seahawks be more than 10.5?'):
    # sides Over / Under, the line in the question
    return {"slug": ident, "question": q, "closed": False,
            "marketSides": [_side(ident, "Over", True), _side(ident, "Under", False)]}


def _venue(date=VENUE_DATE, aec_q=Q_AEC, totals=TOTAL_LINES + ("41pt5",), aec=True, spreads=SPREAD_LINES):
    """The venue's markets on the game, dated `date`: the aec row, the
    asc neg / pos rows, the tsc totals (41.5 built in the 1h rows' shape
    beside the eight the listing reached under its LIMIT)."""
    head = f"nfl-ne-sea-{date}"
    out = []
    if aec:
        out.append(_aec(f"aec-{head}", aec_q))
    for ln in spreads:
        L = _L(ln)
        out.append(_asc(f"asc-{head}-neg-{ln}", _asc_q("-", L), L))
        out.append(_asc(f"asc-{head}-pos-{ln}", _asc_q("", L), L))
    for ln in totals:
        out.append(_tsc(f"tsc-{head}-total-{ln}", Q_TSC.format(L=_L(ln))))
    return out


def _board(markets, event_slug=None) -> list[dict]:
    """The rows as the sweep writes them: every market of the event keyed
    by the event's own keys (event_keys_for(title, event_slug) --
    ne-sea's venue event_title is not on file, so None; the event slug
    is the venue's KINDLESS one, `nfl-ne-sea-<date>`, the shape `refresh`
    hands event_keys_for -- a real row's stored keys,
    hard2/premap_rows_1909.log:58, are {els-kbk-tro-2026-09-06, ...} with
    no kind-prefixed sibling (review MEDIUM-2: a kind-prefixed event slug
    here hid CRITICAL-1))."""
    rows: list[dict] = []
    for m in markets:
        d = premap.date_of(m["slug"])
        ev = event_slug or m["slug"][m["slug"].index("-") + 1:m["slug"].index(d) + len(d)]
        keys = premap.event_keys_for(None, ev)
        assert keys, ev
        for r in premap._market_rows({"slug": ev, "title": None}, m):
            r["event_keys"] = keys
            rows.append(r)
    return rows


class _Pool:
    """us_premap by event keys; the league_alias_probe's regex read
    answered by the identifiers; ingestion_state absent; the C6 columns
    absent."""

    def __init__(self, rows):
        self.rows, self.probe_patterns = rows, []

    async def fetch(self, sql, *a):
        if "identifier ~ $1::text" in sql:
            import re as _re

            self.probe_patterns.append(a[0])
            return [{"identifier": r["identifier"]} for r in self.rows if _re.search(a[0], r["identifier"])]
        if "us_premap" in sql and "event_keys &&" in sql:
            k = set(a[0])
            return [dict(r) for r in self.rows if set(r["event_keys"]) & k]
        return []

    async def fetchval(self, sql, *a):
        if "information_schema" in sql:
            return 0
        return None


def _resolve(rows, slug, title, outcome, event_title=None):
    return asyncio.run(premap.resolve(_Pool(rows), title, event_title, outcome, slug))


def _explain(rows, slug, title, outcome, event_title=None):
    return asyncio.run(premap.resolve_explain(_Pool(rows), title, event_title, outcome, slug))


def _short(h):
    return None if not h else (h["market_slug"], h["outcome"], h["intent"], h["matched_by"])


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


@pytest.fixture(autouse=True)
def _tol_default(monkeypatch):
    # the switch as the code default reads it: absent
    monkeypatch.delenv(premap.PREMAP_NFL_DATE_TOL_ENV, raising=False)
    monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)


# --------------------------------------------- (A) the football date key

class TestTheKeyBuilder:
    TODAY = {
        # 6509878's key sets, byte for byte (computed on that tree)
        "nfl-ne-sea-2026-09-10": ["ne-sea-2026-09-10", "nfl-ne-sea-2026-09-10"],
        "cfb-washst-wash-2026-09-06": ["cfb-washst-wash-2026-09-06", "washst-wash-2026-09-06"],
        "epl-eve-mnu-2026-09-06": ["epl-eve-mnu-2026-09-06", "eve-mnu-2026-09-06"],
        "wta-quevedo-tormo-2026-09-09": ["quevedo-tormo-2026-09-09", "wta-quevedo-tormo-2026-09-09"],
        "cs2-1win-b8-2026-09-10": ["1win-b8-2026-09-10", "cs2-1win-b8-2026-09-10"],
        "atp-gea-zandsch-2026-09-07": ["atp-gea-zandsch-2026-09-07", "gea-zandsch-2026-09-07"],
        "mlb-nyy-bos-2026-08-23": ["mlb-nyy-bos-2026-08-23", "nyy-bos-2026-08-23"],
        "aec-nfl-ne-sea-2026-09-09": ["aec-nfl-ne-sea-2026-09-09", "ne-sea-2026-09-09", "nfl-ne-sea-2026-09-09"],
    }
    EXTRA = ["ne-sea-2026-09-09", "ne-sea-2026-09-11", "nfl-ne-sea-2026-09-09", "nfl-ne-sea-2026-09-11"]

    def test_his_nfl_slug_emits_todays_keys_plus_the_two_adjacent_dates(self):
        assert premap.nfl_date_keys(HIS_ML) == self.EXTRA
        # event_keys_for itself is 6509878 byte for byte: the sweep calls it
        # on the venue's KINDLESS event slug too (review CRITICAL-1), so the
        # four keys are added by the RESOLVERS, his side of the lookup alone
        assert premap.event_keys_for(None, HIS_ML) == self.TODAY[HIS_ML]
        # the family slugs key the same game
        for tail in ("-spread-home-3pt5", "-total-41pt5"):
            assert premap.event_keys_for(None, HIS_ML + tail) == self.TODAY[HIS_ML]
            assert premap.nfl_date_keys(HIS_ML + tail) == self.EXTRA
        # with his title: today's title keys stamped with HIS date, untouched
        assert premap.event_keys_for(HIS_TITLE, HIS_ML) == sorted(
            self.TODAY[HIS_ML] + ["patriots vs seahawks", "patriots vs seahawks@2026-09-10",
                                  "seahawks vs patriots", "seahawks vs patriots@2026-09-10"])
        # the resolvers' key set: today's two plus his four, admitted
        assert asyncio.run(premap.resolve_explain(_Pool([]), None, None, "Patriots", HIS_ML))["keys"] == 6
        assert "nfl_date_keys" not in inspect.getsource(premap.event_keys_for)

    def test_the_calendar_rolls_and_an_unreadable_date_adds_nothing(self):
        assert premap.nfl_date_keys("nfl-ne-sea-2026-09-01") == [
            "ne-sea-2026-08-31", "ne-sea-2026-09-02", "nfl-ne-sea-2026-08-31", "nfl-ne-sea-2026-09-02"]
        assert premap.nfl_date_keys("nfl-ne-sea-2026-12-31") == [
            "ne-sea-2026-12-30", "ne-sea-2027-01-01", "nfl-ne-sea-2026-12-30", "nfl-ne-sea-2027-01-01"]
        assert premap.nfl_date_keys("nfl-ne-sea-2026-03-01") == [
            "ne-sea-2026-02-28", "ne-sea-2026-03-02", "nfl-ne-sea-2026-02-28", "nfl-ne-sea-2026-03-02"]
        assert premap.nfl_date_keys("nfl-ne-sea-2026-13-40") == []
        assert premap.event_keys_for(None, "nfl-ne-sea-2026-13-40") == ["ne-sea-2026-13-40", "nfl-ne-sea-2026-13-40"]
        assert premap._adjacent_dates("2026-02-29") is None and premap._adjacent_dates("") is None
        assert premap._adjacent_dates("2028-02-29") == ("2028-02-28", "2028-03-01")

    def test_every_other_league_and_the_venues_side_emit_exactly_todays_keys(self):
        for slug, keys in self.TODAY.items():
            if slug == HIS_ML:
                continue
            assert premap.event_keys_for(None, slug) == keys, slug
            assert premap.nfl_date_keys(slug) == [], slug
        # the venue's own kind-prefixed nfl slug: no extra key (his side alone)
        assert premap.nfl_date_keys("aec-nfl-ne-sea-2026-09-09") == []
        assert premap.nfl_date_keys("asc-nfl-ne-sea-2026-09-09-neg-3pt5") == []
        # a shared code, a two-token head, a dateless slug: nothing
        for bad in ("nfl-ne-ne-2026-09-10", "nfl-ne-2026-09-10", "nfl-ne-sea", "", None, "NFL-NE-SEA-2026-09-10-x-"):
            assert premap.nfl_date_keys(bad) == ([] if bad != "NFL-NE-SEA-2026-09-10-x-" else premap.nfl_date_keys(HIS_ML)), bad
        assert premap._nfl_slug_parts("nfl-ne-sea-2026-09-10") == {"lg": "nfl", "a": "ne", "b": "sea", "date": "2026-09-10"}
        assert premap._nfl_slug_parts("cfb-ne-sea-2026-09-10") is None and premap._nfl_slug_parts("nfl-ne-sea-2026-09-1") is None

    def test_the_switch_off_is_todays_key_set_on_nfl_too(self, monkeypatch):
        for word in ("off", "0", "false", "no", "junk", " OFF "):
            monkeypatch.setenv(premap.PREMAP_NFL_DATE_TOL_ENV, word)
            assert not premap.nfl_date_tol_on()
            assert premap.nfl_date_keys(HIS_ML) == []
            assert premap.event_keys_for(None, HIS_ML) == self.TODAY[HIS_ML], word
            assert premap._dated_admissible(set(self.TODAY[HIS_ML] + self.EXTRA), HIS_DATE, slug=HIS_ML) == set(self.TODAY[HIS_ML])
        for word in ("", "on", "1", "true", "yes", " ON "):
            monkeypatch.setenv(premap.PREMAP_NFL_DATE_TOL_ENV, word)
            assert premap.nfl_date_tol_on(), word
        monkeypatch.delenv(premap.PREMAP_NFL_DATE_TOL_ENV)
        assert premap.nfl_date_tol_on()

    def test_dated_admissible_admits_the_extra_keys_with_the_slug_alone(self):
        keys = set(self.TODAY[HIS_ML] + self.EXTRA + ["patriots vs seahawks", "patriots vs seahawks@2026-09-10"])
        # without the slug: byte for byte the old rule (the adjacent keys dropped)
        assert premap._dated_admissible(keys, HIS_DATE) == set(self.TODAY[HIS_ML] + ["patriots vs seahawks@2026-09-10"])
        assert premap._dated_admissible(keys, HIS_DATE, slug=HIS_ML) == set(
            self.TODAY[HIS_ML] + self.EXTRA + ["patriots vs seahawks@2026-09-10"])
        # a cfb slug admits nothing extra even when such keys are present
        assert premap._dated_admissible(keys, HIS_DATE, slug="cfb-ne-sea-2026-09-10") == set(
            self.TODAY[HIS_ML] + ["patriots vs seahawks@2026-09-10"])
        assert "def _dated_admissible(keys: set[str], d: str, slug: str | None = None)" in inspect.getsource(premap._dated_admissible)

    def test_the_switch_reader_in_a_fresh_interpreter(self):
        code = ("from sportsassets.workers import premap as p; "
                "print(p.nfl_date_tol_on(), len(p.nfl_date_keys('nfl-ne-sea-2026-09-10')))")
        for word, want in ((None, "True 4"), ("on", "True 4"), ("1", "True 4"), ("yes", "True 4"),
                           ("off", "False 0"), ("0", "False 0"), ("junk", "False 0"), ("false", "False 0")):
            env = {k: v for k, v in os.environ.items() if k != premap.PREMAP_NFL_DATE_TOL_ENV}
            if word is not None:
                env[premap.PREMAP_NFL_DATE_TOL_ENV] = word
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                                 cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            assert out.returncode == 0, out.stderr[-400:]
            assert out.stdout.strip() == want, (word, out.stdout)


# ------------------------------------------------ (B) the venue's rows

class TestTheVenuesRows:
    def test_c9_the_verbatim_rows_are_the_sweeps_own_rows(self):
        rows = _board(_venue(totals=TOTAL_LINES))
        built = sorted((r["identifier"], r["side_norm"], r["line"], r["question"][:100]) for r in rows)
        assert built == sorted(TABLE_4), "identifier, side_norm, line and the question's first 100 characters"
        # the aec rows' line '20' is the kickoff clock's minutes, as the sweep stamps them
        aec = [r for r in rows if r["identifier"] == AEC]
        assert [(r["side_norm"], r["line"], r["intent"]) for r in aec] == [("patriots", "20", LONG), ("seahawks", "20", SHORT)]
        assert all(premap._clock_artifact(r["line"], r) for r in aec)
        # the asc rows: yes = the venue's long marker, the neg rows signed
        neg = [r for r in rows if r["identifier"] == f"asc-nfl-ne-sea-{VENUE_DATE}-neg-3pt5"]
        assert [(r["side_norm"], r["intent"], r["signed"], r["line"]) for r in neg] == [("yes", LONG, "-3.5", "3.5"), ("no", SHORT, "-3.5", "3.5")]
        pos = [r for r in rows if r["identifier"] == f"asc-nfl-ne-sea-{VENUE_DATE}-pos-3pt5"]
        assert [(r["side_norm"], r["intent"], r["signed"]) for r in pos] == [("yes", LONG, ""), ("no", SHORT, "")]
        # every row of the event carries the venue's keys on ITS date and no other
        for r in rows:
            assert "nfl-ne-sea-2026-09-09" in r["event_keys"] and "ne-sea-2026-09-09" in r["event_keys"]
            assert not any(k.endswith(HIS_DATE) for k in r["event_keys"])

    def test_c9_the_venues_question_names_the_codes_as_the_code_rules_read_them(self):
        # the venue's own words: 'Seattle Seahawks' names sea by its first
        # word; the two-letter 'ne' is read by NO code rule (the `nd` shape
        # of docs section 23), on 'New England Patriots' or anything else
        assert map_lane.code_reads("sea", "Seattle Seahawks", "ne")
        assert not map_lane.code_reads("ne", "Seattle Seahawks", "sea")
        assert not map_lane.code_reads("ne", "New England Patriots", "sea")
        assert not map_lane.code_reads("sea", "New England Patriots", "ne")
        # and his feed's mascot beside them: 'Seahawks' names sea by its first
        # word, 'Patriots' names nothing -- the bar between his words and the
        # venue's full names is token-set equality, never a prefix
        assert map_lane.code_reads("sea", "Seahawks", "ne") and not map_lane.code_reads("ne", "Patriots", "sea")
        from sportsassets import pmus
        assert not pmus._yn_name_match("seattle seahawks", "seahawks")
        assert not pmus._yn_name_match("new england patriots", "patriots")


# ------------------------------------ (C) the moneyline and the date read

class TestTheMoneyline:
    def test_c9_his_moneyline_binds_to_the_aec_row_through_the_venues_date(self):
        rows = _board(_venue())
        for outcome, side, intent in (("Patriots", "patriots", LONG), ("Seahawks", "seahawks", SHORT)):
            ex = _explain(rows, HIS_ML, HIS_TITLE, outcome)
            assert ex["step"] == "resolves", ex
            assert ex["date_shift"] == {"his": HIS_DATE, "venue": VENUE_DATE}
            assert "date_candidates" not in ex
            hit = _resolve(rows, HIS_ML, HIS_TITLE, outcome)
            assert _short(hit) == (AEC, side, intent, "premap"), outcome
            assert hit["date_shift"] == {"his": HIS_DATE, "venue": VENUE_DATE}
        # the identity switch changes nothing on the moneyline: the wording
        # arm is the binding on both settings
        os.environ[premap.PREMAP_YN_IDENTITY_ENV] = "on"
        try:
            assert _short(_resolve(rows, HIS_ML, HIS_TITLE, "Patriots")) == (AEC, "patriots", LONG, "premap")
        finally:
            del os.environ[premap.PREMAP_YN_IDENTITY_ENV]

    def test_c9_a_matching_date_binds_as_today_with_no_shift(self):
        rows = _board(_venue(date=HIS_DATE))
        ex = _explain(rows, HIS_ML, HIS_TITLE, "Patriots")
        assert ex["step"] == "resolves" and "date_shift" not in ex and "date_candidates" not in ex
        hit = _resolve(rows, HIS_ML, HIS_TITLE, "Patriots")
        assert _short(hit) == (f"aec-nfl-ne-sea-{HIS_DATE}", "patriots", LONG, "premap") and "date_shift" not in hit

    def test_c9_the_day_after_binds_the_same_way(self):
        rows = _board(_venue(date="2026-09-11"))
        hit = _resolve(rows, HIS_ML, HIS_TITLE, "Seahawks")
        assert _short(hit) == ("aec-nfl-ne-sea-2026-09-11", "seahawks", SHORT, "premap")
        assert hit["date_shift"] == {"his": HIS_DATE, "venue": "2026-09-11"}

    def test_c9_two_dates_refuse_date_ambiguous_and_bind_nothing(self):
        # the pair on both adjacent dates
        rows = _board(_venue(date=VENUE_DATE)) + _board(_venue(date="2026-09-11"))
        ex = _explain(rows, HIS_ML, HIS_TITLE, "Patriots")
        assert ex["step"] == "date_ambiguous" and ex["date_candidates"] == [VENUE_DATE, "2026-09-11"]
        assert "date_shift" not in ex and ex["split"] == f"{VENUE_DATE},2026-09-11" and ex["rows"] > 0
        assert _resolve(rows, HIS_ML, HIS_TITLE, "Patriots") is None
        # his date AND an adjacent one
        rows = _board(_venue(date=HIS_DATE)) + _board(_venue(date=VENUE_DATE))
        ex = _explain(rows, HIS_ML, HIS_TITLE, "Patriots")
        assert ex["step"] == "date_ambiguous" and ex["date_candidates"] == [VENUE_DATE, HIS_DATE]
        assert _resolve(rows, HIS_ML, HIS_TITLE, "Patriots") is None
        for tail in ("-total-41pt5", "-spread-home-3pt5"):
            assert _resolve(rows, HIS_ML + tail, HIS_TITLE, "Over") is None
            assert _explain(rows, HIS_ML + tail, HIS_TITLE, "Over")["step"] == "date_ambiguous"
        # the shadow's reason text through explain_unmapped is the step name
        # with the venue's dates as its split (the venue's own word: the
        # exact lane's 404 trail never rides it)
        assert ex["split"] == f"{VENUE_DATE},{HIS_DATE}"
        assert asyncio.run(ms.explain_unmapped(_Pool(rows), {"title": HIS_TITLE, "event_title": None,
                                                              "outcome": "Patriots", "his_slug": HIS_ML},
                                                None, exact_404=3)) == f"date_ambiguous:{VENUE_DATE},{HIS_DATE}"
        assert asyncio.run(ms.explain_unmapped(_Pool(rows), {"title": HIS_TITLE, "event_title": None,
                                                              "outcome": "Patriots", "his_slug": HIS_ML})) == f"date_ambiguous:{VENUE_DATE},{HIS_DATE}"

    def test_c9_the_switch_off_is_todays_refusal_byte_for_byte(self, monkeypatch):
        rows = _board(_venue())
        monkeypatch.setenv(premap.PREMAP_NFL_DATE_TOL_ENV, "off")
        assert _resolve(rows, HIS_ML, HIS_TITLE, "Patriots") is None
        ex = _explain(rows, HIS_ML, HIS_TITLE, "Patriots")
        assert ex["step"] == "no_key_intersection" and ex["rows"] == 0
        assert "date_shift" not in ex and "date_candidates" not in ex
        assert asyncio.run(premap.resolve_explain(_Pool(rows), None, None, "Patriots", HIS_ML))["keys"] == 2
        for tail in ("-total-41pt5", "-spread-home-3pt5"):
            assert _resolve(rows, HIS_ML + tail, HIS_TITLE, "Over") is None
            assert _explain(rows, HIS_ML + tail, HIS_TITLE, "Over")["step"] == "no_key_intersection"

    def test_c9_the_pair_in_the_other_order_is_not_fetched(self):
        # a venue row carrying his codes reversed (sea-ne) is another key
        rows = _board([_aec(f"aec-nfl-sea-ne-{VENUE_DATE}", Q_AEC)])
        assert _resolve(rows, HIS_ML, HIS_TITLE, "Patriots") is None
        ex = _explain(rows, HIS_ML, HIS_TITLE, "Patriots")
        assert ex["step"] == "no_key_intersection" and "date_shift" not in ex

    def test_c9_a_shift_with_an_arm_refusing_is_that_refusal_by_name(self):
        rows = _board(_venue())
        # his outcome naming neither side: the wording arm's own refusal,
        # the shift on the trace, nothing bound
        ex = _explain(rows, HIS_ML, HIS_TITLE, "Cowboys")
        assert ex["step"] == "no_side_match" and ex["date_shift"] == {"his": HIS_DATE, "venue": VENUE_DATE}
        assert _resolve(rows, HIS_ML, HIS_TITLE, "Cowboys") is None
        # the stored line '20' beside a question that does NOT carry the
        # clock (the file's cut text, the line written in as the table
        # holds it): the line is then unexplained by its own question and
        # the wording arm refuses -- fail closed, never a bind. (The sweep
        # itself stamps no line on the cut text; the row is set by hand
        # to the table's state.)
        cut = _board(_venue(aec_q=Q_AEC_CUT))
        assert [r["line"] for r in cut if r["identifier"] == AEC] == ["", ""], "the cut text carries no clock"
        for r in cut:
            if r["identifier"] == AEC:
                r["line"] = "20"
        assert not any(premap._clock_artifact(r["line"], r) for r in cut if r["identifier"] == AEC)
        assert _resolve(cut, HIS_ML, HIS_TITLE, "Patriots") is None
        assert _explain(cut, HIS_ML, HIS_TITLE, "Patriots")["step"] == "no_side_match"
        # a second listing of his pair under another league code on the
        # venue's date is fetched by the pair key exactly as R1 fetches it
        # on his own date, and two candidates refuse by the arm's uniqueness
        two = rows + _board([_aec(f"aec-xfl-ne-sea-{VENUE_DATE}", Q_AEC)])
        assert _resolve(two, HIS_ML, HIS_TITLE, "Patriots") is None
        assert _explain(two, HIS_ML, HIS_TITLE, "Patriots")["step"] == "no_side_match"

    def test_c9_nfl_date_read_is_pure_and_names_its_verdicts(self):
        p = premap._nfl_slug_parts
        assert p(HIS_ML) is not None
        r09 = {"identifier": AEC}
        r11 = {"identifier": "aec-nfl-ne-sea-2026-09-11"}
        r10 = {"identifier": f"aec-nfl-ne-sea-{HIS_DATE}"}
        junk = {"identifier": "atc-nfl-buf-ne-series-2026-ne"}
        other_pair = {"identifier": "aec-nfl-ari-lac-2026-09-13"}
        assert premap.nfl_date_read([r09, junk, other_pair], HIS_ML) == (f"nfl-ne-sea-{VENUE_DATE}", {"date_shift": {"his": HIS_DATE, "venue": VENUE_DATE}})
        assert premap.nfl_date_read([r09, r11], HIS_ML) == (None, {"date_candidates": [VENUE_DATE, "2026-09-11"]})
        assert premap.nfl_date_read([r10, r09], HIS_ML) == (None, {"date_candidates": [VENUE_DATE, HIS_DATE]})
        assert premap.nfl_date_read([r10], HIS_ML) == (HIS_ML, {})
        assert premap.nfl_date_read([junk, other_pair], HIS_ML) == (HIS_ML, {})
        assert premap.nfl_date_read([], HIS_ML) == (HIS_ML, {})
        # a far date is no shift (the keys never fetch one; the arms refuse it as today)
        assert premap.nfl_date_read([{"identifier": "aec-nfl-ne-sea-2026-09-20"}], HIS_ML) == (HIS_ML, {})
        # every other league and the venue's own slug: untouched
        assert premap.nfl_date_read([r09], "cfb-ne-sea-2026-09-10") == ("cfb-ne-sea-2026-09-10", {})
        assert premap.nfl_date_read([r09], AEC) == (AEC, {})
        assert premap.nfl_date_read([r09], None) == (None, {}) or premap.nfl_date_read([r09], None)[1] == {}
        src = inspect.getsource(premap.nfl_date_read)
        for forbidden in ("await", "pool", "os.getenv", "SequenceMatcher"):
            assert forbidden not in src, forbidden
        assert 'return None, {"date_candidates": sorted(dates)}' in src


# ------------------------------------------------------ (D) the totals

class TestTheTotals:
    def test_c9_his_total_binds_with_over_on_the_venues_over_side(self):
        rows = _board(_venue())
        slug = HIS_ML + "-total-41pt5"
        title = "Patriots vs. Seahawks: O/U 41.5"
        want = f"tsc-nfl-ne-sea-{VENUE_DATE}-total-41pt5"
        for outcome, side, intent in (("Over", "over", LONG), ("Under", "under", SHORT)):
            ex = _explain(rows, slug, title, outcome)
            assert ex["step"] == "resolves" and ex["date_shift"] == {"his": HIS_DATE, "venue": VENUE_DATE}, ex
            hit = _resolve(rows, slug, title, outcome)
            assert _short(hit) == (want, side, intent, "premap"), outcome
            assert hit["date_shift"] == {"his": HIS_DATE, "venue": VENUE_DATE}
        # under the identity switch the same row, the same side (the wording
        # arm answers first; C3's own pick is never reached)
        os.environ[premap.PREMAP_YN_IDENTITY_ENV] = "on"
        try:
            assert _short(_resolve(rows, slug, title, "Over")) == (want, "over", LONG, "premap")
            ex = _explain(rows, slug, title, "Over")
            assert ex["step"] == "resolves" and "c3" not in ex
        finally:
            del os.environ[premap.PREMAP_YN_IDENTITY_ENV]
        # his other listed lines
        for ln in ("38pt5", "32pt5"):
            assert _short(_resolve(rows, HIS_ML + f"-total-{ln}", f"Patriots vs. Seahawks: O/U {_L(ln)}", "Under")) == (
                f"tsc-nfl-ne-sea-{VENUE_DATE}-total-{ln}", "under", SHORT, "premap")

    def test_c9_a_total_the_venue_does_not_list_refuses_by_name(self, armed):
        rows = _board(_venue())
        for ln in ("62pt5", "44pt5", "43pt5", "47pt5", "50pt5"):
            slug = HIS_ML + f"-total-{ln}"
            title = f"Patriots vs. Seahawks: O/U {_L(ln)}"
            assert _resolve(rows, slug, title, "Over") is None
            ex = _explain(rows, slug, title, "Over")
            assert ex["step"] == "no_side_match" and ex["split"] == "total:line-absent", (ln, ex)
            assert ex["date_shift"] == {"his": HIS_DATE, "venue": VENUE_DATE}
        # a wrong line never matches a listed one
        assert _resolve(rows, HIS_ML + "-total-41pt5", "Patriots vs. Seahawks: O/U 41.5", "Over 38.5") is None


# ---------------------------------------------------- (E) the spreads

# THE SIDE TABLE (the venue's asc question certifies the subject:
# 'Will the New England Patriots cover <sign><L> vs the Seattle Seahawks in
# New England Patriots vs. Seattle Seahawks?' -- New England Patriots at
# code ne = a; his 'Spread: Seahawks (-L)' gives L to sea = b): his
# Seahawks -L is the venue's pos-L NO (the Patriots do not cover +L), his
# Patriots +L the venue's pos-L YES; neg-L is never his row on these
# titles. C3's table (docs section 10.2 step 4): b with -L -> pos-L no.
SIDE_TABLE = {
    ("spread-home-3pt5", "Seahawks"): (f"asc-nfl-ne-sea-{VENUE_DATE}-pos-3pt5", "no", SHORT),
    ("spread-home-3pt5", "Patriots"): (f"asc-nfl-ne-sea-{VENUE_DATE}-pos-3pt5", "yes", LONG),
    ("spread-home-2pt5", "Seahawks"): (f"asc-nfl-ne-sea-{VENUE_DATE}-pos-2pt5", "no", SHORT),
    ("spread-home-2pt5", "Patriots"): (f"asc-nfl-ne-sea-{VENUE_DATE}-pos-2pt5", "yes", LONG),
    ("spread-home-6pt5", "Seahawks"): (f"asc-nfl-ne-sea-{VENUE_DATE}-pos-6pt5", "no", SHORT),
    ("spread-home-6pt5", "Patriots"): (f"asc-nfl-ne-sea-{VENUE_DATE}-pos-6pt5", "yes", LONG),
    ("spread-home-1pt5", "Seahawks"): (f"asc-nfl-ne-sea-{VENUE_DATE}-pos-1pt5", "no", SHORT),
    ("spread-home-1pt5", "Patriots"): (f"asc-nfl-ne-sea-{VENUE_DATE}-pos-1pt5", "yes", LONG),
    ("spread-home-4pt5", "Seahawks"): (f"asc-nfl-ne-sea-{VENUE_DATE}-pos-4pt5", "no", SHORT),
    ("spread-home-4pt5", "Patriots"): (f"asc-nfl-ne-sea-{VENUE_DATE}-pos-4pt5", "yes", LONG),
}


def _attribution(rows, tail, outcome):
    """The venue identifier and side his outcome takes, computed from the
    venue's OWN question on his line through C3's sign table, with the
    subject read by the code rules (the venue's question naming ne at a):
    the proof of the attribution, never a binding."""
    from sportsassets import pmus

    L = _L(tail.rsplit("-", 1)[-1])
    cands = [r for r in rows if r["identifier"].endswith(f"-{tail.rsplit('-', 1)[-1]}")
             and r["identifier"].startswith("asc-")]
    qm = premap._C3_ASC_Q_RE.match(pmus.fold_latin(cands[0]["question"]))
    names = (premap._c3_norm(qm.group("a")), premap._c3_norm(qm.group("b")))
    assert names == ("new england patriots", "seattle seahawks") and premap._canon_line(qm.group("line")) == L
    # the subject slot is a (ne) by the venue's grammar; his 'Seahawks' is
    # sea = b by its first word (the C1 rule), his 'Patriots' the pair's
    # other position (no code rule reads the two-letter ne)
    assert outcome in ("Patriots", "Seahawks")
    mine = 1 if map_lane.code_reads("sea", outcome, "ne") else 0
    assert (mine == 1) == (outcome == "Seahawks")
    gives = True             # 'Spread: Seahawks (-L)': the title's sign is '-'
    if mine != 1:            # the title names the OTHER team (sea = b): the sign is theirs
        gives = not gives
    sign_tok, side = (("neg" if gives else "pos"), "yes") if mine == 0 else (("pos" if gives else "neg"), "no")
    ident = f"asc-nfl-ne-sea-{VENUE_DATE}-{sign_tok}-{tail.rsplit('-', 1)[-1]}"
    row = next(r for r in rows if r["identifier"] == ident and r["side_norm"] == side)
    return ident, side, row["intent"]


class TestTheSpreads:
    def test_c9_the_side_table_is_read_off_the_venues_question(self):
        rows = _board(_venue())
        for (tail, outcome), want in SIDE_TABLE.items():
            assert _attribution(rows, tail, outcome) == want, (tail, outcome)
        # neg-L is nobody's row on 'Spread: Seahawks (-L)'
        assert not any(v[0].count("-neg-") for v in SIDE_TABLE.values())

    def test_c9_the_spreads_refuse_by_name_and_never_bind_neg(self, armed):
        # RE-PINNED by C10 (2026-09-10, coverage lane C10; docs section 74):
        # this board's aec sides carry NO team dict (the C9 fixtures were
        # built before the nfl-team read), and on nfl the spread's subject
        # is now read from the aec row's own team field INSTEAD of the C4
        # code chain -- so under the switch the refusal on a team-less
        # board is `spread:team-absent` (nothing binds: fail closed), where
        # it was `spread:names-unreadable`. The switch-OFF branch below is
        # unchanged: today's refusal, byte for byte. The binding itself,
        # on the rows carrying the venue's team field, is pinned in
        # tests/test_c10_nfl_spreads.py (THE SIDE TABLE above holds).
        rows = _board(_venue())
        for (tail, outcome), (ident, _side, _intent) in SIDE_TABLE.items():
            slug = f"{HIS_ML}-{tail}"
            title = f"Spread: Seahawks (-{_L(tail.rsplit('-', 1)[-1])})"
            for ev in (None, HIS_TITLE):
                assert _resolve(rows, slug, title, outcome, ev) is None, (tail, outcome, ev)
                ex = _explain(rows, slug, title, outcome, ev)
                assert ex["step"] == "no_side_match", (tail, outcome, ev, ex)
                assert ex["split"] == "spread:team-absent" and ex["c3"]["refusal"] == "spread:team-absent"
                assert ex["c3"]["venue_names"] == ["new england patriots", "seattle seahawks"]
                assert ex["c3"]["aec"] == {"identifier": AEC, "sides": ["patriots", "seahawks"]}
                assert ex["date_shift"] == {"his": HIS_DATE, "venue": VENUE_DATE}
                assert "admitted" not in ex["c3"] and "-neg-" not in str(ex.get("matched_by") or "")
                # the C4 chain is never consulted on nfl: no code reading on the trace
                assert "code_hits" not in ex["c3"] and "code_school" not in ex["c3"]
        # the switch off (the identity switch): the same rows, today's refusal
        os.environ.pop(premap.PREMAP_YN_IDENTITY_ENV, None)
        ex = _explain(rows, f"{HIS_ML}-spread-home-3pt5", "Spread: Seahawks (-3.5)", "Seahawks")
        assert ex["step"] == "no_side_match" and "c3" not in ex and ex["date_shift"]["venue"] == VENUE_DATE

    def test_c9_the_c3_pick_on_the_shifted_slug_is_the_same_verdict(self, armed):
        # the pick itself, pure, on the venue's own identifiers: the name
        # step reads nothing ('seahawks' is not 'seattle seahawks'); C10:
        # on nfl the team-field reader runs instead of the code chain, and
        # this board's aec sides state no team -> spread:team-absent
        # (re-pinned from spread:names-unreadable, the reason above)
        rows = _board(_venue())
        tr: dict = {}
        hit = premap.c3_pick([r for r in rows if r["identifier"].startswith("asc-")], "Seahawks",
                             "Spread: Seahawks (-3.5)", f"nfl-ne-sea-{VENUE_DATE}-spread-home-3pt5", tr,
                             board=rows, cert={}, his_event_title=HIS_TITLE)
        assert hit is None and tr["refusal"] == "spread:team-absent"


# ------------------------------------------ (F) the football set (C6-ML)

# built in the PREMAP-TEAM probe's shape (docs section 23: abbreviation =
# the slug code, safeName the venue's own school name, name / alias the
# mascot); NO nfl team dict is on file, and these say so
TEAM_NE = {"abbreviation": "ne", "alias": "Patriots", "id": 1, "league": "nfl", "name": "Patriots",
           "ordering": "away", "safeName": "New England"}
TEAM_SEA = {"abbreviation": "sea", "alias": "Seahawks", "id": 2, "league": "nfl", "name": "Seahawks",
            "ordering": "home", "safeName": "Seattle"}
# a shape where no code rule reads his outcome, so the team field is the
# only reader (the C6 site itself): a code the first-word / split rules
# cannot read ('nwe' against 'New England')
TEAM_NWE = dict(TEAM_NE, abbreviation="nwe")


class TestTheFootballSet:
    def test_c9_football_leagues_and_the_two_sites(self):
        assert map_lane.FOOTBALL_LEAGUES == frozenset({"cfb", "nfl"})
        assert isinstance(map_lane.FOOTBALL_LEAGUES, frozenset)
        src = inspect.getsource(map_lane.aec_code_side)
        assert "if not hits and lg in FOOTBALL_LEAGUES and _identity_on():" in src
        assert 'lg == "cfb"' not in src
        src = inspect.getsource(map_lane.grammar_truth)
        assert "if head is not None and head[0] in FOOTBALL_LEAGUES:" in src and 'head[0] == "cfb"' not in src
        # the live lane's winner-row admission is NOT widened (E28 / E29's file)
        assert 'and lg == "cfb" and map_lane._identity_on()' in inspect.getsource(ml._contract_candidates)
        assert "FOOTBALL_LEAGUES" not in inspect.getsource(ml)

    def test_c9_the_c6_ml_team_field_reads_an_nfl_side_under_the_switch_and_is_dark_without_it(self, monkeypatch):
        slug = "nfl-nwe-sea-2026-09-10"
        mk = _aec("aec-nfl-nwe-sea-2026-09-10", Q_AEC, TEAM_NWE, TEAM_SEA)
        # no code rule reads 'New England' against nwe
        assert not map_lane.code_reads("nwe", "New England", "sea")
        monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)
        assert map_lane.aec_code_side(slug, "New England", mk, 0) == (None, "side_code_unmatched")
        monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")
        hit, why = map_lane.aec_code_side(slug, "New England", mk, 0)
        assert why is None and hit["outcome"] == "Patriots" and hit["intent"] == LONG and hit["side_index"] == 0
        # the mascot is never the school: his 'Patriots' reads nothing on
        # the team field (safeName 'New England'), on either setting
        assert map_lane.aec_code_side(slug, "Patriots", mk, 0) == (None, "side_code_unmatched")
        monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV)
        assert map_lane.aec_code_side(slug, "Patriots", mk, 0) == (None, "side_code_unmatched")
        # on the real codes the C1 first-word rule reads 'Seahawks' -> sea
        # before any team field, and 'Patriots' reads nothing
        real = _aec(AEC, Q_AEC, TEAM_NE, TEAM_SEA)
        hit, why = map_lane.aec_code_side(f"nfl-ne-sea-{VENUE_DATE}", "Seahawks", real, 1)
        assert why is None and hit["outcome"] == "Seahawks" and hit["intent"] == SHORT
        assert map_lane.aec_code_side(f"nfl-ne-sea-{VENUE_DATE}", "Patriots", real, 0) == (None, "side_code_unmatched")
        # a side stating a code at the other index: the venue's contradiction
        monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")
        swapped = _aec("aec-nfl-nwe-sea-2026-09-10", Q_AEC, TEAM_SEA, TEAM_NWE)
        assert map_lane.aec_code_side(slug, "New England", swapped, 0) == (None, "side_code_conflict")

    def test_c9_grammar_truth_reads_an_nfl_winner_contract_under_the_switch(self, monkeypatch):
        mk = _aec(AEC, Q_AEC, TEAM_NE, TEAM_SEA)
        con = {"slug": f"atc-nfl-ne-sea-{VENUE_DATE}-winner-1h-ne", "question": "Will New England win the first half?",
               "outcome": "Yes", "title": "Yes"}
        monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)
        assert map_lane.grammar_truth(con, mk, 0, code="ne", school="New England")[0] == "unverified"
        monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")
        assert map_lane.grammar_truth(con, mk, 0, code="ne", school="New England")[0] == "ok"
        # the winner question naming the FULL name beside a safeName that is
        # the city alone is a MISMATCH -- the trip the next lane must probe
        # the NFL team dict for before the live admission is widened
        full = dict(con, question="Will New England Patriots win the first half?")
        assert map_lane.grammar_truth(full, mk, 0, code="ne", school="New England")[0] == "mismatch"
        # a plain contract of another shape: unverified, never a trip
        plain = {"slug": f"atc-nfl-ne-sea-{VENUE_DATE}-ne", "question": "Will the Patriots win?", "outcome": "Yes"}
        assert map_lane.grammar_truth(plain, mk, 0, code="ne")[0] == "unverified"


# ------------------------------------------------ (G) the probe (a read)

def test_c9_the_league_alias_probe_asks_both_moneyline_kinds(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_NFL_DATE_TOL_ENV, "off")
    src = inspect.getsource(premap.resolve_explain)
    assert 'pat = (f"^(atc|aec)-[a-z0-9]+-({parts[\'a\']}-[a-z0-9]+|[a-z0-9]+-{parts[\'b\']})"' in src
    assert 'f"-{d}(-|$)")' in src and 'f"^atc-[a-z0-9]+-' not in src
    # the read, on a board that lists the game only under aec on his date
    # and keyed so that no key of his meets it: 'would_have_hit', no
    # 'venue:league-unlisted'. The bare moneyline slug parses through
    # neither slug reader the probe uses (no '-<rest>'), so it asks
    # nothing, as before; the family slugs ask
    rows = _board([_aec(f"aec-nfl-ne-sea-{HIS_DATE}", Q_AEC_CUT)])
    for r in rows:
        r["event_keys"] = ["something-else"]
    p = _Pool(rows)
    ex = asyncio.run(premap.resolve_explain(p, HIS_TITLE, None, "Over", HIS_ML + "-total-41pt5"))
    want_pat = f"^(atc|aec)-[a-z0-9]+-(ne-[a-z0-9]+|[a-z0-9]+-sea)-{HIS_DATE}(-|$)"
    assert ex["step"] == "no_key_intersection" and p.probe_patterns == [want_pat]
    assert ex["league_alias_probe"]["would_have_hit"] and ex["league_alias_probe"]["rows_it_would_find"] == 2
    assert "split" not in ex
    # the tail: an aec identifier ends at its date, an atc row carries a
    # suffix dash; a row on another date or another pair never matches
    import re as _re
    assert _re.search(want_pat, f"aec-nfl-ne-sea-{HIS_DATE}") and _re.search(want_pat, f"atc-nfl-ne-sea-{HIS_DATE}-winner-1h-ne")
    assert _re.search(want_pat, f"atc-xfl-ari-sea-{HIS_DATE}-ari")
    assert not _re.search(want_pat, AEC) and not _re.search(want_pat, f"aec-nfl-sea-ne-{HIS_DATE}")
    assert not _re.search(want_pat, f"asc-nfl-ne-sea-{HIS_DATE}-neg-3pt5") and not _re.search(want_pat, f"aec-nfl-ne-sea-{HIS_DATE}1")
    p = _Pool(rows)
    ex = asyncio.run(premap.resolve_explain(p, HIS_TITLE, None, "Patriots", HIS_ML))
    assert ex["step"] == "no_key_intersection" and p.probe_patterns == [] and "league_alias_probe" not in ex
    # nothing listed: unlisted, as before
    p = _Pool([])
    ex = asyncio.run(premap.resolve_explain(p, HIS_TITLE, None, "Over", HIS_ML + "-total-41pt5"))
    assert ex["split"] == "venue:league-unlisted"


# --------------------------------------------- (H) the seams and hashes

def _h(fn) -> str:
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]


def test_c9_the_untouched_arms_hash_as_6509878():
    assert _h(premap.match_side) == "6894ea0ebb90cefc"
    # RE-PINNED by C10 (docs section 74): _c3_pick_spread gained the nfl
    # team-field branch (31522f233c535a4a on 0b24564 -> the value below);
    # every other function here is 0b24564 byte for byte
    assert _h(premap._c3_pick_spread) == "354668be1edc3d2d"
    assert _h(premap._c3_pick_total) == "c07814e34a74b0b6"
    assert _h(premap._c4_subject_by_code) == "6a7ae0f56c7ae4e2"
    assert _h(premap._c6_team_subject) == "403035d6c15a74bb"
    assert _h(map_lane._team_hits) == "964c90ddbeb5cad5"
    assert _h(map_lane._team_truth) == "afc42ffbbba39aad"
    assert _h(premap._pair_key) == "4f457aefeb08b39f" and _h(premap.keys_for_row) == "338c8ad313b0afbd"
    assert _h(premap.yn_identity_on) == "66fca2a5f93747a6"


def test_c9_the_names_the_switch_and_no_census_word():
    assert premap.PREMAP_NFL_DATE_TOL_ENV == "PREMAP_NFL_DATE_TOL" and premap.NFL_DATE_TOL_LEAGUE == "nfl"
    assert premap._NFL_DATE_TOL_ON == frozenset({"", "on", "1", "true", "yes"})
    src = inspect.getsource(premap.nfl_date_tol_on)
    assert 'os.getenv(PREMAP_NFL_DATE_TOL_ENV, "").strip().lower() in _NFL_DATE_TOL_ON' in src
    # the same reader shape as the identity switch
    assert 'os.getenv(PREMAP_YN_IDENTITY_ENV, "").strip().lower() == "on"' in inspect.getsource(premap.yn_identity_on)
    # the step name in both resolvers' style: the explain names it, resolve returns None
    ex_src, rs_src = inspect.getsource(premap.resolve_explain), inspect.getsource(premap.resolve)
    assert 'out["step"] = "date_ambiguous"' in ex_src and "nfl_date_read(rows, global_slug)" in ex_src
    assert "nfl_date_read(rows, global_slug)" in rs_src and 'out["date_shift"] = dict(date_trace["date_shift"])' in rs_src
    assert rs_src.index("nfl_date_read(rows, global_slug)") < rs_src.index("_want_prefixes(global_slug)")
    assert ex_src.index("nfl_date_read(rows, global_slug)") < ex_src.index("mtype = market_type_of(global_slug")
    # both resolvers hand the slug to the dated filter
    assert rs_src.count("_dated_admissible(keys, d, slug=global_slug)") == 1
    assert ex_src.count("_dated_admissible(keys, d, slug=global_slug)") == 1
    # no census name, no decision word, no rule, no migration
    assert "date_ambiguous" not in ml.CENSUS_KEYS and "date_ambiguous" not in inspect.getsource(ml)
    assert "date_shift" not in inspect.getsource(ml)
    from sportsassets.analytics import mirror_live_rules as rules
    assert "NFL" not in inspect.getsource(rules) and "date_tol" not in inspect.getsource(rules)
    assert "nfl_date" not in inspect.getsource(ms)
    # the venue's keys_for_row and the sweep's key site are the R1 / C6-N
    # statement, unchanged
    assert "await _upsert(pool, r, keys_for_row(keys, r))" in inspect.getsource(premap.refresh)
    # the docs section, in the house header form
    import re as _re
    from pathlib import Path
    doc = (Path(__file__).resolve().parents[2] / "docs" / "mirror-coverage.md").read_text()
    assert _re.search(r"^## \d+\. C9 -- .* \(2026-09-10, coverage lane C9\)", doc, _re.M), "the C9 section header"
    sec = doc[doc.index("## 72. C9 -- "):]
    for word in ("PREMAP_NFL_DATE_TOL", "date_ambiguous", "date_shift", "date_candidates", "FOOTBALL_LEAGUES",
                 "spread:names-unreadable", "THE SIDE TABLE", "DOES NOT FIX", "nfl_date_read", "nfl_date_keys"):
        assert word in sec, word


# ----------------------------------------- (I) the worker world: a book

class _NflPool(_LivePool):
    """The worker's database with the venue's NFL rows in us_premap and
    no ledger row on his tokens (the premap arm is the mapper)."""

    def __init__(self, rows, **kw):
        kw.setdefault("mapped", False)
        super().__init__(**kw)
        self.premap_rows = rows

    async def fetch(self, sql, *a):
        if "us_premap" in sql and "event_keys &&" in sql:
            k = set(a[0])
            return [dict(r) for r in self.premap_rows if set(r["event_keys"]) & k]
        return await super().fetch(sql, *a)


def _nfl_fills(size=300.0, px=0.31):
    return [_fill(M, "BUY", size, px, NOW - 3000, market_slug=HIS_ML, event_slug=HIS_ML,
                  market_title=HIS_TITLE, outcome="Patriots", outcome_index=0)]


def test_c9_a_candidate_opens_a_long_book_on_the_aec_nfl_market(monkeypatch):
    monkeypatch.setattr(ms, "_map_cache", {})
    ml._unmapped_until.clear()
    ml._unmapped_memo.clear()
    ml._terminal_until.clear()
    p = _NflPool(_board(_venue()), fills=_nfl_fills(), snap={M: 300.0, N: 0.0}, snap_at=NOW - 40,
                 ratio_fills=_ratio_fills())
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "unmapped") == 0 and _census(st, "map_source_unverified") == 0, st["census"]
    assert p.books, "the book opened"
    b = next(iter(p.books.values()))
    assert b["us_market_slug"] == AEC and b["long_asset"] == M and b["map_source"] == "premap"
    assert b.get("intent") in (LONG, None)
    # the fixture world's ratio is 1.0 (tests/test_mirror_live_worker's
    # _ratio_fills: "ratio 1.0"; its own pin `b["ratio"] == 1.0 and
    # b["target"] == 300`), so the target is his 300 shares here; the
    # production ratio (rules.MIRROR_RATIO, 10%) is not this lane's
    assert b["ratio"] == 1.0 and b["target"] == 300
    placed = [o for o in p.orders.values()]
    assert placed and all(o["side"] == "BUY_LONG" for o in placed), placed
    assert sum(o["qty"] for o in placed) == 300
    assert any(c == ("bbo", AEC) for c in v.calls), "the quote read on the venue's own identifier"
    assert not any(r.get("refusal") == "unmapped" for r in p.cand_refusals)


def test_c9_with_the_switch_off_the_candidate_stays_unmapped(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_NFL_DATE_TOL_ENV, "off")
    monkeypatch.setattr(ms, "_map_cache", {})
    ml._unmapped_until.clear()
    ml._unmapped_memo.clear()
    ml._terminal_until.clear()
    p = _NflPool(_board(_venue()), fills=_nfl_fills(), snap={M: 300.0, N: 0.0}, snap_at=NOW - 40,
                 ratio_fills=_ratio_fills())
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "unmapped") == 1 and not p.books and not p.orders
