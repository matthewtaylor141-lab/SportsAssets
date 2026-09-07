"""C1 (owner order 2026-09-06, "let's remove those caps so we start
copying his actual book"): the mirror maps what the copy lane already
maps. After premap says no, map_market runs the copy lane's exact steps
(map_lane.exact_lane) -- paced, cached per market, bounded per tick --
and the mirror-only aec code-order side; never the fuzzy step. Driven
with fakes: no venue, no database.

Two things this file is the proof of:
  1. the COPY LANE DID NOT MOVE -- its inline block stays byte-identical
     (its own tests pin the text) and this file drives that block and
     the shared lane with the same fake resolvers and asserts the same
     calls, in the same order, on the same candidates;
  2. every new refusal is NAMED and fails closed: a market the new steps
     also fail keeps premap's own explain; a college game whose outcome
     names neither team code is `side_code_unmatched`; a spent budget is
     `map_reads_capped` (no verdict, never TTL-skipped); a source the
     live worker has not certified opens no book.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from datetime import date

import pytest

from sportsassets import live_executor as le
from sportsassets import map_lane, pmus
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import premap
from tests.test_mirror_shadow import CID, HIS, M, N, RATIO, SLUG, _fill, _nosleep, _Pmus, _Pool

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
CFB = "cfb-bayl-aubrn-2026-09-05"
AEC_CFB = "aec-cfb-bayl-aubrn-2026-09-05"
# the per-team yes/no shape the copy lane's own lane ACCEPTS (its
# fixture club pair: both names prefix their codes under the lane's
# collapsed-name rule); RB Leipzig does not (yn:title-code) -- see
# test_the_leipzig_row_maps_by_premap_identity_not_by_the_yesno_lane
AME = "lmx-ame-san-2026-09-05-ame"
ATC_AME = "atc-lmx-ame-san-2026-09-05-ame"
Q_AME = ("Will CF América win against Club Santos Laguna in the Liga MX match "
         "scheduled for September 5, 2026?")
T_AME = "Will CF América win on 2026-09-05?"
EV_AME = "CF América vs. Club Santos Laguna"
LEI = "bun-wer-lei-2026-09-05-lei"
ATC_LEI = "atc-bun-wer-lei-2026-09-05-lei"
Q_LEI = ("Will RB Leipzig win against SV Werder Bremen in the Bundesliga match "
         "scheduled for Sep 5, 2026?")
T_LEI = "Will RB Leipzig win on 2026-09-05?"
EV_LEI = "SV Werder Bremen vs. RB Leipzig"


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- fakes

class _Markets:
    def __init__(self, table, calls):
        self.table, self.calls = table, calls

    def retrieve_by_slug(self, slug):
        self.calls.append(slug)
        if slug not in self.table:
            raise KeyError(slug)
        return {"market": self.table[slug]}


class _Client:
    def __init__(self, table, calls, portfolio):
        self.markets = _Markets(table, calls)
        self.portfolio = portfolio


class _Venue(_Pmus):
    """The shadow's venue fake plus the copy lane's resolvers, answering
    from a slug table through the REAL pmus resolvers (pmus._get_client
    is pointed at the table), so what maps here is what the copy lane's
    own resolvers map."""

    def __init__(self, table=None, **kw):
        super().__init__(**kw)
        self.table = dict(table or {})
        self.slug_calls: list[str] = []
        self.resolve_market_exact = pmus.resolve_market_exact
        self.resolve_derivative_exact = pmus.resolve_derivative_exact
        self.resolve_team_yesno_exact = pmus.resolve_team_yesno_exact
        self.order_intent_for = pmus.order_intent_for

    def _get_client(self):
        return _Client(self.table, self.slug_calls, self.portfolio)


def _aec_cfb():
    """The game's aec- market. The SIDES are the mascots either way: the
    in-repo fixture (tests/test_pmus_account.py:287-293) carries the venue's
    positions row with title "Bears vs. Tigers" and outcome "Bears"; the
    live probe of 2026-09-03 (the account's positions payload on the same
    slug) carries title "Baylor vs. Auburn" and outcome "Bears". The title
    used here is the probe's (schools in slug order); the rule never reads
    a mascot title as a witness, so either title maps the same way."""
    return {AEC_CFB: {"slug": AEC_CFB, "closed": False, "question": "Baylor vs. Auburn",
                      "title": "Baylor vs. Auburn",
                      "marketSides": [{"identifier": AEC_CFB, "description": "Bears", "long": True},
                                      {"identifier": AEC_CFB, "description": "Tigers", "long": False}]}}


ATC_BAYL = AEC_CFB.replace("aec-", "atc-") + "-bayl"


def _atc_bayl(outcome="Bears"):
    """The venue's per-side contract for Baylor, naming the side it is."""
    return {ATC_BAYL: {"slug": ATC_BAYL, "closed": False, "title": outcome, "outcome": outcome,
                       "marketSides": []}}


def _yesno_market(ident, q, event_slug):
    return {ident: {"slug": ident, "closed": False, "question": q, "eventSlug": event_slug,
                    "marketSides": [{"identifier": ident, "description": "Yes", "long": True},
                                    {"identifier": ident, "description": "No", "long": False}]}}


def _cfb_fills(outcome_a="Baylor", outcome_b="Auburn", index_a=0, index_b=1):
    a = _fill("tok-bayl", "BUY", 1000.0, 0.55, 1000, market_title="Baylor vs. Auburn",
              event_title="Baylor vs. Auburn", event_slug=CFB, market_slug=CFB,
              outcome=outcome_a, outcome_index=index_a)
    b = _fill("tok-aubrn", "BUY", 10.0, 0.45, 1100, market_title="Baylor vs. Auburn",
              event_title="Baylor vs. Auburn", event_slug=CFB, market_slug=CFB,
              outcome=outcome_b, outcome_index=index_b)
    return [a, b]


