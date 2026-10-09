"""RC6 LANE ADRIANA (category 11, SHADOW ONLY): VOID TERMS FROM EACH CONTRACT'S
OWN PUBLISHED RULES, THE CROSS-VENUE CLAIM SCAN RECORDED EVERY PASS WITH ITS
NEAR COMPLEMENTS, AND THE COMPLETION READBACK STATING WHAT THE SCANS READ.

Production RC5 (pm-acceptance 37836393458, 2026-10-08 ~20:03Z):
completion.json arbitrage.fail_closed was the constant "void terms not
established -> every structure refused (agents/adriana.ESTABLISHED_VOID_TERMS)"
whatever the scans read; the latest scan read 1 market, 0 fresh, 0
structures; the readback's "claim scan" was adr-claims-1791436419188 (05:13Z,
15 h old) although the claim scan ran every 5 min and priced 56 claim pairs a
pass (28 of them complements in ordinary completion: the cross-venue /
cross-market arbitrage candidates) without recording one.

The venue's own text, captured by the market plane (research-sql 37874944438
B: 4,166 football full-game spreads, 2,640 full-game totals, every NBA / NHL /
soccer line ESTABLISHED LAST_FAIR_PRICE / LAST_FAIR_PRICE with its window),
settles a cancelled or unrescheduled game at THAT market's last fair price.
Read per contract, those terms ARE established -- and they refuse every
structure across two markets, because two markets' fair prices are never
summed to $1 (settlement_pair_policy, owner directive RC5). Both truths are
pinned here: the terms are read and cited; the structures stay refused.

Nothing here can order, size, fund or approve anything: every record is
SHADOW, every verdict REFUSED unless an exact complement is proven after
costs on terms established for every leg.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from sportsassets import canonical_claims as CC
from sportsassets import kalshi_fees as KF
from sportsassets import refusal_taxonomy_table as TT
from sportsassets import settlement_pair_policy as SPP
from sportsassets import settlement_rule_registry as SRR
from sportsassets.agents import adriana as AD
from sportsassets.agents import adriana_arb as A
from sportsassets.agents import adriana_claims as AC
from sportsassets.completion import read as CR
from sportsassets.market_plane import rules as RULES

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
LISTINGS = {m["slug"]: m for m in json.loads(
    (FIX / "pmus_line_listings_2026_10_04.json").read_text())["markets"]}
DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
D = Decimal
NOW = datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc)
NBA_TOTAL = "tsc-nba-gs-lac-2026-10-04-221pt5"
NFL_TOTAL = "tsc-nfl-det-car-2026-10-04-total-22pt5"
NFL_TEAM_TOTAL = "tsc-nfl-det-car-2026-10-04-tt-car-10pt5"


def captured(slug, *, tamper=False, published=True, text=None):
    """The market_plane_rules columns of one contract as the census reads
    them beside its book: the venue's own recorded listing through the
    premap capture (market_plane.rules.pmus_row)."""
    row = RULES.pmus_row(LISTINGS[slug])
    out = {"rules_published": row["rules_published"],
           "rules_sha256": row["rules_sha256"], "rules_text": row["rules_text"],
           "rules_parse_status": row["parse_status"],
           "rules_parser_version": row["parser_version"],
           "rules_source": row["source"],
           "rules_observed_at": NOW - timedelta(hours=1)}
    if text is not None:
        out.update(rules_text=text, rules_sha256=SRR.fingerprint(text))
    if tamper:
        out["rules_text"] = out["rules_text"].replace("two calendar days",
                                                      "two weeks")
    if not published:
        out.update(rules_published=False, rules_text=None, rules_sha256=None)
    return out


def _lv(p, q):
    return {"px": {"value": str(p)}, "qty": str(q)}


def book(slug, ask, bid, age_s, *, rules=None, qty=50):
    r = {"us_market_slug": slug, "observed_at": NOW - timedelta(
        seconds=age_s), "offers": [_lv(ask, qty)], "bids": [_lv(bid, qty)],
        "error": None, "sport": "basketball"}
    r.update(rules or {})
    return r


# OVER 210.5 asks 0.40 while OVER 211.5 is bid 0.47 (NO at 0.53): a middle
# that costs 0.93 + fees for a floor of 1 in every ORDINARY outcome
LO, HI = ("tsc-nba-lal-bos-2026-10-05-210pt5",
          "tsc-nba-lal-bos-2026-10-05-211pt5")
MIDDLE = [book(LO, "0.40", "0.38", 3, rules=captured(NBA_TOTAL)),
          book(HI, "0.49", "0.47", 2, rules=captured(NBA_TOTAL))]


# ═════════════════════════════════════════════════════════════════════
# 1 · ONE CONTRACT'S VOID TERMS, FROM ITS OWN CAPTURED RULES
# ═════════════════════════════════════════════════════════════════════

def test_each_contract_reads_its_own_published_void_terms():
    nba = AD.contract_void_terms(captured(NBA_TOTAL))
    assert nba["established"] and nba["why"] is None
    assert (nba["void_rule"], nba["postponement_payout"], nba["window_h"]) \
        == (SRR.LAST_FAIR_PRICE, SRR.LAST_FAIR_PRICE, 48.0)
    # a last fair price is ONE market's number in [0, 1]: guaranteed 0 to
    # the holder of either side, never a constant shared with another market
    assert nba["void_lo"] == (D(0), D(0)) == nba["postponed_lo"]
    cite = nba["citation"]
    assert cite["rules_sha256"] == RULES.pmus_row(
        LISTINGS[NBA_TOTAL])["rules_sha256"]
    assert cite["source"] == RULES.SOURCE_PMUS_BOARD
    assert cite["parser_version"] == SRR.PARSER_VERSION
    assert "last fair market price" in cite["clause"]
    assert "two calendar days" in cite["clause"]
    nfl = AD.contract_void_terms(captured(NFL_TOTAL))
    assert nfl["established"] and nfl["window_h"] == 336.0


def test_every_line_form_the_census_reads_is_established_from_venue_text():
    """Every recorded listing whose slug the census grammar reads (football,
    hockey, basketball, baseball, soccer: full game) states its void terms
    unambiguously; the forms it does not read are other subjects."""
    read = 0
    for slug in LISTINGS:
        if AD.parse_slug(slug) is None:
            continue
        read += 1
        t = AD.contract_void_terms(captured(slug))
        assert t["established"], (slug, t["why"])
    assert read >= 14


def test_terms_not_stated_or_not_verifiable_are_never_defaulted():
    # a team total says a reviewer settles it: no payout is established
    tt = AD.contract_void_terms(captured(NFL_TEAM_TOTAL))
    assert not tt["established"] and tt["why"] == AD.R_VT_MANUAL_REVIEW
    assert AD.contract_void_terms({})["why"] == AD.R_VT_RULES_NOT_CAPTURED
    assert AD.contract_void_terms(captured(NBA_TOTAL, published=False))[
        "why"] == AD.R_VT_RULES_NOT_PUBLISHED
    # the text no longer hashes to the recorded fingerprint
    assert AD.contract_void_terms(captured(NBA_TOTAL, tamper=True))[
        "why"] == AD.R_VT_FINGERPRINT_DIFFERS
    silent = ("This market will settle to Yes if GS Warriors and LA Clippers "
              "combine for over 221.5 points. Outcome sourced from the "
              "relevant governing body.")
    assert AD.contract_void_terms(captured(NBA_TOTAL, text=silent))[
        "why"] == AD.R_VT_VOID_NOT_STATED
    conflict = dict(captured(NBA_TOTAL), rules_parse_status=SRR.CONFLICT)
    assert AD.contract_void_terms(conflict)["why"] == AD.R_VT_CONFLICT
    # every reason is a classified SOFTWARE refusal, never economic
    for code in (AD.R_VT_RULES_NOT_CAPTURED, AD.R_VT_RULES_NOT_PUBLISHED,
                 AD.R_VT_FINGERPRINT_DIFFERS, AD.R_VT_CONFLICT,
                 AD.R_VT_MANUAL_REVIEW, AD.R_VT_VOID_NOT_STATED,
                 AD.R_VT_POSTPONEMENT_NOT_STATED, AD.R_VT_WINDOW_NOT_STATED,
                 AD.R_VT_RULE_NOT_FIXED, AD.R_VOID_STATE_FLOOR):
        assert TT.TABLE[code][0] == "SOFTWARE", code


# ═════════════════════════════════════════════════════════════════════
# 2 · THE CENSUS ON ESTABLISHED TERMS: READ, CITED -- AND STILL REFUSED
# ═════════════════════════════════════════════════════════════════════

def test_established_fair_price_terms_refuse_the_middle_at_its_void_floor():
    r = AD.census(MIDDLE, NOW)
    assert r["opportunities"] == []
    c = r["census"]
    assert c["void_terms_established"] is True
    vt = c["void_terms"]
    assert (vt["contracts"], vt["established"]) == (2, 2)
    assert vt["rules"] == {"LAST_FAIR_PRICE/LAST_FAIR_PRICE": 2}
    assert vt["windows_h"] == {"48": 2}
    assert vt["examples"][0]["rules_sha256"]
    # no structure waits on unread terms any more
    assert c["conditional_candidates"] == 0
    for rec in r["refusals"]:
        assert rec["verdict"] == A.REFUSED
        assert AD.VOID_TERMS_NOT_ESTABLISHED not in A.reason_codes(rec)
    top = r["refusals"][0]
    assert A.reason_codes(top)[0] == AD.R_VOID_STATE_FLOOR
    assert top["reasons"][0]["outcomes"] == sorted(
        [A.OUTCOME_VOID, A.OUTCOME_POSTPONED])
    tab = top["payoff_table"]
    assert tab["floor_payout_per_set"] == "0"
    assert set(tab["floor_outcomes"]) == {A.OUTCOME_VOID, A.OUTCOME_POSTPONED}
    # the ordinary-completion floor is a real hedge (1 in every bucket)
    assert {v for o, v in tab["payout_by_outcome"].items()
            if o not in A.REQUIRED_NONSTANDARD} == {"1", "2"}
    # what it WOULD have been if both markets settled 50-50: labelled,
    # counted apart, never a verdict and never an opportunity
    assert c["exceptional_state_refusals_proven_under_hypothesis"] == 1
    assert top["conditional_on"] == AD.HYPOTHESIS["label"]
    assert D(top["conditional_economics"]["worst_case_net_profit"]) > 0
    assert set(top["void_terms"]) == {LO, HI}
    assert all(t["established"] and t["citation"]["rules_sha256"]
               for t in top["void_terms"].values())
    # every considered structure is recorded exactly once
    assert c["pairs_considered"] == len(r["refusals"]) == 4


def test_a_leg_without_captured_terms_keeps_the_structure_fail_closed():
    rows = [MIDDLE[0], book(HI, "0.49", "0.47", 2)]
    r = AD.census(rows, NOW)
    c = r["census"]
    assert r["opportunities"] == []
    assert c["void_terms_established"] is False
    assert c["void_terms"]["not_established"] == {
        AD.R_VT_RULES_NOT_CAPTURED: 1}
    # judged exactly as before: all legs under the hypothesis, the would-be
    # middle a CONDITIONAL candidate refused for the unread terms
    assert c["conditional_candidates"] == 1
    for rec in r["refusals"]:
        codes = A.reason_codes(rec)
        assert AD.VOID_TERMS_NOT_ESTABLISHED in codes
        assert AD.R_VOID_STATE_FLOOR not in codes
    assert A.reason_codes(r["refusals"][0])[0] == \
        AD.VOID_TERMS_NOT_ESTABLISHED


def test_the_venues_own_slug_forms_are_read_and_other_subjects_are_not():
    t = AD.parse_slug("tsc-cfb-airf-nill-2026-10-10-total-19pt5")
    assert (t["family"], t["period"], t["threshold"]) == (
        A.TOTAL, "FULL", D("19.5"))
    assert t["event"] == "cfb-airf-nill-2026-10-10"
    q = AD.parse_slug("tsc-nfl-was-sf-2026-10-19-1q-8pt5")
    assert (q["family"], q["period"]) == (A.TOTAL, "Q1")
    s = AD.parse_slug("asc-cfb-airf-nill-2026-10-10-3q-neg-1pt5")
    assert (s["family"], s["period"], s["threshold"]) == (
        A.SPREAD, "Q3", D("1.5"))
    # production forms of OTHER subjects stay unread (named, never guessed)
    for slug in ("tsc-cfb-airf-nill-2026-10-10-tt-airf-16pt5",
                 "tsc-cfb-airf-nill-2026-10-10-tt1h-airf-11pt5",
                 "tsc-nfl-was-sf-2026-10-19-tt2h-was-9pt5",
                 "tsc-atp-abeshe-dangli-2026-10-09-tg-17pt5",
                 "tsc-setkamecz-blajan-dufjak-2026-10-09-st-3pt5",
                 "asc-atp-abeshe-dangli-2026-10-09-gs-neg-1pt5",
                 "asc-atp-abeshe-dangli-2026-10-09-ss-neg-1pt5",
                 "tsc-cs2-1win-aur-2026-10-09-tot-2pt5",
                 "tsc-mlb-az-col-2026-09-22-f5-2pt5",
                 "asc-cfb-airf-nill-2026-10-10-total-neg-1pt5"):
        assert AD.parse_slug(slug) is None, slug


# ═════════════════════════════════════════════════════════════════════
# 3 · THE CLAIM-FIRST SCAN: NEAR COMPLEMENTS RECORDED, VOID TERMS COUNTED
# ═════════════════════════════════════════════════════════════════════

CNOW = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc).timestamp()
FX = CC.Fixture(event_key="MLB:2026-10-08T00:00Z:TB@NYY", sport="BASEBALL",
                league="MLB", start_epoch=CNOW + 4 * 3600,
                outcome_kind="TWO_WAY", home="NYY", away="TB")
FAIR = {"overtime_included": True, "draw_rule": "IMPOSSIBLE",
        "void_rule": "LAST_FAIR_PRICE", "postponement_window_hours": 48.0,
        "postponement_payout": "LAST_FAIR_PRICE",
        "verification_sources": ["MLB"]}
KTERMS = KF.effective_terms(
    series_ticker="KXMLBGAME", event_ticker="KXMLBGAME-26OCT072000TBNYY",
    at=CNOW, event_changes=[], series_changes=[{
        "id": "test-change-x1", "fee_type": "quadratic_with_maker_fees",
        "fee_multiplier": 1, "scheduled_ts": "2025-10-04T07:00:00Z",
        "series_ticker": "KXMLBGAME"}])


def inst(venue, market, side, subject, ask, *, terms=FAIR, at=CNOW):
    return CC.Instrument(venue=venue, market_id=market, side=side,
                         subject=subject, settlement=dict(terms),
                         settlement_status="PROVEN",
                         mapping_status="ESTABLISHED",
                         asks=((D(ask), 100),), observed_at=at,
                         book_basis="TEST_BOOK", sport="BASEBALL",
                         fee_terms=KTERMS if venue == "KALSHI" else None)


def test_a_cross_market_near_complement_is_a_recorded_refusal_not_a_tally():
    """Kalshi NYY YES + Kalshi TB YES at 0.30 + 0.30: $1 in every ordinary
    outcome, each market's own fair price on a cancelled game. Recorded as a
    REFUSED structure with the policy's floor (0) -- the engine on 0.50
    fair prices WOULD call it guaranteed; that is labelled, never used."""
    y = inst("KALSHI", "K-NYY", "YES", "HOME", "0.30")
    t = inst("KALSHI", "K-TB", "YES", "AWAY", "0.30")
    b = CC.build_claims(FX, [y, t])
    res = AC.scan_fixture(FX, b, now=CNOW)
    assert res["records"] == []                 # complementary pairs only
    assert res["near_complement_pairs"] == 1
    (rec,) = res["near_records"]
    assert rec["verdict"] == A.REFUSED and rec["prices_compared"] is False
    assert SPP.R_PRICED_NOT_A_COMPLEMENT in A.reason_codes(rec)
    assert rec["settlement_pair_policy"]["guaranteed_floor"] == "0"
    assert rec["claim_pair"]["near_complement"] is True
    assert rec["claim_pair"]["topology"] == "SAME_VENUE"
    assert rec["conditional_on"] == AC.CONDITIONAL_LABEL
    assert rec["conditional_verdict_not_a_verdict"] == \
        A.GUARANTEED_AFTER_COSTS
    eco = rec["conditional_economics"]
    assert D(eco["worst_case_net_profit"]) > 0
    # per-venue fee on every leg, sized against real depth
    assert all(D(x["fee"]) > 0 for x in eco["legs"])
    assert int(eco["qty"]) <= 100
    # the census: one structure considered, refused, no opportunity
    cen = AC.census_result([res], markets_read=2, books_fresh=2, skipped={})
    assert cen["opportunities"] == [] and len(cen["refusals"]) == 1
    assert cen["census"]["pairs_considered"] == 1
    assert cen["census"]["near_complement_pairs"] == 1


def test_a_cross_venue_near_complement_names_the_settlement_difference():
    """Kalshi NYY YES + Polymarket US TB YES (the PMUS moneyline's long side
    on TB): complementary in ordinary completion, graded by DIFFERENT stated
    sources, each a separate market's fair price on a cancelled game."""
    yk = inst("KALSHI", "K-NYY", "YES", "HOME", "0.30")
    pm_terms = dict(FAIR, postponement_window_hours=336.0,
                    verification_sources=["the relevant governing body"])
    tp = inst("POLYMARKET_US", "aec-mlb-tb-nyy-2026-10-07", "YES", "AWAY",
              "0.30", terms=pm_terms)
    res = AC.scan_fixture(FX, CC.build_claims(FX, [yk, tp]), now=CNOW)
    (rec,) = res["near_records"]
    assert rec["verdict"] == A.REFUSED
    assert rec["claim_pair"]["topology"] == "CROSS_VENUE"
    codes = A.reason_codes(rec)
    assert SPP.R_PARTIAL_UNKNOWN in codes
    # the conditional engine run refuses before any price: different
    # sources, different windows -- the venues never fill as one
    assert rec["conditional_economics"] is None
    assert {r["code"] for r in rec["conditional_reasons"]} & {
        A.SETTLEMENT_SOURCE_DIFFERS, A.SETTLE_WINDOW_DIFFERS}
    assert res["records"] == []


