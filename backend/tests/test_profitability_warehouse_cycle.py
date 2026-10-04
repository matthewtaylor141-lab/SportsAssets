"""THE WAREHOUSE, THROUGH THE SCHEDULED CYCLE, ON A REAL DATABASE.

  * every position's lineage carries the source record ids of its chain
    and context, stage by stage, with its named reproducibility gaps;
  * capital-hour economics, the counterfactual, capacity, the five metrics
    and the forecast are persisted as RESEARCH rows;
  * nothing churns: a second cycle over unchanged records writes no new
    revision; a new event writes exactly one, keeping every earlier id;
  * PAPER, ACTUAL and COUNTERFACTUAL stay separate books end to end;
  * a forecast whose horizon has ended is scored against what happened;
  * the read routes answer the RESEARCH envelope (EMPTY before any run).

ALL DATA IS SYNTHETIC (tests/pos_fixture.py), written inside a transaction
that is rolled back.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import asyncpg
import pytest

from sportsassets.profitability import reads as R
from sportsassets.profitability import runner as RUN

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
    from tests import pos_fixture as P
except ImportError:                                             # pragma: no cover
    import intel_fixture as F
    import paper_harness as H
    import pos_fixture as P

pytestmark = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
DAY = 86400.0


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


async def _cycle(conn, s, **kw):
    kw.setdefault("include_actual", False)
    got = await RUN.run_cycle(conn, now=kw.pop("now", s["now"]),
                              account_id=s["acct"]["account_id"], **kw)
    assert got["ran"], got
    assert set(got["components"].values()) == {"OK"}, got
    return got


async def _econ(conn, key):
    return await conn.fetchrow(
        "SELECT * FROM pos_economics_latest WHERE position_key=$1", key)


async def test_the_cycle_persists_lineage_economics_and_the_counterfactual():
    async with _txn() as conn:
        s = await P.scenario(conn)
        a = s["settled"]
        g = a["group_id"]
        await conn.execute(
            "INSERT INTO paper_xavier_reviews (review_id, session_id, "
            " account_id, group_id, reviewed_at, trigger, alternatives, "
            " exposure, recommendation) VALUES ($1,$2,$3,$4,"
            " to_timestamp($5),'FIRST_FILL','[]','{}','HOLD')",
            "paperrev:" + F.uid(), s["acct"]["session_id"],
            s["acct"]["account_id"], g, a["t0"] + 10)
        fid = "paperfind:" + F.uid()
        await conn.execute(
            "INSERT INTO paper_audrey_findings (finding_id, session_id, "
            " account_id, found_at, kind, severity, subject, detail) VALUES "
            " ($1,$2,$3,now(),'TEST','INFO',$4,'{}')", fid,
            s["acct"]["session_id"], s["acct"]["account_id"], g)
        await _cycle(conn, s)
        lin = await conn.fetchrow(
            "SELECT * FROM pos_lineage_latest WHERE position_key=$1",
            a["position_key"])
        assert lin["book"] == "PAPER" and lin["revision"] == 1
        assert lin["decision_ids"] == [a["decision"]["decision_id"]]
        assert lin["order_ids"] == [a["order_id"]]
        assert lin["fill_ids"] == [a["fill_id"]]
        assert lin["settlement_ids"] == [a["settlement_id"]]
        assert lin["audit_finding_ids"] == [fid]
        assert len(lin["review_ids"]) == 1
        assert lin["policy_versions"] == ["TEST"]
        assert lin["simulator_versions"] == [F.SIM_VERSION]
        stages = P.j(lin["stages"])
        for st in ("DECISION", "ORDER", "FILL", "POSITION",
                   "MANAGEMENT_REVIEW", "SETTLEMENT", "PNL", "PROBABILITY"):
            assert stages[st]["present"], st
        assert stages["CANDIDATE"]["present"] is False
        assert stages["CANDIDATE"]["why"] == (
            "NO_VALUATION_LINKED_TO_THE_DECISION")
        assert stages["EXECUTION_INTENT"]["why"] == (
            "NO_EXECUTION_INTENT_DERIVED_FROM_THIS_PAPER_DECISION")
        gaps = {x["gap"] for x in P.j(lin["gaps"])}
        assert {"PROVIDER_INPUTS", "BOOK_AT_DECISION"} <= gaps
        e = await _econ(conn, a["position_key"])
        assert e["lineage_id"] == lin["lineage_id"]
        assert e["net_profit_usd"] == pytest.approx(46.0)
        assert e["expected_net_profit_usd"] == pytest.approx(8.0)
        assert e["expected_capital_hours"] == pytest.approx(270.0)
        assert e["capital_hours"] == pytest.approx(54 * 5 + 60 * 2 / 3600.0
                                                   - 54 * 2 / 3600.0,
                                                   abs=1e-6)
        assert e["realized_profit_per_capital_hour"] == pytest.approx(
            46.0 / e["capital_hours"], rel=1e-6)
        # the sold position's hold-to-settlement counterfactual
        b = s["sold"]
        cf = await conn.fetchrow(
            "SELECT * FROM pos_economics_latest WHERE basis_position_key=$1",
            b["position_key"])
        assert cf["book"] == "COUNTERFACTUAL"
        assert cf["counterfactual_kind"] == "HOLD_TO_SETTLEMENT"
        assert cf["basis_book"] == "PAPER"
        assert cf["net_profit_usd"] == pytest.approx(-20.5)
        real = await _econ(conn, b["position_key"])
        assert real["net_profit_usd"] == pytest.approx(1.5)
        assert cf["lineage_id"] == (await conn.fetchval(
            "SELECT lineage_id FROM pos_lineage_latest WHERE "
            " position_key=$1", b["position_key"]))
        # an open position: no realized profit, named
        o = await _econ(conn, s["open"]["position_key"])
        assert o["state"] == "OPEN" and o["net_profit_usd"] is None
        assert P.j(o["unmeasured"])["net_profit_usd"] == (
            "POSITION_OPEN_NO_REALIZED_PROFIT")
        assert o["open_cost_basis_usd"] == pytest.approx(3.0)
        # the warehouse view joins lineage and economics per real position
        w = await conn.fetchrow(
            "SELECT * FROM pos_warehouse WHERE position_key=$1",
            a["position_key"])
        assert w["econ_id"] == e["econ_id"]
        # the view over the existing paper records sees the same chain
        ch = await conn.fetchrow(
            "SELECT * FROM pos_paper_chain_v WHERE position_key=$1",
            a["position_key"])
        assert ch["fill_ids"] == [a["fill_id"]]
        assert ch["settlement_ids"] == [a["settlement_id"]]
        assert ch["decision_ids"] == [a["decision"]["decision_id"]]


async def test_capacity_metrics_forecast_and_snapshots_are_persisted():
    async with _txn() as conn:
        s = await P.scenario(conn)
        got = await _cycle(conn, s)
        rid = got["run_id"]
        cap = await conn.fetchrow(
            "SELECT * FROM pos_capacity_latest WHERE candidate_id=$1",
            s["cap"]["decision_id"])
        assert cap["status"] == "MEASURED"
        assert cap["theoretical_opportunity_dollars"] == pytest.approx(96.0)
        assert 0 < cap["executable_opportunity_dollars"] < 96.0
        assert cap["capacity_ceiling_usd"] > cap["executable_capacity_usd"]
        assert cap["fee_basis"] == RUN.FEE_BASIS
        assert len(P.j(cap["edge_at_size"])) == 10
        nb = await conn.fetchrow(
            "SELECT * FROM pos_capacity_latest WHERE candidate_id=$1",
            s["cap_nobook"]["decision_id"])
        assert nb["status"] == "UNAVAILABLE"
        assert nb["why"] == "NO_RECORDED_BOOK_OBSERVATION"
        assert nb["executable_capacity_usd"] is None
        # (R30A, migration 227) the metrics are per book AND sleeve: the
        # fixture's positions are PINNACLE_ONLY_PAPER_BENCHMARK, which
        # migration 223 classifies BENCHMARK -- so the figures this test has
        # always pinned (47.5 / 74.5 over 2 positions) are the BENCHMARK
        # sleeve's, labelled research, and the PRODUCTION-CONFIDENCE
        # (INVESTMENT) metric is UNAVAILABLE: a benchmark win never reads as
        # investment evidence.
        rows = await conn.fetch(
            "SELECT * FROM pos_metric_observations WHERE run_id=$1", rid)
        assert all(r["sleeve"] in ("INVESTMENT", "TRAINING", "BENCHMARK",
                                   "UNCLASSIFIED") for r in rows)
        bm = {r["metric"]: r for r in rows if r["book"] == "PAPER"
              and r["sleeve"] == "BENCHMARK" and r["strategy"] == "ALL"}
        assert len(bm) == 5
        assert bm["REALIZED_NET_EDGE"]["status"] == "INSUFFICIENT_SAMPLE"
        assert bm["REALIZED_NET_EDGE"]["value"] == pytest.approx(
            47.5 / 74.5, rel=1e-6)
        assert bm["REALIZED_NET_EDGE"]["sample_n"] == 2
        assert bm["REALIZED_NET_EDGE"]["confidence_scope"] == \
            "RESEARCH_NOT_PRODUCTION_CONFIDENCE"
        inv = {r["metric"]: r for r in rows if r["book"] == "PAPER"
               and r["sleeve"] == "INVESTMENT" and r["strategy"] == "ALL"}
        assert len(inv) == 5
        assert inv["REALIZED_NET_EDGE"]["status"] == "UNAVAILABLE"
        assert inv["REALIZED_NET_EDGE"]["value"] is None
        assert inv["REALIZED_NET_EDGE"]["confidence_scope"] == \
            "PRODUCTION_CONFIDENCE"
        per_strategy = [r for r in rows if r["strategy"]
                        == "PINNACLE_ONLY_PAPER_BENCHMARK"]
        assert per_strategy and all(r["sleeve"] == "BENCHMARK"
                                    for r in per_strategy)
        fcs = {(r["book"], r["sleeve"]): r for r in await conn.fetch(
            "SELECT * FROM pos_forecasts WHERE run_id=$1", rid)}
        assert set(fcs) == {("PAPER", s) for s in (
            "INVESTMENT", "TRAINING", "BENCHMARK", "UNCLASSIFIED")}
        fc = fcs[("PAPER", "INVESTMENT")]
        assert fc["status"] == "UNAVAILABLE"
        assert fc["confidence_scope"] == "PRODUCTION_CONFIDENCE"
        assert fc["expected_pnl_usd"] is None and fc["why"]
        snaps = {(r["component"], r["book"]) for r in await conn.fetch(
            "SELECT component, book FROM pos_snapshots WHERE run_id=$1",
            rid)}
        assert snaps == {("CAPACITY", "NONE"), ("WAREHOUSE", "NONE"),
                         ("CAPITAL", "PAPER"), ("CAPITAL", "COUNTERFACTUAL")}
        capital = P.j(await conn.fetchval(
            "SELECT payload FROM pos_snapshots WHERE run_id=$1 AND "
            " component='CAPITAL' AND book='PAPER'", rid))
        assert capital["capital_locked_positions_usd"] == pytest.approx(3.0)
        assert capital["account_capital_usd"] == pytest.approx(500050.0)
        assert capital["summed_with_other_book"] is False


async def _review(conn, s, group_id):
    rv = "paperrev:" + F.uid()
    await conn.execute(
        "INSERT INTO paper_xavier_reviews (review_id, session_id, "
        " account_id, group_id, reviewed_at, trigger, alternatives, "
        " exposure, recommendation) VALUES ($1,$2,$3,$4,now(),"
        " 'SCHEDULED_BACKSTOP','[]','{}','HOLD')", rv,
        s["acct"]["session_id"], s["acct"]["account_id"], group_id)
    return rv


async def test_nothing_churns_and_a_new_event_is_one_new_revision():
    async with _txn() as conn:
        s = await P.scenario(conn)
        await _review(conn, s, s["open"]["group_id"])   # the first review
        await _cycle(conn, s)
        counts = {t: await conn.fetchval("SELECT count(*) FROM %s" % t)
                  for t in ("pos_lineage", "pos_position_economics",
                            "pos_capacity", "pos_metric_observations",
                            "pos_forecasts")}
        # a further review of a merely held position is a stream, not an
        # event: no new revision until something else happens
        rv = await _review(conn, s, s["open"]["group_id"])
        got = await _cycle(conn, s, now=s["now"] + 60)
        for t, n in counts.items():
            assert await conn.fetchval("SELECT count(*) FROM %s" % t) == n, t
        assert got["results"]["warehouse"]["lineage_revisions"] == 0
        # the open position sells out: one new revision, ids kept
        o = s["open"]
        so = await P.order(conn, s["acct"], group_id=o["group_id"],
                           slug=o["decision"]["slug"], at=s["now"] + 100,
                           qty=10, price=0.35, role="EXIT",
                           direction="SELL")
        sf = await F.fill(conn, s["acct"], order_id=so,
                          group_id=o["group_id"], slug=o["decision"]["slug"],
                          role="EXIT", direction="SELL", qty=10, price=0.35,
                          at=s["now"] + 102)
        await _cycle(conn, s, now=s["now"] + 200)
        revs = await conn.fetch(
            "SELECT revision, fill_ids, state FROM pos_lineage "
            " WHERE position_key=$1 ORDER BY revision", o["position_key"])
        assert [r["revision"] for r in revs] == [1, 2]
        assert set(revs[0]["fill_ids"]) < set(revs[1]["fill_ids"])
        assert sf in revs[1]["fill_ids"] and revs[1]["state"] == "CLOSED"
        assert rv in await conn.fetchval(
            "SELECT review_ids FROM pos_lineage WHERE position_key=$1 "
            "   AND revision=2", o["position_key"])
        e = await _econ(conn, o["position_key"])
        assert e["revision"] == 2 and e["state"] == "CLOSED"
        assert e["net_profit_usd"] == pytest.approx(3.5 - 3.0)


async def test_actual_positions_are_their_own_book_end_to_end():
    async with _txn() as conn:
        s = await P.scenario(conn)
        act = await P.actual_position(conn, now=s["now"],
                                      decision_id=s["open"]["decision"][
                                          "decision_id"])
        got = await _cycle(conn, s, include_actual=True)
        e = await _econ(conn, act["position_key"])
        assert e["book"] == "ACTUAL" and e["venue"] == "POLYMARKET_US"
        assert e["capital_committed_usd"] == pytest.approx(20 * 0.45 + 0.2)
        assert e["probability"] == pytest.approx(0.40)
        lin = await conn.fetchrow(
            "SELECT * FROM pos_lineage_latest WHERE position_key=$1",
            act["position_key"])
        assert lin["intent_ids"] == [act["intent_id"]]
        assert lin["order_ids"] == [act["mirror_id"]]
        assert lin["fill_ids"] == [act["fill_key"]]
        assert lin["policy_versions"] == ["TEST", "TEST_POLICY"]
        gaps = {x["gap"] for x in P.j(lin["gaps"])}
        assert "ACTUAL_VENUE_FILL_TIME" in gaps
        rid = got["run_id"]
        snaps = {r["book"]: P.j(r["payload"]) for r in await conn.fetch(
            "SELECT book, payload FROM pos_snapshots WHERE run_id=$1 AND "
            " component='CAPITAL'", rid)}
        assert set(snaps) == {"PAPER", "ACTUAL", "COUNTERFACTUAL"}
        assert snaps["PAPER"]["capital_locked_positions_usd"] == \
            pytest.approx(3.0)                       # not + the actual 9.2
        assert snaps["ACTUAL"]["capital_locked_positions_usd"] == \
            pytest.approx(9.2)
        assert snaps["ACTUAL"]["capital_locked_reservations_usd"] is None
        books = {r["book"] for r in await conn.fetch(
            "SELECT DISTINCT book FROM pos_metric_observations "
            " WHERE run_id=$1", rid)}
        assert books == {"PAPER", "ACTUAL"}
        # the PAPER decision the actual entry copied gets the intent too
        plin = await conn.fetchrow(
            "SELECT intent_ids FROM pos_lineage_latest WHERE "
            " position_key=$1", s["open"]["position_key"])
        assert plin["intent_ids"] == [act["intent_id"]]


async def test_an_ended_forecast_is_scored_against_what_happened():
    async with _txn() as conn:
        s = await P.scenario(conn)
        await _cycle(conn, s)
        now = s["now"]
        q = {str(round(0.05 * i, 2)): -50.0 + 5.0 * i for i in range(1, 20)}
        # (R30A, migration 227) a forecast names its scope and is scored
        # against ITS OWN sleeve: the fixture's positions are BENCHMARK, so
        # the BENCHMARK forecast realizes the +47.5 this test has always
        # pinned, and an INVESTMENT forecast over the same horizon realizes
        # nothing -- a benchmark win never scores an investment forecast.
        ins = ("INSERT INTO pos_forecasts (forecast_id, book, run_id, "
               " issued_at, issued_day, horizon_start, horizon_end, "
               " horizon_days, method, status, why, expected_pnl_usd, "
               " p10_pnl_usd, p50_pnl_usd, p90_pnl_usd, prob_positive, "
               " quantiles, inputs_sha256, version, sleeve, strategy, "
               " policy_versions, confidence_scope) VALUES ($4,"
               " 'PAPER','r',to_timestamp($1),"
               " (to_timestamp($1) AT TIME ZONE 'UTC')::date,to_timestamp($1),"
               " to_timestamp($2),30,'m','UNPROVEN','x',0,-40,0,40,0.5,"
               " $3::jsonb,'s','v',$5,'ALL','{}',$6)")
        await conn.execute(ins, now - 31 * DAY, now - DAY, json.dumps(q),
                           "posfc:old", "BENCHMARK",
                           "RESEARCH_NOT_PRODUCTION_CONFIDENCE")
        await conn.execute(ins, now - 31 * DAY, now - DAY, json.dumps(q),
                           "posfc:old-inv", "INVESTMENT",
                           "PRODUCTION_CONFIDENCE")
        got = await _cycle(conn, s, now=now + 60)
        assert got["results"]["forecast"]["scored"] == 2
        sc = await conn.fetchrow(
            "SELECT * FROM pos_forecast_scores WHERE forecast_id='posfc:old'")
        # released inside [now-31d, now-1d): the settled (+46) and sold (+1.5)
        assert sc["realized_pnl_usd"] == pytest.approx(47.5)
        assert sc["realized_positions"] == 2
        assert sc["inside_p10_p90"] is False and sc["realized_positive"]
        assert sc["brier_positive"] == pytest.approx(0.25)
        assert sc["label"] == "RESEARCH"
        assert sc["sleeve"] == "BENCHMARK" and sc["strategy"] == "ALL"
        inv = await conn.fetchrow(
            "SELECT * FROM pos_forecast_scores "
            " WHERE forecast_id='posfc:old-inv'")
        assert inv["sleeve"] == "INVESTMENT"
        assert inv["realized_pnl_usd"] == 0.0
        assert inv["realized_positions"] == 0
        assert inv["confidence_scope"] == "PRODUCTION_CONFIDENCE"
        # the whole book (a pre-227 book-wide forecast's scope) still sees it
        whole = await R.realized_between(conn, book="PAPER", start=now - 31
                                         * DAY, end=now - DAY)
        assert whole == (pytest.approx(47.5), 2)
        again = await _cycle(conn, s, now=now + 120)
        assert again["results"]["forecast"]["scored"] == 0


# ── the read routes ──────────────────────────────────────────────────

class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


async def test_the_reads_serve_research_envelopes(monkeypatch):
    from sportsassets.api import command_profitability as CP

    async with _txn() as conn:
        async def pool():
            return _Pool(conn)

        monkeypatch.setattr(CP, "_pool", pool)
        for fn in (CP.profitability_index, CP.profitability_north_star):
            got = await fn()
            assert got["status"] == "EMPTY" and got["data"] is None
            assert got["label"] == "RESEARCH"
            assert got["authority"] == "SHADOW_NO_AUTHORITY"
        s = await P.scenario(conn)
        await _cycle(conn, s)
        keys = {"label", "authority", "status", "why", "computed_at", "data"}
        ns = await CP.profitability_north_star()
        assert keys <= set(ns) and ns["status"] == "OK"
        m = ns["data"]["PAPER"]["MAX_DRAWDOWN"]
        assert {"value", "sample_n", "ci_low", "ci_high", "status", "trend",
                "period", "freshness"} <= set(m)
        assert m["trend"]["direction"] == "UNAVAILABLE"
        assert ns["data"]["ACTUAL"] == {}
        assert ns["summed_across_books"] is False
        # (R30A, migration 227) data.PAPER is the INVESTMENT sleeve --
        # production confidence. The fixture's positions are BENCHMARK: they
        # are served separately in by_sleeve, labelled research, and never
        # reach the production row.
        assert m["sleeve"] == "INVESTMENT" and m["strategy"] == "ALL"
        assert m["confidence_scope"] == "PRODUCTION_CONFIDENCE"
        assert m["value"] is None and m["status"] == "UNAVAILABLE"
        bm = ns["by_sleeve"]["PAPER"]["BENCHMARK"]["MAX_DRAWDOWN"]
        assert bm["value"] is not None and bm["sample_n"] == 2
        assert bm["confidence_scope"] == "RESEARCH_NOT_PRODUCTION_CONFIDENCE"
        assert "PINNACLE_ONLY_PAPER_BENCHMARK" in ns["by_strategy"]["PAPER"]
        assert ns["summed_across_sleeves"] is False
        cap = await CP.profitability_capital(book="", limit=50)
        assert cap["status"] == "OK"
        assert cap["data"]["ACTUAL"] is None
        assert cap["data"]["COUNTERFACTUAL"]["book"] == "COUNTERFACTUAL"
        pc = cap["data"]["PAPER"]
        assert pc["production_confidence"]["sleeve"] == "INVESTMENT"
        assert pc["production_confidence"]["realized_net_profit_usd"] is None
        assert pc["by_sleeve"]["BENCHMARK"]["realized_net_profit_usd"] == \
            pytest.approx(47.5)
        op = [p for p in cap["positions"] if p["state"] == "OPEN"][0]
        assert op["capital_hours_to_date"] >= op["capital_hours"]
        assert "REALIZED_PROFIT_PER_CAPITAL_HOUR" in op
        assert "EXPECTED_PROFIT_PER_CAPITAL_HOUR" in op
        cp = await CP.profitability_capacity(limit=10)
        assert cp["status"] == "OK" and cp["candidates"]
        assert "EXECUTABLE_OPPORTUNITY_DOLLARS" in cp["data"]
        assert "EXPECTED_EDGE_AT_SIZE" in cp["candidates"][0]
        fc = await CP.profitability_forecast(history=5)
        assert fc["status"] == "OK"
        assert fc["data"]["PAPER"]["status"] == "UNAVAILABLE"
        wh = await CP.profitability_warehouse(s["sold"]["position_key"])
        assert wh["status"] == "OK"
        assert wh["data"]["lineage"]["book"] == "PAPER"
        assert set(wh["data"]["economics"]) == {"PAPER", "COUNTERFACTUAL"}
        none = await CP.profitability_warehouse("paperpos:nope")
        assert none["status"] == "EMPTY"
        assert none["why"] == "NO_LINEAGE_FOR_POSITION_KEY"
        idx = await CP.profitability_index()
        assert set(idx["data"]) == {"CYCLE", "CAPACITY", "ECONOMICS",
                                    "WAREHOUSE", "CAPITAL", "NORTH_STAR",
                                    "FORECAST"}


async def test_a_failed_read_is_unavailable_not_zeros(monkeypatch):
    from sportsassets.api import command_profitability as CP

    async def broken():
        raise ConnectionError("database down")

    monkeypatch.setattr(CP, "_pool", broken)
    got = await CP.profitability_capital(book="", limit=5)
    assert got["status"] == "UNAVAILABLE" and got["data"] is None
    assert "database down" in got["why"]
