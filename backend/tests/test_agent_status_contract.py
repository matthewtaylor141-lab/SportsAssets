"""EVERY AGENT'S STATUS CONTRACT (owner Mission 4: "every agent exposes its
latest action, status, input freshness, next cycle, refusals and failures;
DEGRADED, never green") -- GET /api/command/agent-status.

  §1 INVENTORY. Every registry agent and the Chief Allocator is a subject
     (EDDIE only as ARCHER's named historical alias); every loop the R30A
     inventory names, every venue-write loop registered and not started, and
     the dedicated market-plane service's beats are subjects. Every copied
     constant is pinned to its source.
  §2 THE STATUS RULES (pure), one per rule: all fresh -> GREEN; a stale
     input -> DEGRADED; a recent failure -> DEGRADED; the newest pass failed
     / self-reported FAILED / heartbeat stale -> FAILED; no heartbeat / never
     ran -> UNKNOWN; a SOFTWARE or UNCLASSIFIED refusal -> DEGRADED and an
     ECONOMIC one never; ANY missing field -> never GREEN.
  §3 POSTGRES. Production-shaped rows (research-sql runs 37932587274 /
     37932780159) for every agent and the loops, read through the real
     reader: each agent's contract and status, and the ROUTE's own body:
     GREEN never carries a reason or a missing field, for any subject.
  §4 THE ENDPOINT. GET only, COMMAND auth, mounted in the real app, 503 on a
     dry pool, READ ONLY transaction, single-flight cache that never
     restamps a body as fresh.
  §5 AUTHORITY. The modules import no order / venue / execution / paper /
     funded module and issue no write.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import pathlib
import re
import time

import pytest

from sportsassets import agent_status_contract as A
from sportsassets import loop_health as LH

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "sportsassets"
DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")
NOW = 1_791_550_000.0
W = A.DEFAULT_WINDOW_S
REQUIRED = ("id", "kind", "name", "status", "status_reasons",
            "latest_action", "input_freshness", "next_cycle_at",
            "next_cycle_why", "refusals", "failures", "missing_fields",
            "heartbeat_sources")


def _literal(path: pathlib.Path, name: str):
    """A module-level constant AS ITS SOURCE DECLARES IT (no import)."""
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError("%s not declared in %s" % (name, path))


# ── §1 inventory ───────────────────────────────────────────────────────

def test_every_registry_agent_and_the_allocator_is_a_subject():
    from sportsassets.agents import registry as R
    assert set(A.AGENT_IDS) == set(R.AGENTS) | {"CHIEF_ALLOCATOR"}
    # EDDIE is a historical alias, never a ninth agent
    assert "EDDIE" not in A.AGENT_IDS
    assert A.HISTORICAL_ALIASES == R.HISTORICAL_ALIASES
    assert {a["id"]: a.get("aliases") for a in A.AGENT_SPECS}["ARCHER"] == (
        "EDDIE",)
    # every agent's host loop is an inventoried loop
    for a in A.AGENT_SPECS:
        assert a["host_loop"] in LH.BY_NAME, a


def test_every_loop_and_dedicated_service_is_a_subject_of_the_read():
    body = asyncio.run(A.read(_EmptyConn(), now=NOW))
    ids = {s["id"] for s in body["subjects"]}
    for spec in LH.INVENTORY:
        assert "loop:%s@%s" % (spec["name"], spec["process"]) in ids
    for name in LH.WORKERS_NOT_STARTED:
        assert "loop:%s@workers" % name in ids
    services = {s["name"] for s in A.SERVICE_SPECS}
    assert set(LH.WORKERS_DEDICATED_ONLY) <= services
    for s in services:
        assert "service:%s" % s in ids
    for a in A.AGENT_IDS:
        assert a in ids
    assert len(ids) == len(body["subjects"])          # no duplicate subject
    # NOTHING READ -> NOTHING GREEN
    assert body["summary"]["green"] == 0
    for s in body["subjects"]:
        for k in REQUIRED:
            assert k in s, (s["id"], k)
        assert s["status"] in A.STATUSES


def test_copied_constants_match_their_sources():
    ext = SRC / "workers" / "ext_pinnacle_loop.py"
    assert A.EXT_CYCLE_S == _literal(ext, "CYCLE_S")
    assert A.EXT_IDLE_POLL_S == _literal(ext, "IDLE_POLL_S")
    assert A.ADRIANA_BOOK_WINDOW_S == _literal(
        SRC / "agents" / "adriana.py", "BOOK_WINDOW_S")
    from sportsassets.agents import archer
    assert A.ARCHER_NOT_EXECUTING == (archer.WAIT, archer.SKIP)
    assert set(A.ARCHER_NOT_EXECUTING).isdisjoint(archer.EXECUTING)
    from sportsassets import shadow, shadow_experiments, shadow_lanes
    assert A.SHADOW_LANES == {
        "shadow_bettor": shadow_lanes.BETTOR_EV_SHADOW,
        "shadow_rn1": shadow_lanes.RN1_SHADOW}
    # the experimental lane is not in shadow_decisions (its CHECK admits
    # only the two lanes above): never read there as a zero
    assert shadow_experiments.EXPERIMENTAL_LANE not in A.SHADOW_LANES.values()
    assert "shadow_experimental" in A.NO_REFUSAL_CODE_LOOPS
    lanes = (ROOT / "migrations" / "069_shadow_lanes.sql").read_text()
    assert "CHECK (lane IN ('RN1_SHADOW', 'BETTOR_EV_SHADOW'))" in lanes
    assert A.NO_TRADE == shadow.NO_TRADE
    ump = SRC / "workers" / "universal_market_plane.py"
    kws = SRC / "workers" / "kalshi_ws_market_data.py"
    by = {s["name"]: s for s in A.SERVICE_SPECS}
    assert by["market_plane"]["service"] == _literal(ump, "PLANE_SERVICE")
    assert by["market_plane"]["cadence_s"] == _literal(
        ump, "PLANE_BEAT_EVERY_S")
    assert by["universal_market_plane"]["service"] == _literal(ump,
                                                               "SERVICE")
    assert by["kalshi_ws_market_data"]["service"] == _literal(kws, "SERVICE")
    assert by["kalshi_ws_market_data"]["cadence_s"] == _literal(
        kws, "HEARTBEAT_EVERY_S")
    from sportsassets.agents import registry as R
    assert set(A.SELF_DEGRADED_STATES) | {A.SELF_FAILED_STATE} <= set(
        R.STATES)
    assert "'%s'" % A.NOT_YET_RUN in (SRC / "agents" / "registry.py"
                                      ).read_text()
    # the bounds are the code base's own
    from sportsassets import agent_work_state as AWS
    assert A.STALE_FLOOR_S == AWS.STALE_FLOOR_S == 900.0
    assert A.FEED_BOUND_S == 3 * AWS.FEED_HEARTBEAT_S
    assert A.VENUE_BOUND_S == AWS.MARKET_DATA_MAX_AGE_S
    assert A.HEALTH_FACTOR == LH.HEALTH_FACTOR
    assert A.RUN_TABLES == ("improve_runs", "intel_runs", "pos_runs",
                            "twin_runs")


class _EmptyConn:
    """A connection on which every table is absent (nothing deployed)."""

    def transaction(self):
        class _T:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *e):
                return False
        return _T()

    async def fetchval(self, sql, *a):
        if "to_regclass" in sql:
            return False
        return None

    async def fetch(self, sql, *a):
        raise RuntimeError("absent")

    async def fetchrow(self, sql, *a):
        raise RuntimeError("absent")

    async def execute(self, sql, *a):
        raise RuntimeError("absent")


# ── §2 the status rules (pure) ─────────────────────────────────────────

def _verdict(name="agents.karen_runner", process="api", status=LH.HEALTHY,
             lag=60.0, **kw):
    spec = LH.BY_NAME[(name, process)]
    v = {"name": name, "process": process, "status": status,
         "why": kw.pop("why", None), "lag_s": lag,
         "unhealthy_after_s": 3 * (spec["cadence_s"] or 0) or None,
         "last_success_at": None if lag is None else NOW - lag,
         "success_source": "service_heartbeats:agent_karen",
         "last_error_at": kw.pop("error_at", None),
         "last_error": kw.pop("error", None),
         "beat_status": kw.pop("beat_status", "ok"),
         "last_start_at": NOW - 86400, "start_source": "process_boot"}
    v.update(kw)
    return v


def _karen_facts(**over):
    f = {"status": {"agent_id": "KAREN", "state": "DECISION_RECORDED",
                    "activity": "OPENED 3 CHALLENGE(S)",
                    "last_heartbeat_at": NOW - 60,
                    "last_run_started_at": NOW - 62,
                    "last_run_finished_at": NOW - 60, "runs": 1599,
                    "errors": 7, "last_error": None,
                    "cadence": {"target_interval_s": 300}},
         "status_read": True,
         "verdicts": {("agents.karen_runner", "api"): _verdict()},
         "market": {}, "positions": None, "outputs": [],
         "runs": {"failed": 0, "hung": 0},
         "last_run": {"run_id": "karen-run:1", "finished_at": NOW - 60,
                      "outcome": "CHALLENGES_OPENED"},
         "refusals": None, "eval": None, "loop_errors": {},
         "allocator": None, "ext_cycle": {}, "servicing": {},
         "census": None, "sections": {}, "extra_actions": [],
         "aliases": []}
    f.update(over)
    return f


SPEC = {a["id"]: a for a in A.AGENT_SPECS}


def _karen(**over):
    return A.agent_contract(SPEC["KAREN"], _karen_facts(**over), now=NOW,
                            window_s=W)


def test_all_fresh_is_green_and_every_field_is_real_state():
    c = _karen()
    assert c["status"] == A.GREEN, c["status_reasons"]
    assert c["status_reasons"] == [] and c["missing_fields"] == []
    assert c["latest_action"] == {"what": "CHALLENGES_OPENED",
                                  "at": NOW - 60, "age_s": 60.0,
                                  "source": "agent_runs:karen-run:1"}
    # next cycle: the recorded run start + the recorded cadence
    assert c["next_cycle_at"] == NOW - 62 + 300
    assert c["next_cycle"]["in_s"] == 238.0
    hb = c["input_freshness"][0]
    assert (hb["input"], hb["age_s"], hb["bound_s"], hb["fresh"]) == (
        "heartbeat", 60.0, 900.0, True)
    assert c["refusals"]["status"] == A.NOT_APPLICABLE
    assert c["failures"]["count"] == 0 and c["failures"]["last"] is None


def test_a_stale_input_is_degraded():
    c = _karen(verdicts={("agents.karen_runner", "api"): _verdict(
        lag=1000.0, status=LH.UNHEALTHY,
        why="NO_SUCCESS_WITHIN_3X_CADENCE")})
    assert c["status"] == A.DEGRADED
    assert "INPUT_NOT_FRESH:loop agents.karen_runner (api)" in \
        c["status_reasons"]


def test_a_recent_failure_is_degraded_and_named():
    c = _karen(runs={"failed": 2, "hung": 1, "last_failed_at": NOW - 120,
                     "last_failed_what": "FAILED {\"x\": \"KeyError\"}"})
    assert c["status"] == A.DEGRADED
    assert "FAILURES_IN_WINDOW:3" in c["status_reasons"]
    assert c["failures"]["count"] == 3
    assert c["failures"]["last"]["at"] == NOW - 120
    assert "KeyError" in c["failures"]["last"]["what"]
    # the host loop's newest error inside the window is a failure too
    c2 = _karen(verdicts={("agents.karen_runner", "api"): _verdict(
        error_at=NOW - 100, error="NON_SUCCESS_BEAT:error")})
    assert c2["status"] == A.DEGRADED
    assert c2["failures"]["count"] == 1
    assert c2["failures"]["count_is_lower_bound"] is True
    # outside the window it is history, not a recent failure
    c3 = _karen(verdicts={("agents.karen_runner", "api"): _verdict(
        error_at=NOW - W - 1, error="NON_SUCCESS_BEAT:error")})
    assert c3["status"] == A.GREEN, c3["status_reasons"]


def test_down_or_erroring_is_failed():
    st = dict(_karen_facts()["status"])
    stale = _karen(status=dict(st, last_heartbeat_at=NOW - 901))
    assert stale["status"] == A.FAILED
    assert stale["status_reasons"][0] == "HEARTBEAT_STALE:901s>900s"
    failed = _karen(status=dict(st, state="FAILED", activity="FAILED"))
    assert failed["status"] == A.FAILED
    assert failed["status_reasons"][0].startswith("SELF_REPORTED_FAILED")


def test_no_heartbeat_is_unknown_never_green():
    assert _karen(status=None)["status"] == A.UNKNOWN
    assert _karen(status=None)["status_reasons"][0] == \
        "NO_HEARTBEAT_RECORDED"
    never = _karen(status={"agent_id": "KAREN", "state": "IDLE",
                           "activity": "NOT_YET_RUN", "runs": 0,
                           "last_heartbeat_at": None,
                           "last_run_started_at": None})
    assert never["status"] == A.UNKNOWN
    assert _karen(status_read=False)["status"] == A.UNKNOWN
    assert _karen(status_read=False)["status_reasons"][0] == \
        "HEARTBEAT_UNREADABLE"


def test_the_agents_own_waiting_word_is_degraded():
    st = dict(_karen_facts()["status"], state="WAITING_FOR_EVIDENCE",
              activity="NO_NEW_COMPLIANT_EVIDENCE")
    c = _karen(status=st)
    assert c["status"] == A.DEGRADED
    assert ("SELF_REPORTED_WAITING_FOR_EVIDENCE:NO_NEW_COMPLIANT_EVIDENCE"
            in c["status_reasons"])


def _derek(counts, by_class, **over):
    f = _karen_facts(
        status={"agent_id": "DEREK", "state": "DECISION_RECORDED",
                "activity": "VALUATION_ROWS_WRITTEN:4",
                "last_heartbeat_at": NOW - 20, "last_run_started_at": NOW - 300,
                "last_run_finished_at": NOW - 20, "runs": 9, "errors": 0},
        verdicts={("ext_pinnacle.entry_cycle", "api"): _verdict(
            name="ext_pinnacle.entry_cycle", lag=20.0)},
        market={"feed": {"recorded": True, "state": "OWNER_SYNCED",
                         "beat_at": NOW - 5},
                "venue": {"recorded": True, "reads": [
                    {"obs_id": i, "at": NOW - 10 - i, "error": False}
                    for i in range(3)]}},
        last_run=None, eval={"count": 0},
        ext_cycle={"at": NOW - 20, "state": "LIVE", "ran": True,
                   "elapsed_s": 280},
        refusals=A.refusal_summary(
            counts, window_s=W, source="paper_decisions",
            software=by_class.get("REJECTED_SOFTWARE", 0),
            unclassified=by_class.get("REJECTED_UNCLASSIFIED", 0)))
    f.update(over)
    return A.agent_contract(SPEC["DEREK"], f, now=NOW, window_s=W)


def test_an_economic_refusal_never_degrades_cash_is_acceptable():
    c = _derek({"BELOW_MIN_GROSS_EDGE": 66,
                "CASH_WAIT_TOTAL_EXECUTABLE_EV_NOT_POSITIVE": 36},
               {"REJECTED_ECONOMIC": 102})
    assert c["status"] == A.GREEN, c["status_reasons"]
    assert c["refusals"]["by_reason"]["BELOW_MIN_GROSS_EDGE"] == 66
    assert c["refusals"]["by_class"] == {"ECONOMIC": 102}
    # the entry cycle's own start-to-start rule
    assert c["next_cycle_at"] == NOW - 20 + (A.EXT_CYCLE_S - 280)
    feed, venue = c["input_freshness"][2], c["input_freshness"][3]
    assert (feed["age_s"], feed["bound_s"], feed["fresh"]) == (5.0, 90.0,
                                                               True)
    assert (venue["age_s"], venue["bound_s"], venue["fresh"]) == (
        10.0, 300.0, True)


def test_a_software_or_unclassified_refusal_degrades():
    c = _derek({"BELOW_MIN_GROSS_EDGE": 66, "PROBABILITY_EVIDENCE_STALE": 3},
               {"REJECTED_ECONOMIC": 66, "REJECTED_SOFTWARE": 3})
    assert c["status"] == A.DEGRADED
    assert "SOFTWARE_REFUSALS_IN_WINDOW:3" in c["status_reasons"]
    u = _derek({"A_CODE_NO_TAXONOMY_KNOWS": 2},
               {"REJECTED_UNCLASSIFIED": 2})
    assert u["status"] == A.DEGRADED
    assert "UNCLASSIFIED_REFUSALS_IN_WINDOW:2" in u["status_reasons"]
    assert u["refusals"]["unclassified"] == ["A_CODE_NO_TAXONOMY_KNOWS"]


def test_a_stale_feed_or_failing_venue_reads_degrade_the_market_agents():
    m = {"feed": {"recorded": True, "state": "OWNER_SYNCED",
                  "beat_at": NOW - 91},
         "venue": {"recorded": True, "reads": [
             {"obs_id": 1, "at": NOW - 10, "error": False}]}}
    c = _derek({}, {}, market=m)
    assert c["status"] == A.DEGRADED
    assert "INPUT_NOT_FRESH:PinnAPI feed telemetry" in c["status_reasons"]
    m2 = {"feed": {"recorded": True, "state": "OWNER_SYNCED",
                   "beat_at": NOW - 5},
          "venue": {"recorded": True, "reads": [
              {"obs_id": i, "at": NOW - 10 - i, "error": i > 0}
              for i in range(4)]}}
    c2 = _derek({}, {}, market=m2)
    assert c2["status"] == A.DEGRADED
    venue = [i for i in c2["input_freshness"]
             if i["input"] == "venue book reads"][0]
    assert venue["fresh"] is False
    assert venue["why"] == "VENUE_BOOK_READS_FAILING"


FIELD_KNOCKOUTS = {
    "latest_action": dict(last_run=None, status=dict(
        _karen_facts()["status"], last_run_finished_at=None)),
    "next_cycle_at": dict(status=dict(_karen_facts()["status"],
                                      cadence=None)),
    "refusals": None,      # set below (an agent whose refusals are read)
    "failures": dict(runs=None),
    "input_freshness.age_s": dict(verdicts={
        ("agents.karen_runner", "api"): _verdict(lag=None)}),
}


@pytest.mark.parametrize("field", sorted(FIELD_KNOCKOUTS))
def test_never_green_with_any_missing_field(field):
    """THE INVARIANT, FIELD BY FIELD: from an otherwise GREEN contract, take
    away one field's source; the subject is never GREEN and names it."""
    if field == "refusals":
        base = _derek({}, {})
        assert base["status"] == A.GREEN, base["status_reasons"]
        c = _derek({}, {}, refusals=A.refusals_unavailable(
            "UndefinedTableError", W, "paper_decisions"))
    else:
        assert _karen()["status"] == A.GREEN
        c = _karen(**FIELD_KNOCKOUTS[field])
    assert c["status"] != A.GREEN
    assert c["missing_fields"], c
    assert any(m.startswith(field.split(".")[0]) for m in c["missing_fields"])
    assert any(r.startswith("FIELD_MISSING:") for r in c["status_reasons"])


