"""THE LOST OPPORTUNITY LEDGER, THE OPPORTUNITY SCORE AND THE HORIZON
FORECASTS (migration 220).

  * a hindsight winner with no decision-time edge is GOOD_REFUSAL (never
    FALSE); the class is identical whatever the settlement says;
  * a positive executable net EV refused by an identified defect is
    FALSE_REFUSAL with the defect named; the same EV refused by a control
    whose condition the record shows is GOOD_REFUSAL;
  * missing decision-time inputs -> UNKNOWABLE; an unsettled market (or one
    settled before the decision) -> no row;
  * the runner is idempotent (one row per decision per classifier version);
    the coverage ledger's refusals are one row per market, UNKNOWABLE;
  * the score is transparent and decomposed; missing inputs -> UNAVAILABLE
    with the reason; a measured zero is 0;
  * horizon forecasts fail closed (UNAVAILABLE, no numbers) and are
    UNPROVEN otherwise; rounded (no false precision);
  * the reads answer the RESEARCH envelope, EMPTY before a run, and a failed
    read is UNAVAILABLE, never zeros.

ALL DATA IS SYNTHETIC (tests/lol_fixture.py), inside rolled-back
transactions.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

import asyncpg
import pytest

from sportsassets.lost_opportunity import classify as CL
from sportsassets.lost_opportunity import horizons as HZ
from sportsassets.lost_opportunity import runner as LR
from sportsassets.lost_opportunity import score as SC

try:
    from tests import intel_fixture as F
    from tests import lol_fixture as X
    from tests import paper_harness as H
    from tests import pos_fixture as P
except ImportError:                                             # pragma: no cover
    import intel_fixture as F
    import lol_fixture as X
    import paper_harness as H
    import pos_fixture as P

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
DAY = 86400.0
HOUR = 3600.0


# ── pure ────────────────────────────────────────────────────────────

def _dec(**kw):
    d = {"source": CL.PAPER_SOURCE, "decision_ref": "paperdec:x",
         "decision_id": "paperdec:x", "us_market_slug": "m", "fixture": "f",
         "holding_side": "LONG", "p_internal": 0.6, "p_pinnacle": 0.58,
         "p_blended": 0.59, "proposed_qty": None, "book": X.book_rec(),
         "label": {"competition": "MLB", "unknown": {}},
         "pinnacle": {"p": 0.58, "qualified": True, "age_s": 10.0,
                      "limit_s": 60.0, "at": 1.0},
         "valuation": None}
    d.update(kw)
    return d


def _settle(outcome, side="LONG"):
    return {"settlement_id": "papersettle:s", "holding_side": side,
            "outcome": outcome,
            "payout_per_contract": 1.0 if outcome == "WON" else 0.0,
            "settled_at": 2.0}


def test_a_hindsight_winner_without_edge_is_never_a_false_refusal():
    d = _dec(refusals=[CL.R_BELOW, CL.R_NET],
             policy_decision=X.pd_fig(-0.5, edge=0.02))
    got = CL.classify(d)
    assert got["classification"] == CL.GOOD
    assert got["attribution"] == "THRESHOLD"
    hyp = CL.hypothetical_pnl(d, got["economics"], _settle("WON"))
    assert hyp["label"] == "HYPOTHETICAL" and hyp["value"] == pytest.approx(
        10 * 1.0 - 5.0 - 0.10)
    # the settlement never feeds the class
    assert CL.classify(dict(d, settlement=_settle("LOST")))[
        "classification"] == got["classification"]


def test_positive_ev_refused_by_a_defect_is_a_false_refusal_named():
    stale_wrong = _dec(refusals=[CL.R_STALE],
                       policy_decision=X.pd_fig(0.8))
    got = CL.classify(stale_wrong)
    assert got["classification"] == CL.FALSE
    assert got["defect"] == ("CONTROL_FIRED_WITHOUT_ITS_CONDITION:"
                             "PROBABILITY_EVIDENCE_STALE")
    assert got["attribution"] == "FRESHNESS"
    raised = CL.classify(_dec(refusals=[CL.R_RAISED],
                              policy_decision=X.pd_fig(0.8)))
    assert raised["classification"] == CL.FALSE
    assert raised["defect"] == CL.R_RAISED
    contra = CL.classify(_dec(refusals=[CL.R_BELOW],
                              policy_decision=X.pd_fig(0.8)))
    assert contra["classification"] == CL.FALSE
    assert contra["defect"].startswith("MARKET_REFUSAL_CONTRADICTS")


def test_the_same_ev_refused_by_a_supported_control_is_good():
    stale = _dec(refusals=[CL.R_STALE], policy_decision=X.pd_fig(0.8),
                 pinnacle={"p": 0.58, "age_s": 120.0, "limit_s": 60.0,
                           "at": 1.0})
    got = CL.classify(stale)
    assert got["classification"] == CL.GOOD
    assert got["reason"].startswith("CORRECT_CONTROL PROBABILITY_EVIDENCE")
    off = CL.classify(_dec(refusals=[CL.R_ENTRIES_DISABLED],
                           policy_decision=X.pd_fig(0.8, switch=False)))
    assert off["classification"] == CL.GOOD
    assert off["attribution"] == "EXPLICIT_POLICY"
    ident = CL.classify(_dec(refusals=[CL.R_IDENTITY], fixture=None,
                             policy_decision=None, book=None))
    assert ident["classification"] == CL.GOOD
    assert ident["attribution"] == "IDENTITY_MAPPING"


def test_missing_decision_time_inputs_are_unknowable():
    got = CL.classify(_dec(refusals=[CL.R_NO_RESEARCH_MODEL],
                           p_internal=None, p_blended=None, book=None,
                           policy_decision=None, economics=None))
    assert got["classification"] == CL.UNKNOWABLE
    assert "MISSING_DECISION_TIME_INPUT" in got["reason"]
    assert got["attribution"] == "MISSING_PROBABILITY"
    hyp = CL.hypothetical_pnl(_dec(), got["economics"], _settle("WON"))
    assert hyp["value"] is None and hyp["why"]
    unver = CL.classify(_dec(refusals=[CL.R_NOT_REAL],
                             policy_decision=X.pd_fig(0.8)))
    assert unver["classification"] == CL.UNKNOWABLE


def test_coverage_ledger_refusals_are_unknowable_and_attributed():
    got = CL.classify({"source": CL.COVERAGE_SOURCE,
                       "refusals": ["VENUE_ABSENT"], "stage": "3_IDENTITY"})
    assert got["classification"] == CL.UNKNOWABLE
    assert got["attribution"] == "IDENTITY_MAPPING"
    assert CL.attribution("SOMETHING_NEW", "7_RISK") == "RISK"
    assert CL.attribution("SOMETHING_NEW") == "UNATTRIBUTED"
    for code in CL.ATTRIBUTION_OF:
        assert CL.attribution(code) in CL.ATTRIBUTIONS


def test_opposite_side_settlement_prices_the_complement():
    d = _dec(holding_side="SHORT")
    ppc, basis = CL.side_payout(d, _settle("WON", side="LONG"))
    assert ppc == 0.0 and basis.endswith("BINARY_COMPLEMENT")
    ppc, basis = CL.side_payout(d, dict(_settle("WON", side="LONG"),
                                        outcome="VOID_REFUND"))
    assert ppc is None


def test_the_score_is_transparent_and_decomposed():
    cand = {"candidate_id": "c", "status": "MEASURED",
            "executable_opportunity_dollars": 5.0,
            "executable_capacity_usd": 200.0, "decided_at": 1000.0 * HOUR,
            "event_start_at": 1002.0 * HOUR, "capacity_ceiling_usd": 400.0}
    lags = [(0.0, 3 * HOUR)] * 6
    got = SC.score(cand, fill_probability=0.8, fill_basis="b",
                   idle_capital_usd=1000.0, lag_samples=lags)
    assert got["status"] == "MEASURED"
    assert got["capacity_factor"] == 1.0
    assert got["expected_hold_h"] == pytest.approx(5.0)
    assert got["opportunity_score"] == pytest.approx(5.0 * 0.8 / 1000.0)
    comps = got["components"]
    assert set(comps) == {"NET_EV", "EDGE_CONFIDENCE",
                          "CALIBRATION_CONFIDENCE", "EXECUTION_CONFIDENCE",
                          "LIQUIDITY_CAPACITY", "SETTLEMENT_CONFIDENCE",
                          "REGIME_CONFIDENCE", "CORRELATION_RISK_COST"}
    assert comps["NET_EV"]["in_score"] and comps["NET_EV"]["value"] == 5.0
    for k in ("EDGE_CONFIDENCE", "CORRELATION_RISK_COST",
              "REGIME_CONFIDENCE", "SETTLEMENT_CONFIDENCE"):
        assert comps[k]["value"] is None and comps[k]["why"], k
        assert comps[k]["in_score"] is False
    half = SC.score(cand, fill_probability=0.8, idle_capital_usd=100.0,
                    lag_samples=lags)
    assert half["capacity_factor"] == 0.5


def test_a_missing_score_input_is_unavailable_with_its_reason():
    cand = {"candidate_id": "c", "status": "MEASURED",
            "executable_opportunity_dollars": 5.0,
            "executable_capacity_usd": 200.0, "decided_at": 1000.0,
            "event_start_at": 2000.0}
    got = SC.score(cand, fill_probability=None, idle_capital_usd=1000.0,
                   lag_samples=[(0.0, 100.0)] * 6)
    assert got["status"] == "UNAVAILABLE" and got["opportunity_score"] is None
    assert "fill_probability" in got["why"]
    un = SC.score({"candidate_id": "c", "status": "UNAVAILABLE",
                   "why": "NO_RECORDED_BOOK_OBSERVATION"},
                  fill_probability=0.5, idle_capital_usd=10.0)
    assert un["status"] == "UNAVAILABLE"
    assert "NO_RECORDED_BOOK_OBSERVATION" in un["why"]
    zero = SC.score(dict(cand, executable_opportunity_dollars=0.0),
                    fill_probability=None)
    assert zero["status"] == "MEASURED" and zero["opportunity_score"] == 0.0


def test_horizon_forecasts_fail_closed_and_are_unproven_and_rounded():
    now = 1_800_000_000.0
    thin = HZ.build([], book="PAPER", horizon="7D", days=7, now=now,
                    lookback_days=90)
    assert thin["status"] == "UNAVAILABLE" and thin["expected_pnl_usd"] is None
    assert thin["expected_opportunities"] is None
    econs = []
    for i in range(40):
        t = now - (i + 1) * DAY + 3600
        econs.append({"book": "PAPER", "state": "CLOSED",
                      "net_profit_usd": (3.3333 if i % 3 else -1.7777),
                      "released_at": t, "opened_at": t - 7200,
                      "first_fill_at": t - 7200, "capital_committed_usd": 50.0,
                      "segments": [(t - 7200, t, 50.0)]})
    opp = {"candidates": 140, "qualified": 28, "days": 14.0}
    cap = {"idle_capital_usd": 400123.456, "account_capital_usd": 500000.0}
    out = {}
    for key, days in HZ.HORIZONS:
        fc = HZ.build(econs, book="PAPER", horizon=key, days=days, now=now,
                      lookback_days=90, opportunity=opp, capital=cap,
                      capacity_daily=12.0, fill_probability=0.5)
        assert fc["status"] == "UNPROVEN", fc["why"]
        assert fc["p10_pnl_usd"] <= fc["p50_pnl_usd"] <= fc["p90_pnl_usd"]
        assert float(fc["expected_pnl_usd"]).is_integer()
        assert fc["prob_positive"] == round(fc["prob_positive"], 2)
        assert fc["deployable_capital_usd"] == 400123.0
        out[key] = fc
    assert out["7D"]["expected_opportunities"] == 70.0
    # trailing turnover is measured: 29 fills of $50 inside the 30 days
    # (the 30th filled an hour before it); locked capital-hours 29 x 2h x
    # $50 + the 1h of the 30th inside the window, over 720 h
    assert out["30D"]["trailing_30d_committed_usd"] == 1450.0
    assert out["30D"]["trailing_30d_capital_turnover"] == pytest.approx(
        1450.0 / ((29 * 2 * 50.0 + 50.0) / 720.0), abs=0.01)
    assert out["30D"]["expected_qualified_opportunities"] == 60.0
    act = HZ.build(econs, book="ACTUAL", horizon="24H", days=1, now=now,
                   lookback_days=90, opportunity=opp)
    assert act["expected_opportunities"] is None
    assert "PAPER_DECISIONS_ONLY" in act["unmeasured"]["expected_opportunities"]


# ── database ───────────────────────────────────────────────────────

@asynccontextmanager
async def _txn():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        yield conn
    finally:
        await tr.rollback()
        await conn.close()


async def _ledger(conn, ref):
    return await conn.fetchrow(
        "SELECT * FROM lol_ledger WHERE decision_ref = $1", ref)


async def _seed(conn, now):
    acct = await X.account(conn, now=now)
    t = now - 3 * DAY
    out = {}
    out["winner"] = await X.decision(
        conn, acct, at=t, refusals=[CL.R_BELOW, CL.R_NET],
        pd=X.pd_fig(-0.5, edge=0.02), book=X.book_rec())
    await X.settle(conn, slug=out["winner"]["slug"], outcome="WON",
                   at=t + 6 * HOUR, now=now)
    out["defect"] = await X.decision(
        conn, acct, at=t, refusals=[CL.R_STALE], pd=X.pd_fig(0.8),
        book=X.book_rec())
    await X.settle(conn, slug=out["defect"]["slug"], outcome="LOST",
                   at=t + 6 * HOUR, now=now)
    out["missing"] = await X.decision(
        conn, acct, at=t, refusals=[CL.R_NO_RESEARCH_MODEL],
        p_internal=None)
    await X.settle(conn, slug=out["missing"]["slug"], side="SHORT",
                   outcome="WON", at=t + 6 * HOUR, now=now)
    out["open"] = await X.decision(
        conn, acct, at=t, refusals=[CL.R_BELOW], pd=X.pd_fig(-0.1))
    out["before"] = await X.decision(
        conn, acct, at=t, refusals=[CL.R_BELOW], pd=X.pd_fig(-0.1))
    await X.settle(conn, slug=out["before"]["slug"], at=t - HOUR, now=now)
    out["benchmark"] = await X.decision(
        conn, acct, at=t, refusals=[CL.R_BELOW], pd=X.pd_fig(-0.1),
        strategy=F.STRATEGY)
    await X.settle(conn, slug=out["benchmark"]["slug"], at=t + HOUR, now=now)
    return acct, out


@pg
async def test_a_venue_price_close_is_not_a_market_settlement():
    """A paper position closed at the venue's price (SETTLED_AT_VENUE_PRICE)
    is not the contract's resolution: the refused decision on that market
    gets no ledger row from it, and the LEDGER component stays OK (one such
    row used to fail the whole component on lol_ledger's outcome CHECK)."""
    now = time.time()
    async with _txn() as conn:
        acct = await X.account(conn, now=now)
        t = now - 3 * DAY
        d = await X.decision(conn, acct, at=t, refusals=[CL.R_BELOW],
                             pd=X.pd_fig(-0.1), book=X.book_rec())
        await X.settle(conn, slug=d["slug"], outcome="SETTLED_AT_VENUE_PRICE",
                       at=t + 6 * HOUR, now=now)
        got = await LR.run_component(conn, now=now)
        assert got["ran"] and got["components"]["LEDGER"] == "OK", got
        assert await _ledger(conn, d["decision_id"]) is None
        # ...and once the market itself settles, the decision is classified
        await X.settle(conn, slug=d["slug"], outcome="LOST",
                       at=t + 7 * HOUR, now=now)
        again = await LR.run_component(conn, now=now + 60)
        assert again["components"]["LEDGER"] == "OK", again
        row = await _ledger(conn, d["decision_id"])
        assert row is not None and row["settlement_outcome"] == "LOST"


@pg
async def test_the_runner_classifies_settled_refusals_once():
    now = time.time()
    async with _txn() as conn:
        _, s = await _seed(conn, now)
        got = await LR.run_component(conn, now=now)
        assert got["ran"] and got["components"]["LEDGER"] == "OK", got
        w = await _ledger(conn, s["winner"]["decision_id"])
        assert w["classification"] == "GOOD_REFUSAL"
        assert w["hypothetical_pnl_usd"] > 0          # a hindsight winner
        assert w["hypothetical_pnl_label"] == "HYPOTHETICAL"
        assert w["settlement_outcome"] == "WON"
        assert w["league"] == "MLB"
        assert s["winner"]["decision_id"] in w["decision_evidence_ids"]
        d = await _ledger(conn, s["defect"]["decision_id"])
        assert d["classification"] == "FALSE_REFUSAL"
        assert d["defect"].endswith("PROBABILITY_EVIDENCE_STALE")
        assert d["decision_time_net_ev_usd"] == pytest.approx(0.8)
        assert d["hypothetical_pnl_usd"] < 0          # it lost; still FALSE
        m = await _ledger(conn, s["missing"]["decision_id"])
        assert m["classification"] == "UNKNOWABLE"
        assert m["settlement_basis"].endswith("BINARY_COMPLEMENT")
        for k in ("open", "before", "benchmark"):
            assert await _ledger(conn, s[k]["decision_id"]) is None, k
        # idempotent: nothing new, one row per decision per version
        again = await LR.run_component(conn, now=now + 60)
        assert again["ledger"]["written"] == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM lol_ledger WHERE decision_ref = $1",
            s["winner"]["decision_id"]) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM lol_runs WHERE component = 'LEDGER' "
            "   AND status = 'OK'") >= 2


