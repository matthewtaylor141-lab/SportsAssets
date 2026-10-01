"""THE SETTLEMENT PATH, SUPPORTED WHERE THE EVIDENCE SUPPORTS IT AND NAMED
WHERE IT DOES NOT (2026-10-01).

Codex found every PINNACLE_ONLY_PAPER_BENCHMARK decision refused as
SETTLEMENT_NOT_SUPPORTED, and two specific gaps behind it:

  1  MLB playoffs. Valuation 2059 (Phillies v Braves, NL Wild Card Game 3)
     had a fresh quote, established participants and a verified MLB fixture,
     but scope_phase PLAYOFF_OR_PLAY_IN while the capture admitted only the
     regular season. The official Pinnacle playoff rule is now CAPTURED
     (fetch-docs run 36857955284, page sha256 63d64321...) and answers a
     playoff fixture from its own table -- never from the regular-season one.
  2  Soccer. Valuation 2061 reported FIXTURE_METADATA_HAS_NO_CONDITION_KEY: a
     venue-native contract has no global condition id. It now has its own
     namespaced key ('PMUS', 'event:<slug>', migration 183) and the
     organiser's schedule (UEFA match API) as its source.

And one projection defect: the Phillies row read overall_established=True
beside compatibility=UNKNOWN, because the ABSENCE of a recognised refusal was
treated as establishment. Establishment is now positive evidence or nothing.

What the evidence then says, honestly: the venue settles a game "delayed,
postponed, or suspended and not rescheduled ... within two weeks" at "the last
fair market price"; Pinnacle returns the stake on a fixture never played.
That is a stated payout conflict, so these contracts are INCOMPATIBLE -- and
the record now says exactly that instead of SETTLEMENT_NOT_SUPPORTED.

The eligible case below is a SYNTHETIC venue text that states every
condition the way Pinnacle does (tests/paper_live_fixture.py); the refusing
cases use the venue's RECORDED text. Books are fresh synthetic observations
taken at the decision; no old valuation is executed.
"""
from __future__ import annotations

import json
import pathlib
import time

import pytest

from sportsassets import bettor_settlement_terms as ST
from sportsassets import bettor_soccer_fixture as SF
from sportsassets import bettor_venue_settlement as V
from sportsassets.agents import derek_policy as DP
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR
from sportsassets.workers import ext_pinnacle_loop as X

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
FEE = H.flat_fee(0.01)
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


def _attest(prose, *, family="baseball", phase=None, fmt=ST.FMT_NINE,
            ctx=ST.CTX_PRE_GAME, outcomes=("HOME", "AWAY"), fixture=None):
    a = V.attest(sport_family=family,
                 venue_evidence={"rules_text": prose,
                                 "draw_contract_present": family == "soccer"},
                 book_evidence={"outcome_names": list(outcomes)},
                 book_context=ctx, phase=phase, game_format=fmt)
    a["fixture_metadata"] = fixture if fixture is not None else {"read": True}
    return a


# ═════════════════════════════════════════════════════════════════════
# 1 · MLB PLAYOFFS: CAPTURED, SCOPED, NEVER THE REGULAR-SEASON TABLE
# ═════════════════════════════════════════════════════════════════════

def test_the_playoff_terms_carry_their_own_capture_provenance():
    po = ST.book_terms(sport_family="baseball", context=ST.CTX_PRE_GAME,
                       phase=ST.PHASE_PLAYOFF, game_format=ST.FMT_NINE)
    assert po, "a playoff fixture is answered"
    for cond, t in po.items():
        c = t["cite"]
        assert c["retrieved_at"] == "2026-10-01T11:51:10Z", cond
        assert c["page_sha256"].startswith("63d6432114be131d"), cond
        assert c["source_url"] == "https://www.pinnacle.com/en/future/" \
                                  "betting-rules"
        assert not ST.check_citation(c), cond
    assert "MLB Playoff and Play-In games, which will have action whenever " \
           "the game is completed." in po[ST.C_SUSPENDED_RESUMED]["cite"][
               "quote"]
    assert ST.CAPTURE_RUN_PLAYOFF["job"].endswith("/job/110354861334")


