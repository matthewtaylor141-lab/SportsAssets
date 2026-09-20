"""COMMAND: the BETTOR EXPERIMENTAL SHADOW panel.

Owner directive 2026-09-19 21:2xZ §13. A lane of its own on COMMAND,
labelled so that no screenshot of it can ever be mistaken for trading
performance.

THREE THINGS THIS MODULE REFUSES TO DO, each because the honest answer
is less flattering than the easy one:

  IT NEVER PRINTS AN UNMARKED POSITION AS ZERO P&L. A position whose
  markout horizon has not matured, or whose book never arrived inside
  the tolerance, has NO measured value. Counting it as 0 would quietly
  claim we measured something we did not, and under the GitHub bridge
  -- where most short-horizon markouts miss -- it would make a lane of
  unknowns look like a lane of flat trades. `markedPositions` and
  `unmarkedPositions` are both on the payload and the headline says
  which it covers.

  IT NEVER POOLS TWO LATENCY REGIMES. A book fetched by a CI runner
  minutes after the decision is a different execution environment from
  a persistent worker's. Every figure is per regime, and there is no
  "total" row across them -- an average of the two would describe
  neither.

  IT NEVER RANKS ON A TINY SAMPLE. §11: "Do not rank/promote on tiny
  samples." The leaderboard carries each experiment's sample size and
  a `sufficientSample` flag, and nothing here promotes anything.

P&L HERE IS A MARK, NOT A REALISATION. No experimental position has
been exited; every figure is the newest OBSERVED markout against the
entry vwap. The payload says so in `pnlBasis` and the panel prints it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .. import shadow as sh
from .. import shadow_experiment_registry as reg
from .. import shadow_experimental_markouts as mk
from .. import shadow_experimental_store as xstore
from .. import shadow_experiments as xp

LANE = xp.EXPERIMENTAL_LANE

# What a reader must see before any number on this panel.
DISCLOSURE_LINES = ("EXPERIMENTAL SHADOW", "NO REAL CAPITAL",
                    "NOT VALIDATED PERFORMANCE")

PNL_BASIS = ("MARK_TO_OBSERVED_MARKOUT: no experimental position has "
             "been exited, so every P&L figure is the newest OBSERVED "
             "markout against the entry vwap, not a realisation")

# §11's guard against reading a leaderboard too early. A number below
# this is printed and is NOT ranked on.
MIN_SAMPLE_FOR_RANKING = 30


class RetrievalIncomplete(RuntimeError):
    """The ledger could not be read. NOT a reading of zero."""

    def __init__(self, what, cause):
        super().__init__("%s: %s" % (what, cause))
        self.what = what
        self.cause = cause


# THE FUNNEL'S WINDOW. A day, so the screen answers "why is there no
# activity TODAY" rather than averaging today into a week.
FUNNEL_WINDOW_S = 86400


def _f(v):
    return None if v is None else float(v)


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else None


async def _guard(pool, what, sql, *args, one=False):
    try:
        return await (pool.fetchrow(sql, *args) if one
                      else pool.fetch(sql, *args))
    except Exception as exc:                                   # noqa: BLE001
        raise RetrievalIncomplete(what, "%s: %s" % (type(exc).__name__,
                                                    exc)) from exc


# THE NEWEST OBSERVED MARKOUT PER POSITION. Newest by horizon length,
# so a position with a 300S mark is valued on it rather than on its
# 30S; a position with NO observed mark appears here not at all, which
# is what keeps it out of the P&L rather than in it at zero.
_MARKED = """
    SELECT DISTINCT ON (m.experimental_decision_id)
           m.experimental_decision_id, m.horizon,
           m.mid_markout_usd, m.executable_markout_usd, m.observed_at
      FROM bettor_experimental_markouts m
     WHERE m.status = 'OBSERVED'
       AND m.executable_markout_usd IS NOT NULL
     ORDER BY m.experimental_decision_id,
              CASE m.horizon WHEN '300S' THEN 3 WHEN '60S' THEN 2
                             WHEN '30S' THEN 1 ELSE 0 END DESC