@pg
async def test_coverage_ledger_refusals_are_one_row_per_market():
    now = time.time()
    async with _txn() as conn:
        slug = F.uid("lol-cov-")
        ev = F.uid("ev-")
        for i, at in enumerate((now - 2 * DAY, now - 2 * DAY + 900)):
            await conn.execute(
                "INSERT INTO ext_candidate_outcomes (cycle_id, cycle_at, "
                " sport_key, family, queue_position, provider_event_id, "
                " us_market_slug, stage, outcome, first_refusal, codes) "
                "VALUES ($1,to_timestamp($2),'baseball_mlb','baseball',$3,"
                " $4,$5,'4_SETTLEMENT_SCOPE','REFUSED',"
                " 'VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED','[]'::jsonb)",
                F.uid("cyc-"), at, i, ev, slug)
        await X.settle(conn, slug=slug, at=now - DAY, now=now)
        await LR.run_component(conn, now=now)
        rows = await conn.fetch(
            "SELECT * FROM lol_ledger WHERE source = 'COVERAGE_LEDGER' "
            "   AND us_market_slug = $1", slug)
        assert len(rows) == 1
        assert rows[0]["classification"] == "UNKNOWABLE"
        assert rows[0]["attribution"] == "SETTLEMENT"
        assert rows[0]["league"] == "baseball_mlb"
        assert rows[0]["hypothetical_pnl_usd"] is None
        await LR.run_component(conn, now=now + 60)
        assert await conn.fetchval(
            "SELECT count(*) FROM lol_ledger WHERE us_market_slug = $1",
            slug) == 1