def test_regular_season_terms_never_answer_a_playoff_fixture():
    kw = dict(sport_family="baseball", context=ST.CTX_PRE_GAME,
              game_format=ST.FMT_NINE)
    po = ST.book_terms(phase=ST.PHASE_PLAYOFF, **kw)
    rs = ST.book_terms(phase=ST.PHASE_REGULAR, **kw)
    assert po != rs
    # the conditions the playoff page states AMBIGUOUSLY are not filled in
    amb = ST.book_ambiguous(sport_family="baseball", context=ST.CTX_PRE_GAME,
                            phase=ST.PHASE_PLAYOFF)
    assert set(amb) == {ST.C_SUSPENDED_BEYOND, ST.C_CALLED_FINAL,
                        ST.C_STOPPED_EARLY}
    for cond in amb:
        assert cond not in po and cond in rs
    # a phase with no capture of its own is answered by nothing at all
    assert ST.book_terms(phase="SPRING_TRAINING", **kw) == {}
    assert ST.book_terms(phase=None, **kw) == {}


def test_the_live_wild_card_contract_is_incompatible_on_the_postponed_game():
    """Valuation 2059's contract, through the real comparison, with the
    phase the MLB schedule reported for it."""
    cmp_ = ST.compare_prose(sport_family="baseball",
                            venue_prose=PL.RECORDED_PHI_ATL_VENUE_PROSE,
                            context=ST.CTX_PRE_GAME, phase=ST.PHASE_PLAYOFF,
                            game_format=ST.FMT_NINE)
    assert cmp_["verdict"] == ST.INCOMPATIBLE
    assert cmp_["mismatched_conditions"] == [ST.C_NOT_PLAYED]
    row = cmp_["per_condition"][ST.C_NOT_PLAYED]
    assert row["book_payout"] == ST.PAY_STAKE_BACK
    assert row["venue_payout"] == ST.PAY_LAST_FAIR_MARKET_PRICE
    assert cmp_["book_capture"]["retrieved_at"] == "2026-10-01T11:51:10Z"
    # the same text with the phase UNESTABLISHED stays UNKNOWN, by name
    unk = ST.compare_prose(sport_family="baseball",
                           venue_prose=PL.RECORDED_PHI_ATL_VENUE_PROSE,
                           context=ST.CTX_PRE_GAME, phase=None,
                           game_format=ST.FMT_NINE)
    assert unk["verdict"] == ST.UNKNOWN
    assert ST.R_PHASE_UNKNOWN in unk["book_side_absent_refusals"]


# ═════════════════════════════════════════════════════════════════════
# 2 · PRECISE BLOCKERS AND POSITIVE ESTABLISHMENT
# ═════════════════════════════════════════════════════════════════════

def test_blockers_name_the_conflicting_payout():
    sb = V.settlement_blockers(_attest(PL.RECORDED_PHI_ATL_VENUE_PROSE,
                                       phase=ST.PHASE_PLAYOFF))
    assert sb["established"] is False
    assert sb["blockers"][0] == (
        "SETTLEMENT_TERMS_INCOMPATIBLE:POSTPONED_OR_ABANDONED_AND_NEVER_"
        "COMPLETED(book=RETURNS_THE_STAKE_OR_BASIS_IN_FULL;venue=PAYS_THE_"
        "LAST_FAIR_MARKET_PRICE_OF_THE_CONTRACT_NOT_A_STAKE_RETURN)")
    assert "SETTLEMENT_BOOK_TERMS_AMBIGUOUS:%s" % ST.C_SUSPENDED_BEYOND \
        in sb["blockers"]
    assert sb["book_capture"]["sha256"].startswith("63d64321")


def test_blockers_name_the_missing_scope_and_fixture_evidence():
    sb = V.settlement_blockers(
        _attest(GRE_GER, family="soccer", phase=None, fmt=None,
                ctx=None, outcomes=("Greece", "Germany", "Draw")),
        fixture={"read": False, "acquisition": {
            "refusal": "NO_AUTHORITATIVE_FIXTURE_SOURCE_FOR_COMPETITION:"
                       "soccer_brazil_serie_b"}})
    assert sb["established"] is False
    assert sb["blockers"][0] == (
        "SETTLEMENT_FIXTURE_METADATA_ABSENT:NO_AUTHORITATIVE_FIXTURE_SOURCE_"
        "FOR_COMPETITION:soccer_brazil_serie_b")
    assert "SETTLEMENT_SCOPE_NOT_ESTABLISHED:COMPETITION_PHASE_NOT_" \
           "ESTABLISHED" in sb["blockers"]


