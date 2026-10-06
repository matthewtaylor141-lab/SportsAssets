"""The Capital Readiness observer (RESEARCH / SHADOW_NO_AUTHORITY).

`observe(conn)` is POST-DECISION and READ-ONLY toward every BETTOR layer:

  phase 1  every collector of feeds.py runs inside ONE transaction that is
           rolled back (READ ONLY when the connection is not already in a
           transaction), each in its own savepoint: a collector that failed
           or tried to write is reported, never persisted;
  phase 2  the pure Lab modules (shadow_court, agent_championship,
           scale_twin, alpha_decay, portfolio_twin, adversarial_committee,
           readiness) turn that evidence into verdicts;
  phase 3  the results are appended through runner.py ONLY, in a
           transaction of their own, to the four migration-310 tables.

It never runs on a decision path: `run()` schedules it from the API
lifespan every CYCLE_S (CAPITAL_READINESS_OBSERVER=off is the kill switch),
under a per-cycle advisory lock, and logs and swallows every failure. It
cannot place, cancel, size, approve or route anything and changes no gate,
limit, cap or authority: a GREEN verdict still grants no live authority
(readiness.score sets live_authority_granted False, owner promotion
required) and SMALL LIVE stays SHADOW.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from . import adversarial_committee as AV
from . import agent_championship as AC
from . import alpha_decay as AD
from . import common as C
from . import feeds as F
from . import portfolio_twin as PT
from . import readiness as R
from . import runner as RUN
from . import scale_twin as ST

log = logging.getLogger(__name__)

VERSION = "CAPITAL_READINESS_OBSERVER_V1"
ENV_KILL = "CAPITAL_READINESS_OBSERVER"
CYCLE_S = 900.0
FIRST_DELAY_S = 420.0
LOCK_KEY = 0x43524C31                      # 'CRL1'
READ_STATEMENT_TIMEOUT_MS = 30000
TABLES = ("capital_readiness_runs", "capital_readiness_shadow_court",
          "capital_readiness_agent_economics",
          "capital_readiness_scale_trials")


def _plain(v):
    """JSON-safe copy (Decimal / datetime -> str) for the runner's dumps."""
    return json.loads(json.dumps(v, default=str))


def enabled() -> bool:
    return str(os.environ.get(ENV_KILL, "on")).strip().lower() not in (
        "off", "0", "false", "no")


async def _collect(conn, *, account_id, now, source_sha, readers):
    out: dict = {}

    async def step(name, fn):
        got, why = await F._guard(conn, fn)
        out[name] = got if got is not None else {
            "status": "UNAVAILABLE", "why": why}

    await step("court", lambda: F.court_judgements(conn, account_id, now=now))
    await step("court_outcomes", lambda: F.court_outcomes(conn, account_id))
    await step("agents", lambda: F.agent_observations(conn, account_id,
                                                      now=now))
    await step("scale", lambda: F.scale_inputs(conn, account_id, now=now))
    got, why = await F._guard(conn, lambda: F.gates(
        conn, account_id=account_id, now=now, source_sha=source_sha,
        readers=readers))
    out["gates"] = got if got is not None else {
        g: {"value": False, "reason": "UNREADABLE", "evidence": {"why": why}}
        for g in R.HARD_GATES}
    return out


async def _read_phase(conn, **kw):
    if conn.is_in_transaction():
        tr = conn.transaction()
    else:
        tr = conn.transaction(readonly=True)
    await tr.start()
    try:
        await conn.execute("SET LOCAL statement_timeout = %d"
                           % READ_STATEMENT_TIMEOUT_MS)
        return await _collect(conn, **kw)
    finally:
        await tr.rollback()


def _committee(gates: dict, championship: dict, twin: dict | None) -> dict:
    """The adversarial committee over flags we can actually measure."""
    agents = championship.get("agents") or {}
    ev = {"freshness_failed": gates.get("freshness_gte_95", {}).get(
        "value") is not True,
        "thin_sample": (not agents or any(
            a.get("sample_n", 0) < AC.MIN_SAMPLES for a in agents.values()))}
    if twin and twin.get("status") == "OK":
        pos = [r for r in twin.get("rows") or []
               if r.get("positive_lower_bound")]
        cap = C.num(twin.get("capacity_usd")) or 0
        ev["capacity_near_limit"] = bool(pos) and cap > 0 and max(
            r["capital_usd"] for r in pos) >= 0.8 * cap
    rep = AV.review(ev)
    rep["inputs"] = ev
    rep["unmeasured"] = sorted(set(AV.CRITICAL) - set(ev))
    return rep


