"""THE READ-ONLY SHADOW DATA CONTRACT FOR COMMAND.

Owner directive 2026-09-19 (COMMAND IS P0): get COMMAND live against
the REAL shadow ledger while the prospective dataset starts
accumulating. "If there are zero decisions: SHOW ZERO. If listening has
started: SHOW LISTENING. If an engine is blocked: SHOW THE BLOCKER. If
data is stale: SHOW STALE. If a metric is unknown: SHOW NOT IDENTIFIED."

WHAT THIS MODULE IS ALLOWED TO DO: select. Nothing here writes, and
nothing here can place, cancel or price a real order -- there is no
venue client in its import graph at all. A test reads this file's own
source and fails the build if a mutating statement appears.

A FAILED RETRIEVAL IS NOT AN EMPTY LEDGER. Every path that cannot
produce real records raises RetrievalIncomplete, and the routes turn
that into 503. COMMAND then shows FEED UNAVAILABLE. It must never show
a well-formed page of zeros during an outage: a zero is a claim about
the world, and "we could not read" is a different claim.

ZERO IS NOT A FAILURE EITHER. An empty prospective ledger on day one is
the correct, honest answer, and it is returned as a real zero with
`listening` true beside it -- which is why the two cases above have to
be distinguishable in the payload rather than in the reader's head.

NO COMBINED P&L. RN1_SHADOW is a mechanism benchmark and
BETTOR_EV_SHADOW is an intelligence claim; every total is per lane and
`combinedPnl` is null with the reason attached. One headline number
would support neither claim.

NO SIMULATED NUMBER IS EVER ADDED TO AN OBSERVED ONE. Economics come
back keyed by execution class, never pre-summed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .. import shadow as sh
from .. import shadow_lanes as lanes
from .. import shadow_policy as pol
from .command_snapshot import RetrievalIncomplete

log = logging.getLogger(__name__)

DISCLOSURE = sh.DISCLOSURE
NOT_IDENTIFIED = "NOT_IDENTIFIED"

# How old the newest source row may be before COMMAND says STALE. This
# is a statement about the SOURCE, not about the browser: a page that
# polls every two seconds against a ledger that stopped an hour ago is
# fresh in the browser and stale in every sense that matters.
STALE_AFTER_S = 900

MAX_ROWS = 200


def _f(v):
    """A finite float, or None. Never a silent zero."""
    if v is None:
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) \
        else None


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else (v or None)


def _js(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


async def _guard(pool, what: str, fn, *args):
    try:
        return await fn(*args)
    except Exception as exc:                                   # noqa: BLE001
        raise RetrievalIncomplete(what, type(exc).__name__) from exc


# ── the environment header every screen carries ──────────────────────


def environment() -> dict:
    """The safety labels, produced server-side so a screenshot cannot be
    taken of a page that lost them. They are not decoration: an
    investor-facing image of simulated performance that does not say so
    is the single most misleading artifact this system could produce."""
    return {
        "shadowMode": sh.SHADOW_MODE,
        "realOrderSubmissionEnabled": sh.REAL_ORDER_SUBMISSION_ENABLED,
        "capitalAtRisk": sh.CAPITAL_AT_RISK,
        "mirrorLive": False,
        "realOrderActivity": "NONE",
        "disclosure": DISCLOSURE,
        "disclosureLines": ["SHADOW TRADING", "NO REAL CAPITAL",
                            "COUNTERFACTUAL / SIMULATED EXECUTION"],
        "policyVersion": pol.RN1_SHADOW_POLICY_VERSION,
        "policySha": pol.POLICY_SHA,
        "lanes": list(lanes.LANES),
        # THE HIERARCHY, STATED BY THE SERVER RATHER THAN BY THE CSS.
        # Owner clarification 2026-09-19: "BETTOR EV ENGINE IS THE
        # PRIMARY PRODUCT. RN1_SHADOW is a secondary benchmark/research
        # lane." A front end can reorder cards; only the payload can
        # make the ordering a fact every reader of this API inherits,
        # including a PDF or a future client nobody has written yet.
        "identity": "BETTOR EV ENGINE",
        "primaryLane": lanes.BETTOR_EV_SHADOW,
        "benchmarkLane": lanes.RN1_SHADOW,
        "laneOrder": [lanes.BETTOR_EV_SHADOW, lanes.RN1_SHADOW],
        "laneRoles": {
            lanes.BETTOR_EV_SHADOW: "PRIMARY — INDEPENDENT INTELLIGENCE",
            lanes.RN1_SHADOW: "BENCHMARK — EXTERNAL MECHANISM RESEARCH",
        },
        "hybridLane": {"name": "RN1_PLUS_BETTOR",
                       "state": "NOT_YET_ACTIVE",
                       "why": ("a future hybrid challenger; it does not "
                               "exist and no row is attributed to it")},
    }


# ── summary ──────────────────────────────────────────────────────────

_COUNTS_SQL = """
    SELECT
      (SELECT count(*) FROM rn1_observations
        WHERE record_kind = 'OBSERVATION')              AS observations,
      (SELECT count(*) FROM rn1_observations
        WHERE record_kind <> 'OBSERVATION')             AS corrections,
      (SELECT max(bettor_received_ts) FROM rn1_observations) AS last_obs,
      (SELECT count(*) FROM shadow_decisions
        WHERE lane = 'RN1_SHADOW')                      AS rn1_decisions,
      (SELECT count(*) FROM shadow_decisions
        WHERE lane = 'BETTOR_EV_SHADOW')                AS bettor_decisions,
      (SELECT count(*) FROM shadow_decisions
        WHERE lane = 'RN1_SHADOW'
          AND proposed_action = 'NO_TRADE')             AS rn1_no_trade,
      (SELECT max(decision_ts) FROM shadow_decisions)   AS last_decision,
      (SELECT count(*) FROM shadow_positions)           AS positions,
      (SELECT count(*) FROM shadow_executions)          AS executions,
      (SELECT count(*) FROM shadow_policy_versions)     AS policies
