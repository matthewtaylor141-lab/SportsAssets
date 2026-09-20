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
from .. import shadow_markout_observability as ob
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
# WHICH HORIZONS MAY CARRY A P&L NUMBER. Owner 2026-09-20, from a
# measured capture cadence of P50 60.95s / P95 63.98s: the 30S horizon
# is UNOBSERVABLE at that frequency, because its 30s tolerance reaches
# its 30s horizon and the admissible window therefore opens at the
# decision instant -- a "30-second markout" is permitted to be the
# entry book at zero elapsed time.
#
# THE ROWS ARE NOT TOUCHED. Every 30S markout ever written stays
# exactly as written: not deleted, not rewritten, not converted to
# zero. They simply stop being eligible to produce a performance
# conclusion, and this statement is where that takes effect.
#
# A POSITION WHOSE ONLY MARK IS 30S BECOMES UNMARKED, not flat. The
# panel already reports unmarked positions as having NO P&L rather
# than zero P&L, which is the honest place for them.
_PERFORMANCE_HORIZONS = "', '".join(sorted(ob.observable_horizons()))

_MARKED = """
    SELECT DISTINCT ON (m.experimental_decision_id)
           m.experimental_decision_id, m.horizon,
           m.mid_markout_usd, m.executable_markout_usd, m.observed_at
      FROM bettor_experimental_markouts m
     WHERE m.status = 'OBSERVED'
       AND m.executable_markout_usd IS NOT NULL
       AND m.horizon IN ('%s')
     ORDER BY m.experimental_decision_id,
              CASE m.horizon WHEN '300S' THEN 3 WHEN '60S' THEN 2
                             ELSE 0 END DESC
""" % _PERFORMANCE_HORIZONS


# ONE ROW PER DECISION WITH EACH HORIZON IN ITS OWN COLUMN. The excess
# markouts have to compare like with like: X1's 30S against the
# control's 30S, never X1's 300S against the control's 30S because one
# of them happened to be the longest horizon that landed. `_MARKED`
# deliberately collapses to the longest OBSERVED horizon, which is the
# right thing for a headline P&L and the wrong thing for a per-horizon
# difference.
_MARKED_WITH_HORIZONS = """
    SELECT m.experimental_decision_id,
           max(m.executable_markout_usd) FILTER (WHERE m.horizon = '30S')
               AS h30,
           max(m.executable_markout_usd) FILTER (WHERE m.horizon = '60S')
               AS h60,
           max(m.executable_markout_usd) FILTER (WHERE m.horizon = '300S')
               AS h300,
           max(m.executable_markout_usd) FILTER (
               WHERE m.horizon = '300S') AS executable_markout_usd
      FROM bettor_experimental_markouts m
     WHERE m.status = 'OBSERVED'
       AND m.executable_markout_usd IS NOT NULL
     GROUP BY m.experimental_decision_id
"""