def test_the_shared_workers_count_near_complements_but_never_price_them():
    """The workers' digest (claims_pass, every 30 s) scans the same fixtures:
    it counts the near complements and never runs the engine on them --
    only Adriana's own scan records them (no extra CPU in the shared
    workers)."""
    import ast
    import inspect

    from sportsassets.workers import kalshi_market_data as W
    y = inst("KALSHI", "K-NYY", "YES", "HOME", "0.30")
    t = inst("KALSHI", "K-TB", "YES", "AWAY", "0.30")
    res = AC.scan_fixture(FX, CC.build_claims(FX, [y, t]), now=CNOW,
                          near_records=False)
    assert res["near_complement_pairs"] == 1 and res["near_records"] == []
    calls = [n for n in ast.walk(ast.parse(inspect.getsource(W.claims_pass)))
             if isinstance(n, ast.Call) and getattr(n.func, "attr", None)
             == "scan_fixture"]
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(kw.get("near_records"), ast.Constant) and \
        kw["near_records"].value is False


def test_alias_void_terms_count_every_alias_by_name():
    a = inst("KALSHI", "K-NYY", "YES", "HOME", "0.40")
    no_window = dict(FAIR)
    no_window.pop("postponement_window_hours")
    no_window.pop("postponement_payout")
    b = inst("KALSHI", "K-TB", "YES", "AWAY", "0.40", terms=no_window)
    no_void = dict(FAIR)
    no_void.pop("void_rule")
    c = inst("POLYMARKET_US", "aec-x", "YES", "AWAY", "0.40", terms=no_void)
    built = CC.build_claims(FX, [a, b, c])
    vt = AC.alias_void_terms([a, b, c], built["states"])
    assert vt["aliases"] == 3 and vt["established"] == 1
    assert vt["not_established"] == {AC.R_ALIAS_POSTPONEMENT_NOT_STATED: 1,
                                     AC.R_ALIAS_CANCELLATION_NOT_STATED: 1}
    assert vt["rules"] == {"LAST_FAIR_PRICE/LAST_FAIR_PRICE": 1}
    cen = AC.census_result([], markets_read=3, books_fresh=3, skipped={},
                           void_terms=vt)["census"]
    assert cen["void_terms_established"] is False
    assert cen["void_terms"]["source"].startswith("market_plane_rules")


