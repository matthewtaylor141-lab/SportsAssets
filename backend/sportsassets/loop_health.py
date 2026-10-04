"""RUNTIME LOOP HEALTH (R30A): every recurring loop, its single-writer lease,
and when it last started, succeeded and failed.

THE OWNER'S RULE (audit 2026-10-04, "Architecture / reliability"): "Move
capital-critical recurring work from generic API lifespan loops to dedicated
workers or prove a robust single-writer lease for every loop." This module
is the INVENTORY that makes the claim checkable, the RECORDER the loops call,
and the READER behind GET /api/command/loop-health.

INVENTORY. Every loop api/app.py's lifespan starts and every loop
workers/all.py supervises, with: its process, cadence, whether it is
CAPITAL-CRITICAL (it decides, manages or reconciles positions, or feeds a
decision its probability), its LEASE -- the mechanism that makes it a single
writer across the processes that exist during a deploy -- and where its
success is recorded. Cadences and keys are copied, not imported (this module
imports no loop, venue, order or execution module); tests pin every copy to
the loop's own constant.

LEASE KINDS
  ADVISORY_LOCK   a session advisory lock held for the loop's life on a
                  session of its own (db.lease_session); FENCED where noted:
                  the loop re-proves before each pass that its backend still
                  holds the key (db.advisory_held) and contends again if not.
  CHILD_OF        started only by its parent after the parent's lock, stopped
                  with it, and FENCED per pass by the parent's writer backend
                  pid (db.advisory_held_by).
  ADVISORY_PER_CYCLE  a session advisory lock taken for each cycle.
  OWN_LEASE       its own lock on its own connection (the PinnAPI feed owner,
                  pinnapi_owner.Lease), re-checked every liveness pass.
  TASK_LEASE_ROWS work claimed row by row with an expiring lease.
  NONE            no lease; the entry says why that is safe (idempotent keys,
                  a per-process cache, a process locked out of venue writes).

THE RECORD. runtime_loop_health (migration 229): one current row per (loop,
process). `record` writes START / SUCCESS / ERROR with the server's clock.
It NEVER raises into a loop and never waits more than RECORD_TIMEOUT_S; a
SUCCESS more frequent than the loop's `record_every_s` is skipped in process
(a 2 s mirror tick does not write every 2 s). A failed write is logged once
per WARN_EVERY_S per loop, never silently dropped.

THE VERDICT (`classify`, pure): HEALTHY when the newest success from any of
the loop's sources is within HEALTH_FACTOR (3) x its cadence; UNHEALTHY when
older, or when a capital-critical, armed loop's writer lock is held by no
backend; DISABLED (named) when the loop is not armed in this deployment;
EVENT_DRIVEN when it has no cadence to judge against; UNAVAILABLE (named)
when no source holds any record -- never a manufactured success.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

VERSION = "LOOP_HEALTH_V1"
HEALTH_FACTOR = 3.0
RECORD_TIMEOUT_S = 3.0
WARN_EVERY_S = 300.0
STATEMENT_TIMEOUT_MS = 4000
MAX_ERROR_CHARS = 500

HEALTHY, UNHEALTHY, DISABLED = "HEALTHY", "UNHEALTHY", "DISABLED"
UNAVAILABLE, EVENT_DRIVEN = "UNAVAILABLE", "EVENT_DRIVEN"

#: the six single-writer keys read granted in production (research-sql run
#: 37226381750) plus the feed owner's
K_EXECMIRROR = 0x45584D31
K_DESK = 7723901544120031
K_RN1X_SHADOW = 7723901544120032
K_RN1X_LEARN = 7723901544120033
K_EXT_PINNACLE = 7723901544120034
K_RN1X_MODEL = 7723901544120035
K_FEED = 7723901544120036
K_INTEL, K_POS, K_TWIN = 0x494E5431, 0x504F5331, 0x54574E31
K_POSITION_LEARNING = 0x504F534C

_ON = ("on", "1", "true", "yes")
_OFF = ("off", "0", "false", "no")


def _spec(name, process, cadence_s, *, critical, lease, sources, armed=None,
          record_every_s=None, note=None):
    return {"name": name, "process": process, "cadence_s": cadence_s,
            "capital_critical": bool(critical), "lease": lease,
            "sources": tuple(sources), "armed": armed or ("always",),
            "record_every_s": record_every_s, "note": note}


def _lh(name, process):
    return ("runtime_loop_health", name, process)


# ── THE API LIFESPAN (api/app.py) ──────────────────────────────────────
API_LOOPS = (
    _spec("ext_pinnacle.entry_cycle", "api", 900.0, critical=True,
          lease={"kind": "ADVISORY_LOCK", "key": K_EXT_PINNACLE,
                 "session": "own (db.lease_session)",
                 "fencing": "db.advisory_held before every pass; a lost lock "
                            "stops the children and contends again"},
          armed=("env_on", "EXT_PINNACLE_SHADOW"),
          sources=(_lh("ext_pinnacle.entry_cycle", "api"),
                   ("ingestion_state", "ext_pinnacle_last_cycle", "at")),
          note="the entry lane: Derek's decisions, PAPER and the SHADOW "
               "actual lane; IDLE_POLL_S 60 s when a cycle did not run"),
    _spec("ext_pinnacle.servicing", "api", 60.0, critical=True,
          lease={"kind": "CHILD_OF", "parent": "ext_pinnacle.entry_cycle",
                 "key": K_EXT_PINNACLE,
                 "fencing": "db.advisory_held_by(writer pid) before every "
                            "pass; a pass the parent's lock does not cover "
                            "is skipped"},
          armed=("env_on", "EXT_PINNACLE_SHADOW"),
          sources=(_lh("ext_pinnacle.servicing", "api"),
                   ("ingestion_state", "ext_pinnacle_last_servicing", "at")),
          note="management and recovery of held positions on their own "
               "cadence"),
    _spec("pinnapi_feed.heartbeat", "api", 30.0, critical=True,
          lease={"kind": "OWN_LEASE", "key": K_FEED,
                 "session": "own (pinnapi_owner.Lease)",
                 "fencing": "the owner re-checks its lease AND the decider's "
                            "writer lock every liveness pass"},
          armed=("env_not_off_and_row", "PINNAPI_FEED", "pinnapi_feed"),
          sources=(("ingestion_state", "pinnapi_feed_last", "beat_at"),),
          note="the one provider socket; Xavier's held-position measure"),
    _spec("pinnapi_held.refresh", "api", 10.0, critical=True,
          lease={"kind": "CHILD_OF", "parent": "pinnapi_feed.heartbeat",
                 "key": K_FEED,
                 "fencing": "lives and dies with the feed owner"},
          armed=("env_not_off_and_row", "PINNAPI_FEED", "pinnapi_feed"),
          record_every_s=30.0,
          sources=(_lh("pinnapi_held.refresh", "api"),),
          note="which provider events are held (Xavier's priority targets)"),
    _spec("pinnapi_reactive", "api", None, critical=True,
          lease={"kind": "CHILD_OF", "parent": "ext_pinnacle.entry_cycle",
                 "key": K_EXT_PINNACLE,
                 "fencing": "db.advisory_held_by(writer pid) on the job's "
                            "connection before each job"},
          armed=("env_on_both", "PINNAPI_REACTIVE_PAPER", "PAPER_SESSION"),
          sources=(("reactive_attempts",),),
          note="event-driven: a provider change -> one bounded evaluation"),
    _spec("execmirror.tick", "api", 30.0, critical=True,
          lease={"kind": "ADVISORY_LOCK", "key": K_EXECMIRROR,
                 "session": "own (db.lease_session)",
                 "fencing": "db.advisory_held before every tick; a lost lock "
                            "or dead session contends again"},
          record_every_s=30.0,
          sources=(_lh("execmirror.tick", "api"),),
          note="ticks every 2 s (TICK_S); health is recorded at most every "
               "30 s, so 30 s is its health cadence. Reviews actual "
               "positions even while the control row keeps the lane off"),
    _spec("bettor_desk_loop", "api", 20.0, critical=False,
          lease={"kind": "ADVISORY_LOCK", "key": K_DESK,
                 "session": "own (db.lease_session)"},
          armed=("env_on", "BETTOR_DESK_LOOP"), sources=(),
          note="SHADOW desk; no venue path; persists no success heartbeat"),
    _spec("rn1x_shadow", "api", 20.0, critical=False,
          lease={"kind": "ADVISORY_LOCK", "key": K_RN1X_SHADOW,
                 "session": "own (db.lease_session)"},
          armed=("env_not_off", "RN1X_SHADOW", "off"),
          sources=(("service_heartbeats", "rn1x_shadow"),)),
    _spec("rn1x_learn", "api", 3600.0, critical=False,
          lease={"kind": "ADVISORY_LOCK", "key": K_RN1X_LEARN,
                 "session": "own (db.lease_session)"},
          armed=("env_not_off", "RN1X_LEARN", "off"),
          sources=(("service_heartbeats", "rn1x_learn"),)),
    _spec("rn1x_model", "api", 3600.0, critical=False,
          lease={"kind": "ADVISORY_LOCK", "key": K_RN1X_MODEL,
                 "session": "own (db.lease_session)"},
          armed=("env_on", "RN1X_MODEL_FIT"),
          sources=(("ingestion_state", "rn1x_model_last_cycle", "at"),)),
    _spec("intel.runner", "api", 600.0, critical=False,
          lease={"kind": "ADVISORY_PER_CYCLE", "key": K_INTEL},
          armed=("env_not_off", "INTEL_SHADOW", "on"),
          sources=(("run_table", "intel_runs"),)),
    _spec("profitability.runner", "api", 3600.0, critical=False,
          lease={"kind": "ADVISORY_PER_CYCLE", "key": K_POS},
          armed=("env_not_off", "POS_ECON", "on"),
          sources=(("run_table", "pos_runs"),),
          note="also hosts the lost-opportunity LEDGER / SCORES components"),
    # THE POSITION-LEARNING LAYER IS ISOLATED BY DESIGN: nothing outside
    # its package may read its tables or import it (only api/app.py starts
    # it, and its authority test pins that). Its run records are therefore
    # NOT read here; the loop is listed so the inventory is complete and is
    # reported UNAVAILABLE (NO_PERSISTED_HEALTH_SOURCE) rather than given a
    # manufactured status. It is SHADOW research with no capital path.
    _spec("position_learning.runner", "api", 900.0, critical=False,
          lease={"kind": "ADVISORY_PER_CYCLE", "key": K_POSITION_LEARNING},
          armed=("env_not_off", "POS_LEARN", "on"),
          sources=(),
          note="layer isolated by design; its run records are read only "
               "inside the layer"),
    _spec("twin.runner", "api", 21600.0, critical=False,
          lease={"kind": "ADVISORY_PER_CYCLE", "key": K_TWIN},
          armed=("env_not_off", "POS_TWIN", "on"),
          sources=(("run_table", "twin_runs"),)),
    _spec("agents.karen_runner", "api", 300.0, critical=False,
          lease={"kind": "NONE", "why": "writes only her own challenge "
                 "records, keyed per detector and target"},
          armed=("env_not_off", "KAREN_RUNNER_ENABLED", "1"),
          sources=(("service_heartbeats", "agent_karen"),)),
    _spec("agents.peer_responder", "api", 120.0, critical=False,
          lease={"kind": "NONE", "why": "answers recorded challenges; one "
                 "response per challenge"},
          armed=("env_not_off", "PEER_RESPONDER_ENABLED", "1"),
          sources=(("service_heartbeats", "agent_peer_responder"),)),
    _spec("agents.eddie_runner", "api", 300.0, critical=False,
          lease={"kind": "NONE", "why": "SHADOW estimates keyed per "
                 "decision"},
          armed=("env_not_off", "EDDIE_RUNNER_ENABLED", "1"),
          sources=(("service_heartbeats", "agent_eddie"),)),
    _spec("agents.scout_runner", "api", 600.0, critical=False,
          lease={"kind": "NONE", "why": "RESEARCH observations keyed per "
                 "feature and window"},
          armed=("env_not_off", "SCOUT_RUNNER_ENABLED", "1"),
          sources=(("service_heartbeats", "agent_scout"),)),
    _spec("agents.improvement_pipeline", "api", 600.0, critical=False,
          lease={"kind": "NONE", "why": "items seeded from source keys "
                 "(UNIQUE); stages mirrored, never decided"},
          armed=("env_not_off", "IMPROVEMENT_PIPELINE_ENABLED", "1"),
          sources=(("service_heartbeats", "improvement_pipeline"),
                   ("run_table", "improve_runs"))),
    _spec("agents.capability_runtime", "api", 15.0, critical=False,
          lease={"kind": "TASK_LEASE_ROWS",
                 "why": "agent_tasks claimed with an expiring lease"},
          sources=(("ingestion_state",
                    "agent.capabilities.heartbeat:paper_acct_main", "at"),),
          note="sleeps 15 s between ticks; RESEARCH_ONLY"),
    _spec("api.loop_watchdog", "api", None, critical=False,
          lease={"kind": "NONE", "why": "per-process stall telemetry"},
          sources=(), note="records the API event loop's own stalls"),
    _spec("api.desk_feed_warm", "api", None, critical=False,
          lease={"kind": "NONE", "why": "a per-process cache warm; writes "
                 "nothing"}, sources=()),
    _spec("api.whale_idents_refresh", "api", None, critical=False,
          lease={"kind": "NONE", "why": "a per-process snapshot; writes "
                 "nothing"}, sources=()),
    _spec("api.malloc_trim", "api", None, critical=False,
          lease={"kind": "NONE", "why": "per-process memory hygiene"},
          sources=()),
)

# ── THE WORKERS SERVICE (workers/all.py) ───────────────────────────────
_WORKERS_LEASE = {"kind": "NONE",
                  "why": "the workers process is LOCKED in the execution "
                         "gate before any loop starts (cand21): no venue "
                         "write; ingestion and measurement writes dedupe on "
                         "their keys"}


def _w(name, cadence_s, *sources, note=None, armed=None):
    return _spec(name, "workers", cadence_s, critical=False,
                 lease=dict(_WORKERS_LEASE),
                 sources=(_lh(name, "workers"),) + tuple(sources),
                 armed=armed, note=note)


WORKERS_LOOPS = (
    _w("poller", None, ("service_heartbeats", "poller")),
    _w("chain_listener", None, ("service_heartbeats", "chain_listener")),
    _w("metadata", 60.0, ("service_heartbeats", "metadata")),
    _w("analytics", 300.0, ("service_heartbeats", "analytics")),
    _w("dispatcher", None, ("service_heartbeats", "dispatcher")),
    _w("roster", 168 * 3600.0, ("service_heartbeats", "roster")),
    _w("reconciler", 3600.0, ("service_heartbeats", "reconciler")),
    _w("premap", 1800.0, ("ingestion_state", "premap_last", "at")),
    _w("price_path", 5.0),
    _w("roster_auto", 3600.0, ("ingestion_state", "roster_auto_last", "at")),
    _w("edge_marks", 20.0),
    _w("mirror_shadow", 30.0, ("service_heartbeats", "mirror_shadow")),
    _w("retention", 3600.0, ("service_heartbeats", "retention")),
    _w("rn1_obs", None, note="inert unless RN1_OBSERVABILITY_SHADOW is set"),
    _w("shadow_bettor", 60.0, ("service_heartbeats", "shadow_bettor")),
    _w("bettor_state", 60.0, ("service_heartbeats", "bettor_state")),
    _w("institutional_md", 60.0, ("service_heartbeats", "institutional_md")),
    _w("shadow_experimental", 60.0,
       ("service_heartbeats", "shadow_experimental")),
    _w("shadow_rn1", 5.0, ("service_heartbeats", "shadow_rn1")),
    _w("bettor_live", None, note="STOPPED by its control row; decision only"),
    _w("memory", 60.0, ("service_heartbeats", "workers_memory")),
)
#: registered in workers/all.py LOOPS and deliberately NOT started (cand21)
WORKERS_NOT_STARTED = ("copy_sweep", "underdog", "whale_exits", "mirror_live")

INVENTORY = API_LOOPS + WORKERS_LOOPS
BY_NAME = {(s["name"], s["process"]): s for s in INVENTORY}


# ═════════════════════════════════════════════════════════════════════
# THE RECORDER
# ═════════════════════════════════════════════════════════════════════

START, SUCCESS, ERROR = "START", "SUCCESS", "ERROR"
_last_write: dict = {}
_last_warn: dict = {}

UPSERT_SQL = """
INSERT INTO runtime_loop_health AS h (loop_name, process, cadence_s,
    last_start_at, last_success_at, last_error_at, last_error,
    starts, successes, errors, commit_sha, host, pid, detail, updated_at)