@pg
async def test_the_database_refuses_an_unnamed_false_refusal():
    async with _txn() as conn:
        base = ("INSERT INTO lol_ledger (ledger_id, decision_ref, source, "
                " classifier_version, run_id, classified_at, decided_at, "
                " classification, reason, defect, attribution, "
                " decision_time_net_ev_usd, settlement_evidence_id, "
                " settlement_basis, settled_at, settlement_outcome, "
                " hypothetical_pnl_why, content_sha256) VALUES ($1,$1,"
                " 'PAPER_DECISION','v','r',now(),now(),$2,'x',$3,"
                " 'THRESHOLD',$4,'s','b',now(),'WON','w','h')")
        await conn.execute(base, "ok1", "FALSE_REFUSAL", "DEFECT_X", 1.0)
        for i, args in enumerate((("FALSE_REFUSAL", None, 1.0),
                                  ("FALSE_REFUSAL", "D", -1.0),
                                  ("GOOD_REFUSAL", "D", -1.0))):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(base, "bad%d" % i, *args)
            await sp.rollback()


@pg
async def test_scores_are_written_as_of_the_decision():
    now = time.time()
    async with _txn() as conn:
        acct = await X.account(conn, now=now)
        await P.lag_history(conn, acct, now=now)
        d = await X.decision(conn, acct, at=now - 600, refusals=[CL.R_BELOW],
                             pd=X.pd_fig(-0.1))
        await P.premap(conn, d["slug"], now + 2 * HOUR)
        snap = ("INSERT INTO pos_snapshots (snapshot_id, run_id, component, "
                " book, computed_at, payload, data_sha256, version) VALUES "
                " ($1,'r',$2,$3,to_timestamp($4),$5::jsonb,'s','v')")
        await conn.execute(snap, F.uid("s"), "CAPITAL", "PAPER", now - 3600,
                           '{"idle_capital_usd": 1000.0, "unmeasured": {}}')
        await conn.execute(snap, F.uid("s"), "CAPACITY", "NONE", now - 3600,
                           '{"rates": {"fill_probability": {"value": 0.8, '
                           '"n": 40, "basis": "TEST"}}}')
        await conn.execute(
            "INSERT INTO pos_capacity (capacity_id, candidate_id, run_id, "
            " computed_at, decided_at, us_market_slug, holding_side, status, "
            " executable_opportunity_dollars, executable_capacity_usd, "
            " capacity_ceiling_usd, content_sha256, version) VALUES ($1,$2,"
            " 'r',to_timestamp($3),to_timestamp($4),$5,'LONG','MEASURED',5.0,"
            " 200.0,400.0,'s','v')", F.uid("cap"), d["decision_id"], now,
            now - 600, d["slug"])
        got = await LR.run_component(conn, now=now)
        assert got["components"]["SCORES"] == "OK", got
        r = await conn.fetchrow(
            "SELECT * FROM lol_opportunity_scores_latest "
            " WHERE candidate_id = $1", d["decision_id"])
        assert r["status"] == "MEASURED", r["why"]
        assert r["opportunity_score"] == pytest.approx(
            5.0 * 0.8 * 1.0 / (200.0 * r["expected_hold_h"]))
        comps = P.j(r["components"])
        assert comps["EXECUTION_CONFIDENCE"]["value"] == 0.8
        assert comps["EDGE_CONFIDENCE"]["value"] is None
        again = await LR.run_component(conn, now=now + 60)
        assert again["scores"]["written"] == 0