def test_a_supported_soccer_scope_resolves_the_conflict_by_name():
    sb = V.settlement_blockers(
        _attest(GRE_GER, family="soccer", phase=ST.PHASE_LEAGUE,
                fmt=ST.FMT_NINETY, outcomes=("Greece", "Germany", "Draw")))
    assert sb["verdict"] == ST.INCOMPATIBLE and sb["established"] is False
    assert sb["blockers"][0].startswith(
        "SETTLEMENT_TERMS_INCOMPATIBLE:POSTPONED_OR_ABANDONED_AND_NEVER_"
        "COMPLETED(")


def test_only_a_compatible_comparison_with_no_blocker_is_established():
    ok = V.settlement_blockers(_attest(PL.SYNTHETIC_COMPATIBLE_VENUE_PROSE,
                                       phase=ST.PHASE_REGULAR))
    assert ok == dict(ok, established=True, verdict=ST.COMPATIBLE,
                      blockers=[])
    proj = X._settlement_compatibility(_attest(
        PL.SYNTHETIC_COMPATIBLE_VENUE_PROSE, phase=ST.PHASE_REGULAR))
    assert proj["overall_established"] is True and proj["blockers"] == []


def test_the_absence_of_a_recognised_refusal_is_not_establishment():
    """THE PHILLIES PROJECTION. A row whose comparison is UNKNOWN and which
    carries no settlement-stage refusal code used to read
    overall_established=True."""
    base = {"id": 2059, "us_market_slug": "aec-mlb-phi-atl-2026-10-01",
            "buy_intent": "ORDER_INTENT_BUY_SHORT", "refusals": [
                "CALIBRATION_ONLY_RECORD_IS_NOT_AN_ENTRY_CANDIDATE",
                "VOID_ABANDONMENT_BOOK_RULE_NOT_HELD"]}
    legacy = DP.candidate_from_row(dict(base, settlement_comparison={
        "compatibility": "UNKNOWN", "fixture_read": True}))
    assert legacy["settlement"]["overall_established"] is False
    recorded = DP.candidate_from_row(dict(base, settlement_comparison=dict(
        PL.settlement_comparison("UNKNOWN"))))
    assert recorded["settlement"]["overall_established"] is False
    assert recorded["settlement"]["blockers"][0] == \
        "SETTLEMENT_SCOPE_NOT_ESTABLISHED:COMPETITION_PHASE_NOT_ESTABLISHED"
    # even a COMPATIBLE verdict without the recorded establishment is not one
    bare = DP.candidate_from_row(dict(base, refusals=[],
                                      settlement_comparison={
                                          "compatibility": "COMPATIBLE"}))
    assert bare["settlement"]["overall_established"] is False
    good = DP.candidate_from_row(dict(base, refusals=[],
                                      settlement_comparison=PL.
                                      settlement_comparison("COMPATIBLE")))
    assert good["settlement"]["overall_established"] is True


# ═════════════════════════════════════════════════════════════════════
# 3 · SOCCER: THE ORGANISER'S SCHEDULE UNDER THE VENUE'S OWN KEY
# ═════════════════════════════════════════════════════════════════════

def test_the_uefa_schedule_establishes_phase_format_and_state():
    ev = SF.parse(UEFA["matches"], home="Portugal", away="Norway",
                  date_str="2026-10-04", retrieved_at="2026-10-01T11:58:41Z",
                  url="u", src=UNL)
    assert ev["ok"] and not ev["refusals"]
    assert ev["phase"] == ST.PHASE_LEAGUE
    assert ev["game_format"] == ST.FMT_NINETY
    assert ev["play_has_begun"] is False and ev["event_state_raw"] == \
        "UPCOMING"
    assert ev["actual_start_at"] is None, "a schedule is not an observed start"
    assert ev["scheduled_kickoff"] == "2026-10-04T18:45:00Z"
    assert ev["source_match_id"] == "2047924" and ev["orientation"] == "SAME"
    sw = SF.parse(UEFA["matches"], home="Norway", away="Portugal",
                  date_str="2026-10-04", retrieved_at="x", url="u", src=UNL)
    assert sw["ok"] and sw["orientation"] == "SWAPPED"


