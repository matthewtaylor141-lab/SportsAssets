"""MANAGEMENT ACCOUNTING FOR THE BETTOR EV SHADOW LANE.

Owner directive 2026-09-19: "Management does not only want P&L. COMMAND
must continuously answer: HOW MUCH CAPITAL HAVE WE PLAYED THROUGH? HOW
MUCH CAPITAL DID WE ACTUALLY NEED? HOW MANY TIMES DID WE RECYCLE IT? HOW
MUCH DID WE MAKE? WHAT RETURN DID THAT CAPITAL PRODUCE?"

READ-ONLY, BY CONSTRUCTION. Every statement here is a SELECT. Accounting
that could write would eventually write, and the prospective ledger's
whole value is that the claim recorded at T0 is the claim you read back
later. A test refuses any mutating statement in this file.

ONE LANE. Every query filters lane = BETTOR_EV_SHADOW. "Use the
independent BETTOR_EV_SHADOW lane. Do not combine RN1 economics with
BETTOR economics." There is no union, no total-across-lanes, and no
parameter that would let a caller ask for both at once.

THE THREE CAPITAL NUMBERS ARE THREE NUMBERS.

    ENTRY_NOTIONAL_PLAYED   dollars put through the strategy on entries.
    GROSS_TRADING_TURNOVER  every executed position-changing leg.
    CAPITAL_DEPLOYED        what we would actually have had to fund.

The first two are sums; the third is a step function over time, which is
why peak and average come from an integral over the ledger's own events
rather than from a sampler.

WHAT IS NOT COUNTED, AND WHY.

    PASSIVE_QUEUE_MODEL_ESTIMATE    "Do not count unidentified passive
                                     fills." A queue model's opinion
                                     about whether we would have been
                                     filled is not a fill.
    PASSIVE_COUNTERFACTUAL_MARKOUT  "Do not count counterfactual markout
                                     as realized P&L." Research evidence,
                                     never economics.

Both remain in the ledger and stay readable for research. They are
excluded from every dollar on this screen.

ZERO IS A CLAIM. A denominator of zero yields NOT_APPLICABLE, never 0%.
"Do not display 0.00% where there has been no capital deployment." A
figure nobody has established reads NOT_IDENTIFIED. The only honest
zeros are the ones the directive names: with no entry yet, dollars
played and P&L really are $0.

NOTHING HERE MAKES BETTOR TRADE. This module has no decision path, no
threshold and no write. "This accounting starts when the EV Engine
actually generates an eligible prospective shadow trade."
"""

from __future__ import annotations

from . import shadow as sh
from . import shadow_bettor_sizing as sizing
from . import shadow_lanes as lanes

LANE = lanes.BETTOR_EV_SHADOW

NOT_APPLICABLE = "NOT_APPLICABLE"
NOT_IDENTIFIED = sh.NOT_IDENTIFIED
NOT_SELECTED = "NOT_SELECTED"

# ── which executions are economics ───────────────────────────────────
#
# SHADOW-ECONOMIC IS NOT THE SAME QUESTION AS REALIZABLE-IN-CASH.
# sh.REALIZABLE_CLASSES answers "would this have been real money", and
# today it is exactly {ACTUAL_FILL}, which the schema forbids from
# carrying any quantity while no real order exists. That is the correct
# answer to a different question. What belongs in SHADOW economics is a
# fill the reconstruction actually established -- the directive's "sum
# of actual reconstructed shadow entry fills" -- plus any real fill if
# one ever exists.
ECONOMIC_CLASSES = (sh.MARKETABLE_RECONSTRUCTED, sh.ACTUAL_FILL)
EXCLUDED_CLASSES = (sh.PASSIVE_QUEUE_MODEL_ESTIMATE,
                    sh.PASSIVE_COUNTERFACTUAL_MARKOUT)

# ── periods, cut on event timestamps ─────────────────────────────────
#
# "Use event timestamps, not browser-local grouping." The boundary is
# computed in the database, in UTC, so two people in two time zones
# reading the same screen see the same number.
PERIODS = ("TODAY", "7D", "30D", "ALL")