def test_the_claim_scan_runs_its_engine_passes_off_the_event_loop(
        monkeypatch):
    """Adriana's runner is in the API process: the claim scan's pure engine
    passes run in a worker thread, never on the event loop that serves the
    API (its database reads stay on the loop)."""
    import threading

    from sportsassets import canonical_claims_db as CDB
    from sportsassets.redteam import settlement as RTS
    y = inst("KALSHI", "K-NYY", "YES", "HOME", "0.30")
    t = inst("KALSHI", "K-TB", "YES", "AWAY", "0.30")
    built = CC.build_claims(FX, [y, t])
    seen = []

    async def fake_assemble(conn, *, now=None, scope=None):
        return [(FX, built, [y, t])]

    async def fake_apply(conn, b):
        return b, {}

    real = AC.scan_fixture

    def spy(fx, b, **kw):
        seen.append(threading.current_thread() is threading.main_thread())
        return real(fx, b, **kw)
    monkeypatch.setattr(CDB, "assemble", fake_assemble)
    monkeypatch.setattr(RTS, "apply", fake_apply)
    monkeypatch.setattr(AC, "scan_fixture", spy)
    cen = asyncio.run(CDB.claims_census(object(), now=CNOW))
    assert seen == [False]
    assert cen["census"]["near_complement_pairs"] == 1
    assert cen["census"]["markets_read"] == 2


