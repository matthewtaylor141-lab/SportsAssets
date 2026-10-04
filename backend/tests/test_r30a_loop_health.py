"""R30A WORKER RELIABILITY: the loop inventory, the single-writer leases with
fencing, the health record (migration 229) and GET /api/command/loop-health.

  §1 INVENTORY. Every loop workers/all.py registers is inventoried (started or
     not started by design); every cadence, key and env flag copied into
     loop_health is pinned to the loop's own constant; every CAPITAL-CRITICAL
     loop has a lease that is a lock (or a child fenced by one) and a source
     its success can be read from.
  §2 FENCING. A child the writer lock does not cover does nothing: the
     servicing pass and the reactive job are refused (counted), and the next
     pass asks again.
  §3 VERDICT. HEALTHY within 3 x cadence, UNHEALTHY beyond it, UNHEALTHY when
     a critical armed loop's lock is held by no backend, DISABLED when not
     armed, UNAVAILABLE when nothing is recorded -- never a made-up success.
  §4 RECORD + READ (Postgres). START / SUCCESS / ERROR rows with counters;
     SUCCESS throttled per loop; a missing table is a logged False, never a
     raise; the reader combines the record, service_heartbeats, ingestion_state
     and pg_locks holders on production-shaped rows.
  §5 MIGRATION 229. Idempotent; the row cannot be deleted; the rollback drops
     only 229's objects.
  §6 ENDPOINT. GET only, COMMAND auth, READ ONLY transaction; the modules
     import no order / venue / execution / paper / funded module.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import pathlib
import time

import pytest

from sportsassets import loop_health as LH

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")


# ── §1 inventory ───────────────────────────────────────────────────────

def test_every_workers_loop_is_inventoried():
    from sportsassets.workers import all as W
    names = [n for n, _fn in W.LOOPS]
    inventoried = {s["name"] for s in LH.WORKERS_LOOPS}
    assert set(LH.WORKERS_NOT_STARTED) == set(W.VENUE_WRITE_LOOPS)
    missing = [n for n in names if n not in inventoried
               and n not in LH.WORKERS_NOT_STARTED]
    assert not missing, "workers loops with no inventory entry: %s" % missing
    stale = inventoried - set(names)
    assert not stale, "inventory entries no loop has: %s" % stale


def test_every_api_lifespan_runner_is_inventoried():
    """The recurring tasks api/app.py's lifespan creates, by the module that
    runs them."""
    src = (ROOT / "sportsassets" / "api" / "app.py").read_text()
    lifespan = src[src.index("async def lifespan"):src.index("app = FastAPI(")]
    expected = {
        "_DESKLOOP.run": "bettor_desk_loop", "_RN1X.run": "rn1x_shadow",
        "_RN1XL.run": "rn1x_learn", "_EXT.run": "ext_pinnacle.entry_cycle",
        "_RN1XM.run": "rn1x_model", "_CAP.run": "agents.capability_runtime",
        "_KAREN.run": "agents.karen_runner",
        "_PEER.run": "agents.peer_responder",
        "_EDDIE.run": "agents.eddie_runner",
        "_SCOUT.run": "agents.scout_runner", "_EXM.run": "execmirror.tick",
        "_INTEL.run": "intel.runner", "_POS.run": "profitability.runner",
        "_POSLEARN.run": "position_learning.runner",
        "_TWIN.run": "twin.runner",
        "_IMPROVE.run": "agents.improvement_pipeline",
        "_WATCHDOG.start": "api.loop_watchdog",
        "_desk_feed_warm_loop": "api.desk_feed_warm",
        "refresh_whale_idents_loop": "api.whale_idents_refresh",
        "_trim_loop": "api.malloc_trim"}
    names = {s["name"] for s in LH.API_LOOPS}
    for call, name in expected.items():
        assert call in lifespan, "%s no longer in the lifespan" % call
        assert name in names, name
    # the decider's children are inventoried too
    for child in ("ext_pinnacle.servicing", "pinnapi_feed.heartbeat",
                  "pinnapi_held.refresh", "pinnapi_reactive"):
        assert child in names


def test_copied_constants_match_the_loops():
    from sportsassets import bettor_desk_loop as DESK
    from sportsassets import execmirror as EXM
    from sportsassets import pinnapi_feed_runtime as FR
    from sportsassets import pinnapi_held as PH
    from sportsassets import pinnapi_owner as PO
    from sportsassets.agents import capability_runtime as CAP
    from sportsassets.agents import eddie_runner, improvement_pipeline
    from sportsassets.agents import karen_runner, peer_responder, scout_runner
    from sportsassets.intel import runner as INTEL
    from sportsassets.poslearn import runner as POSL
    from sportsassets.profitability import runner as POS
    from sportsassets.twin import runner as TWIN
    from sportsassets.workers import ext_pinnacle_loop as EXT
    from sportsassets.workers import rn1x_learn_loop as LRN
    from sportsassets.workers import rn1x_model_loop as MOD
    from sportsassets.workers import rn1x_shadow as SHD

    B = {s["name"]: s for s in LH.API_LOOPS}
    assert B["ext_pinnacle.entry_cycle"]["cadence_s"] == EXT.CYCLE_S
    assert B["ext_pinnacle.entry_cycle"]["lease"]["key"] == EXT.LOCK_KEY
    assert B["ext_pinnacle.entry_cycle"]["armed"][1] == EXT.ENV_FLAG
    assert B["ext_pinnacle.servicing"]["cadence_s"] == EXT.SERVICING_INTERVAL_S
    srcs = dict((s[1], s) for s in B["ext_pinnacle.entry_cycle"]["sources"])
    assert EXT.HEARTBEAT_KEY in srcs
    srcs = dict((s[1], s) for s in B["ext_pinnacle.servicing"]["sources"])
    assert EXT.SERVICING_KEY in srcs
    assert B["execmirror.tick"]["lease"]["key"] == EXM.LOCK_KEY
    assert B["bettor_desk_loop"]["lease"]["key"] == DESK.LOCK_KEY
    assert B["bettor_desk_loop"]["cadence_s"] == DESK.CYCLE_S
    assert B["bettor_desk_loop"]["armed"][1] == DESK.ENABLE_ENV
    assert B["rn1x_shadow"]["lease"]["key"] == SHD.LOCK_KEY
    assert B["rn1x_shadow"]["cadence_s"] == SHD.TICK_S
    assert B["rn1x_learn"]["lease"]["key"] == LRN.LOCK_KEY
    assert B["rn1x_learn"]["cadence_s"] == LRN.CYCLE_S
    assert B["rn1x_model"]["lease"]["key"] == MOD.LOCK_KEY
    assert B["rn1x_model"]["cadence_s"] == MOD.CYCLE_S
    assert B["rn1x_model"]["armed"][1] == MOD.ENV_FLAG
    assert B["rn1x_model"]["sources"][0][1] == MOD.HEARTBEAT_KEY
    assert B["pinnapi_feed.heartbeat"]["lease"]["key"] == PO.FEED_LOCK_KEY
    assert B["pinnapi_feed.heartbeat"]["cadence_s"] == FR.HEARTBEAT_S
    assert B["pinnapi_feed.heartbeat"]["sources"][0][1] == FR.HEARTBEAT_KEY
    assert B["pinnapi_held.refresh"]["cadence_s"] == PH.HELD_REFRESH_S
    for mod, name in ((INTEL, "intel.runner"), (POS, "profitability.runner"),
                      (POSL, "position_learning.runner"),
                      (TWIN, "twin.runner")):
        assert B[name]["lease"]["key"] == mod.LOCK_KEY, name
        assert B[name]["cadence_s"] == mod.CYCLE_S, name
        assert B[name]["armed"][1] == mod.ENV_KILL, name
    for mod, name in ((karen_runner, "agents.karen_runner"),
                      (eddie_runner, "agents.eddie_runner"),
                      (scout_runner, "agents.scout_runner"),
                      (peer_responder, "agents.peer_responder"),
                      (improvement_pipeline, "agents.improvement_pipeline")):
        assert B[name]["cadence_s"] == mod.INTERVAL_S, name
    assert B["agents.karen_runner"]["sources"][0][1] == karen_runner.SERVICE
    assert B["agents.capability_runtime"]["sources"][0][1] == CAP.HEARTBEAT
    # the six production lock holders and the feed: distinct keys
    keys = [s["lease"]["key"] for s in LH.API_LOOPS
            if s["lease"]["kind"] in ("ADVISORY_LOCK", "OWN_LEASE")]
    assert len(keys) == len(set(keys))


def test_every_capital_critical_loop_has_a_lock_or_a_fenced_parent():
    crit = [s for s in LH.INVENTORY if s["capital_critical"]]
    assert {s["name"] for s in crit} == {
        "ext_pinnacle.entry_cycle", "ext_pinnacle.servicing",
        "pinnapi_feed.heartbeat", "pinnapi_held.refresh", "pinnapi_reactive",
        "execmirror.tick"}
    for s in crit:
        lease = s["lease"]
        assert lease["kind"] in ("ADVISORY_LOCK", "OWN_LEASE", "CHILD_OF"), s
        assert lease.get("key") is not None, s["name"]
        assert lease.get("fencing"), "%s names no fencing" % s["name"]
        if lease["kind"] == "CHILD_OF":
            assert (lease["parent"], "api") in LH.BY_NAME
        assert s["sources"], "%s has nowhere its success is read" % s["name"]
        assert s["process"] == "api"


# ── §2 fencing ─────────────────────────────────────────────────────────

def test_a_servicing_pass_the_writer_lock_does_not_cover_services_nothing(
        monkeypatch):
    from sportsassets.workers import ext_pinnacle_loop as L

    served, recorded = [], []

    class _Conn:
        async def execute(self, sql, *args):
            if "runtime_loop_health" in sql:
                recorded.append((args[0], args[3], args[4]))
            return "OK"

    class _Ctx:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *exc):
            return False

    class _Pool:
        def acquire(self, timeout=None):
            return _Ctx()

    async def _service_once(conn, **kw):
        served.append(1)
        return {"ok": True}

    async def _beat(conn, res):
        return None

    answers = [False, True]

    async def fence(conn):
        return answers.pop(0)

    async def _sleep(_s):
        return None

    monkeypatch.setattr(L, "_SERVICING", L._servicing_state())
    monkeypatch.setattr(L, "_service_once", _service_once)
    monkeypatch.setattr(L, "_servicing_heartbeat", _beat)
    LH._last_write.clear()
    asyncio.run(L._servicing_loop(_Pool(), interval_s=1.0, sleep=_sleep,
                                  max_passes=2, fence=fence))
    assert served == [1], "the fenced-out pass serviced nothing; the next ran"
    assert L._SERVICING["fenced_out"] == 1
    assert ("ext_pinnacle.servicing", "ERROR",
            "FENCED_OUT_WRITER_LOCK_NOT_HELD") in recorded
    assert ("ext_pinnacle.servicing", "SUCCESS", None) in recorded


def test_a_reactive_job_outside_the_writer_lock_is_refused():
    from tests.test_r30a_pool_starvation_root_cause import (_Session,
                                                            _scheduler)

    async def main():
        conns, records, calls, done = [], [], [], asyncio.Event()
        s, cache, tick = _scheduler(_Session(conns), records, calls, done)

        async def fence(conn):
            return False
        s.fence = fence
        task = asyncio.create_task(s.run())
        try:
            tick(cache)
            for _ in range(200):
                if s.counts["FENCED_OUT"]:
                    break
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        return records, calls, s
    records, calls, s = asyncio.run(main())
    assert s.counts["FENCED_OUT"] == 1
    assert records == [] and calls == [], "nothing audited, nothing evaluated"


# ── §3 verdict ─────────────────────────────────────────────────────────

NOW = 1_791_140_000.0


def _spec(name):
    return LH.BY_NAME[(name, "api")]


def test_healthy_within_three_cadences_unhealthy_beyond():
    s = _spec("ext_pinnacle.servicing")          # cadence 60 s
    ok = LH.classify(s, {"success_at": [(NOW - 179, "x")],
                         "sources_read": ["x"]}, now=NOW,
                     holders=[{"pid": 1}])
    assert ok["status"] == LH.HEALTHY and ok["lag_s"] == 179
    bad = LH.classify(s, {"success_at": [(NOW - 181, "x")],
                          "sources_read": ["x"]}, now=NOW)
    assert bad["status"] == LH.UNHEALTHY
    assert bad["why"] == "NO_SUCCESS_WITHIN_3X_CADENCE"


def test_the_newest_success_of_any_source_counts():
    s = _spec("ext_pinnacle.entry_cycle")        # cadence 900 s
    got = LH.classify(s, {"success_at": [(NOW - 5000, "runtime_loop_health"),
                                         (NOW - 600, "ingestion_state")],
                          "sources_read": ["a", "b"]}, now=NOW,
                      holders=[{"pid": 7}])
    assert got["status"] == LH.HEALTHY
    assert got["success_source"] == "ingestion_state"


def test_a_critical_armed_loop_whose_lock_nobody_holds_is_unhealthy():
    s = _spec("execmirror.tick")
    got = LH.classify(s, {"success_at": [(NOW - 5, "x")],
                          "sources_read": ["x"]}, now=NOW, holders=[])
    assert got["status"] == LH.UNHEALTHY
    assert got["why"] == "WRITER_LOCK_HELD_BY_NO_BACKEND"


def test_disabled_and_unavailable_are_named_never_successes():
    s = _spec("bettor_desk_loop")
    off = LH.classify(s, {}, now=NOW, is_armed=False,
                      armed_why="ENV_BETTOR_DESK_LOOP_NOT_ON")
    assert off["status"] == LH.DISABLED
    assert off["why"] == "ENV_BETTOR_DESK_LOOP_NOT_ON"
    on = LH.classify(s, {}, now=NOW, is_armed=True)
    assert on["status"] == LH.UNAVAILABLE
    assert on["why"] == "NO_PERSISTED_HEALTH_SOURCE"
    s = _spec("execmirror.tick")
    none = LH.classify(s, {"success_at": [], "sources_read": ["x"]},
                       now=NOW, holders=[{"pid": 1}])
    assert none["status"] == LH.UNAVAILABLE
    assert none["why"] == "NO_SUCCESS_RECORDED"
    ev = LH.classify(_spec("pinnapi_reactive"),
                     {"success_at": [(NOW - 9, "a")], "sources_read": ["a"]},
                     now=NOW)
    assert ev["status"] == LH.EVENT_DRIVEN


def test_the_isolated_position_learning_layer_is_unavailable_not_read():
    """That layer's tables may be read only inside it (its authority test
    pins the isolation), so the inventory lists its runner with no source
    and reports it UNAVAILABLE by name -- never a manufactured status."""
    s = _spec("position_learning.runner")
    assert s["sources"] == () and not s["capital_critical"]
    got = LH.classify(s, {}, now=NOW, is_armed=True)
    assert got["status"] == LH.UNAVAILABLE
    assert got["why"] == "NO_PERSISTED_HEALTH_SOURCE"


def test_arming_reads_the_api_env_and_the_feed_control_row():
    s = _spec("ext_pinnacle.entry_cycle")
    assert LH.armed(s, env={"EXT_PINNACLE_SHADOW": "on"}) == (True, None)
    assert LH.armed(s, env={})[0] is False
    k = _spec("agents.karen_runner")
    assert LH.armed(k, env={})[0] is True          # default on
    assert LH.armed(k, env={"KAREN_RUNNER_ENABLED": "0"})[0] is False
    f = _spec("pinnapi_feed.heartbeat")
    assert LH.armed(f, env={}, rows={"pinnapi_feed": True}) == (True, None)
    assert LH.armed(f, env={}, rows={"pinnapi_feed": False})[0] is False
    assert LH.armed(f, env={"PINNAPI_FEED": "off"},
                    rows={"pinnapi_feed": True})[0] is False
    assert LH.armed(f, env={}, rows={"pinnapi_feed": None})[0] is None
    w = LH.BY_NAME[("poller", "workers")]
    assert LH.armed(w, env={}) == (True, None)


def test_success_writes_are_throttled_per_loop_errors_never():
    LH._last_write.clear()
    LH._last_write[("execmirror.tick", "api", LH.SUCCESS)] = 100.0
    assert not LH.due("execmirror.tick", "api", LH.SUCCESS, now=110.0)
    assert LH.due("execmirror.tick", "api", LH.SUCCESS, now=131.0)
    assert LH.due("execmirror.tick", "api", LH.ERROR, now=101.0)
    assert LH.due("ext_pinnacle.entry_cycle", "api", LH.SUCCESS, now=101.0)


def test_spawn_record_never_builds_a_pool(monkeypatch):
    from sportsassets import db as DB
    monkeypatch.setattr(DB, "_pool", None)
    assert LH.spawn_record("poller", process="workers",
                           phase=LH.START) is False


# ── §4 record + read on Postgres ───────────────────────────────────────

@pg
def test_record_writes_start_success_error_with_counters():
    import asyncpg

    async def main():
        c = await asyncpg.connect(DSN)
        tx = c.transaction()
        await tx.start()
        try:
            LH._last_write.clear()
            assert await LH.record(c, "ext_pinnacle.entry_cycle",
                                   process="api", phase=LH.START)
            assert await LH.record(c, "ext_pinnacle.entry_cycle",
                                   process="api", phase=LH.SUCCESS,
                                   detail={"state": "LIVE"})
            assert await LH.record(c, "ext_pinnacle.entry_cycle",
                                   process="api", phase=LH.ERROR,
                                   error=RuntimeError("x" * 900))
            r = await c.fetchrow(
                "SELECT * FROM runtime_loop_health WHERE loop_name = $1 "
                "   AND process = 'api'", "ext_pinnacle.entry_cycle")
            assert (r["starts"], r["successes"], r["errors"]) == (1, 1, 1)
            assert r["cadence_s"] == 900.0
            assert r["last_start_at"] and r["last_success_at"]
            assert r["last_error"].startswith("RuntimeError: xxx")
            assert len(r["last_error"]) == LH.MAX_ERROR_CHARS
            assert json.loads(r["detail"]) == {"state": "LIVE"}
            # SUCCESS again: counted, error kept
            LH._last_write.clear()
            await LH.record(c, "ext_pinnacle.entry_cycle", process="api",
                            phase=LH.SUCCESS)
            r2 = await c.fetchrow(
                "SELECT successes, last_error FROM runtime_loop_health "
                " WHERE loop_name = 'ext_pinnacle.entry_cycle'")
            assert r2["successes"] == 2 and r2["last_error"]
            with pytest.raises(asyncpg.RestrictViolationError):
                async with c.transaction():
                    await c.execute("DELETE FROM runtime_loop_health")
        finally:
            await tx.rollback()
            await c.close()

    asyncio.run(main())


@pg
def test_a_missing_table_is_a_logged_false_not_a_raise(caplog):
    import asyncpg

    async def main():
        c = await asyncpg.connect(DSN)
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute("DROP TABLE runtime_loop_health")
            LH._last_write.clear()
            LH._last_warn.clear()
            got = await LH.record(c, "execmirror.tick", process="api",
                                  phase=LH.ERROR, error="boom")
            assert got is False
        finally:
            await tx.rollback()
            await c.close()

    with caplog.at_level("WARNING"):
        asyncio.run(main())
    assert "loop health execmirror.tick/api ERROR not written" in caplog.text


@pg
def test_read_combines_sources_and_lock_holders_on_production_shaped_rows():
    """Production shapes (research-sql run 37226381750): service_heartbeats
    rows ('agent_karen' ok, 'shadow_rn1' venue_unreadable), the feed's
    'pinnapi_feed_last' {state: OWNER_SYNCED, beat_at: epoch}, and a
    runtime_loop_health row; the decider's lock held by another session."""
    import asyncpg

    async def main():
        holder = await asyncpg.connect(DSN)
        c = await asyncpg.connect(DSN)
        tx = c.transaction()
        await tx.start()
        now = time.time()
        try:
            assert await holder.fetchval(
                "SELECT pg_try_advisory_lock($1)", LH.K_EXT_PINNACLE)
            await c.execute(
                "INSERT INTO service_heartbeats (service, status, detail, "
                "beat_at) VALUES ('agent_karen', 'ok', '{}', now()), "
                "('shadow_rn1', 'venue_unreadable', '{}', now()) "
                "ON CONFLICT (service) DO UPDATE SET status = "
                "EXCLUDED.status, beat_at = now()")
            await c.execute(
                "INSERT INTO ingestion_state (key, value) VALUES "
                "('pinnapi_feed_last', $1::jsonb), ('pinnapi_feed', 'true') "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                json.dumps({"state": "OWNER_SYNCED", "beat_at": now - 12}))
            await c.execute(
                "INSERT INTO runtime_loop_health (loop_name, process, "
                "cadence_s, last_start_at, last_success_at, starts, "
                "successes) VALUES ('ext_pinnacle.entry_cycle', 'api', 900, "
                "now() - interval '2 hours', now() - interval '5 minutes', "
                "1, 7) ON CONFLICT (loop_name, process) DO UPDATE SET "
                "last_success_at = EXCLUDED.last_success_at")
            body = await LH.read(c, now=now, env={
                "EXT_PINNACLE_SHADOW": "on"})
        finally:
            await tx.rollback()
            await c.close()
            await holder.execute("SELECT pg_advisory_unlock($1)",
                                 LH.K_EXT_PINNACLE)
            await holder.close()
        return body

    body = asyncio.run(main())
    by = {(lp["name"], lp["process"]): lp for lp in body["loops"]}
    ext = by[("ext_pinnacle.entry_cycle", "api")]
    assert ext["status"] == LH.HEALTHY, ext
    assert ext["lease"]["held"] is True and len(ext["lease"]["holders"]) == 1
    feed = by[("pinnapi_feed.heartbeat", "api")]
    assert feed["status"] in (LH.HEALTHY, LH.UNHEALTHY)
    assert feed["beat_status"] == "OWNER_SYNCED"
    assert feed["armed"] is True
    karen = by[("agents.karen_runner", "api")]
    assert karen["status"] == LH.HEALTHY
    rn1 = by[("shadow_rn1", "workers")]
    assert rn1["status"] == LH.HEALTHY
    assert rn1["beat_status"] == "venue_unreadable"
    # the mirror is critical and armed, and no backend holds its key here
    exm = by[("execmirror.tick", "api")]
    assert exm["status"] == LH.UNHEALTHY
    assert exm["why"] == "WRITER_LOCK_HELD_BY_NO_BACKEND"
    assert "execmirror.tick" in body["capital_critical_not_healthy"]
    assert body["workers_not_started_by_design"] == list(
        LH.WORKERS_NOT_STARTED)