"""

_HEADLINE = """
    WITH marked AS (%s)
    SELECT d.latency_regime,
           count(*) FILTER (WHERE d.position_id IS NOT NULL) AS trades,
           count(*) AS decisions,
           coalesce(sum(d.executed_notional_usd), 0)       AS entry_played,
           coalesce(sum(d.unfilled_notional_usd), 0)       AS unfilled,
           coalesce(sum(d.intended_notional_usd), 0)       AS intended,
           count(*) FILTER (WHERE m.experimental_decision_id IS NOT NULL)
                                                           AS marked_n,
           count(*) FILTER (WHERE d.position_id IS NOT NULL
                              AND m.experimental_decision_id IS NULL)
                                                           AS unmarked_n,
           sum(m.executable_markout_usd)                   AS pnl_exec,
           sum(m.mid_markout_usd)                          AS pnl_mid,
           count(*) FILTER (WHERE m.executable_markout_usd > 0) AS wins,
           count(*) FILTER (WHERE m.executable_markout_usd < 0) AS losses,
           sum(m.executable_markout_usd) FILTER (
               WHERE d.decision_timestamp >= date_trunc('day', now()))
                                                           AS pnl_today,
           max(d.decision_timestamp)                       AS last_decision
      FROM bettor_experimental_decisions d
      LEFT JOIN marked m ON m.experimental_decision_id
                          = d.experimental_decision_id
     GROUP BY d.latency_regime
""" % _MARKED

_BY_EXPERIMENT = """
    WITH marked AS (%s)
    SELECT d.experiment_id, d.latency_regime, d.control_id,
           count(*) FILTER (WHERE d.position_id IS NOT NULL) AS trades,
           coalesce(sum(d.executed_notional_usd), 0)       AS entry_played,
           count(*) FILTER (WHERE m.experimental_decision_id IS NOT NULL)
                                                           AS marked_n,
           sum(m.executable_markout_usd)                   AS pnl_exec,
           count(*) FILTER (WHERE m.executable_markout_usd > 0) AS wins,
           count(*) FILTER (WHERE d.action = 'NO_TRADE')   AS no_trades
      FROM bettor_experimental_decisions d
      LEFT JOIN marked m ON m.experimental_decision_id
                          = d.experimental_decision_id
     GROUP BY d.experiment_id, d.latency_regime, d.control_id
     ORDER BY d.experiment_id
""" % _MARKED

_POSITIONS = """
    SELECT status, count(*) AS n,
           coalesce(sum(entry_notional_usd), 0) AS notional,
           coalesce(sum(entry_qty), 0)          AS qty
      FROM bettor_experimental_positions GROUP BY status
"""

_REFUSALS = """
    SELECT execution_status, action, count(*) AS n,
           coalesce(max(left(why, 140)), '') AS why
      FROM bettor_experimental_decisions
     WHERE position_id IS NULL
     GROUP BY execution_status, action ORDER BY count(*) DESC LIMIT 20
"""

# WHY THE MARKOUTS THAT DID NOT LAND ARE ON THE PANEL. Under the bridge
# most short-horizon markouts miss their tolerance, and that is a fact
# about the regime rather than a gap in the data. Hiding it would make
# the coverage look complete.
_MARKOUT_COVERAGE = """
    SELECT horizon, status, count(*) AS n,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY observed_lag_ms)
               AS median_lag_ms,
           max(tolerance_ms) AS tolerance_ms
      FROM bettor_experimental_markouts
     GROUP BY horizon, status ORDER BY horizon, status
"""

_TAPE = """
    SELECT d.experimental_decision_id, d.experiment_id, d.market_id,
           d.outcome_leg, d.action, d.execution_status,
           d.decision_timestamp, d.arrival_timestamp,
           d.signal_strength, d.intended_notional_usd,
           d.executed_notional_usd, d.unfilled_notional_usd,
           d.filled_qty, d.vwap, d.identity_binding_status,
           d.latency_regime, d.observed_arrival_latency_ms,
           d.l2_book_sha, d.l2_evidence_id, d.position_id,
           d.eligible_population_id
      FROM bettor_experimental_decisions d
     ORDER BY d.decision_timestamp DESC LIMIT $1