_PERIOD_SQL = {
    "TODAY": "date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'",
    "7D": "now() - interval '7 days'",
    "30D": "now() - interval '30 days'",
    "ALL": "NULL::timestamptz",
}


def period_bound_sql(period: str) -> str:
    if period not in _PERIOD_SQL:
        raise ValueError("refused: unknown period %r; known: %s"
                         % (period, ", ".join(PERIODS)))
    return _PERIOD_SQL[period]


# ── the two honest non-answers ───────────────────────────────────────


def ratio(numerator, denominator):
    """A return, or NOT_APPLICABLE -- never 0%.

    "Where denominator is zero: NOT_APPLICABLE, not 0%." A zero
    denominator does not make a return of nothing; it makes the question
    unanswerable, and rendering 0.00% would answer it falsely.
    """
    if numerator is None or denominator in (None, 0):
        return NOT_APPLICABLE
    d = float(denominator)
    if d == 0.0:
        return NOT_APPLICABLE
    return float(numerator) / d


def usd(value, *, missing=NOT_IDENTIFIED):
    """A dollar figure, or the word. NULL from a sum over no rows is
    not zero dollars -- it is no rows."""
    return missing if value is None else float(value)


def _z(value):
    """A dollar figure whose true value when nothing exists IS zero.

    Used only where the directive says so: "ENTRY_NOTIONAL_PLAYED = $0
    ... GROSS_TRADING_TURNOVER = $0 ... NET_SHADOW_PNL = $0" before the
    first eligible entry. Nothing played really is nothing played.
    """
    return 0.0 if value is None else float(value)


# ── 1. the executed legs ─────────────────────────────────────────────

_EXECUTIONS_SQL = """
    SELECT
      sum(CASE WHEN e.execution_leg_kind = 'ENTRY'
               THEN COALESCE(e.executed_notional_usd, 0) END)
          AS entry_notional_played,
      sum(CASE WHEN e.execution_leg_kind = 'ENTRY'
               THEN COALESCE(e.intended_notional_usd, 0) END)
          AS intended_notional,
      sum(CASE WHEN e.execution_leg_kind = 'ENTRY'
               THEN COALESCE(e.unfilled_notional_usd, 0) END)
          AS unfilled_notional,
      -- EVERY position-changing leg, which is what turnover means.
      sum(COALESCE(e.executed_notional_usd, 0)) AS gross_turnover,

      sum(e.fees_usd)              AS fees,
      count(*) FILTER (WHERE e.fees_usd IS NULL)            AS fees_missing,
      sum(e.spread_cost_usd)       AS spread_cost,
      count(*) FILTER (WHERE e.spread_cost_usd IS NULL)     AS spread_missing,
      sum(e.slippage_cost_usd)     AS slippage_cost,
      count(*) FILTER (WHERE e.slippage_cost_usd IS NULL)   AS slip_missing,
      -- "ADVERSE SELECTION where identified" -- so the sum is NOT
      -- COALESCEd: all-NULL stays NULL and reads NOT_IDENTIFIED.
      sum(e.adverse_selection_usd) AS adverse_selection,
      count(*) FILTER (WHERE e.adverse_selection_usd IS NOT NULL)
          AS adverse_identified,

      count(*) AS legs,
      count(*) FILTER (WHERE e.execution_leg_kind = 'ENTRY') AS entry_legs,
      count(*) FILTER (WHERE e.execution_leg_kind IS NULL)   AS unclassed_legs
      FROM shadow_executions e
      JOIN shadow_decisions d
        ON d.shadow_decision_id = e.shadow_decision_id
     WHERE d.lane = '%(lane)s'
       AND e.execution_class = ANY($1::text[])
       AND (%(since)s IS NULL OR e.created_at >= %(since)s)
"""