def _yesno_fills(slug, title, event_title, event_slug):
    y = _fill("tok-yes", "BUY", 500.0, 0.40, 1000, market_title=title, event_title=event_title,
              event_slug=event_slug, market_slug=slug, outcome="Yes", outcome_index=0)
    n = _fill("tok-no", "BUY", 20.0, 0.60, 1100, market_title=title, event_title=event_title,
              event_slug=event_slug, market_slug=slug, outcome="No", outcome_index=1)
    return [y, n]


def _ame_fills():
    return _yesno_fills(AME, T_AME, EV_AME, "lmx-ame-san-2026-09-05")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """Every test starts with an empty mapping memory and a venue client
    that is the fake's (the real resolvers call pmus._get_client)."""
    _nosleep(monkeypatch)
    monkeypatch.setattr(ms, "_map_cache", {})
    monkeypatch.setattr(ms, "_slug_404_until", {})
    monkeypatch.setattr(ms, "_unmapped_until", {})
    monkeypatch.setattr(ml, "_unmapped_until", {})
    monkeypatch.delenv("PREMAP_YN_IDENTITY", raising=False)
    yield


def _with_venue(monkeypatch, venue):
    monkeypatch.setattr(pmus, "_get_client", venue._get_client)
    return venue


def _map(monkeypatch, fills, venue, budget=None, out=None):
    _with_venue(monkeypatch, venue)
    p = _Pool(fills=fills, mapped=False)
    return _run(ms.map_market(p, fills, venue, whale="rn1", condition_id=CID,
                              budget=budget, out=out))


# -------------------------------------------- 1. the sample slugs map

def test_the_soccer_per_team_yes_no_maps_through_the_copy_lanes_yesno_lane(monkeypatch):
    """lmx-ame-san-2026-09-05-ame, outcome Yes -> atc-lmx-ame-san-2026-09-05-ame
    by the copy lane's per-team yes/no lane (premap refuses it on
    wording); the Yes token is the long side, source 'yesno', ONE token
    read: the No token is the other side of the same contract by
    construction."""
    out: dict = {}
    venue = _Venue(_yesno_market(ATC_AME, Q_AME, "lmx-ame-san-2026-09-05"))
    m = _map(monkeypatch, _ame_fills(), venue, out=out)
    assert m == {"us_slug": ATC_AME, "long_asset": "tok-yes", "other_asset": "tok-no",
                 "source": "yesno"}
    assert out["lane"] == "yesno" and out["venue_reads"] >= 1 and "refusal" not in out
    assert ATC_AME in venue.slug_calls


def test_the_leipzig_row_maps_by_premap_identity_not_by_the_yesno_lane(monkeypatch):
    """bun-wer-lei-2026-09-05-lei ('Will RB Leipzig win on 2026-09-05?',
    outcome Yes): the copy lane's yes/no lane REFUSES it pre-network --
    'rb leipzig' does not prefix the code 'lei' under the lane's own
    collapsed-name rule (yn:title-code) -- and the copy lane only ever
    mapped it through the fuzzy step, which the mirror never runs. What
    maps it network-free is premap's identity branch (PREMAP_YN_IDENTITY,
    source 'premap'): with the venue row in the table and the owner's
    flip on, map_market answers atc-bun-wer-lei-2026-09-05-lei from
    Postgres before the exact lane is reached; with the flip off it is
    unmapped under premap's own name, and the exact lane's refusal is
    the copy lane's own code."""
    d: list = []
    assert pmus.resolve_team_yesno_exact(LEI, "Yes", T_LEI, EV_LEI, d) is None
    assert d == ["yn:title-code"]
    rows = premap._market_rows({"slug": "aec-bun-wer-lei-2026-09-05", "title": EV_LEI},
                               _yesno_market(ATC_LEI, Q_LEI, "bun-wer-lei-2026-09-05")[ATC_LEI])
    assert [r["side_norm"] for r in rows] == ["yes", "no"]

    class _P(_Pool):
        async def fetch(self, sql, *a):
            if "FROM us_premap" in sql:
                return [dict(r) for r in rows]
            return await super().fetch(sql, *a)

    fills = _yesno_fills(LEI, T_LEI, EV_LEI, "bun-wer-lei-2026-09-05")
    venue = _Venue({})
    _with_venue(monkeypatch, venue)
    assert _run(ms.map_market(_P(fills=fills, mapped=False), fills, venue, whale="rn1",
                              condition_id=CID)) is None
    monkeypatch.setenv("PREMAP_YN_IDENTITY", "on")
    monkeypatch.setattr(ms, "_map_cache", {})
    m = _run(ms.map_market(_P(fills=fills, mapped=False), fills, venue, whale="rn1",
                           condition_id=CID))
    assert m == {"us_slug": ATC_LEI, "long_asset": "tok-yes", "other_asset": "tok-no",
                 "source": "premap"}