_HEADLINE = """
    WITH marked AS (%s)
    -- EXPERIMENT_ID IS IN THE GROUPING, and this is load-bearing
    -- (owner 2026-09-20). Grouped by regime alone, this statement
    -- summed X1_SHORT_HORIZON_DIRECTION and X1C_NULL_CONTROL into one
    -- set of trades, entry notional and P&L -- and the moment the
    -- control opened the first production position that combined
    -- number WAS the panel's headline. X1C is a frozen always-long
    -- counterfactual, not BETTOR EV performance, and the two must
    -- never appear as one figure.
    SELECT d.experiment_id, d.latency_regime,
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
     GROUP BY d.experiment_id, d.latency_regime
     ORDER BY d.experiment_id, d.latency_regime
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
    -- ALSO SPLIT BY EXPERIMENT. A capital-deployed figure that adds the
    -- control's notional to the model's describes a portfolio nobody
    -- runs.
    SELECT experiment_id, status, count(*) AS n,
           coalesce(sum(entry_notional_usd), 0) AS notional,
           coalesce(sum(entry_qty), 0)          AS qty
      FROM bettor_experimental_positions
     GROUP BY experiment_id, status
     ORDER BY experiment_id, status
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
    experiment = r["experiment_id"]
    return {
        "experimentId": experiment,
        # THE CONTROL IS LABELLED ON ITS OWN TILE. A reader who sees
        # only "TRADES 1 / PLAYED $805" has no way to know that figure
        # belongs to a frozen always-long counterfactual rather than to
        # the model, and that is the single most misreadable number on
        # this panel.
        "isNullControl": experiment == X1_CONTROL,
        "portfolio": ("X1C NULL CONTROL" if experiment == X1_CONTROL
                      else "X1 MODEL" if experiment == X1_MODEL
                      else experiment),
        "isBettorEvPerformance": experiment != X1_CONTROL,
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



# ── X1 VS CONTROL, ON COMMON SUPPORT ONLY ────────────────────────────
#
# Owner 2026-09-20: report COMMON_SUPPORT_N, X1_EXCESS_PNL,
# X1_EXCESS_RETURN and the excess markouts -- and never sum the two
# portfolios.
#
# COMMON SUPPORT IS THE WHOLE POINT. An "excess" computed over two
# non-overlapping sets is not an excess; it is a comparison of two
# different games. X1 and its control decide the SAME opportunity --
# they share `experimental_observation_id` by construction, because the
# control exists to be run on exactly the population X1 sees -- so the
# pairing key is that id and nothing else. An opportunity only one of
# them acted on is outside common support and contributes to neither
# side of the difference.
#
# THE EXCESS IS A DIFFERENCE, NOT A RATIO OF TOTALS. X1's return minus
# the control's return on the same opportunities answers "did the model
# add anything to always being long"; dividing one portfolio's P&L by
# the other's would answer nothing.

X1_MODEL = "X1_SHORT_HORIZON_DIRECTION"
X1_CONTROL = "X1C_NULL_CONTROL"

_VS_CONTROL = """
    WITH marked AS (%s),
    paired AS (
        SELECT d.experimental_observation_id AS obs,
               d.experiment_id,
               d.executed_notional_usd       AS entry,
               m.executable_markout_usd      AS pnl,
               m.h30, m.h60, m.h300
          FROM bettor_experimental_decisions d
          LEFT JOIN marked m ON m.experimental_decision_id
                              = d.experimental_decision_id
         WHERE d.experimental_observation_id IS NOT NULL
           AND d.experiment_id IN ($1, $2)
           AND COALESCE(d.executed_notional_usd, 0) > 0
    ),
    both_sides AS (
        SELECT obs
          FROM paired
         GROUP BY obs
        HAVING count(*) FILTER (WHERE experiment_id = $1) > 0
           AND count(*) FILTER (WHERE experiment_id = $2) > 0
    )
    SELECT count(DISTINCT p.obs)                            AS common_n,
           sum(p.pnl)    FILTER (WHERE p.experiment_id = $1) AS x1_pnl,
           sum(p.pnl)    FILTER (WHERE p.experiment_id = $2) AS ctl_pnl,
           sum(p.entry)  FILTER (WHERE p.experiment_id = $1) AS x1_entry,
           sum(p.entry)  FILTER (WHERE p.experiment_id = $2) AS ctl_entry,
           avg(p.h30)    FILTER (WHERE p.experiment_id = $1) AS x1_h30,
           avg(p.h30)    FILTER (WHERE p.experiment_id = $2) AS ctl_h30,
           avg(p.h60)    FILTER (WHERE p.experiment_id = $1) AS x1_h60,
           avg(p.h60)    FILTER (WHERE p.experiment_id = $2) AS ctl_h60,
           avg(p.h300)   FILTER (WHERE p.experiment_id = $1) AS x1_h300,
           avg(p.h300)   FILTER (WHERE p.experiment_id = $2) AS ctl_h300
      FROM paired p
      JOIN both_sides b ON b.obs = p.obs