def test_finalize_refuses_to_make_a_green_with_a_missing_field():
    c = A.skeleton("X", A.AGENT, "X", window_s=W)
    out = A.finalize(c, base=A.OK, hard=[], soft=[])
    assert out["status"] == A.DEGRADED            # every field missing
    assert set(out["missing_fields"]) >= {"latest_action", "input_freshness",
                                          "next_cycle_at", "refusals",
                                          "failures"}
    for base in (A.UNKNOWN, A.FAILED, A.DISABLED):
        assert A.finalize(A.skeleton("X", A.AGENT, "X", window_s=W),
                          base=base, hard=["WHY"], soft=[])["status"] == base


def _loop(name, process, v, **f):
    spec = dict(LH.BY_NAME[(name, process)])
    spec["run_table"] = next((s[1] for s in spec["sources"]
                              if s[0] == "run_table"), None)
    facts = {"lifetime_errors": 0, "run_failures": {"failed": 0},
             "reactive": None, "refusals": None, "parent": None}
    facts.update(f)
    return A.loop_contract(spec, v, facts, now=NOW, window_s=W)


def _lv(name, process, status=LH.HEALTHY, lag=10.0, **kw):
    spec = LH.BY_NAME[(name, process)]
    v = _verdict(name="agents.karen_runner", status=status, lag=lag, **kw)
    v.update(name=name, process=process,
             unhealthy_after_s=None if not spec["cadence_s"]
             else 3 * spec["cadence_s"])
    return v