"""

_LATENCY_SQL = """
    SELECT avg(EXTRACT(EPOCH FROM (d.decision_ts - d.bettor_received_ts))
               * 1000)                                  AS decide_ms,
           avg(EXTRACT(EPOCH FROM (d.bettor_received_ts - d.venue_source_ts))
               * 1000)                                  AS observe_ms,
           count(*)                                     AS n
      FROM shadow_decisions d
     WHERE d.lane = 'RN1_SHADOW'
       AND d.bettor_received_ts IS NOT NULL
"""

_ECON_SQL = """
    SELECT e.execution_class,
           count(*)                       AS n,
           avg(e.slippage)                AS slippage,
           avg(e.spread_cost)             AS spread_cost,
           sum(e.shadow_filled_qty)       AS filled_qty,
           avg(e.total_to_arrival_ms)     AS total_ms
      FROM shadow_executions e
     GROUP BY e.execution_class
"""

_BLOCKER_SQL = """
    SELECT jsonb_array_elements(d.blockers) ->> 'code' AS code,
           count(*) AS n
      FROM shadow_decisions d
     WHERE d.lane = 'RN1_SHADOW'
       AND jsonb_array_length(d.blockers) > 0
     GROUP BY 1
     ORDER BY 2 DESC
     LIMIT 12
"""


_BETTOR_SQL = """
    SELECT
      (SELECT count(*) FROM bettor_opportunities)        AS opportunities,
      (SELECT max(observed_at) FROM bettor_opportunities) AS last_seen,
      (SELECT count(DISTINCT symbol) FROM bettor_opportunities)
                                                        AS markets,
      (SELECT count(*) FROM shadow_decisions
        WHERE lane = 'BETTOR_EV_SHADOW'
          AND proposed_action = 'NO_TRADE')             AS no_trades,
      (SELECT count(*) FROM shadow_decisions
        WHERE lane = 'BETTOR_EV_SHADOW'
          AND proposed_action <> 'NO_TRADE')            AS trades
"""

_BETTOR_BLOCKER_SQL = """
    SELECT jsonb_array_elements(d.blockers) ->> 'code' AS code,
           count(*) AS n
      FROM shadow_decisions d
     WHERE d.lane = 'BETTOR_EV_SHADOW'
       AND jsonb_array_length(d.blockers) > 0
     GROUP BY 1
     ORDER BY 2 DESC
     LIMIT 12