VALUES ($1, $2, $3,
    CASE WHEN $4 = 'START' THEN now() END,
    CASE WHEN $4 = 'SUCCESS' THEN now() END,
    CASE WHEN $4 = 'ERROR' THEN now() END,
    CASE WHEN $4 = 'ERROR' THEN $5 END,
    CASE WHEN $4 = 'START' THEN 1 ELSE 0 END,
    CASE WHEN $4 = 'SUCCESS' THEN 1 ELSE 0 END,
    CASE WHEN $4 = 'ERROR' THEN 1 ELSE 0 END,
    $6, $7, $8, $9::jsonb, now())
ON CONFLICT (loop_name, process) DO UPDATE SET
    cadence_s = EXCLUDED.cadence_s,
    last_start_at = coalesce(EXCLUDED.last_start_at, h.last_start_at),
    last_success_at = coalesce(EXCLUDED.last_success_at, h.last_success_at),
    last_error_at = coalesce(EXCLUDED.last_error_at, h.last_error_at),
    last_error = CASE WHEN EXCLUDED.last_error_at IS NOT NULL
                      THEN EXCLUDED.last_error ELSE h.last_error END,
    starts = h.starts + EXCLUDED.starts,
    successes = h.successes + EXCLUDED.successes,
    errors = h.errors + EXCLUDED.errors,
    commit_sha = EXCLUDED.commit_sha, host = EXCLUDED.host,
    pid = EXCLUDED.pid,
    detail = CASE WHEN EXCLUDED.detail = '{}'::jsonb THEN h.detail
                  ELSE EXCLUDED.detail END,
    updated_at = now()
