"""CAPITAL-CRITICAL: THE PROFITABILITY LAYER IS RESEARCH -- IT CANNOT REACH A
VENUE, AN ORDER, A SIZE, A LIMIT OR CAPITAL, AND IT WRITES ONLY ITS OWN
APPEND-ONLY TABLES.

  §1 STATIC. Every module in sportsassets/profitability imports only the
     standard library, its own package, the intelligence layer's pure
     modules and the published fee schedule -- checked over the TRANSITIVE
     import closure. No venue, order, execution, ledger-writing or
     agent-execution module is reachable. Every SQL write targets a pos_*
     table, and the only write verb is INSERT (append-only). The read
     routes read only pos_* tables and write nothing.
  §2 THE DATABASE. Migration 216 stores label RESEARCH and authority
     SHADOW_NO_AUTHORITY only; every pos_* table refuses UPDATE, DELETE and
     TRUNCATE; book is PAPER | ACTUAL | COUNTERFACTUAL and a counterfactual
     must name its kind and its real position; unmeasured headline values
     need a named reason; an UNAVAILABLE capacity row cannot carry a number.
     The migration is idempotent; its rollback drops only pos_* objects and
     refuses while a forecast exists.
  §3 RUNTIME. A full cycle over synthetic rows changes no order, fill,
     ledger, settlement, decision, valuation, control, execution or funded
     table; every row it writes is RESEARCH / SHADOW_NO_AUTHORITY.
  §4 FAIL-CLOSED / FAILURE ISOLATION. A component that raises is recorded
     FAILED and the rest still run; without migration 216 the cycle idles.
  §5 THE READS. /api/command/profitability/* are GET only, require a
     command session (401 without), and answer the RESEARCH envelope.
  §6 ARMING. The runner is a kill-switchable (POS_ECON=off) task in the API
     lifespan; these tests are on the capital-critical list.
"""
from __future__ import annotations

import ast
import pathlib
import re
import time

import asyncpg
import pytest
from fastapi.testclient import TestClient

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

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
MODULES = sorted((PKG / "profitability").glob("*.py"))
ROUTES_MOD = PKG / "api" / "command_profitability.py"
POS_TABLES = ("pos_runs", "pos_lineage", "pos_position_economics",
              "pos_capacity", "pos_metric_observations", "pos_snapshots",
              "pos_forecasts", "pos_forecast_scores")

STDLIB = {"__future__", "asyncio", "datetime", "hashlib", "json", "logging",
          "math", "os", "random", "time", "uuid", "zoneinfo", "typing",
          "contextlib", "dataclasses", "decimal", "collections"}
ALLOWED_PROJECT = {"sportsassets", "sportsassets.profitability",
                   "sportsassets.intel", "sportsassets.bettor_fee_schedule",
                   # the lost opportunity component (migration 220), called
                   # by the cycle after FORECAST; its own closure and write
                   # targets are pinned by test_lost_opportunity_is_research_
                   # only.py and are traversed (and leaf-checked) here too
                   "sportsassets.lost_opportunity",
                   "sportsassets.lost_opportunity.runner"}
#: the intelligence modules this layer may import (pure computation and
#: SELECT-only reads; their own closure is pinned by test_intel_is_shadow_only)
ALLOWED_INTEL = {"sportsassets.intel.common", "sportsassets.intel.reads",
                 "sportsassets.intel.attribution"}
FORBIDDEN = ("execmirror", "kalshi", "pmus", "venue", "clob", "executor",
             "execution_intent", "execution_gate", "bettor_paper",
             "bettor_funded", "paper_", "smalllive", "order", "live_",
             "actual_admission", "pinnapi", "submit", "sizing", "allocator",
             "runtime", "desk")
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE|"
                   r"ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+TABLE|COPY)\s+"
                   r"([A-Za-z_][A-Za-z0-9_]*)")