def test_nothing_is_defaulted_when_the_organiser_does_not_say():
    m = json.loads(json.dumps(UEFA["matches"][0]))
    m["round"]["mode"] = "SOMETHING_NEW"
    m["type"] = "UNHEARD_OF"
    m["status"] = "RAIN_CHECK"
    h = m["homeTeam"]["internationalName"]
    a = m["awayTeam"]["internationalName"]
    ev = SF.parse([m], home=h, away=a, date_str=m["kickOffTime"]["date"],
                  retrieved_at="x", url="u", src=UNL)
    assert ev["ok"] and ev["phase"] is None and ev["game_format"] is None
    assert ev["play_has_begun"] is None
    assert {SF.R_PHASE, SF.R_FORMAT, SF.R_STATE} <= set(ev["refusals"])
    ko = json.loads(json.dumps(UEFA["matches"][0]))
    ko["round"]["mode"] = "KNOCKOUT"
    ko["type"] = "FINAL"
    kev = SF.parse([ko], home=h, away=a, date_str=ko["kickOffTime"]["date"],
                   retrieved_at="x", url="u", src=UNL)
    assert kev["phase"] == ST.PHASE_KNOCKOUT
    assert ST.admit_scope(sport_family="soccer", phase=kev["phase"],
                          game_format=kev["game_format"])["ok"] is False
    nm = SF.parse(UEFA["matches"], home="Greece", away="Germany",
                  date_str="2026-10-04", retrieved_at="x", url="u", src=UNL)
    assert not nm["ok"] and nm["refusals"] == [SF.R_NO_MATCH]


def _uefa_fetcher(calls):
    def fetch(src, date_str):
        calls.append((src["competition_id"], date_str))
        return {"ok": True, "url": SF.schedule_url(src, date_str),
                "payload": UEFA["matches"],
                "retrieved_at": "2026-10-04T12:00:00+00:00"}
    return fetch


@pg
async def test_a_venue_native_soccer_contract_is_keyed_by_its_own_event():
    conn = await H.connect()
    key = SF.venue_fixture_key("atc-unl-por-nor-2026-10-04")
    try:
        await conn.execute("DELETE FROM venue_fixture_metadata WHERE "
                           " venue_fixture_key = $1", key)
        before = await conn.fetchval("SELECT count(*) FROM fixture_metadata")
        calls = []
        got = await X.acquire_fixture_scope(
            conn, condition_id=None, home="Portugal", away="Norway",
            commence_iso="2026-10-04T18:45:00Z", now=time.time(), cache={},
            sport_family="soccer", sport_key="soccer_uefa_nations_league",
            venue_event_slug="atc-unl-por-nor-2026-10-04",
            venue_fetcher=_uefa_fetcher(calls))
        assert got["read"] is True, got
        assert got["venue_fixture_key"] == key == \
            "event:atc-unl-por-nor-2026-10-04"
        assert got["phase"] == ST.PHASE_LEAGUE
        assert got["game_format"] == ST.FMT_NINETY
        assert got["play_has_begun"] is False
        assert got["source_match_id"] == "2047924"
        assert got["source"] == UNL["source"]
        assert calls == [("2014", "2026-10-04")]
        row = await conn.fetchrow("SELECT * FROM venue_fixture_metadata "
                                  " WHERE venue='PMUS' AND "
                                  " venue_fixture_key=$1", key)
        assert row is not None and row["sport_family"] == "soccer"
        # NO GLOBAL ID WAS INVENTED OR BORROWED
        assert await conn.fetchval("SELECT count(*) FROM fixture_metadata") \
            == before
        assert await conn.fetchval(
            "SELECT count(*) FROM fixture_metadata WHERE condition_id IN "
            " ('', 'None') OR condition_id LIKE 'event:%'") == 0
        # the persisted evidence supplies the quote context
        ctx = X.fmeta_mod.context_for(
            {"play_has_begun": got["play_has_begun"],
             "actual_start_at": got.get("actual_start_at"),
             "retrieved_at": got["retrieved_at"]},
            observed_at="2026-10-04T11:59:00Z")
        assert ctx["context"] == ST.CTX_PRE_GAME
        # a held, recent row is reused rather than re-fetched
        again = await X.acquire_fixture_scope(
            conn, condition_id=None, home="Portugal", away="Norway",
            commence_iso="2026-10-04T18:45:00Z",
            now=row["retrieved_at"].timestamp() + 10, cache={},
            sport_family="soccer", sport_key="soccer_uefa_nations_league",
            venue_event_slug="atc-unl-por-nor-2026-10-04",
            venue_fetcher=_uefa_fetcher(calls))
        assert again["read"] is True and len(calls) == 1
    finally:
        await conn.execute("DELETE FROM venue_fixture_metadata WHERE "
                           " venue_fixture_key = $1", key)
        await conn.close()


