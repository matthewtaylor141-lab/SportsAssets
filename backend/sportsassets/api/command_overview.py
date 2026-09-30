"""THE MANAGEMENT OVERVIEW: ONE READ OF WHAT IS RUNNING AND WHAT IS BLOCKED.

Every section answers from production tables or from this serving process, and
every section carries its own status:

  OK           read, and it has data
  EMPTY        read, and there is nothing -- with the NAMED reason there is
               nothing (an empty book is a fact to explain, not a zero to show)
  UNAVAILABLE  the read itself failed -- with the error's type; never shown as
               an empty book or a row of zeros

Read-only. No mutating statement, no venue client. The funded-launch verdict
is `bettor_funded_activation.readiness` sorted by `bettor_pilot_prerequisites`
into who can clear each check -- the same functions the admin prerequisites
route serves -- so this page cannot disagree with them.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

VERSION = "COMMAND_OVERVIEW_V1"

OK = "OK"
EMPTY = "EMPTY"
UNAVAILABLE = "UNAVAILABLE"

HEARTBEAT_KEY = "ext_pinnacle_last_cycle"


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _iso(t):
    if t is None:
        return None
    try:
        return t.isoformat()
    except AttributeError:
        return str(t)


def _sec(status, data=None, why=None, **extra) -> dict:
    return dict({"status": status, "why": why, "data": data}, **extra)


async def _regclass(conn, name) -> bool:
    return await conn.fetchval("SELECT to_regclass($1)", name) is not None


async def _section(fn, conn) -> dict:
    """One section, never raising: a failed read is UNAVAILABLE by name."""
    try:
        return await fn(conn)
    except Exception as exc:                                    # noqa: BLE001
        return _sec(UNAVAILABLE, why="the read failed: %s" % type(exc).__name__)


async def _heartbeat(conn) -> dict | None:
    raw = await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key = $1", HEARTBEAT_KEY)
    return _j(raw) if raw is not None else None


# ── SECTIONS ─────────────────────────────────────────────────────────

async def build_and_mode(conn) -> dict:
    from . import app as _app

    hb = await _heartbeat(conn) or {}
    switches = _app.running_switches()
    at = hb.get("at")
    age = None if at is None else round(time.time() - float(at), 1)
    data = {
        "serving_commit": os.getenv("RENDER_GIT_COMMIT") or None,
        "cycle_writer_build": (hb.get("writer") or {}).get("build"),
        "cycle_state": hb.get("state"),
        "cycle_why": hb.get("why"),
        "last_cycle_at": at, "last_cycle_age_s": age,
        "submission_switches": switches,
        "funded_submission": ("ENABLED" if all(switches.values())
                              else "DISABLED"),
        "operating_mode": (
            "FUNDED" if all(switches.values()) else
            "NON_FUNDED: entry valuations, calibration and pair observations "
            "run on the schedule; no order can be sent"),
    }
    if not hb:
        return _sec(EMPTY, data, why=("no cycle heartbeat has been written: "
                                      "the scheduled loop has not run on "
                                      "this database"))
    return _sec(OK, data)


async def observations(conn) -> dict:
    if not await _regclass(conn, "bettor_pair_observations"):
        return _sec(UNAVAILABLE, why="the observation table is not in this "
                                     "database (migration 140 absent)")
    has_adm = await conn.fetchval(
        "SELECT count(*) FROM information_schema.columns "
        " WHERE table_name='bettor_pair_observations' "
        "   AND column_name='admission_status'")
    adm = "admission_status" if has_adm else "'ADMITTED_BY_DISCOVERY'"
    rows = await conn.fetch(
        "SELECT %s AS admission, label_status, count(*) AS n, "
        "       count(DISTINCT fixture) AS fixtures, "
        "       min(observed_at) AS first, max(observed_at) AS last "
        "  FROM bettor_pair_observations GROUP BY 1, 2 ORDER BY 1, 2" % adm)
    recent = await conn.fetchval(
        "SELECT count(*) FROM bettor_pair_observations "
        " WHERE observed_at > now() - interval '24 hours'")
    attempts = []
    if await _regclass(conn, "bettor_pair_observation_attempts"):
        attempts = [dict(r) for r in await conn.fetch(
            "SELECT coalesce(detail->>'conclusion', outcome) AS conclusion, "
            "       count(*) AS n, count(DISTINCT fixture) AS fixtures, "
            "       max(attempted_at) AS last "
            "  FROM bettor_pair_observation_attempts "
            " WHERE attempted_at > now() - interval '24 hours' "
            " GROUP BY 1 ORDER BY 2 DESC")]
    data = {"by_admission_and_label": [
                dict(r, first=_iso(r["first"]), last=_iso(r["last"]))
                for r in rows],
            "total": sum(int(r["n"]) for r in rows),
            "fixtures": sum(int(r["fixtures"]) for r in rows),
            "last_24h": int(recent or 0),
            "attempts_24h_by_conclusion": [
                dict(a, last=_iso(a["last"])) for a in attempts],
            "meaning": ("ADMITTED_BY_DISCOVERY rows passed the same discovery "
                        "funded pairing uses. OBSERVED_NOT_ADMITTED rows are "
                        "correctly identified pairs whose cancellation "
                        "treatment is unresolved: research evidence, never an "
                        "approval, and excluded from model training")}
    if not rows:
        return _sec(EMPTY, data, why=(
            "no pair observation has been recorded yet: every attempt is "
            "listed by conclusion below"))
    return _sec(OK, data)


async def calibration(conn) -> dict:
    hb = await _heartbeat(conn) or {}
    m = hb.get("source_calibration_measurement") or {}
    short = (m.get("shortfall") or {}) if isinstance(m, dict) else {}
    by_purpose = [dict(r) for r in await conn.fetch(
        "SELECT record_purpose, count(*) AS n FROM external_valuations "
        " WHERE decided_at > now() - interval '24 hours' GROUP BY 1")]
    data = {"status": m.get("status"),
            "resolved_fixtures": m.get("resolved_fixtures"),
            "needed_for_verdict": short.get("needed_for_verdict"),
            "shortfall": short.get("shortfall"),
            "unit": short.get("unit"),
            "last_measured_at": m.get("last_ran_at"),
            "valuations_24h_by_purpose": by_purpose,
            "meaning": ("independent resolved fixtures scored point in time; "
                        "a verdict needs the evaluator's own split (fit + "
                        "300 scored)")}
    if not m:
        return _sec(EMPTY, data, why="the calibration measurement has not "
                                     "reported in the latest cycle")
    return _sec(OK, data)


async def xavier(conn) -> dict:
    if not await _regclass(conn, "bettor_xavier_decisions"):
        return _sec(UNAVAILABLE, why="migration 148 absent")
    rows = [dict(r) for r in await conn.fetch(
        "SELECT responsibility_state, coalesce(chosen_action, '-') AS action, "
        "       split_part(execution_eligibility, ':', 1) AS eligibility, "
        "       count(*) AS n, count(DISTINCT intent_id) AS positions, "
        "       max(decided_at) AS newest "
        "  FROM bettor_xavier_decisions "
        " WHERE decided_at > now() - interval '7 days' "
        " GROUP BY 1, 2, 3 ORDER BY n DESC")]
    hb = await _heartbeat(conn) or {}
    data = {"decisions_7d": [dict(r, newest=_iso(r["newest"])) for r in rows],
            "daily_review": hb.get("xavier_review") or hb.get("xavier")}
    if not rows:
        return _sec(EMPTY, data, why=(
            "no funded position exists for Xavier to manage: funded "
            "submission is disabled and no account is authorized, so no "
            "entry has filled. The full management path is demonstrated "
            "in rehearsal with substituted venue transport (see evidence)"))
    return _sec(OK, data)


async def execution(conn) -> dict:
    if not await _regclass(conn, "bettor_funded_intents"):
        return _sec(UNAVAILABLE, why="migration 125 absent")
    rows = [dict(r) for r in await conn.fetch(
        "SELECT state, count(*) AS n, max(created_at) AS newest "
        "  FROM bettor_funded_intents GROUP BY 1 ORDER BY 2 DESC")]
    events = []
    if await _regclass(conn, "bettor_xavier_execution_events"):
        events = [dict(r) for r in await conn.fetch(
            "SELECT event_kind, count(*) AS n FROM "
            " bettor_xavier_execution_events GROUP BY 1 ORDER BY 2 DESC")]
    data = {"funded_intents_by_state": [dict(r, newest=_iso(r["newest"]))
                                        for r in rows],
            "execution_events": events}
    if not rows:
        return _sec(EMPTY, data, why=(
            "no funded order intent has ever been recorded: submission is "
            "disabled in this build and no first order has been authorized"))
    return _sec(OK, data)


async def account(conn) -> dict:
    if not await _regclass(conn, "bettor_desk_accounts"):
        return _sec(UNAVAILABLE, why="the account registry is absent")
    rows = [dict(r) for r in await conn.fetch(
        "SELECT account_id, status, paused, "
        "       left(coalesce(pause_reason, ''), 160) AS pause_reason, "
        "       accounting_status, last_verified_at "
        "  FROM bettor_desk_accounts ORDER BY account_id")]
    for r in rows:
        r["last_verified_at"] = _iso(r["last_verified_at"])
    if not rows:
        return _sec(EMPTY, [], why="no venue account is registered")
    return _sec(OK, rows)


async def funded_launch(conn) -> dict:
    """The serving process's own readiness, sorted by who can clear it."""
    from .. import bettor_funded_activation as FA
    from .. import bettor_pilot_prerequisites as PR

    bound = _j(await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key = $1", FA.ACCOUNT_KEY))
    got = await FA.readiness(conn, account_id=(bound or {}).get("account_id"))
    cls = PR.classify(got["checks"])
    data = {"ready": got.get("ready"),
            "unmet_count": got.get("unmet_count"),
            "unknown_count": got.get("unknown_count"),
            "bound_account": (bound or {}).get("account_id"),
            "by_owner": cls}
    verdict = ("READY_FOR_THE_OWNERS_APPROVAL_STEP" if got.get("ready")
               else "NOT_READY: a first order is blocked by the unmet checks "
                    "below, each with the party who can clear it")
    return _sec(OK, data, verdict=verdict)


SECTIONS = (("build_and_mode", build_and_mode),
            ("observations", observations),
            ("calibration", calibration),
            ("xavier", xavier),
            ("execution", execution),
            ("account", account),
            ("funded_launch", funded_launch))


async def overview(conn) -> dict:
    out: dict[str, Any] = {"version": VERSION, "read_at": time.time(),
                           "read_only": True}
    for name, fn in SECTIONS:
        out[name] = await _section(fn, conn)
    return out
