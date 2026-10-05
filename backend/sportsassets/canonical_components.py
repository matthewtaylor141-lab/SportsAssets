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
  settlement_exception_risk
                     (R30C) the expected cost of the exceptional settlement
                     states -- postponement / suspension settled at a price,
                     a declared void, a tie the book does not price -- against
                     the completed-game assumption, from the MEASURED
                     exception-risk table (settlement_exception_risk). Carried
                     on the intent's EVIDENCE; shadow information, never a
                     gate

Each component is MEASURED or carries an explicit UNAVAILABLE reason --
never a manufactured value. Components are evidence on the intent; none of
them changes the paper decision, its order or the live proposal (both
adapters read the same intent, so they see the same components).

R30C · EXECUTION EVIDENCE. Every execution figure a component carries says
which class it was fitted on (execution_evidence): Eddie's fill probability,
markout and time to fill are the paper simulator's (PAPER_SIMULATION), with
their class interval and a WIDER live interval; the Opportunity Score's
P(fill) and Allie's executable net say the same; ACTUAL is UNMEASURED with
its reason while SMALL LIVE is SHADOW. Simulated fills are never presented
as live execution quality.

R30C · OPPORTUNITY SCORE V2 (opportunity_score_v2) is computed here at the
same instant from the same inputs and returned as `opportunity_score_v2` --
NOT a component of the intent: live_parity records it beside the intent in
the shadow tournament (migration 300) and nothing reads it to decide.

Bounded: every read runs in its own savepoint under a short timeout, and the
slow-moving inputs (Eddie's fill history, settlement lags, the capital
snapshot, Allie's latest run) are cached for CACHE_S.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from . import execution_evidence as EE

log = logging.getLogger(__name__)

VERSION = "CANONICAL_COMPONENTS_V1"
CACHE_S = 300.0
COMPONENT_TIMEOUT_S = 2.0
#: how far back V2's exceptional-settlement counts look (R30C)
EXCEPTIONAL_LOOKBACK_DAYS = 60
#: the settlement outcomes V2 counts as EXCEPTIONAL (the edge is forfeited
#: or replaced): a void refund, and the venue's own published settlement
#: price (migration 184, e.g. $0.50 per contract on an NFL tie)
EXCEPTIONAL_OUTCOMES = ("VOID_REFUND", "SETTLED_AT_VENUE_PRICE")
SETTLED_OUTCOMES = ("WON", "LOST") + EXCEPTIONAL_OUTCOMES
#: why Allie's fixture haircut is not a measurement for this decision
R_NO_FIXTURE = ("NO_FIXTURE_ON_THE_DECISION: the open groups on its fixture "
                "cannot be counted, so the correlation haircut 0 is not a "
                "measured absence of correlation")
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
            "max_executable_qty": est.get("max_executable_qty"),
            "unmeasured": est.get("unmeasured") or {},
            # R30C: what the fill probability, markout and time to fill were
            # fitted on (PAPER_SIMULATION today), their LIVE (widened)
            # intervals, and why ACTUAL is unmeasured -- so the LIVE adapter
            # reading this intent can never take a paper fill rate for the
            # venue's (execution_evidence)
            "execution_evidence": est.get("execution_evidence") or {
                "fitted_on": "UNMEASURED",
                "why": "THE_ESTIMATE_CARRIES_NO_EXECUTION_EVIDENCE"},
            # R30C review: the point EV / net edge / fill probability above
            # are the paper simulator's; their LIVE interval (wider, by the
            # declared transfer penalty and floor) travels beside them
            "live_interval": _eddie_live_interval(est),
            "authority": "SHADOW_ONLY_CARRIED_AS_EVIDENCE"}


def _eddie_live_interval(est: dict) -> dict:
    """The LIVE interval of Eddie's EV, net executable edge and fill
    probability (execution_evidence.live_executable_bounds, computed by
    agents/eddie.estimate), or UNAVAILABLE with its reason."""
    ev = est.get("execution_evidence") or {}
    li = ev.get("live_interval") or {}
    fp = ev.get("fill_probability") or {}
    if li.get("status") != "MEASURED":
        return {"status": "UNAVAILABLE", "fitted_on": ev.get("fitted_on"),
                "why": li.get("why") or "THE_ESTIMATE_CARRIES_NO_LIVE_"
                                        "INTERVAL",
                "expected_fill_probability": {
                    "low": fp.get("live_ci_low"),
                    "high": fp.get("live_ci_high")}}
    return {"status": "MEASURED", "fitted_on": li.get("fitted_on"),
            "live_use": li.get("live_use"),
            "is_proof_of_live_execution": bool(
                li.get("is_proof_of_live_execution")),
            "expected_executable_ev_usd": li.get(
                "expected_executable_ev_usd"),
            "expected_net_executable_edge_pp": li.get(
                "expected_net_executable_edge_pp"),
            "expected_fill_probability": {"low": fp.get("live_ci_low"),
                                          "high": fp.get("live_ci_high")}}


