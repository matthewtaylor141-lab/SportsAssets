"""CAPITAL-CRITICAL: THE LOST OPPORTUNITY LEDGER, THE OPPORTUNITY SCORE AND
THE HORIZON FORECASTS (migration 220) ARE RESEARCH -- THEY CANNOT REACH A
VENUE, AN ORDER, A SIZE, A LIMIT OR CAPITAL, AND THEY WRITE ONLY THEIR OWN
APPEND-ONLY lol_* TABLES.

  §1 STATIC. Every module in sportsassets/lost_opportunity imports only the
     standard library, its own package and the profitability layer's pure /
     SELECT-only modules -- over the TRANSITIVE import closure; no venue,
     order, execution, paper, funded, sizing or agent-execution module is
     reachable. Every SQL write is an INSERT into a lol_* table, from
     store.py only. The read routes write nothing, run READ ONLY with a
     statement timeout, and import nothing with authority. The refusal
     codes the classifier copies are pinned to their source constants.
  §2 THE DATABASE. Migration 220 stores label RESEARCH and authority
     SHADOW_NO_AUTHORITY only; every lol_* table refuses UPDATE, DELETE and
     TRUNCATE; it is idempotent; its rollback drops only lol_* objects and
     refuses while the ledger or a horizon forecast holds a row.
  §3 RUNTIME. A full component run over synthetic rows changes no order,
     fill, ledger, settlement, decision, valuation, control, execution,
     funded or pos_* table; it writes only lol_* rows, all RESEARCH.
  §4 THE READS. The routes are GET only and require a command session.
  §5 These proofs are on the capital-critical list.
"""
from __future__ import annotations

import ast
import pathlib
import re
import time

import asyncpg
import pytest
from fastapi.testclient import TestClient

from sportsassets.lost_opportunity import classify as CL
from sportsassets.lost_opportunity import runner as LR

try:
    from tests import intel_fixture as F
    from tests import lol_fixture as X
    from tests import paper_harness as H
    from tests import test_profitability_is_research_only as POS
except ImportError:                                             # pragma: no cover
    import intel_fixture as F
    import lol_fixture as X
    import paper_harness as H
    import test_profitability_is_research_only as POS

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
MODULES = sorted((PKG / "lost_opportunity").glob("*.py"))
ROUTES_MOD = PKG / "api" / "command_lost_opportunity.py"
LOL_TABLES = ("lol_runs", "lol_ledger", "lol_opportunity_scores",
              "lol_horizon_forecasts", "lol_horizon_forecast_scores")
ALLOWED_PROJECT = {
    "sportsassets", "sportsassets.lost_opportunity",
    "sportsassets.profitability", "sportsassets.profitability.common",
    "sportsassets.profitability.economics",
    "sportsassets.profitability.reads",
    "sportsassets.profitability.forecast",
    "sportsassets.profitability.metrics"}
FORBIDDEN = POS.FORBIDDEN
#: tables the read routes may read (SELECT only): their own, pos-econ's,
#: and the records the expandable opportunity view joins by id
ROUTE_READS = {"lol_ledger", "lol_runs", "lol_opportunity_scores_latest",
               "lol_horizon_forecasts", "lol_horizon_forecast_scores",
               "pos_capacity", "paper_decisions", "karen_challenges",
               "paper_audrey_findings", "intel_allocations",
               "xavier_entry_theses"}


# ── §1 static ────────────────────────────────────────────────────────

def test_the_transitive_import_closure_holds_no_venue_or_order_module():
    seen, todo = set(), [POS._module_name(p) for p in MODULES]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        path = POS._path_of(name)
        if path is None:
            continue
        for imp in POS._imports(path):
            top = imp.split(".")[0]
            if top != "sportsassets":
                assert top in POS.STDLIB, (name, imp)
                continue
            if POS._path_of(imp) is None:
                continue
            if name.startswith("sportsassets.lost_opportunity"):
                assert (imp.startswith("sportsassets.lost_opportunity")
                        or imp in ALLOWED_PROJECT), (name, imp)
            todo.append(imp)
    reached = {s for s in seen if s.startswith("sportsassets")}
    for name in reached:
        leaf = name.rsplit(".", 1)[-1]
        assert not any(f in leaf for f in FORBIDDEN), name


def test_every_sql_write_is_an_insert_into_the_layers_own_tables():
    found = 0
    for path in MODULES:
        for kw, table in POS._sql_writes(path):
            found += 1
            assert table in LOL_TABLES, (path.name, kw, table)
            assert kw == "INSERT INTO", (path.name, kw, table)
            assert path.name == "store.py", (path.name, kw, table)
    assert found >= 5


