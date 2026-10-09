"""RC6.2 LANE p-coverage: A VALUED SOCCER MONEY LINE WHOSE SCOPE CAN BE
ESTABLISHED IS NOT LEFT UNKNOWN FOR WANT OF EVIDENCE THE LANE ALREADY HOLDS.

Production (research-sql runs 37941166989, 37946119133, 37945671144,
2026-10-09): every one of 264 valued soccer money lines in 24 h read
settlement verdict UNKNOWN. Two software defects, before the genuinely
missing evidence (domestic leagues have no organiser source at all):

  1  THE SOURCE WAS LOOKED UP BY THE ODDS PROVIDER'S SPORT KEY. PinnAPI
     quotes carry the generic 'pinnapi_soccer', which no declared source can
     match -- so even a Nations League fixture priced from PinnAPI was
     refused NO_AUTHORITATIVE_FIXTURE_SOURCE_FOR_COMPETITION:pinnapi_soccer.
     The venue's own event slug names the competition (unl-wal-nor-...).
  2  THE QUOTE'S PROVED PRE-MATCH / IN-PLAY LABEL WAS DROPPED. Of 32 UNL
     money lines whose scope WAS established (LEAGUE_OR_GROUP_STAGE,
     SCHEDULED_NINETY_MINUTES), 27 read UNKNOWN because the organiser
     publishes no observed start; 24 of them were priced from a PinnAPI quote
     whose stream (live / prematch) its reader had proved, and which the
     terms module already names the AUTHORITATIVE context.

The venue text is the recorded Nations League text (admin probe, the same
the settlement-scope tests use); the organiser payload is the UEFA match
API's own (tests/fixtures/uefa_unl_matches_2026_10_04.json). Gates are
unchanged: with the context established the verdict is the stated payout
conflict (INCOMPATIBLE on the postponed match) the priced settlement
difference policy then decides on, never COMPATIBLE by default.
"""
from __future__ import annotations

import copy
import json
import pathlib
import time

import pytest

from sportsassets import bettor_settlement_terms as ST
from sportsassets import bettor_soccer_fixture as SF
from sportsassets import bettor_venue_settlement as V
from sportsassets.workers import ext_pinnacle_loop as X

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
UEFA = json.loads((FIX / "uefa_unl_matches_2026_10_04.json").read_text())
UNL = SF.SOURCES["soccer_uefa_nations_league"]

#: The venue's recorded Nations League text (admin probe, 11:53:00Z).
GRE_GER = (
    "This market will settle to the winner at the end of 90 minutes plus "
    "stoppage time in the Greece vs Germany UEFA Nations League match "
    "scheduled for 2026-10-04 2:45PM ET. If the match is tied following 90 "
    "minutes plus stoppage time, the market will settle to Tie. If the match "
    "is delayed, postponed, or suspended and not rescheduled to a date within "
    "two weeks of the originally scheduled date, the market will settle to "
    "the last fair market price. Outcome sourced from UEFA.")


# ═════════════════════════════════════════════════════════════════════
# 1. THE ORGANISER SOURCE FROM THE VENUE'S OWN LEAGUE CODE
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("slug", ("unl-wal-nor-2026-10-01",
                                  "atc-unl-por-nor-2026-10-04"))
def test_the_generic_provider_key_resolves_through_the_venue_league(slug):
    r = SF.resolve_source("pinnapi_soccer", slug)
    assert r["source"] is UNL and r["basis"] == "VENUE_LEAGUE_CODE"
    assert r["venue_league"] == "unl" and r["refusal"] is None
    # the provider key alone still works where it names the competition
    k = SF.resolve_source("soccer_uefa_nations_league", None)
    assert k["source"] is UNL and k["basis"] == "PROVIDER_SPORT_KEY"


