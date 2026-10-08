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
    # a dedicated-service loop is neither registered nor inventoried here
    assert set(LH.WORKERS_DEDICATED_ONLY) == set(W.DEDICATED_ONLY_LOOPS)
    assert not set(LH.WORKERS_DEDICATED_ONLY) & (inventoried | set(names))


def _lifespan_task_callees(lifespan: str) -> list:
    """Every callable the lifespan hands to create_task (or a module's own
    .start(pool)), by its dotted name, parsed -- not grepped from a list."""
    tree = ast.parse("async def _l():\n" + "\n".join(
        "    " + ln for ln in lifespan.split("\n")[1:]))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = ast.unparse(fn)
        if name.endswith("create_task") and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Call):
                out.append(ast.unparse(arg.func))
        elif name in ("_WATCHDOG.start", "_IAS.start"):
            out.append(name)
    return out


def test_every_api_lifespan_runner_is_inventoried():
    """The recurring tasks api/app.py's lifespan creates, by the module that
    runs them. R30A review: the test now PARSES every create_task in the
    lifespan and fails on one the inventory (or the one-shot list) does not
    name -- a hardcoded list could not catch an omission (slack_bridge.run
    and the API institutional stream were missing)."""
    src = (ROOT / "sportsassets" / "api" / "app.py").read_text()
    lifespan = src[src.index("async def lifespan"):src.index("app = FastAPI(")]
    expected = {
        "_SLACK.run": "slack_bridge.run",
        "_IAS.start": "institutional_api_stream",
        "_delayed_poller": "api.poller_fallback",
        "_DESKLOOP.run": "bettor_desk_loop", "_RN1X.run": "rn1x_shadow",
        "_RN1XL.run": "rn1x_learn", "_EXT.run": "ext_pinnacle.entry_cycle",
        "_RN1XM.run": "rn1x_model", "_CAP.run": "agents.capability_runtime",
        "_KAREN.run": "agents.karen_runner",
        "_PEER.run": "agents.peer_responder",
        "_ARCHER.run": "agents.archer_runner",
        "_SCOUT.run": "agents.scout_runner", "_EXM.run": "execmirror.tick",
        "_ADRIANA.run": "agents.adriana_runner",
        "_REDTEAM.run": "redteam.runner",
        "_INTEL.run": "intel.runner", "_POS.run": "profitability.runner",
        "_POSLEARN.run": "position_learning.runner",
        "_TWIN.run": "twin.runner",
        "_IMPROVE.run": "agents.improvement_pipeline",
        "_CRL.run": "capital_readiness.observer",
        "_WATCHDOG.start": "api.loop_watchdog",
        "_desk_feed_warm_loop": "api.desk_feed_warm",
        "refresh_whale_idents_loop": "api.whale_idents_refresh",
        "_trim_loop": "api.malloc_trim"}
    names = {s["name"] for s in LH.API_LOOPS}
    for call, name in expected.items():
        assert call in lifespan, "%s no longer in the lifespan" % call
        assert name in names, name
    callees = _lifespan_task_callees(lifespan)
    assert len(callees) >= 20, callees
    unknown = [c for c in callees if c not in expected
               and c not in LH.API_ONE_SHOT]
    assert not unknown, ("lifespan tasks with no inventory entry: %s"
                         % unknown)
    # the decider's children are inventoried too
    for child in ("ext_pinnacle.servicing", "pinnapi_feed.heartbeat",
                  "pinnapi_held.refresh", "pinnapi_reactive"):
        assert child in names