# The legs deliberately left out of every dollar above, counted so the
# screen can say how much research evidence it is NOT showing rather
# than silently dropping it.
_EXCLUDED_SQL = """
    SELECT e.execution_class, count(*) AS n
      FROM shadow_executions e
      JOIN shadow_decisions d
        ON d.shadow_decision_id = e.shadow_decision_id
     WHERE d.lane = '%(lane)s'
       AND e.execution_class = ANY($1::text[])
       AND (%(since)s IS NULL OR e.created_at >= %(since)s)
     GROUP BY e.execution_class
"""

# ── 2. the P&L waterfall ─────────────────────────────────────────────
#
# Each component is summed from the position events that produced it and
# NEVER from a single blended column. The directive lists them
# separately because they answer different questions and mature on
# different clocks.

_PNL_SQL = """
    WITH ev AS (
        SELECT pe.*
          FROM shadow_position_events pe
          JOIN shadow_positions p
            ON p.shadow_position_id = pe.shadow_position_id
          JOIN shadow_decisions d
            ON d.shadow_decision_id = p.originating_decision_id
         WHERE d.lane = '%(lane)s'
           -- Economics never travel without the class that produced
           -- them, and an event with no class is not economics.
           AND pe.execution_class = ANY($1::text[])
           AND (%(since)s IS NULL OR pe.at >= %(since)s)
    )
    SELECT
      sum(settlement_pnl) FILTER (WHERE kind = 'SETTLE')      AS settled_pnl,
      sum(realized_pnl) FILTER (WHERE kind IN ('REDUCE', 'CLOSE'))
          AS realized_exit_pnl,
      sum(realized_pnl) FILTER (WHERE kind = 'PAIR')          AS pairing_pnl,
      sum(realized_pnl) FILTER (WHERE kind = 'CASH_OUT')      AS cashout_pnl,
      count(*) AS events
      FROM ev
"""

# UNREALIZED is a property of what is STILL OPEN, read from each open
# position's most recent mark. A closed position has no unrealized P&L,
# and summing stale marks from closed positions is how unrealized gains
# get counted twice.
_UNREALIZED_SQL = """
    WITH bettor_pos AS (
        SELECT p.shadow_position_id
          FROM shadow_positions p
          JOIN shadow_decisions d
            ON d.shadow_decision_id = p.originating_decision_id
         WHERE d.lane = '%(lane)s'
    ),
    open_pos AS (
        SELECT bp.shadow_position_id
          FROM bettor_pos bp
         WHERE NOT EXISTS (
                   SELECT 1 FROM shadow_position_events e
                    WHERE e.shadow_position_id = bp.shadow_position_id
                      AND e.kind IN ('CLOSE', 'SETTLE'))
    ),
    latest AS (
        SELECT DISTINCT ON (e.shadow_position_id)
               e.shadow_position_id, e.unrealized_pnl, e.execution_class
          FROM shadow_position_events e
          JOIN open_pos o ON o.shadow_position_id = e.shadow_position_id
         WHERE e.unrealized_pnl IS NOT NULL
         ORDER BY e.shadow_position_id, e.at DESC
    )
    SELECT sum(unrealized_pnl) FILTER (
               WHERE execution_class = ANY($1::text[])) AS unrealized_pnl,
           count(*) AS marked_open_positions
      FROM latest
"""

# ── 3. capital deployed, as an integral over the ledger ──────────────
#
# PEAK is the true maximum of the true step function, not the largest
# value a sampler happened to observe. AVERAGE is capital-hours divided
# by elapsed hours, which is the only average that respects how long
# each dollar was actually tied up.
#
# The segment arithmetic CLAMPS to the window on both ends, so a
# position that was already open when the period began contributes only
# the part of its life that falls inside the period.