# ── §5 migration 229 ───────────────────────────────────────────────────

UP = (ROOT / "migrations" / "229_runtime_loop_health.sql").read_text()
DOWN = (ROOT / "migrations" / "rollback" /
        "229_runtime_loop_health.down.sql").read_text()


@pg
def test_229_is_idempotent_and_its_rollback_drops_only_229():
    import asyncpg

    async def main():
        c = await asyncpg.connect(DSN)
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute(UP)
            await c.execute(UP)
            before = await c.fetchval("SELECT count(*) FROM pg_tables")
            await c.execute(DOWN)
            assert await c.fetchval(
                "SELECT to_regclass('runtime_loop_health')") is None
            assert await c.fetchval("SELECT count(*) FROM pg_tables") == \
                before - 1
            await c.execute(DOWN)          # a second rollback is a no-op
            await c.execute(UP)
            assert await c.fetchval(
                "SELECT to_regclass('runtime_loop_health')") is not None
            for bad in ("INSERT INTO runtime_loop_health (loop_name, "
                        "process, cadence_s) VALUES ('X Y', 'api', 1)",
                        "INSERT INTO runtime_loop_health (loop_name, "
                        "process, cadence_s) VALUES ('a', 'edge', 1)",
                        "INSERT INTO runtime_loop_health (loop_name, "
                        "process, cadence_s) VALUES ('a', 'api', 0)",
                        "INSERT INTO runtime_loop_health (loop_name, "
                        "process, cadence_s, last_error) VALUES "
                        "('a', 'api', 1, 'no time')"):
                with pytest.raises(asyncpg.CheckViolationError):
                    async with c.transaction():
                        await c.execute(bad)
        finally:
            await tx.rollback()
            await c.close()

    asyncio.run(main())


