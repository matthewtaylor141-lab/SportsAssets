"""CAPITAL-CRITICAL: THE TWIN / PROFITABILITY LAYER IS RESEARCH -- IT CANNOT
REACH A VENUE, AN ORDER, A CONTROL OR A SIZE, AND IT WRITES ONLY ITS OWN
TABLES.

  §1 STATIC. Every module in sportsassets/twin imports only the standard
     library, its own package, and the pure intel common / attribution and
     quality-scorecard statistics -- over the TRANSITIVE import closure. No
     venue, order, execution, paper-ledger or agent-execution module is
     reachable. Every SQL write in sportsassets/twin targets a twin_* table,
     is an INSERT, and lives in store.py. The GET routes read only twin_*
     tables. No production module imports the twin (nothing consumes a
     research result to decide).
  §2 THE DATABASE. Migration 219 is idempotent; its rollback touches only
     twin_* objects and refuses while preregistrations / results /
     recommendations exist; every table is append-only; labels and
     research flags are CHECKed.
  §3 RUNTIME. A full cycle over synthetic rows changes no order, fill,
     ledger, settlement, decision, valuation, control, execution or funded
     table (counts AND control-table contents); every row it writes is
     labelled.
  §4 FAILURE ISOLATION. A component that raises is recorded FAILED and the
     rest of the cycle still runs.
  §5 THE READS. The seven routes are GET only, require a command session
     (401 without), and answer RESEARCH envelopes; no run -> EMPTY with a
     reason, never zeros.
  §6 THE ARMING. The runner is kill-switchable (POS_TWIN=off) and armed in
     the API lifespan; these tests are on the capital-critical list.
"""
from __future__ import annotations

import ast
import pathlib
import re
import time
import warnings
from contextlib import asynccontextmanager

import asyncpg
import pytest
from fastapi.testclient import TestClient

from sportsassets.twin import runner as RUN
from sportsassets.twin import transfer as TR

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
    from tests import twin_fixture as TF
except ImportError:                                             # pragma: no cover
    import intel_fixture as F
    import paper_harness as H
    import twin_fixture as TF

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
TWIN = sorted((PKG / "twin").glob("*.py"))
API = PKG / "api" / "command_twin.py"
MIG = ROOT / "migrations"

STDLIB = {"__future__", "asyncio", "datetime", "hashlib", "json", "logging",
          "math", "os", "random", "time", "uuid", "zoneinfo", "typing",
          "contextlib", "dataclasses", "decimal", "collections", "copy",
          "statistics", "glob", "re"}
ALLOWED_PROJECT = {"sportsassets.simulated_account_context", "sportsassets", "sportsassets.intel",
                   "sportsassets.intel.common",
                   "sportsassets.intel.attribution",
                   # SELECT-only, pinned by tests/test_intel_is_shadow_only
                   "sportsassets.intel.reads",
                   "sportsassets.agents",
                   "sportsassets.agents.quality_stats"}
FORBIDDEN = ("execmirror", "kalshi", "pmus", "venue", "clob", "executor",
             "execution_intent", "execution_gate", "paper", "bettor_funded",
             "derek", "xavier", "karen", "maker", "smalllive", "order",
             "live_", "actual_admission", "pinnapi", "submit", "sizing",
             "allocator", "runtime")
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE|"
                   r"ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+TABLE|COPY)\s+"
                   r"([A-Za-z_][A-Za-z0-9_]*)")
ROUTES = ("/api/command/twin", "/api/command/twin/scenarios/twinscn:x",
          "/api/command/research/transfer",
          "/api/command/profitability/scorecards",
          "/api/command/profitability/evidence-ladder",
          "/api/command/profitability/kill-switches", "/api/command/evals")
TWIN_TABLES = ("twin_runs", "twin_snapshots", "twin_frozen_specs",
               "twin_scenarios", "twin_scenario_results",
               "twin_decision_traces", "twin_transfer_tests",
               "twin_transfer_evaluations", "twin_agent_scorecards",
               "twin_evidence_ladder", "twin_kill_switch_recommendations",
               "twin_evals")


def _module_name(path):
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(path):
    mod = _module_name(path)
    pkg = mod if path.name == "__init__.py" else mod.rsplit(".", 1)[0]
    out = set()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
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


def _path_of(name):
    p = ROOT / pathlib.Path(*name.split("."))
    if p.with_suffix(".py").exists():
        return p.with_suffix(".py")
    if (p / "__init__.py").exists():
        return p / "__init__.py"
    return None


def _sql_writes(path):
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for kw, table in WRITE.findall(node.value):
                if kw.upper() == "UPDATE" and table.upper() == "SET":
                    continue
                out.append((" ".join(kw.split()).upper(), table))
    return out