def test_partial_fills_on_a_cross_venue_pair_are_exposure_never_profit():
    """A proven cross-venue complement (fixed terms on both venues, one
    stated source) planned in SHADOW: the venues never fill atomically --
    leg A partial, leg B rejected leaves UNHEDGED exposure whose worst case
    is a LOSS of its whole cost basis, and recovery is priced, not assumed."""
    fixed = dict(FAIR, void_rule="SCALAR_0_50",
                 postponement_payout="SCALAR_0_50")
    yk = inst("KALSHI", "K-NYY", "YES", "HOME", "0.44", terms=fixed)
    tp = inst("POLYMARKET_US", "aec-mlb-tb-nyy-2026-10-07", "YES", "AWAY",
              "0.45", terms=fixed)
    res = AC.scan_fixture(FX, CC.build_claims(FX, [yk, tp]), now=CNOW)
    (rec,) = res["records"]
    assert rec["verdict"] == A.GUARANTEED_AFTER_COSTS, rec["reasons"]
    assert rec["claim_pair"]["topology"] == "CROSS_VENUE"
    fees = {x["venue"]: D(x["fee"]) for x in rec["economics"]["legs"]}
    assert set(fees) == {"KALSHI", "POLYMARKET_US"} and all(
        f > 0 for f in fees.values())
    ex = A.plan_execution(rec)
    assert ex.state == A.PLANNED
    t = A.transition(ex, A.Event(A.EV_WORK, "A")).execution
    t = A.transition(t, A.Event(A.EV_FILL, "A", qty=10,
                                price=t.leg_a.last_ask)).execution
    t = A.transition(t, A.Event(A.EV_TIMEOUT, "A")).execution
    assert t.state == A.LEG_A_FILLED and t.target_qty == 10
    t = A.transition(t, A.Event(A.EV_WORK, "B")).execution
    t = A.transition(t, A.Event(A.EV_FILL, "B", qty=4,
                                price=t.leg_b.last_ask)).execution
    t = A.transition(t, A.Event(A.EV_REJECT, "B")).execution
    assert t.state == A.UNHEDGED_EXPOSURE
    e = A.exposure(t)
    assert (e["matched_qty"], e["unhedged_qty_a"]) == (4, 6)
    # the residual is never profit: its worst case is losing all of it
    assert e["worst_case_loss_unhedged"] == t.leg_a.basis_of(6)
    assert e["worst_case_total_pnl"] < e["matched_locked_pnl"]
    rr = A.recommend_recovery(t, complete_ask=D("0.60"),
                              complete_fee=D("0.02"), unwind_bid=D("0.30"),
                              unwind_fee=D("0.01"))
    assert rr["unhedged_qty"] == 6 and rr["worst_case_if_nothing_done"] < 0


# ═════════════════════════════════════════════════════════════════════
# 4 · COMPLETION STATES WHAT THE SCANS RECORDED (never a constant)
# ═════════════════════════════════════════════════════════════════════

#: a claim scan whose capped fixture read reached every fixture it could
#: price (canonical_claims_db.new_scope shape)
FULL_SCOPE = {"max_fixtures": 80, "in_window": 65, "read": 65,
              "cut_by_cap": 0, "cut_by_cap_readable": 0,
              "cut_by_cap_cross_venue": 0, "cut_by_cap_named": [],
              "pmus_identity_missing": 0, "pmus_identity_missing_named": []}


def _scan(kind, vt, *, age=60.0, now=1_000_000.0, scope="full"):
    bc = {"void_terms": vt}
    if kind == "claims" and scope is not None:
        bc["scope"] = FULL_SCOPE if scope == "full" else scope
    return {"scan_id": "adr-%s-1" % kind,
            "finished_at": datetime.fromtimestamp(now - age, timezone.utc),
            "by_code": json.dumps(bc)}


def test_the_readback_states_the_void_terms_the_scans_recorded():
    now = 1_000_000.0
    est = {"contracts": 2, "established": 2, "not_established": {},
           "rules": {"LAST_FAIR_PRICE/LAST_FAIR_PRICE": 2}}
    alias_short = {"aliases": 10, "established": 8, "not_established": {
        AC.R_ALIAS_POSTPONEMENT_NOT_STATED: 2}, "rules": {}}
    alias_all = dict(alias_short, established=10, not_established={})
    none_read = {"contracts": 0, "established": 0, "not_established": {},
                 "rules": {}}
    phrase = "void terms not established"
    # no scan at all: the base statement, verbatim
    v = CR.arbitrage_void_terms({}, now=now)
    assert v["statement"] == CR.ARB_FAIL_CLOSED_UNREAD and not v[
        "established"]
    # both current scans read contracts, every one established
    v = CR.arbitrage_void_terms({"census": _scan("scan", est),
                                 "cross_venue": _scan("claims", alias_all)},
                                now=now)
    assert v["established"] and phrase not in v["statement"]
    assert v["by_scan"]["census"]["state"] == "ESTABLISHED"
    # ANY alias short of its terms: not established, named, counted
    v = CR.arbitrage_void_terms({"census": _scan("scan", est),
                                 "cross_venue": _scan("claims", alias_short)},
                                now=now)
    assert not v["established"] and phrase in v["statement"]
    assert "2 of 10 aliases" in v["statement"]
    # a stale scan establishes nothing
    v = CR.arbitrage_void_terms({"census": _scan("scan", est, age=901.0)},
                                now=now)
    assert not v["established"] and phrase in v["statement"]
    # a scan that read nothing establishes nothing on its own
    v = CR.arbitrage_void_terms({"census": _scan("scan", none_read)},
                                now=now)
    assert not v["established"] and phrase in v["statement"]
    # a row recorded before this census existed (no void_terms) is not read
    v = CR.arbitrage_void_terms({"census": _scan("scan", None)}, now=now)
    assert not v["established"] and phrase in v["statement"]