def test_college_football_maps_by_the_team_code_never_the_mascot(monkeypatch):
    """The venue names mascots ('Bears'/'Tigers'), his feed names schools.
    The exact resolver finds aec-cfb-bayl-aubrn-2026-09-05 by slug grammar
    and refuses the side by name (Baylor is not Bears); the code side maps
    Baylor -- his outcome names 'bayl' by the whole-word/first-word rule
    and his outcome index says position 0 -- onto the side at bayl's
    position, the venue's long side, source 'grammar'. Auburn is NEVER
    named by 'aubrn' (no subsequence, review A): his Auburn token is the
    other side by the PAIR rule -- complementary outcome index, no claim
    on the mapped code -- with no venue read of its own."""
    v = _Venue(_aec_cfb())
    out: dict = {}
    m = _map(monkeypatch, _cfb_fills(), v, out=out)
    assert m == {"us_slug": AEC_CFB, "long_asset": "tok-bayl", "other_asset": "tok-aubrn",
                 "source": "grammar"}
    assert out["lane"] == "grammar"
    assert out["grammar"]["side_index"] == 0 and out["grammar"]["outcome_desc"] == "Bears"
    assert out["grammar"]["intent"] == LONG and out["grammar"]["his_slug"] == CFB
    assert v.slug_calls.count(AEC_CFB) == 2, "one exact read, one grammar read, first token only"
    # the same market with his larger position on Auburn: the Auburn token
    # is read first, names no code (unmatched, pair-resolvable), the Baylor
    # token maps, and the pair rule accepts Auburn as the other side
    fills = _cfb_fills()
    fills[0]["size"], fills[1]["size"] = 10.0, 1000.0
    monkeypatch.setattr(ms, "_map_cache", {})
    m2 = _map(monkeypatch, fills, _Venue(_aec_cfb()))
    assert m2 == {"us_slug": AEC_CFB, "long_asset": "tok-bayl", "other_asset": "tok-aubrn",
                  "source": "grammar"}
    # the mascot never voted: the venue side descriptions are not his outcomes
    hit, why = map_lane.aec_code_side(CFB, "Bears", _aec_cfb()[AEC_CFB], 0)
    assert hit is None and why == "side_code_unmatched"
    # the whole-word / first-word rule (review A): no substring, no subsequence
    assert map_lane.code_hits("bayl", "Baylor") and map_lane.code_hits("ucla", "UCLA Bruins")
    assert map_lane.code_hits("hawaii", "Hawaii") and map_lane.code_hits("mich", "Michigan")
    assert not map_lane.code_hits("aubrn", "Auburn"), "an abbreviation is not a prefix"
    assert map_lane.code_hits("mich", "Michigan State"), "first word: Michigan State hits mich ..."
    assert not map_lane.code_hits("mist", "Michigan State"), "... and not mist: the index decides"
    assert map_lane.code_hits("state", "Michigan State"), "a whole word anywhere hits ..."
    assert not map_lane.code_hits("stat", "Michigan State"), "... a prefix of a later word never"
    assert not map_lane.code_hits("aubrn", "Sooners") and not map_lane.code_hits("bayl", "Sooners")
    assert not map_lane.code_hits("aubrn", "Texas A&M"), "a one-letter word is never a code"
    assert not map_lane.code_hits("ba", "Baylor"), "codes under three letters never hit"
    # the pair rule
    assert map_lane.pair_agrees(CFB, 0, "Auburn", 1) is None
    assert map_lane.pair_agrees(CFB, 0, "Auburn", 0) == "side_code_pair"
    assert map_lane.pair_agrees(CFB, 0, "Auburn", None) == "side_code_pair"
    assert map_lane.pair_agrees(CFB, 0, "Baylor", 1) == "side_code_conflict"
    assert map_lane.pair_agrees(CFB, 1, "Baylor", 0) is None
    # a sibling whose index disagrees refuses the MARKET, not just the token
    bad = _cfb_fills(index_b=0)
    monkeypatch.setattr(ms, "_map_cache", {})
    out3: dict = {}
    assert _map(monkeypatch, bad, _Venue(_aec_cfb()), out=out3) is None
    assert out3["refusal"] == "side_code_pair"
    # and a token with NO outcome index refuses (mandatory, review A)
    noidx = _cfb_fills(index_a=None)
    monkeypatch.setattr(ms, "_map_cache", {})
    out4: dict = {}
    assert _map(monkeypatch, noidx, _Venue(_aec_cfb()), out=out4) is None
    assert out4["refusal"] == "side_code_no_index"


def test_an_outcome_naming_neither_code_refuses_side_code_unmatched(monkeypatch):
    """'Sooners' names neither bayl nor aubrn: the market is found and
    the side is REFUSED by name -- the shadow row says so, and it is
    remembered as unmapped (a verdict), not re-read every tick."""
    out: dict = {}
    m = _map(monkeypatch, _cfb_fills("Sooners", "Aggies"), _Venue(_aec_cfb()), out=out)
    assert m is None and out["refusal"] == "side_code_unmatched"
    p = _Pool(fills=_cfb_fills("Sooners", "Aggies"), mapped=False)
    monkeypatch.setattr(ms, "_map_cache", {})
    row = _run(ms.shadow_market(p, _Venue(_aec_cfb()), "rn1", CID, RATIO, {}, positions={}))
    assert row["reason"].startswith("unmapped") and row["detail"]["explain"] == "side_code_unmatched"


UH = "cfb-ucla-hawaii-2026-09-05"
AEC_UH = "aec-" + UH


def _aec_uh(d0="Bruins", d1="Rainbow Warriors", title="UCLA vs. Hawaii"):
    return {"slug": AEC_UH, "closed": False, "question": title, "title": title,
            "marketSides": [{"identifier": AEC_UH, "description": d0, "long": True},
                            {"identifier": AEC_UH, "description": d1, "long": False}]}


