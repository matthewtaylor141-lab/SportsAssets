"""RC6.2 LANE p-coverage: THE PLANE READS A NEVER-VALUED SOCCER / BASEBALL
MONEY LINE UNDER ITS EVENT'S OWN FIXTURE SCOPE WHEN THE VENUE ROW IS HELD.

market_plane.settlement.terms_comparison called compare_prose with no
context, phase or format, and never read the fixture scope the decision
pipeline persists (venue_fixture_metadata, ('PMUS', 'event:<event slug>'),
migration 183), so every never-valued soccer and baseball full-game winner
read BOOK_TERMS_SCOPE_NOT_ESTABLISHED (production: 2,866 rows) -- including
the contracts of an event whose organiser row IS held (36 UNL rows,
research-sql run 37945671144 F2). With the row the terms are read in BOTH
quote contexts (a never-valued contract has no quote to place) and recorded
only when the two readings agree; otherwise QUOTE_CONTEXT_DECIDES_THE_TERMS.
Never PROVEN here, and ENFORCED (rework): the scoped reading is capped at
MAPPED_BUT_SETTLEMENT_NOT_PROVEN -- both contexts COMPATIBLE reads
FIXTURE_SCOPED_READING_IS_NOT_A_PROOF:COMPATIBLE_IN_BOTH_CONTEXTS (no capture
held today reads COMPATIBLE in both contexts; a simulated future capture is
tested below). A proof under a scope comes only from a decision that attested
its own quote's context; a priced difference needs a valuation.

Texts: the venue's own (tests/fixtures/pmus_market_types_and_segment_rules_
2026_10_09.json, research-sql run 37939739782 T1): a UEFA Conference League
money line listed for 2026-10-15, a Saudi Pro League money line, an NPB
money line and an NHL money line.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import time

import asyncpg
import pytest

from sportsassets import bettor_settlement_terms as ST
from sportsassets.market_plane import populate as POP
from sportsassets.market_plane import settlement as S

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
ROWS = {r["contract_id"]: r for r in json.loads(
    (FIX / "pmus_market_types_and_segment_rules_2026_10_09.json")
    .read_text())["rules_rows"]}
UECL = ROWS["atc-uecl-ata-paf-2026-10-15-paf"]
SPL = ROWS["atc-spl-fay-riy-2026-10-11-draw"]
NPB = ROWS["aec-npb-ygo-ybo-2026-10-10"]
NHL = ROWS["aec-nhl-van-nyr-2026-10-11"]
LEAGUE = {"phase": ST.PHASE_LEAGUE, "game_format": ST.FMT_NINETY,
          "venue_fixture_key": "event:x", "source": "UEFA match API"}
KNOCKOUT = {"phase": ST.PHASE_KNOCKOUT, "game_format": ST.FMT_KNOCKOUT}
NOW = time.time()


def _c(r):
    return POP.contract_row({"market_slug": r["contract_id"],
                             "event_slug": r["event_id"],
                             "sports_type": r["market_type"],
                             "listing_state": "PREGAME", "updated_at": NOW,
                             "sides": []}, now=NOW)


def _rules(r):
    return {"rules_sha256": r["rules_sha256"], "rules_text": r["rules_text"],
            "rules_published": True, "venue": "POLYMARKET_US",
            "parse_status": "ESTABLISHED", "evidence": {}}


def _state(r, scope, derivative=True):
    return S.state_for(_c(r), rules=_rules(r), rules_looked_up=True,
                       derivative_terms=derivative, fixture_scope=scope)


@pytest.mark.parametrize("r", (UECL, SPL), ids=("uecl", "spl"))
def test_a_held_league_scope_reads_the_stated_conflict_in_both_contexts(r):
    before = _state(r, None)
    # production's 732cc0c6 label, as RC6.1 already renames it
    assert r["production_settlement_why"] == \
        before["why"].replace(S.R_BOOK_TERMS_SCOPE,
                              S.R_BOOKMAKER_TERMS_NOT_HELD, 1)
    assert before["why"].startswith(S.R_BOOK_TERMS_SCOPE)
    st = _state(r, LEAGUE)
    assert st["state"] == S.NOT_PROVEN and not st["proven"]
    assert st["why"] == ("%s:%s:SETTLEMENT_DIFFERENCE_ORDINARY_COMPLETION_"
                         "NOT_ESTABLISHED" % (S.R_INCOMPATIBLE_NOT_PRICEABLE,
                                              ST.C_NOT_PLAYED))
    t = st["evidence"]["terms"]
    assert t["scope"]["contexts"] == "BOTH_AGREE"
    assert t["mismatched_conditions"] == [ST.C_NOT_PLAYED]
    assert st["evidence"]["fixture_scope"]["phase"] == ST.PHASE_LEAGUE


def test_a_knockout_scope_is_named_outside_the_capture():
    st = _state(UECL, KNOCKOUT)
    assert st["why"] == ("%s:%s,%s" % (S.R_BOOK_TERMS_SCOPE,
                                       ST.R_PHASE_EXCLUDED,
                                       ST.R_FORMAT_EXCLUDED))


def test_contexts_that_read_the_terms_differently_establish_nothing():
    """Baseball: the pre-game and in-play rules disagree on a called game,
    and a never-valued contract has no quote to choose between them."""
    st = _state(NPB, {"phase": ST.PHASE_REGULAR,
                      "game_format": ST.FMT_NINE})
    assert st["why"] == "%s:%s" % (S.R_BOOK_TERMS_SCOPE, S.R_CONTEXT_DECIDES)
    assert st["evidence"]["terms"]["scope"]["contexts"] == "DIFFER"
    assert not st["proven"]


def test_the_scope_is_read_only_where_it_scopes_and_only_in_rc6_mode():
    # a phase-independent family ignores it
    assert _state(NHL, LEAGUE)["why"] == _state(NHL, None)["why"]
    # RC5 mode ignores it
    assert _state(UECL, LEAGUE, derivative=False)["why"] == \
        _state(UECL, None, derivative=False)["why"]
    # half a scope is no scope
    assert _state(UECL, {"phase": ST.PHASE_LEAGUE})["why"] == \
        _state(UECL, None)["why"]
    # the unscoped comparison keeps its RC4 / RC6 cache key
    assert S.terms_key("sha", "soccer", "uecl") == ("sha", "soccer", "uecl")
    assert len(S.terms_key("sha", "soccer", "uecl", LEAGUE)) == 5


async def _seed(c, r):
    for intent, side in (("ORDER_INTENT_BUY_LONG", "a"),
                         ("ORDER_INTENT_BUY_SHORT", "b")):
        await c.execute(
            "INSERT INTO us_premap (identifier, event_slug, market_slug, kind,"
            " side_norm, intent, sports_type, listing_state, "
            " listing_state_source, updated_at, game_start, team_id, "
            " team_league) VALUES ($1,$2,$3,'side',$4,$5,$6,'PREGAME',"
            " 'VENUE_LIVE_FLAG', now(), now() + interval '2 hours', $7,"
            " 'uecl')", r["contract_id"] + side, r["event_id"],
            r["contract_id"], side, intent, r["market_type"],
            11 if side == "a" else 12)
    from sportsassets.market_plane import rules as RULES
    await RULES.upsert(c, [RULES.pmus_row({
        "slug": r["contract_id"], "description": r["rules_text"],
        "sportsMarketType": r["market_type"]})])


@pg
def test_the_coverage_pass_reads_the_events_fixture_row():
    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            await c.execute("DELETE FROM us_premap")
            await _seed(c, UECL)
            await POP.populate(c, since=0.0, now=time.time(), full=True)
            S._TERMS_CACHE.clear()
            cov = await POP.coverage_pass(c, now=time.time(),
                                          derivative_terms=True)
            why = await c.fetchval(
                "SELECT settlement_why FROM market_plane_registry "
                " WHERE contract_id=$1", UECL["contract_id"])
            assert why.startswith(S.R_BOOK_TERMS_SCOPE), why
            await c.execute(
                "INSERT INTO venue_fixture_metadata (venue, "
                " venue_fixture_key, sport_family, competition, phase, "
                " game_format, play_has_begun, source, source_url, "
                " retrieved_at, reader_version) VALUES ('PMUS', $1, "
                " 'soccer', 'UECL', $2, $3, false, 'UEFA match API "
                " (match.uefa.com/v5/matches)', 'u', now(), "
                " 'BETTOR_SOCCER_FIXTURE_V1')",
                "event:" + UECL["event_id"], ST.PHASE_LEAGUE, ST.FMT_NINETY)
            cov = await POP.coverage_pass(c, now=time.time(),
                                          derivative_terms=True)
            row = await c.fetchrow(
                "SELECT settlement_state, settlement_why, settlement_evidence"
                "  FROM market_plane_registry WHERE contract_id=$1",
                UECL["contract_id"])
            assert row["settlement_state"] == S.NOT_PROVEN
            assert row["settlement_why"].startswith(
                "%s:%s:" % (S.R_INCOMPATIBLE_NOT_PRICEABLE, ST.C_NOT_PLAYED))
            ev = json.loads(row["settlement_evidence"])
            assert ev["fixture_scope"]["venue_fixture_key"] == \
                "event:" + UECL["event_id"]
            assert cov["by_state"]["PRICEABLE"] == 0
        finally:
            await tr.rollback()
            S._TERMS_CACHE.clear()
            await c.close()
    asyncio.run(go())


# ═════════════════════════════════════════════════════════════════════
# (RC6.2, p-coverage rework) THE FIXTURE-SCOPED READING IS NEVER A PROOF
# ═════════════════════════════════════════════════════════════════════
#
# Review finding: "never PROVEN without a valuation" was stated (the
# capital-critical manifest, this file's docstring, commit 70244ae1) but not
# enforced -- with a scope held and both contexts reading COMPATIBLE,
# state_for returned SETTLEMENT_PROVEN_COMPATIBLE (BASIS_RULES_TERMS) for a
# never-valued soccer / baseball money line. Unreachable only because no
# capture yet reads COMPATIBLE in both contexts; a future capture would
# have made it a proof path silently. The reading is now capped.

def _compatible_everywhere(monkeypatch):
    """A FUTURE CAPTURE, simulated: the book's terms state every condition
    the venue states, the same way, in every context. The comparison
    machinery (both contexts, the cache) is the real one."""
    def fake(*, sport_family, market, venue_prose, extra_book_terms=None,
             context=None, phase=None, game_format=None, **kw):
        return {"verdict": "COMPATIBLE", "book_terms_held": True,
                "per_condition": {ST.C_NOT_PLAYED: {
                    "verdict": "MATCH", "book_payout": "VOID",
                    "venue_payout": "VOID"}},
                "mismatched_conditions": [], "unstated_conditions": [],
                "venue_self_contradictory": [],
                "book_side_absent_refusals": [], "refusal": None}
    monkeypatch.setattr(ST, "compare_prose", fake)
    S._TERMS_CACHE.clear()


@pytest.mark.parametrize("r,scope", (
    (UECL, LEAGUE), (SPL, LEAGUE),
    (NPB, {"phase": ST.PHASE_REGULAR, "game_format": ST.FMT_NINE})),
    ids=("uecl", "spl", "npb"))
def test_both_contexts_compatible_under_a_scope_is_named_never_proven(
        monkeypatch, r, scope):
    _compatible_everywhere(monkeypatch)
    try:
        st = _state(r, scope)
        assert st["state"] == S.NOT_PROVEN and not st["proven"]
        assert st["state"] not in S.PROVEN_STATES
        assert st["why"] == ("%s:COMPATIBLE_IN_BOTH_CONTEXTS"
                             % S.R_SCOPED_NOT_A_PROOF)
        assert st["evidence"]["terms"]["scope"]["contexts"] == "BOTH_AGREE"
        assert st["evidence"]["fixture_scope"]["phase"] == scope["phase"]
    finally:
        S._TERMS_CACHE.clear()


def test_the_cap_touches_only_the_scoped_reading(monkeypatch):
    """The same simulated capture: an unscoped, phase-independent money line
    (NHL) reads exactly as RC6 did, and a DECISION that attested its own
    quote's context stays the authority for a scoped one."""
    _compatible_everywhere(monkeypatch)
    try:
        nhl = _state(NHL, LEAGUE)
        assert nhl["state"] == S.COMPATIBLE and \
            nhl["basis"] == S.BASIS_RULES_TERMS
        assert nhl == _state(NHL, None)
        dec = S.state_for(_c(UECL), rules=_rules(UECL), rules_looked_up=True,
                          derivative_terms=True, fixture_scope=LEAGUE,
                          valuation={"settlement_verdict": "COMPATIBLE",
                                     "refusals": [],
                                     "decision_rules_fingerprint":
                                         UECL["rules_sha256"]})
        assert (dec["state"], dec["basis"]) == (S.COMPATIBLE,
                                                S.BASIS_DECISION_ATTEST)
    finally:
        S._TERMS_CACHE.clear()