@pg
async def test_score_candidates_read_event_starts_in_one_batched_query():
    """Production R28b: SCORES failed every cycle on its statement timeout.
    The candidate read looked up each candidate's event start with a
    correlated us_premap subquery; us_premap has no market_slug index, so
    that was a full-table scan per candidate (1,500 x 108k rows, 80 s).
    The start is now read once per cycle, and it is still the latest mapped
    game_start of the slug (a NULL game_start never wins)."""
    from sportsassets.lost_opportunity import reads as R
    now = time.time()
    async with _txn() as conn:
        acct = await X.account(conn, now=now)
        d = await X.decision(conn, acct, at=now - 600, refusals=[CL.R_BELOW],
                             pd=X.pd_fig(-0.1))
        ins = ("INSERT INTO us_premap (identifier, market_slug, side_norm, "
               " game_start, updated_at) VALUES ($1,$2,'HOME',"
               " to_timestamp($3),to_timestamp($4))")
        await conn.execute(ins, F.uid("pm-"), d["slug"], now + HOUR, now - 60)
        await conn.execute(ins, F.uid("pm-"), d["slug"], now + 3 * HOUR, now)
        await conn.execute(
            "INSERT INTO us_premap (identifier, market_slug, side_norm, "
            " game_start, updated_at) VALUES ($1,$2,'AWAY',NULL,"
            " to_timestamp($3))", F.uid("pm-"), d["slug"], now + 30)
        await conn.execute(
            "INSERT INTO pos_capacity (capacity_id, candidate_id, run_id, "
            " computed_at, decided_at, us_market_slug, holding_side, status, "
            " executable_opportunity_dollars, executable_capacity_usd, "
            " capacity_ceiling_usd, content_sha256, version) VALUES ($1,$2,"
            " 'r',to_timestamp($3),to_timestamp($4),$5,'LONG','MEASURED',5.0,"
            " 200.0,400.0,'s','v')", F.uid("cap"), d["decision_id"], now,
            now - 600, d["slug"])
        seen = []

        class Counting:                 # asyncpg's Connection has slots
            async def fetch(self, sql, *a, **k):
                seen.append(sql)
                return await conn.fetch(sql, *a, **k)
        cands = await R.score_candidates(Counting(), now=now,
                                         version=SC.VERSION)
        mine = [c for c in cands if c["candidate_id"] == d["decision_id"]]
        assert len(mine) == 1
        assert mine[0]["event_start_at"] == pytest.approx(now + 3 * HOUR)
        premap_reads = [s for s in seen if "us_premap" in s]
        assert len(premap_reads) == 1, premap_reads
        assert "pos_capacity_latest" not in premap_reads[0]
        assert await R.event_starts(conn, []) == {}


