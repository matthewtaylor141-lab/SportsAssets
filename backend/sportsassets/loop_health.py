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
older, when the loop has NEVER succeeded since its last known start (its own
START record, or the process's boot) and that start is more than 3 x cadence
ago, or when a capital-critical, armed loop's writer lock is held by no
backend; STARTING when it started less than 3 x cadence ago and has not
succeeded yet; DISABLED (named) when the loop is not armed in this
deployment; EVENT_DRIVEN when it has no cadence to judge against;
UNAVAILABLE (named) only when its sources are unreadable or absent, or when
nothing says when it last started -- never a manufactured success.

WHAT COUNTS AS A SUCCESS (R30A review). A heartbeat is a success only when
its status is in THAT WRITER's success vocabulary, declared per source below
from the writer's own code and the production rows (research-sql run
37231484481): `agent_karen` 'ok' is a success and 'error' is not;
`shadow_rn1` 'venue_unreadable' and `shadow_bettor` 'store_not_ready' are
passes that FAILED and are reported as such (beat_status, last_error), not
as a fresh success. A status the vocabulary does not name is NOT a success
(fail closed) and is shown by name. Business outcomes a writer reports from
a pass that ran -- 'idle', 'drift', 'no_eligible_population', a cycle that
was STOPPED or BLOCKED -- are successes of the LOOP; whether the business is
healthy is the SLOs' question, not this one's.
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
STARTING = "STARTING"
#: the bound on a record's JSON detail; content beyond it is summarised
#: (keys kept, values dropped) BEFORE serialising, so the row is always valid
#: JSON -- cutting the serialised string made the jsonb cast fail and lost
#: the whole row, an ERROR included (R30A review)
MAX_DETAIL_CHARS = 4000

#: THIS PROCESS's start, the "first sighting" of every loop the API lifespan
#: starts: a loop that has recorded no success since the process booted more
#: than 3 x its cadence ago is UNHEALTHY. (The workers process's own boot is
#: read from ingestion_state 'workers_boot'.) Imported at app start-up by
#: the loop-health router, so it is the API's boot to within a second.
PROCESS_STARTED_AT = time.time()

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
K_CAPITAL_READINESS = 0x43524C31
#: (rc6.3 kalshi-shadow) the Kalshi SHADOW planner's per-pass lock
K_KSHADOW = 0x4B534831

_ON = ("on", "1", "true", "yes")
_OFF = ("off", "0", "false", "no")


def _spec(name, process, cadence_s, *, critical, lease, sources, armed=None,
          record_every_s=None, period_s=None, note=None):
    """`cadence_s` is what the 3 x rule judges against; `period_s` the loop's
    own sleep when it differs (a 2 s tick recorded every 30 s). A test pins
    record_every_s + period_s well inside 3 x cadence, so a loop that
    succeeds on every pass can never read UNHEALTHY between two writes."""
    return {"name": name, "process": process, "cadence_s": cadence_s,
            "capital_critical": bool(critical), "lease": lease,
            "sources": tuple(sources), "armed": armed or ("always",),
            "record_every_s": record_every_s,
            "period_s": period_s if period_s is not None else cadence_s,
            "note": note}


def _lh(name, process):
    return ("runtime_loop_health", name, process)


def _hb(service, *success):
    """A service_heartbeats row, a success only when its status is one of
    `success` (that writer's vocabulary)."""
    return ("service_heartbeats", service, frozenset(success))


#: ingestion_state rules: ANY -- the key is written only when a pass
#: completes; FIELD_IN -- a success when value[field] is one of the names;
#: FIELD_NULL -- a success when value[field] is null (the writer stamps its
#: error there on a failed pass)
ANY = ("ANY",)


def _is(key, at_field, rule=ANY):
    return ("ingestion_state", key, at_field, rule)


def _in(field, *names):
    return ("FIELD_IN", field, frozenset(names))


def _null(field):
    return ("FIELD_NULL", field)


#: ONE vocabulary for the five agent runners (each writes 'ok' / 'error')
OK_ERROR = ("ok",)