def test_the_code_side_refuses_every_contradiction_by_name():
    mk = _aec_cfb()[AEC_CFB]
    # both codes hit as whole words: ambiguous
    assert map_lane.aec_code_side(CFB, "Bayl Aubrn", mk, 0)[1] == "side_code_ambiguous"
    # no outcome index: refuse (mandatory, review A)
    assert map_lane.aec_code_side(CFB, "Baylor", mk, None)[1] == "side_code_no_index"
    assert map_lane.aec_code_side(CFB, "Baylor", mk, "x")[1] == "side_code_no_index"
    # his feed's outcome index disagrees with the code's position
    assert map_lane.aec_code_side(CFB, "Baylor", mk, 1)[1] == "side_code_conflict"
    # his slug's own side token names the other team
    assert map_lane.aec_code_side(CFB + "-aubrn", "Baylor", mk, 0)[1] == "side_code_conflict"
    # a venue side whose description names the OTHER position's code and
    # not its own (UCLA/Hawaii: both codes are whole words)
    assert map_lane.aec_code_side(UH, "UCLA", _aec_uh("Hawaii Rainbow Warriors", "UCLA Bruins"),
                                  0)[1] == "side_code_conflict"
    # a description naming BOTH codes (review B)
    assert map_lane.aec_code_side(UH, "UCLA", _aec_uh("UCLA vs Hawaii", "Rainbow Warriors"),
                                  0)[1] == "side_code_conflict"
    # the venue's own matchup title naming the codes in the opposite order
    assert map_lane.aec_code_side(UH, "UCLA", _aec_uh(title="Hawaii vs. UCLA"), 0)[1] \
        == "side_code_conflict"
    # a title that names the schools in slug order corroborates; a mascot
    # title says nothing
    assert map_lane.aec_code_side(UH, "UCLA", _aec_uh(), 0)[1] is None
    assert map_lane.aec_code_side(UH, "UCLA", _aec_uh(title="Bruins vs. Rainbow Warriors"), 0)[1] \
        is None
    # not the aec shape: distinct per-side identifiers, a slug that is not
    # his echoed, closed
    distinct = json.loads(json.dumps(mk))
    distinct["marketSides"][0]["identifier"] = AEC_CFB + "-bayl"
    assert map_lane.aec_code_side(CFB, "Baylor", distinct, 0)[1] == "side_code_shape"
    other = dict(mk, slug="aec-cfb-aubrn-bayl-2026-09-05")
    assert map_lane.aec_code_side(CFB, "Baylor", other, 0)[1] == "side_code_shape"
    closed = dict(mk, closed=True)
    assert map_lane.aec_code_side(CFB, "Baylor", closed, 0)[1] == "side_code_shape"
    # no long/short marker on the side: the venue named no intent, refuse
    unmarked = json.loads(json.dumps(mk))
    for sd in unmarked["marketSides"]:
        sd.pop("long")
    assert map_lane.aec_code_side(CFB, "Baylor", unmarked, 0)[1] == "side_code_nointent"
    # the accepting shape: Baylor at position 0, the venue's long side
    hit, why = map_lane.aec_code_side(CFB, "Baylor", mk, 0)
    assert why is None and hit["market_slug"] == AEC_CFB and hit["intent"] == LONG
    assert hit["matched_by"] == "aec_code_order" and hit["outcome"] == "Bears"
    assert hit["side_index"] == 0
    # Hawaii at position 1, the venue's short side
    hit2, why2 = map_lane.aec_code_side(UH, "Hawaii", _aec_uh(), 1)
    assert why2 is None and hit2["intent"] == SHORT and hit2["outcome"] == "Rainbow Warriors"
    # a code shorter than three letters never selects a side
    assert map_lane.aec_code_side("cfb-ba-aubrn-2026-09-05", "Baylor",
                                  dict(mk, slug="aec-cfb-ba-aubrn-2026-09-05"), 0)[1] \
        == "side_code_unmatched"


def test_the_venue_truth_and_the_fill_echo_are_pure_and_fail_closed():
    """map_lane.grammar_truth (before an open) and grammar_fill_echo (the
    first fill): venue-only strings on both sides, exact-equal or not."""
    mk = _aec_cfb()[AEC_CFB]
    con = _atc_bayl()[ATC_BAYL]
    assert map_lane.contract_slug(CFB, 0) == ATC_BAYL
    assert map_lane.contract_slug(CFB, 1) == AEC_CFB.replace("aec-", "atc-") + "-aubrn"
    assert map_lane.contract_slug("nonsense", 0) is None
    assert map_lane.grammar_truth(con, mk, 0)[0] == "ok"
    # the contract naming the side through its team fields is the same fact
    team = {ATC_BAYL: {"slug": ATC_BAYL, "title": "Baylor", "team": {"name": "Bears"}}}
    assert map_lane.grammar_truth(team[ATC_BAYL], mk, 0)[0] == "ok"
    # a listed contract that names the OTHER side: MISMATCH (refuse + trip);
    # one that names neither side by name is a shape the check cannot
    # read: unverified (round 3, review 2)
    assert map_lane.grammar_truth(_atc_bayl("Tigers")[ATC_BAYL], mk, 0)[0] == "mismatch"
    assert map_lane.grammar_truth(_atc_bayl("Baylor")[ATC_BAYL], mk, 0)[0] == "unverified"
    # unlisted / unreadable: unverified (refuse, nothing tripped)
    assert map_lane.grammar_truth(None, mk, 0)[0] == "unverified"
    assert map_lane.grammar_truth({}, mk, 0)[0] == "unverified"
    assert map_lane.grammar_truth(con, None, 0)[0] == "unverified"
    assert map_lane.grammar_truth(con, mk, 2)[0] == "unverified"
    # the fill echo: the venue must hold the chosen side with the book's sign
    ok = {"net": 208.0, "outcome": "Bears", "title": "Baylor vs. Auburn"}
    assert map_lane.grammar_fill_echo(ok, "Bears", LONG, 208.0)[0] == "ok"
    assert map_lane.grammar_fill_echo(ok, "Tigers", LONG, 208.0)[0] == "mismatch"
    assert map_lane.grammar_fill_echo(dict(ok, net=-208.0), "Bears", LONG, 208.0)[0] == "mismatch"
    assert map_lane.grammar_fill_echo(ok, "Bears", SHORT, 208.0)[0] == "mismatch"
    # unattributable, unheld, unreadable: unverified, never a verdict
    assert map_lane.grammar_fill_echo(ok, "Bears", LONG, 100.0)[0] == "unverified"
    assert map_lane.grammar_fill_echo(dict(ok, net=0.0), "Bears", LONG, 208.0)[0] == "unverified"
    assert map_lane.grammar_fill_echo(None, "Bears", LONG, 208.0)[0] == "unverified"
    assert map_lane.grammar_fill_echo(dict(ok, outcome=None), "Bears", LONG, 208.0)[0] == "unverified"