class _Declared:
    """A module's constants AS ITS SOURCE DECLARES THEM (a literal at module
    level), falling back to the live attribute for anything computed.

    THE FULL-SUITE FAILURE THIS IS FOR (R30A review, full run on a7109d2):
    test_desk_concurrency.py:123 and test_desk_recovery.py:254/792 assign
    `bettor_desk_loop.CYCLE_S = 0.01` directly and never restore it, so in
    the full suite this test read 0.01 against the copied 20.0 and failed,
    while it passes alone. What loop_health copies is the declared constant,
    so that is what is compared -- not whatever another test left behind."""

    def __init__(self, mod):
        self._mod = mod
        self._lit = {}
        tree = ast.parse(pathlib.Path(mod.__file__).read_text())
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            for t in targets:
                if isinstance(t, ast.Name):
                    try:
                        self._lit[t.id] = self._eval(value)
                    except Exception:                  # noqa: BLE001
                        pass                            # computed: live value

    @staticmethod
    def _eval(value):
        """A literal, or the env knob pattern `float(os.getenv(NAME,
        DEFAULT))` evaluated against this process's environment (what the
        module itself evaluated at import)."""
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id in ("float", "int") and len(value.args) == 1
                and isinstance(value.args[0], ast.Call)
                and ast.unparse(value.args[0].func) == "os.getenv"):
            name, default = (ast.literal_eval(a) for a in value.args[0].args)
            return {"float": float, "int": int}[value.func.id](
                os.getenv(name, default))
        return ast.literal_eval(value)

    def __getattr__(self, name):
        if name in self._lit:
            return self._lit[name]
        return getattr(self._mod, name)


def test_declared_reads_the_source_not_a_reassigned_attribute(monkeypatch):
    from sportsassets import bettor_desk_loop as DESK
    declared = _Declared(DESK).CYCLE_S
    monkeypatch.setattr(DESK, "CYCLE_S", 0.01)
    assert _Declared(DESK).CYCLE_S == declared != 0.01


def test_copied_constants_match_the_loops():
    from sportsassets import bettor_desk_loop as DESK
    from sportsassets import execmirror as EXM
    from sportsassets import pinnapi_feed_runtime as FR
    from sportsassets import pinnapi_held as PH
    from sportsassets import pinnapi_owner as PO
    from sportsassets.agents import capability_runtime as CAP
    from sportsassets.agents import archer_runner, improvement_pipeline
    from sportsassets.agents import karen_runner, peer_responder, scout_runner
    from sportsassets.intel import runner as INTEL
    from sportsassets.poslearn import runner as POSL
    from sportsassets.profitability import runner as POS
    from sportsassets.twin import runner as TWIN
    from sportsassets.workers import ext_pinnacle_loop as EXT
    from sportsassets.workers import rn1x_learn_loop as LRN
    from sportsassets.workers import rn1x_model_loop as MOD
    from sportsassets.workers import rn1x_shadow as SHD

    (DESK, EXM, FR, PH, PO, CAP, archer_runner, improvement_pipeline,
     karen_runner, peer_responder, scout_runner, INTEL, POSL, POS, TWIN, EXT,
     LRN, MOD, SHD) = map(_Declared, (
        DESK, EXM, FR, PH, PO, CAP, archer_runner, improvement_pipeline,
        karen_runner, peer_responder, scout_runner, INTEL, POSL, POS, TWIN,
        EXT, LRN, MOD, SHD))
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
                      (archer_runner, "agents.archer_runner"),
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
    # UNAVAILABLE now only when nothing says when the loop last started
    # (R30A review: "NO_SUCCESS_RECORDED" used to swallow the loop that
    # starts and fails on every pass -- see the never-succeeded tests)
    none = LH.classify(s, {"success_at": [], "sources_read": ["x"]},
                       now=NOW, holders=[{"pid": 1}])
    assert none["status"] == LH.UNAVAILABLE
    assert none["why"] == "NO_SUCCESS_RECORDED_AND_NO_KNOWN_START"
    ev = LH.classify(_spec("pinnapi_reactive"),
                     {"success_at": [(NOW - 9, "a")], "sources_read": ["a"]},
                     now=NOW)
    assert ev["status"] == LH.EVENT_DRIVEN