# ── THE API LIFESPAN (api/app.py) ──────────────────────────────────────
API_LOOPS = (
    _spec("ext_pinnacle.entry_cycle", "api", 900.0, critical=True,
          lease={"kind": "ADVISORY_LOCK", "key": K_EXT_PINNACLE,
                 "session": "own (db.lease_session)",
                 "fencing": "db.advisory_held before every pass; a lost lock "
                            "stops the children and contends again"},
          armed=("env_on", "EXT_PINNACLE_SHADOW"),
          sources=(_lh("ext_pinnacle.entry_cycle", "api"),
                   # written only when a cycle completes (LIVE, STOPPED and
                   # BLOCKED are all completed passes)
                   _is("ext_pinnacle_last_cycle", "at")),
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
                   # SERVICED, or NOT_RUN when the execution lock was held
                   # (a completed pass that skipped); shown as beat_status
                   _is("ext_pinnacle_last_servicing", "at",
                       _in("state", "SERVICED", "NOT_RUN"))),
          note="management and recovery of held positions on their own "
               "cadence"),
    _spec("pinnapi_feed.heartbeat", "api", 30.0, critical=True,
          lease={"kind": "OWN_LEASE", "key": K_FEED,
                 "session": "own (pinnapi_owner.Lease)",
                 "fencing": "the owner re-checks its lease AND the decider's "
                            "writer lock every liveness pass"},
          armed=("env_not_off_and_row", "PINNAPI_FEED", "pinnapi_feed"),
          # a beat is written every HEARTBEAT_S whatever the feed's state;
          # only OWNER_SYNCED is a feed that delivers (CONNECTING,
          # RESYNCHRONIZING, STANDBY, OWNER_ERROR ... are not successes)
          sources=(_is("pinnapi_feed_last", "beat_at",
                       _in("state", "OWNER_SYNCED")),),
          note="the one provider socket; Xavier's held-position measure"),
    _spec("pinnapi_held.refresh", "api", 10.0, critical=True,
          lease={"kind": "CHILD_OF", "parent": "pinnapi_feed.heartbeat",
                 "key": K_FEED,
                 "fencing": "lives and dies with the feed owner"},
          armed=("env_not_off_and_row", "PINNAPI_FEED", "pinnapi_feed"),
          # EVERY pass is recorded (R30A review): a 30 s throttle against a
          # 3 x 10 s threshold read a loop that succeeded on every pass as
          # UNHEALTHY ~5% of the time
          record_every_s=None, period_s=10.0,
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
          record_every_s=30.0, period_s=2.0,
          sources=(_lh("execmirror.tick", "api"),),
          note="ticks every 2 s (TICK_S); health is recorded at most every "
               "30 s, so 30 s is its health cadence. Reviews actual "
               "positions even while the control row keeps the lane off"),
    # the desk's cadence is env-set in bettor_desk_loop (CYCLE_S =
    # float(os.getenv("BETTOR_DESK_CYCLE_S", "20"))); read the same knob, so
    # an override there is not judged against the default here
    _spec("bettor_desk_loop", "api",
          float(os.getenv("BETTOR_DESK_CYCLE_S", "20")), critical=False,
          lease={"kind": "ADVISORY_LOCK", "key": K_DESK,
                 "session": "own (db.lease_session)"},
          armed=("env_on", "BETTOR_DESK_LOOP"), sources=(),
          note="SHADOW desk; no venue path; persists no success heartbeat"),
    _spec("rn1x_shadow", "api", 20.0, critical=False,
          lease={"kind": "ADVISORY_LOCK", "key": K_RN1X_SHADOW,
                 "session": "own (db.lease_session)"},
          armed=("env_not_off", "RN1X_SHADOW", "off"),
          sources=(_hb("rn1x_shadow", "ok", "idle"),)),
    _spec("rn1x_learn", "api", 3600.0, critical=False,
          lease={"kind": "ADVISORY_LOCK", "key": K_RN1X_LEARN,
                 "session": "own (db.lease_session)"},
          armed=("env_not_off", "RN1X_LEARN", "off"),
          sources=(_hb("rn1x_learn", "ok", "idle"),)),
    _spec("rn1x_model", "api", 3600.0, critical=False,
          lease={"kind": "ADVISORY_LOCK", "key": K_RN1X_MODEL,
                 "session": "own (db.lease_session)"},
          armed=("env_on", "RN1X_MODEL_FIT"),
          sources=(_is("rn1x_model_last_cycle", "at",
                       _in("state", "LIVE", "STOPPED", "BLOCKED")),)),
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
    # THE CAPITAL READINESS OBSERVER writes ONLY the four append-only
    # migration-310 tables (no heartbeat, no run table with started /
    # finished columns), so it has no persisted health source here; its
    # newest run is readable at GET /api/command/capital-readiness.
    _spec("capital_readiness.observer", "api", 900.0, critical=False,
          lease={"kind": "ADVISORY_PER_CYCLE", "key": K_CAPITAL_READINESS},
          armed=("env_not_off", "CAPITAL_READINESS_OBSERVER", "on"),
          sources=(),
          note="RESEARCH / SHADOW_NO_AUTHORITY; capital_readiness_runs.computed_at "
               "is its record"),
    _spec("agents.karen_runner", "api", 300.0, critical=False,
          lease={"kind": "NONE", "why": "writes only her own challenge "
                 "records, keyed per detector and target"},
          armed=("env_not_off", "KAREN_RUNNER_ENABLED", "1"),
          sources=(_hb("agent_karen", *OK_ERROR),)),
    _spec("agents.peer_responder", "api", 120.0, critical=False,
          lease={"kind": "NONE", "why": "answers recorded challenges; one "
                 "response per challenge"},
          armed=("env_not_off", "PEER_RESPONDER_ENABLED", "1"),
          sources=(_hb("agent_peer_responder", *OK_ERROR),)),
    _spec("agents.archer_runner", "api", 300.0, critical=False,
          lease={"kind": "NONE", "why": "SHADOW estimates keyed per "
                 "decision"},
          # (266) the variable carries the agent's new name; a deployment
          # that set only the historical EDDIE_RUNNER_ENABLED keeps it
          armed=("env_not_off_or_legacy", "ARCHER_RUNNER_ENABLED", "1",
                 "EDDIE_RUNNER_ENABLED"),
          sources=(_hb("agent_archer", *OK_ERROR),)),
    _spec("agents.scout_runner", "api", 600.0, critical=False,
          lease={"kind": "NONE", "why": "RESEARCH observations keyed per "
                 "feature and window"},
          armed=("env_not_off", "SCOUT_RUNNER_ENABLED", "1"),
          sources=(_hb("agent_scout", *OK_ERROR),)),
    _spec("agents.adriana_runner", "api", 300.0, critical=False,
          lease={"kind": "NONE", "why": "SHADOW census passes keyed per "
                 "scan id"},
          armed=("env_not_off", "ADRIANA_RUNNER_ENABLED", "1"),
          sources=(_hb("agent_adriana", *OK_ERROR),)),
    # (rc6.3 kalshi-shadow) SHADOW only: the Kalshi leg of each linked PAPER
    # decision planned with kalshi_orders.plan and recorded PLANNED /
    # EXCLUDED (migration 367); a GET-only account reconciliation per 15
    # min window. Never submits. 'stood_down' is a pass that ran and found
    # a Kalshi live-money switch on (or its tables absent) and so recorded
    # nothing -- the writer's business outcome, not a failure.
    _spec("kalshi_shadow.runner", "api", 15.0, critical=False,
          lease={"kind": "ADVISORY_PER_CYCLE", "key": K_KSHADOW,
                 "why": "pg_try_advisory_xact_lock per planning pass; rows "
                        "are keyed per decision (UNIQUE) and append-only"},
          armed=("env_not_off", "KALSHI_SHADOW_PLANNER", "on"),
          sources=(_hb("kalshi_shadow", "ok", "stood_down"),),
          note="SHADOW only; no order, cancel or capital path"),
    _spec("redteam.runner", "api", 300.0, critical=False,
          lease={"kind": "NONE", "why": "append-only receipts keyed per "
                 "pass and evidence hash"},
          armed=("env_not_off", "RED_TEAM_RUNNER_ENABLED", "1"),
          sources=(_hb("red_team_readiness", *OK_ERROR),)),
    _spec("agents.improvement_pipeline", "api", 600.0, critical=False,
          lease={"kind": "NONE", "why": "items seeded from source keys "
                 "(UNIQUE); stages mirrored, never decided"},
          armed=("env_not_off", "IMPROVEMENT_PIPELINE_ENABLED", "1"),
          sources=(_hb("improvement_pipeline", *OK_ERROR),
                   ("run_table", "improve_runs"))),
    _spec("agents.capability_runtime", "api", 15.0, critical=False,
          lease={"kind": "TASK_LEASE_ROWS",
                 "why": "agent_tasks claimed with an expiring lease"},
          # its heartbeat is written only at the end of a whole tick; a
          # failed tick records ERROR (with the failing phase) here
          sources=(_is("agent.capabilities.heartbeat:paper_acct_main", "at",
                       _in("status", "OK")),
                   _lh("agents.capability_runtime", "api")),
          note="sleeps 15 s between ticks; RESEARCH_ONLY"),
    # ── the rest of the lifespan's recurring tasks (R30A review: the
    # inventory test now parses every create_task in the lifespan) ──────
    _spec("slack_bridge.run", "api", 3.0, critical=False,
          lease={"kind": "TASK_LEASE_ROWS",
                 "why": "deliveries claimed row by row with a claim token "
                        "(agent_slack_delivery); no venue path"},
          armed=("env_set", "SLACK_TEAM_ID"), sources=(),
          note="persists no liveness record (only delivery rows when there "
               "is work); reported UNAVAILABLE rather than guessed"),
    _spec("institutional_api_stream", "api", None, critical=False,
          lease={"kind": "NONE", "why": "observation only: a market-data "
                 "stream and its refdata reads; writes no order"},
          armed=("env_on", "INSTITUTIONAL_MD_STREAM"), sources=(),
          note="started by institutional_api_stream.start only when the "
               "credential passes its identity guard; state is in process"),
    _spec("api.poller_fallback", "api", None, critical=False,
          lease={"kind": "NONE", "why": "diagnostic ingestion fallback; "
                 "trades dedupe on their keys"},
          armed=("env_eq", "API_INGESTION_FALLBACK", "1"),
          sources=(_hb("poller", "ok", "idle"),),
          note="shares the workers poller's heartbeat row"),
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