def test_the_loop_rules():
    g = _loop("shadow_bettor", "workers", _lv("shadow_bettor", "workers"),
              refusals=A.refusal_summary({}, window_s=W, source="x"))
    assert g["status"] == A.GREEN, g["status_reasons"]
    assert g["next_cycle_at"] == NOW - 10 + 60.0
    assert g["latest_action"]["what"] == "PASS_SUCCEEDED (ok)"
    # a failure in the window, after which it succeeded: DEGRADED
    d = _loop("shadow_bettor", "workers", _lv(
        "shadow_bettor", "workers", error_at=NOW - 100,
        error="NON_SUCCESS_BEAT:store_not_ready"))
    assert d["status"] == A.DEGRADED
    # the newest pass failed: FAILED
    f = _loop("shadow_bettor", "workers", _lv(
        "shadow_bettor", "workers", error_at=NOW - 5,
        error="NON_SUCCESS_BEAT:tick_failed"))
    assert f["status"] == A.FAILED
    assert f["status_reasons"][0].startswith("LATEST_PASS_FAILED")
    u = _loop("mirror_shadow", "workers", _lv(
        "mirror_shadow", "workers", status=LH.UNHEALTHY, lag=164.0,
        why="NO_SUCCESS_WITHIN_3X_CADENCE"))
    assert u["status"] == A.FAILED
    assert u["status_reasons"][0] == \
        "LOOP_UNHEALTHY:NO_SUCCESS_WITHIN_3X_CADENCE"
    na = _loop("api.malloc_trim", "api", _lv(
        "api.malloc_trim", "api", status=LH.UNAVAILABLE, lag=None,
        why="NO_PERSISTED_HEALTH_SOURCE"))
    assert na["status"] == A.UNKNOWN
    st = _loop("intel.runner", "api", _lv("intel.runner", "api",
                                          status=LH.STARTING, lag=None))
    assert st["status"] == A.UNKNOWN
    dis = _loop("rn1x_model", "api", _lv(
        "rn1x_model", "api", status=LH.DISABLED, lag=None,
        why="ENV_RN1X_MODEL_FIT_NOT_ON"))
    assert dis["status"] == A.DISABLED
    assert dis["status_reasons"] == ["DISABLED:ENV_RN1X_MODEL_FIT_NOT_ON"]
    # the writer's own 'degraded' / 'high' / 'drift' is never GREEN
    for beat in ("degraded", "high", "drift"):
        sd = _loop("kalshi_market_data", "workers", _lv(
            "kalshi_market_data", "workers", beat_status=beat))
        assert sd["status"] == A.DEGRADED
        assert "SELF_REPORTED_DEGRADED:%s" % beat in sd["status_reasons"]
    # a run-table loop's FAILED runs in the window
    rt = _loop("twin.runner", "api", _lv("twin.runner", "api"),
               run_failures={"failed": 2, "last_failed_at": NOW - 50,
                             "last_failed_what": "FAILED"})
    assert rt["status"] == A.DEGRADED
    assert rt["failures"]["count"] == 2
    # a child whose fencing parent is down is degraded
    ch = _loop("ext_pinnacle.servicing", "api", _lv(
        "ext_pinnacle.servicing", "api"), parent=_lv(
        "ext_pinnacle.entry_cycle", "api", status=LH.UNHEALTHY, lag=5000.0,
        why="NO_SUCCESS_WITHIN_3X_CADENCE"))
    assert ch["status"] == A.DEGRADED
    assert "INPUT_NOT_FRESH:loop ext_pinnacle.entry_cycle (api)" in \
        ch["status_reasons"]


