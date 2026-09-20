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
from .. import shadow_markout_timing as tm
from .. import shadow_experimental_capital as cap
from .. import shadow_latency_integrity as lat
from .. import shadow_position_lifecycle as lc
from .. import shadow_reentry_guard as rg
from .. import shadow_exit_semantics as xsem
from .. import shadow_exit_spec as xspec
from .. import shadow_experiment_versions as ver
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

# AND EACH ROW MUST HAVE BEEN OBSERVED WHEN IT CLAIMS (owner
# 2026-09-20): "A row labelled '60S' must not become performance
# evidence merely because TARGET_HORIZON = 60S." The gate below is
# re-derived from the recorded timestamps on every read -- it never
# consults `status`, because a status column is a conclusion written
# earlier and the directive makes the clock authoritative.
_MARKED = """
    SELECT DISTINCT ON (m.experimental_decision_id)
           m.experimental_decision_id, m.horizon,
           m.mid_markout_usd, m.executable_markout_usd, m.observed_at
      FROM bettor_experimental_markouts m
      JOIN bettor_experimental_decisions d
        ON d.experimental_decision_id = m.experimental_decision_id
     WHERE m.executable_markout_usd IS NOT NULL
       AND %s
     ORDER BY m.experimental_decision_id,
              CASE m.horizon WHEN '300S' THEN 3 WHEN '60S' THEN 2
                             ELSE 0 END DESC
""" % tm.performance_eligible_sql()


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
      JOIN bettor_experimental_decisions d
        ON d.experimental_decision_id = m.experimental_decision_id
     WHERE m.executable_markout_usd IS NOT NULL
       AND %s
     GROUP BY m.experimental_decision_id
""" % tm.TIMING_ELIGIBLE_SQL

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
        # THE CLASS, not just the portfolio. X1 is experimental model
        # evidence; only the EV lane's own class is decision-grade.
        "performanceClass": PERFORMANCE_CLASS.get(experiment, experiment),
        "portfolio": CLASS_LABEL.get(
            PERFORMANCE_CLASS.get(experiment), experiment),
        # CORRECTED 2026-09-20: this was True for X1, which attributed
        # an experimental model's economics to the BETTOR EV engine.
        # Only the decision-grade class is EV performance, and neither
        # experiment in this lane is it.
        "isBettorEvPerformance": (
            PERFORMANCE_CLASS.get(experiment) == DECISION_GRADE_CLASS),
        "decisionGrade": False,
        "latencyRegime": r["latency_regime"],
        # §13's tiles, in its own words.
        # §3 (owner 2026-09-20): "Do not label the 300S markout simply
        # 'P&L' without its status." These are EXECUTABLE MARKOUTS --
        # where the position could have been exited on the observed
        # book at that instant. Nothing has exited and nothing has
        # settled, so realized and settled are NOT_APPLICABLE rather
        # than zero. The legacy key is kept beside the correct one so
        # an older reader is not silently broken, and it carries its
        # status rather than standing alone.
        "currentExecutableMarkoutUsd": pnl,
        "currentExecutableMarkoutStatus": cap.UNREALIZED_MARKOUT,
        "realizedPnlUsd": None,
        "realizedPnlStatus": cap.NOT_APPLICABLE,
        "settledPnlUsd": None,
        "settledPnlStatus": cap.NOT_APPLICABLE,
        "netShadowPnlUsd": pnl,
        "netShadowPnlIsMarkoutNotRealized": True,
        "todayExecutableMarkoutUsd": _f(r["pnl_today"]),
        "todayPnlUsd": _f(r["pnl_today"]),
        "midMarkoutUsd": _f(r["pnl_mid"]),
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

# ── THREE PERFORMANCE CLASSES, NEVER ONE NUMBER ──────────────────────
#
# Owner directive 2026-09-20: "X1 IS AN EXPERIMENTAL BETTOR MODEL. X1
# IS NOT YET THE DECISION-GRADE BETTOR EV ENGINE... The current X1
# economics must therefore be displayed as BETTOR X1 - EXPERIMENTAL
# SHADOW, not BETTOR EV ENGINE P&L."
#
# The distinction is an attribution claim, not a label: X1 emits a
# signed drift and nothing else. It produces no P_BETTOR, no P_FILL
# and no CONSERVATIVE_ACTION_EV, and every one of its rows carries
# not_decision_grade, so calling its economics BETTOR EV performance
# would attribute an experimental model's result to an engine that has
# not yet made a trade.

CLASS_EV = "BETTOR_EV_SHADOW"
CLASS_X1 = "BETTOR_X1_EXPERIMENTAL_SHADOW"
CLASS_CONTROL = "X1C_NULL_CONTROL"

PERFORMANCE_CLASS = {
    X1_MODEL: CLASS_X1,
    X1_CONTROL: CLASS_CONTROL,
}

CLASS_LABEL = {
    CLASS_EV: "BETTOR EV SHADOW",
    CLASS_X1: "BETTOR X1 EXPERIMENTAL",
    CLASS_CONTROL: "X1C NULL CONTROL",
}

# Which class, if any, may ever be described as BETTOR EV performance.
DECISION_GRADE_CLASS = CLASS_EV

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


_ELIGIBLE_BY_EXPERIMENT = """
    SELECT d.experiment_id, m.horizon,
           sum(m.executable_markout_usd) AS executable
      FROM bettor_experimental_markouts m
      JOIN bettor_experimental_decisions d
        ON d.experimental_decision_id = m.experimental_decision_id
     WHERE m.executable_markout_usd IS NOT NULL
       AND %s
     GROUP BY d.experiment_id, m.horizon