_CAPITAL_SQL = """
    WITH tl AS (
        SELECT at, sum(capital_delta) AS delta
          FROM bettor_capital_timeline
         GROUP BY at
    ),
    runs AS (
        SELECT at, sum(delta) OVER (ORDER BY at
                                    ROWS UNBOUNDED PRECEDING) AS deployed
          FROM tl
    ),
    segs AS (
        SELECT deployed,
               GREATEST(at, COALESCE(%(since)s, at))                AS seg_from,
               LEAST(COALESCE(lead(at) OVER (ORDER BY at), now()),
                     now())                                        AS seg_to
          FROM runs
    ),
    windowed AS (
        SELECT deployed, seg_from, seg_to
          FROM segs
         WHERE seg_to > seg_from
    )
    SELECT max(deployed)                                        AS peak,
           sum(deployed * extract(epoch FROM (seg_to - seg_from)) / 3600.0)
               AS capital_hours,
           min(seg_from)                                        AS first_at,
           -- CURRENT is a fact about right now, so it is read from the
           -- whole timeline rather than from the selected window: a
           -- position opened last month is still deployed today.
           (SELECT deployed FROM runs ORDER BY at DESC LIMIT 1) AS current_deployed
      FROM windowed
"""

# ── 4. positions, hold times and the closed-trade statistics ─────────

_POSITIONS_SQL = """
    WITH bettor_pos AS (
        SELECT p.shadow_position_id, p.entry_time, p.entry_price, p.entry_qty
          FROM shadow_positions p
          JOIN shadow_decisions d
            ON d.shadow_decision_id = p.originating_decision_id
         WHERE d.lane = '%(lane)s'
           AND (%(since)s IS NULL OR p.entry_time >= %(since)s)
    ),
    ending AS (
        SELECT bp.shadow_position_id, bp.entry_time, bp.entry_price,
               bp.entry_qty,
               min(e.at) FILTER (WHERE e.kind IN ('CLOSE', 'SETTLE'))
                   AS ended_at,
               bool_or(e.kind = 'SETTLE')  AS settled,
               bool_or(e.kind = 'CLOSE')   AS closed,
               -- One position's whole economic result, from the events
               -- whose class makes them economics.
               sum(COALESCE(e.realized_pnl, 0) + COALESCE(e.settlement_pnl, 0))
                   FILTER (WHERE e.execution_class = ANY($1::text[]))
                   AS result_pnl
          FROM bettor_pos bp
          LEFT JOIN shadow_position_events e
            ON e.shadow_position_id = bp.shadow_position_id
         GROUP BY 1, 2, 3, 4
    )
    SELECT
      count(*)                                          AS positions,
      count(*) FILTER (WHERE ended_at IS NULL)           AS open_positions,
      count(*) FILTER (WHERE closed)                     AS closed_positions,
      count(*) FILTER (WHERE settled)                    AS settled_positions,

      count(*) FILTER (WHERE ended_at IS NOT NULL AND result_pnl > 0)
          AS winners,
      count(*) FILTER (WHERE ended_at IS NOT NULL AND result_pnl < 0)
          AS losers,
      count(*) FILTER (WHERE ended_at IS NOT NULL AND result_pnl = 0)
          AS breakeven,

      avg(result_pnl) FILTER (WHERE ended_at IS NOT NULL AND result_pnl > 0)
          AS average_win,
      avg(result_pnl) FILTER (WHERE ended_at IS NOT NULL AND result_pnl < 0)
          AS average_loss,
      sum(result_pnl) FILTER (WHERE ended_at IS NOT NULL AND result_pnl > 0)
          AS gross_profit,
      sum(result_pnl) FILTER (WHERE ended_at IS NOT NULL AND result_pnl < 0)
          AS gross_loss,
      -- THE BIGGEST WIN IS A WIN AND THE BIGGEST LOSS IS A LOSS.
      -- Ranging max() over every closed trade reports the least-bad
      -- loss as MAX_WIN when nothing won, and min() reports the
      -- smallest win as MAX_LOSS when nothing lost -- which is what
      -- this did until the first fixture with one winner and no losers
      -- printed MAX_LOSS = +65.00. With no losers there is no worst
      -- loss, and the honest answer is NOT_IDENTIFIED.
      max(result_pnl) FILTER (WHERE ended_at IS NOT NULL AND result_pnl > 0)
          AS max_win,
      min(result_pnl) FILTER (WHERE ended_at IS NOT NULL AND result_pnl < 0)
          AS max_loss,

      -- RETURN PER CLOSED TRADE is measured against that trade's own
      -- cost basis, so a $50 trade and a $1,000 trade contribute
      -- comparably rather than by size.
      avg(result_pnl / NULLIF(entry_price * entry_qty, 0))
          FILTER (WHERE ended_at IS NOT NULL)          AS average_return,
      percentile_cont(0.5) WITHIN GROUP (
          ORDER BY result_pnl / NULLIF(entry_price * entry_qty, 0))
          FILTER (WHERE ended_at IS NOT NULL)          AS median_return,

      avg(extract(epoch FROM (ended_at - entry_time)))
          FILTER (WHERE ended_at IS NOT NULL)          AS avg_hold_s,
      percentile_cont(0.5) WITHIN GROUP (
          ORDER BY extract(epoch FROM (ended_at - entry_time)))
          FILTER (WHERE ended_at IS NOT NULL)          AS median_hold_s,
      percentile_cont(0.9) WITHIN GROUP (
          ORDER BY extract(epoch FROM (ended_at - entry_time)))
          FILTER (WHERE ended_at IS NOT NULL)          AS p90_hold_s
      FROM ending
"""