def test_an_event_driven_loop_with_no_event_is_unknown_never_green():
    v = _lv("pinnapi_reactive", "api", status=LH.EVENT_DRIVEN, lag=None)
    parent = _lv("ext_pinnacle.entry_cycle", "api")
    none = _loop("pinnapi_reactive", "api", v, parent=parent, reactive={
        "completed_in_window": 0, "failed_in_window": 0,
        "orphaned_started": 0})
    assert none["status"] == A.UNKNOWN
    assert none["next_cycle_why"] == "EVENT_DRIVEN"
    v2 = _lv("pinnapi_reactive", "api", status=LH.EVENT_DRIVEN, lag=30.0)
    bad = _loop("pinnapi_reactive", "api", v2, parent=parent, reactive={
        "completed_in_window": 5, "failed_in_window": 2,
        "last_failed_at": NOW - 40, "orphaned_started": 3})
    assert bad["status"] == A.DEGRADED
    assert "ORPHANED_STARTED_ATTEMPTS:3" in bad["status_reasons"]
    assert bad["failures"]["count"] == 2


def test_the_dedicated_service_rules():
    s = {x["name"]: x for x in A.SERVICE_SPECS}
    ok = A.service_contract(s["market_plane"], {"status": "ok",
                                                "beat_at": NOW - 10}, True,
                            now=NOW, window_s=W)
    assert ok["status"] == A.GREEN, ok["status_reasons"]
    assert ok["kind"] == A.SERVICE and ok["id"] == "service:market_plane"
    deg = A.service_contract(s["kalshi_ws_market_data"], {
        "status": "degraded", "beat_at": NOW - 5}, True, now=NOW, window_s=W)
    assert deg["status"] == A.DEGRADED
    blk = A.service_contract(s["universal_market_plane"], {
        "status": "blocked", "beat_at": NOW - 5}, True, now=NOW, window_s=W)
    assert blk["status"] == A.FAILED
    stale = A.service_contract(s["kalshi_ws_market_data"], {
        "status": "ok", "beat_at": NOW - 46}, True, now=NOW, window_s=W)
    assert stale["status"] == A.FAILED
    none = A.service_contract(s["market_plane"], None, True, now=NOW,
                              window_s=W)
    assert none["status"] == A.UNKNOWN
    unread = A.service_contract(s["market_plane"], None, False, now=NOW,
                                window_s=W)
    assert unread["status"] == A.UNKNOWN