""" % _MARKED_WITH_HORIZONS


def _excess(a, b):
    """a - b, or None if either side has not been measured.

    NEVER treats an unmeasured side as zero. A missing markout is not a
    markout of nothing, and subtracting it would manufacture an edge
    out of an absence.
    """
    if a is None or b is None:
        return None
    return a - b


def vs_control(r) -> dict:
    """The comparison block, or an honest empty one."""
    r = dict(r or {})
    n = int(r.get("common_n") or 0)
    x1_pnl, ctl_pnl = _f(r.get("x1_pnl")), _f(r.get("ctl_pnl"))
    x1_entry, ctl_entry = _f(r.get("x1_entry")), _f(r.get("ctl_entry"))
    x1_ret = (None if not x1_entry or x1_pnl is None else x1_pnl / x1_entry)
    ctl_ret = (None if not ctl_entry or ctl_pnl is None
               else ctl_pnl / ctl_entry)
    return {
        "COMMON_SUPPORT_N": n,
        "X1_EXCESS_PNL": _excess(x1_pnl, ctl_pnl),
        "X1_EXCESS_RETURN": _excess(x1_ret, ctl_ret),
        # 30S IS NOT REPORTED AS A DIFFERENCE. The horizon is
        # unobservable at the current capture frequency, so an "excess"
        # built from it would be arithmetic on two numbers that cannot
        # both be trusted to describe 30 seconds. The status is
        # returned in its place.
        "EXCESS_30S_MARKOUT": (
            _excess(_f(r.get("x1_h30")), _f(r.get("ctl_h30")))
            if "30S" in ob.observable_horizons() else None),
        "EXCESS_30S_MARKOUT_STATUS": ob.by_horizon()["30S"]["status"],
        "EXCESS_60S_MARKOUT": _excess(_f(r.get("x1_h60")),
                                      _f(r.get("ctl_h60"))),
        "EXCESS_300S_MARKOUT": _excess(_f(r.get("x1_h300")),
                                       _f(r.get("ctl_h300"))),
        # THE INPUTS, SO THE DIFFERENCE CAN BE CHECKED rather than
        # believed -- and so nobody reads the excess as a portfolio.
        "x1": {"pnlUsd": x1_pnl, "entryNotionalUsd": x1_entry,
               "return": x1_ret},
        "control": {"pnlUsd": ctl_pnl, "entryNotionalUsd": ctl_entry,
                    "return": ctl_ret},
        "note": ("excess is X1 minus X1C on the SAME opportunities. The "
                 "two portfolios are never summed: X1C is a frozen "
                 "always-long counterfactual, not BETTOR EV performance."),
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
        compare = vs_control(await _guard(
            pool, "x1 vs control", _VS_CONTROL, X1_MODEL, X1_CONTROL,
            one=True))
    except Exception as exc:                                   # noqa: BLE001
        compare = {"unavailable": "%s: %s" % (type(exc).__name__, exc)}

    # WHICH FOCUS MARKETS THE DATA-QUALITY RULE IS HOLDING OUT. Named
    # on the panel rather than silently absent: a market dropped from
    # the focus set with no explanation is indistinguishable from a
    # market nobody ever found, and the difference is the whole point
    # of the rule.
    try:
        excluded = await xstore.focus_excluded(pool)
    except Exception as exc:                                   # noqa: BLE001
        excluded = {"unavailable": "%s: %s" % (type(exc).__name__, exc)}

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
        "positions": [{"experimentId": r["experiment_id"],
                       "isNullControl": r["experiment_id"] == X1_CONTROL,
                       "status": r["status"], "n": int(r["n"]),
                       "entryNotionalUsd": _f(r["notional"]),
                       "qty": _f(r["qty"])} for r in positions],
        "refusals": [{"executionStatus": r["execution_status"],
                      "action": r["action"], "n": int(r["n"]),
                      "why": r["why"] or None} for r in refusals],
        # §11's funnel, in the directive's own names so the screen and
        # the order use one vocabulary.
        "funnel": funnel,
        # X1 VS CONTROL, as a DIFFERENCE on common support. The two
        # portfolios appear above under their own names and are never
        # added together anywhere on this panel.
        "x1VsControl": compare,
        "neverCombined": (
            "X1_SHORT_HORIZON_DIRECTION and X1C_NULL_CONTROL are "
            "separate portfolios. Their trades, entry notional, capital "
            "deployed, P&L, return and drawdown are never summed: X1C "
            "is a frozen always-long counterfactual research portfolio, "
            "not BETTOR EV performance."),
        # §: EACH HORIZON CARRIES ITS OWN OBSERVABILITY VERDICT, so
        # 30S never reads as a measurement beside 60S and 300S.
        "markoutObservability": {
            name: {"status": v["status"],
                   "eligibleForPerformance": v["eligibleForPerformance"],
                   "captureGuaranteed": v["captureGuaranteed"],
                   "earliestElapsedS": v["earliestElapsedS"],
                   "why": v["why"]}
            for name, v in ob.by_horizon().items()},
        "performanceHorizons": list(ob.observable_horizons()),
        # §: THE FROZEN DATA-QUALITY EXCLUSION, with its reason and the
        # sha of the rule text, so a reader can tell that a market left
        # for want of a leg-specific feature -- never for performance.
        "focusExclusions": {
            "rule": xstore.FOCUS_EXCLUSION_RULE,
            "ruleSha": xstore.FOCUS_EXCLUSION_RULE_SHA,
            "minSamples": xstore.FOCUS_EXCLUSION_MIN_SAMPLES,
            "basis": "DATA_QUALITY_ONLY",
            "readsNoModelOutput": True,
            "excluded": ([] if isinstance(excluded, dict)
                         else [dict(e, newest=_iso(e["newest"]))
                               for e in excluded]),
            "unavailable": (excluded.get("unavailable")
                            if isinstance(excluded, dict) else None),
        },
        "captureCadence": ob.observability(
            300, 150)["capture"],
        "markoutCoverage": [
            {"horizon": r["horizon"], "status": r["status"],
             "observability": ob.by_horizon().get(
                 r["horizon"], {}).get("status"),
             "eligibleForPerformance": ob.by_horizon().get(
                 r["horizon"], {}).get("eligibleForPerformance", False),
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