"""


async def _bettor_counts(pool) -> dict:
    """BETTOR's own collection, or a NAMED reason it could not be read.

    This one does NOT raise into the page. The primary lane's table
    arrives with migration 071, and on a deployment that has not applied
    it yet the honest render is a named blocker on an otherwise working
    screen -- not FEED UNAVAILABLE across the whole management view,
    which would misreport a staged rollout as an outage.
    """
    try:
        row = await pool.fetchrow(_BETTOR_SQL)
        blockers = await pool.fetch(_BETTOR_BLOCKER_SQL)
    except Exception as exc:                                   # noqa: BLE001
        return {"state": "STORE_NOT_READY",
                "why": "%s — migration 071 may not have applied yet"
                       % type(exc).__name__,
                "opportunitiesObserved": None, "marketsObserved": None,
                "noTrades": None, "trades": None, "lastObservedAt": None,
                "blockers": []}
    return {"state": "COLLECTING",
            "opportunitiesObserved": int(row["opportunities"]),
            "marketsObserved": int(row["markets"]),
            "noTrades": int(row["no_trades"]),
            "trades": int(row["trades"]),
            "lastObservedAt": _iso(row["last_seen"]),
            "blockers": [{"code": b["code"], "count": int(b["n"])}
                         for b in blockers]}


async def summary(pool) -> dict:
    counts = await _guard(pool, "SHADOW_COUNTS_UNREAD", pool.fetchrow,
                          _COUNTS_SQL)
    bettor = await _bettor_counts(pool)
    lat = await _guard(pool, "SHADOW_LATENCY_UNREAD", pool.fetchrow,
                       _LATENCY_SQL)
    econ = await _guard(pool, "SHADOW_ECONOMICS_UNREAD", pool.fetch, _ECON_SQL)
    blockers = await _guard(pool, "SHADOW_BLOCKERS_UNREAD", pool.fetch,
                            _BLOCKER_SQL)

    last_source = max([t for t in (counts["last_obs"], counts["last_decision"])
                       if t is not None], default=None)
    now = datetime.now(tz=timezone.utc)
    age_s = ((now - last_source).total_seconds()
             if isinstance(last_source, datetime) else None)

    by_class = {}
    for r in econ:
        by_class[r["execution_class"]] = {
            "n": int(r["n"]),
            "avgSlippage": _f(r["slippage"]),
            "avgSpreadCost": _f(r["spread_cost"]),
            "shadowFilledQty": _f(r["filled_qty"]),
            "avgTotalLatencyMs": _f(r["total_ms"]),
            # THE REALIZABILITY FLAG TRAVELS WITH THE NUMBER. Only
            # ACTUAL_FILL is realizable, and there are none while no
            # real order exists -- so every figure here is explicitly
            # counterfactual rather than implicitly so.
            "realizable": r["execution_class"] in sh.REALIZABLE_CLASSES,
        }

    rn1_n = int(counts["rn1_decisions"])
    return {
        "environment": environment(),
        "generatedAt": now.isoformat(),
        "lastSourceTimestamp": _iso(last_source),
        "sourceAgeSeconds": age_s,
        # STALENESS IS ABOUT THE SOURCE, never about the poll. A browser
        # heartbeat is not freshness.
        "sourceState": ("NO_DATA_YET" if last_source is None
                        else "STALE" if age_s and age_s > STALE_AFTER_S
                        else "LIVE"),
        "kpi": {
            "rn1SignalsObserved": int(counts["observations"]),
            "observationCorrections": int(counts["corrections"]),
            "rn1ShadowDecisions": rn1_n,
            "bettorEvDecisions": int(counts["bettor_decisions"]),
            "shadowPositions": int(counts["positions"]),
            "shadowExecutions": int(counts["executions"]),
            # NOT_IDENTIFIED, not zero: no position has been opened, so
            # there is no capital figure to state -- and a 0 here would
            # read as "we deployed nothing and that is the measurement".
            "shadowCapitalEmployed": NOT_IDENTIFIED,
            "shadowPnl": NOT_IDENTIFIED,
            "settledShadowPnl": NOT_IDENTIFIED,
            "unrealizedShadowPnl": NOT_IDENTIFIED,
            "pairCompletions": 0,
            "noTradeRate": (int(counts["rn1_no_trade"]) / rn1_n
                            if rn1_n else None),
            "averageLatencyMs": _f(lat["decide_ms"]),
            "averageObservationLatencyMs": _f(lat["observe_ms"]),
            "averageSlippage": _f(next(
                (r["slippage"] for r in econ
                 if r["execution_class"] == sh.MARKETABLE_RECONSTRUCTED),
                None)),
            "averageEntryEv": NOT_IDENTIFIED,
        },
        "byExecutionClass": by_class,
        # NEVER ONE HEADLINE NUMBER ACROSS THE TWO LANES.
        "combinedPnl": None,
        "combinedPnlNote": (
            "no combined P&L is reported: RN1_SHADOW is a mechanism "
            "benchmark and BETTOR_EV_SHADOW is an intelligence claim, "
            "and one number would support neither"),
        # BETTOR IS THE PRIMARY PRODUCT AND IS REPORTED FIRST.
        "bettor": bettor,
        "lanes": {
            lanes.BETTOR_EV_SHADOW: {
                "role": "PRIMARY — INDEPENDENT INTELLIGENCE",
                "decisions": int(counts["bettor_decisions"]),
                "opportunitiesObserved": bettor["opportunitiesObserved"],
                "trades": bettor["trades"],
                "noTrades": bettor["noTrades"],
                # LIVE / LEARNING is the state the directive names: the
                # engine is running and collecting, and has earned no
                # independent EV yet. It is not "not yet eligible" as a
                # euphemism for "nothing is happening".
                "state": ("LIVE / LEARNING"
                          if bettor["state"] == "COLLECTING"
                          else "BLOCKED"),
                "pBettor": lanes.NOT_ESTABLISHED,
                "informationEv": lanes.NOT_ESTABLISHED,
                "blockers": bettor["blockers"],
                "note": ("BETTOR's own prospective dataset: the market "
                         "state it actually had, the features it could "
                         "honestly compute, and every refusal with its "
                         "reason. RN1 is excluded from this lane."),
            },
            lanes.RN1_SHADOW: {
                "role": "BENCHMARK — EXTERNAL MECHANISM RESEARCH",
                "decisions": rn1_n,
                "state": "LIVE" if rn1_n else "LISTENING",
                "pBettor": lanes.NOT_ESTABLISHED,
                "informationEv": lanes.NOT_ESTABLISHED,
                "note": ("a mechanism benchmark: what BETTOR's own "
                         "latency, sizing and execution would have done "
                         "observing RN1. No fair value is manufactured "
                         "from RN1 activity."),
            },
        },
        "blockers": [{"code": r["code"], "count": int(r["n"])}
                     for r in blockers],
        "policiesFrozen": int(counts["policies"]),
    }


# ── decisions ────────────────────────────────────────────────────────

_DECISIONS_SQL = """
    SELECT d.*, o.rn1_source_ts, o.source_type, o.rn1_quantity,
           o.bettor_received_ts AS obs_received_ts
      FROM shadow_decisions d
      LEFT JOIN rn1_observations o
             ON o.rn1_observation_id = d.rn1_observation_id
     WHERE ($1::text IS NULL OR d.lane = $1)
     ORDER BY d.decision_ts DESC
     LIMIT $2