def test_a_venue_that_lists_nothing_keeps_premaps_own_explain(monkeypatch):
    """Every candidate 404s and the yes/no lane refuses: the market is
    unmapped under premap's own step name, never a new one."""
    async def _explain(pool, market_title, event_title, outcome, global_slug, **_kw):
        return {"step": "no_side_match", "detail": "x", "keys": 3, "rows": 2}

    monkeypatch.setattr(premap, "resolve_explain", _explain)
    v = _Venue({})
    _with_venue(monkeypatch, v)
    p = _Pool(fills=_cfb_fills(), mapped=False)
    row = _run(ms.shadow_market(p, v, "rn1", CID, RATIO, {}, positions={}))
    assert row["reason"].startswith("unmapped") and row["detail"]["explain"] == "no_side_match"
    assert row["detail"]["map_venue_reads"] >= 1
    assert all(c in v.slug_calls for c in ("atc-cfb-bayl-aubrn-2026-09-05-bayl", AEC_CFB, CFB))


def test_the_unknown_market_type_explain_carries_its_split(monkeypatch):
    """Measurement only: the step name the copy lane's census buckets on
    stays, and the shadow's explain says which gap it is."""
    class _P(_Pool):
        async def fetch(self, sql, *a):
            if "FROM us_premap" in sql:
                return [{"identifier": "astatc-x", "side_norm": "yes"}]
            return await super().fetch(sql, *a)

    p = _P(fills=HIS)
    ex = _run(premap.resolve_explain(p, "Motherwell vs Dundee", None, "3-0",
                                     "elc-mot-dun-2026-09-02-es-3-0"))
    assert ex["step"] == "unknown_market_type" and ex["split"] == "family_not_listed"
    ex2 = _run(premap.resolve_explain(p, "x", None, "y", "elc-mot-dun-2026-09-02-weird-thing"))
    assert ex2["step"] == "unknown_market_type" and ex2["split"] == "unparsed"
    ctx = {"title": "Motherwell vs Dundee", "event_title": None, "outcome": "3-0",
           "his_slug": "elc-mot-dun-2026-09-02-es-3-0"}
    assert _run(ms.explain_unmapped(p, ctx)) == "unknown_market_type:family_not_listed"
    assert _run(ms.explain_unmapped(p, ctx, "side_code_unmatched")) == "side_code_unmatched"


# ----------------------------------------------- 2. cache and budget

def test_two_ticks_one_venue_read(monkeypatch):
    """The exact lane's answer is remembered per (whale, condition_id):
    the tick that follows maps from memory, `map_cache_hit`, no read."""
    # the cache clock is the module's time.time (pinned HERE only: the
    # copy lane's own gates read the real clock in the drift pin below)
    monkeypatch.setattr(ms.time, "time", lambda: 5000.0)
    v = _Venue(_yesno_market(ATC_AME, Q_AME, "lmx-ame-san-2026-09-05"))
    out1: dict = {}
    m1 = _map(monkeypatch, _ame_fills(), v, out=out1)
    reads = len(v.slug_calls)
    assert m1 and reads >= 1 and out1["venue_reads"] >= 1 and "cache_hit" not in out1
    out2: dict = {}
    m2 = _map(monkeypatch, _ame_fills(), v, out=out2)
    assert m2 == m1 and len(v.slug_calls) == reads and out2["cache_hit"] == 1
    assert "venue_reads" not in out2
    # a miss is remembered the same way: the second tick reads nothing
    v2 = _Venue({})
    monkeypatch.setattr(ms, "_map_cache", {})
    monkeypatch.setattr(ms, "_slug_404_until", {})
    assert _map(monkeypatch, _cfb_fills(), v2) is None
    n = len(v2.slug_calls)
    assert n >= 1
    out3: dict = {}
    assert _map(monkeypatch, _cfb_fills(), v2, out=out3) is None
    assert len(v2.slug_calls) == n and out3["cache_hit"] == 2
    # and it expires with the TTL
    monkeypatch.setattr(ms.time, "time", lambda: 5000.0 + ms.MAP_CACHE_TTL_S + 1)
    assert _map(monkeypatch, _cfb_fills(), v2) is None
    assert len(v2.slug_calls) > n