""" % tm.performance_eligible_sql()


def _version_tile(experiment_id: str) -> dict:
    """What management must understand about THIS version (§8).

    A version whose frozen contract cannot close a position is not a
    book of live positions awaiting a discretionary exit. It is a
    finished historical cohort whose exits were never available, and
    the tile says exactly that rather than leaving it to be inferred
    from a status column elsewhere.
    """
    declaration = reg.BY_ID.get(experiment_id, {})
    verdict = ver.position_creation(experiment_id, declaration=declaration)
    complete = xp.lifecycle_completeness(declaration) \
        if declaration else xp.LIFECYCLE_EXIT_INCOMPLETE
    historical = not verdict["permitted"] \
        and complete != xp.LIFECYCLE_COMPLETE
    return {
        "EXPERIMENT_VERSION": experiment_id,
        "NEW_POSITION_CREATION": (
            "PERMITTED" if verdict["permitted"] else "BLOCKED"),
        "BLOCKER": None if verdict["permitted"] else verdict["decision"],
        "BLOCKER_IS_PERFORMANCE_BASED": verdict.get(
            "performanceBased", False),
        "LIFECYCLE": complete,
        "STATUS": ("HISTORICAL_PROSPECTIVE_EXPERIMENT" if historical
                   else "PROSPECTIVE"),
        "EXIT_POLICY_AT_ENTRY": (
            "INCOMPLETE" if complete != xp.LIFECYCLE_COMPLETE else
            "COMPLETE"),
        "REALIZED_PNL_USD": None,
        "REALIZED_PNL_STATUS": cap.NOT_APPLICABLE,
        "supersededBy": verdict.get("supersededBy"),
        "why": verdict.get("why"),
        "readerNote": (
            "these positions are NOT live positions waiting for a "
            "discretionary exit. The exit mechanism was unavailable "
            "when they opened and the frozen contract cannot complete "
            "them; the figure beside them is an executable markout, "
            "never realized P&L" if historical else None),
    }


def _successor_tile() -> dict:
    """The successor: complete, declared, and not trading (§8/§10)."""
    ids = [e["experimentId"] for e in reg.EXPERIMENTS
           if e.get("supersedes")]
    return {
        "SUCCESSOR_EXPERIMENT_IDS": ids,
        "POSITIONS": 0,
        "STATUS": "PROSPECTIVE",
        "READINESS": xp.DECLARED_AWAITING_REVIEW,
        "NEW_POSITION_CREATION": "BLOCKED",
        "BLOCKER": ver.AWAITING_REVIEW,
        "LIFECYCLE": xp.LIFECYCLE_COMPLETE,
        "ENTRY_AND_EXIT_FROZEN": True,
        "MAX_EXIT_OBSERVATION_DELAY_MS":
            xspec.MAX_EXIT_OBSERVATION_DELAY_MS,
        "MAX_EXIT_DELAY_BASIS": xspec.MAX_EXIT_DELAY_BASIS,
        "exitSemantics": xspec.exit_semantics(),
        "completeness": xspec.completeness(),
        "declarations": [
            {"experimentId": e["experimentId"],
             "experimentSha": e["experimentSha"],
             "supersedes": e["supersedes"],
             "role": e["role"]}
            for e in reg.EXPERIMENTS if e.get("supersedes")],
        "why": ("declared completely -- entry carried forward "
                "unchanged, exit frozen for the first time -- and "
                "holding for review before its first position"),
    }


async def _management(pool) -> dict:
    """The three performance classes, each with its own economics.

    ONE CLASS PER BLOCK AND NO TOTAL ACROSS THEM. The panel cannot
    render a combined figure because this function never computes one:
    there is no "all experiments" branch to fall back to.
    """
    positions = await cap.position_events(pool)
    eligible = await _guard(pool, "eligible markouts by experiment",
                            _ELIGIBLE_BY_EXPERIMENT)
    latency = await lat.latency_rows(pool)
    # §1/§13: state folded from the append-only event ledger, never
    # read off the immutable position row's frozen status column.
    folded = lc.by_position(await lc.events(pool))

    by_experiment = {}
    for p in positions:
        by_experiment.setdefault(p["experiment_id"], []).append(p)
    markouts = {}
    for r in eligible:
        markouts.setdefault(r["experiment_id"], {})[r["horizon"]] = _f(
            r["executable"])

    now = datetime.now(tz=timezone.utc)
    classes = {}

    # CLASS A: the decision-grade EV lane. It keeps NO rows in this
    # store, and that is the point -- this lane is the experimental
    # one. Its zeros are stated explicitly rather than left as an
    # absence a reader might fill in with X1's numbers.
    classes[CLASS_EV] = {
        "label": CLASS_LABEL[CLASS_EV],
        "DECISION_GRADE": True,
        "ENTRY_NOTIONAL_PLAYED_USD": 0.0,
        "POSITIONS": 0,
        "ELIGIBLE_TRADES": 0,
        "NET_PNL_USD": 0.0,
        "RETURN": cap.NOT_APPLICABLE,
        "STATUS": "LEARNING / NO DECISION-GRADE TRADE YET",
        "note": ("the BETTOR EV SHADOW lane writes to its own ledger "
                 "and has produced no trade; X1's economics are never "
                 "shown here"),
    }

    for experiment, rows in sorted(by_experiment.items()):
        klass = PERFORMANCE_CLASS.get(experiment, experiment)
        capital = cap.capital(rows, now=now)
        conc = cap.concentration(rows)
        econ = cap.economics(
            entry_notional_usd=capital["ENTRY_NOTIONAL_PLAYED_USD"],
            eligible_markouts=markouts.get(experiment, {}))
        mine = [v for v in latency if v["EXPERIMENT_ID"] == experiment]
        settlement = await cap.settlement_semantics(
            pool, [p["market_id"] for p in rows])
        classes[klass] = {
            "label": CLASS_LABEL.get(klass, experiment),
            "experimentId": experiment,
            # X1 IS NOT THE EV ENGINE. It emits a signed drift and no
            # P_BETTOR, P_FILL or CONSERVATIVE_ACTION_EV, and every row
            # is not_decision_grade.
            "DECISION_GRADE": False,
            "IS_NULL_CONTROL": experiment == X1_CONTROL,
            # §13: the position lifecycle, folded from the events.
            "lifecycle": lc.lifecycle(folded, experiment=experiment),
            # §6: capital that knows a close frees dollars. Identical
            # to the entry-only walk while nothing has closed, correct
            # the moment something does.
            "capitalWithReleases": lc.capital_with_releases(
                folded, experiment=experiment, now=now),
            "capital": capital,
            "concentration": conc,
            "economics": econ,
            "LATENCY_STATUS": lat.census(mine)["byStatus"],
            "latencyUsableForEconomics": lat.census(
                mine)["usableForEconomics"],
            "SETTLEMENT_SEMANTICS_STATUS": settlement[
                "SETTLEMENT_SEMANTICS_STATUS"],
            "settlement": settlement,
            # §8 (owner 2026-09-20): "Do not make management think the
            # V1 positions are still normal live positions waiting for
            # discretionary exits." The version's own status travels
            # with its economics, on the same tile.
            "version": _version_tile(experiment),
        }

    return {
        "classes": classes,
        # §2/§16: the exit rule is declared but two of its semantics are
        # absent from the frozen spec, so the trigger is not built. The
        # blockage is on the panel rather than in a comment.
        "exitMechanism": xsem.exit_mechanism_status(),
        # §1/§2 (owner 2026-09-20): which versions may still open a
        # position, and the successor that is declared but not armed.
        "versions": ver.report(reg.EXPERIMENTS),
        "successor": _successor_tile(),
        # §10: the historical re-entry violation, preserved.
        "reentry": {
            "X1_REENTRY_ENFORCEMENT_STATUS": "ENFORCED_BEFORE_CREATION",
            "frozenClause": rg.REENTRY_CLAUSE,
            "boundary": "gap < horizon is refused; gap == horizon is "
                        "permitted",
            "historicalViolations": await rg.historical_violations(pool),
        },
        "neverCombined": (
            "BETTOR EV SHADOW, BETTOR X1 EXPERIMENTAL and X1C NULL "
            "CONTROL are three separate performance classes. Their "
            "P&L, notional, positions, win rate and returns are never "
            "combined. X1 is experimental model evidence, not BETTOR "
            "EV engine performance; X1C is always-long control "
            "evidence and is not performance at all."),
        "disclosure": "EXPERIMENTAL SHADOW / NO REAL CAPITAL",
        "REAL_ORDER_ACTIVITY": "NONE",
        "REAL_CAPITAL_AT_RISK": 0,
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

    # EVERY MARKOUT'S TIMING, ROW BY ROW (owner 2026-09-20). The eight
    # fields are derived from the recorded timestamps on each read, so
    # the panel states WHY a row is or is not performance evidence
    # rather than asserting that it is because of its label.
    try:
        timing_rows = await tm.timing_rows(pool)
    except Exception as exc:                                   # noqa: BLE001
        timing_rows = {"unavailable": "%s: %s" % (type(exc).__name__, exc)}

    # §4/§5/§6/§7 (owner 2026-09-20). Capital from the position event
    # series, never inferred from entry notional; concentration so four
    # entries in one market are not read as four samples; the clock
    # check so no latency number is manufactured; and settlement
    # semantics kept apart from instrument identity.
    try:
        management = await _management(pool)
    except Exception as exc:                                   # noqa: BLE001
        management = {"unavailable": "%s: %s" % (type(exc).__name__, exc)}

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
        # §2/§12: the management panel, three classes, never combined.
        "management": management,
        # §1 OF THE 2026-09-20 DIRECTIVE: a row is eligible because of
        # WHEN IT WAS OBSERVED, never because of its label. Both gates
        # are reported separately so a reader can see which one
        # refused a row -- the horizon's observability, or this row's
        # own realized timing against the frozen tolerance.
        "markoutTiming": ({"unavailable": timing_rows["unavailable"]}
                          if isinstance(timing_rows, dict) else {
            "rule": ("PERFORMANCE_ELIGIBLE requires the recorded "
                     "timestamps to satisfy the frozen contract: "
                     "target_at == decision_timestamp + horizon, and "
                     "|observed_at - target_at| <= tolerance, and an "
                     "L2 book actually recorded. The label is never "
                     "sufficient and the status column is never read."),
            "noInterpolation": True,
            "noNearestObservationOutsideTolerance": True,
            "noZeroPnlSubstitution": True,
            "noDeletion": True,
            "census": list(tm.census(timing_rows).values()),
            # EVIDENCE ABOUT THE WRITE PATH, not part of the gate. An
            # empty list is the expected state and is itself a result.
            "writerDisagreements": tm.writer_disagreements(timing_rows),
            "rows": [{k: (_iso(v) if hasattr(v, "isoformat") else v)
                      for k, v in r.items()} for r in timing_rows],
        }),
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
