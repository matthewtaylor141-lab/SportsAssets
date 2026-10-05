"""THE AGENT COMPONENTS A CANONICAL DECISION INTENT CARRIES (R30).

Every qualified ENTER decision's canonical intent (live_parity) carries, AS OF
THE DECISION INSTANT, from the decision's own inputs:

  derek              the verdict, policy decision and economics that made it
  eddie              Archer's executable-EV estimate (agents/archer.estimate --
                     the same pure estimator his runner uses), with the hard
                     rule applied
  opportunity_score  LOL_OPPORTUNITY_SCORE_V1 (lost_opportunity/score.score --
                     the same pure scorer) on the decision's executable EV and
                     capacity, Archer's fill probability, the latest PAPER idle
                     capital snapshot and the recorded settlement lags
  allie              the Chief Allocator's rule (intel/allocator.allocate --
                     the same pure greedy allocation) applied to this
                     candidate against the open book of her latest run
  karen              Karen's review state of this market and strategy: open
                     challenges at the decision instant

THE `eddie` KEY KEEPS ITS STORAGE NAME (migration 266 renamed the agent
EDDIE -> ARCHER): it is the canonical_decision_intents.eddie column and part
of every recorded intent's content hash and of the live-parity record, so a
rename here would make yesterday's intents unreadable to today's comparison.
Its labels (fill_source "EDDIE", "EDDIE_ESTIMATE_AT_DECISION") are kept for
the same reason; they name the same execution estimator, now Archer's.

Each component is MEASURED or carries an explicit UNAVAILABLE reason --
never a manufactured value. Components are evidence on the intent; none of
them changes the paper decision, its order or the live proposal (both
adapters read the same intent, so they see the same components).

Bounded: every read runs in its own savepoint under a short timeout, and the
slow-moving inputs (Archer's fill history, settlement lags, the capital
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
    from .agents import archer as E
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
            "max_executable_qty": est.get("max_executable_qty"),
            "unmeasured": est.get("unmeasured") or {},
            "authority": "SHADOW_ONLY_CARRIED_AS_EVIDENCE"}


async def eddie_at_decision(conn, *, decision: dict, book_row: dict | None,
                            now: float) -> dict:
    from .agents import archer as E

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


async def allie_at_decision(conn, *, decision: dict, eddie: dict,
                            now: float) -> dict:
    """ALLIE'S CAPITAL-EFFICIENCY ALLOCATION (allie_capital.allocate) from the
    decision's own inputs, Archer's executable EV and depth, the recorded
    settlement lags, the paper book's open exposure on the fixture, idle
    capital, recent INVESTMENT candidates (the hurdle) and the rails."""
    from . import allie_capital as AC
    from .lost_opportunity import reads as R
    from .profitability import economics as EC

    async def lags():
        return await R.lag_samples(conn, now=now)

    async def capital():
        snaps = await R.snapshots_since(conn, since=now)
        return R.as_of(snaps["CAPITAL"], now)

    async def hurdle():
        rows = await conn.fetch(
            """SELECT (allie->'correlation_concentration'->>
                       'adjusted_profit_per_capital_hour')::float8 AS a
                 FROM canonical_decision_intents
                WHERE sleeve = 'INVESTMENT' AND allie->>'status' = 'MEASURED'
                  AND created_at > to_timestamp($1) - interval '7 days'
                ORDER BY created_at DESC LIMIT 50""", float(now))
        return [r["a"] for r in rows if r["a"] is not None]

    async def go():
        lg = await _cached("lol_lags", now, lags)
        lag, lag_n = EC.settlement_lag(lg or [], as_of=now)
        cap_snap = await _cached("lol_capital", now, capital)
        hs = await _cached("allie_hurdle", now, hurdle)
        fx = decision.get("fixture")
        fixture = await conn.fetchrow(
            """SELECT count(DISTINCT o.group_id) AS n,
                      coalesce(sum(o.filled_qty * o.limit_price), 0) AS usd
                 FROM paper_orders o
                WHERE o.role = 'ENTRY' AND o.fixture = $1 AND o.filled_qty > 0
                  AND NOT EXISTS (SELECT 1 FROM paper_settlements s
                                   WHERE s.group_id = o.group_id)""",
            fx) if fx else None
        book = await conn.fetchval(
            """SELECT coalesce(sum(o.filled_qty * o.limit_price), 0)
                 FROM paper_orders o
                WHERE o.role = 'ENTRY' AND o.filled_qty > 0
                  AND NOT EXISTS (SELECT 1 FROM paper_settlements s
                                   WHERE s.group_id = o.group_id)""")
        em = await conn.fetchrow(
            "SELECT scale, max_order_usd FROM execmirror_control LIMIT 1")
        e = eddie if eddie.get("status") == "MEASURED" else {}
        return AC.allocate(
            eddie_ev_usd=e.get("expected_executable_ev_usd"),
            modelled_net_usd=decision.get("executable_opportunity_dollars"),
            capital_required_usd=decision.get("capital_required_usd"),
            event_start_at=decision.get("event_start_at"),
            decided_at=decision.get("decided_at"), median_lag_s=lag,
            lag_n=lag_n, eddie_max_qty=e.get("max_executable_qty"),
            limit_price=decision.get("limit_price"),
            displayed_depth_qty=decision.get("depth_within_limit"),
            fixture_open_groups=0 if fixture is None else fixture["n"],
            fixture_open_usd=0 if fixture is None else float(fixture["usd"]),
            book_open_usd=float(book or 0),
            idle_capital_usd=None if cap_snap is None else cap_snap[1],
            recent_adjusted_ppch=hs,
            paper_rail_usd=decision.get("per_order_cap_usd"),
            live_rail_usd=None if em is None else float(em["max_order_usd"]),
            live_scale=None if em is None else float(em["scale"]),
            order_cost_usd=decision.get("capital_required_usd"))
    return await _bounded(conn, go)


async def at_decision(conn, *, decision: dict, book_row: dict | None,
                      cost_usd, p, wire, now: float | None = None) -> dict:
    """All four computed components for one decision (derek is built by the
    caller from its own record). Never raises."""
    at = float(now if now is not None else time.time())
    if decision.get("event_start_at") is None and decision.get("us_market_slug"):
        # the event start the opportunity score and Allie's time to capital
        # release use: the pre-map's latest mapped game start for the market
        from .lost_opportunity import reads as R

        async def start():
            got = await R.event_starts(conn, [decision["us_market_slug"]])
            return {"t": got.get(decision["us_market_slug"])}
        st = await _bounded(conn, start)
        if st.get("t") is not None:
            decision = dict(decision, event_start_at=st["t"],
                            event_start_basis="us_premap.game_start")
    eddie = await eddie_at_decision(conn, decision=decision,
                                    book_row=book_row, now=at)
    opp = await opportunity_at_decision(conn, decision=decision, eddie=eddie,
                                        now=at)
    karen = await karen_at_decision(conn, slug=decision.get("us_market_slug"),
                                    strategy=decision.get("strategy"), now=at)
    allie = await allie_at_decision(conn, decision=decision, eddie=eddie,
                                    now=at)
    return {"eddie": eddie, "opportunity_score": opp, "karen": karen,
            "allie": allie, "version": VERSION}
