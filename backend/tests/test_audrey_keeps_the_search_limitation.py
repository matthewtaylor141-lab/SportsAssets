"""AUDREY EVALUATES THE ALTERNATIVES ACTUALLY CAPTURED AT DECISION TIME.

Xavier's decision may record `reasoning.xavier_ladder.search_completeness`
(contracts discovered, examined, excluded with reasons, left unexamined,
and why the search ended). These pin, on SYNTHETIC 2032 ledger rows:

  * the limitation is kept and shown on every affected decision review;
  * a search that did not end COMPLETE supports only 'best among
    examined', never 'best available'; a decision that does not record it
    is SEARCH_COMPLETENESS_NOT_RECORDED, never assumed complete;
  * a contract first seen LATER (a later decision names it) is recorded as
    NOT_CONSIDERED_AT_DECISION_TIME with its discovery time, never as an
    executable, eligible or considered alternative of the earlier decision,
    and never as a missed opportunity -- it may only sit in
    NOT_OBSERVED_OR_UNEVALUABLE;
  * the report states the counts, and an incomplete search is a named
    evidence gap and can be an improvement candidate.
"""
from __future__ import annotations

import datetime as dt

import pytest

from sportsassets.agents import audrey_audit as AA
from tests import audrey_helpers as H

pg = H.pg
NY = "America/New_York"
D = dt.date(2032, 3, 16)


def _bounds():
    from zoneinfo import ZoneInfo
    return AA.day_bounds(D, ZoneInfo(NY))


D0, D1 = _bounds()
LIMITED = {"xavier_ladder": {"search_completeness": {
    "contracts_discovered": 12, "contracts_examined": 4,
    "excluded": {"NO_EXECUTABLE_DEPTH": 2, "NOT_PRICEABLE": 1},
    "left_unexamined": 5, "ended": "READ_BUDGET_EXHAUSTED"}}}


@pytest.fixture(autouse=True)
def _tz(monkeypatch):
    monkeypatch.setenv(AA.TZ_ENV, NY)


@pytest.fixture()
async def db():
    if not H.DSN:
        pytest.skip("needs RN1X_TEST_DSN")
    async with H.connect() as c:
        if not await AA.has_schema(c):
            pytest.skip("migration 155 is not in this database")
        await H.ensure_core_tables(c)
        await H.purge(c)
        yield c
        await H.purge(c)


def test_completeness_is_read_as_recorded_and_never_assumed():
    got = AA.search_completeness({"reasoning": LIMITED})
    assert got["status"] == "INCOMPLETE:READ_BUDGET_EXHAUSTED"
    assert got["supports"] == AA.SUPPORTS_BEST_EXAMINED
    assert (got["discovered"], got["examined"], got["excluded"],
            got["left_unexamined"]) == (12, 4, 3, 5)
    none = AA.search_completeness({"reasoning": {"why": "older row"}})
    assert none["status"] == AA.SC_NOT_RECORDED and none["limited"] is True
    assert none["supports"] == AA.SUPPORTS_UNKNOWN
    full = AA.search_completeness({"reasoning": {"xavier_ladder": {
        "search_completeness": {"ended": "COMPLETE",
                                "contracts_discovered": 3,
                                "contracts_examined": 3}}}})
    assert full["supports"] == AA.SUPPORTS_BEST_AVAILABLE
    for end in ("LIMIT_REACHED", "DEADLINE"):
        x = AA.search_completeness({"reasoning": {"xavier_ladder": {
            "search_completeness": {"ended": end}}}})
        assert x["supports"] == AA.SUPPORTS_BEST_EXAMINED


def test_an_incomplete_search_can_be_an_improvement_candidate():
    rep = {"xavier": {"decisions": 6, "search_completeness": {
        "by_status": {"INCOMPLETE:DEADLINE": 4, "COMPLETE": 2},
        "decisions_not_recorded": 0, "contracts_left_unexamined": 9}}}
    got = AA.proposals(rep, {"evidence_gap_alert_fraction": 0.25})
    assert [p["change_class"] for p in got["proposals"]] == [
        "XAVIER_SEARCH_BUDGET"]