"""


def _environment() -> dict:
    return {
        "lane": LANE,
        "label": "BETTOR EXPERIMENTAL SHADOW",
        "disclosureLines": list(DISCLOSURE_LINES),
        "notDecisionGrade": True,
        "realOrderActivity": "NONE",
        "capitalAtRisk": 0,
        "shadowMode": sh.SHADOW_MODE,
        "pnlBasis": PNL_BASIS,
        "minSampleForRanking": MIN_SAMPLE_FOR_RANKING,
        "separateLaneNote": (
            "This lane is NOT the decision-grade lane. BETTOR_EV_SHADOW "
            "keeps its own frozen framework and may remain at zero "
            "trades; nothing here promotes anything, and no figure on "
            "this panel is evidence for the decision-grade lane."),
        "regimeNote": (
            "Figures are reported PER LATENCY REGIME and are never "
            "pooled. A book fetched through the GitHub bridge minutes "
            "after the decision is a different execution environment "
            "from a persistent worker's, and X1's horizon is 60 "
            "seconds -- so bridge-era results are a weak test of X1."),
    }


def _headline(r) -> dict:
    marked = int(r["marked_n"] or 0)
    entry = _f(r["entry_played"]) or 0.0
    pnl = _f(r["pnl_exec"])
    wins = int(r["wins"] or 0)
    decided = wins + int(r["losses"] or 0)
    return {
        "latencyRegime": r["latency_regime"],
        # §13's tiles, in its own words.
        "netShadowPnlUsd": pnl,
        "todayPnlUsd": _f(r["pnl_today"]),
        "midPnlUsd": _f(r["pnl_mid"]),
        "entryNotionalPlayedUsd": entry,
        "unfilledNotionalUsd": _f(r["unfilled"]),
        "intendedNotionalUsd": _f(r["intended"]),
        "returnOnEntryNotional": (None if not entry or pnl is None
                                  else pnl / entry),
        "trades": int(r["trades"] or 0),
        "decisions": int(r["decisions"] or 0),
        # WIN RATE OVER THE MARKED ONES ONLY, and it says so.
        "winRate": (None if decided == 0 else wins / decided),
        "winRateSample": decided,
        "markedPositions": marked,
        # NOT ZERO P&L -- NO P&L. These are the positions whose value
        # has not been measured, and they are excluded from every
        # figure above rather than counted flat.
        "unmarkedPositions": int(r["unmarked_n"] or 0),
        "sufficientSample": decided >= MIN_SAMPLE_FOR_RANKING,
        "lastDecision": _iso(r["last_decision"]),
    }


async def summary(pool) -> dict:
    """The panel's headline, its leaderboard and its refusals."""
    regimes = await _guard(pool, "experimental headline", _HEADLINE)
    by_exp = await _guard(pool, "experimental leaderboard", _BY_EXPERIMENT)
    positions = await _guard(pool, "experimental positions", _POSITIONS)
    refusals = await _guard(pool, "experimental refusals", _REFUSALS)
    coverage = await _guard(pool, "markout coverage", _MARKOUT_COVERAGE)

    # THE FUNNEL (owner 2026-09-20): "instrument the funnel so
    # management can see why activity is or is not occurring." It is
    # computed from the same append-only rows the rest of this panel
    # reads, never from a separate counter that could disagree with
    # them. A window of 24 h so the screen answers about today.
    try:
        funnel = await xstore.funnel(pool, window_s=FUNNEL_WINDOW_S)
    except Exception as exc:                                   # noqa: BLE001
        # A PANEL THAT LOSES ONE SECTION STILL SHOWS THE REST. The
        # failure is named on the screen rather than rendering zeros,
        # because a funnel of zeros and an unavailable funnel are
        # different facts and only one of them means "no activity".
        funnel = {"unavailable": "%s: %s" % (type(exc).__name__, exc)}

    leaderboard = []
    for r in by_exp:
        marked = int(r["marked_n"] or 0)
        entry = _f(r["entry_played"]) or 0.0
        pnl = _f(r["pnl_exec"])
        leaderboard.append({
            "experimentId": r["experiment_id"],
            "latencyRegime": r["latency_regime"],
            "isControlFor": r["control_id"],
            "trades": int(r["trades"] or 0),
            "noTrades": int(r["no_trades"] or 0),
            "entryNotionalPlayedUsd": entry,
            "netShadowPnlUsd": pnl,
            "returnOnEntryNotional": (None if not entry or pnl is None
                                      else pnl / entry),
            "markedPositions": marked,
            "wins": int(r["wins"] or 0),
            # §11: printed, never ranked on, until the sample is real.
            "sufficientSample": marked >= MIN_SAMPLE_FOR_RANKING,
            "rankable": marked >= MIN_SAMPLE_FOR_RANKING,
        })

    return {
        "environment": _environment(),
        "registry": reg.registry_report(),
        "regimes": [_headline(r) for r in regimes],
        "leaderboard": leaderboard,
        "positions": [{"status": r["status"], "n": int(r["n"]),
                       "entryNotionalUsd": _f(r["notional"]),
                       "qty": _f(r["qty"])} for r in positions],
        "refusals": [{"executionStatus": r["execution_status"],
                      "action": r["action"], "n": int(r["n"]),
                      "why": r["why"] or None} for r in refusals],
        # §11's funnel, in the directive's own names so the screen and
        # the order use one vocabulary.
        "funnel": funnel,
        "markoutCoverage": [
            {"horizon": r["horizon"], "status": r["status"],
             "n": int(r["n"]), "medianLagMs": _f(r["median_lag_ms"]),
             "toleranceMs": _f(r["tolerance_ms"]),
             "horizonOrder": [h for h, _s in mk.HORIZONS].index(r["horizon"])
             if r["horizon"] in [h for h, _s in mk.HORIZONS] else 99}
            for r in coverage],
        "asOf": datetime.now(tz=timezone.utc).isoformat(),
    }