@pg
async def test_the_cycle_runs_the_component_and_its_failure_is_isolated(
        monkeypatch):
    from sportsassets.lost_opportunity import reads as LRD
    from sportsassets.profitability import runner as RUN

    async def boom(*a, **k):
        raise RuntimeError("synthetic ledger failure")

    async with _txn() as conn:
        s = await P.scenario(conn)
        got = await RUN.run_cycle(conn, now=s["now"],
                                  account_id=s["acct"]["account_id"],
                                  include_actual=False)
        assert set(got["components"].values()) == {"OK"}, got
        lol = got["lost_opportunity"]
        assert lol["ran"] and set(lol["components"].values()) == {"OK"}, lol
        hz = await conn.fetch(
            "SELECT book, horizon, status FROM lol_horizon_forecasts "
            " WHERE run_id = $1", lol["run_id"])
        assert {(r["book"], r["horizon"]) for r in hz} == {
            (b, h) for b in ("PAPER", "ACTUAL") for h in ("24H", "7D", "30D")}
        monkeypatch.setattr(LRD, "settled_refusals", boom)
        got2 = await RUN.run_cycle(conn, now=s["now"] + 3600,
                                   account_id=s["acct"]["account_id"],
                                   include_actual=False)
        assert set(got2["components"].values()) == {"OK"}
        c = got2["lost_opportunity"]["components"]
        assert c["LEDGER"] == "FAILED" and c["SCORES"] == "OK"
        err = await conn.fetchval(
            "SELECT error FROM lol_runs WHERE run_id = $1 "
            "   AND component = 'LEDGER'", got2["lost_opportunity"]["run_id"])
        assert "synthetic ledger failure" in err