@pg
async def test_an_unsupported_competition_or_absent_key_is_refused_by_name():
    conn = await H.connect()
    try:
        n0 = await conn.fetchval("SELECT count(*) FROM venue_fixture_metadata")
        b = await X.acquire_fixture_scope(
            conn, condition_id=None, home="Botafogo-SP", away="Vila Nova",
            commence_iso="2026-10-03T21:30:00Z", now=time.time(), cache={},
            sport_family="soccer", sport_key="soccer_brazil_serie_b",
            venue_event_slug="atc-brb-bot-vln-2026-10-03")
        assert b["read"] is False
        assert b["acquisition"]["refusal"] == \
            "NO_AUTHORITATIVE_FIXTURE_SOURCE_FOR_COMPETITION:" \
            "soccer_brazil_serie_b"
        k = await X.acquire_fixture_scope(
            conn, condition_id=None, home="Greece", away="Germany",
            commence_iso="2026-10-04T18:45:00Z", now=time.time(), cache={},
            sport_family="soccer", sport_key="soccer_uefa_nations_league",
            venue_event_slug=None)
        assert k["read"] is False
        assert k["acquisition"]["refusal"] == SF.R_NO_EVENT_KEY
        # baseball keeps the old guard: no condition key, no read
        m = await X.acquire_fixture_scope(
            conn, condition_id=None, home="A", away="B",
            commence_iso="2026-10-04T18:45:00Z", now=time.time(), cache={},
            sport_family="baseball")
        assert m["refusal"] == X.R_FIXTURE_KEY_ABSENT
        assert await conn.fetchval(
            "SELECT count(*) FROM venue_fixture_metadata") == n0
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · THROUGH THE BENCHMARK AND THE SIMULATOR
# ═════════════════════════════════════════════════════════════════════

async def _nosleep(_):
    return None


async def _pass(conn, acct, transport, now, **kw):
    transport.t = max(transport.t, float(now))
    return await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                               market_data=kw.pop("client", None)
                               or PL.client(transport),
                               config=acct["config"], force=True,
                               fee_fn=FEE, sleep=_nosleep, **kw)


@pytest.fixture
def bench_on(monkeypatch):
    # THE STRICT POLICY ALONE: its own row switched ON for the proof (its
    # migrated state since 184 is off), the completed-game policy's OFF, so
    # every count here is the strict policy's (the completed-game proofs are
    # in test_completed_game_paper_policy.py). Both rows go back to their
    # migrated state afterwards.
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    PL.set_policy_control(PB.CONTROL_KEY, True)
    PL.set_policy_control(PB.CG_POLICY["control_key"], False)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    yield
    PB._CONTEXT_CACHE.clear()
    PL.set_policy_control(PB.CG_POLICY["control_key"], True)
    PL.set_policy_control(PB.CONTROL_KEY, False)