#: tasks the lifespan starts ONCE (not loops): no cadence, no health
API_ONE_SHOT = ("warm_cache", "_rescore_once")

# ── THE WORKERS SERVICE (workers/all.py) ───────────────────────────────
_WORKERS_LEASE = {"kind": "NONE",
                  "why": "the workers process is LOCKED in the execution "
                         "gate before any loop starts (cand21): no venue "
                         "write; ingestion and measurement writes dedupe on "
                         "their keys"}


def _w(name, cadence_s, *sources, note=None, armed=None,
       record_every_s=None, period_s=None):
    return _spec(name, "workers", cadence_s, critical=False,
                 lease=dict(_WORKERS_LEASE),
                 sources=(_lh(name, "workers"),) + tuple(sources),
                 armed=armed, note=note, record_every_s=record_every_s,
                 period_s=period_s)


# EACH VOCABULARY IS THE WRITER'S OWN (its heartbeat call sites) checked
# against the production rows of research-sql run 37231484481: 'running'
# (analytics) marks a pass that STARTED; 'degraded' (metadata, mirror_shadow)
# a pass in which a stage failed; 'tick_failed', 'store_not_ready',
# '*_unreadable', 'sweep_failed', 'credential_missing', 'registry_mismatch',
# 'refused', 'down', 'error' passes that failed. 'drift' (reconciler: the
# walk ran and found misses), 'idle', 'high' (memory: measured, and high),
# 'off' (retention told not to delete), 'no_universe', 'no_focus_set',
# 'no_eligible_population', 'already_sealed' are passes that ran.
# 'coverage_gap' (reconciler, RC6 identity lane) is a pass that ran but
# left fills that no run swept (a named refusal on the beat): NOT a success.
WORKERS_LOOPS = (
    _w("poller", None, _hb("poller", "ok", "idle")),
    _w("chain_listener", None, _hb("chain_listener", "ok")),
    _w("metadata", 60.0, _hb("metadata", "ok")),
    _w("analytics", 300.0, _hb("analytics", "ok")),
    _w("dispatcher", None, _hb("dispatcher", "ok")),
    _w("roster", 168 * 3600.0, _hb("roster", "ok")),
    _w("reconciler", 3600.0, _hb("reconciler", "ok", "drift")),
    _w("premap", 1800.0, _is("premap_last", "at", _null("err"))),
    # price_path and edge_marks record their own SUCCESS / ERROR (R30A
    # review: before, only the supervisor's START / ERROR reached this row,
    # so neither could ever be judged). Health cadence 30 s / 60 s, written
    # at most every 30 s; the loops themselves pass every 5 s / 20 s.
    _w("price_path", 30.0, record_every_s=30.0, period_s=5.0,
       note="samples every POLL_S (5 s); health recorded at most every 30 s"),
    _w("roster_auto", 3600.0, _is("roster_auto_last", "at", _null("error"))),
    _w("edge_marks", 60.0, record_every_s=30.0, period_s=20.0,
       note="passes every EVERY_S (20 s); health recorded at most every "
            "30 s"),
    _w("mirror_shadow", 30.0, _hb("mirror_shadow", "ok")),
    _w("kalshi_market_data", 30.0, _hb("kalshi_market_data", "ok",
                                       "degraded")),
    _w("retention", 3600.0, _hb("retention", "ok", "off")),
    _w("rn1_obs", None, note="inert unless RN1_OBSERVABILITY_SHADOW is set"),
    _w("shadow_bettor", 60.0, _hb("shadow_bettor", "ok", "no_universe")),
    _w("bettor_state", 60.0, _hb("bettor_state", "ok")),
    _w("institutional_md", 60.0,
       _hb("institutional_md", "ok", "no_focus_set")),
    _w("shadow_experimental", 60.0,
       _hb("shadow_experimental", "ok", "no_eligible_population",
           "no_focus_set", "already_sealed")),
    _w("shadow_rn1", 5.0, _hb("shadow_rn1", "ok")),
    _w("bettor_live", None, note="STOPPED by its control row; decision only"),
    _w("memory", 60.0, _hb("workers_memory", "ok", "high")),
)
#: registered in workers/all.py LOOPS and deliberately NOT started (cand21)
WORKERS_NOT_STARTED = ("copy_sweep", "underdog", "whale_exits", "mirror_live")
#: never a workers loop at all: each runs in its own dedicated service
#: (workers/all.py DEDICATED_ONLY_LOOPS); its health is its own heartbeat,
#: read by its own readback (/api/command/market-plane)
WORKERS_DEDICATED_ONLY = ("universal_market_plane", "kalshi_ws_market_data")

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