async def tape(pool, *, limit=100) -> dict:
    """§13's live tape: every decision, action and execution apart."""
    rows = await _guard(pool, "experimental tape", _TAPE,
                        max(1, min(int(limit), 300)))
    return {
        "environment": _environment(),
        "decisions": [{
            "experimentalDecisionId": r["experimental_decision_id"],
            "experimentId": r["experiment_id"],
            "marketId": r["market_id"],
            "outcomeLeg": r["outcome_leg"],
            # ACTION AND EXECUTION STAY APART ON THE SCREEN TOO. A
            # BUY_NO that could not be executed reads as BUY_NO beside
            # BLOCKED_IDENTITY, never as NO_TRADE.
            "action": r["action"],
            "executionStatus": r["execution_status"],
            "decisionTimestamp": _iso(r["decision_timestamp"]),
            "arrivalTimestamp": _iso(r["arrival_timestamp"]),
            "signalStrength": _f(r["signal_strength"]),
            "intendedNotionalUsd": _f(r["intended_notional_usd"]),
            "executedNotionalUsd": _f(r["executed_notional_usd"]),
            "unfilledNotionalUsd": _f(r["unfilled_notional_usd"]),
            "filledQty": _f(r["filled_qty"]),
            "vwap": _f(r["vwap"]),
            "identityBindingStatus": r["identity_binding_status"],
            "latencyRegime": r["latency_regime"],
            "observedArrivalLatencyMs": _f(r["observed_arrival_latency_ms"]),
            "l2BookSha": r["l2_book_sha"],
            "l2EvidenceId": r["l2_evidence_id"],
            "positionId": r["position_id"],
            "eligiblePopulationId": r["eligible_population_id"],
        } for r in rows],
        "asOf": datetime.now(tz=timezone.utc).isoformat(),
    }