def test_a_domestic_league_has_no_source_and_the_refusal_names_it():
    """research-sql 37946119133 G1: epl, bun, sea, lal ... carried the
    uninformative key; the refusal now names the venue's competition."""
    for slug, code in (("epl-sun-bha-2026-10-10", "epl"),
                       ("bun-unb-elv-2026-10-10", "bun"),
                       ("uwwcq-tur-slo-2026-10-09", "uwwcq")):
        r = SF.resolve_source("pinnapi_soccer", slug)
        assert r["source"] is None and r["refusal"] == SF.R_NO_SOURCE
        assert r["refusal_subject"] == code
    # a specific provider key is still what a refusal names (unchanged)
    b = SF.resolve_source("soccer_brazil_serie_b", "atc-brb-bot-vln-2026-10-03")
    assert b["refusal_subject"] == "soccer_brazil_serie_b"


def test_provider_and_venue_naming_different_competitions_is_refused():
    r = SF.resolve_source("soccer_uefa_nations_league",
                          "ucl-vil-nap-2026-10-13")
    assert r["source"] is None and r["refusal"] == SF.R_SOURCES_DISAGREE


def test_an_unverified_declaration_can_never_yield_another_competitions_match():
    """UCL / UEL / UECL are declared, not yet read back: the parser reads a
    match only when the payload's OWN competition id and code are the
    declared ones. The UEFA Nations League payload read as UCL yields no
    evidence at all."""
    ucl = SF.VENUE_LEAGUE_SOURCES["ucl"]
    assert ucl["declared_unverified"] is True
    ev = SF.parse(UEFA["matches"], home="Portugal", away="Norway",
                  date_str="2026-10-04", retrieved_at="x", url="u", src=ucl)
    assert not ev["ok"] and ev["refusals"] == [SF.R_COMPETITION]
    assert "phase" not in ev and ev["foreign"] == len(UEFA["matches"])
    # and a match whose payload states no competition is never read either
    bare = copy.deepcopy(UEFA["matches"])
    for m in bare:
        m.pop("competition", None)
        m["matchday"].pop("competitionId", None)
    nb = SF.parse(bare, home="Portugal", away="Norway",
                  date_str="2026-10-04", retrieved_at="x", url="u", src=UNL)
    assert not nb["ok"] and nb["refusals"] == [SF.R_COMPETITION]
    # the declared competition's own payload is read exactly as before
    ok = SF.parse(UEFA["matches"], home="Portugal", away="Norway",
                  date_str="2026-10-04", retrieved_at="x", url="u", src=UNL)
    assert ok["ok"] and ok["phase"] == ST.PHASE_LEAGUE
    assert ok["game_format"] == ST.FMT_NINETY


def _fetcher(calls, payload=None):
    def fetch(src, date_str):
        calls.append((src["competition_id"], date_str))
        return {"ok": True, "url": SF.schedule_url(src, date_str),
                "payload": payload if payload is not None else UEFA["matches"],
                "retrieved_at": "2026-10-04T12:00:00+00:00"}
    return fetch


@pg
async def test_a_pinnapi_priced_nations_league_fixture_gets_its_scope():
    """THE FAILING CASE ON b3f1b0cd: the same fixture, keyed by the venue's
    own event, priced from PinnAPI (generic key) -- refused
    NO_AUTHORITATIVE_FIXTURE_SOURCE_FOR_COMPETITION:pinnapi_soccer before."""
    conn = await H.connect()
    slug = "unl-por-nor-2026-10-04"
    key = SF.venue_fixture_key(slug)
    try:
        await conn.execute("DELETE FROM venue_fixture_metadata WHERE "
                           " venue_fixture_key = $1", key)
        calls = []
        got = await X.acquire_fixture_scope(
            conn, condition_id=None, home="Portugal", away="Norway",
            commence_iso="2026-10-04T18:45:00Z", now=time.time(), cache={},
            sport_family="soccer", sport_key="pinnapi_soccer",
            venue_event_slug=slug, venue_fetcher=_fetcher(calls))
        assert got["read"] is True, got.get("acquisition")
        assert got["phase"] == ST.PHASE_LEAGUE
        assert got["game_format"] == ST.FMT_NINETY
        assert got["acquisition"]["source_basis"] == "VENUE_LEAGUE_CODE"
        assert got["acquisition"]["venue_league"] == "unl"
        assert calls == [("2014", "2026-10-04")]
    finally:
        await conn.execute("DELETE FROM venue_fixture_metadata WHERE "
                           " venue_fixture_key = $1", key)
        await conn.close()