def test_missing_cross_venue_evidence_never_establishes_the_void_terms():
    """Review finding (completion/read.py): a census with ONE established
    PMUS contract, beside a cross-venue claim scan that read no contract,
    was never recorded, or is absent, made `established` True and dropped
    "void terms not established" -- so the evaluator's void_terms_established
    unit (scorecard_14: the phrase absent) passed on zero cross-venue
    evidence. Missing evidence is never neutral: every scan kind must be
    current, carry a census and have read a contract, and each gap is named
    with its state."""
    now = 1_000_000.0
    phrase = "void terms not established"
    est = {"contracts": 1, "established": 1, "not_established": {},
           "rules": {"LAST_FAIR_PRICE/LAST_FAIR_PRICE": 1}}
    alias_all = {"aliases": 6, "established": 6, "not_established": {},
                 "rules": {"LAST_FAIR_PRICE/LAST_FAIR_PRICE": 6}}
    read_none = {"aliases": 0, "established": 0, "not_established": {},
                 "rules": {}}
    census = _scan("scan", est)
    empty = dict(_scan("claims", read_none), status="NO_EVIDENCE",
                 why="NO_ESTABLISHED_KALSHI_FIXTURE_WITH_A_READABLE_BOOK_IN_"
                     "THE_CLAIM_WINDOW")
    for cross, state in ((None, "NO_SCAN"), ("absent", "NO_SCAN"),
                         (empty, "READ_NO_CONTRACT"),
                         (_scan("claims", None),
                          "VOID_TERMS_CENSUS_NOT_RECORDED"),
                         (_scan("claims", alias_all, age=901.0),
                          "STALE_SCAN")):
        scans = {"census": census}
        if cross != "absent":
            scans["cross_venue"] = cross
        v = CR.arbitrage_void_terms(scans, now=now)
        assert v["established"] is False, state
        assert phrase in v["statement"], state
        assert v["by_scan"]["cross_venue"]["state"] == state
        assert "cross_venue: void terms not established" in v["statement"]
        # the census's own established terms stay visible beside the gap
        assert v["by_scan"]["census"]["state"] == "ESTABLISHED"
    # the read-nothing state names the scan's own status and why
    v = CR.arbitrage_void_terms({"census": census, "cross_venue": empty},
                                now=now)
    assert "status NO_EVIDENCE, NO_ESTABLISHED_KALSHI_FIXTURE" in \
        v["statement"]
    # and the mirror image: the cross-venue scan established, the census
    # read nothing -> not established either (no kind is ever neutral)
    v = CR.arbitrage_void_terms({
        "census": _scan("scan", {"contracts": 0, "established": 0,
                                 "not_established": {}, "rules": {}}),
        "cross_venue": _scan("claims", alias_all)}, now=now)
    assert v["established"] is False and phrase in v["statement"]
    assert v["by_scan"]["census"]["state"] == "READ_NO_CONTRACT"
    # only both kinds current, read and established make it established
    v = CR.arbitrage_void_terms({"census": census,
                                 "cross_venue": _scan("claims", alias_all)},
                                now=now)
    assert v["established"] is True and phrase not in v["statement"]
    assert v["requires"] == ["census", "cross_venue"]


def test_terms_the_capped_read_did_not_reach_are_never_established():
    """The cap must never be how the terms come out established: a readable
    fixture the claim scan's cap cut (production: up to ~150 ESTABLISHED
    fixtures in the window against a cap of 80, research-sql 37888668578 C)
    has unread terms -- e.g. the Kalshi soccer aliases whose postponement
    window the registry does not read. So does a mapped PMUS market the scan
    could not read. A claim scan with no recorded scope is unread scope too.
    A cut fixture with no readable Kalshi book forms no structure and only
    stays named in the scope."""
    now = 1_000_000.0
    phrase = "void terms not established"
    est = {"contracts": 2, "established": 2, "not_established": {},
           "rules": {"LAST_FAIR_PRICE/LAST_FAIR_PRICE": 2}}
    alias_all = {"aliases": 336, "established": 336, "not_established": {},
                 "rules": {"LAST_FAIR_PRICE/LAST_FAIR_PRICE": 336}}
    cut = dict(FULL_SCOPE, in_window=150, read=80, cut_by_cap=70,
               cut_by_cap_readable=70,
               cut_by_cap_named=["KXEPLGAME-26OCT10ARSCHE", "KXNBAGAME-X"])
    for scope, why in ((cut, "70 readable fixture(s) cut by the 80-fixture "
                             "cap (KXEPLGAME-26OCT10ARSCHE, KXNBAGAME-X)"),
                       (dict(FULL_SCOPE, pmus_identity_missing=1,
                             pmus_identity_missing_named=["aec-x"]),
                        "1 mapped PMUS market(s) not read, no premap "
                        "identity (aec-x)"),
                       (None, "the scan recorded no fixture scope")):
        v = CR.arbitrage_void_terms({
            "census": _scan("scan", est),
            "cross_venue": _scan("claims", alias_all, scope=scope)}, now=now)
        assert v["established"] is False and phrase in v["statement"], why
        cv = v["by_scan"]["cross_venue"]
        assert cv["state"] == "SCOPE_NOT_READ" and cv["unread_scope"] == [why]
        assert why in v["statement"]
    # a cap that cut only fixtures without a readable book reached every
    # structure the scan could price: the read terms decide
    dark = dict(FULL_SCOPE, in_window=83, read=80, cut_by_cap=3,
                cut_by_cap_readable=0, cut_by_cap_named=["A", "B", "C"])
    v = CR.arbitrage_void_terms({
        "census": _scan("scan", est),
        "cross_venue": _scan("claims", alias_all, scope=dark)}, now=now)
    assert v["established"] is True and phrase not in v["statement"]
    assert v["by_scan"]["cross_venue"]["unread_scope"] == []
    # the census reads recorded books (no fixture cap): no scope needed
    assert v["by_scan"]["census"]["unread_scope"] == []