# ── §6 endpoint authority ─────────────────────────────────────────────

FORBIDDEN = ("execmirror", "kalshi", "pmus", "venue", "clob", "executor",
             "execution", "ledger", "simulator", "funded", "paper_",
             "smalllive", "order", "submit", "live_executor", "pinnapi",
             "slack_bridge", "workers", "render")


def _imports(path: pathlib.Path) -> set:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add(("." * node.level) + (node.module or ""))
            for a in node.names:
                out.add(("." * node.level) + (node.module or "") + "."
                        + a.name)
    return out


@pytest.mark.parametrize("rel", ["sportsassets/loop_health.py",
                                 "sportsassets/api/command_loop_health.py",
                                 "sportsassets/runtime_slo.py",
                                 "sportsassets/api/command_slo.py"])
def test_the_health_and_slo_modules_import_no_authority(rel):
    for name in _imports(ROOT / rel):
        low = name.lower()
        assert not any(f in low for f in FORBIDDEN), (rel, name)


def test_the_reader_issues_no_write():
    import inspect
    import re
    src = inspect.getsource(LH.read)
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|DROP|"
                         r"CREATE)\b", src)


def test_the_routes_are_get_only_and_need_a_command_session():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sportsassets.api import command_loop_health as CLH
    from sportsassets.api import command_slo as CS
    app = FastAPI()
    app.include_router(CLH.router)
    app.include_router(CS.router)
    for r in app.routes:
        if getattr(r, "path", "") in ("/api/command/loop-health",
                                      "/api/command/slo"):
            assert r.methods == {"GET"}
    client = TestClient(app)
    assert client.get("/api/command/loop-health").status_code == 401
    assert client.get("/api/command/slo").status_code == 401
    assert client.post("/api/command/loop-health").status_code == 405


@pg
def test_the_loop_health_read_runs_in_a_read_only_transaction():
    import asyncpg

    async def main():
        c = await asyncpg.connect(DSN)
        try:
            async with c.transaction(readonly=True):
                await c.execute("SET LOCAL statement_timeout = %d"
                                % LH.STATEMENT_TIMEOUT_MS)
                body = await LH.read(c)
                assert body["loops"]
                # the recorder's own write is refused inside it
                with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
                    await c.execute(LH.UPSERT_SQL, "x", "api", 1.0, "START",
                                    None, None, None, None, "{}")
        finally:
            await c.close()

    asyncio.run(main())


def test_the_r30a_runtime_tests_are_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    for f in ("tests/test_r30a_pool_starvation_root_cause.py",
              "tests/test_r30a_runtime_defects.py",
              "tests/test_r30a_loop_health.py",
              "tests/test_r30a_runtime_slo.py"):
        assert f in listed, f