@pg
async def test_the_kill_switch_and_missing_migration_idle(monkeypatch):
    monkeypatch.setenv("POS_LOL", "off")
    assert (await LR.run_component(None))["ran"] is False
    monkeypatch.delenv("POS_LOL")

    class _No:
        async def fetchval(self, *a):
            return False
    got = await LR.run_component(_No())
    assert got == {"ran": False, "why": "MIGRATION_220_NOT_APPLIED"}


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@pg
async def test_the_reads_serve_research_envelopes(monkeypatch):
    from sportsassets.api import command_lost_opportunity as API

    now = time.time()
    async with _txn() as conn:
        async def pool():
            return _Pool(conn)
        monkeypatch.setattr(API, "_pool", pool)
        for fn in (API.lost_opportunities, API.opportunity_scores,
                   API.forecast_horizons):
            kw = ({"classification": "", "league": "",
                   "classifier_version": CL.VERSION, "limit": 50}
                  if fn is API.lost_opportunities else
                  {"status": "", "limit": 50}
                  if fn is API.opportunity_scores else {})
            e = await fn(**kw)
            assert e["label"] == "RESEARCH"
            assert e["authority"] == "SHADOW_NO_AUTHORITY"
            if e["status"] == "EMPTY":
                assert e["data"] is None and e["why"]
        _, s = await _seed(conn, now)
        await LR.run_component(conn, now=now)
        got = await API.lost_opportunities(classification="", league="",
                                           classifier_version=CL.VERSION,
                                           limit=200)
        assert got["status"] == "OK"
        summ = got["data"]["summary"]
        assert summ["by_class"]["FALSE_REFUSAL"]["n"] >= 1
        assert {"by_league", "by_refusal_reason", "by_attribution",
                "trend_30d", "management"} <= set(summ)
        assert summ["management"]["label"] == "HYPOTHETICAL"
        assert summ["management"][
            "hindsight_winners_correctly_refused_n"] >= 1
        defects = {d["defect"] for d in got["data"]["false_refusal_defects"]}
        assert ("CONTROL_FIRED_WITHOUT_ITS_CONDITION:"
                "PROBABILITY_EVIDENCE_STALE") in defects
        only = await API.lost_opportunities(
            classification="FALSE_REFUSAL", league="",
            classifier_version=CL.VERSION, limit=200)
        assert {r["classification"] for r in only["data"]["rows"]} == {
            "FALSE_REFUSAL"}