"""


def _error_text(error) -> str | None:
    if error is None:
        return None
    if isinstance(error, BaseException):
        text = "%s: %s" % (type(error).__name__, error)
    else:
        text = str(error)
    return text[:MAX_ERROR_CHARS]


def due(name: str, process: str, phase: str, *, now: float | None = None,
        every_s: float | None = None) -> bool:
    """A START or ERROR is always written; a SUCCESS at most every
    `every_s` (the inventory's record_every_s) per loop, in this process."""
    if phase != SUCCESS:
        return True
    every = every_s
    if every is None:
        spec = BY_NAME.get((name, process)) or {}
        every = spec.get("record_every_s")
    if not every:
        return True
    now = time.monotonic() if now is None else now
    last = _last_write.get((name, process, SUCCESS))
    return last is None or now - last >= float(every)


async def record(target, name: str, *, process: str, phase: str,
                 cadence_s: float | None = None, error=None,
                 detail: dict | None = None) -> bool:
    """Write one START / SUCCESS / ERROR for a loop. `target` is a
    connection (anything with .execute) or a pool (anything with .acquire).
    Returns True when written. NEVER RAISES; bounded by RECORD_TIMEOUT_S."""
    if phase not in (START, SUCCESS, ERROR):
        return False
    mono = time.monotonic()
    if not due(name, process, phase, now=mono):
        return False
    spec = BY_NAME.get((name, process)) or {}
    cad = cadence_s if cadence_s is not None else spec.get("cadence_s")
    cad = float(cad) if cad else 3600.0
    args = (name, process, cad, phase, _error_text(error),
            (os.environ.get("RENDER_GIT_COMMIT") or None),
            socket.gethostname()[:120], os.getpid(),
            json.dumps(detail or {}, default=str)[:4000])
    try:
        async with asyncio.timeout(RECORD_TIMEOUT_S):
            if hasattr(target, "execute") and not hasattr(target, "acquire"):
                await target.execute(UPSERT_SQL, *args)
            else:
                async with target.acquire() as conn:
                    await conn.execute(UPSERT_SQL, *args)
        _last_write[(name, process, phase)] = mono
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                    # noqa: BLE001
        k = (name, process)
        if mono - _last_warn.get(k, -1e18) >= WARN_EVERY_S:
            _last_warn[k] = mono
            log.warning("loop health %s/%s %s not written: %s: %s", name,
                        process, phase, type(exc).__name__, exc)
        return False


_spawned: set = set()


def spawn_record(name: str, *, process: str, phase: str, error=None,
                 detail: dict | None = None) -> bool:
    """Fire-and-forget `record` through the process's EXISTING pool, for a
    caller that must not wait (workers/all.py's supervisor). Does nothing
    when this process has no pool yet: building one (db.get_pool) can walk a
    105 s backoff against a dead database, and a supervisor never waits on
    telemetry. Returns True when a write was scheduled."""
    from . import db as _db
    pool = _db._pool
    if pool is None or not due(name, process, phase):
        return False
    try:
        task = asyncio.get_running_loop().create_task(record(
            pool, name, process=process, phase=phase, error=error,
            detail=detail))
    except RuntimeError:
        return False
    _spawned.add(task)
    task.add_done_callback(_spawned.discard)
    return True


# ═════════════════════════════════════════════════════════════════════
# THE VERDICT (pure)
# ═════════════════════════════════════════════════════════════════════

def _ep(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.timestamp()
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except ValueError:
        return None


def armed(spec: dict, env=None, rows: dict | None = None) -> tuple:
    """(armed: bool | None, why). None = cannot be judged from here (a
    workers-process flag read in the API)."""
    env = os.environ if env is None else env
    rule = spec.get("armed") or ("always",)
    kind = rule[0]
    if kind == "always":
        return True, None
    if spec["process"] != "api":
        return None, "ARMING_IS_THE_WORKERS_PROCESS_ENV"
    if kind == "env_on":
        v = str(env.get(rule[1], "")).strip().lower()
        return (v in _ON), (None if v in _ON else "ENV_%s_NOT_ON" % rule[1])
    if kind == "env_not_off":
        v = str(env.get(rule[1], rule[2])).strip().lower()
        return (v not in _OFF), (None if v not in _OFF
                                 else "ENV_%s_OFF" % rule[1])
    if kind == "env_on_both":
        bad = [n for n in rule[1:]
               if str(env.get(n, "")).strip().lower() not in _ON]
        return (not bad), (None if not bad else "ENV_%s_NOT_ON" % bad[0])
    if kind == "env_not_off_and_row":
        v = str(env.get(rule[1], "")).strip().lower()
        if v in _OFF:
            return False, "ENV_%s_OFF" % rule[1]
        row = (rows or {}).get(rule[2])
        if row is None:
            return None, "CONTROL_ROW_%s_UNREAD" % rule[2]
        return (row is True), (None if row is True
                               else "CONTROL_ROW_%s_NOT_TRUE" % rule[2])
    return None, "UNKNOWN_ARMING_RULE"


def classify(spec: dict, facts: dict, *, now: float, is_armed=True,
             armed_why=None, holders: list | None = None) -> dict:
    """One loop's verdict from its gathered facts. Pure.

    facts: {"success_at": [(epoch, source), ...], "start_at", "error_at",
            "error", "beat_status", "sources_read": [...],
            "sources_missing": [...]}"""
    cad = spec.get("cadence_s")
    succ = [(t, s) for t, s in (facts.get("success_at") or [])
            if t is not None]
    last_success, success_source = (max(succ) if succ else (None, None))
    out = {"name": spec["name"], "process": spec["process"],
           "capital_critical": spec["capital_critical"],
           "cadence_s": cad,
           "unhealthy_after_s": None if not cad else HEALTH_FACTOR * cad,
           "lease": dict(spec["lease"]),
           "armed": is_armed, "armed_why": armed_why,
           "last_start_at": facts.get("start_at"),
           "last_success_at": last_success,
           "success_source": success_source,
           "last_error_at": facts.get("error_at"),
           "last_error": facts.get("error"),
           "beat_status": facts.get("beat_status"),
           "lag_s": None if last_success is None
           else round(now - last_success, 1),
           "sources_missing": list(facts.get("sources_missing") or []),
           "note": spec.get("note")}
    key = spec["lease"].get("key")
    if holders is not None and key is not None and spec["lease"]["kind"] in (
            "ADVISORY_LOCK", "OWN_LEASE"):
        out["lease"]["holders"] = holders
        out["lease"]["held"] = bool(holders)
    if is_armed is False:
        out.update(status=DISABLED, why=armed_why or "NOT_ARMED")
        return out
    if (spec["capital_critical"] and is_armed and holders is not None
            and key is not None
            and spec["lease"]["kind"] in ("ADVISORY_LOCK", "OWN_LEASE")
            and not holders):
        out.update(status=UNHEALTHY, why="WRITER_LOCK_HELD_BY_NO_BACKEND")
        return out
    if not spec["sources"]:
        out.update(status=UNAVAILABLE, why="NO_PERSISTED_HEALTH_SOURCE")
        return out
    if not cad:
        out.update(status=EVENT_DRIVEN if spec["name"] == "pinnapi_reactive"
                   else UNAVAILABLE,
                   why=("NO_CADENCE_EVENT_DRIVEN"
                        if spec["name"] == "pinnapi_reactive"
                        else "NO_DECLARED_CADENCE"))
        return out
    if last_success is None:
        out.update(status=UNAVAILABLE, why=(
            "NO_SUCCESS_RECORDED" if facts.get("sources_read")
            else "NO_SOURCE_READABLE"))
        return out
    if now - last_success <= HEALTH_FACTOR * cad:
        out.update(status=HEALTHY, why=None)
    else:
        out.update(status=UNHEALTHY,
                   why="NO_SUCCESS_WITHIN_%gX_CADENCE" % HEALTH_FACTOR)
    return out


# ═════════════════════════════════════════════════════════════════════
# THE READER (SELECT only; each source in its own savepoint)
# ═════════════════════════════════════════════════════════════════════

async def _try(conn, fn, missing: list, label: str):
    try:
        async with conn.transaction():
            return await fn()
    except Exception as exc:                                    # noqa: BLE001
        missing.append("%s:%s" % (label, type(exc).__name__))
        return None


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


async def read(conn, *, now: float | None = None, env=None) -> dict:
    now = float(time.time() if now is None else now)
    missing: list = []
    lh = await _try(conn, lambda: conn.fetch(
        "SELECT loop_name, process, cadence_s, last_start_at, "
        "       last_success_at, last_error_at, last_error, starts, "
        "       successes, errors, commit_sha, detail "
        "  FROM runtime_loop_health"), missing, "runtime_loop_health")
    lh = {(r["loop_name"], r["process"]): dict(r) for r in (lh or [])}
    sb = await _try(conn, lambda: conn.fetch(
        "SELECT service, status, beat_at FROM service_heartbeats"),
        missing, "service_heartbeats")
    sb = {r["service"]: dict(r) for r in (sb or [])}
    keys = sorted({s[1] for spec in INVENTORY for s in spec["sources"]
                   if s[0] == "ingestion_state"} | {"pinnapi_feed"})
    ist_rows = await _try(conn, lambda: conn.fetch(
        "SELECT key, value FROM ingestion_state WHERE key = ANY($1::text[])",
        keys), missing, "ingestion_state")
    ist = {r["key"]: _j(r["value"]) for r in (ist_rows or [])}
    runs = {}
    for t in sorted({s[1] for spec in INVENTORY for s in spec["sources"]
                     if s[0] == "run_table"}):
        got = await _try(conn, lambda t=t: conn.fetchrow(
            "SELECT max(finished_at) FILTER (WHERE status = 'OK') AS ok_at, "
            "       max(coalesce(finished_at, started_at)) FILTER "
            "           (WHERE status <> 'OK') AS err_at, "
            "       max(started_at) AS start_at FROM %s "
            " WHERE started_at > now() - interval '7 days'" % t),
            missing, t)
        if got is not None:
            runs[t] = dict(got)
    ra = await _try(conn, lambda: conn.fetchrow(
        "SELECT max(updated_at) FILTER (WHERE state = 'COMPLETED') AS ok_at, "
        "       max(updated_at) FILTER (WHERE state IN "
        "           ('TIMEOUT', 'ERROR')) AS err_at, "
        "       count(*) FILTER (WHERE state = 'STARTED' AND updated_at < "
        "           now() - interval '60 seconds') AS orphaned_started "
        "  FROM pinnapi_reactive_attempts "
        " WHERE created_at > now() - interval '24 hours'"),
        missing, "pinnapi_reactive_attempts")
    lock_keys = sorted({s["lease"]["key"] for s in INVENTORY
                        if s["lease"].get("key") is not None})
    holders_rows = await _try(conn, lambda: conn.fetch(
        "SELECT ((l.classid::bigint << 32) | l.objid::bigint) AS k, "
        "       l.pid, a.application_name, host(a.client_addr) AS client "
        "  FROM pg_locks l LEFT JOIN pg_stat_activity a ON a.pid = l.pid "
        " WHERE l.locktype = 'advisory' AND l.granted AND l.objsubid = 1 "
        "   AND ((l.classid::bigint << 32) | l.objid::bigint) "
        "       = ANY($1::bigint[])", lock_keys), missing, "pg_locks")
    holders = None if holders_rows is None else {}
    for r in holders_rows or []:
        holders.setdefault(int(r["k"]), []).append(
            {"pid": r["pid"], "application_name": r["application_name"],
             "client": r["client"]})
    # the feed's arming row: absent or anything but true is DISARMED (the
    # feed's own rule); an unreadable table is not judged
    control_rows = {"pinnapi_feed": None if ist_rows is None
                    else ist.get("pinnapi_feed") is True}

    loops = []
    for spec in INVENTORY:
        facts = {"success_at": [], "sources_read": [],
                 "sources_missing": []}
        for src in spec["sources"]:
            kind = src[0]
            if kind == "runtime_loop_health":
                row = lh.get((src[1], src[2]))
                facts["sources_read"].append("runtime_loop_health")
                if row:
                    facts["success_at"].append(
                        (_ep(row["last_success_at"]), "runtime_loop_health"))
                    facts["start_at"] = _ep(row["last_start_at"])
                    facts["error_at"] = _ep(row["last_error_at"])
                    facts["error"] = row["last_error"]
            elif kind == "service_heartbeats":
                row = sb.get(src[1])
                facts["sources_read"].append("service_heartbeats:" + src[1])
                if row:
                    facts["success_at"].append(
                        (_ep(row["beat_at"]), "service_heartbeats:" + src[1]))
                    facts["beat_status"] = row["status"]
                else:
                    facts["sources_missing"].append(
                        "service_heartbeats:%s:NO_ROW" % src[1])
            elif kind == "ingestion_state":
                v = ist.get(src[1])
                facts["sources_read"].append("ingestion_state:" + src[1])
                if isinstance(v, dict) and v.get(src[2]) is not None:
                    facts["success_at"].append(
                        (_ep(v.get(src[2])), "ingestion_state:" + src[1]))
                    facts.setdefault("beat_status", v.get("state")
                                     or v.get("status"))
                else:
                    facts["sources_missing"].append(
                        "ingestion_state:%s:NO_ROW" % src[1])
            elif kind == "run_table":
                row = runs.get(src[1])
                if row is not None:
                    facts["sources_read"].append(src[1])
                    facts["success_at"].append((_ep(row["ok_at"]), src[1]))
                    facts.setdefault("start_at", _ep(row["start_at"]))
                    if row["err_at"] is not None:
                        facts["error_at"] = _ep(row["err_at"])
            elif kind == "reactive_attempts" and ra is not None:
                facts["sources_read"].append("pinnapi_reactive_attempts")
                facts["success_at"].append(
                    (_ep(ra["ok_at"]), "pinnapi_reactive_attempts"))
                facts["error_at"] = _ep(ra["err_at"])
                facts["beat_status"] = (
                    "ORPHANED_STARTED_24H=%s" % ra["orphaned_started"])
        is_armed, why = armed(spec, env=env, rows=control_rows)
        key = spec["lease"].get("key")
        h = None if holders is None or key is None else holders.get(key, [])
        loops.append(classify(spec, facts, now=now, is_armed=is_armed,
                              armed_why=why, holders=h))
    summary = {}
    for lp in loops:
        summary[lp["status"]] = summary.get(lp["status"], 0) + 1
    critical_bad = [lp["name"] for lp in loops if lp["capital_critical"]
                    and lp["status"] in (UNHEALTHY, UNAVAILABLE)]
    return {"version": VERSION, "now": now,
            "rule": "UNHEALTHY when the newest recorded success is older "
                    "than %g x the loop's cadence" % HEALTH_FACTOR,
            "loops": loops, "summary": summary,
            "capital_critical_not_healthy": critical_bad,
            "workers_not_started_by_design": list(WORKERS_NOT_STARTED),
            "sources_missing": missing}