def _module_name(path: pathlib.Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(path: pathlib.Path) -> set:
    mod = _module_name(path)
    pkg = mod if path.name == "__init__.py" else mod.rsplit(".", 1)[0]
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg.split(".")
                base = base[:len(base) - (node.level - 1)]
                target = ".".join(base + ([node.module] if node.module
                                          else []))
            else:
                target = node.module
            out.add(target)
            for a in node.names:
                out.add("%s.%s" % (target, a.name))
    return out


def _path_of(name: str):
    p = ROOT / pathlib.Path(*name.split("."))
    if p.with_suffix(".py").exists():
        return p.with_suffix(".py")
    if (p / "__init__.py").exists():
        return p / "__init__.py"
    return None


def _sql_writes(path: pathlib.Path) -> list:
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for kw, table in WRITE.findall(node.value):
                if kw.upper() == "UPDATE" and table.upper() == "SET":
                    continue
                out.append((" ".join(kw.split()).upper(), table))
    return out


# ── §1 static ────────────────────────────────────────────────────────

def test_the_transitive_import_closure_holds_no_venue_or_order_module():
    seen, todo = set(), [_module_name(p) for p in MODULES]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        path = _path_of(name)
        if path is None:
            continue
        for imp in _imports(path):
            top = imp.split(".")[0]
            if top != "sportsassets":
                assert top in STDLIB, (name, imp)
                continue
            if _path_of(imp) is None:
                continue
            if name.startswith("sportsassets.profitability"):
                assert (imp.startswith("sportsassets.profitability")
                        or imp in ALLOWED_PROJECT or imp in ALLOWED_INTEL), (
                    name, imp)
            todo.append(imp)
    reached = {s for s in seen if s.startswith("sportsassets")}
    assert "sportsassets.bettor_fee_schedule" in reached
    for name in reached:
        leaf = name.rsplit(".", 1)[-1]
        assert not any(f in leaf for f in FORBIDDEN), name


def test_every_sql_write_is_an_insert_into_the_layers_own_tables():
    found = 0
    for path in MODULES:
        for kw, table in _sql_writes(path):
            found += 1
            assert table in POS_TABLES, (path.name, kw, table)
            assert kw == "INSERT INTO", (path.name, kw, table)
            assert path.name == "store.py", (path.name, kw, table)
    assert found >= 7                       # the scan sees the store's SQL


def test_the_read_routes_read_only_pos_tables():
    assert not _sql_writes(ROUTES_MOD)
    src = ROUTES_MOD.read_text()
    tables = set(re.findall(r"(?<!epoch )\bFROM\s+([a-z_]+)", src))
    assert tables and all(t.startswith("pos_") for t in tables), tables


def test_the_routes_module_imports_nothing_with_authority():
    for imp in _imports(ROUTES_MOD):
        leaf = imp.rsplit(".", 1)[-1]
        assert not any(f in leaf for f in FORBIDDEN), imp


# ── §2 the database ──────────────────────────────────────────────────

@pg
async def test_every_pos_table_is_append_only():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute(
            "INSERT INTO pos_runs (run_id, component, started_at, "
            " finished_at, status, version) VALUES ('r', 'CYCLE', now(), "
            " now(), 'OK', 'v')")
        for sql in ("UPDATE pos_runs SET status = 'FAILED'",
                    "DELETE FROM pos_runs", "TRUNCATE pos_runs"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.IntegrityConstraintViolationError,
                               match="append-only"):
                await conn.execute(sql)
            await sp.rollback()
        trg = {r["tgrelid"] for r in await conn.fetch(
            "SELECT tgrelid::regclass::text AS tgrelid FROM pg_trigger "
            " WHERE tgname LIKE '%_append_only_trg'")}
        assert set(POS_TABLES) <= trg
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_the_schema_refuses_authority_summed_books_and_silent_nulls():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    base = ("INSERT INTO pos_position_economics (econ_id, book, position_key,"
            " revision, run_id, computed_at, state, capital_hours, "
            " net_profit_usd, expected_net_profit_usd, expected_capital_hours,"
            " realized_profit_per_capital_hour, "
            " expected_profit_per_capital_hour, unmeasured, content_sha256, "
            " version, counterfactual_kind, basis_book, basis_position_key, "
            " label, authority) VALUES (%s)")
    ok = ("'e1','PAPER','k',1,'r',now(),'CLOSED',1,1,1,1,1,1,'{}','s','v',"
          "NULL,NULL,NULL,'RESEARCH','SHADOW_NO_AUTHORITY'")
    try:
        await conn.execute(base % ok)
        bad = (
            ok.replace("'RESEARCH'", "'LIVE'"),
            ok.replace("'SHADOW_NO_AUTHORITY'", "'ORDER_AUTHORITY'"),
            ok.replace("'PAPER'", "'PAPER_PLUS_ACTUAL'"),
            # a counterfactual must name its kind and its real position
            ok.replace("'PAPER'", "'COUNTERFACTUAL'"),
            ok.replace("NULL,NULL,NULL", "'HOLD_TO_SETTLEMENT','PAPER','k'"),
            # an open position has no realized profit
            ok.replace("'CLOSED'", "'OPEN'"),
            # an unmeasured value needs its named reason
            ok.replace("'CLOSED',1,1,1", "'CLOSED',1,NULL,1"),
        )
        for i, vals in enumerate(bad):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(base % vals.replace("'e1'", "'e%d'" % (
                    i + 2)))
            await sp.rollback()
        await conn.execute(base % ok.replace("'e1'", "'e9'").replace(
            "'CLOSED',1,1,1", "'CLOSED',1,NULL,1").replace(
            "'{}'", "'{\"net_profit_usd\": \"WHY\"}'").replace(",1,'r'",
                                                               ",2,'r'"))
        for sql in (
                "INSERT INTO pos_capacity (capacity_id, candidate_id, run_id,"
                " computed_at, status, why, executable_capacity_usd, "
                " content_sha256, version) VALUES ('c','d','r',now(),"
                " 'UNAVAILABLE','NO_RECORDED_BOOK_OBSERVATION',0,'s','v')",
                "INSERT INTO pos_capacity (capacity_id, candidate_id, run_id,"
                " computed_at, status, content_sha256, version) VALUES "
                " ('c2','d','r',now(),'UNAVAILABLE','s2','v')",
                "INSERT INTO pos_metric_observations (observation_id, run_id,"
                " book, metric, computed_at, value, status, why, "
                " content_sha256, version) VALUES ('m','r','PAPER',"
                " 'MAX_DRAWDOWN',now(),0,'UNAVAILABLE','X','s','v')",
                "INSERT INTO pos_metric_observations (observation_id, run_id,"
                " book, metric, computed_at, status, content_sha256, version)"
                " VALUES ('m2','r','COUNTERFACTUAL','MAX_DRAWDOWN',now(),"
                " 'UNAVAILABLE','s','v')",
                "INSERT INTO pos_forecasts (forecast_id, book, run_id, "
                " issued_at, issued_day, horizon_start, horizon_end, "
                " horizon_days, method, status, why, expected_pnl_usd, "
                " inputs_sha256, version) VALUES ('f','PAPER','r',now(),"
                " current_date, now(), now() + interval '30 days', 30, 'm',"
                " 'UNAVAILABLE','X',0,'s','v')"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(sql)
            await sp.rollback()
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_migration_216_is_idempotent_and_its_rollback_drops_only_pos():
    up = (ROOT / "migrations" / "216_profitability_warehouse.sql").read_text()
    down = (ROOT / "migrations" / "rollback" /
            "216_profitability_warehouse.down.sql").read_text()
    stmts = [ln for ln in down.splitlines() if ln.startswith("DROP ")]
    assert stmts and all(re.match(
        r"DROP (TABLE|FUNCTION|VIEW) IF EXISTS pos_", ln) for ln in stmts), \
        stmts
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute(up)
        await conn.execute(up)
        # migration 217 (Archer/Scout) also owns pos_* objects
        # (pos_candidate_review*, pos_iface_*): the rollback of 216 must drop
        # EVERY relation 216 creates and NONE of anyone else's
        own = sorted(set(re.findall(
            r"CREATE (?:TABLE|VIEW|OR REPLACE VIEW)(?: IF NOT EXISTS)? "
            r"(pos_[a-z0-9_]+)", up)))
        assert set(POS_TABLES) <= set(own), (POS_TABLES, own)
        others = await conn.fetch(
            "SELECT relname FROM pg_class WHERE relname LIKE 'pos\\_%' "
            "   AND relkind IN ('r', 'v') AND NOT relname = ANY($1::text[])",
            own)
        await conn.execute(down)
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_class WHERE relname = ANY($1::text[]) "
            "   AND relkind IN ('r', 'v')", own) == 0
        assert sorted(r["relname"] for r in await conn.fetch(
            "SELECT relname FROM pg_class WHERE relname LIKE 'pos\\_%' "
            "   AND relkind IN ('r', 'v')")) == sorted(
                r["relname"] for r in others)
        await conn.execute(up)
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_class WHERE relname = ANY($1::text[]) "
            "   AND relkind = 'r'", list(POS_TABLES)) == len(POS_TABLES)
        await conn.execute(
            "INSERT INTO pos_forecasts (forecast_id, book, run_id, "
            " issued_at, issued_day, horizon_start, horizon_end, "
            " horizon_days, method, status, why, inputs_sha256, version) "
            "VALUES ('f','PAPER','r',now(),current_date,now(),"
            " now() + interval '30 days',30,'m','UNAVAILABLE','X','s','v')")
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.RaiseError, match="rollback refused"):
            await conn.execute(down)
        await sp.rollback()
    finally:
        await tr.rollback()
        await conn.close()