"""


def _decision_row(r) -> dict:
    received = r["bettor_received_ts"]
    decided = r["decision_ts"]
    decide_ms = ((decided - received).total_seconds() * 1000
                 if isinstance(received, datetime)
                 and isinstance(decided, datetime) else None)
    source = r["venue_source_ts"]
    observe_ms = ((received - source).total_seconds() * 1000
                  if isinstance(received, datetime)
                  and isinstance(source, datetime) else None)
    return {
        "shadowDecisionId": r["shadow_decision_id"],
        "lane": r["lane"],
        "symbol": r["symbol"],
        "outcomeLeg": r["outcome_leg"],
        "eventId": r["event_id"],
        "marketId": r["market_id"],
        "sport": r["sport"],
        "proposedAction": r["proposed_action"],
        "proposedSide": r["proposed_side"],
        "proposedPrice": _f(r["proposed_price"]),
        "proposedQuantity": _f(r["proposed_quantity"]),
        "rn1Price": _f(r["rn1_price"]),
        "rn1Quantity": _f(r["rn1_quantity"]),
        "priceWhenBettorObserved": _f(r["price_when_bettor_observed"]),
        "priceWhenBettorDecided": _f(r["price_when_bettor_decided"]),
        "marketBid": _f(r["market_bid"]),
        "marketAsk": _f(r["market_ask"]),
        "mid": _f(r["mid"]),
        "spread": _f(r["spread"]),
        "pMarket": _f(r["p_market"]),
        # HELD CLOSED IN THE RN1 LANE, and said so rather than shown as
        # a blank a reader could take for zero.
        "pBettor": _f(r["p_bettor"]),
        "pBettorStatus": r["p_bettor_status"] or lanes.NOT_ESTABLISHED,
        "informationEv": _f(r["information_ev"]),
        "executionEv": _f(r["execution_ev"]),
        "totalActionEv": _f(r["total_action_ev"]),
        "pFill": _f(r["p_fill"]),
        "pFillStatus": r["p_fill_status"] or lanes.NOT_ESTABLISHED,
        "reasonCodes": _js(r["reason_codes"]) or [],
        "blockers": _js(r["blockers"]) or [],
        "gateResults": _js(r["gate_results"]) or {},
        "alternatives": _js(r["alternatives"]) or [],
        "evidenceSource": r["evidence_source"],
        "signalSource": r["signal_source"],
        "modelVersion": r["model_version"],
        "policyVersion": r["policy_version"],
        "sourceType": r["source_type"],
        "venueSourceTs": _iso(r["venue_source_ts"]),
        "bettorReceivedTs": _iso(received),
        "decisionTs": _iso(decided),
        "observationLatencyMs": observe_ms,
        "decisionComputeMs": decide_ms,
        "rn1ObservationId": r["rn1_observation_id"],
        "marketStateId": r["market_state_id"],
        "shadowMode": bool(r["shadow_mode"]),
        "capitalAtRisk": _f(r["capital_at_risk"]),
    }


async def decisions(pool, *, lane=None, limit=100) -> dict:
    if lane is not None and lane not in lanes.LANES:
        raise RetrievalIncomplete("UNKNOWN_LANE", str(lane)[:40])
    rows = await _guard(pool, "SHADOW_DECISIONS_UNREAD", pool.fetch,
                        _DECISIONS_SQL, lane, min(int(limit), MAX_ROWS))
    return {"environment": environment(),
            "lane": lane or "ALL",
            "rows": [_decision_row(r) for r in rows],
            "count": len(rows),
            "emptyMeans": ("no decision has been recorded yet — this is a "
                           "real zero, not a failed read")}


# ── positions ────────────────────────────────────────────────────────

_POSITIONS_SQL = """
    SELECT p.*,
           (SELECT to_jsonb(e) FROM shadow_position_events e
             WHERE e.shadow_position_id = p.shadow_position_id
             ORDER BY e.at DESC LIMIT 1)        AS latest,
           d.model_version, d.policy_version, d.total_action_ev, d.sport
      FROM shadow_positions p
      LEFT JOIN shadow_decisions d
             ON d.shadow_decision_id = p.originating_decision_id
     WHERE ($1::text IS NULL OR p.lane = $1)
     ORDER BY p.entry_time DESC
     LIMIT $2