def test_a_loop_that_starts_and_fails_on_every_pass_is_unhealthy():
    """THE REVIEWER'S PROBE, PINNED: execmirror.tick (cadence 30 s) started
    an hour ago, failed 2 s ago, never succeeded -> UNHEALTHY, not
    UNAVAILABLE."""
    spec = LH.BY_NAME[("execmirror.tick", "api")]
    facts = {"success_at": [(None, "runtime_loop_health")],
             "start_at": NOW - 3600, "error_at": NOW - 2,
             "error": "TimeoutError: boom",
             "sources_read": ["runtime_loop_health"], "sources_missing": []}
    got = LH.classify(spec, facts, now=NOW, holders=[{"pid": 1}])
    assert got["status"] == LH.UNHEALTHY
    assert got["why"] == "NO_SUCCESS_ON_RECORD_LATEST_PASS_FAILED"
    assert got["last_error"] == "TimeoutError: boom"


def test_never_succeeded_since_start_is_measured_against_the_last_start():
    spec = LH.BY_NAME[("execmirror.tick", "api")]          # 3 x 30 = 90 s
    base = {"success_at": [], "sources_read": ["runtime_loop_health"]}
    # its own START record 91 s ago, nothing since: UNHEALTHY
    old = LH.classify(spec, dict(base, start_at=NOW - 91), now=NOW,
                      holders=[{"pid": 1}])
    assert old["status"] == LH.UNHEALTHY
    assert old["why"] == "NO_SUCCESS_SINCE_START"
    assert old["start_source"] == "runtime_loop_health"
    # started 30 s ago: not yet judgeable as unhealthy, and not healthy
    young = LH.classify(spec, dict(base, start_at=NOW - 30), now=NOW,
                        holders=[{"pid": 1}])
    assert young["status"] == LH.STARTING
    # no START record: the process's boot is the first sighting
    boot = LH.classify(spec, dict(base, process_started_at=NOW - 600),
                       now=NOW, holders=[{"pid": 1}])
    assert boot["status"] == LH.UNHEALTHY
    assert boot["start_source"] == "process_boot"
    # the LATER of the two anchors wins (a restart re-opens the grace)
    later = LH.classify(spec, dict(base, start_at=NOW - 20,
                                   process_started_at=NOW - 600),
                        now=NOW, holders=[{"pid": 1}])
    assert later["status"] == LH.STARTING


def test_a_failing_heartbeat_is_not_a_success():
    """service_heartbeats rows whose status the writer uses for a FAILED
    pass ('error', 'venue_unreadable', 'store_not_ready', 'running' = only
    started) never count as a success; the writer's success vocabulary
    does."""
    for name, proc, bad, good in (
            ("agents.karen_runner", "api", "error", "ok"),
            ("rn1x_shadow", "api", "error", "idle"),
            ("analytics", "workers", "running", "ok"),
            ("shadow_rn1", "workers", "venue_unreadable", "ok"),
            ("shadow_bettor", "workers", "store_not_ready", "no_universe"),
            ("reconciler", "workers", "error", "drift"),
            ("metadata", "workers", "degraded", "ok"),
            ("memory", "workers", "high", "high")):
        spec = LH.BY_NAME[(name, proc)]
        hb = [src for src in spec["sources"]
              if src[0] == "service_heartbeats"][0]
        assert good in hb[2], (name, good)
        if bad != good:
            assert bad not in hb[2], (name, bad)


def test_ingestion_state_rules_honour_the_writers_own_fields():
    assert LH.beat_ok({"state": "OWNER_SYNCED"},
                      LH._in("state", "OWNER_SYNCED")) == (True,
                                                            "OWNER_SYNCED")
    assert LH.beat_ok({"state": "CONNECTING"},
                      LH._in("state", "OWNER_SYNCED"))[0] is False
    assert LH.beat_ok({"at": "x", "err": None}, LH._null("err"))[0] is True
    ok, why = LH.beat_ok({"at": "x", "err": "QueryCanceledError"},
                         LH._null("err"))
    assert ok is False and why == "err=QueryCanceledError"
    assert LH.beat_ok({"state": "BLOCKED"}, LH.ANY)[0] is True
    feed = LH.BY_NAME[("pinnapi_feed.heartbeat", "api")]["sources"][0]
    assert feed[3] == LH._in("state", "OWNER_SYNCED")
    premap = [s for s in LH.BY_NAME[("premap", "workers")]["sources"]
              if s[0] == "ingestion_state"][0]
    assert premap[3] == LH._null("err")