# ── §1 static ────────────────────────────────────────────────────────

def test_no_venue_order_or_execution_module_is_reachable():
    seen, todo = set(), [_module_name(p) for p in TWIN + [API]]
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
            if top == "fastapi" and name == _module_name(API):
                continue
            if top != "sportsassets":
                assert top in STDLIB, (name, imp)
                continue
            if _path_of(imp) is None:
                continue
            if name == _module_name(API) and imp == (
                    "sportsassets.api.agents_core"):
                continue      # require_read / _pool: the shared auth seam
            assert imp.startswith("sportsassets.twin") or \
                imp in ALLOWED_PROJECT, (name, imp)
            todo.append(imp)
    reached = {s for s in seen if s.startswith("sportsassets")}
    assert "sportsassets.intel.attribution" in reached
    for name in reached:
        leaf = name.rsplit(".", 1)[-1]
        assert not any(f in leaf for f in FORBIDDEN), name


def test_every_sql_write_is_an_insert_into_a_twin_table_in_the_store():
    found = 0
    for path in TWIN:
        writes = _sql_writes(path)
        if path.name != "store.py":
            assert not writes, (path.name, writes)
            continue
        for kw, table in writes:
            found += 1
            assert kw == "INSERT INTO", (kw, table)
            assert table in TWIN_TABLES, table
    assert found >= 10


def test_the_routes_read_only_twin_tables_and_write_nothing():
    assert not _sql_writes(API)
    src = API.read_text()
    tables = set(re.findall(r"(?<!epoch )\bFROM\s+([a-z_]+)", src))
    tables -= {"window_start", "window_end", "computed_at", "frozen_at",
               "started_at", "declared_at", "evaluated_at", "created_at",
               "decision_at", "max_input_at"}
    assert tables and all(t.startswith("twin_") for t in tables), tables


def test_no_production_module_consumes_the_research_layer():
    offenders = []
    for p in PKG.rglob("*.py"):
        rel = p.relative_to(PKG)
        if rel.parts[0] == "twin" or str(rel) in ("api/command_twin.py",
                                                  "api/app.py"):
            continue
        for imp in _imports(p):
            if imp.startswith("sportsassets.twin") or imp.endswith(
                    ".twin") or ".twin." in imp:
                offenders.append((str(rel), imp))
    assert not offenders, offenders
    app = (PKG / "api" / "app.py").read_text()
    uses = re.findall(r"_TWIN\.[a-z_]+", app)
    assert set(uses) <= {"_TWIN.enabled", "_TWIN.run"}, uses


# ── §2 the database ──────────────────────────────────────────────────

