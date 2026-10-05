"""CAPITAL-CRITICAL: GET /api/command/profitability/os OVER SEEDED ROWS.

ALL DATA HERE IS SYNTHETIC TEST DATA written into a scratch test database
inside a transaction each test rolls back (tests/pos_fixture.py: decisions,
entry orders, fills, settlements, a sold position with its hold-to-
settlement counterfactual, an open position, capacity candidates; the
profitability cycle then writes pos_*). Added here: recorded books at and
after a fill (markouts), a policy-version change and a CRITICAL finding
(the release / incident twin).

  * every section answers with the seeded rows, scoped to ONE account;
  * an account with nothing recorded is EMPTY with reasons, never zeros;
  * a failed connection is UNAVAILABLE everywhere, one failed input only
    its own sections;
  * the request is a READ ONLY transaction with a statement timeout and it
    changes no protected table;
  * the route is GET only, needs a command session, and the existing
    /api/command/profitability/* routes are still served.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import asyncpg
import pytest
from fastapi.testclient import TestClient

from sportsassets.pos_os import assemble as A
from sportsassets.pos_os import common as C
from sportsassets.pos_os import reads as R
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
HOUR = 3600.0


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


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


async def _decision(conn, acct, *, at, version, verdict="REFUSE"):
    did = "paperdec:" + F.uid()
    slug = F.uid("posos-mkt-")
    await conn.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, us_market_slug, holding_side, fixture, label, verdict,"
        " refusal, p_pinnacle, internal_model, pinnacle, limit_price, "
        " proposed_qty, economics, qualification_gaps, policy_version, "
        " simulator_version, strategy) VALUES ($1,$2,$3,to_timestamp($4),"
        " $5,'LONG',$6,'{}'::jsonb,$7,$8,0.55,'{}'::jsonb,'{}'::jsonb,0.5,"
        " 10,'{}'::jsonb,'[]'::jsonb,$9,$10,$11)",
        did, acct["session_id"], acct["account_id"], float(at), slug,
        "fx-" + slug, verdict, None if verdict == "ENTER"
        else "PROBABILITY_EVIDENCE_STALE", version, F.SIM_VERSION,
        F.STRATEGY)
    return did


async def _seed(conn):
    s = await P.scenario(conn)
    acct, now = s["acct"], s["now"]
    # recorded books AT the settled position's entry fill and after it
    slug, t_fill = s["settled"]["decision"]["slug"], s["settled"]["t0"] + 2
    await F.book(conn, slug, t_fill - 1, bids=((0.51, 500),),
                 offers=((0.55, 500),))
    for dt, (b, o) in ((70, (0.50, 0.54)), (320, (0.47, 0.51)),
                       (1850, (0.45, 0.49))):
        await F.book(conn, slug, t_fill + dt, bids=((b, 500),),
                     offers=((o, 500),))
    # a release the decisions saw (TEST -> TEST_V2) and an incident
    for i in range(4):
        await _decision(conn, acct, at=now - 6 * HOUR + i * 600,
                        version="TEST")
    for i in range(4):
        await _decision(conn, acct, at=now - 3 * HOUR + i * 600,
                        version="TEST_V2", verdict="ENTER" if i % 2 else
                        "REFUSE")
    await conn.execute(
        "INSERT INTO paper_audrey_findings (finding_id, session_id, "
        " account_id, found_at, kind, severity, subject, detail) VALUES "
        " ($1,$2,$3,to_timestamp($4),'SYNTHETIC_INCIDENT','CRITICAL','x',"
        " '{}'::jsonb)", "paperfind:posos:" + F.uid(), acct["session_id"],
        acct["account_id"], now - 2 * HOUR)
    got = await RUN.run_cycle(conn, now=now, account_id=acct["account_id"],
                              include_actual=False)
    assert got["ran"], got
    return s


async def _page(monkeypatch, conn, account_id, **kw):
    from sportsassets.api import command_profitability_os as OS

    async def pool():
        return _Pool(conn)
    monkeypatch.setattr(OS, "_pool", pool)
    return await OS.profitability_os(response=None, account_id=account_id,
                                     window_days=kw.get("window_days", 30.0))


async def test_every_section_answers_from_the_seeded_paper_book(monkeypatch):
    async with _txn() as conn:
        s = await _seed(conn)
        before = await F.protected_counts(conn)
        page = await _page(monkeypatch, conn, s["acct"]["account_id"])
        assert await F.protected_counts(conn) == before
    assert page["label"] == "RESEARCH"
    assert page["authority"] == "SHADOW_NO_AUTHORITY"
    assert page["status"] == C.OK and page["summed_across_books"] is False
    assert page["scope"]["account_id"] == s["acct"]["account_id"]
    assert set(page["sections"]) == set(A.NAMES)
    assert not page["input_errors"], page["input_errors"]
    sec = page["sections"]
    for name in ("champion_challenger", "execution_cost_learning",
                 "capital_hour_optimizer", "capacity_frontier",
                 "counterfactual_twin", "data_quality_sentinel",
                 "model_economic_drift", "scenario_correlation",
                 "execution_policy_league", "expected_profit_clock",
                 "autonomy_health", "release_incident_twin"):
        assert sec[name]["status"] == C.OK, (name, sec[name]["why"])
    for name in ("regime_detection", "post_trade_attribution",
                 "experiment_governance"):
        assert sec[name]["status"] in (C.OK, C.EMPTY), name
        assert sec[name]["status"] == C.OK or sec[name]["why"]
    # markouts from the recorded books, at the fill's own horizons
    lr = sec["execution_cost_learning"]["data"]["by_strategy_style"][0]
    assert lr["fills_with_mid"] == 1
    adv = lr["adverse"]
    assert adv["60s"]["n"] == 1 and adv["60s"]["raw_pp"] == pytest.approx(
        0.01)
    assert adv["300s"]["raw_pp"] == pytest.approx(0.04)
    assert adv["1800s"]["raw_pp"] == pytest.approx(0.06)
    # counterfactual kept apart from realized
    cf = sec["counterfactual_twin"]["data"]["hold_to_settlement"]
    assert cf["counterfactual_positions"] == 1
    assert sec["counterfactual_twin"]["data"]["summed_with_realized"] is False
    # recommendation only
    assert sec["capital_hour_optimizer"]["data"]["applied"] is False
    # the open position is in the clock and the correlation book
    assert sec["expected_profit_clock"]["data"]["open_positions"] == 1
    assert sec["scenario_correlation"]["data"]["exposure"][
        "open_positions"] == 1
    # the release and the incident were replayed on recorded rows
    kinds = {r["kind"] for r in sec["release_incident_twin"]["data"][
        "replays"]}
    assert {"POLICY_VERSION", "INCIDENT"} <= kinds
    pv = [r for r in sec["release_incident_twin"]["data"]["replays"]
          if r["kind"] == "POLICY_VERSION" and r["to"] == "TEST_V2"][0]
    assert pv["from"] == "TEST"
    assert pv["status"] == C.MEASURED
    assert pv["after"]["enters"] >= 2 and pv["before"]["enters"] == 0
    au = sec["autonomy_health"]["data"]
    assert au["decisions"]["last_24h"]["decisions"] >= 8
    assert au["refusal_mix"]["stale_rate"] is not None
    json.dumps(page)


async def test_an_account_with_nothing_recorded_is_empty_not_zero(
        monkeypatch):
    async with _txn() as conn:
        page = await _page(monkeypatch, conn, "paper_test_posos_nobody")
    sec = page["sections"]
    for name in ("champion_challenger", "execution_cost_learning",
                 "capital_hour_optimizer", "capacity_frontier",
                 "data_quality_sentinel", "model_economic_drift",
                 "scenario_correlation", "execution_policy_league",
                 "expected_profit_clock", "release_incident_twin",
                 "post_trade_attribution"):
        assert sec[name]["status"] == C.EMPTY, (name, sec[name])
        assert sec[name]["why"], name


async def test_a_failed_connection_is_unavailable_everywhere(monkeypatch):
    from sportsassets.api import command_profitability_os as OS

    async def broken():
        raise ConnectionError("database down")
    monkeypatch.setattr(OS, "_pool", broken)
    page = await OS.profitability_os(response=None,
                                     account_id="paper_acct_main",
                                     window_days=30.0)
    assert page["status"] == C.UNAVAILABLE and "database down" in page["why"]
    assert set(page["sections"]) == set(A.NAMES)
    for s in page["sections"].values():
        assert s["status"] == C.UNAVAILABLE and s["data"] is None
        assert C.R_INPUT_NOT_READ in s["why"]


async def test_one_failed_input_leaves_only_its_sections_unavailable(
        monkeypatch):
    async def bad_fills(conn, **kw):
        await conn.fetchval("SELECT * FROM no_such_table_posos")

    monkeypatch.setitem(R.LOADERS, "fills", bad_fills)
    async with _txn() as conn:
        s = await _seed(conn)
        page = await _page(monkeypatch, conn, s["acct"]["account_id"])
    sec = page["sections"]
    assert "fills" in page["input_errors"]
    for name in ("execution_cost_learning", "execution_policy_league",
                 "scenario_correlation"):
        assert sec[name]["status"] == C.UNAVAILABLE, name
        assert "fills(" in sec[name]["why"], sec[name]["why"]
    # a later read in the same transaction still worked (savepoints)
    assert sec["champion_challenger"]["status"] == C.OK
    assert sec["capacity_frontier"]["status"] == C.OK
    assert sec["autonomy_health"]["status"] == C.OK


async def test_the_request_path_is_a_read_only_bounded_transaction(
        monkeypatch):
    from sportsassets.api import command_profitability_os as OS

    conn = await asyncpg.connect(H.DSN)
    try:
        async def pool():
            return _Pool(conn)
        monkeypatch.setattr(OS, "_pool", pool)

        async def write(c):
            assert await c.fetchval("SHOW transaction_read_only") == "on"
            assert await c.fetchval(
                "SELECT setting::int FROM pg_settings "
                " WHERE name='statement_timeout'") == OS.STATEMENT_TIMEOUT_MS
            await c.execute("INSERT INTO pos_runs (run_id) VALUES ('x')")

        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await OS._read_only(write)
        page = await OS.profitability_os(response=None,
                                         account_id="paper_acct_main",
                                         window_days=7.0)
        assert page["status"] in (C.OK, C.EMPTY)
        assert await conn.fetchval("SHOW transaction_read_only") == "off"
    finally:
        await conn.close()


def test_the_route_is_get_only_behind_a_command_session():
    from sportsassets.api import app as APP
    from sportsassets.api import command_profitability_os as OS

    mine = {r.path: set(r.methods) for r in OS.router.routes}
    assert mine == {OS.PATH: {"GET"}}
    paths, stack = set(), list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        else:
            paths.add(getattr(r, "path", ""))
    assert OS.PATH in paths
    for p in ("/api/command/profitability",
              "/api/command/profitability/north-star",
              "/api/command/profitability/capital",
              "/api/command/profitability/capacity",
              "/api/command/profitability/forecast",
              "/api/command/profitability/warehouse/{position_key:path}"):
        assert p in paths, p
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get(OS.PATH).status_code == 401
    assert client.post(OS.PATH).status_code in (401, 405)
    assert client.get("/api/command/profitability/north-star"
                      ).status_code == 401