@pg
async def test_supported_entry_and_named_refusals_through_the_real_pass(
        bench_on):
    """ONE PASS, FOUR VALUATIONS, FRESH BOOKS:

      eligible     COMPATIBLE through the real comparison -> ENTER -> order
                   -> simulated fill after the delay -> cash debit on the one
                   ledger -> Xavier handoff
      incompatible the recorded Wild Card text, playoff scope -> REFUSE,
                   named payout conflict, no book read, no order
      unknown      the same text, phase unestablished -> REFUSE, named scope
      legacy       a bare {"compatibility": "COMPATIBLE"} with no recorded
                   establishment -> REFUSE, never an entry
    """
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "settle", now=now)
        t = PL.Transport(now)
        vals = {}
        for kind in ("COMPATIBLE", "INCOMPATIBLE", "UNKNOWN", "LEGACY"):
            v = await PL.valuation(
                conn, decided_at=now - 10, p_pin=0.62,
                compatibility=("COMPATIBLE" if kind == "LEGACY" else kind))
            if kind == "LEGACY":
                await conn.execute(
                    "UPDATE external_valuations SET settlement_comparison="
                    " '{\"compatibility\": \"COMPATIBLE\"}'::jsonb "
                    " WHERE id=$1", v["valuation_id"])
            t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
            vals[kind] = v
        client = PL.client(t)
        p1 = await _pass(conn, acct, t, now, client=client)
        assert p1["ran"] and not p1["errors"], p1["errors"]
        assert client.mutation_attempts == 0

        async def bench(kind):
            return await conn.fetchrow(
                "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
                " valuation_id=$2 AND strategy=$3", acct["session_id"],
                vals[kind]["valuation_id"], PB.STRATEGY)

        ok = await bench("COMPATIBLE")
        assert ok["verdict"] == "ENTER", (ok["refusal"], ok["refusals"])
        chk = {c["check"]: c for c in
               H.j(ok["pinnacle"])["contract_match"]["checks"]}
        st = chk[DP.C_SETTLEMENT]
        assert st["passed"] is True and st["blockers"] == []
        assert st["compatibility"] == "COMPATIBLE"
        assert st["overall_established"] is True
        econ = H.j(ok["economics"])
        assert econ["best_level_edge_pp"] == pytest.approx(12.0)
        assert econ["acquisition"]["net_ev_positive"] is True
        assert ok["p_internal"] is None

        inc = await bench("INCOMPATIBLE")
        assert inc["verdict"] == "REFUSE"
        assert inc["refusal"] == "SETTLEMENT_NOT_SUPPORTED"
        assert inc["refusals"][1].startswith(
            "SETTLEMENT_TERMS_INCOMPATIBLE:POSTPONED_OR_ABANDONED_AND_NEVER_"
            "COMPLETED(")
        assert inc["book_obs_id"] is None, "no book is read for a refusal"
        unk = await bench("UNKNOWN")
        assert unk["verdict"] == "REFUSE"
        assert unk["refusals"][1] == ("SETTLEMENT_SCOPE_NOT_ESTABLISHED:"
                                      "COMPETITION_PHASE_NOT_ESTABLISHED")
        leg = await bench("LEGACY")
        assert leg["verdict"] == "REFUSE"
        assert "SETTLEMENT_BLOCKERS_NOT_RECORDED_ON_THIS_ROW" in \
            leg["refusals"]
        orders = await conn.fetch(
            "SELECT * FROM paper_orders WHERE session_id=$1 AND "
            " role='ENTRY'", acct["session_id"])
        assert [o["decision_id"] for o in orders] == [ok["decision_id"]]
        # THE SIMULATED FILL AFTER THE DELAY, THE CASH, THE HANDOFF
        p2 = await _pass(conn, acct, t, now + 5, client=client)
        assert not p2["errors"], p2["errors"]
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1", ok["decision_id"])
        assert o["state"] == "FILLED" and o["strategy"] == PB.STRATEGY
        fills = await conn.fetch("SELECT * FROM paper_fills WHERE "
                                 " order_id=$1", o["order_id"])
        assert sum(float(f["qty"]) for f in fills) == float(o["qty"])
        assert {f["event_source"] for f in fills} == {"SIMULATOR"}
        from sportsassets import bettor_paper_ledger as L
        b = await L.balances(conn, acct["account_id"], now=now + 6)
        assert b["ledger_consistent"] is True
        cost = sum(float(f["qty"]) * float(f["price"]) for f in fills)
        fees = sum(float(f["fee_usd"]) for f in fills)
        assert b["cash_usd"] == pytest.approx(500000.0 - cost - fees)
        h = await conn.fetchrow("SELECT * FROM paper_handoffs WHERE "
                                " group_id=$1", o["group_id"])
        assert h is not None and h["owner"] == "XAVIER"
        assert h["strategy"] == PB.STRATEGY
        assert float(h["confirmed_qty"]) == float(o["qty"])
        n_fund = await conn.fetchval(
            "SELECT count(*) FROM paper_ledger WHERE account_id=$1 AND "
            " kind='INITIAL_FUNDING'", acct["account_id"])
        assert n_fund == 1
    finally:
        await PL.purge_everything(conn)
        await conn.close()