def test_xaviers_held_positions_are_an_input_judged_per_review():
    """Fresh only when every open position's ONE current review is CURRENT
    (agent_work_state.position_class); nothing held -> no such input;
    positions unreadable -> the input is missing, never fresh."""
    from sportsassets import xavier_freshness as XF
    market = {"feed": {"recorded": True, "state": "OWNER_SYNCED",
                       "beat_at": NOW - 5},
              "venue": {"recorded": True, "reads": [
                  {"obs_id": 1, "at": NOW - 10, "error": False}]}}

    def pos(state):
        return {"position_kind": "PAPER", "group_id": "g", "slug": "s",
                "current_review": {"management_state": state,
                                   "recommendation_state": state,
                                   "reviewed_at": NOW - 40},
                "book": {"observed_at": NOW - 10, "error": False}}

    cur = A.positions_input({"open": 1, "positions": [pos(XF.S_CURRENT)]},
                            market, NOW)
    assert cur["fresh"] is True and cur["age_s"] == 40.0
    assert cur["bound_rule"] == "PER_REVIEW_FRESHNESS_WINDOW"
    stale = A.positions_input({"open": 1, "positions": [
        pos("WAITING_FOR_FRESH_EVIDENCE")]}, market, NOW)
    assert stale["fresh"] is False
    assert stale["why"].startswith("0_OF_1_CURRENT")
    # one held position shown of two open: never fresh
    cut = A.positions_input({"open": 2, "positions": [pos(XF.S_CURRENT)]},
                            market, NOW)
    assert cut["fresh"] is False
    assert A.positions_input({"open": 0, "positions": []}, market,
                             NOW) is None
    gone = A.positions_input({"open": None, "positions": [],
                              "why": "UndefinedTableError"}, market, NOW)
    assert gone["fresh"] is False and gone["age_s"] is None
    # on Xavier's contract: a held position not CURRENT degrades him
    f = _karen_facts(
        status=dict(_karen_facts()["status"], agent_id="XAVIER",
                    cadence={"source": "SERVICING_TASK",
                             "review_interval_s": 60.0}),
        verdicts={("ext_pinnacle.servicing", "api"): _verdict(
            name="ext_pinnacle.servicing", lag=20.0)},
        market=market, positions={"open": 1, "positions": [
            pos("WAITING_FOR_FRESH_EVIDENCE")]},
        refusals=A.refusal_summary({}, window_s=W, source="x"))
    x = A.agent_contract(SPEC["XAVIER"], f, now=NOW, window_s=W)
    assert x["status"] == A.DEGRADED
    assert "INPUT_NOT_FRESH:held positions' current reviews" in \
        x["status_reasons"]
    f["positions"] = {"open": 1, "positions": [pos(XF.S_CURRENT)]}
    assert A.agent_contract(SPEC["XAVIER"], f, now=NOW,
                            window_s=W)["status"] == A.GREEN


def test_the_allocator_lives_by_its_intel_runs():
    """No agent_status row: the Chief Allocator's heartbeat is his newest
    ALLOCATOR intel run; its newest run FAILED is FAILED, none is UNKNOWN,
    one older than 3 x the intel runner's cadence is FAILED."""
    base = dict(_karen_facts(), status=None, last_run=None,
                outputs=[{"table": "intel_runs", "id": "ir-1",
                          "at": NOW - 190, "label": "allocation run ir-1"}],
                verdicts={("intel.runner", "api"): _verdict(
                    name="intel.runner", lag=190.0)})

    def alloc(**al):
        f = dict(base, allocator=dict({"read": True,
                                       "newest_started_at": NOW - 200,
                                       "newest_status": "OK",
                                       "failed": 0}, **al))
        return A.agent_contract(SPEC["CHIEF_ALLOCATOR"], f, now=NOW,
                                window_s=W)
    ok = alloc()
    assert ok["status"] == A.GREEN, ok["status_reasons"]
    assert ok["next_cycle_at"] == NOW - 200 + 600
    assert alloc(newest_status="FAILED", failed=1,
                 last_failed_at=NOW - 190)["status"] == A.FAILED
    assert alloc(newest_started_at=None)["status"] == A.UNKNOWN
    assert alloc(newest_started_at=NOW - 1801)["status"] == A.FAILED
    assert alloc(read=False)["status"] == A.UNKNOWN


def test_the_window_is_bounded():
    assert A.bound_window(10) == A.MIN_WINDOW_S
    assert A.bound_window(10 ** 9) == A.MAX_WINDOW_S
    assert A.bound_window("x") == A.DEFAULT_WINDOW_S
    assert A.bound_window(float("nan")) == A.DEFAULT_WINDOW_S


# ── §3 Postgres ────────────────────────────────────────────────────────

CLEAN = ("agent_status", "agent_runs", "service_heartbeats",
         "runtime_loop_health", "ingestion_state", "paper_decisions",
         "paper_evaluation_attempts", "paper_xavier_reviews",
         "xavier_management_assessments", "paper_book_observations",
         "eddie_execution_estimates", "adriana_arb_scans",
         "adriana_arb_refusals", "intel_runs", "pos_runs", "twin_runs",
         "improve_runs", "shadow_decisions", "karen_challenges",
         "paper_audrey_findings", "audrey_audit_reports", "scout_features",
         "pinnapi_reactive_attempts", "paper_fills", "paper_settlements",
         "smalllive_handoffs")
ENV = {"EXT_PINNACLE_SHADOW": "on"}