def test_a_loop_that_succeeds_every_pass_never_reads_unhealthy():
    """THE THROTTLE MAY NOT OUTRUN THE THRESHOLD (R30A review: pinnapi_held
    recorded at most every 30 s against 3 x 10 s and read UNHEALTHY ~5% of
    the time while succeeding on every pass). For every inventoried loop,
    the worst gap between two recorded successes -- the throttle plus one
    loop period plus a 20% slow pass -- stays inside 3 x cadence; and a
    simulation of 2 hours of passes through `due` never reads UNHEALTHY."""
    for spec in LH.INVENTORY:
        cad = spec["cadence_s"]
        if not cad:
            continue
        period = spec["period_s"] or cad
        throttle = spec["record_every_s"] or 0.0
        worst = throttle + period * 1.2
        assert worst < LH.HEALTH_FACTOR * cad, (spec["name"], worst, cad)
    for name, proc in (("pinnapi_held.refresh", "api"),
                       ("execmirror.tick", "api"),
                       ("price_path", "workers"),
                       ("edge_marks", "workers")):
        spec = LH.BY_NAME[(name, proc)]
        LH._last_write.clear()
        t, last_written, worst_lag = 0.0, None, 0.0
        while t < 7200.0:
            t += spec["period_s"] * 1.05          # a slightly slow pass
            if LH.due(name, proc, LH.SUCCESS, now=t):
                LH._last_write[(name, proc, LH.SUCCESS)] = t
                if last_written is not None:
                    worst_lag = max(worst_lag, t - last_written)
                last_written = t
            got = LH.classify(spec, {"success_at": [(last_written, "x")],
                                     "sources_read": ["x"]},
                              now=t + spec["period_s"], holders=[{"pid": 1}])
            assert got["status"] == LH.HEALTHY, (name, t, worst_lag)
    LH._last_write.clear()


def test_record_detail_is_bounded_on_content_and_always_valid_json():
    big = LH.bounded_detail({"blob": "y" * 5000, "state": "LIVE"})
    assert len(big) <= LH.MAX_DETAIL_CHARS
    got = json.loads(big)
    assert got["truncated"] is True and got["keys"] == ["blob", "state"]
    assert got["original_chars"] > 5000
    many = json.loads(LH.bounded_detail({"k%05d" % i: i for i in range(4000)}))
    assert many["truncated"] is True
    assert len(json.dumps(many)) <= LH.MAX_DETAIL_CHARS
    assert json.loads(LH.bounded_detail({"a": 1})) == {"a": 1}
    assert json.loads(LH.bounded_detail(None)) == {}


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
        return body, now

    body, now = asyncio.run(main())
    by = {(lp["name"], lp["process"]): lp for lp in body["loops"]}
    ext = by[("ext_pinnacle.entry_cycle", "api")]
    assert ext["status"] == LH.HEALTHY, ext
    assert ext["lease"]["held"] is True and len(ext["lease"]["holders"]) == 1
    feed = by[("pinnapi_feed.heartbeat", "api")]
    # an exact verdict (R30A review): OWNER_SYNCED 12 s ago vs 3 x 30 s;
    # its lock holder is checked separately (no backend holds K_FEED here)
    assert feed["beat_status"] == "OWNER_SYNCED"
    assert feed["armed"] is True
    assert feed["last_success_at"] == pytest.approx(now - 12, abs=0.01)
    assert feed["status"] == LH.UNHEALTHY
    assert feed["why"] == "WRITER_LOCK_HELD_BY_NO_BACKEND"
    karen = by[("agents.karen_runner", "api")]
    assert karen["status"] == LH.HEALTHY
    # 'venue_unreadable' is a pass that FAILED: never a fresh success
    rn1 = by[("shadow_rn1", "workers")]
    assert rn1["status"] == LH.UNHEALTHY, rn1
    assert rn1["why"] == "NO_SUCCESS_ON_RECORD_LATEST_PASS_FAILED"
    assert rn1["beat_status"] == "venue_unreadable"
    assert rn1["last_error"] == "NON_SUCCESS_BEAT:venue_unreadable"
    # the mirror is critical and armed, and no backend holds its key here
    exm = by[("execmirror.tick", "api")]
    assert exm["status"] == LH.UNHEALTHY
    assert exm["why"] == "WRITER_LOCK_HELD_BY_NO_BACKEND"
    assert "execmirror.tick" in body["capital_critical_not_healthy"]
    assert body["workers_not_started_by_design"] == list(
        LH.WORKERS_NOT_STARTED)