@pg
async def test_a_misdeclared_competition_writes_no_row_and_says_so():
    conn = await H.connect()
    slug = "ucl-por-nor-2026-10-04"
    key = SF.venue_fixture_key(slug)
    try:
        await conn.execute("DELETE FROM venue_fixture_metadata WHERE "
                           " venue_fixture_key = $1", key)
        calls = []
        got = await X.acquire_fixture_scope(
            conn, condition_id=None, home="Portugal", away="Norway",
            commence_iso="2026-10-04T18:45:00Z", now=time.time(), cache={},
            sport_family="soccer", sport_key="pinnapi_soccer",
            venue_event_slug=slug, venue_fetcher=_fetcher(calls))
        assert got["read"] is False
        assert got["acquisition"]["refusal"] == SF.R_COMPETITION
        assert [c[0] for c in calls] == ["1", "1", "1"]
        assert await conn.fetchval(
            "SELECT count(*) FROM venue_fixture_metadata WHERE "
            " venue_fixture_key = $1", key) == 0
        # a domestic league: refused by the venue's own competition code
        d = await X.acquire_fixture_scope(
            conn, condition_id=None, home="Sunderland", away="Brighton",
            commence_iso="2026-10-10T14:00:00Z", now=time.time(), cache={},
            sport_family="soccer", sport_key="pinnapi_soccer",
            venue_event_slug="epl-sun-bha-2026-10-10")
        assert d["acquisition"]["refusal"] == \
            "NO_AUTHORITATIVE_FIXTURE_SOURCE_FOR_COMPETITION:epl"
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2. THE QUOTE'S PROVED LABEL IS CONTEXT EVIDENCE; DISAGREEMENT IS NOT
# ═════════════════════════════════════════════════════════════════════

def _quote(stream, provider=X.PINNAPI_WS_PROVIDER):
    return {"reference_input": {"provider": provider, "stream": stream}}


def test_only_a_pinnapi_quote_with_a_proved_stream_carries_a_label():
    assert X.provider_stream_label(_quote("live")) is True
    assert X.provider_stream_label(_quote("prematch")) is False
    assert X.provider_stream_label(_quote("other")) is None
    assert X.provider_stream_label(_quote("live", "the-odds-api.com/v4")) \
        is None
    assert X.provider_stream_label({}) is None


FX_PRE = {"context": ST.CTX_PRE_GAME, "why": "reported not started"}
FX_LIVE = {"context": ST.CTX_LIVE, "why": "after an actual start"}
FX_NONE = {"context": None, "why": "no start stamp and no provider label"}


@pytest.mark.parametrize("fx,label,ctx,basis,refusal", (
    (FX_NONE, False, ST.CTX_PRE_GAME, "PROVIDER_QUOTE_LABEL", None),
    (FX_NONE, True, ST.CTX_LIVE, "PROVIDER_QUOTE_LABEL", None),
    (None, True, ST.CTX_LIVE, "PROVIDER_QUOTE_LABEL", None),
    (FX_PRE, False, ST.CTX_PRE_GAME,
     "FIXTURE_EVENT_STATE_AND_PROVIDER_LABEL", None),
    (FX_LIVE, True, ST.CTX_LIVE, "FIXTURE_EVENT_STATE_AND_PROVIDER_LABEL",
     None),
    (FX_PRE, None, ST.CTX_PRE_GAME, "FIXTURE_EVENT_STATE", None),
    (FX_PRE, True, None, None, X.R_CONTEXT_EVIDENCE_DISAGREES),
    (FX_LIVE, False, None, None, X.R_CONTEXT_EVIDENCE_DISAGREES),
    (FX_NONE, None, None, None, None),
))
def test_the_context_is_reconciled_never_preferred(fx, label, ctx, basis,
                                                   refusal):
    r = X.reconcile_quote_context(fx, label)
    assert (r["context"], r["basis"], r["refusal"]) == (ctx, basis, refusal)
    if r["basis"] == "PROVIDER_QUOTE_LABEL":
        assert r["book_context"] is None
        assert r["quote_is_in_play"] is bool(label)
    elif ctx is not None:
        assert r["book_context"] == ctx and r["quote_is_in_play"] is None
    else:
        assert r["book_context"] is None and r["quote_is_in_play"] is None