async def _seed(c, now: float, *, derek_software: bool = False,
                derek_timeout: bool = False):
    """Production-shaped rows: the shapes and vocabularies of research-sql
    runs 37932587274 / 37932780159, inside the caller's transaction."""
    await c.execute("SET LOCAL session_replication_role = replica")
    for t in CLEAN:
        await c.execute("DELETE FROM %s" % t)

    def ts(age):
        return now - age

    async def status(aid, state, activity, hb_age, cadence=None, *,
                     started_age=None, finished_age=None, runs=1, errors=0,
                     last_error=None):
        await c.execute(
            "INSERT INTO agent_status (agent_id, state, activity, "
            " last_heartbeat_at, last_run_started_at, last_run_finished_at,"
            " runs, errors, last_error, cadence) VALUES ($1, $2, $3, "
            " to_timestamp($4), to_timestamp($5), to_timestamp($6), $7, $8,"
            " $9, $10::jsonb)", aid, state, activity, ts(hb_age),
            ts(started_age if started_age is not None else hb_age + 2),
            ts(finished_age if finished_age is not None else hb_age), runs,
            errors, last_error,
            None if cadence is None else json.dumps(cadence))

    async def run(aid, rid, started_age, finished_age, outcome, summary):
        await c.execute(
            "INSERT INTO agent_runs (run_id, agent_id, started_at, "
            " finished_at, outcome, summary) VALUES ($1, $2, "
            " to_timestamp($3), CASE WHEN $4::float8 IS NULL THEN NULL "
            " ELSE to_timestamp($4::float8) END, $5, $6::jsonb)", rid, aid,
            ts(started_age), None if finished_age is None
            else ts(finished_age), outcome, json.dumps(summary))

    async def beat(service, st, age):
        await c.execute(
            "INSERT INTO service_heartbeats (service, status, detail, "
            " beat_at) VALUES ($1, $2, '{}', to_timestamp($3))", service, st,
            ts(age))

    async def key(k, v):
        await c.execute("INSERT INTO ingestion_state (key, value) VALUES "
                        "($1, $2::jsonb)", k, json.dumps(v))

    # DEREK: decisions (economic, or one software code), fresh feed / books
    await status("DEREK", "DECISION_RECORDED", "VALUATION_ROWS_WRITTEN:4",
                 20, None, started_age=300)
    await key("pinnapi_feed_last", {"state": "OWNER_SYNCED",
                                    "beat_at": ts(5)})
    await key("ext_pinnacle_last_cycle", {"at": ts(20), "state": "LIVE",
                                          "ran": True, "elapsed_s": 280})
    await key("ext_pinnacle_last_servicing", {
        "at": ts(30), "state": "SERVICED", "servicing_cadence": {
            "slow_half_at": ts(100), "learning_interval_s": 900.0}})
    await key("workers_boot", {"at": ts(36000)})
    await c.execute(
        "INSERT INTO runtime_loop_health (loop_name, process, cadence_s, "
        " last_start_at, last_success_at, starts, successes) VALUES "
        " ('ext_pinnacle.entry_cycle', 'api', 900, to_timestamp($1), "
        "  to_timestamp($2), 1, 30), ('ext_pinnacle.servicing', 'api', 60, "
        "  NULL, to_timestamp($3), 0, 900)", ts(30000), ts(20), ts(30))
    for i in range(3):
        await c.execute(
            "INSERT INTO paper_book_observations (us_market_slug, "
            " observed_at, source, read_basis) VALUES ($1, "
            " to_timestamp($2), 'PMUS', 'BBO')", "slug-%d" % i, ts(10 + i))
    codes = ["BELOW_MIN_GROSS_EDGE"] * 5 + (
        ["PROBABILITY_EVIDENCE_STALE"] if derek_software else [])
    for i, code in enumerate(codes):
        await c.execute(
            "INSERT INTO paper_decisions (decision_id, session_id, "
            " account_id, decided_at, verdict, refusal, refusals, "
            " internal_model, pinnacle, qualification_gaps, policy_version,"
            " simulator_version, strategy) VALUES ($1, 's', "
            " 'paper_acct_main', to_timestamp($2), 'REFUSE', $3, $4, "
            " '{}', '{}', '[]', 'v', 'v', 'DEREK_ENTRY_POLICY_V2')",
            "paper-d-%d" % i, ts(60 + i), code, [code])
    await c.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, verdict, internal_model, pinnacle, qualification_gaps,"
        " policy_version, simulator_version, strategy) VALUES ('paper-d-enter', "
        " 's', 'paper_acct_main', to_timestamp($1), 'ENTER', '{}', '{}', "
        " '[]', 'v', 'v', 'PINNACLE_EXPLORATION_PAPER')", ts(40))

    if derek_timeout:
        # an evaluation that never reached a decision (agent_funnel's
        # NOT_DECIDED): a failure in the window, named by its outcome
        await c.execute(
            "INSERT INTO paper_evaluation_attempts (at, session_id, "
            " account_id, valuation_id, strategy, via, attempt_no, outcome) "
            " VALUES (to_timestamp($1), 's', 'paper_acct_main', 7, "
            " 'DEREK_ENTRY_POLICY_V2', 'PAPER_PASS', 1, 'TIMEOUT')",
            ts(45))
    # XAVIER: his own FAILED word on a fresh heartbeat
    await status("XAVIER", "FAILED", "HOOK_RAISED:xavier", 30,
                 {"source": "SERVICING_TASK", "review_interval_s": 60.0},
                 last_error="RuntimeError: boom")
    # AUDREY: no heartbeat row at all
    # KAREN: everything fresh
    await status("KAREN", "DECISION_RECORDED", "OPENED 3 CHALLENGE(S)", 60,
                 {"target_interval_s": 300}, started_age=62, runs=1599,
                 errors=7)
    await run("KAREN", "karen-run:a", 62, 60, "CHALLENGES_OPENED",
              {"detector_errors": {}, "opened": ["c1"]})
    await beat("agent_karen", "ok", 60)
    # ARCHER: every pass records a phase error (production: errors == runs)
    await status("ARCHER", "IDLE", "NO_NEW_CANDIDATE", 30,
                 {"target_interval_s": 300}, runs=957, errors=957,
                 last_error="results:KeyError")
    for i in range(3):
        await run("ARCHER", "archer-run:%d" % i, 330 + i * 300,
                  328 + i * 300, "NO_NEW_CANDIDATE",
                  {"phase_errors": {"results": "KeyError"}})
    await beat("agent_archer", "ok", 30)
    # the historical alias's row, three and a half days old
    await status("EDDIE", "DECISION_RECORDED",
                 "ESTIMATED 11 CANDIDATE(S) (SHADOW)", 311357,
                 {"target_interval_s": 300})
    await beat("agent_eddie", "ok", 311357)
    # SCOUT: heartbeat older than 3 x 600
    await status("SCOUT", "WAITING_FOR_EVIDENCE", "NO_NEW_COMPLIANT_EVIDENCE",
                 5000, {"target_interval_s": 600})
    await beat("agent_scout", "ok", 5000)
    # ADRIANA: waiting, and her newest census found no supported book
    await status("ADRIANA", "WAITING_FOR_EVIDENCE",
                 "NO_RECORDED_BOOK_IN_WINDOW", 100,
                 {"target_interval_s": 300})
    await run("ADRIANA", "adriana-run:a", 102, 100,
              "NO_RECORDED_BOOK_IN_WINDOW", {"phase_errors": {}})
    await beat("agent_adriana", "ok", 100)
    await c.execute(
        "INSERT INTO adriana_arb_scans (scan_id, started_at, finished_at, "
        " status, why, engine_version, venues, markets_read, books_fresh, "
        " structures_considered, opportunities, refusals_total, "
        " refusals_recorded, by_verdict, by_kind, by_code, limits, "
        " authority) VALUES ('scan-a', to_timestamp($1), to_timestamp($2),"
        " 'NO_EVIDENCE', "
        " 'NO_RECORDED_BOOK_OF_A_SUPPORTED_FAMILY_IN_THE_LAST_900S', 'v', "
        " '[]', 0, 0, 0, 0, 0, 0, '{}', '{}', '{}', '{}', "
        " '{\"submit\": false, \"cancel\": false, \"credentials\": false,"
        " \"capital\": false}')", ts(101), ts(100))
    # CHIEF_ALLOCATOR: a fresh OK allocation run
    for comp in ("ALLOCATOR", "CYCLE"):
        await c.execute(
            "INSERT INTO intel_runs (run_id, component, started_at, "
            " finished_at, status, version) VALUES ('ir-1', $1, "
            " to_timestamp($2), to_timestamp($3), 'OK', 'v')", comp,
            ts(200), ts(190))
    # LOOPS: the shadow bettor (fresh; NO_TRADE with its named blockers),
    # mirror_shadow (stale), and the dedicated plane's beats
    await beat("shadow_bettor", "ok", 20)
    for i in range(2):
        await c.execute(
            "INSERT INTO shadow_decisions (shadow_decision_id, symbol, "
            " outcome_leg, model_version, policy_version, evidence_source, "
            " decision_ts, proposed_action, lane, blockers, "
            " bettor_opportunity_id, rn1_features_used, feature_lineage, "
            " shadow_mode, capital_at_risk) VALUES ($1, "
            " 'sym', 'YES', 'm', 'p', 'PMUS_BBO', to_timestamp($2), "
            " 'NO_TRADE', 'BETTOR_EV_SHADOW', $3::jsonb, 'opp-1', false, "
            " '{}', true, 0)", "sd-%d" % i,
            ts(30 + i), json.dumps([
                {"code": "INDEPENDENT_EV_NOT_ESTABLISHED", "why": "x"},
                {"code": "NO_FAIR_VALUE", "why": "y"}]))
    await beat("mirror_shadow", "ok", 500)
    await beat("market_plane", "ok", 10)
    await beat("kalshi_ws_market_data", "degraded", 5)
    await beat("universal_market_plane", "blocked", 5)
    await c.execute("SET LOCAL session_replication_role = origin")