async def observe(conn, *, now=None, source_sha=None, account_id=None,
                  readers=None) -> dict:
    """Compute and persist one readiness observation; returns the summary.
    Idempotent where the tables allow: a court per evaluation, a scored
    court per decision, an agent observation per (agent, metric, subject),
    a scale trial set only when its inputs changed."""
    if account_id is None:
        from .. import bettor_paper_ledger as L
        account_id = L.ACCOUNT_ID
    now = float(time.time() if now is None else now)
    ev = await _read_phase(conn, account_id=account_id, now=now,
                           source_sha=source_sha, readers=readers)
    summary: dict = {"version": VERSION, "label": C.LABEL,
                     "authority": C.AUTHORITY, "account_id": account_id,
                     "computed_at": now, "source_sha": source_sha}
    court = ev["court"]
    outs = ev["court_outcomes"]
    agents = ev["agents"]
    scale = ev["scale"]
    gates = ev["gates"]
    twin = None
    if scale.get("status") == "OK":
        twin = ST.evaluate(**scale["args"])
    async with conn.transaction():
        for c in court.get("courts") or []:
            await RUN.record_court(conn, _plain(c["court"]),
                                   decided_at=c["decided_at"])
        for s in outs.get("scored") or []:
            await RUN.record_court(conn, _plain(s["court"]),
                                   decided_at=s["decided_at"],
                                   result=_plain(s["result"]))
        for o in agents.get("observations") or []:
            await RUN.record_agent_observation(
                conn, agent=o["agent"], role_metric=o["role_metric"],
                subject_id=o["subject_id"],
                economic_alpha_usd=o["economic_alpha_usd"],
                detail=_plain(o["detail"]), observed_at=o["observed_at"])
        scale_recorded = 0
        if twin is not None and twin.get("status") == "OK":
            last = await conn.fetchval(
                "SELECT inputs->>'inputs_sha' FROM "
                " capital_readiness_scale_trials WHERE scope_key = $1 "
                " ORDER BY computed_at DESC, trial_id DESC LIMIT 1",
                scale["scope_key"])
            if last != scale["inputs"]["inputs_sha"]:
                await RUN.record_scale(conn, scale["scope_key"], twin,
                                       _plain(scale["inputs"]),
                                       computed_at=now)
                scale_recorded = sum(1 for r in twin["rows"]
                                     if r.get("status") == "MEASURED")
        stored = await F.stored_agent_rows(conn)
        champ = AC.evaluate(F.championship_input(stored))
        decay = AD.estimate(F.alpha_decay_input(stored))
        tail = PT.evaluate([])
        tail["why"] = ("NO_PORTFOLIO_STRESS_SCENARIO_PNL_SOURCE: "
                       "twin_scenario_results are policy counterfactual "
                       "worlds, not portfolio stress scenarios")
        committee = _committee(gates, champ, twin)
        verdict = R.score(gates={g: v.get("value") is True
                                 for g, v in gates.items()},
                          agent_championship=champ,
                          scale_twin=twin if twin and twin.get(
                              "status") == "OK" else None)
        verdict["gate_evidence"] = gates
        verdict["sections"] = {
            "agent_championship": champ,
            "agent_sources": agents.get("agents") or {
                "status": agents.get("status"), "why": agents.get("why")},
            "scale_twin": (twin if twin is not None else {
                "status": "UNAVAILABLE", "why": scale.get("why"),
                "detail": {k: v for k, v in scale.items()
                           if k not in ("status", "why")}}),
            "scale_inputs": scale.get("inputs"),
            "shadow_court": {
                "judged_this_run": len(court.get("courts") or []),
                "scored_this_run": len(outs.get("scored") or []),
                "status": court.get("status"), "why": court.get("why")},
            "alpha_decay": decay,
            "portfolio_twin": tail,
            "adversarial_committee": committee,
        }
        verdict["observer_version"] = VERSION
        await RUN.record_readiness(conn, _plain(verdict), source_sha=source_sha,
                                   computed_at=now)
    summary.update(
        court_judged=len(court.get("courts") or []),
        court_scored=len(outs.get("scored") or []),
        agent_observations=len(agents.get("observations") or []),
        scale={"status": "OK" if twin and twin.get("status") == "OK"
               else "UNAVAILABLE",
               "why": None if twin and twin.get("status") == "OK"
               else (scale.get("why") or (twin or {}).get("why")),
               "rows_recorded": scale_recorded,
               "recommended_capital_usd": (twin or {}).get(
                   "recommended_capital_usd", 0)},
        readiness_status=verdict["readiness_status"],
        recommended_capital_usd=verdict["recommended_capital_usd"],
        blocking_gates=verdict["blocking_gates"],
        live_authority_granted=False)
    return summary


async def _one(pool) -> dict:
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT to_regclass("
                                   "'capital_readiness_runs') IS NOT NULL"):
            return {"ran": False, "why": "MIGRATION_310_NOT_APPLIED"}
        if not await conn.fetchval("SELECT pg_try_advisory_lock($1)",
                                   LOCK_KEY):
            return {"ran": False, "why": "STANDBY_ANOTHER_OBSERVER_HOLDS_LOCK"}
        try:
            return dict(await observe(
                conn, source_sha=F.source_sha_from_env()), ran=True)
        finally:
            try:
                await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
            except Exception:                                  # noqa: BLE001
                log.warning("capital readiness unlock failed", exc_info=True)


async def run(get_pool) -> None:
    """The scheduled loop. Never raises into the API."""
    await asyncio.sleep(FIRST_DELAY_S)
    while True:
        try:
            if enabled():
                got = await _one(await get_pool())
                log.info("capital readiness observer: %s", {
                    k: got.get(k) for k in (
                        "ran", "why", "readiness_status", "court_judged",
                        "court_scored", "agent_observations", "scale")})
        except asyncio.CancelledError:
            raise
        except Exception:                                      # noqa: BLE001
            log.warning("capital readiness observer failed", exc_info=True)
        await asyncio.sleep(CYCLE_S)