def test_the_claim_scan_is_never_ok_when_the_cap_cut_a_cross_venue_pair():
    """Review finding (canonical_claims_db.MAX_FIXTURES): what the fixture
    cap cuts is named on the scan record. A cut cross-venue fixture means
    the scan did not cover every matched pair: PARTIAL, never OK. A cut of
    Kalshi-only fixtures (cross-venue ones are read first) stays OK with the
    cut named in its why."""
    from sportsassets.agents import adriana_runner as RUN
    assert RUN.claims_scan_status({"markets_read": 0}) == (
        "NO_EVIDENCE", "NO_ESTABLISHED_KALSHI_FIXTURE_WITH_A_READABLE_BOOK_"
                       "IN_THE_CLAIM_WINDOW")
    base = {"markets_read": 40, "scope": {
        "in_window": 101, "in_window_cross_venue": 90, "cut_by_cap": 21,
        "cut_by_cap_cross_venue": 10}}
    assert RUN.claims_scan_status(base) == (
        "PARTIAL", "FIXTURE_CAP_CUT_10_CROSS_VENUE_OF_90_IN_WINDOW")
    kalshi_only = {"markets_read": 40, "scope": {
        "in_window": 101, "in_window_cross_venue": 19, "cut_by_cap": 21,
        "cut_by_cap_cross_venue": 0}}
    assert RUN.claims_scan_status(kalshi_only) == (
        "OK", "FIXTURE_CAP_CUT_21_KALSHI_ONLY_OF_101_IN_WINDOW")
    assert RUN.claims_scan_status({"markets_read": 40, "scope": {
        "in_window": 65, "cut_by_cap": 0, "cut_by_cap_cross_venue": 0}}) \
        == ("OK", None)


# ═════════════════════════════════════════════════════════════════════
# 5 · A REAL PASS ON POSTGRES
# ═════════════════════════════════════════════════════════════════════

async def _tx():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    return conn, tx


@pg
def test_the_cross_venue_scan_records_its_pairs_and_names_the_missing_pmus_book():
    """A Kalshi game fixture mapped to its PMUS moneyline (production shape:
    research-sql 37875105097 C -- the 19 mapped PMUS slugs had no recorded
    book within the window): the claim scan reads both venues, prices the
    cross-market near complements, records them REFUSED with their policy
    codes, and names the PMUS leg's missing book per venue -- the missing
    half of the cross-venue scan is evidence, never a silent success."""
    import asyncpg

    from sportsassets import canonical_claims_db as CDB
    from sportsassets import kalshi_market_data as KMD
    from sportsassets.api import command_venues as V
    from sportsassets.workers import kalshi_market_data as W

    # identifiers of this test only: market_plane.rules keeps a process-
    # local fingerprint cache (_SEEN) that a rolled-back upsert must not
    # leave behind for another test's identical row (cleared in `finally`)
    ev = "KXMLBGAME-99OCT072000TBNYY"
    slug = "aec-mlb-tb-nyy-2099-10-07"
    kev = {"status": "ESTABLISHED", "settlement": dict(FAIR),
           "verification_sources": ["MLB"]}

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            for t in ("kalshi_books_current", "kalshi_fixtures_current"):
                await c.execute("DELETE FROM %s" % t)
            now = time.time()
            assert await W.persist_fee_terms(c, [{
                "id": "test-rc6-adr-%d" % int(now * 1e6),
                "fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1,
                "scheduled_ts": "2025-10-04T07:00:00Z",
                "series_ticker": "KXMLBGAME"}], kind="SERIES_CHANGE") == 1
            start = now + 3 * 3600
            k = KMD.KalshiFixture(
                event_ticker=ev, series_ticker="KXMLBGAME", sport="BASEBALL",
                league="MLB", start_epoch=start, home_id="H", away_id="A",
                home_code="NYY", away_code="TB", tie_ticker=None,
                team_tickers=(ev + "-NYY", ev + "-TB"),
                outcome_kind="TWO_WAY", status="ESTABLISHED", reasons=(),
                milestone_id="m1")
            await W.persist_fixture(c, k, {"status": "ESTABLISHED",
                                           "pmus": {"slug": slug},
                                           "reasons": []})
            for side, abbr in (("ORDER_INTENT_BUY_LONG", "tb"),
                               ("ORDER_INTENT_BUY_SHORT", "nyy")):
                await c.execute(
                    "INSERT INTO us_premap (identifier, market_slug, "
                    " side_norm, intent, team_abbr, team_league, "
                    " game_start) VALUES ($1, $2, $3, $4, $5, 'mlb', "
                    " to_timestamp($6))", "%s:%s" % (slug, side), slug,
                    side, side, abbr, start)
            for t in (ev + "-NYY", ev + "-TB"):
                ob = {"orderbook_fp": {"no_dollars": [["0.70", "100.00"]],
                                       "yes_dollars": [["0.30", "100.00"]]}}
                await W.persist_book(c, t, ev, KMD.book_from_orderbook(
                    ob, observed_at=now), {})
                await RULES.upsert(c, [{
                    "contract_id": "kalshi:%s" % t, "venue": "KALSHI",
                    "rules_published": True, "rules_field": "rules_primary",
                    "rules_sha256": "%064d" % len(t),
                    "rules_text": "test rules for %s" % t,
                    "rules_secondary": None, "parse_status": "ESTABLISHED",
                    "evidence": kev, "parser_version": "TEST",
                    "source": "TEST"}], now=now)
            await RULES.upsert(c, [{
                "contract_id": slug, "venue": "POLYMARKET_US",
                "rules_published": True, "rules_field": "description",
                "rules_sha256": "%064d" % 7, "rules_text": "test",
                "rules_secondary": None, "parse_status": "ESTABLISHED",
                "evidence": {"status": "ESTABLISHED", "settlement": dict(
                    FAIR, postponement_window_hours=336.0),
                    "verification_sources": ["the relevant governing body"]},
                "parser_version": "TEST", "source": "TEST"}], now=now)
            later = now + 1.0
            cen = await CDB.claims_census(c, now=later)
            v = cen["venues"]
            assert v["KALSHI"]["status"] == "SUPPORTED"
            assert v["KALSHI"]["fresh"] == v["KALSHI"]["aliases"] == 4
            assert v["POLYMARKET_US"]["status"] == "UNAVAILABLE"
            assert v["POLYMARKET_US"]["why"] == \
                "NO_RECORDED_BOOK_FOR_ANY_ALIAS_IN_THE_WINDOW"
            assert v["POLYMARKET_US"]["markets_without_book_n"] == 1
            bs = cen["census"]["book_sources"]
            assert bs["POLYMARKET_US"]["markets_without_book"] == [slug]
            # the missing PMUS book counts against the scan's freshness
            assert cen["census"]["markets_read"] == 6
            assert cen["books_fresh"] == 4
            vt = cen["census"]["void_terms"]
            assert (vt["aliases"], vt["established"]) == (6, 6)
            assert cen["census"]["void_terms_established"] is True
            # every near complement priced and REFUSED, never an opportunity
            assert cen["opportunities"] == []
            near = cen["census"]["near_complement_pairs"]
            assert near >= 1 and len(cen["refusals"]) >= near
            assert cen["census"]["by_topology"].get("CROSS_VENUE", 0) >= 1
            sid = "adr-claims-rc6-%d" % int(now * 1000)
            rec = await AD.record(c, cen, started=now, finished=later,
                                  scan_id=sid, status="OK", why=None)
            assert rec["created"]
            row = await c.fetchrow(
                "SELECT markets_read, books_fresh, structures_considered, "
                "       refusals_total, venues, by_code FROM "
                " adriana_arb_scans WHERE scan_id = $1", sid)
            assert (row["markets_read"], row["books_fresh"]) == (6, 4)
            assert json.loads(row["venues"])["POLYMARKET_US"][
                "markets_without_book_n"] == 1
            assert json.loads(row["by_code"])["near_complement_pairs"] == near
            codes = [r["primary_code"] for r in await c.fetch(
                "SELECT primary_code FROM adriana_arb_refusals WHERE "
                " scan_id = $1", sid)]
            assert len(codes) == row["refusals_total"]
            assert all(TT.TABLE.get(x) for x in codes), codes
            data = await V.read(c, now=later)
            assert data["arbitrage"]["scan"]["scan_id"] == sid
            assert all(r["primary_code"] for r in data["arbitrage"]["refusals"])
            blk = await CR.arbitrage_block(c)
            cv = blk["scans"]["cross_venue"]
            assert cv["scan_id"] == sid and cv["near_complement_pairs"] == near
            assert cv["book_sources"]["POLYMARKET_US"]["fresh"] == 0
        finally:
            await tr.rollback()
            await c.close()
            for cid in ("kalshi:%s-NYY" % ev, "kalshi:%s-TB" % ev, slug):
                RULES._SEEN.pop(cid, None)
    asyncio.run(go())