"""


async def positions(pool, *, lane=None, limit=100) -> dict:
    rows = await _guard(pool, "SHADOW_POSITIONS_UNREAD", pool.fetch,
                        _POSITIONS_SQL, lane, min(int(limit), MAX_ROWS))
    out = []
    for r in rows:
        latest = _js(r["latest"]) or {}
        out.append({
            "shadowPositionId": r["shadow_position_id"],
            "lane": r["lane"],
            "sport": r["sport"],
            "eventId": r["event_id"],
            "marketId": r["market_id"],
            "symbol": r["symbol"],
            # ONE ROW PER LEG. YES and NO are never netted here, because
            # netting them is the defect that cost real money on the
            # live mirror.
            "leg": r["leg"],
            "entryTime": _iso(r["entry_time"]),
            "entryPrice": _f(r["entry_price"]),
            "entryQuantity": _f(r["entry_qty"]),
            "entryEv": _f(r["entry_ev"]),
            "currentExecutableMark": _f(latest.get("executable_mark")),
            "unrealizedPnl": _f(latest.get("unrealized_pnl")),
            "realizedPnl": _f(latest.get("realized_pnl")),
            "pairStatus": latest.get("pair_status") or NOT_IDENTIFIED,
            "exitIntention": latest.get("exit_intention") or NOT_IDENTIFIED,
            "capitalHours": _f(latest.get("capital_hours")),
            "executionClass": latest.get("execution_class") or NOT_IDENTIFIED,
            "modelVersion": r["model_version"],
            "policyVersion": r["policy_version"],
            "originatingDecisionId": r["originating_decision_id"],
        })
    return {"environment": environment(), "rows": out, "count": len(out)}


# ── executions ───────────────────────────────────────────────────────

_EXECUTIONS_SQL = """
    SELECT e.*, d.symbol, d.outcome_leg, d.lane, d.proposed_action,
           d.rn1_price, d.price_when_bettor_observed,
           d.price_when_bettor_decided, d.decision_ts, d.bettor_received_ts,
           d.venue_source_ts
      FROM shadow_executions e
      JOIN shadow_decisions d
        ON d.shadow_decision_id = e.shadow_decision_id
     WHERE ($1::text IS NULL OR d.lane = $1)
     ORDER BY e.created_at DESC
     LIMIT $2
"""


async def executions(pool, *, lane=None, limit=100) -> dict:
    rows = await _guard(pool, "SHADOW_EXECUTIONS_UNREAD", pool.fetch,
                        _EXECUTIONS_SQL, lane, min(int(limit), MAX_ROWS))
    out = []
    for r in rows:
        out.append({
            "shadowExecutionId": r["shadow_execution_id"],
            "shadowDecisionId": r["shadow_decision_id"],
            "lane": r["lane"],
            "symbol": r["symbol"],
            "outcomeLeg": r["outcome_leg"],
            "proposedAction": r["proposed_action"],
            "executionClass": r["execution_class"],
            "realizable": r["execution_class"] in sh.REALIZABLE_CLASSES,
            "status": r["status"],
            "why": r["why"],
            # THE FIVE PRICES, SEPARATELY. RN1's price is never
            # substituted for ours, in the store or on the screen.
            "rn1Price": _f(r["rn1_price"]),
            "priceWhenBettorObserved": _f(r["price_when_bettor_observed"]),
            "priceWhenBettorDecided": _f(r["price_when_bettor_decided"]),
            "priceAtShadowArrival": _f(r["price_at_shadow_arrival"]),
            "shadowExecutionVwap": _f(r["vwap"]),
            # THE LATENCIES, UNBLENDED. A total appears only where every
            # component is accounted for.
            "dataLatencyMs": _f(r["data_latency_ms"]),
            "decisionComputeMs": _f(r["decision_compute_ms"]),
            "executionLatencyMs": _f(r["execution_latency_ms"]),
            "totalToArrivalMs": _f(r["total_to_arrival_ms"]),
            "latencyBasis": r["latency_basis"],
            "latencyScenarioMs": _f(r["latency_scenario_ms"]),
            "shadowFilledQty": _f(r["shadow_filled_qty"]),
            "unfilledQty": _f(r["unfilled_qty"]),
            "slippage": _f(r["slippage"]),
            "spreadCost": _f(r["spread_cost"]),
            "arrivalTs": _iso(r["arrival_ts"]),
        })
    return {"environment": environment(), "rows": out, "count": len(out)}


# ── the comparison lane ──────────────────────────────────────────────

_COMPARISON_SQL = """
    SELECT classification, count(*) AS n
      FROM shadow_disagreements
     GROUP BY classification