def bounded_detail(detail, limit: int = MAX_DETAIL_CHARS) -> str:
    """The record's JSON detail, ALWAYS valid JSON and at most `limit`
    characters. Bounded on the CONTENT, never on the serialised string: an
    oversized detail keeps its keys (and says it was truncated and how big
    it was) and drops the values; a detail whose keys alone are too big
    keeps only the marker."""
    try:
        text = json.dumps(detail or {}, default=str)
    except (TypeError, ValueError) as exc:
        return json.dumps({"truncated": True,
                           "unserialisable": type(exc).__name__})
    if not isinstance(detail or {}, dict):
        return json.dumps({"truncated": True, "not_an_object": True})
    if len(text) <= limit:
        return text
    keys = sorted(str(k) for k in (detail or {}))
    out = {"truncated": True, "original_chars": len(text), "keys": []}
    for k in keys:
        out["keys"].append(k[:64])
        if len(json.dumps(out)) > limit - 16:
            out["keys"].pop()
            break
    return json.dumps(out)


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
            bounded_detail(detail))
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
    if kind == "env_not_off_or_legacy":
        # (name, default, legacy name): the name when set, else the legacy
        # name when set, else the default
        name = rule[1] if env.get(rule[1]) is not None else (
            rule[3] if env.get(rule[3]) is not None else rule[1])
        v = str(env.get(name, rule[2])).strip().lower()
        return (v not in _OFF), (None if v not in _OFF
                                 else "ENV_%s_OFF" % name)
    if kind == "env_set":
        v = str(env.get(rule[1], "") or "").strip()
        return bool(v), (None if v else "ENV_%s_NOT_SET" % rule[1])
    if kind == "env_eq":
        v = str(env.get(rule[1], "") or "").strip()
        return (v == rule[2]), (None if v == rule[2]
                                else "ENV_%s_NOT_%s" % (rule[1], rule[2]))
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
            "error", "beat_status", "process_started_at",
            "sources_read": [...], "sources_missing": [...]}

    THE NEVER-SUCCEEDED RULE (R30A review). A loop with no recorded success
    is judged against its LAST KNOWN START -- its own START record, or the
    boot of the process that runs it, whichever is later: more than
    HEALTH_FACTOR x cadence ago it is UNHEALTHY (NO_SUCCESS_SINCE_START),
    within it STARTING. Before, it read UNAVAILABLE, so the loop that
    starts and fails on every pass -- the bettor_state heartbeat of
    2026-10-04 -- never read as unhealthy at all. UNAVAILABLE is kept for
    unreadable or absent sources, and for a loop whose start is unknown."""
    cad = spec.get("cadence_s")
    succ = [(t, s) for t, s in (facts.get("success_at") or [])
            if t is not None]
    last_success, success_source = (max(succ) if succ else (None, None))
    own_start = facts.get("start_at")
    boot = facts.get("process_started_at")
    starts = [t for t in (own_start, boot) if t is not None]
    anchor = max(starts) if starts else None
    out = {"name": spec["name"], "process": spec["process"],
           "capital_critical": spec["capital_critical"],
           "cadence_s": cad,
           "unhealthy_after_s": None if not cad else HEALTH_FACTOR * cad,
           "lease": dict(spec["lease"]),
           "armed": is_armed, "armed_why": armed_why,
           "last_start_at": anchor,
           "start_source": (None if anchor is None
                            else "runtime_loop_health" if anchor == own_start
                            else "process_boot"),
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
    limit = HEALTH_FACTOR * cad
    if last_success is not None:
        if now - last_success <= limit:
            out.update(status=HEALTHY, why=None)
        else:
            out.update(status=UNHEALTHY,
                       why="NO_SUCCESS_WITHIN_%gX_CADENCE" % HEALTH_FACTOR)
        return out
    if not facts.get("sources_read"):
        out.update(status=UNAVAILABLE, why="NO_SOURCE_READABLE")
        return out
    if facts.get("error_at") is not None:
        # it RAN and the newest thing on record is a failure, with no
        # success on record anywhere (an overwritten heartbeat row that
        # now says 'error', or a loop-health row that has errors and no
        # success): not healthy, by name -- never UNAVAILABLE
        out.update(status=UNHEALTHY,
                   why="NO_SUCCESS_ON_RECORD_LATEST_PASS_FAILED")
        return out
    if anchor is None:
        out.update(status=UNAVAILABLE,
                   why="NO_SUCCESS_RECORDED_AND_NO_KNOWN_START")
        return out
    if now - anchor > limit:
        out.update(status=UNHEALTHY, why="NO_SUCCESS_SINCE_START")
    else:
        out.update(status=STARTING, why="NO_SUCCESS_YET_WITHIN_%gX_CADENCE"
                                        "_OF_START" % HEALTH_FACTOR)
    return out


def beat_ok(value, rule) -> tuple:
    """(is a success, status label) for one ingestion_state value under its
    source's rule. Pure."""
    v = value if isinstance(value, dict) else {}
    kind = (rule or ANY)[0]
    if kind == "ANY":
        return True, (v.get("state") or v.get("status"))
    if kind == "FIELD_IN":
        got = v.get(rule[1])
        return (got in rule[2]), (None if got is None else str(got))
    if kind == "FIELD_NULL":
        got = v.get(rule[1])
        return (got is None), (None if got is None
                               else "%s=%s" % (rule[1], str(got)[:120]))
    return False, "UNKNOWN_RULE"


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
    lh_rows = lh
    lh = {(r["loop_name"], r["process"]): dict(r) for r in (lh or [])}
    sb_rows = await _try(conn, lambda: conn.fetch(
        "SELECT service, status, beat_at, detail ->> 'refusal' AS refusal, "
        "       detail ->> 'owner_blocker' AS owner_blocker "
        "  FROM service_heartbeats"),
        missing, "service_heartbeats")
    sb = {r["service"]: dict(r) for r in (sb_rows or [])}
    keys = sorted({s[1] for spec in INVENTORY for s in spec["sources"]
                   if s[0] == "ingestion_state"} | {"pinnapi_feed",
                                                     "workers_boot"})
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

    # THE FIRST SIGHTING of every loop: its process's boot. The API's is
    # this process's own; the workers' is the boot stamp workers/all.py
    # writes (ingestion_state 'workers_boot'), absent -> unknown.
    wb = ist.get("workers_boot")
    boots = {"api": PROCESS_STARTED_AT,
             "workers": _ep(wb.get("at")) if isinstance(wb, dict) else None}

    def _error(facts, at, text):
        if at is not None and (facts.get("error_at") is None
                               or at >= facts["error_at"]):
            facts["error_at"], facts["error"] = at, text

    loops = []
    for spec in INVENTORY:
        facts = {"success_at": [], "sources_read": [],
                 "sources_missing": [],
                 "process_started_at": boots.get(spec["process"])}
        for src in spec["sources"]:
            kind = src[0]
            if kind == "runtime_loop_health":
                if lh_rows is None:
                    continue
                row = lh.get((src[1], src[2]))
                facts["sources_read"].append("runtime_loop_health")
                if row:
                    facts["success_at"].append(
                        (_ep(row["last_success_at"]), "runtime_loop_health"))
                    facts["start_at"] = _ep(row["last_start_at"])
                    _error(facts, _ep(row["last_error_at"]),
                           row["last_error"])
            elif kind == "service_heartbeats":
                if sb_rows is None:
                    continue
                row = sb.get(src[1])
                label = "service_heartbeats:" + src[1]
                facts["sources_read"].append(label)
                if row:
                    at, status = _ep(row["beat_at"]), row["status"]
                    facts["beat_status"] = status
                    if status in src[2]:
                        facts["success_at"].append((at, label))
                    else:
                        # A FAILED PASS IS NOT A SUCCESS: the writer said so
                        # in its own status (or used one its vocabulary
                        # does not name, which is not trusted either). A beat
                        # that NAMES its refusal (and the owner blocker, when
                        # only the owner can clear it) carries both into the
                        # error text, so the loop reads as degraded BY NAME,
                        # never as a nameless failure (RC6 identity lane:
                        # mirror_shadow on a ledger-derived reading)
                        why = "NON_SUCCESS_BEAT:%s" % status
                        if row.get("refusal"):
                            why += ":%s" % row["refusal"]
                        if row.get("owner_blocker"):
                            why += ":OWNER_BLOCKER=%s" % row["owner_blocker"]
                        _error(facts, at, why)
                else:
                    facts["sources_missing"].append(
                        "service_heartbeats:%s:NO_ROW" % src[1])
            elif kind == "ingestion_state":
                if ist_rows is None:
                    continue
                v = ist.get(src[1])
                label = "ingestion_state:" + src[1]
                facts["sources_read"].append(label)
                if isinstance(v, dict) and v.get(src[2]) is not None:
                    at = _ep(v.get(src[2]))
                    ok, status = beat_ok(v, src[3] if len(src) > 3 else ANY)
                    facts.setdefault("beat_status", status)
                    if ok:
                        facts["success_at"].append((at, label))
                    else:
                        _error(facts, at, "NON_SUCCESS_BEAT:%s" % status)
                else:
                    facts["sources_missing"].append(
                        "ingestion_state:%s:NO_ROW" % src[1])
            elif kind == "run_table":
                row = runs.get(src[1])
                if row is not None:
                    facts["sources_read"].append(src[1])
                    facts["success_at"].append((_ep(row["ok_at"]), src[1]))
                    st = _ep(row["start_at"])
                    if st is not None and (facts.get("start_at") is None
                                           or st > facts["start_at"]):
                        facts["start_at"] = st
                    if row["err_at"] is not None:
                        _error(facts, _ep(row["err_at"]),
                               "RUN_NOT_OK:%s" % src[1])
            elif kind == "reactive_attempts" and ra is not None:
                facts["sources_read"].append("pinnapi_reactive_attempts")
                facts["success_at"].append(
                    (_ep(ra["ok_at"]), "pinnapi_reactive_attempts"))
                _error(facts, _ep(ra["err_at"]), "TIMEOUT_OR_ERROR_ATTEMPT")
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
                    and lp["status"] in (UNHEALTHY, UNAVAILABLE, STARTING)]
    return {"version": VERSION, "now": now,
            "rule": "UNHEALTHY when the newest recorded success is older "
                    "than %g x the loop's cadence, or when there is none "
                    "since a start more than %g x cadence ago; a heartbeat "
                    "counts only when its status is in its writer's "
                    "success vocabulary" % (HEALTH_FACTOR, HEALTH_FACTOR),
            "process_started_at": {"api": boots["api"],
                                   "workers": boots["workers"]},
            "loops": loops, "summary": summary,
            "capital_critical_not_healthy": critical_bad,
            "workers_not_started_by_design": list(WORKERS_NOT_STARTED),
            "dedicated_service_only_by_design": list(WORKERS_DEDICATED_ONLY),
            "sources_missing": missing}