def _pg(fn, **kw):
    import asyncpg

    async def main():
        holder = await asyncpg.connect(DSN)
        c = await asyncpg.connect(DSN)
        tx = c.transaction()
        await tx.start()
        try:
            # the decider's writer lock, held as in production
            assert await holder.fetchval("SELECT pg_try_advisory_lock($1)",
                                         LH.K_EXT_PINNACLE)
            now = time.time()
            await _seed(c, now, **kw)
            return await fn(c, now)
        finally:
            await tx.rollback()
            await c.close()
            await holder.execute("SELECT pg_advisory_unlock($1)",
                                 LH.K_EXT_PINNACLE)
            await holder.close()
    return asyncio.run(main())


@pg
def test_every_agents_contract_on_production_shaped_rows():
    async def go(c, now):
        return await A.read(c, now=now, env=ENV), now

    body, now = _pg(go)
    by = {s["id"]: s for s in body["subjects"]}
    assert set(A.AGENT_IDS) <= set(by)
    assert "EDDIE" not in by

    karen = by["KAREN"]
    assert karen["status"] == A.GREEN, karen["status_reasons"]
    assert karen["latest_action"]["source"] == "agent_runs:karen-run:a"
    assert karen["next_cycle_at"] == pytest.approx(now - 62 + 300, abs=0.01)

    derek = by["DEREK"]
    assert derek["status"] == A.GREEN, derek["status_reasons"]
    assert derek["refusals"]["by_reason"] == {"BELOW_MIN_GROSS_EDGE": 5}
    assert derek["refusals"]["enter"] == 1
    assert derek["refusals"]["decisions_by_class"] == {
        "REJECTED_ECONOMIC": 5}
    # his newest recorded action is the cycle end his heartbeat recorded
    # (20 s ago), newer than his newest decision row (40 s ago)
    assert derek["latest_action"]["source"] == \
        "agent_status.last_run_finished_at"
    assert derek["latest_action"]["what"] == \
        "DECISION_RECORDED: VALUATION_ROWS_WRITTEN:4"
    assert derek["next_cycle_at"] == pytest.approx(
        now - 20 + A.EXT_CYCLE_S - 280, abs=0.01)

    archer = by["ARCHER"]
    assert archer["status"] == A.DEGRADED
    assert "FAILURES_IN_WINDOW:3" in archer["status_reasons"]
    assert "results" in archer["failures"]["last"]["what"]
    assert archer["historical_aliases"][0]["alias"] == "EDDIE"
    assert archer["historical_aliases"][0][
        "agent_status_heartbeat_age_s"] == pytest.approx(311357, abs=1)

    assert by["XAVIER"]["status"] == A.FAILED
    assert by["XAVIER"]["status_reasons"][0] == \
        "SELF_REPORTED_FAILED:HOOK_RAISED:xavier"
    assert by["XAVIER"]["failures"]["count"] >= 1
    assert by["SCOUT"]["status"] == A.FAILED
    assert by["SCOUT"]["status_reasons"][0].startswith("HEARTBEAT_STALE")
    assert by["AUDREY"]["status"] == A.UNKNOWN
    assert by["AUDREY"]["status_reasons"][0] == "NO_HEARTBEAT_RECORDED"
    # her slow half is still read and judged
    sh = [i for i in by["AUDREY"]["input_freshness"]
          if i["input"].startswith("servicing slow half")][0]
    assert sh["fresh"] is True and sh["bound_s"] == 2700.0
    assert by["AUDREY"]["next_cycle_at"] == pytest.approx(now - 100 + 900,
                                                          abs=0.01)

    adriana = by["ADRIANA"]
    assert adriana["status"] == A.DEGRADED
    assert any(r.startswith("SELF_REPORTED_WAITING_FOR_EVIDENCE")
               for r in adriana["status_reasons"])
    census = [i for i in adriana["input_freshness"]
              if i["input"].startswith("census evidence")][0]
    assert census["fresh"] is False
    assert census["why"].startswith("NO_EVIDENCE:NO_RECORDED_BOOK")

    alloc = by["CHIEF_ALLOCATOR"]
    assert alloc["status"] == A.GREEN, alloc["status_reasons"]
    assert alloc["next_cycle_at"] == pytest.approx(now - 200 + 600, abs=0.01)

    sb = by["loop:shadow_bettor@workers"]
    assert sb["refusals"]["by_reason"] == {
        "INDEPENDENT_EV_NOT_ESTABLISHED": 2}
    assert sb["status"] == A.DEGRADED
    assert "UNCLASSIFIED_REFUSALS_IN_WINDOW:2" in sb["status_reasons"]
    assert by["loop:mirror_shadow@workers"]["status"] == A.FAILED
    assert by["service:market_plane"]["status"] == A.GREEN
    assert by["service:kalshi_ws_market_data"]["status"] == A.DEGRADED
    assert by["service:universal_market_plane"]["status"] == A.FAILED
    assert by["loop:copy_sweep@workers"]["status"] == A.DISABLED
    # every section read
    bad = {k: v for k, v in body["sections"].items()
           if v["status"] not in ("OK", "EMPTY")}
    assert not bad, bad


@pg
def test_a_software_refusal_degrades_derek_on_postgres():
    async def go(c, now):
        return await A.read(c, now=now, env=ENV)

    body = _pg(go, derek_software=True)
    derek = {s["id"]: s for s in body["subjects"]}["DEREK"]
    assert derek["status"] == A.DEGRADED
    assert "SOFTWARE_REFUSALS_IN_WINDOW:1" in derek["status_reasons"]
    assert derek["refusals"]["decisions_by_class"] == {
        "REJECTED_ECONOMIC": 5, "REJECTED_SOFTWARE": 1}


@pg
def test_an_evaluation_timeout_is_a_failure_of_derek_on_postgres():
    async def go(c, now):
        return await A.read(c, now=now, env=ENV), now

    body, now = _pg(go, derek_timeout=True)
    derek = {s["id"]: s for s in body["subjects"]}["DEREK"]
    assert derek["status"] == A.DEGRADED
    assert "FAILURES_IN_WINDOW:1" in derek["status_reasons"]
    f = derek["failures"]
    assert f["by_source"]["paper_evaluation_attempts (TIMEOUT / ERROR)"] == 1
    assert f["last"]["what"] == "TIMEOUT"
    assert f["last"]["at"] == pytest.approx(now - 45, abs=0.01)