def _attest_with(qctx):
    a = V.attest(sport_family="soccer",
                 venue_evidence={"rules_text": GRE_GER,
                                 "draw_contract_present": True},
                 book_evidence={"outcome_names": ["Home", "Away", "Draw"]},
                 book_context=qctx["book_context"],
                 quote_is_in_play=qctx["quote_is_in_play"],
                 phase=ST.PHASE_LEAGUE, game_format=ST.FMT_NINETY)
    a["fixture_metadata"] = {"read": True}
    return X._settlement_compatibility(a)


@pytest.mark.parametrize("label", (False, True))
def test_an_established_scope_with_a_labelled_quote_states_its_conflict(
        label):
    """Production's 24 UNL rows: organiser scope held, no observed start,
    a PinnAPI label. Before: UNKNOWN (SETTLEMENT_QUOTE_CONTEXT_NOT_
    ESTABLISHED). Now: the stated payout conflict, and ONLY that one."""
    before = _attest_with(X.reconcile_quote_context(FX_NONE, None))
    assert before["compatibility"] == ST.UNKNOWN
    assert any(b.startswith("SETTLEMENT_QUOTE_CONTEXT_NOT_ESTABLISHED")
               for b in before["blockers"])
    s = _attest_with(X.reconcile_quote_context(FX_NONE, label))
    assert s["compatibility"] == ST.INCOMPATIBLE
    assert s["mismatched_conditions"] == [ST.C_NOT_PLAYED]
    assert s["overall_established"] is False
    assert not any("QUOTE_CONTEXT" in b for b in s["blockers"])


def test_disagreeing_context_evidence_leaves_the_verdict_unknown():
    s = _attest_with(X.reconcile_quote_context(FX_PRE, True))
    assert s["compatibility"] == ST.UNKNOWN
    assert any(b.startswith("SETTLEMENT_QUOTE_CONTEXT_NOT_ESTABLISHED")
               for b in s["blockers"])


# ═════════════════════════════════════════════════════════════════════
# 3. A VENUE-NATIVE MLB GAME: THE LEAGUE SCHEDULE UNDER THE VENUE'S KEY
# ═════════════════════════════════════════════════════════════════════
#
# Production (research-sql runs 37941166989 M7, 37949739839 R5): every
# venue-native baseball money line valued in 24 h (mlb, kbo, npb) read
# UNKNOWN, refused FIXTURE_METADATA_HAS_NO_CONDITION_KEY before any schedule
# was read. The MLB game below is the league's own shape as the shape probe
# records it (tests/test_the_entry_lane_acquires_its_own_scope.GAME).

from sportsassets import bettor_fixture_metadata as FM  # noqa: E402

MLB_GAME = {
    "gamePk": 824950, "gameType": "R", "scheduledInnings": 9,
    "doubleHeader": "N", "gameNumber": 1,
    "officialDate": "2026-09-25",
    "gameDate": "2026-09-26T01:40:00Z",
    "status": {"detailedState": "Scheduled", "abstractGameState": "Preview"},
    "teams": {"away": {"team": {"name": "Houston Astros"}},
              "home": {"team": {"name": "Athletics"}}},
}


def _mlb_fetcher(calls, game):
    def fetch(date_str):
        calls.append(date_str)
        g = dict(game)
        return {"ok": True, "url": FM.SOURCE_URL % date_str,
                "payload": {"totalGames": 1,
                            "dates": [{"games": [g]}]
                            if date_str == g["officialDate"] else []}}
    return fetch