@pg
def test_a_real_pass_reads_captured_terms_records_both_scans_and_reads_back():
    from sportsassets.agents import adriana_runner as RUN
    from sportsassets.agents import registry as R
    from sportsassets.api import command_floor as F

    base = "tsc-nba-lal-bos-2099-01-01-%s"

    async def go():
        conn, tx = await _tx()
        try:
            for t in ("kalshi_fixtures_current", "kalshi_books_current"):
                await conn.execute("DELETE FROM %s" % t)
            # THIS test's universe only: the pass reads every recorded book
            # in its window and the readbacks count every scan and the void
            # task, so rows other tests committed (CI 37927445675 on
            # 5d83e0de: a recorded over/under book with no captured rules
            # made the census "not established") are hidden -- inside this
            # transaction, which is always rolled back. The book table is
            # append-only (its trigger), hence the replica role, for these
            # deletes alone.
            await conn.execute("SET LOCAL session_replication_role = replica")
            for t in ("paper_book_observations", "adriana_arb_refusals",
                      "adriana_arb_opportunities", "adriana_arb_scans"):
                await conn.execute("DELETE FROM %s" % t)
            await conn.execute("DELETE FROM agent_tasks WHERE task_id = "
                               "'adriana-task-void-terms'")
            await conn.execute("SET LOCAL session_replication_role = origin")
            now = time.time()
            for line, ask, bid, age in (("210pt5", "0.40", "0.38", 3),
                                        ("211pt5", "0.49", "0.47", 2)):
                slug = base % line
                await conn.execute(
                    "INSERT INTO paper_book_observations (us_market_slug, "
                    " observed_at, source, bids, offers, read_basis) VALUES "
                    " ($1, to_timestamp($2), 'TEST', $3::jsonb, $4::jsonb, "
                    " 'TEST')", slug, now - age, json.dumps([_lv(bid, 50)]),
                    json.dumps([_lv(ask, 50)]))
                # the premap capture of THIS contract's own listing
                m = dict(LISTINGS[NBA_TOTAL], slug=slug)
                await RULES.upsert(conn, [RULES.pmus_row(m)], now=now)
            await R.ensure_identities(conn)
            s = await RUN.pass_once(conn, now=now)
            assert s["status"] == "CENSUS_RECORDED", s
            assert s["phase_errors"] == {}, s
            scan = await conn.fetchrow(
                "SELECT * FROM adriana_arb_scans WHERE scan_id = $1",
                s["scan_id"])
            bc = json.loads(scan["by_code"])
            assert bc["void_terms_established"] is True
            assert bc["void_terms"]["established"] == 2
            assert bc["by_primary_code"].get(AD.R_VOID_STATE_FLOOR) == 1
            assert AD.VOID_TERMS_NOT_ESTABLISHED not in bc["by_code"]
            assert scan["opportunities"] == 0
            ref = await conn.fetchrow(
                "SELECT primary_code, detail FROM adriana_arb_refusals "
                " WHERE scan_id = $1 AND primary_code = $2",
                s["scan_id"], AD.R_VOID_STATE_FLOOR)
            det = json.loads(ref["detail"])
            assert det["conditional_on"] == AD.HYPOTHESIS["label"]
            assert all(v["citation"]["rules_sha256"]
                       for v in det["void_terms"].values())
            # the claim-first scan is recorded on THIS pass too: it read no
            # Kalshi fixture, and says so
            c = await conn.fetchrow(
                "SELECT status, why, markets_read FROM adriana_arb_scans "
                " WHERE scan_id = $1", s["claims"]["scan"])
            assert c["status"] == "NO_EVIDENCE" and c["why"].startswith(
                "NO_ESTABLISHED_KALSHI_FIXTURE")
            # no void-terms blocker: every contract read has its terms
            assert await conn.fetchval(
                "SELECT count(*) FROM agent_tasks WHERE task_id = "
                " 'adriana-task-void-terms'") == 0
            # completion: computed from the scans, both listed with ages
            blk = await CR.arbitrage_block(conn)
            assert blk["latest_scan"]["scan_id"] == s["claims"]["scan"]
            assert blk["scans"]["census"]["scan_id"] == s["scan_id"]
            assert blk["void_terms"]["by_scan"]["census"]["state"] == \
                "ESTABLISHED"
            assert blk["void_terms"]["by_scan"]["cross_venue"]["state"] == \
                "READ_NO_CONTRACT"
            # the census's contracts are established, but the cross-venue
            # scan read none: missing evidence is never neutral, so the
            # category's terms are NOT established and the words stay
            assert blk["void_terms"]["established"] is False
            assert "void terms not established" in blk["fail_closed"]
            assert ("cross_venue: void terms not established (the scan read "
                    "no contract; status NO_EVIDENCE") in blk["fail_closed"]
            assert "census: void terms established for 2 of 2" in \
                blk["fail_closed"]
            assert blk["verdict"] == "NO_ELIGIBLE_ARB"
            assert blk["authority"] == "SHADOW_ONLY"
            # the floor counts ONE pass, not one per recorded scan
            floor = await F.build_floor(conn, now=now + 5)
            a = next(x for x in floor["agents"] if x["agent"] == "ADRIANA")
            mon = {m["label"]: m["value"] for m in a["monitor"]}
            assert mon["Census passes (24h)"] == 1
        finally:
            await tx.rollback()
            await conn.close()
            for line in ("210pt5", "211pt5"):
                RULES._SEEN.pop(base % line, None)
    asyncio.run(go())