async def test_a_failed_read_is_unavailable_not_zeros(monkeypatch):
    from sportsassets.api import command_lost_opportunity as API

    async def broken():
        raise ConnectionError("database down")

    monkeypatch.setattr(API, "_pool", broken)
    got = await API.opportunity_scores(status="", limit=5)
    assert got["status"] == "UNAVAILABLE" and got["data"] is None
    assert "database down" in got["why"]


# ── cand27 integration: Eddie's execution estimate and the attribution ──

OWNER_ATTRIBUTIONS = (
    "SETTLEMENT", "IDENTITY_MAPPING", "FRESHNESS", "LIQUIDITY",
    "EXECUTION_UNCERTAINTY", "THRESHOLD", "RISK", "CAPACITY",
    "KAREN_CHALLENGE", "UNSUPPORTED_LEAGUE", "EXPLICIT_POLICY",
    "MISSING_PROBABILITY", "MISSING_EXECUTABLE_BOOK")


def test_every_owner_refusal_category_is_reachable():
    assert set(OWNER_ATTRIBUTIONS) <= set(CL.ATTRIBUTIONS)
    reach = {
        CL.R_SETTLEMENT: "SETTLEMENT", CL.R_IDENTITY: "IDENTITY_MAPPING",
        CL.R_STALE: "FRESHNESS", CL.R_NO_DEPTH: "LIQUIDITY",
        CL.R_LIMIT: "EXECUTION_UNCERTAINTY", CL.R_BELOW: "THRESHOLD",
        CL.R_ORDER_REFUSED: "RISK", CL.R_CAPACITY: "CAPACITY",
        "KAREN_CHALLENGE_BLOCKED": "KAREN_CHALLENGE",
        "UNSUPPORTED_LEAGUE": "UNSUPPORTED_LEAGUE",
        CL.R_ENTRIES_DISABLED: "EXPLICIT_POLICY",
        CL.R_NO_PINNACLE: "MISSING_PROBABILITY",
        CL.R_NO_BOOK: "MISSING_EXECUTABLE_BOOK"}
    assert {CL.attribution(c) for c in reach} == set(OWNER_ATTRIBUTIONS)
    for code, cat in reach.items():
        assert CL.attribution(code) == cat, code
    # an unknown code is never guessed into a category
    assert CL.attribution("SOMETHING_ELSE_ENTIRELY") == "UNATTRIBUTED"


def test_a_hindsight_winner_is_never_false_for_any_refusal_code():
    """Every recognised refusal, on a decision with no executable positive
    net EV, settles WON and is still never FALSE_REFUSAL."""
    codes = sorted(CL.MARKET_CODES | CL.CONTROL_CODES
                   | CL.MISSING_INPUT_CODES | CL.DEFECT_CODES)
    for code in codes:
        d = _dec(refusals=[code], policy_decision=X.pd_fig(-0.25))
        won = CL.classify(dict(d, settlement=_settle("WON")))
        lost = CL.classify(dict(d, settlement=_settle("LOST")))
        assert won["classification"] != CL.FALSE, (code, won["reason"])
        assert won["classification"] == lost["classification"], code


EDDIE = {"estimate_id": "eddie:1", "estimator_version": "EDDIE_V1",
         "estimated_at": 1.0, "expected_fill_probability": 0.6,
         "expected_net_executable_edge_pp": -0.4,
         "expected_executable_ev_usd": -0.01, "recommendation":
         "SKIP_EXECUTION", "recommendation_reason": "NET_EDGE_NOT_POSITIVE",
         "book_obs_id": 7}


def test_execution_confidence_comes_from_eddie_when_he_measured_it():
    fp, basis, src = SC.execution_input(EDDIE, 0.8, "snapshot")
    assert (fp, src) == (0.6, SC.EDDIE) and "eddie:1" in basis
    fp, basis, src = SC.execution_input(
        dict(EDDIE, expected_fill_probability=None, fill_why="NO_HISTORY"),
        0.8, "snapshot")
    assert (fp, basis, src) == (0.8, "snapshot", SC.CAPACITY_SNAPSHOT)
    assert SC.execution_input(None, None, "NO_SNAPSHOT") == (
        None, "NO_SNAPSHOT", None)
    cand = {"candidate_id": "c", "status": "MEASURED",
            "executable_opportunity_dollars": 5.0,
            "executable_capacity_usd": 200.0, "decided_at": 1000.0 * HOUR,
            "event_start_at": 1002.0 * HOUR}
    lags = [(0.0, 3 * HOUR)] * 6
    got = SC.score(cand, fill_probability=0.6, fill_basis=basis,
                   fill_source=SC.EDDIE, idle_capital_usd=1000.0,
                   lag_samples=lags, ctx={"eddie": EDDIE})
    ex = got["components"]["EXECUTION_CONFIDENCE"]
    assert ex["value"] == 0.6 and ex["source"] == SC.EDDIE
    assert ex["in_score"] is True
    assert ex["eddie"]["status"] == "MEASURED"
    assert ex["eddie"]["recommendation"] == "SKIP_EXECUTION"
    assert got["opportunity_score"] == pytest.approx(5.0 * 0.6 / 1000.0)
    none = SC.score(cand, fill_probability=None, idle_capital_usd=1000.0,
                    lag_samples=lags, ctx={"eddie_why": "TABLE_ABSENT"})
    ex = none["components"]["EXECUTION_CONFIDENCE"]
    assert ex["value"] is None and ex["why"] and ex["source"] is None
    assert ex["eddie"] == {"status": "UNAVAILABLE", "why": "TABLE_ABSENT"}


