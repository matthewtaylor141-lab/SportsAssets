"""THE AGENT COMPONENTS A CANONICAL DECISION INTENT CARRIES (R30).

Every qualified ENTER decision's canonical intent (live_parity) carries, AS OF
THE DECISION INSTANT, from the decision's own inputs:

  derek              the verdict, policy decision and economics that made it
  eddie              Eddie's executable-EV estimate (agents/eddie.estimate --
                     the same pure estimator his runner uses), with the hard
                     rule applied
  opportunity_score  LOL_OPPORTUNITY_SCORE_V1 (lost_opportunity/score.score --
                     the same pure scorer) on the decision's executable EV and
                     capacity, Eddie's fill probability, the latest PAPER idle
                     capital snapshot and the recorded settlement lags
  allie              the Chief Allocator's rule (intel/allocator.allocate --
                     the same pure greedy allocation) applied to this
                     candidate against the open book of her latest run
  karen              Karen's review state of this market and strategy: open
                     challenges at the decision instant

Each component is MEASURED or carries an explicit UNAVAILABLE reason --
never a manufactured value. Components are evidence on the intent; none of
them changes the paper decision, its order or the live proposal (both
adapters read the same intent, so they see the same components).

Bounded: every read runs in its own savepoint under a short timeout, and the
slow-moving inputs (Eddie's fill history, settlement lags, the capital
snapshot, Allie's latest run) are cached for CACHE_S.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

VERSION = "CANONICAL_COMPONENTS_V1"
CACHE_S = 300.0
COMPONENT_TIMEOUT_S = 2.0
_CACHE: dict[str, tuple[float, Any]] = {}


def _un(why: str, **extra) -> dict:
    return dict({"status": "UNAVAILABLE", "why": why}, **extra)


def reset_cache() -> None:
    _CACHE.clear()


async def _cached(key: str, now: float, fn):
    hit = _CACHE.get(key)
    if hit is not None and now - hit[0] < CACHE_S:
        return hit[1]
    val = await fn()
    _CACHE[key] = (now, val)
    return val


async def _bounded(conn, fn, *, timeout=COMPONENT_TIMEOUT_S):
    """Run one component read in a savepoint under a timeout. A failure or a
    timeout is the component's UNAVAILABLE reason, never the decision's."""
    async def inner():
        async with conn.transaction():
            return await fn()
    try:
        return await asyncio.wait_for(inner(), timeout)
    except asyncio.TimeoutError:
        return _un("COMPONENT_TIMEOUT_AT_DECISION_%.1fS" % timeout)
    except Exception as exc:                                  # noqa: BLE001
        log.debug("canonical component failed", exc_info=True)
        return _un("COMPONENT_FAILED:%s" % type(exc).__name__)


def derek_component(*, verdict, policy_version, policy_decision, refusals,
                    economics, gross_edge_pp, probability) -> dict:
    pd = policy_decision if isinstance(policy_decision, dict) else {}
    econ = economics if isinstance(economics, dict) else {}
    return {"status": "MEASURED", "verdict": verdict,
            "policy_version": policy_version,
            "selection_reason": pd.get("selection_reason") or pd.get("reason"),
            "refusals": list(refusals or []),
            "probability": probability, "gross_edge_pp": gross_edge_pp,
            "expected_net_profit_usd": econ.get("expected_net_profit_usd"),
            "basis": "the paper decision that qualified this intent"}


def eddie_component(est: dict | None, why: str | None = None) -> dict:
    if not est or est.get("status") == "UNAVAILABLE":
        return _un(why or (est or {}).get("why") or "NO_EDDIE_ESTIMATE")
    from .agents import eddie as E
    rec, overridden = E.enforce_hard_rule(
        est.get("recommendation"),
        net_pp=est.get("expected_net_executable_edge_pp"),
        ev_usd=est.get("expected_executable_ev_usd"),
        fill_p=est.get("expected_fill_probability"))
    return {"status": "MEASURED", "estimator_version": est.get(
                "estimator_version"),
            "estimate_id": est.get("estimate_id"),
            "recommendation": rec, "hard_rule_overrode": overridden,
            "expected_executable_ev_usd": est.get("expected_executable_ev_usd"),
            "expected_net_executable_edge_pp": est.get(
                "expected_net_executable_edge_pp"),
            "expected_fill_probability": est.get("expected_fill_probability"),
            "execution_style": est.get("execution_style"),
            "unmeasured": est.get("unmeasured") or {},
            "authority": "SHADOW_ONLY_CARRIED_AS_EVIDENCE"}


async def eddie_at_decision(conn, *, decision: dict, book_row: dict | None,
                            now: float) -> dict:
    from .agents import eddie as E

    async def hist():
        return await E.history_stats(conn, now=now)

    async def go():
        h = await _cached("eddie_history", now, hist)
        return E.estimate(decision, book_row, h, now=now)
    est = await _bounded(conn, go)
    if est.get("status") == "UNAVAILABLE":
        return eddie_component(None, est["why"])
    return eddie_component(est)


async def opportunity_at_decision(conn, *, decision: dict, eddie: dict,
                                  now: float) -> dict:
    from .lost_opportunity import reads as R
    from .lost_opportunity import score as SC

    async def lags():
        return await R.lag_samples(conn, now=now)

    async def capital():
        snaps = await R.snapshots_since(conn, since=now)
        return R.as_of(snaps["CAPITAL"], now)

    async def go():
        lg = await _cached("lol_lags", now, lags)
        cap = await _cached("lol_capital", now, capital)
        fp = eddie.get("expected_fill_probability") \
            if eddie.get("status") == "MEASURED" else None
        cand = dict(decision, status="MEASURED")
        got = SC.score(
            cand, fill_probability=fp,
            fill_basis=("EDDIE_ESTIMATE_AT_DECISION" if fp is not None
                        else None),
            fill_source="EDDIE" if fp is not None else None,
            idle_capital_usd=None if cap is None else cap[1],
            idle_capital_why=("NO_PAPER_CAPITAL_SNAPSHOT_AT_OR_BEFORE_"
                              "DECISION" if cap is None else cap[2]),
            lag_samples=lg, ctx={})
        return {"status": got.get("status"), "version": SC.VERSION,
                "opportunity_score": got.get("opportunity_score"),
                "score_basis": got.get("score_basis"), "why": got.get("why"),
                "expected_net_executable_ev_usd": got.get(
                    "expected_net_executable_ev_usd"),
                "fill_probability": got.get("fill_probability"),
                "capacity_factor": got.get("capacity_factor"),
                "capital_hours": got.get("capital_hours"),
                "unmeasured": got.get("unmeasured") or {},
                "as_of": "THE_DECISION_INSTANT"}
    return await _bounded(conn, go)


async def karen_at_decision(conn, *, slug: str, strategy: str,
                            now: float) -> dict:
    async def go():
        row = await conn.fetchrow(
            """SELECT count(*) FILTER (WHERE d.us_market_slug = $1) AS on_market,
                      count(*) FILTER (WHERE d.strategy = $2) AS on_strategy,
                      count(*) AS total_open
                 FROM karen_challenges k
                 LEFT JOIN paper_decisions d
                   ON k.target_kind = 'paper_decisions' AND d.decision_id = k.target_id
                WHERE k.state = 'OPEN'
                  AND k.challenged_at <= to_timestamp($3)""",
            slug, strategy, float(now))
        on_m, on_s = int(row["on_market"] or 0), int(row["on_strategy"] or 0)
        return {"state": ("OPEN_CHALLENGES_ON_THIS_MARKET_OR_STRATEGY"
                          if (on_m or on_s) else
                          "NO_OPEN_CHALLENGE_ON_THIS_MARKET_OR_STRATEGY"),
                "open_on_market": on_m, "open_on_strategy": on_s,
                "open_total": int(row["total_open"] or 0),
                "authority": "CHALLENGE_ONLY_ZERO_AUTHORITY",
                "basis": "karen_challenges OPEN at the decision instant; a "
                         "later challenge of this decision is linked by "
                         "decision_id, never written into the intent"}
    got = await _bounded(conn, go)
    if got.get("status") == "UNAVAILABLE":
        return {"state": "UNAVAILABLE", "why": got["why"]}
    return got


async def allie_at_decision(conn, *, decision: dict, cost_usd, p,
                            wire, now: float) -> dict:
    """Allie's rule on THIS candidate against the open book of her latest
    allocator run (same game/team counts, drawdown and regime factors)."""
    from .intel import allocator as A
    from .intel import common as C

    async def latest():
        r = await conn.fetchrow(
            """SELECT run_id, max(computed_at) AS at FROM intel_allocations
                GROUP BY run_id ORDER BY max(computed_at) DESC LIMIT 1""")
        if r is None:
            return None
        rows = await conn.fetch(
            """SELECT candidate_kind, us_market_slug, score, inputs
                 FROM intel_allocations WHERE run_id = $1""", r["run_id"])
        return {"run_id": r["run_id"], "at": r["at"].timestamp(),
                "rows": [dict(x) for x in rows]}

    async def go():
        run = await _cached("allie_latest", now, latest)
        pf, wf = C.num(p), C.num(wire)
        cost = C.num(cost_usd)
        if pf is None or wf is None or wf <= 0:
            return _un("NO_PROBABILITY_OR_PRICE_AT_DECISION")
        u = 0.10
        ev = (pf - u - wf) / wf
        cap = None if cost is None else min(
            cost * A.SLEEVE_SCALE, A.PER_POSITION_CAP * C.SLEEVE_NOTIONAL_USD)
        cand = {"candidate_id": "decision:%s" % decision["decision_id"],
                "candidate_kind": "NEW_DECISION",
                "decision_id": decision["decision_id"], "group_id": None,
                "us_market_slug": decision.get("us_market_slug"),
                "game": str(decision.get("fixture")
                            or decision.get("us_market_slug")),
                "sport": decision.get("sport") or "UNKNOWN",
                "team": "UNKNOWN", "ev": C.rnd(ev), "ev_why": None,
                "capacity_usd": C.rnd(cap),
                "calibration_uncertainty": u, "liquidity_cap_usd": None,
                "unmeasured": {"calibration_uncertainty":
                               "NO_CALIBRATION_AT_DECISION_DEFAULT_0.10"}}
        inputs = {}
        open_scores = []
        if run is not None:
            for r in run["rows"]:
                if r["candidate_kind"] == "OPEN_POSITION" and \
                        r["score"] is not None:
                    open_scores.append(float(r["score"]))
            inputs = C.jload(run["rows"][0]["inputs"]) if run["rows"] else {}
        ddf = C.num((inputs or {}).get("drawdown_factor"))
        rf = C.num((inputs or {}).get("regime_factor"))
        res = A.allocate([cand], game_open={}, actual_game_exposure={},
                         drawdown_factor=1.0 if ddf is None else ddf,
                         regime_factor=1.0 if rf is None else rf)
        c = res["ranked"][0]
        best_open = max(open_scores) if open_scores else None
        return {"status": "MEASURED", "version": A.VERSION,
                "shadow_usd": c.get("shadow_usd"),
                "shadow_weight": c.get("shadow_weight"),
                "score": c.get("score"),
                "binding_constraint": c.get("binding_constraint"),
                "reasons": c.get("reasons"),
                "best_open_position_score_latest_run": best_open,
                "latest_run_id": None if run is None else run["run_id"],
                "factors": {"drawdown": ddf, "regime": rf,
                            "defaulted": [k for k, v in (("drawdown", ddf),
                                                         ("regime", rf))
                                          if v is None]},
                "authority": "SHADOW_WEIGHTS_ONLY_CARRIED_AS_EVIDENCE",
                "basis": "INTEL_ALLOCATOR_V1 applied at the decision instant"}
    return await _bounded(conn, go)


async def at_decision(conn, *, decision: dict, book_row: dict | None,
                      cost_usd, p, wire, now: float | None = None) -> dict:
    """All four computed components for one decision (derek is built by the
    caller from its own record). Never raises."""
    at = float(now if now is not None else time.time())
    eddie = await eddie_at_decision(conn, decision=decision,
                                    book_row=book_row, now=at)
    opp = await opportunity_at_decision(conn, decision=decision, eddie=eddie,
                                        now=at)
    karen = await karen_at_decision(conn, slug=decision.get("us_market_slug"),
                                    strategy=decision.get("strategy"), now=at)
    allie = await allie_at_decision(conn, decision=decision,
                                    cost_usd=cost_usd, p=p, wire=wire, now=at)
    return {"eddie": eddie, "opportunity_score": opp, "karen": karen,
            "allie": allie, "version": VERSION}