def test_eleven_candidates_ten_reads_the_eleventh_is_capped(monkeypatch):
    """The per-tick budget: MIRROR_MAP_READS (10) resolver calls a tick;
    the market that would need the eleventh stays unmapped THIS tick
    under `map_reads_capped` -- no verdict, not remembered, read again
    next tick -- and the reads already made are not lost. Spread slugs:
    the derivative lane is exactly one resolver call per market."""
    assert ms.MAP_READS_PER_TICK == 10
    v = _Venue({})
    _with_venue(monkeypatch, v)
    budget = ms.MapBudget()
    calls = []

    def _one_read(slug, outcome, title=None):
        calls.append(slug)
        return None

    v.resolve_derivative_exact = _one_read

    def _fills(i):
        return [_fill(f"tok-{i}", "BUY", 100.0, 0.5, 1000 + i, market_title=f"Team{i} spread",
                      event_title=None, event_slug=None,
                      market_slug=f"lg-t{i}-o{i}-2026-09-05-t{i}-1pt5",
                      outcome=f"Team{i}", outcome_index=0)]

    verdicts = []
    for i in range(11):
        out: dict = {}
        fills = _fills(i)
        m = _run(ms.map_market(_Pool(fills=fills, mapped=False), fills, v, whale="rn1",
                               condition_id=f"c{i}", budget=budget, out=out))
        verdicts.append((m, out.get("refusal"), out.get("venue_reads")))
    assert len(calls) == 10 and budget.reads == 10 and budget.capped == 1
    assert verdicts[:10] == [(None, None, 1)] * 10
    assert verdicts[10] == (None, "map_reads_capped", None)
    # the capped market carries no verdict: a fresh budget maps it next tick
    fresh = ms.MapBudget()
    out: dict = {}
    fills = _fills(10)
    _run(ms.map_market(_Pool(fills=fills, mapped=False), fills, v, whale="rn1",
                       condition_id="c10", budget=fresh, out=out))
    assert fresh.reads == 1 and out.get("refusal") is None and len(calls) == 11
    # the ten that were read are remembered: nothing re-read under a fresh budget
    fresh2 = ms.MapBudget()
    fills = _fills(0)
    out0: dict = {}
    _run(ms.map_market(_Pool(fills=fills, mapped=False), fills, v, whale="rn1",
                       condition_id="c0", budget=fresh2, out=out0))
    assert fresh2.reads == 0 and out0["cache_hit"] == 1 and len(calls) == 11


def test_the_tick_counts_the_lane_and_never_ttl_skips_a_capped_market(monkeypatch):
    """tick_once: mapped_by counts every source, the reads and cache hits
    are on the census, a capped market's reason never starts with
    'unmapped' so the unmapped TTL never remembers it."""
    monkeypatch.setenv("MIRROR_WHALES", "rn1")
    ms._ratio_cache.update(at=0.0, by_whale={})
    ms._backoff_until = 0.0
    v = _Venue(_yesno_market(ATC_AME, Q_AME, "lmx-ame-san-2026-09-05"))
    _with_venue(monkeypatch, v)
    p = _Pool(fills=_ame_fills(), mapped=False, whales_ratio_fills=[])
    stats = _run(ms.tick_once(p, v, now_ts=5000.0))
    assert not stats.get("abandoned"), stats
    assert stats["mapped_by"]["yesno"] == 1 and stats["unmapped"] == 0
    assert stats["map_venue_reads"] >= 1 and stats["map_cache_hit"] == 0
    assert stats["map_reads_capped"] == 0
    stats2 = _run(ms.tick_once(p, v, now_ts=5010.0))
    assert stats2["mapped_by"]["yesno"] == 1 and stats2["map_cache_hit"] == 1
    assert stats2["map_venue_reads"] == 0
    # the budget spent before the market is read: capped, not unmapped
    monkeypatch.setattr(ms, "MAP_READS_PER_TICK", 0)
    monkeypatch.setattr(ms, "_map_cache", {})
    stats3 = _run(ms.tick_once(p, v, now_ts=5020.0))
    assert stats3["map_reads_capped"] == 1 and stats3["unmapped"] == 0
    assert ms._unmapped_until == {}, "a capped market is never TTL-skipped"
    rows = [w for w in p.writes if "INSERT INTO mirror_shadow" in w[0]]
    assert rows and any("map reads capped" in str(a) for _, a in rows)
    ms._backoff_until = 0.0


def test_every_venue_read_goes_through_the_measurement_pacer(monkeypatch):
    """One measurement-pacer slot per resolver call, both tokens: the
    slots equal the reads the lane counted (a yes/no call's own two
    lookups ride one slot; the exact resolver is called one candidate
    at a time so each of its calls is one lookup)."""
    slept = _nosleep(monkeypatch)
    v = _Venue(_yesno_market(ATC_AME, Q_AME, "lmx-ame-san-2026-09-05"))
    out: dict = {}
    _map(monkeypatch, _ame_fills(), v, out=out)
    assert slept.count(ms.READ_PACING_S) == out["venue_reads"] >= 1
    assert "pace(READ_PACING_S)" in inspect.getsource(ms._venue_reader)


def test_a_venue_module_without_the_resolvers_is_a_named_absence(monkeypatch):
    """The shadow's quote-only fake (and any venue module that cannot
    resolve) is a lane that is not there: no read, no exception, NOT a
    refusal of his market -- it stays unmapped under premap's own
    explain, and the absence is named on the row's detail."""
    out: dict = {}
    p = _Pool(fills=HIS, mapped=False)
    assert _run(ms.map_market(p, HIS, _Pmus(), whale="rn1", condition_id=CID, out=out)) is None
    assert out["lane"] == "unavailable" and "venue_reads" not in out and "refusal" not in out
    row = _run(ms.shadow_market(_Pool(fills=HIS, mapped=False), _Pmus(), "rn1", CID, RATIO, {},
                                positions={}))
    assert row["detail"]["map_lane"] == "unavailable"
    assert row["detail"]["explain"] not in ("map_lane_unavailable", "unavailable")
    # and without a venue module at all the read is table-only, as before
    assert _run(ms.map_market(p, HIS)) is None


# ------------------------------------------ 3. the copy lane's fills

def test_his_fills_coalesces_the_outcome_from_the_token_catalogue():
    src = inspect.getsource(ms.his_fills)
    assert "COALESCE(t.outcome, mt.outcome) AS outcome" in src
    assert "LEFT JOIN market_tokens mt ON mt.token_id = t.asset" in src
    assert "LEFT JOIN markets m ON m.condition_id = t.condition_id" in src