def test_the_read_routes_write_nothing_and_run_read_only():
    assert not POS._sql_writes(ROUTES_MOD)
    src = ROUTES_MOD.read_text()
    tables = set(re.findall(r"(?<!epoch )\bFROM\s+([a-z_]+)", src))
    tables |= set(re.findall(r"\bJOIN\s+([a-z_]+)", src))
    assert tables and tables <= ROUTE_READS, tables - ROUTE_READS
    assert "transaction(readonly=not nested)" in src
    assert "SET LOCAL statement_timeout" in src
    for imp in POS._imports(ROUTES_MOD):
        leaf = imp.rsplit(".", 1)[-1]
        assert not any(f in leaf for f in FORBIDDEN), imp


def test_the_copied_refusal_codes_match_their_sources():
    from sportsassets.agents import derek_policy as DP
    from sportsassets.agents import paper_derek as PD
    for name in ("R_IDENTITY", "R_NOT_REAL", "R_SETTLEMENT", "R_NO_PINNACLE",
                 "R_STALE", "R_FRESHNESS_UNKNOWN", "R_NO_DEPTH", "R_LIMIT",
                 "R_NO_QTY", "R_FEES", "R_CAPACITY", "R_NO_MODEL",
                 "R_MODEL_CANNOT_SCORE", "R_BELOW", "R_DISAGREE", "R_NET",
                 "R_BELOW_NET", "R_LANE", "R_RAISED", "R_NOT_RECORDED"):
        assert getattr(CL, name) == getattr(DP, name), name
    for name, src in (("R_NO_RESEARCH_MODEL", "R_NO_RESEARCH_MODEL"),
                      ("R_MODEL_UNVERIFIED", "R_MODEL_UNVERIFIED"),
                      ("R_RESEARCH_CANNOT_SCORE", "R_MODEL_CANNOT_SCORE"),
                      ("R_NO_BOOK", "R_NO_BOOK"), ("R_NOT_PMUS", "R_NOT_PMUS"),
                      ("R_ORDER_REFUSED", "R_ORDER_REFUSED"),
                      ("R_ENTRIES_DISABLED", "R_ENTRIES_DISABLED"),
                      ("R_BOOK_DEADLINE", "R_BOOK_DEADLINE")):
        assert getattr(CL, name) == getattr(PD, src), name
    assert CL.attribution("x") in CL.ATTRIBUTIONS


def test_the_classifier_never_reads_the_settlement():
    """The class comes from the decision-time record: no function on the
    classification path touches the decision's `settlement` key."""
    tree = ast.parse((PKG / "lost_opportunity" / "classify.py").read_text())
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in ("classify", "_classify", "decision_economics",
                 "control_supported", "refusal_codes", "attribution"):
        src = ast.unparse(fns[name])
        assert not re.search(r"""(\[|get\()['"]settlement['"]""", src), name
        assert not re.search(r"""['"](outcome|payout_per_contract|"""
                             r"""settled_at)['"]""", src), name


# ── §2 the database ──────────────────────────────────────────────────