async def eddie_estimate_at_decision(conn, *, decision: dict,
                                     book_row: dict | None,
                                     now: float) -> dict:
    """Eddie's RAW estimate at the decision instant (agents/eddie.estimate on
    his cached recorded history), or {status: UNAVAILABLE, why}."""
    from .agents import eddie as E

    async def hist():
        return await E.history_stats(conn, now=now)

    async def go():
        h = await _cached("eddie_history", now, hist)
        return E.estimate(decision, book_row, h, now=now)
    return await _bounded(conn, go)


async def eddie_at_decision(conn, *, decision: dict, book_row: dict | None,
                            now: float) -> dict:
    est = await eddie_estimate_at_decision(conn, decision=decision,
                                           book_row=book_row, now=now)
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
                # R30C: the P(fill) in the score is Eddie's, fitted on the
                # paper simulator -- said here, beside the number
                "fill_probability_evidence": (
                    (eddie.get("execution_evidence") or {}).get("fitted_on")
                    if fp is not None else None),
                "fill_probability_live_use": (
                    (eddie.get("execution_evidence") or {}).get("live_use")
                    if fp is not None else None),
                # R30C review: the WIDER live uncertainty of that P(fill)
                # beside the point; V1 itself is a point score -- its
                # uncertainty-adjusted counterpart is V2 (a lower bound),
                # recorded in the shadow tournament, never on the intent
                "fill_probability_live_interval": (
                    ((eddie.get("live_interval") or {}).get(
                        "expected_fill_probability"))
                    if fp is not None else None),
                "score_live_interval": {
                    "status": "UNAVAILABLE",
                    "why": ("V1_IS_A_POINT_SCORE: its P(fill) live interval "
                            "is beside it; the uncertainty-adjusted live "
                            "counterpart is Opportunity Score V2 (lower "
                            "confidence bound), shadow tournament only")},
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
    decision's own inputs, Eddie's executable EV and depth, the recorded
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
        # R30C review: with no fixture on the decision the open groups on it
        # cannot be counted -- Allie's arithmetic still sees 0 (her module
        # is unchanged), but the haircut is labelled UNMEASURED below so no
        # reader (V2 included) takes that 0 for a measured "no correlation"
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
        out = AC.allocate(
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
        # R30C: Allie's executable net is Eddie's EV (its fill probability
        # fitted on the paper simulator) and her capacity ceiling is a walk
        # of the displayed book -- neither is live execution evidence
        # (the decision's modelled net, her labelled fallback, is a walk of
        # the displayed book too)
        ev_cls = (e.get("execution_evidence") or {}).get("fitted_on")
        from_eddie = str(out.get("net_basis") or "").startswith("EDDIE")
        net_cls = (ev_cls if from_eddie else EE.NO_FILL_EVIDENCE
                   if out.get("net_basis") else None)
        eli = e.get("live_interval") or {}
        if from_eddie and eli.get("status") == "MEASURED":
            net_live = dict(eli.get("expected_executable_ev_usd") or {},
                            status="MEASURED", fitted_on=net_cls,
                            basis="Eddie's EV at its LIVE interval")
        else:
            net_live = {"status": "UNAVAILABLE", "fitted_on": net_cls,
                        "why": (eli.get("why") or "EDDIE_LIVE_INTERVAL_"
                                "UNAVAILABLE") if from_eddie else (
                            "MODELLED_NET_IS_A_WALK_OF_THE_DISPLAYED_BOOK: "
                            "fitted on no fill, so no fill model exists to "
                            "widen it; it is not live execution evidence"
                            if out.get("net_basis") else
                            "NO_EXECUTABLE_NET")}
        out["execution_evidence"] = {
            "executable_net_fitted_on": net_cls,
            "executable_net_live_interval_usd": net_live,
            "capacity_fitted_on": (
                EE.NO_FILL_EVIDENCE if out.get("capacity_basis") else None),
            "live_use": EE.LIVE_USE.get(net_cls) if net_cls else None,
            # her `confidence` grades input COMPLETENESS; it says nothing
            # about live execution, which no ACTUAL fill has measured
            "confidence_scope": "INPUT_COMPLETENESS_ONLY",
            "live_execution_confidence": (
                "NOT_ESTABLISHED_NO_ACTUAL_CANONICAL_FILL"),
            "actual": {"status": EE.UNMEASURED, "why": EE.R_NO_ACTUAL}}
        if not fx:
            cc = dict(out.get("correlation_concentration") or {})
            cc.update(haircut_status="UNMEASURED",
                      haircut_why=R_NO_FIXTURE)
            out["correlation_concentration"] = cc
            out["unmeasured"] = dict(out.get("unmeasured") or {},
                                     correlation_haircut=R_NO_FIXTURE)
        return out
    return await _bounded(conn, go)


async def settlement_exception_at_decision(conn, *, decision: dict,
                                           contract: dict | None,
                                           p, now: float) -> dict:
    """THE EXPECTED SETTLEMENT-EXCEPTION COST OF THIS DECISION (R30C,
    settlement_exception_risk.decision_cost) against the completed-game
    assumption: the measured exception-risk table (cached CACHE_S; the venue's
    own settlement records) at the decision's sport / league / market, the
    decision's own venue rules text for the payouts, the held side's
    probability, price and quantity. MEASURED, PRIOR_BOUNDED (named events at
    the conservative prior) or UNAVAILABLE with the reason.

    SHADOW EVIDENCE ONLY: it is recorded on the intent's evidence and gates
    nothing -- the ENTER / REFUSE rule, the size and every cap are unchanged
    (R30C; Eddie and Allie read it in R30B)."""
    from . import settlement_exception_risk as SER
    c = contract if isinstance(contract, dict) else {}

    async def table():
        return await SER.measure(conn, now=now)

    async def go():
        t = await _cached("settlement_exception_table", now, table)
        if not t.get("ok"):
            return _un("%s:%s" % (SER.R_NO_TABLE, t.get("refusal")),
                       version=SER.VERSION, authority=SER.AUTHORITY,
                       gates_the_decision=False)
        scmp = c.get("settlement_comparison")
        if isinstance(scmp, str):
            import json as _json
            try:
                scmp = _json.loads(scmp)
            except ValueError:
                scmp = None
        if not isinstance(scmp, dict) and c.get("valuation_id") is not None:
            # the decision's candidate carries the valuation id, not its
            # rules text: read the venue's own words off the valuation row
            # the decision was made on (the collector persisted them there)
            txt = await conn.fetchval(
                "SELECT settlement_comparison->>'venue_rules_text' "
                "  FROM external_valuations WHERE id = $1",
                int(c["valuation_id"]))
            scmp = {"venue_rules_text": txt}
        e = (decision.get("economics") or {}).get("acquisition") or {}
        price = e.get("vwap") if e.get("vwap") is not None else \
            decision.get("limit_price")
        slug = decision.get("us_market_slug") or c.get("us_market_slug")
        return SER.decision_cost(
            t, sport_family=decision.get("sport") or c.get("sport_family"),
            league=SER.league_of(slug), market=c.get("market"),
            holding_side=decision.get("holding_side"), p=p, price=price,
            qty=decision.get("proposed_qty"),
            venue_rules_text=(scmp or {}).get("venue_rules_text"),
            conditional_net_usd=decision.get(
                "executable_opportunity_dollars"))
    return await _bounded(conn, go)


async def exceptional_at_decision(conn, *, sport, now: float) -> dict:
    """THE EXCEPTIONAL-SETTLEMENT BOUND V2 reads: EXCEPTIONAL_OUTCOMES
    (VOID_REFUND and SETTLED_AT_VENUE_PRICE) counts of settled markets
    (SETTLED_OUTCOMES, recorded at or before the decision, last
    EXCEPTIONAL_LOOKBACK_DAYS) by sport over the canonical INVESTMENT path
    (INVESTMENT intent -> its paper ENTRY order -> the group's settlement),
    and pooled over every paper settlement (any sleeve, labelled so).
    Cached; bounded."""
    from . import opportunity_score_v2 as V2

    async def counts():
        by = {}
        if await conn.fetchval(
                "SELECT to_regclass('canonical_decision_intents') IS NOT NULL"):
            for r in await conn.fetch(
                    """SELECT i.contract->>'sport_family' AS sport,
                              count(DISTINCT s.us_market_slug) FILTER (
                                  WHERE s.outcome = ANY($3::text[])) AS k,
                              count(DISTINCT s.us_market_slug) AS n
                         FROM canonical_decision_intents i
                         JOIN paper_orders o
                           ON o.idempotency_key = i.decision_id || ':ENTRY'
                         JOIN paper_settlements s ON s.group_id = o.group_id
                        WHERE i.sleeve = 'INVESTMENT'
                          AND s.outcome = ANY($4::text[])
                          AND s.recorded_at <= to_timestamp($1)
                          AND s.recorded_at >= to_timestamp($1)
                                               - make_interval(days => $2)
                        GROUP BY 1""", float(now),
                    int(EXCEPTIONAL_LOOKBACK_DAYS),
                    list(EXCEPTIONAL_OUTCOMES), list(SETTLED_OUTCOMES)):
                if r["sport"]:
                    by[str(r["sport"]).lower()] = {"k": int(r["k"]),
                                                   "n": int(r["n"])}
        p = await conn.fetchrow(
            """SELECT count(DISTINCT us_market_slug) FILTER (
                          WHERE outcome = ANY($3::text[])) AS k,
                      count(DISTINCT us_market_slug) AS n
                 FROM paper_settlements
                WHERE outcome = ANY($4::text[])
                  AND recorded_at <= to_timestamp($1)
                  AND recorded_at >= to_timestamp($1)
                                     - make_interval(days => $2)""",
            float(now), int(EXCEPTIONAL_LOOKBACK_DAYS),
            list(EXCEPTIONAL_OUTCOMES), list(SETTLED_OUTCOMES))
        return {"by_sport": by, "pooled": {"k": int(p["k"] or 0),
                                           "n": int(p["n"] or 0)}}

    async def go():
        c = await _cached("v2_exceptional", now, counts)
        return V2.exceptional_from_counts(
            scope_counts=c["by_sport"],
            scope_key=None if not sport else str(sport).lower(),
            pooled=c["pooled"])
    got = await _bounded(conn, go)
    if got.get("status") == "UNAVAILABLE":
        return {"rate_ucb": None, "why": got.get("why")}
    return got


async def opportunity_v2_at_decision(conn, *, decision: dict,
                                     eddie_est: dict, allie: dict,
                                     now: float) -> dict:
    """OPPORTUNITY SCORE V2 (opportunity_score_v2) at the decision
    instant, from the same inputs V1 and Allie saw. SHADOW, NO AUTHORITY:
    it is recorded in the tournament (migration 300), never on the intent,
    and nothing reads it to decide."""
    from . import opportunity_score_v2 as V2
    ex = await exceptional_at_decision(conn, sport=decision.get("sport"),
                                       now=now)
    try:
        return V2.score(V2.inputs_from(eddie_est, allie, ex))
    except Exception as exc:                                  # noqa: BLE001
        log.debug("opportunity v2 failed", exc_info=True)
        return {"status": "UNAVAILABLE", "version": V2.VERSION,
                "spec_sha": V2.SPEC_SHA, "opportunity_score": None,
                "why": "V2_FAILED:%s" % type(exc).__name__}


async def at_decision(conn, *, decision: dict, book_row: dict | None,
                      cost_usd, p, wire, now: float | None = None,
                      contract: dict | None = None) -> dict:
    """All computed components for one decision (derek is built by the
    caller from its own record). `contract` is the valuation row the
    decision was made on (its market type and venue rules text feed the
    settlement-exception cost). Never raises."""
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
    est = await eddie_estimate_at_decision(conn, decision=decision,
                                           book_row=book_row, now=at)
    eddie = (eddie_component(None, est["why"])
             if est.get("status") == "UNAVAILABLE" else eddie_component(est))
    opp = await opportunity_at_decision(conn, decision=decision, eddie=eddie,
                                        now=at)
    karen = await karen_at_decision(conn, slug=decision.get("us_market_slug"),
                                    strategy=decision.get("strategy"), now=at)
    allie = await allie_at_decision(conn, decision=decision, eddie=eddie,
                                    now=at)
    exc = await settlement_exception_at_decision(
        conn, decision=decision, contract=contract, p=p, now=at)
    # R30C: Opportunity Score V2 (shadow tournament only -- the caller
    # records it beside the intent, never in it)
    v2 = await opportunity_v2_at_decision(conn, decision=decision,
                                          eddie_est=est, allie=allie, now=at)
    return {"eddie": eddie, "opportunity_score": opp, "karen": karen,
            "allie": allie, "settlement_exception_risk": exc,
            "opportunity_score_v2": v2, "version": VERSION}