# ── 5. the decision counters ─────────────────────────────────────────

_DECISIONS_SQL = """
    SELECT
      (SELECT count(*) FROM bettor_opportunities o
        WHERE (%(since)s IS NULL OR o.observed_at >= %(since)s))
          AS opportunities_evaluated,
      (SELECT count(*) FROM shadow_decisions d
        WHERE d.lane = '%(lane)s'
          AND (%(since)s IS NULL OR d.created_at >= %(since)s))
          AS decisions,
      (SELECT count(*) FROM shadow_decisions d
        WHERE d.lane = '%(lane)s' AND d.proposed_action = 'NO_TRADE'
          AND (%(since)s IS NULL OR d.created_at >= %(since)s))
          AS no_trade_decisions,
      (SELECT count(*) FROM shadow_decisions d
        WHERE d.lane = '%(lane)s' AND d.proposed_action <> 'NO_TRADE'
          AND (%(since)s IS NULL OR d.created_at >= %(since)s))
          AS acting_decisions
"""

# ── 6. the equity curve and its drawdown ─────────────────────────────
#
# Built from the prospective economics in event order. "Do not choose
# starting capital retrospectively to flatter returns" -- so this curve
# starts at zero P&L and measures the DRAWDOWN OF THE P&L ITSELF. No
# bankroll is assumed, which is why MAX_DRAWDOWN_PCT stays
# NOT_APPLICABLE until management selects one.

_EQUITY_SQL = """
    WITH ev AS (
        SELECT pe.at,
               COALESCE(pe.realized_pnl, 0) + COALESCE(pe.settlement_pnl, 0)
                   AS pnl
          FROM shadow_position_events pe
          JOIN shadow_positions p
            ON p.shadow_position_id = pe.shadow_position_id
          JOIN shadow_decisions d
            ON d.shadow_decision_id = p.originating_decision_id
         WHERE d.lane = '%(lane)s'
           AND pe.execution_class = ANY($1::text[])
           AND (%(since)s IS NULL OR pe.at >= %(since)s)
    ),
    curve AS (
        SELECT at,
               sum(pnl) OVER (ORDER BY at ROWS UNBOUNDED PRECEDING) AS equity
          FROM ev
    ),
    hwm AS (
        SELECT at, equity,
               max(equity) OVER (ORDER BY at ROWS UNBOUNDED PRECEDING) AS peak
          FROM curve
    )
    SELECT max(peak)                          AS high_water_mark,
           min(equity - peak)                 AS max_drawdown,
           (SELECT equity FROM curve ORDER BY at DESC LIMIT 1) AS current_equity,
           (SELECT equity - peak FROM hwm ORDER BY at DESC LIMIT 1)
               AS current_drawdown,
           count(*)                           AS points
      FROM hwm
"""