@pg
async def test_a_venue_native_mlb_game_gets_its_scope_under_its_own_key():
    conn = await H.connect()
    slug = "mlb-hou-ath-2026-09-25"
    key = SF.venue_fixture_key(slug)
    try:
        await conn.execute("DELETE FROM venue_fixture_metadata WHERE "
                           " venue_fixture_key = $1", key)
        before = await conn.fetchval("SELECT count(*) FROM fixture_metadata")
        calls = []
        got = await X.acquire_fixture_scope(
            conn, condition_id=None, home="Athletics", away="Houston Astros",
            commence_iso="2026-09-26T01:40:00Z", now=time.time(), cache={},
            sport_family="baseball", sport_key="pinnapi_baseball",
            venue_event_slug=slug, fetcher=_mlb_fetcher(calls, MLB_GAME))
        assert got["read"] is True, got.get("acquisition")
        assert got["phase"] == ST.PHASE_REGULAR
        assert got["game_format"] == ST.FMT_NINE
        assert got["play_has_begun"] is False
        assert got["acquisition"]["source_basis"] == "VENUE_LEAGUE_CODE"
        assert calls == ["2026-09-26", "2026-09-25"]
        row = await conn.fetchrow("SELECT sport_family, reader_version, "
                                  " source, source_match_id FROM "
                                  " venue_fixture_metadata WHERE venue='PMUS'"
                                  " AND venue_fixture_key=$1", key)
        assert (row["sport_family"], row["reader_version"], row["source"],
                row["source_match_id"]) == ("baseball", FM.VERSION, FM.SOURCE,
                                            "824950")
        # NO GLOBAL ID WAS INVENTED OR BORROWED
        assert await conn.fetchval(
            "SELECT count(*) FROM fixture_metadata") == before
        # the attest inputs: the playoff-free regular-season capture admits it
        assert ST.admit_scope(sport_family="baseball", phase=got["phase"],
                              game_format=got["game_format"])["ok"] is True

        # A GAME IN PROGRESS: the reported start is a NUMBER the context
        # rule reads (the venue store returns text; never a crash)
        live = dict(MLB_GAME, status={"detailedState": "In Progress",
                                      "abstractGameState": "Live"})
        await conn.execute("DELETE FROM venue_fixture_metadata WHERE "
                           " venue_fixture_key = $1", key)
        got2 = await X.acquire_fixture_scope(
            conn, condition_id=None, home="Athletics", away="Houston Astros",
            commence_iso="2026-09-26T01:40:00Z", now=time.time(), cache={},
            sport_family="baseball", venue_event_slug=slug,
            fetcher=_mlb_fetcher([], live))
        assert got2["play_has_begun"] is True
        assert isinstance(got2["actual_start_at"], float)
        ctx = X.fmeta_mod.context_for(
            {"play_has_begun": True,
             "actual_start_at": got2["actual_start_at"],
             "retrieved_at": got2["retrieved_at"]},
            observed_at=got2["actual_start_at"] + 60)
        assert ctx["context"] == ST.CTX_LIVE
    finally:
        await conn.execute("DELETE FROM venue_fixture_metadata WHERE "
                           " venue_fixture_key = $1", key)
        await conn.close()


@pg
async def test_a_venue_native_kbo_or_npb_game_is_refused_by_its_league():
    conn = await H.connect()
    try:
        n0 = await conn.fetchval("SELECT count(*) FROM venue_fixture_metadata")
        for slug, code in (("kbo-sls-sla-2026-10-09", "kbo"),
                           ("npb-ygo-ybo-2026-10-11", "npb")):
            got = await X.acquire_fixture_scope(
                conn, condition_id=None, home="A", away="B",
                commence_iso="2026-10-09T09:00:00Z", now=time.time(),
                cache={}, sport_family="baseball", venue_event_slug=slug,
                fetcher=_mlb_fetcher([], MLB_GAME))
            assert got["read"] is False
            assert got["acquisition"]["refusal"] == \
                "%s:%s" % (SF.R_NO_SOURCE, code)
        assert await conn.fetchval(
            "SELECT count(*) FROM venue_fixture_metadata") == n0
    finally:
        await conn.close()