# ---------------------------------------------- 4. the live worker

def test_a_live_book_opens_from_exact_and_yesno_as_from_premap_and_refuses_grammar(monkeypatch):
    """_mapping_admitted reads the mirror's source exactly as it reads a
    copy fill's mapping_src: 'exact' rides the resume lane with
    'premap'; 'yesno' is refused by name while the quarantine holds
    (the copy lane's yesno_exact, same day it lifts); 'grammar' never
    opens a book -- MIRROR_LIVE_MAP_SRC refuses it before admission."""
    class _P:
        async def fetchval(self, sql, *a):
            if a and a[0] == "premap_live":
                return json.dumps(True)
            if a and a[0] == "mapping_quarantine":
                return json.dumps(True)
            return None

    monkeypatch.delenv("LIVE_MAPPING_QUARANTINE", raising=False)
    monkeypatch.delenv("LIVE_PREMAP", raising=False)
    monkeypatch.setenv("LIVE_PREMAP_WHALES", "rn1")
    monkeypatch.setenv("LIVE_VERIFIED_WHALES", "")
    monkeypatch.setattr(le, "overrides_unreadable", lambda: False)
    monkeypatch.setattr(le, "_roster_override", None)
    p = _P()
    for src in ("premap", "exact"):
        ok, why = _run(le._mapping_admitted(p, "rn1", src, SLUG))
        assert (ok, why) == (True, None), src
    ok, why = _run(le._mapping_admitted(p, "rn1", "yesno", SLUG))
    assert ok is False and why.startswith("quarantined:") and "(src=yesno," in why
    ok, why = _run(le._mapping_admitted(p, "rn1", "grammar", SLUG))
    assert ok is False and "(src=grammar," in why
    # the quarantine lifted: yesno is admitted like every class; grammar
    # is still refused by the live worker itself
    monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "off")
    assert _run(le._mapping_admitted(p, "rn1", "yesno", SLUG)) == (True, None)
    assert ml.MIRROR_LIVE_MAP_SRC == frozenset({"ledger", "premap", "exact", "yesno"})
    assert "grammar" not in ml.MIRROR_LIVE_MAP_SRC, "grammar opens only through its certification"
    src = inspect.getsource(ml._tick_candidate)
    assert 'not in MIRROR_LIVE_MAP_SRC' in src and '_mirror_stop("map_source_unverified", w)' in src
    assert src.index("map_source_unverified") < src.index("_admit_source(")
    assert "le._mapping_admitted(" not in src, "every source goes through _admit_source"
    assert src.index("_grammar_admission(") < src.index("_admit_source(")
    assert 'if mo.get("refusal") == "map_reads_capped":' in src
    i = src.index('_mirror_stop("map_reads_capped", w)')
    assert "_unmapped_until" not in src[i:i + 80], "a capped candidate is never TTL-skipped"
    assert "t.map_budget" in src
    for k in ("map_reads_capped", "map_source_unverified", "map_venue_read", "map_cache_hit"):
        assert k in ml.CENSUS_KEYS


def test_the_live_candidate_refuses_a_grammar_source_and_caps_by_name(monkeypatch):
    """Driven: a grammar-sourced map opens no book and is counted; a
    capped map is counted and not TTL-skipped."""
    from tests.test_mirror_live_worker import _pool

    seen = []

    async def _grammar(pool, fills, pmus=None, **kw):
        seen.append(kw)
        return {"us_slug": AEC_CFB, "long_asset": M, "other_asset": N, "source": "grammar"}

    monkeypatch.setattr(ms, "map_market", _grammar)
    ml._current_stats = ml._new_stats()
    t = ml._Tick(pool=_pool(), pmus=_Venue(), http=None, now=5000.0, stats=ml._current_stats)
    _run(ml._tick_candidate(t, "rn1", CID))
    # a grammar map with no side facts cannot be certified: refused by the
    # certification's own name, never map_source_unverified (that is for a
    # class the worker does not know at all)
    assert ml._current_stats["census"]["grammar_echo_unverified"] == 1
    assert ml._current_stats["census"]["map_source_unverified"] == 0
    assert ("rn1", CID) in ml._unmapped_until
    assert seen and seen[0]["budget"] is t.map_budget and seen[0]["whale"] == "rn1"

    async def _unknown(pool, fills, pmus=None, **kw):
        return {"us_slug": AEC_CFB, "long_asset": M, "other_asset": N, "source": "fuzzy"}

    monkeypatch.setattr(ms, "map_market", _unknown)
    monkeypatch.setattr(ml, "_unmapped_until", {})
    ml._current_stats = ml._new_stats()
    t = ml._Tick(pool=_pool(), pmus=_Venue(), http=None, now=5000.0, stats=ml._current_stats)
    _run(ml._tick_candidate(t, "rn1", CID))
    assert ml._current_stats["census"]["map_source_unverified"] == 1

    async def _capped(pool, fills, pmus=None, **kw):
        kw["out"]["refusal"] = "map_reads_capped"
        return None

    monkeypatch.setattr(ms, "map_market", _capped)
    monkeypatch.setattr(ml, "_unmapped_until", {})
    ml._current_stats = ml._new_stats()
    t = ml._Tick(pool=_pool(), pmus=_Venue(), http=None, now=5000.0, stats=ml._current_stats)
    _run(ml._tick_candidate(t, "rn1", CID))
    assert ml._current_stats["census"]["map_reads_capped"] == 1
    assert ml._unmapped_until == {}
    ml._current_stats = None


# --------------------------------- 5. the two lanes cannot drift