@pg
def test_the_fixture_cap_reads_cross_venue_first_and_names_every_cut():
    """Review finding (canonical_claims_db.fixtures): the claim scan read at
    most MAX_FIXTURES ESTABLISHED Kalshi fixtures ordered by start time,
    BEFORE dropping unreadable ones, and nothing counted what the cap cut
    (production research-sql 37876834482 B, 02:55Z: 101 readable fixtures
    in the window, 19 mapped to PMUS; about 21 cut silently, from both the
    numerator and the denominator of the scan's fresh-book count).

    Here MAX_FIXTURES + 3 fixtures are in the window: MAX - 1 Kalshi-only
    with no readable book start first, then 2 Kalshi-only readable, then 2
    mapped to PMUS (one without a premap identity) start LAST -- the
    deployed order cut both cross-venue fixtures. Now the cap is spent
    cross-venue first, then readable, and every fixture not priced is
    counted and named by why, on the record and in the completion readback.
    No bound changes: the cap, the window and the book bounds are the
    same."""
    import asyncpg

    from sportsassets import canonical_claims_db as CDB
    from sportsassets import kalshi_market_data as KMD
    from sportsassets.agents import adriana_runner as RUN
    from sportsassets.workers import kalshi_market_data as W

    cap = CDB.MAX_FIXTURES
    n_dark = cap - 1

    def fixture(ev, start):
        return KMD.KalshiFixture(
            event_ticker=ev, series_ticker="KXMLBGAME", sport="BASEBALL",
            league="MLB", start_epoch=start, home_id="H", away_id="A",
            home_code="NYY", away_code="TB", tie_ticker=None,
            team_tickers=(ev + "-NYY", ev + "-TB"), outcome_kind="TWO_WAY",
            status="ESTABLISHED", reasons=(), milestone_id="m-" + ev)

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            for t in ("kalshi_books_current", "kalshi_fixtures_current"):
                await c.execute("DELETE FROM %s" % t)
            now = time.time()
            t0 = now + 3600
            dark = ["KXMLBGAME-99CAP%03dDARK" % i for i in range(n_dark)]
            lit = ["KXMLBGAME-99CAP%03dLIT" % i for i in range(2)]
            cross = ["KXMLBGAME-99CAP%03dXV" % i for i in range(2)]
            slugs = ["aec-mlb-tb-nyy-2099-11-%02d" % (i + 1)
                     for i in range(2)]
            i = 0
            for ev in dark + lit:
                await W.persist_fixture(c, fixture(ev, t0 + 60 * i), None)
                i += 1
            for ev, slug in zip(cross, slugs):
                await W.persist_fixture(
                    c, fixture(ev, t0 + 60 * i),
                    {"status": "ESTABLISHED", "pmus": {"slug": slug},
                     "reasons": []})
                i += 1
            ob = {"orderbook_fp": {"no_dollars": [["0.70", "100.00"]],
                                   "yes_dollars": [["0.30", "100.00"]]}}
            for ev in lit + cross:
                for t in (ev + "-NYY", ev + "-TB"):
                    await W.persist_book(c, t, ev, KMD.book_from_orderbook(
                        ob, observed_at=now), {})
            # the first mapped slug has its premap identity, the second not
            for side, abbr in (("ORDER_INTENT_BUY_LONG", "tb"),
                               ("ORDER_INTENT_BUY_SHORT", "nyy")):
                await c.execute(
                    "INSERT INTO us_premap (identifier, market_slug, "
                    " side_norm, intent, team_abbr, team_league, "
                    " game_start) VALUES ($1, $2, $3, $4, $5, 'mlb', "
                    " to_timestamp($6))", "%s:%s" % (slugs[0], side),
                    slugs[0], side, side, abbr, t0 + 60 * (n_dark + 2))
            later = now + 1.0
            scope = CDB.new_scope()
            got = await CDB.assemble(c, now=later, scope=scope)
            # every cross-venue fixture is read; the cap cuts the last 3
            # Kalshi-only fixtures without a readable book, by name
            assert scope["in_window"] == cap + 3
            assert scope["in_window_cross_venue"] == 2
            assert scope["read"] == cap and scope["read_cross_venue"] == 2
            assert scope["cut_by_cap"] == 3
            assert scope["cut_by_cap_cross_venue"] == 0
            assert scope["cut_by_cap_readable"] == 0
            assert scope["cut_by_cap_named"] == dark[-3:]
            assert scope["no_readable_kalshi_book"] == cap - 4
            assert scope["no_readable_kalshi_book_named"] == \
                dark[:CDB.SCOPE_NAMED_MAX]
            # the mapped slug without an identity: its PMUS leg is not read,
            # and that is named, never silent
            assert scope["pmus_identity_missing"] == 1
            assert scope["pmus_identity_missing_named"] == [slugs[1]]
            assert scope["scanned"] == len(got) == 4
            assert scope["scanned_cross_venue"] == 1
            venues = {i.venue for _fx, _b, insts in got for i in insts}
            assert venues == {"KALSHI", "POLYMARKET_US"}
            # the claim scan carries it: on the record and in completion
            cen = await CDB.claims_census(c, now=later)
            sc = cen["census"]["scope"]
            assert (sc["cut_by_cap"], sc["cut_by_cap_named"]) == (
                3, dark[-3:])
            status, why = RUN.claims_scan_status(cen["census"])
            assert (status, why) == (
                "OK", "FIXTURE_CAP_CUT_3_KALSHI_ONLY_OF_%d_IN_WINDOW"
                % (cap + 3))
            sid = "adr-claims-rc6cap-%d" % int(now * 1000)
            rec = await AD.record(c, cen, started=now, finished=later,
                                  scan_id=sid, status=status, why=why)
            assert rec["created"]
            bc = json.loads(await c.fetchval(
                "SELECT by_code FROM adriana_arb_scans WHERE scan_id = $1",
                sid))
            assert bc["scope"]["cut_by_cap"] == 3
            assert bc["scope"]["cut_by_cap_named"] == dark[-3:]
            blk = await CR.arbitrage_block(c)
            cv = blk["scans"]["cross_venue"]
            assert cv["scan_id"] == sid and cv["why"] == why
            assert cv["scope"]["cut_by_cap"] == 3
            assert cv["scope"]["in_window_cross_venue"] == 2
            assert cv["scope"]["read_cross_venue"] == 2
            assert cv["scope"]["max_fixtures"] == cap
            # the void terms name what the read did not reach: the mapped
            # PMUS market it could not read (the 3 cut fixtures had no
            # readable book, so they form no structure)
            vt = blk["void_terms"]["by_scan"]["cross_venue"]
            assert vt["unread_scope"] == [
                "1 mapped PMUS market(s) not read, no premap identity "
                "(%s)" % slugs[1]]
            assert blk["void_terms"]["established"] is False
            assert "void terms not established" in blk["fail_closed"]
        finally:
            await tr.rollback()
            await c.close()
    asyncio.run(go())