@pg
def test_fresh_error_heartbeats_read_unhealthy_on_postgres():
    """THE REVIEWER'S PROBE ON POSTGRES: fresh 'error' rows for three
    writers that heartbeat on failure read UNHEALTHY with the failure named,
    and a fresh 'ok' row for the same writers reads HEALTHY."""
    import asyncpg

    async def main(status):
        c = await asyncpg.connect(DSN)
        tx = c.transaction()
        await tx.start()
        try:
            for svc in ("agent_karen", "analytics", "rn1x_shadow"):
                await c.execute(
                    "INSERT INTO service_heartbeats (service, status, "
                    "detail, beat_at) VALUES ($1, $2, '{}', now()) "
                    "ON CONFLICT (service) DO UPDATE SET status = $2, "
                    "beat_at = now()", svc, status)
            return await LH.read(c, env={"RN1X_SHADOW": "on"})
        finally:
            await tx.rollback()
            await c.close()

    bad = {(lp["name"], lp["process"]): lp
           for lp in asyncio.run(main("error"))["loops"]}
    for k in (("agents.karen_runner", "api"), ("analytics", "workers"),
              ("rn1x_shadow", "api")):
        lp = bad[k]
        assert lp["status"] != LH.HEALTHY, (k, lp)
        assert lp["beat_status"] == "error"
        assert lp["last_error"] == "NON_SUCCESS_BEAT:error"
    assert bad[("agents.karen_runner", "api")]["status"] == LH.UNHEALTHY
    good = {(lp["name"], lp["process"]): lp
            for lp in asyncio.run(main("ok"))["loops"]}
    assert good[("agents.karen_runner", "api")]["status"] == LH.HEALTHY
    assert good[("analytics", "workers")]["status"] == LH.HEALTHY


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


def test_the_diagnostics_answer_503_when_the_pool_is_dry(monkeypatch):
    """R30A REVIEW: both endpoints acquired with no timeout, so under the
    very starvation they diagnose they hung. A dry pool is now a bounded
    wait and a 503 POOL_UNAVAILABLE."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sportsassets.api import agents_core as AC
    from sportsassets.api import command_loop_health as CLH
    from sportsassets.api import command_slo as CS

    waited = []

    class _DryPool:
        def acquire(self, timeout=None):
            waited.append(timeout)

            class _Ctx:
                async def __aenter__(self):
                    raise asyncio.TimeoutError()

                async def __aexit__(self, *e):
                    return False
            return _Ctx()

    async def _pool():
        return _DryPool()

    monkeypatch.setattr(CLH, "_pool", _pool)
    monkeypatch.setattr(CS, "_pool", _pool)
    app = FastAPI()
    app.include_router(CLH.router)
    app.include_router(CS.router)
    app.dependency_overrides[AC.require_read] = lambda: None
    client = TestClient(app)
    for path in ("/api/command/loop-health", "/api/command/slo"):
        r = client.get(path)
        assert r.status_code == 503, (path, r.text)
        assert r.json()["detail"]["reason"] == "POOL_UNAVAILABLE"
    assert waited == [CLH.POOL_ACQUIRE_TIMEOUT_S, CS.POOL_ACQUIRE_TIMEOUT_S]
    assert CLH.POOL_ACQUIRE_TIMEOUT_S <= 2.0


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
              "tests/test_r30a_loop_stalls.py",
              "tests/test_r30a_loop_health.py",
              "tests/test_r30a_runtime_slo.py"):
        assert f in listed, f