@pg
async def test_scores_read_eddies_estimate_and_the_expand_view_shows_it(
        monkeypatch):
    from sportsassets.api import command_lost_opportunity as API

    now = time.time()
    async with _txn() as conn:
        async def pool():
            return _Pool(conn)
        monkeypatch.setattr(API, "_pool", pool)
        acct = await X.account(conn, now=now)
        await P.lag_history(conn, acct, now=now)
        d = await X.decision(conn, acct, at=now - 600, refusals=[CL.R_BELOW],
                             pd=X.pd_fig(-0.1))
        await P.premap(conn, d["slug"], now + 2 * HOUR)
        snap = ("INSERT INTO pos_snapshots (snapshot_id, run_id, component, "
                " book, computed_at, payload, data_sha256, version) VALUES "
                " ($1,'r',$2,$3,to_timestamp($4),$5::jsonb,'s','v')")
        await conn.execute(snap, F.uid("s"), "CAPITAL", "PAPER", now - 3600,
                           '{"idle_capital_usd": 1000.0, "unmeasured": {}}')
        await conn.execute(snap, F.uid("s"), "CAPACITY", "NONE", now - 3600,
                           '{"rates": {"fill_probability": {"value": 0.8, '
                           '"n": 40, "basis": "TEST"}}}')
        await conn.execute(
            "INSERT INTO pos_capacity (capacity_id, candidate_id, run_id, "
            " computed_at, decided_at, us_market_slug, holding_side, status, "
            " executable_opportunity_dollars, executable_capacity_usd, "
            " capacity_ceiling_usd, content_sha256, version) VALUES ($1,$2,"
            " 'r',to_timestamp($3),to_timestamp($4),$5,'LONG','MEASURED',5.0,"
            " 200.0,400.0,'s','v')", F.uid("cap"), d["decision_id"], now,
            now - 600, d["slug"])
        eid = F.uid("eddie")
        await conn.execute(
            "INSERT INTO eddie_execution_estimates (estimate_id, decision_id,"
            " estimator_version, estimated_at, decided_at, us_market_slug, "
            " holding_side, expected_fill_probability, "
            " expected_net_executable_edge_pp, expected_executable_ev_usd, "
            " recommendation, recommendation_reason, unmeasured, "
            " evidence_refs) VALUES ($1,$2,'EDDIE_TEST',to_timestamp($3),"
            " to_timestamp($4),$5,'LONG',0.55,-0.3,-0.02,'SKIP_EXECUTION',"
            " 'NET_EDGE_NOT_POSITIVE',$6::jsonb,$7::jsonb)", eid,
            d["decision_id"], now - 300, now - 600, d["slug"],
            '{"theoretical_edge": "t", "fees": "t", "spread_cost": "t", '
            '"slippage": "t", "adverse_selection": "t", "time_to_fill": "t",'
            ' "capital_hours": "t", "max_executable_size": "t"}',
            '[{"kind": "paper_decisions", "id": "%s"}]' % d["decision_id"])
        got = await LR.run_component(conn, now=now)
        assert got["components"]["SCORES"] == "OK", got
        r = await conn.fetchrow(
            "SELECT * FROM lol_opportunity_scores_latest "
            " WHERE candidate_id = $1", d["decision_id"])
        assert r["status"] == "MEASURED", r["why"]
        comps = P.j(r["components"])
        ex = comps["EXECUTION_CONFIDENCE"]
        assert ex["value"] == pytest.approx(0.55)
        assert ex["source"] == SC.EDDIE and eid in ex["basis"]
        assert ex["eddie"]["estimate_id"] == eid
        assert r["opportunity_score"] == pytest.approx(
            5.0 * 0.55 * 1.0 / (200.0 * r["expected_hold_h"]))
        served = await API.opportunity_scores(status="", limit=50)
        row = next(x for x in served["data"]["rows"]
                   if x["candidate_id"] == d["decision_id"])
        e = row["expand"]["eddie_execution"]
        assert e["status"] == "OK" and e["estimate_id"] == eid
        assert e["recommendation"] == "SKIP_EXECUTION"
        assert e["authority"] == "SHADOW_ONLY"