def _sql(template: str, period: str) -> str:
    return template % {"lane": LANE, "since": period_bound_sql(period)}


async def report(pool, *, period: str = "ALL",
                 starting_shadow_capital=None) -> dict:
    """The whole management picture for one period.

    `starting_shadow_capital` is None until management selects a
    hypothetical bankroll. "If management has not yet selected a
    hypothetical starting bankroll, report capital-required metrics
    without inventing one." So it is never defaulted to a number.
    """
    if period not in PERIODS:
        raise ValueError("refused: unknown period %r" % (period,))

    ex = await pool.fetchrow(_sql(_EXECUTIONS_SQL, period),
                             list(ECONOMIC_CLASSES))
    excluded_rows = await pool.fetch(_sql(_EXCLUDED_SQL, period),
                                     list(EXCLUDED_CLASSES))
    pnl = await pool.fetchrow(_sql(_PNL_SQL, period), list(ECONOMIC_CLASSES))
    unreal = await pool.fetchrow(_sql(_UNREALIZED_SQL, period),
                                 list(ECONOMIC_CLASSES))
    cap = await pool.fetchrow(_sql(_CAPITAL_SQL, period))
    pos = await pool.fetchrow(_sql(_POSITIONS_SQL, period),
                              list(ECONOMIC_CLASSES))
    dec = await pool.fetchrow(_sql(_DECISIONS_SQL, period))
    eq = await pool.fetchrow(_sql(_EQUITY_SQL, period),
                             list(ECONOMIC_CLASSES))

    # ── capital played through ───────────────────────────────────────
    entry_played = _z(ex["entry_notional_played"])
    turnover = _z(ex["gross_turnover"])
    intended = _z(ex["intended_notional"])
    unfilled = _z(ex["unfilled_notional"])

    # ── capital required ─────────────────────────────────────────────
    current_deployed = _z(cap["current_deployed"])
    peak_deployed = _z(cap["peak"])
    capital_hours = _z(cap["capital_hours"])
    # Average over the window that actually had capital in it.
    elapsed_h = None
    if cap["first_at"] is not None:
        elapsed_h = await pool.fetchval(
            "SELECT extract(epoch FROM (now() - $1)) / 3600.0",
            cap["first_at"])
    average_deployed = (ratio(capital_hours, elapsed_h)
                        if elapsed_h else NOT_APPLICABLE)

    # "CAPITAL_TURNS = ENTRY_NOTIONAL_PLAYED / PEAK_CAPITAL_DEPLOYED
    # where denominator > 0." And it is not leverage: it is how many
    # times the same dollars were recycled.
    capital_turns = ratio(entry_played, peak_deployed)
    turnover_on_average_capital = ratio(turnover, average_deployed
                                        if isinstance(average_deployed, float)
                                        else None)

    # ── the P&L waterfall ────────────────────────────────────────────
    settled = _z(pnl["settled_pnl"])
    realized_exit = _z(pnl["realized_exit_pnl"])
    pairing = _z(pnl["pairing_pnl"])
    cashout = _z(pnl["cashout_pnl"])
    unrealized = _z(unreal["unrealized_pnl"])
    fees = _z(ex["fees"])
    spread_cost = _z(ex["spread_cost"])
    slippage = _z(ex["slippage_cost"])
    adverse = usd(ex["adverse_selection"])

    # NET is the sum of what is identified. Adverse selection joins it
    # only where it was identified -- "ADVERSE SELECTION where
    # identified" -- so an unmeasured effect is never treated as zero
    # inside the total while also being reported as NOT_IDENTIFIED
    # beside it.
    net = (settled + realized_exit + pairing + cashout + unrealized
           - fees - spread_cost - slippage)
    if isinstance(adverse, float):
        net -= adverse

    # ── returns, each against its own denominator ────────────────────
    returns = {
        "returnOnEntryNotional": ratio(net, entry_played),
        "returnOnPeakCapital": ratio(net, peak_deployed),
        "returnOnAverageCapital": ratio(
            net, average_deployed if isinstance(average_deployed, float)
            else None),
        "settledReturnOnEntryNotional": ratio(settled, entry_played),
        "realizedReturnOnEntryNotional": ratio(
            realized_exit + pairing + cashout, entry_played),
    }

    # ── trade statistics ─────────────────────────────────────────────
    winners = int(pos["winners"] or 0)
    losers = int(pos["losers"] or 0)
    breakeven = int(pos["breakeven"] or 0)
    decided = winners + losers + breakeven
    gross_profit = pnl_abs = None
    if pos["gross_profit"] is not None:
        gross_profit = float(pos["gross_profit"])
    if pos["gross_loss"] is not None:
        pnl_abs = abs(float(pos["gross_loss"]))

    stats = {
        "opportunitiesEvaluated": int(dec["opportunities_evaluated"] or 0),
        "decisions": int(dec["decisions"] or 0),
        "noTradeDecisions": int(dec["no_trade_decisions"] or 0),
        "actingDecisions": int(dec["acting_decisions"] or 0),
        "shadowTradesEntered": int(pos["positions"] or 0),
        "positionsOpen": int(pos["open_positions"] or 0),
        "positionsClosed": int(pos["closed_positions"] or 0),
        "positionsSettled": int(pos["settled_positions"] or 0),
        "winningClosedPositions": winners,
        "losingClosedPositions": losers,
        "breakevenClosedPositions": breakeven,
        # WIN RATE OF NOTHING IS NOT ZERO PER CENT.
        "winRate": ratio(winners, decided),
        "averageWinUsd": usd(pos["average_win"]),
        "averageLossUsd": usd(pos["average_loss"]),
        "profitFactor": ratio(gross_profit, pnl_abs),
        "averageReturnPerClosedTrade": usd(pos["average_return"],
                                           missing=NOT_APPLICABLE),
        "medianReturnPerClosedTrade": usd(pos["median_return"],
                                          missing=NOT_APPLICABLE),
        "maxWinUsd": usd(pos["max_win"]),
        "maxLossUsd": usd(pos["max_loss"]),
    }

    hold = {
        "averageHoldSeconds": usd(pos["avg_hold_s"], missing=NOT_APPLICABLE),
        "medianHoldSeconds": usd(pos["median_hold_s"],
                                 missing=NOT_APPLICABLE),
        "p90HoldSeconds": usd(pos["p90_hold_s"], missing=NOT_APPLICABLE),
    }

    # ── equity and drawdown ──────────────────────────────────────────
    max_dd = eq["max_drawdown"]
    # min(equity - peak) is <= 0; drawdown is reported as a positive
    # magnitude, and "no points yet" is not a drawdown of zero dollars.
    max_dd_usd = (abs(float(max_dd)) if max_dd is not None
                  else (0.0 if not int(eq["points"] or 0) else 0.0))
    cur_dd = eq["current_drawdown"]
    equity = {
        # "Do not choose starting capital retrospectively to flatter
        # returns."
        "startingShadowCapital": (NOT_SELECTED
                                  if starting_shadow_capital is None
                                  else float(starting_shadow_capital)),
        "currentShadowPnl": _z(eq["current_equity"]),
        "highWaterMarkPnl": _z(eq["high_water_mark"]),
        "currentDrawdownUsd": (abs(float(cur_dd)) if cur_dd is not None
                               else 0.0),
        "maxDrawdownUsd": max_dd_usd,
        # A percentage needs a bankroll, and none has been selected.
        "maxDrawdownPct": (NOT_APPLICABLE if starting_shadow_capital is None
                           else ratio(max_dd_usd, starting_shadow_capital)),
        "points": int(eq["points"] or 0),
        "why": ("the curve is cumulative prospective shadow P&L; no "
                "hypothetical starting bankroll has been selected, so "
                "drawdown is reported in dollars and the percentage "
                "stays NOT_APPLICABLE"),
    }

    excluded = {r["execution_class"]: int(r["n"]) for r in excluded_rows}
    for c in EXCLUDED_CLASSES:
        excluded.setdefault(c, 0)

    return {
        "lane": LANE,
        "period": period,
        "cohort": sizing.COHORT,
        "sizingPolicyVersion": sizing.SIZING_POLICY_VERSION,
        "standardNotionalUsd": sizing.STANDARD_BETTOR_SHADOW_NOTIONAL_USD,
        "policyVersion": lanes.BETTOR_EV_SHADOW,

        "capitalPlayed": {
            "intendedNotionalUsd": intended,
            "entryNotionalPlayedUsd": entry_played,
            "unfilledNotionalUsd": unfilled,
            "grossTradingTurnoverUsd": turnover,
            "entryLegs": int(ex["entry_legs"] or 0),
            "executedLegs": int(ex["legs"] or 0),
            "legsWithNoKind": int(ex["unclassed_legs"] or 0),
        },
        "capitalRequired": {
            "currentCapitalDeployedUsd": current_deployed,
            "peakCapitalDeployedUsd": peak_deployed,
            "averageCapitalDeployedUsd": average_deployed,
            "capitalHours": capital_hours,
            # NOT LEVERAGE. Recycling the same dollar is not borrowing.
            "capitalTurns": capital_turns,
            "turnoverOnAverageCapital": turnover_on_average_capital,
            "netPnlPer1000CapitalHour": (
                ratio(net, capital_hours) * 1000
                if isinstance(ratio(net, capital_hours), float)
                else NOT_APPLICABLE),
        },
        "pnl": {
            "settledPnlUsd": settled,
            "realizedExitPnlUsd": realized_exit,
            "pairingPnlUsd": pairing,
            "cashoutPnlUsd": cashout,
            "unrealizedExecutablePnlUsd": unrealized,
            "feesUsd": fees,
            "spreadCostUsd": spread_cost,
            "slippageCostUsd": slippage,
            "adverseSelectionUsd": adverse,
            "netShadowPnlUsd": net,
            "costsNotIdentified": {
                "fees": int(ex["fees_missing"] or 0),
                "spreadCost": int(ex["spread_missing"] or 0),
                "slippage": int(ex["slip_missing"] or 0),
                "adverseSelectionIdentifiedOn":
                    int(ex["adverse_identified"] or 0),
            },
        },
        "returns": returns,
        "statistics": stats,
        "holdTime": hold,
        "equity": equity,

        # WHAT WAS DELIBERATELY LEFT OUT, counted rather than dropped.
        "excludedFromEconomics": {
            "counts": excluded,
            "why": ("PASSIVE_QUEUE_MODEL_ESTIMATE is an unidentified "
                    "passive fill and PASSIVE_COUNTERFACTUAL_MARKOUT is "
                    "counterfactual markout; neither is economics. Both "
                    "stay in the ledger for research."),
        },

        # EVERY PANEL CARRIES THIS. "Do not allow shadow returns to be
        # mistaken for actual investment performance."
        "disclosure": {
            "lane": "BETTOR EV SHADOW",
            "capital": "NO REAL CAPITAL",
            "execution": "SIMULATED / COUNTERFACTUAL EXECUTION",
            "realOrderActivity": "NONE",
            "capitalAtRisk": sh.CAPITAL_AT_RISK,
            "shadowMode": sh.SHADOW_MODE,
            "realOrderSubmissionEnabled": sh.REAL_ORDER_SUBMISSION_ENABLED,
            "text": sh.DISCLOSURE,
        },
    }


async def all_periods(pool, **kw) -> dict:
    """TODAY / 7D / 30D / ALL, each cut on event timestamps."""
    return {p: await report(pool, period=p, **kw) for p in PERIODS}