# ── §3 runtime ───────────────────────────────────────────────────────

@pg
async def test_a_full_cycle_writes_only_research_rows():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        s = await P.scenario(conn)
        await P.actual_position(conn, now=s["now"])
        before = await F.protected_counts(conn)
        got = await RUN.run_cycle(conn, now=s["now"],
                                  account_id=s["acct"]["account_id"],
                                  include_actual=True)
        after = await F.protected_counts(conn)
        assert got["ran"] is True, got
        assert set(got["components"].values()) == {"OK"}, got
        assert before == after
        for t in POS_TABLES:
            bad = await conn.fetchval(
                "SELECT count(*) FROM %s WHERE label <> 'RESEARCH' "
                "    OR authority <> 'SHADOW_NO_AUTHORITY'" % t)
            assert bad == 0, t
        assert await conn.fetchval(
            "SELECT count(*) FROM pos_runs WHERE run_id=$1",
            got["run_id"]) == 7
    finally:
        await tr.rollback()
        await conn.close()


# ── §4 fail-closed / failure isolation ───────────────────────────────

@pg
async def test_a_failing_component_does_not_stop_the_cycle(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(R, "capacity_candidates", boom)
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        s = await P.scenario(conn)
        got = await RUN.run_cycle(conn, now=s["now"],
                                  account_id=s["acct"]["account_id"],
                                  include_actual=False)
        c = got["components"]
        assert c["CAPACITY"] == "FAILED"
        assert c["ECONOMICS"] == c["WAREHOUSE"] == c["NORTH_STAR"] == "OK"
        assert c["FORECAST"] == "OK"
        err = await conn.fetchval(
            "SELECT error FROM pos_runs WHERE run_id=$1 AND "
            " component='CAPACITY'", got["run_id"])
        assert "synthetic failure" in err
        assert await conn.fetchval(
            "SELECT status FROM pos_runs WHERE run_id=$1 AND "
            " component='CYCLE'", got["run_id"]) == "FAILED"
        # the forecast's capacity ceiling is named unmeasured, not zero
        fc = await conn.fetchrow(
            "SELECT capacity_ceiling_usd, unmeasured FROM pos_forecasts "
            " WHERE run_id=$1", got["run_id"])
        assert fc["capacity_ceiling_usd"] is None
        assert "capacity_ceiling_usd" in P.j(fc["unmeasured"])
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_a_failed_economics_skips_its_dependants(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("synthetic economics failure")

    monkeypatch.setattr(R, "paper_positions", boom)
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        s = await P.scenario(conn)
        got = await RUN.run_cycle(conn, now=s["now"],
                                  account_id=s["acct"]["account_id"],
                                  include_actual=False)
        c = got["components"]
        assert c["ECONOMICS"] == "FAILED"
        assert c["WAREHOUSE"] == c["CAPITAL"] == "SKIPPED"
        assert c["NORTH_STAR"] == c["FORECAST"] == "SKIPPED"
        assert await conn.fetchval(
            "SELECT count(*) FROM pos_metric_observations WHERE run_id=$1",
            got["run_id"]) == 0
    finally:
        await tr.rollback()
        await conn.close()


class _NoTables:
    async def fetchval(self, sql, *a):
        return False


async def test_without_migration_216_the_cycle_idles():
    got = await RUN.run_cycle(_NoTables(), now=time.time())
    assert got == {"ran": False, "why": "MIGRATION_216_NOT_APPLIED"}


# ── §5 the reads ─────────────────────────────────────────────────────

ROUTES = ("/api/command/profitability",
          "/api/command/profitability/north-star",
          "/api/command/profitability/capital",
          "/api/command/profitability/capacity",
          "/api/command/profitability/forecast",
          "/api/command/profitability/warehouse/paperpos:a:b:c:LONG")


def test_the_routes_are_get_only_and_require_a_command_session():
    from sportsassets.api import app as APP

    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "").startswith("/api/command/profitability"):
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert {"/api/command/profitability",
            "/api/command/profitability/north-star",
            "/api/command/profitability/capital",
            "/api/command/profitability/capacity",
            "/api/command/profitability/forecast",
            "/api/command/profitability/warehouse/{position_key:path}"} <= \
        set(paths)
    for p, methods in paths.items():
        assert methods <= {"GET", "HEAD"}, (p, methods)
    client = TestClient(APP.app, raise_server_exceptions=False)
    for route in ROUTES:
        assert client.get(route).status_code == 401, route
        assert client.post(route).status_code in (401, 404, 405), route


# ── §6 arming and the list ───────────────────────────────────────────

def test_the_runner_is_kill_switchable_and_armed_in_the_lifespan(
        monkeypatch):
    monkeypatch.setenv("POS_ECON", "off")
    assert RUN.enabled() is False
    monkeypatch.setenv("POS_ECON", "0")
    assert RUN.enabled() is False
    monkeypatch.delenv("POS_ECON")
    assert RUN.enabled() is True
    app_src = (PKG / "api" / "app.py").read_text()
    assert "from ..profitability import runner as _POS" in app_src
    assert "_POS.run(_cap_pool)" in app_src
    assert "pos_task" in app_src.split("tasks = [t for t in (")[1][:500]
    assert RUN.CYCLE_S >= 600 and RUN.STATEMENT_TIMEOUT_MS <= 30000
    assert RUN.LOCK_KEY != 0x494E5431            # not the intel runner's lock


def test_these_proofs_are_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    for name in ("test_profitability_is_research_only.py",
                 "test_profitability_economics.py",
                 "test_profitability_capacity.py",
                 "test_profitability_metrics_forecast.py",
                 "test_profitability_warehouse_cycle.py"):
        assert "tests/%s" % name in listed.splitlines(), name