#: every phase / format the readers record, for the real captures below
SOCCER_SCOPES = [{"phase": p, "game_format": f}
                 for p in (ST.PHASE_LEAGUE, ST.PHASE_KNOCKOUT)
                 for f in (ST.FMT_NINETY, ST.FMT_KNOCKOUT)]
BASEBALL_SCOPES = [{"phase": p, "game_format": f}
                   for p in (ST.PHASE_REGULAR, ST.PHASE_PLAYOFF)
                   for f in (ST.FMT_NINE, ST.FMT_SEVEN)]


def test_with_todays_captures_no_scoped_reading_reads_compatible():
    """The true statement the manifest now makes: PROVEN under a scope would
    need both contexts COMPATIBLE, and no capture held today allows it --
    every production soccer / baseball money-line text, every phase and
    format, is something other than COMPATIBLE in both contexts."""
    S._TERMS_CACHE.clear()
    seen = 0
    for r in ROWS.values():
        c = _c(r)
        fam = S.h2h_family(c)
        if fam not in S.SCOPED_H2H_FAMILIES:
            continue
        for scope in (SOCCER_SCOPES if fam == "soccer" else BASEBALL_SCOPES):
            st = _state(r, scope)
            seen += 1
            assert st["state"] not in S.PROVEN_STATES, (r["contract_id"],
                                                         scope)
            assert not st["why"].startswith(S.R_SCOPED_NOT_A_PROOF), (
                r["contract_id"], scope)
    assert seen >= 8
    S._TERMS_CACHE.clear()