"""


async def comparison(pool) -> dict:
    rows = await _guard(pool, "SHADOW_COMPARISON_UNREAD", pool.fetch,
                        _COMPARISON_SQL)
    counts = {c: 0 for c in lanes.DISAGREEMENT_CLASSES}
    for r in rows:
        counts[r["classification"]] = int(r["n"])
    bettor = await _guard(pool, "SHADOW_COUNTS_UNREAD", pool.fetchval,
                          "SELECT count(*) FROM shadow_decisions "
                          "WHERE lane = 'BETTOR_EV_SHADOW'")
    return {
        "environment": environment(),
        "classes": counts,
        "bettorEligible": bool(bettor),
        "bettorState": ("LIVE" if bettor else "LEARNING / NOT YET ELIGIBLE"),
        "pBettor": lanes.NOT_ESTABLISHED,
        "note": ("BETTOR_EV_SHADOW records an honest NO_TRADE until "
                 "independent EV is earned. No trade is invented to fill "
                 "this panel."),
    }


# ── equity ───────────────────────────────────────────────────────────


async def equity(pool) -> dict:
    """The shadow equity curve, per lane, or an honest empty state.

    "Before they exist, render an elegant empty state rather than
    synthetic performance." There is nothing to plot until executions
    exist, and this returns that fact rather than a flat line at zero,
    which a chart would render as a real measured result.
    """
    n = await _guard(pool, "SHADOW_EXECUTIONS_UNREAD", pool.fetchval,
                     "SELECT count(*) FROM shadow_executions")
    if not n:
        return {"environment": environment(),
                "series": {lane: [] for lane in lanes.LANES},
                "combined": None,
                "state": "NO_EXECUTIONS_YET",
                "why": ("no shadow execution has been reconstructed yet; a "
                        "flat line at zero would be a measurement claim "
                        "nothing supports")}
    rows = await _guard(
        pool, "SHADOW_EQUITY_UNREAD", pool.fetch,
        """
        SELECT d.lane, e.execution_class,
               date_trunc('hour', e.created_at) AS bucket,
               sum(COALESCE(e.slippage, 0) * COALESCE(e.shadow_filled_qty, 0))
                   AS drag,
               count(*) AS n
          FROM shadow_executions e
          JOIN shadow_decisions d
            ON d.shadow_decision_id = e.shadow_decision_id
         GROUP BY 1, 2, 3
         ORDER BY 3 ASC
        """)
    series = {lane: [] for lane in lanes.LANES}
    for r in rows:
        series.setdefault(r["lane"], []).append({
            "at": _iso(r["bucket"]),
            "executionClass": r["execution_class"],
            "realizable": r["execution_class"] in sh.REALIZABLE_CLASSES,
            "executionDrag": _f(r["drag"]),
            "n": int(r["n"]),
        })
    return {"environment": environment(), "series": series,
            # NEVER SUMMED ACROSS LANES OR ACROSS EXECUTION CLASSES.
            "combined": None,
            "state": "PARTIAL",
            "why": ("execution drag only; settled and unrealized P&L stay "
                    "NOT_IDENTIFIED until positions and labels mature")}


# ── the trade audit ──────────────────────────────────────────────────


async def trade(pool, shadow_decision_id: str) -> dict:
    """One decision's complete lifecycle, stage by stage, with the
    elapsed milliseconds between stages.

    Each stage carries its own timestamp and its own evidence. A stage
    that did not happen says so; it is not skipped, because a gap the
    reader cannot see is a gap the reader will fill in themselves.
    """
    d = await _guard(
        pool, "SHADOW_DECISION_UNREAD", pool.fetchrow,
        """
        SELECT d.*, o.rn1_source_ts, o.bettor_received_ts AS obs_received_ts,
               o.observation_written_ts, o.source_type, o.source_reference,
               o.idempotency_basis, o.rn1_quantity, o.side AS rn1_side,
               o.source_ts_status, o.record_kind,
               m.captured_at AS state_captured_at, m.evidence_source,
               m.readable AS state_readable, m.why_unreadable,
               m.bid AS state_bid, m.ask AS state_ask,
               m.available_depth, m.l2_reference, m.staleness_ms
          FROM shadow_decisions d
          LEFT JOIN rn1_observations o
                 ON o.rn1_observation_id = d.rn1_observation_id
          LEFT JOIN shadow_market_states m
                 ON m.market_state_id = d.market_state_id
         WHERE d.shadow_decision_id = $1
        """, shadow_decision_id)
    if d is None:
        raise RetrievalIncomplete("SHADOW_DECISION_NOT_FOUND",
                                  str(shadow_decision_id)[:64])
    execs = await _guard(
        pool, "SHADOW_EXECUTIONS_UNREAD", pool.fetch,
        "SELECT * FROM shadow_executions WHERE shadow_decision_id = $1 "
        "ORDER BY created_at ASC", shadow_decision_id)
    scores = await _guard(
        pool, "SHADOW_SCORES_UNREAD", pool.fetch,
        "SELECT * FROM shadow_scores WHERE shadow_decision_id = $1",
        shadow_decision_id)
    policy = await _guard(
        pool, "SHADOW_POLICY_UNREAD", pool.fetchrow,
        "SELECT * FROM shadow_policy_versions WHERE policy_version = $1",
        d["policy_version"])

    stages = [
        {"stage": "RN1_ACTION", "at": _iso(d["rn1_source_ts"]),
         "detail": {"side": d["rn1_side"], "price": _f(d["rn1_price"]),
                    "quantity": _f(d["rn1_quantity"]),
                    "sourceTimestampStatus": d["source_ts_status"]}},
        {"stage": "SOURCE_RECEIVED", "at": _iso(d["obs_received_ts"]),
         "detail": {"sourceType": d["source_type"],
                    "sourceReference": d["source_reference"],
                    "idempotencyBasis": d["idempotency_basis"],
                    "recordKind": d["record_kind"]}},
        {"stage": "OBSERVATION_WRITTEN",
         "at": _iso(d["observation_written_ts"]), "detail": {}},
        {"stage": "MARKET_STATE", "at": _iso(d["state_captured_at"]),
         "detail": {"evidenceSource": d["evidence_source"],
                    "readable": d["state_readable"],
                    "whyUnreadable": d["why_unreadable"],
                    "bid": _f(d["state_bid"]), "ask": _f(d["state_ask"]),
                    "availableDepth": _js(d["available_depth"]),
                    "l2Reference": _js(d["l2_reference"]),
                    "stalenessMs": _f(d["staleness_ms"])}},
        {"stage": "MODEL_AND_POLICY", "at": _iso(d["decision_ts"]),
         "detail": {"modelVersion": d["model_version"],
                    "policyVersion": d["policy_version"],
                    "policySha": policy["policy_sha"] if policy else None,
                    "policyCodeSha": (policy["policy_code_sha"]
                                      if policy else None),
                    "frozenAt": _iso(policy["frozen_at"]) if policy
                    else None}},
        {"stage": "EV_CALCULATION", "at": _iso(d["decision_ts"]),
         "detail": {"pMarket": _f(d["p_market"]),
                    "pBettor": _f(d["p_bettor"]),
                    "pBettorStatus": d["p_bettor_status"]
                    or lanes.NOT_ESTABLISHED,
                    "informationEv": _f(d["information_ev"]),
                    "executionEv": _f(d["execution_ev"]),
                    "totalActionEv": _f(d["total_action_ev"]),
                    "actionEvComponents": _js(d["action_ev_components"]),
                    "actionEvStatus": d["action_ev_status"]
                    or NOT_IDENTIFIED}},
        {"stage": "ALTERNATIVE_ACTIONS", "at": _iso(d["decision_ts"]),
         "detail": {"alternatives": _js(d["alternatives"]) or [],
                    "gateResults": _js(d["gate_results"]) or {}}},
        {"stage": "SELECTED_ACTION", "at": _iso(d["decision_ts"]),
         "detail": {"action": d["proposed_action"],
                    "side": d["proposed_side"],
                    "price": _f(d["proposed_price"]),
                    "quantity": _f(d["proposed_quantity"]),
                    "reasonCodes": _js(d["reason_codes"]) or [],
                    "blockers": _js(d["blockers"]) or []}},
    ]
    for e in execs:
        stages.append({
            "stage": "SHADOW_ARRIVAL", "at": _iso(e["arrival_ts"]),
            "detail": {"arrivalBook": _js(e["arrival_book"]),
                       "priceAtShadowArrival":
                           _f(e["price_at_shadow_arrival"]),
                       "latencyBasis": e["latency_basis"],
                       "dataLatencyMs": _f(e["data_latency_ms"]),
                       "decisionComputeMs": _f(e["decision_compute_ms"]),
                       "executionLatencyMs": _f(e["execution_latency_ms"]),
                       "totalToArrivalMs": _f(e["total_to_arrival_ms"])}})
        stages.append({
            "stage": "SHADOW_EXECUTION", "at": _iso(e["created_at"]),
            "detail": {"executionClass": e["execution_class"],
                       "realizable":
                           e["execution_class"] in sh.REALIZABLE_CLASSES,
                       "status": e["status"], "why": e["why"],
                       "vwap": _f(e["vwap"]),
                       "filledQty": _f(e["shadow_filled_qty"]),
                       "unfilledQty": _f(e["unfilled_qty"]),
                       "slippage": _f(e["slippage"]),
                       "spreadCost": _f(e["spread_cost"])}})
    if scores:
        for s in scores:
            stages.append({
                "stage": "SCORE_%s" % s["horizon"], "at": _iso(s["scored_at"]),
                "detail": {"observable": s["observable"],
                           "whyUnobservable": s["why_unobservable"],
                           "directionCorrect": s["direction_correct"],
                           "midMarkout": _f(s["mid_markout"]),
                           "executableMarkout": _f(s["executable_markout"]),
                           "settlementResult": s["settlement_result"]}})
    else:
        stages.append({"stage": "SETTLEMENT", "at": None,
                       "detail": {"status": NOT_IDENTIFIED,
                                  "why": "labels have not matured yet"}})

    # ELAPSED BETWEEN STAGES, measured, never assumed. A stage with no
    # timestamp contributes no elapsed figure rather than a zero, which
    # would read as "this took no time".
    previous = None
    for stage in stages:
        at = stage["at"]
        parsed = datetime.fromisoformat(at) if at else None
        stage["elapsedMsFromPrevious"] = (
            (parsed - previous).total_seconds() * 1000
            if parsed and previous else None)
        if parsed:
            previous = parsed

    return {"environment": environment(),
            "shadowDecisionId": shadow_decision_id,
            "lane": d["lane"],
            "symbol": d["symbol"],
            "outcomeLeg": d["outcome_leg"],
            "stages": stages,
            "finalEconomics": {
                "byExecutionClass": {
                    e["execution_class"]: {
                        "vwap": _f(e["vwap"]),
                        "filledQty": _f(e["shadow_filled_qty"]),
                        "slippage": _f(e["slippage"]),
                        "realizable":
                            e["execution_class"] in sh.REALIZABLE_CLASSES}
                    for e in execs},
                "total": None,
                "why": ("simulated and observed economics are never summed "
                        "into one number")}}


# ── system health ────────────────────────────────────────────────────

HEALTH_COMPONENTS = ("RN1_LISTENER", "SHADOW_WRITER", "DATABASE",
                     "INSTITUTIONAL_MARKET_DATA", "L2", "LABEL_MATURITY",
                     "SHADOW_ENGINE", "BETTOR_EV_ENGINE", "COMMAND_API")


async def health(pool) -> dict:
    """Per-component state with the SOURCE timestamp beside it.

    "Do not turn browser heartbeat into source freshness." Every state
    below is derived from a row's own timestamp -- a service heartbeat,
    an observation, a decision -- so a page that polls happily against a
    dead writer still reads STALE.
    """
    beats = await _guard(
        pool, "SERVICE_HEARTBEATS_UNREAD", pool.fetch,
        "SELECT service, status, detail, beat_at FROM service_heartbeats "
        "WHERE service = ANY($1::text[])",
        ["chain_listener", "poller", "shadow_rn1"])
    by_service = {b["service"]: b for b in beats}
    now = datetime.now(tz=timezone.utc)

    def from_beat(service):
        b = by_service.get(service)
        if b is None:
            return {"state": "NOT_ESTABLISHED", "sourceTimestamp": None,
                    "detail": "no heartbeat has ever been recorded"}
        age = (now - b["beat_at"]).total_seconds() \
            if isinstance(b["beat_at"], datetime) else None
        state = ("STALE" if age is not None and age > STALE_AFTER_S
                 else "BLOCKED" if b["status"] in ("store_not_ready",)
                 else "DEGRADED" if b["status"] not in ("ok", "live")
                 else "LIVE")
        return {"state": state, "sourceTimestamp": _iso(b["beat_at"]),
                "sourceAgeSeconds": age, "detail": b["status"]}

    last_obs = await _guard(pool, "SHADOW_COUNTS_UNREAD", pool.fetchval,
                            "SELECT max(bettor_received_ts) "
                            "FROM rn1_observations")
    bettor_n = await _guard(pool, "SHADOW_COUNTS_UNREAD", pool.fetchval,
                            "SELECT count(*) FROM shadow_decisions "
                            "WHERE lane = 'BETTOR_EV_SHADOW'")
    scores_n = await _guard(pool, "SHADOW_SCORES_UNREAD", pool.fetchval,
                            "SELECT count(*) FROM shadow_scores")

    components = {
        "RN1_LISTENER": from_beat("chain_listener"),
        "SHADOW_WRITER": {
            "state": "LIVE" if last_obs else "LISTENING",
            "sourceTimestamp": _iso(last_obs),
            "detail": ("no sighting written yet" if not last_obs
                       else "observations landing")},
        "DATABASE": {"state": "LIVE", "sourceTimestamp": now.isoformat(),
                     "detail": "this payload was read from it"},
        "INSTITUTIONAL_MARKET_DATA": {
            "state": "NOT_ESTABLISHED", "sourceTimestamp": None,
            "detail": ("the institutional read-only production lane is "
                       "observed but not wired into the shadow book")},
        "L2": {"state": "NOT_ESTABLISHED", "sourceTimestamp": None,
               "detail": ("no depth feed: sizes stay "
                          "OBSERVED_DEPTH_NOT_ESTABLISHED rather than "
                          "being taken from top of book")},
        "LABEL_MATURITY": {
            "state": "LIVE" if scores_n else "NOT_ESTABLISHED",
            "sourceTimestamp": None,
            "detail": "%d scored horizons" % int(scores_n or 0)},
        "SHADOW_ENGINE": from_beat("shadow_rn1"),
        "BETTOR_EV_ENGINE": {
            "state": "LIVE" if bettor_n else "NOT_ESTABLISHED",
            "sourceTimestamp": None,
            "detail": "P_BETTOR NOT ESTABLISHED — no independent EV yet"},
        "COMMAND_API": {"state": "LIVE",
                        "sourceTimestamp": now.isoformat(),
                        "detail": "serving"},
    }
    return {"environment": environment(), "generatedAt": now.isoformat(),
            "components": components}
