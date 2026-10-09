"""THE PAPER SESSION: ONE DURABLE, RESUMABLE SESSION WITH A FROZEN CONFIG.

A session has an id, a start time, a FROZEN config and a FROZEN simulator
version (migration 171's trigger refuses any change to them). There is at
most one ACTIVE session per paper account; a process restart RESUMES it --
the ledger, orders, fills and positions are all in the database, every write
is idempotent by key, so nothing is duplicated and nothing is lost.

ENABLEMENT: the session runs only when BOTH are on --
    the process environment  PAPER_SESSION=on
    the database control row paper_control('PAPER_SESSION').enabled
Migration 171 inserts the row enabled, so the session starts once the
migration applies and the flag is set. Turning the row off stops it.

THE RISK CONFIGURATION (defaults for the $500,000 book, stated here and
frozen into every session's config):
    per-order cap            $5,000   (limit x qty + max fees of one order)
    per-market cap           $10,000  (open cost basis + open reservations)
    per-fixture cap          $15,000  (the same, over every market of a
                                       fixture)
    hedge reserve            20% of cash: an ENTRY may not reduce available
                             cash below it; hedges and exits may use it
    capital reservations     every open BUY reserves limit x qty + max fees
                             (bettor_paper_ledger); no cash is spent twice
    concurrent groups        up to 150 at once

THE OBJECTIVES are RECORDED AND MEASURED, and never used to lower a
threshold or force a trade (`OBJECTIVES_NEVER_BIND`):
    filled acquisition volume >= $250,000/day -- the simulated cost of
        filled PURCHASES, entries and hedges reported separately; sale
        proceeds are reported separately and never counted;
    200-400 distinct markets/day, distinct fixtures counted separately;
    net P&L of $5,000-$10,000/day after simulated costs.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import time
from typing import Any

from . import bettor_paper_ledger as L

VERSION = "PAPER_SESSION_V1"
CONFIG_VERSION = "PAPER_SESSION_CONFIG_V1"
SIMULATOR_VERSION = "PAPER_SIM_V1"
ENV_FLAG = "PAPER_SESSION"
CONTROL_KEY = "PAPER_SESSION"
REPORTING_TZ = "America/New_York"
RECENT_HEARTBEATS_KEPT = 20

OBJECTIVES_NEVER_BIND = (
    "RECORDED_AND_MEASURED_ONLY: no objective lowers an entry threshold, "
    "relaxes a risk cap or forces a trade; a shortfall is reported with its "
    "causes")

R_ENV_OFF = "PAPER_SESSION_ENVIRONMENT_FLAG_IS_NOT_ON"
R_CONTROL_OFF = "THE_PAPER_SESSION_CONTROL_ROW_IS_OFF"
R_CONTROL_ABSENT = "THE_PAPER_SESSION_CONTROL_ROW_IS_ABSENT"
R_SCHEMA_ABSENT = "MIGRATION_171_IS_NOT_APPLIED"


def default_config() -> dict:
    """THE CODE DEFAULT, frozen into a session at its start."""
    from .agents import derek_policy as DP
    from .workers import ext_pinnacle_loop as LOOP
    return {
        "config_version": CONFIG_VERSION,
        "account_id": L.ACCOUNT_ID,
        "reporting_tz": REPORTING_TZ,
        "simulator_version": SIMULATOR_VERSION,
        "data_label": L.DATA_LABEL,
        "labels": dict(L.LABELS),
        "risk": {
            "per_order_cap_usd": 5000.0,
            "per_market_cap_usd": 10000.0,
            "per_fixture_cap_usd": 15000.0,
            "hedge_reserve_fraction": 0.20,
            "max_concurrent_groups": 150,
            "capital_reservation": ("every open BUY reserves limit x qty + "
                                    "max fees; cash is never spent twice"),
            "multiple_concurrent_groups": True,
        },
        "entry": {
            "policy": DP.POLICY_V2,
            "min_gross_edge_pp": DP.MIN_GROSS_EDGE_PROBABILITY,
            "min_net_ev_usd": DP.DEFAULT_PARAMS["min_net_ev_usd"],
            "order_type": "MARKETABLE",
            "time_in_force": "IOC",
            "allow_partial": True,
            "target_order_usd": 5000.0,
            "limit_rule": ("the highest ask level at which the blended "
                           "probability still clears the minimum gross edge;"
                           " the depth walk stops there"),
            "internal_model": ("the newest CANDIDATE Derek research model "
                               "(bettor_funded_model.KEY_ENTRY_PAYOUT, "
                               "source DEREK_RESEARCH_OBSERVATIONS), used in "
                               "the PAPER session only, labelled "
                               "EXPERIMENTAL_RESEARCH_MODEL with its approval "
                               "status; never promoted"),
            "pinnacle_max_age_s": float(LOOP.PINNACLE_MAX_AGE_S),
            "valuation_lookback_s": 1800.0,
        },
        "simulator": {
            "version": SIMULATOR_VERSION,
            "decision_to_execution_delay_s": 2.0,
            "marketable_ttl_s": 90.0,
            "resting_gtd_s": 3600.0,
            "fees": "bettor_funded_book.fee_for (the deployed schedule), "
                    "charged per simulated fill",
            "marketable": ("walk the observed depth of the side the intent "
                           "consumes, within the price limit, on the FIRST "
                           "book observed at or after decision time + delay; "
                           "no such book before the TTL -> EXPIRED, no fill"),
            "resting": ("never fills on a touch: fills only when displayed "
                        "executable liquidity STRICTLY CROSSES the limit, "
                        "after the queue ahead at placement (displayed size "
                        "at-or-better on our side plus earlier paper orders "
                        "there) is exhausted; filled at the limit"),
            "consumed_liquidity": ("one observed (market, side, price, book "
                                   "instant) level is consumed at most once "
                                   "across every paper order"),
            "missing_updates": "never proof of a fill",
            "optimistic_sensitivity": ("reported separately: the same orders "
                                       "filled at the decision-time book "
                                       "with no delay, no consumption ledger "
                                       "and touch fills; never the primary "
                                       "result"),
        },
        "cadence": {
            "pass_budget_s": 20.0,
            "max_book_reads_per_pass": 12,
            "max_decisions_per_pass": 40,
            "xavier_backstop_s": 60.0,
        },
        "objectives": {
            "filled_acquisition_volume_usd_per_day": 250000.0,
            "distinct_markets_per_day": [200, 400],
            "net_pnl_usd_per_day": [5000.0, 10000.0],
            "binding": False,
            "rule": OBJECTIVES_NEVER_BIND,
            "definitions": {
                "acquisition_volume": ("simulated cost of FILLED purchases "
                                       "(qty x price + fees), entries and "
                                       "hedges separately; sale proceeds "
                                       "reported separately and never "
                                       "counted"),
                "distinct_markets": ("distinct us_market_slug with a filled "
                                     "purchase that day; repeat orders or "
                                     "both sides of one market count once"),
                "distinct_fixtures": "distinct fixtures, counted separately",
                "net_pnl": ("change in total equity over the day after "
                            "simulated fees; realized and unrealized shown "
                            "separately")},
        },
        "never": ["real money", "real venue orders", "funded activation",
                  "promotion of a research model",
                  "an invented or Pinnacle-only internal probability",
                  "a demonstration trade in the live paper account"],
    }


def config_sha(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True,
                                     default=str).encode()).hexdigest()


def env_on() -> bool:
    return str(os.environ.get(ENV_FLAG, "")).strip().lower() in (
        "on", "1", "true", "yes")


async def _regclass(conn, name: str) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        name))
    except Exception:                                           # noqa: BLE001
        return False


async def enablement(conn) -> dict:
    """BOTH the environment flag AND the control row must be on."""
    out: dict[str, Any] = {"env_flag": ENV_FLAG, "env_on": env_on()}
    if not await _regclass(conn, "paper_control"):
        return dict(out, enabled=False, control_on=None,
                    refusal=R_SCHEMA_ABSENT)
    row = await conn.fetchrow(
        "SELECT enabled, why, updated_by, updated_at FROM paper_control "
        " WHERE control_key = $1", CONTROL_KEY)
    if row is None:
        return dict(out, enabled=False, control_on=None,
                    refusal=R_CONTROL_ABSENT)
    out.update(control_on=bool(row["enabled"]), control_why=row["why"],
               control_updated_by=row["updated_by"])
    if not out["env_on"]:
        return dict(out, enabled=False, refusal=R_ENV_OFF)
    if not row["enabled"]:
        return dict(out, enabled=False, refusal=R_CONTROL_OFF)
    return dict(out, enabled=True, refusal=None)


def session_id_for(at: float, account_id: str = L.ACCOUNT_ID) -> str:
    stamp = _dt.datetime.fromtimestamp(
        float(at), _dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if account_id == L.ACCOUNT_ID:
        return "paper_session_" + stamp
    return "paper_session_%s_%s" % (hashlib.sha256(
        account_id.encode()).hexdigest()[:10], stamp)


async def ensure_session(conn, *, now: float | None = None,
                         config: dict | None = None,
                         account_id: str = L.ACCOUNT_ID) -> dict:
    """THE ACTIVE SESSION, RESUMED; or a new one frozen with `config` (the
    code default) when none is active. Idempotent and restart-safe: the
    unique ACTIVE index means two racing starts produce one session."""
    at = float(now if now is not None else time.time())
    acct = await L.ensure_account(conn, account_id=account_id,
                                  account_key=(L.ACCOUNT_KEY
                                               if account_id == L.ACCOUNT_ID
                                               else account_id.upper()))
    if not acct.get("ok"):
        return {"ok": False, "refusal": acct.get("refusal")}
    row = await conn.fetchrow(
        "SELECT * FROM paper_sessions WHERE account_id = $1 "
        "   AND status = 'ACTIVE'", account_id)
    resumed = row is not None
    if row is None:
        cfg = dict(config or default_config())
        cfg["account_id"] = account_id
        sid = session_id_for(at, account_id)
        await conn.execute(
            "INSERT INTO paper_sessions (session_id, account_id, started_at, "
            " config, config_sha, simulator_version, reporting_tz) "
            "VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7) ON CONFLICT DO NOTHING",
            sid, account_id, L._ts(at), json.dumps(cfg, default=str),
            config_sha(cfg), cfg.get("simulator_version", SIMULATOR_VERSION),
            cfg.get("reporting_tz", REPORTING_TZ))
        row = await conn.fetchrow(
            "SELECT * FROM paper_sessions WHERE account_id = $1 "
            "   AND status = 'ACTIVE'", account_id)
    await conn.execute(
        "INSERT INTO paper_session_health (session_id) VALUES ($1) "
        "ON CONFLICT DO NOTHING", row["session_id"])
    view = session_view(row)
    code = default_config() if config is None else config
    view["code_config_differs"] = (config_sha(dict(code,
                                                   account_id=account_id))
                                   != row["config_sha"])
    return dict(view, ok=True, resumed=resumed)


def session_view(row) -> dict:
    d = dict(row)
    from . import bettor_paper_limits as LIMITS
    frozen_config = L._j(d["config"])
    return {"effective_config": LIMITS.effective_config(frozen_config, d["account_id"]),
            "capital_policy": LIMITS.describe(d["account_id"]),
            "session_id": d["session_id"], "account_id": d["account_id"],
            "started_at": L._epoch(d["started_at"]),
            "config": L._j(d["config"]), "config_sha": d["config_sha"],
            "simulator_version": d["simulator_version"],
            "reporting_tz": d["reporting_tz"], "status": d["status"],
            "frozen": ("config, simulator version, start and account are "
                       "frozen by migration 171's trigger")}


async def active_session(conn, account_id: str = L.ACCOUNT_ID) -> dict | None:
    row = await conn.fetchrow(
        "SELECT * FROM paper_sessions WHERE account_id = $1 "
        "   AND status = 'ACTIVE'", account_id)
    return None if row is None else session_view(row)


async def record_pass(conn, session_id: str, *, result: dict, now: float,
                      mutation_attempts: int = 0,
                      last_mutation_attempt: dict | None = None,
                      error: str | None = None) -> None:
    """THE HEARTBEAT: pass count, the last pass digest, recent heartbeats,
    and venue mutation attempts from the paper path (expected 0)."""
    steps_t = result.get("step_elapsed_s") or {}
    slow = (max(steps_t.items(), key=lambda kv: kv[1] or 0.0)
            if isinstance(steps_t, dict) and steps_t else (None, None))
    held = result.get("held_in_pass") or {}
    beat = {"at": float(now), "ok": error is None,
            "elapsed_s": result.get("elapsed_s"),
            "slowest_step": slow[0], "slowest_step_s": slow[1],
            "held_in_pass_reviews": held.get("reviews"),
            "summary": {k: result.get(k) for k in (
                "decisions_recorded", "orders_submitted", "fills",
                "books_read", "reviews", "budget_exhausted")}}
    # THE RING KEEPS THE NEWEST RECENT_HEARTBEATS_KEPT, OLDEST FIRST (SW-2).
    # Before this the newest-first subquery was aggregated without an order
    # and the next append re-numbered it, so every pass dropped the PREVIOUS
    # newest beat: production 2026-10-09 (research-sql 37945613145) kept 19
    # beats of 2026-10-01 01:47Z-02:03Z beside only the latest one, and no
    # pass duration of the last week was readable. Ordered by each beat's own
    # instant (`at`), so that scrambled ring heals on its next pass.
    await conn.execute(
        "UPDATE paper_session_health SET heartbeat_at = $2, "
        " passes = passes + 1, errors = errors + $3, "
        " mutation_attempts = mutation_attempts + $4, "
        " last_mutation_attempt = coalesce($5::jsonb, last_mutation_attempt),"
        " last_pass = $6::jsonb, last_error = coalesce($7, last_error), "
        " recent_heartbeats = (SELECT coalesce(jsonb_agg(x ORDER BY t_at, n),"
        "                                      '[]'::jsonb) "
        "   FROM (SELECT x, n, CASE WHEN jsonb_typeof(x->'at') = 'number' "
        "                      THEN (x->>'at')::float8 END AS t_at "
        "           FROM jsonb_array_elements("
        "                recent_heartbeats || jsonb_build_array($8::jsonb)) "
        "                WITH ORDINALITY AS t(x, n) "
        "          ORDER BY t_at DESC NULLS LAST, n DESC LIMIT $9) s) "
        " WHERE session_id = $1",
        session_id, L._ts(now), 1 if error else 0, int(mutation_attempts),
        (None if last_mutation_attempt is None
         else json.dumps(last_mutation_attempt, default=str)),
        json.dumps(result, default=str)[:60000], error,
        json.dumps(beat, default=str), RECENT_HEARTBEATS_KEPT)


async def health(conn, session_id: str) -> dict | None:
    r = await conn.fetchrow(
        "SELECT * FROM paper_session_health WHERE session_id = $1",
        session_id)
    if r is None:
        return None
    return {"heartbeat_at": L._epoch(r["heartbeat_at"]),
            "passes": int(r["passes"]), "errors": int(r["errors"]),
            "mutation_attempts": int(r["mutation_attempts"]),
            "mutation_attempts_expected": 0,
            "last_mutation_attempt": L._j(r["last_mutation_attempt"]),
            "last_pass": L._j(r["last_pass"]),
            "last_error": r["last_error"],
            "recent_heartbeats": L._j(r["recent_heartbeats"]) or []}


def describe() -> dict:
    return {"version": VERSION, "config_version": CONFIG_VERSION,
            "simulator_version": SIMULATOR_VERSION, "env_flag": ENV_FLAG,
            "control_key": CONTROL_KEY, "reporting_tz": REPORTING_TZ,
            "objectives_never_bind": OBJECTIVES_NEVER_BIND}