@pg
async def test_a_limited_search_and_a_later_contract_are_not_rewritten(db):
    settle = D0 + 20 * 3600
    pos = await H.position(db, 1, fill_at=D0 + 3600, settle_at=settle)
    # THE CONTRACT DISCOVERED LATER, which settled profitably (a funded leg
    # on it was settled by the venue at 1)
    later_leg = await H.position(db, 9, fill_at=D0 + 3600, settle_at=settle)
    later_slug = later_leg["slug"]
    x1 = await H.decide(db, pos, decided=D0 + 7200, p=0.6,
                        reasoning=LIMITED)
    t2 = D0 + 4 * 3600
    x2 = await H.decide(db, pos, decided=t2, p=0.6, alts=[
        {"action": "HOLD", "qty": 10, "value_usd": 1.0,
         "cash_at_settlement_usd": 6.0},
        {"action": "ACQUIRE_HEDGE", "value_usd": 0.2, "units": 10,
         "hedge_slug": later_slug, "hedge_order_intent": H.LONG,
         "cost_usd": 4.0, "fees_usd": 0.1, "limit_price": 0.4,
         "depth": 30, "quote_at": t2 - 3}])
    # AND A LATER CATALOGUE/COLLECTION READ of the same fixture, between
    # the two decisions, names another contract
    t_att = D0 + 3 * 3600
    await db.execute(
        "INSERT INTO bettor_pair_observation_attempts (pass_id, "
        " attempted_at, finished_at, candidate_source, us_market_slug, side,"
        " fixture, outcome, refusal) VALUES ($1,$2,$2,'VENUE_CATALOGUE',$3,"
        " $4,$5,'REFUSED','SYNTHETIC_TEST')", H.PFX + "pass-late",
        H.ts(t_att), H.PFX + "catalogue-slug", H.LONG, pos["fixture"])
    got = await AA.audit_day(db, day=D, now=D1 + 3600)
    rep = got["report"]
    rows = {r["xavier_decision_id"]: r for r in
            rep["xavier"]["decision_rows"]}
    r1, r2 = rows[x1], rows[x2]
    # (a) THE LIMITATION IS KEPT ON THE REVIEW
    assert r1["search_completeness"]["status"] == \
        "INCOMPLETE:READ_BUDGET_EXHAUSTED"
    assert r1["search_completeness"]["left_unexamined"] == 5
    assert r1["search_completeness"]["excluded_reasons"] == {
        "NO_EXECUTABLE_DEPTH": 2, "NOT_PRICEABLE": 1}
    # (b) BEST AMONG EXAMINED, NEVER BEST AVAILABLE
    assert r1["quality_scope"] == AA.SUPPORTS_BEST_EXAMINED
    assert r2["search_completeness"]["status"] == AA.SC_NOT_RECORDED
    assert r2["quality_scope"] == AA.SUPPORTS_UNKNOWN
    # (c) THE LATER CONTRACT: NOT CONSIDERED AT X1's INSTANT
    nc = {n["contract"]: n for n in r1["not_considered_at_decision_time"]}
    assert set(nc) == {later_slug, H.PFX + "catalogue-slug"}
    for n in nc.values():
        assert n["status"] == AA.NOT_CONSIDERED
        assert n["executable_at_decision"] is False
        assert n["eligible_at_decision"] is False
        assert n["category"] == AA.UNEVALUABLE
    assert nc[later_slug]["discovered_at"] == pytest.approx(t2)
    assert nc[later_slug]["source"] == "LATER_XAVIER_DECISION:%s" % x2
    cat = nc[H.PFX + "catalogue-slug"]
    assert cat["discovered_at"] == pytest.approx(t_att)
    assert cat["source"].startswith("LATER_COLLECTION_ATTEMPT:")
    assert r2["not_considered_at_decision_time"] == []
    ev = rep["evidence"]
    ks_x1 = [r for r in ev[AA.KNOWN_SETTLEMENT]["rows"]
             if r.get("xavier_decision_id") == x1]
    assert ks_x1, "X1's own captured exit is still estimated"
    assert all(r["frozen"]["instrument"] != later_slug for r in ks_x1)
    assert all(r["action"] != "ACQUIRE_HEDGE" for r in ks_x1)
    un = [r for r in ev[AA.UNEVALUABLE]["rows"]
          if r.get("contract") == later_slug]
    assert len(un) == 1 and un[0]["xavier_decision_id"] == x1
    assert un[0]["why"] == AA.NOT_CONSIDERED
    assert un[0]["discovered_at"] == pytest.approx(t2)
    assert AA.NOT_CONSIDERED in ev[AA.UNEVALUABLE]["reasons"]
    # never a missed opportunity, never actual
    assert rep["derek"]["entries"].get("missed_opportunities", {}).get(
        "count", 0) == 0
    assert all(r.get("contract") != later_slug
               for r in ev[AA.ACTUAL]["rows"])
    # X2 DID consider it at ITS instant: its estimate is X2's, labelled
    ks_x2 = [r for r in ev[AA.KNOWN_SETTLEMENT]["rows"]
             if r.get("xavier_decision_id") == x2
             and r["action"] == "ACQUIRE_HEDGE"]
    assert len(ks_x2) == 1 and ks_x2[0]["could_have_filled"] == "UNPROVEN"
    # THE COUNTS
    sc = rep["xavier"]["search_completeness"]
    assert sc["by_status"] == {"INCOMPLETE:READ_BUDGET_EXHAUSTED": 1,
                               AA.SC_NOT_RECORDED: 1}
    assert sc["decisions_limited"] == 2
    assert sc["decisions_not_recorded"] == 1
    assert (sc["contracts_discovered"], sc["contracts_examined"],
            sc["contracts_excluded"], sc["contracts_left_unexamined"]) == (
                12, 4, 3, 5)
    assert sc["later_discovered_contracts"] == 2
    assert sc["supports"] == {AA.SUPPORTS_BEST_EXAMINED: 1,
                              AA.SUPPORTS_UNKNOWN: 1}
    # (d) A NAMED EVIDENCE GAP
    gaps = {g["gap"]: g for g in rep["data_quality"]["gaps"]}
    assert gaps["XAVIER_SEARCH_INCOMPLETE"]["decisions_limited"] == 2
    assert gaps["XAVIER_SEARCH_INCOMPLETE"][
        "later_discovered_contracts"] == 2
    checked = {c["rule"]: c for c in rep["improvements"]["checked"]}
    assert checked["XAVIER_SEARCH_COMPLETENESS"]["incomplete"] == 1