@pg
def test_the_route_never_returns_green_with_a_missing_field(monkeypatch):
    """THE ENDPOINT'S OWN BODY, every subject: GREEN carries no reason and
    no missing field; every subject carries every contract key; and a
    subject whose input is taken away is no longer GREEN."""
    from fastapi import Response

    from sportsassets.api import command_agent_status as CAS

    for k, v in ENV.items():
        monkeypatch.setenv(k, v)

    async def go(c, now):
        class _Pool:
            def acquire(self, timeout=None):
                class _Ctx:
                    async def __aenter__(self):
                        return c

                    async def __aexit__(self, *e):
                        return False
                return _Ctx()

        async def _pool():
            return _Pool()
        monkeypatch.setattr(CAS, "_pool", _pool)
        CAS._CACHE.clear()
        first = await CAS.agent_status(Response(), window_s=3600.0)
        await c.execute("SET LOCAL session_replication_role = replica")
        await c.execute("DELETE FROM service_heartbeats "
                        " WHERE service = 'agent_karen'")
        await c.execute("SET LOCAL session_replication_role = origin")
        CAS._CACHE.clear()
        second = await CAS.agent_status(Response(), window_s=3600.0)
        return first, second

    first, second = _pg(go)
    greens = 0
    for body in (first, second):
        assert body["read_only"] is True and body["cached"] is False
        for s in body["subjects"]:
            for k in REQUIRED:
                assert k in s, (s["id"], k)
            if s["status"] == A.GREEN:
                greens += 1
                assert s["missing_fields"] == [], s
                assert s["status_reasons"] == [], s
                assert all(i["fresh"] for i in s["input_freshness"]), s
                assert s["failures"]["count"] == 0
                assert s["refusals"]["status"] in (A.MEASURED,
                                                   A.NOT_APPLICABLE)
    assert greens >= 4          # the property was exercised, not vacuous
    k1 = {s["id"]: s for s in first["subjects"]}["KAREN"]
    k2 = {s["id"]: s for s in second["subjects"]}["KAREN"]
    assert k1["status"] == A.GREEN
    assert k2["status"] != A.GREEN
    assert "INPUT_NOT_FRESH:loop agents.karen_runner (api)" in \
        k2["status_reasons"]


@pg
def test_the_read_runs_in_a_read_only_transaction():
    import asyncpg

    async def main():
        c = await asyncpg.connect(DSN)
        try:
            async with c.transaction(readonly=True):
                await c.execute("SET LOCAL statement_timeout = %d"
                                % A.STATEMENT_TIMEOUT_MS)
                body = await A.read(c)
                assert body["subjects"]
                bad = {k: v for k, v in body["sections"].items()
                       if v["status"] == "UNAVAILABLE"}
                assert not bad, bad
                with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
                    await c.execute("INSERT INTO service_heartbeats "
                                    "(service, status) VALUES ('x', 'ok')")
        finally:
            await c.close()

    asyncio.run(main())


@pg
def test_the_windowed_reads_use_their_indexes():
    """The windowed reads are bounded by an index on the window column (the
    planner on production row counts, research-sql 37932780159 C1)."""
    import asyncpg

    async def main():
        c = await asyncpg.connect(DSN)
        try:
            out = {}
            async with c.transaction():
                await c.execute("SET LOCAL enable_seqscan = off")
                for name, sql, args in (
                        ("agent_runs", A.AGENT_RUNS_SQL, (NOW, NOW)),
                        ("paper_decisions", A.DEREK_DECISIONS_SQL, (NOW,)),
                        ("paper_xavier_reviews", A.XAVIER_REVIEWS_SQL,
                         (NOW,)),
                        ("shadow_decisions", A.SHADOW_SQL,
                         (NOW, ["BETTOR_EV_SHADOW"], "NO_TRADE")),
                        ("adriana_arb_refusals", A.ADRIANA_REFUSALS_SQL,
                         (NOW,))):
                    plan = "\n".join(r[0] for r in await c.fetch(
                        "EXPLAIN " + sql, *args))
                    out[name] = plan
            return out
        finally:
            await c.close()

    for name, plan in asyncio.run(main()).items():
        assert "Index" in plan, (name, plan)


# ── §4 the endpoint ────────────────────────────────────────────────────

def test_the_route_is_get_only_needs_a_command_session_and_is_mounted():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sportsassets.api import command_agent_status as CAS
    app = FastAPI()
    app.include_router(CAS.router)
    client = TestClient(app)
    assert client.get("/api/command/agent-status").status_code == 401
    assert client.post("/api/command/agent-status").status_code == 405
    # MOUNTED IN THE REAL APP (through the loop-health router, which
    # api/app.py includes): 401 without a session, never 404
    from sportsassets.api.app import app as real
    rc = TestClient(real)
    assert rc.get("/api/command/agent-status").status_code == 401
    assert rc.get("/api/command/loop-health").status_code == 401


def test_a_dry_pool_answers_503_at_once(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sportsassets.api import agents_core as AC
    from sportsassets.api import command_agent_status as CAS
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

    monkeypatch.setattr(CAS, "_pool", _pool)
    CAS._CACHE.clear()
    app = FastAPI()
    app.include_router(CAS.router)
    app.dependency_overrides[AC.require_read] = lambda: None
    r = TestClient(app).get("/api/command/agent-status")
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["reason"] == \
        "POOL_UNAVAILABLE_OR_READ_OVER_BUDGET"
    assert waited == [CAS.POOL_ACQUIRE_TIMEOUT_S]
    # an out-of-range window is refused by the route, never clamped silently
    r2 = TestClient(app).get("/api/command/agent-status?window_s=5")
    assert r2.status_code == 422


def test_the_cache_shares_one_read_and_never_restamps_it(monkeypatch):
    from sportsassets.api import command_agent_status as CAS
    calls = []

    async def fake(window_s):
        calls.append(window_s)
        await asyncio.sleep(0.01)
        return {"now": 123.0, "subjects": [], "window_s": window_s}

    monkeypatch.setattr(CAS, "_read_only", fake)
    CAS._CACHE.clear()

    async def main():
        return await asyncio.gather(*[CAS.build(3600.0) for _ in range(5)])

    got = asyncio.run(main())
    assert calls == [3600.0]                       # ONE read for five calls
    assert [g["cached"] for g in got].count(False) == 1
    for g in got:
        assert g["now"] == 123.0                   # the measured instant
        assert g["cache_age_s"] >= 0
    CAS._CACHE.clear()


# ── §5 authority ───────────────────────────────────────────────────────

FORBIDDEN = ("execmirror", "kalshi", "pmus", "clob", "executor", "execution",
             "funded", "smalllive", "submit", "live_", "order", "ledger",
             "paper_runtime", "paper_derek", "paper_xavier", "venue")


def _imports(path: pathlib.Path) -> set:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                out.add(("." * node.level) + (node.module or "") + "."
                        + a.name)
    return out


@pytest.mark.parametrize("rel", ["sportsassets/agent_status_contract.py",
                                 "sportsassets/api/command_agent_status.py"])
def test_the_modules_import_no_authority(rel):
    for name in _imports(ROOT / rel):
        low = name.lower()
        assert not any(f in low for f in FORBIDDEN), (rel, name)


def test_the_contract_issues_no_write():
    WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+"
                       r"FROM|TRUNCATE|ALTER\s+|DROP\s+|CREATE\s+|COPY\s+|"
                       r"GRANT\s+|SET\s+ROLE|SET\s+SESSION)", re.I)
    for rel in ("sportsassets/agent_status_contract.py",
                "sportsassets/api/command_agent_status.py"):
        tree = ast.parse((ROOT / rel).read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                assert not WRITE.search(n.value), (rel, n.value[:80])


def test_the_contract_tests_are_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_agent_status_contract.py" in listed