@pg
async def test_every_lol_table_is_append_only_and_research():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute(
            "INSERT INTO lol_runs (run_id, component, started_at, "
            " finished_at, status, version) VALUES ('r','LEDGER',now(),"
            " now(),'OK','v')")
        for sql in ("UPDATE lol_runs SET status = 'FAILED'",
                    "DELETE FROM lol_runs", "TRUNCATE lol_runs"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.IntegrityConstraintViolationError,
                               match="append-only"):
                await conn.execute(sql)
            await sp.rollback()
        for bad in ("'LIVE','SHADOW_NO_AUTHORITY'",
                    "'RESEARCH','ORDER_AUTHORITY'"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "INSERT INTO lol_runs (run_id, component, started_at, "
                    " finished_at, status, version, label, authority) VALUES"
                    " ('r2','LEDGER',now(),now(),'OK','v',%s)" % bad)
            await sp.rollback()
        trg = {r["t"] for r in await conn.fetch(
            "SELECT tgrelid::regclass::text AS t FROM pg_trigger "
            " WHERE tgname LIKE 'lol_%_append_only_trg'")}
        assert set(LOL_TABLES) <= trg
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_migration_220_is_idempotent_and_its_rollback_drops_only_lol():
    up = (ROOT / "migrations" / "220_lost_opportunity_ledger.sql").read_text()
    down = (ROOT / "migrations" / "rollback" /
            "220_lost_opportunity_ledger.down.sql").read_text()
    stmts = [ln for ln in down.splitlines() if ln.startswith("DROP ")]
    assert stmts and all(re.match(
        r"DROP (TABLE|FUNCTION|VIEW) IF EXISTS lol_", ln) for ln in stmts)
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        pos_before = await conn.fetchval(
            "SELECT count(*) FROM pg_class WHERE relname LIKE 'pos\\_%'")
        await conn.execute(up)
        await conn.execute(up)
        await conn.execute(down)
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_class WHERE relname LIKE 'lol\\_%' "
            "   AND relkind IN ('r', 'v')") == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_class WHERE relname LIKE 'pos\\_%'") \
            == pos_before
        await conn.execute(up)
        await conn.execute(
            "INSERT INTO lol_ledger (ledger_id, decision_ref, source, "
            " classifier_version, run_id, classified_at, decided_at, "
            " classification, reason, attribution, settlement_evidence_id, "
            " settlement_basis, settled_at, settlement_outcome, "
            " hypothetical_pnl_why, content_sha256) VALUES ('l','d',"
            " 'PAPER_DECISION','v','r',now(),now(),'UNKNOWABLE','x',"
            " 'UNATTRIBUTED','s','b',now(),'WON','w','h')")
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.RaiseError, match="rollback refused"):
            await conn.execute(down)
        await sp.rollback()
    finally:
        await tr.rollback()
        await conn.close()


# ── §3 runtime ───────────────────────────────────────────────────────

async def _all_counts(conn) -> dict:
    out = await F.protected_counts(conn)
    for t in [r["relname"] for r in await conn.fetch(
            "SELECT relname FROM pg_class WHERE relkind = 'r' AND "
            " relname LIKE 'pos\\_%'")]:
        out[t] = await conn.fetchval('SELECT count(*) FROM "%s"' % t)
    return out


@pg
async def test_a_component_run_writes_only_lol_rows():
    now = time.time()
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        acct = await X.account(conn, now=now)
        d = await X.decision(conn, acct, at=now - DAY_S, refusals=[CL.R_STALE],
                             pd=X.pd_fig(0.8), book=X.book_rec())
        await X.settle(conn, slug=d["slug"], at=now - 3600, now=now)
        before = await _all_counts(conn)
        lol_before = {t: await conn.fetchval("SELECT count(*) FROM %s" % t)
                      for t in LOL_TABLES}
        got = await LR.run_component(conn, now=now, econs=[])
        assert got["ran"], got
        assert await _all_counts(conn) == before
        lol_after = {t: await conn.fetchval("SELECT count(*) FROM %s" % t)
                     for t in LOL_TABLES}
        assert lol_after["lol_ledger"] > lol_before["lol_ledger"]
        for t in LOL_TABLES:
            assert await conn.fetchval(
                "SELECT count(*) FROM %s WHERE label <> 'RESEARCH' "
                "    OR authority <> 'SHADOW_NO_AUTHORITY'" % t) == 0, t
    finally:
        await tr.rollback()
        await conn.close()


DAY_S = 86400.0


# ── §4 the reads ─────────────────────────────────────────────────────

ROUTES = ("/api/command/profitability/lost-opportunities",
          "/api/command/profitability/opportunity-scores",
          "/api/command/profitability/forecast-horizons")


def test_the_routes_are_get_only_and_require_a_command_session():
    from sportsassets.api import app as APP

    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") in ROUTES:
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert set(paths) == set(ROUTES)
    for p, methods in paths.items():
        assert methods <= {"GET", "HEAD"}, (p, methods)
    client = TestClient(APP.app, raise_server_exceptions=False)
    for route in ROUTES:
        assert client.get(route).status_code == 401, route
        assert client.post(route).status_code in (401, 404, 405), route


# ── §5 the list ──────────────────────────────────────────────────────

def test_these_proofs_are_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    for name in ("test_lost_opportunity_is_research_only.py",
                 "test_lost_opportunity_ledger.py"):
        assert "tests/%s" % name in listed.splitlines(), name
    app_src = (PKG / "profitability" / "runner.py").read_text()
    assert "from ..lost_opportunity import runner as _LOL" in app_src
    assert LR.COMPONENT_TIMEOUT_S <= 120 and LR.STATEMENT_TIMEOUT_MS <= 30000