class _Recorder:
    """Fake resolvers that record every call the way the venue would
    see it: (resolver, candidate slug or his slug, outcome)."""

    def __init__(self):
        self.calls = []

    def exact(self, cands, outcome, diag_out=None):
        for c in cands:
            self.calls.append(("exact", c, outcome))
            if diag_out is not None:
                diag_out.append("404")
        return None

    def derivative(self, slug, outcome, title=None):
        self.calls.append(("derivative", slug, outcome))
        return None

    def yesno(self, slug, outcome, title=None, event=None, diag_out=None):
        self.calls.append(("yesno", slug, outcome))
        return None

    def fuzzy(self, *a, **k):
        self.calls.append(("fuzzy",))
        return None


def _copy_lane_calls(monkeypatch, ctx):
    """Drive live_executor's copy block with the recorder and return the
    resolver calls it made, in order."""
    from tests.test_live_executor_mapping import _MapPool, _payload, _wire

    pool = _MapPool()
    _wire(monkeypatch, pool)
    monkeypatch.setenv("KALSHI_FIRST_PCT", "0")
    rec = _Recorder()

    async def fake_ctx(_pool, _payload):
        return dict(ctx)

    async def _no_premap(*a, **k):
        return None

    monkeypatch.setattr(premap, "resolve", _no_premap)
    monkeypatch.setattr(le, "_market_context", fake_ctx)
    monkeypatch.setattr(pmus, "resolve_market_exact", rec.exact)
    monkeypatch.setattr(pmus, "resolve_derivative_exact", rec.derivative)
    monkeypatch.setattr(pmus, "resolve_team_yesno_exact", rec.yesno)
    monkeypatch.setattr(pmus, "resolve_market", rec.fuzzy)
    asyncio.run(le.maybe_execute(_payload(outcome=ctx["outcome"]), 5.0))
    return rec.calls


def _shared_lane_calls(ctx):
    rec = _Recorder()

    class _V:
        resolve_market_exact = staticmethod(rec.exact)
        resolve_derivative_exact = staticmethod(rec.derivative)
        resolve_team_yesno_exact = staticmethod(rec.yesno)

    class _NoDh:
        async def fetchval(self, *a):
            return None

    async def _read(fn, *args):
        return fn(*args)

    diag: list = []
    mapping, source, refusal = asyncio.run(
        map_lane.exact_lane(_NoDh(), _V(), ctx, _read, diag=diag, grammar=False))
    assert mapping is None and source is None and refusal is None
    return rec.calls


_TODAY = date.today().isoformat()


@pytest.mark.parametrize("ctx", [
    {"market_slug": f"atp-nakashi-michels-{_TODAY}", "event_slug": None,
     "market_title": "US Open ATP: Brandon Nakashima vs Alex Michelsen", "event_title": None,
     "outcome": "Alex Michelsen"},
    {"market_slug": f"cfb-bayl-aubrn-{_TODAY}", "event_slug": f"cfb-bayl-aubrn-{_TODAY}",
     "market_title": "Baylor vs. Auburn", "event_title": "Baylor vs. Auburn", "outcome": "Baylor"},
    {"market_slug": f"bun-wer-lei-{_TODAY}-lei", "event_slug": f"bun-wer-lei-{_TODAY}",
     "market_title": f"Will RB Leipzig win on {_TODAY}?", "event_title": EV_LEI, "outcome": "Yes"},
    {"market_slug": f"epl-ars-che-{_TODAY}-total-2pt5", "event_slug": None,
     "market_title": "Arsenal vs. Chelsea: O/U 2.5", "event_title": None, "outcome": "Over"},
    {"market_slug": f"epl-ars-che-{_TODAY}-ars-1pt5", "event_slug": None,
     "market_title": "Arsenal spread", "event_title": None, "outcome": "Arsenal"},
])
def test_the_copy_lane_and_the_shared_lane_make_the_same_resolver_calls(monkeypatch, ctx):
    """The drift pin: the copy lane's inline block (pinned byte for byte
    by its own tests) and map_lane.exact_lane, driven with the same
    recording resolvers on the same context, make the same resolver
    calls in the same order on the same candidates -- and the copy lane
    makes exactly one more, the fuzzy step the mirror never runs."""
    copy = _copy_lane_calls(monkeypatch, ctx)
    shared = _shared_lane_calls(ctx)
    assert copy and copy[-1] == ("fuzzy",), copy
    assert copy[:-1] == shared, (copy, shared)
    assert ("fuzzy",) not in shared


def test_the_copy_lanes_block_is_byte_identical_to_head():
    """The source pins the copy lane's own tests hold are all still in
    place: the factoring did not move the money path."""
    src = inspect.getsource(le.maybe_execute)
    assert src.count('_ex_diag.append("timeout")') == 3
    assert "[src_slug], ctx.get(\"outcome\")" in src
    assert 'mapping_src = "yesno_exact"' in src and 'mapping_src = "fuzzy"' in src
    assert "map_lane" not in src, "the copy lane keeps its own block; the drift test holds them together"
    lane = inspect.getsource(map_lane)
    assert "resolve_market(" not in lane and "resolve_market," not in lane, "never fuzzy"


def test_the_lane_never_touches_an_order():
    src = inspect.getsource(map_lane)
    for banned in ("submit_fok", "cancel_order", "close_position", "execute_manual",
                   "maybe_execute", "mirror_exit", "position_side("):
        assert banned not in src, banned


def test_the_cache_clock_and_ttl():
    """MAP_CACHE_TTL_S is the unmapped TTL's own 900 s, and the cache
    reads the module's time.time (so a test can pin it)."""
    assert ms.MAP_CACHE_TTL_S == 900.0 == ms.UNMAPPED_TTL_S
    assert "now_ts = time.time()" in inspect.getsource(ms._map_exact)
    assert isinstance(time.time(), float)