@pg
async def test_migration_219_is_idempotent_and_its_rollback_is_scoped():
    up = (MIG / "219_digital_twin_evidence.sql").read_text()
    down = (MIG / "rollback" / "219_digital_twin_evidence.down.sql"
            ).read_text()
    drops = [ln for ln in down.splitlines() if ln.startswith("DROP ")]
    assert drops and all(re.match(r"DROP (TABLE|FUNCTION) IF EXISTS twin_",
                                  ln) for ln in drops), drops
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute(up)
        await conn.execute(up)
        await conn.execute(down)
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_class WHERE relname LIKE 'twin\\_%' "
            "   AND relkind = 'r'") == 0
        await conn.execute(up)
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_class WHERE relname LIKE 'twin\\_%' "
            "   AND relkind = 'r'") == len(TWIN_TABLES)
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_the_rollback_refuses_over_research_evidence():
    down = (MIG / "rollback" / "219_digital_twin_evidence.down.sql"
            ).read_text()
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute(
            "INSERT INTO twin_frozen_specs (spec_id, kind, version, spec, "
            " spec_sha256, frozen_at) VALUES ('KILL_SWITCH_CRITERIA:v99:' "
            " || left(repeat('c', 64), 16), 'KILL_SWITCH_CRITERIA', 99, "
            " '{}', repeat('c', 64), now())")
        await conn.execute(
            "INSERT INTO twin_kill_switch_recommendations (recommendation_id,"
            " run_id, criterion, book, strategy, evidence, evidence_sha256, "
            " criteria_spec_id, criteria_sha256, created_at) VALUES ('k', "
            " 'r', 'REGIME_SHIFT', 'NONE', '*', '{}', repeat('a', 64), "
            " 'KILL_SWITCH_CRITERIA:v99:' || left(repeat('c', 64), 16), "
            " repeat('c', 64), now())")
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(down)
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_every_twin_table_is_append_only():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        for t in TWIN_TABLES:
            trg = await conn.fetchval(
                "SELECT count(*) FROM pg_trigger WHERE tgrelid = $1::regclass"
                "   AND tgname = $2", t, t + "_append_only_trg")
            assert trg == 1, t
        await conn.execute(
            "INSERT INTO twin_runs (run_id, component, started_at, "
            " finished_at, status, duration_ms, version) VALUES ('a', "
            " 'CYCLE', now(), now(), 'OK', 0, 'v')")
        for sql in ("UPDATE twin_runs SET status='FAILED' WHERE run_id='a'",
                    "DELETE FROM twin_runs WHERE run_id='a'"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                await conn.execute(sql)
            await sp.rollback()
        for sql in (
                "INSERT INTO twin_runs (run_id, component, started_at, "
                " finished_at, status, duration_ms, version, label) VALUES "
                " ('b', 'CYCLE', now(), now(), 'OK', 0, 'v', 'LIVE')",
                "INSERT INTO twin_runs (run_id, component, started_at, "
                " finished_at, status, duration_ms, version, authority) "
                " VALUES ('c', 'CYCLE', now(), now(), 'OK', 0, 'v', "
                " 'TRADE')"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(sql)
            await sp.rollback()
    finally:
        await tr.rollback()
        await conn.close()


# ── §3 runtime ───────────────────────────────────────────────────────

async def _seed(conn, now):
    acct = await H.new_account(conn, "twinauth", now=now - 86400)
    exp = F.uid("TWIN_AUTH_")
    vid = await F.valuation(conn, experiment_id=exp, at=now - 4000, p=0.62,
                            outcome=1)
    d = await F.decision(conn, acct, at=now - 600, p=0.62, vwap=0.52,
                         fees_usd=1.0, qty=100, depth=500.0, valuation_id=vid)
    g = await F.position(conn, acct, slug=d["slug"], qty=100, price=0.53,
                         fee=1.0, at=now - 590, decision_id=d["decision_id"],
                         event_key=d["event_key"])
    await F.book(conn, d["slug"], now - 595, bids=((0.55, 100),),
                 offers=((0.57, 100),))
    await F.settle(conn, acct, group_id=g, slug=d["slug"], qty=100,
                   outcome="WON", payout_per_contract=1.0, at=now - 60)
    return acct, d, g


@pg
async def test_a_full_cycle_leaves_production_tables_unchanged():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        acct, d, g = await _seed(conn, now)
        before = await F.protected_counts(conn)
        ctl = await TF.control_snapshot(conn)
        other = {t: await conn.fetchval('SELECT count(*) FROM "%s"' % t)
                 for t in ("intel_runs", "intel_snapshots",
                           "position_postmortems", "karen_challenges",
                           "agent_findings", "paper_audrey_findings")
                 if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        t)}
        got = await RUN.run_cycle(conn, now=now,
                                  account_id=acct["account_id"],
                                  include_actual=True)
        assert got["ran"] is True
        assert set(got["components"].values()) == {"OK"}, got
        assert await F.protected_counts(conn) == before
        assert await TF.control_snapshot(conn) == ctl
        for t, n in other.items():
            assert await conn.fetchval('SELECT count(*) FROM "%s"' % t) == n
        for t, col, ok in (("twin_scenario_results", "label",
                            {"COUNTERFACTUAL"}),
                           ("twin_decision_traces", "label",
                            {"COUNTERFACTUAL"}),
                           ("twin_agent_scorecards", "label", {"RESEARCH"}),
                           ("twin_evals", "label", {"RESEARCH"}),
                           ("twin_runs", "authority",
                            {"RESEARCH_NO_AUTHORITY"})):
            vals = {r[0] for r in await conn.fetch(
                'SELECT DISTINCT "%s" FROM "%s"' % (col, t))}
            assert vals and vals <= ok, (t, vals)
        assert await conn.fetchval(
            "SELECT bool_and(research_only AND NOT production_truth AND NOT "
            " summed_across_books) FROM twin_scenario_results")
    finally:
        await tr.rollback()
        await conn.close()


# ── §4 failure isolation ─────────────────────────────────────────────

@pg
async def test_a_failing_component_does_not_stop_the_cycle(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(TR, "load", boom)
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        acct, d, g = await _seed(conn, now)
        got = await RUN.run_cycle(conn, now=now,
                                  account_id=acct["account_id"],
                                  include_actual=False)
        c = got["components"]
        assert c["TRANSFER"] == "FAILED"
        assert {k: v for k, v in c.items() if k != "TRANSFER"} == {
            "TWIN": "OK", "SCORECARDS": "OK", "LADDER": "OK",
            "KILL_SWITCHES": "OK", "EVALS": "OK"}
        err = await conn.fetchval(
            "SELECT error FROM twin_runs WHERE run_id=$1 AND "
            " component='TRANSFER'", got["run_id"])
        assert "synthetic failure" in err
        assert await conn.fetchval(
            "SELECT status FROM twin_runs WHERE run_id=$1 AND "
            " component='CYCLE'", got["run_id"]) == "FAILED"
    finally:
        await tr.rollback()
        await conn.close()


# ── §5 the reads ─────────────────────────────────────────────────────

def test_the_routes_are_get_only_and_require_a_command_session():
    from sportsassets.api import app as APP

    paths, stack = {}, list(APP.app.routes)
    prefixes = ("/api/command/twin", "/api/command/research",
                "/api/command/profitability", "/api/command/evals")
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "").startswith(prefixes):
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert {"/api/command/twin", "/api/command/twin/scenarios/{scenario_id}",
            "/api/command/research/transfer",
            "/api/command/profitability/scorecards",
            "/api/command/profitability/evidence-ladder",
            "/api/command/profitability/kill-switches",
            "/api/command/evals"} <= set(paths)
    for p, methods in paths.items():
        assert methods <= {"GET", "HEAD"}, (p, methods)
    client = TestClient(APP.app, raise_server_exceptions=False)
    for route in ROUTES:
        assert client.get(route).status_code == 401, route
        assert client.post(route).status_code in (401, 405), route


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@pg
async def test_the_reads_serve_research_envelopes(monkeypatch):
    from sportsassets.api import command_twin as CT

    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        async def pool():
            return _Pool(conn)

        monkeypatch.setattr(CT, "_pool", pool)
        if not await conn.fetchval("SELECT count(*) FROM twin_snapshots"):
            empty = await CT.twin_index()
            assert empty["status"] == "EMPTY"
            assert empty["why"] == "NO_TWIN_RUN_YET"
            assert empty["data"] is None and empty["label"] == "RESEARCH"
            lad = await CT.profitability_ladder()
            assert lad["status"] == "EMPTY" and lad["data"] is None
        now = time.time()
        acct, d, g = await _seed(conn, now)
        await RUN.run_cycle(conn, now=now, account_id=acct["account_id"],
                            include_actual=False)
        idx = await CT.twin_index()
        assert idx["status"] == "OK" and idx["label"] == "RESEARCH"
        assert idx["authority"] == "RESEARCH_NO_AUTHORITY"
        assert idx["production_truth"] is False
        assert idx["summed_across_books"] is False
        sid = idx["data"]["scenarios"][0]["scenario_id"]
        sc = await CT.twin_scenario(scenario_id=sid, limit=50)
        assert sc["status"] == "OK" and sc["results"]
        assert sc["results"][0]["label"] == "COUNTERFACTUAL"
        assert sc["scenario"]["spec"]["research_only"] is True
        missing = await CT.twin_scenario(scenario_id="twinscn:none", limit=1)
        assert missing["status"] == "EMPTY"
        trf = await CT.research_transfer()
        assert trf["status"] == "OK" and trf["transfer_assumed"] is False
        cards = await CT.profitability_scorecards(agent="")
        assert set(cards["agents"]) == {"DEREK", "XAVIER", "ARCHER", "SCOUT",
                                        "KAREN", "ALLOCATOR", "AUDREY"}
        lad = await CT.profitability_ladder()
        assert lad["status"] == "OK" and lad["never_skips_a_level"] is True
        assert 0 <= lad["level"] <= 6 and lad["criteria"]["levels"]
        ks = await CT.profitability_kill_switches(limit=10)
        assert ks["status"] == "OK" and ks["stops_capital"] is False
        assert ks["effect"] == "RECOMMEND_PAUSE_RECORD_ONLY"
        ev = await CT.evals_index(scope="")
        assert ev["status"] == "OK" and ev["rows"] and ev["macro"]
    finally:
        await tr.rollback()
        await conn.close()


# ── §6 arming and the list ───────────────────────────────────────────

def test_the_runner_is_kill_switchable_and_armed_in_the_lifespan(
        monkeypatch):
    monkeypatch.setenv("POS_TWIN", "off")
    assert RUN.enabled() is False
    monkeypatch.delenv("POS_TWIN")
    assert RUN.enabled() is True
    app_src = (PKG / "api" / "app.py").read_text()
    assert "from ..twin import runner as _TWIN" in app_src
    assert "_TWIN.run(_cap_pool)" in app_src
    assert "twin_task" in app_src.split("tasks = [t for t in (")[1][:400]
    assert RUN.CYCLE_S >= 300 and RUN.STATEMENT_TIMEOUT_MS <= 30000
    assert RUN.LOCK_KEY != 0x494E5431                 # not the intel lock


def test_these_proofs_are_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    for name in ("test_twin_authority.py", "test_twin_replay.py",
                 "test_twin_profitability.py"):
        assert "tests/%s" % name in listed.splitlines(), name
