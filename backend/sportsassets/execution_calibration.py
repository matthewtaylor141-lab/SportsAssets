"""LIVE EXECUTION CALIBRATION, THE THREE EVIDENCE CLASSES SIDE BY SIDE (R30C).
PURE: the summaries, the LIVE rule's application and the registry of every
execution estimate on the LIVE decision path. The bounded SELECTs that build
the observations live in api/command_execution_calibration.py (the read
model GET /api/command/execution-calibration); nothing here reads, writes or
sends anything.

For the canonical decision intents in a window, the same execution questions
answered separately for each class (execution_evidence) -- never pooled:

  PAPER_SIMULATION  the PAPER adapter's order (canonical_intent_executions
                    adapter PAPER -> refs.order_id -> paper_orders /
                    paper_fills): what the simulator filled, after its delay,
                    consumption ledger and queue model
  LIVE_SHADOW       the SMALL LIVE SHADOW adapter's proposal (adapter
                    SMALL_LIVE, mode SHADOW, state SHADOW_PROPOSED): the
                    live-size order it would have sent, walked through the
                    venue book observed at the decision (the intent's
                    book_obs_id) by the SAME walk the simulator uses
                    (bettor_paper_simulator.walk) -- no delay, no queue, no
                    consumption, nothing sent
  ACTUAL            small_live_order_events (source VENUE_ORDER_RECORD) of a
                    LIVE-mode execution. NONE EXIST: SMALL LIVE is SHADOW.
                    Every ACTUAL metric is UNAVAILABLE with
                    execution_evidence.R_NO_ACTUAL -- never a zero. An event
                    naming a SHADOW execution would be an integrity
                    violation (migration 233 refuses it); it is counted,
                    never used.

THE METRICS, per class (each {value, n, clusters, ci_low, ci_high,
ci_method, status, why, unit, basis}):

  fill_rate                    orders FULLY filled / terminal orders
  any_fill_rate                orders with any fill / terminal orders
  qty_fill_share               filled quantity / requested quantity
  slippage_vs_limit_pp         fill VWAP - limit, per contract, cost space
                               (<= 0: never above the limit)
  slippage_vs_decision_best_pp fill VWAP - best acquisition price of the
                               decision book
  adverse_selection_30s_pp     mid (holding side) at the reference instant
  adverse_selection_300s_pp    - mid at the first book 30 s (5 min) later;
                               positive = the price moved against the buyer.
                               Reference: the first fill (PAPER, ACTUAL),
                               the decision (LIVE_SHADOW)
  cancel_rate                  terminal orders whose remainder was cancelled
                               or expired (an IOC remainder included)
  recovery_rate                of those, the share whose opportunity
                               (opportunity_tournament.opportunity_key) was
                               filled by a later intent of the same class
                               within RECOVERY_WINDOW_S

INTERVALS are 95% and EVENT-CLUSTERED (orders on one event are not
independent): proportions use Wilson at the cluster effective sample size,
means a cluster-robust t interval; with fewer than two independent events
there is no interval at all. Below execution_evidence.MIN_N INDEPENDENT
EVENTS (or without an interval) a figure is INSUFFICIENT_SAMPLE: shown,
never used for LIVE.

THE LIVE ESTIMATE per metric (`live_estimates`) is execution_evidence's rule:
the first ELIGIBLE class that is MEASURED (>= MIN_N independent events with
a clustered interval) -- ACTUAL, else (adverse selection only) LIVE_SHADOW,
else PAPER_SIMULATION -- labelled with what it was fitted on and widened by
the declared transfer penalty and floor.

THE SHADOW'S FILL FIGURES ARE TAUTOLOGICAL AT LIVE SCALE (R30C review). The
SMALL LIVE SHADOW order is sized within the decision book's depth at its
limit (paper_benchmark.size_within_edge), scaled 1:1,000 and walked here
through that SAME book with nothing consumed: it fills fully at the touch by
construction. Its fill rate, quantity share, slippage, cancel and recovery
are therefore shown in the LIVE_SHADOW column with `live_eligible: false`,
`evidence_kind: NO_FILL_EVIDENCE_DISPLAYED_BOOK_ONLY` and the reason
(execution_evidence.R_SHADOW_TAUTOLOGICAL) -- never as live execution
quality, never as a LIVE estimate. Its adverse selection (the venue book's
move after the decision) is a measurement and stays eligible.

`estimates_in_use` lists every execution estimate on the LIVE decision path
and the class it is fitted on, read from each module's own declaration --
except the micro-calibration lane (calibration_store / calibration_execute,
real-venue machinery wired to no route), whose declaration is RESTATED here
as a literal and pinned equal by the test: a module a request can reach
never imports the venue submit / cancel module.
"""
from __future__ import annotations

import math

from . import execution_evidence as EE

VERSION = "EXECUTION_CALIBRATION_V1"
WINDOW_DAYS = 14.0
MAX_INTENTS = 5000
MAX_EVENTS = 20000
HORIZONS = (30.0, 300.0)
#: how long after reference + horizon a book may be and still be the
#: horizon's book
HORIZON_TOLERANCE_S = {30.0: 30.0, 300.0: 120.0}
RECOVERY_WINDOW_S = 3600.0
PAPER_TERMINAL = ("FILLED", "CANCELED", "EXPIRED", "REJECTED")
ACTUAL_TERMINAL = ("FILLED", "CANCELLED", "REJECTED")

METRICS = ("fill_rate", "any_fill_rate", "qty_fill_share",
           "slippage_vs_limit_pp", "slippage_vs_decision_best_pp",
           "adverse_selection_30s_pp", "adverse_selection_300s_pp",
           "cancel_rate", "recovery_rate")
#: the metric family of each metric (execution_evidence.LIVE_ELIGIBLE)
FAMILY = {m: EE.FILL_FAMILY for m in METRICS}
FAMILY.update({"adverse_selection_30s_pp": EE.MARKOUT_FAMILY,
               "adverse_selection_300s_pp": EE.MARKOUT_FAMILY})
#: the unit of a mean metric's LIVE transfer floor
FLOOR_UNIT = {"qty_fill_share": EE.FRACTION,
              "slippage_vs_limit_pp": EE.USD_PER_CONTRACT,
              "slippage_vs_decision_best_pp": EE.USD_PER_CONTRACT,
              "adverse_selection_30s_pp": EE.USD_PER_CONTRACT,
              "adverse_selection_300s_pp": EE.USD_PER_CONTRACT}
KIND = {"fill_rate": "proportion", "any_fill_rate": "proportion",
        "qty_fill_share": "mean", "slippage_vs_limit_pp": "mean",
        "slippage_vs_decision_best_pp": "mean",
        "adverse_selection_30s_pp": "mean",
        "adverse_selection_300s_pp": "mean", "cancel_rate": "proportion",
        "recovery_rate": "proportion"}
UNITS = {"fill_rate": "share of terminal orders",
         "any_fill_rate": "share of terminal orders",
         "qty_fill_share": "filled / requested quantity",
         "slippage_vs_limit_pp": "USD per contract (cost space)",
         "slippage_vs_decision_best_pp": "USD per contract (cost space)",
         "adverse_selection_30s_pp": "USD per contract (cost space)",
         "adverse_selection_300s_pp": "USD per contract (cost space)",
         "cancel_rate": "share of terminal orders",
         "recovery_rate": "share of cancelled remainders"}

STORAGE = {
    EE.PAPER_SIMULATION: ("canonical_intent_executions (PAPER, SIMULATED) -> "
                          "paper_orders / paper_fills (event_source "
                          "SIMULATOR)"),
    EE.LIVE_SHADOW: ("canonical_intent_executions (SMALL_LIVE, SHADOW) "
                     "priced on paper_book_observations (the venue book "
                     "observed at the decision)"),
    EE.ACTUAL: ("small_live_order_events (source VENUE_ORDER_RECORD) of a "
                "LIVE-mode SMALL_LIVE execution"),
}


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


# ═════════════════════════════════════════════════════════════════════
# THE SUMMARY (pure)
# ═════════════════════════════════════════════════════════════════════

def mark_recoveries(obs: list) -> None:
    """Set `recovered` on every observation with a cancelled remainder:
    True when a LATER observation of the same opportunity (same class)
    within RECOVERY_WINDOW_S had any fill. Pure (mutates the dicts)."""
    by: dict = {}
    for o in obs:
        by.setdefault(o.get("opportunity_id"), []).append(o)
    for o in obs:
        if not o.get("cancelled_remainder"):
            o["recovered"] = None
            continue
        t = _num(o.get("decided_at")) or 0.0
        o["recovered"] = any(
            (_num(x.get("decided_at")) or 0.0) > t
            and (_num(x.get("decided_at")) or 0.0) <= t + RECOVERY_WINDOW_S
            and x.get("any") for x in by.get(o.get("opportunity_id"), []))


def live_eligible(cls: str, metric: str) -> bool:
    return cls in EE.LIVE_ELIGIBLE[FAMILY[metric]]


def _metric(name, stat: dict, *, basis: str, why_empty: str,
            cls: str | None = None) -> dict:
    st = EE.status_of(stat)
    out = dict(stat or {})
    out.update(metric=name, unit=UNITS[name], basis=basis, status=st,
               independent_events=EE.independent_events(out))
    if st == EE.UNAVAILABLE:
        out["why"] = out.get("why") or why_empty
    elif st == EE.INSUFFICIENT:
        out["why"] = EE.insufficient_why(stat)
    if cls is not None:
        out["live_eligible"] = live_eligible(cls, name)
        if cls == EE.LIVE_SHADOW and not out["live_eligible"]:
            # shown beside the other classes, said for what it is
            out.update(evidence_kind=EE.NO_FILL_EVIDENCE,
                       tautological_at_live_scale=True,
                       note=EE.R_SHADOW_TAUTOLOGICAL)
    return out


def summarise(cls: str, obs: list, excluded: dict, *,
              unavailable_why: str | None = None) -> dict:
    """ONE CLASS'S METRICS from its observations. Pure."""
    base = {"class": cls, "storage": STORAGE[cls],
            "live_use": EE.LIVE_USE[cls],
            # only MEASURED ACTUAL fills are live evidence; an empty ACTUAL
            # class proves nothing
            "is_proof_of_live_execution": cls == EE.ACTUAL and bool(
                [o for o in obs if o.get("terminal")]),
            "excluded": dict(excluded or {})}
    if unavailable_why is not None and not obs:
        base.update(orders=0, independent_events=0, metrics={
            m: {"metric": m, "value": None, "n": 0, "status": EE.UNAVAILABLE,
                "why": unavailable_why, "unit": UNITS[m]} for m in METRICS},
            status=EE.UNMEASURED, why=unavailable_why)
        return base
    term = [o for o in obs if o.get("terminal")]
    cl = [o.get("cluster") for o in term]
    none = "NO_TERMINAL_ORDER_IN_THE_WINDOW"
    m = {}
    m["fill_rate"] = _metric("fill_rate", EE.clustered_proportion(
        [o["full"] for o in term], cl),
        basis="fully filled / terminal", why_empty=none, cls=cls)
    m["any_fill_rate"] = _metric("any_fill_rate", EE.clustered_proportion(
        [o["any"] for o in term], cl),
        basis="any fill / terminal", why_empty=none, cls=cls)
    m["qty_fill_share"] = _metric("qty_fill_share", EE.clustered_ratio(
        [o["filled_qty"] for o in term], [o["requested_qty"] for o in term],
        cl), basis="sum filled / sum requested", why_empty=none, cls=cls)
    fl = [o for o in term if o.get("any")]
    nofill = "NO_FILLED_ORDER_IN_THE_WINDOW"

    def mean_of(f, rows):
        xs, cs = [], []
        for o in rows:
            v = f(o)
            if v is not None:
                xs.append(v)
                cs.append(o.get("cluster"))
        return EE.clustered_mean(xs, cs)
    m["slippage_vs_limit_pp"] = _metric(
        "slippage_vs_limit_pp", mean_of(
            lambda o: None if None in (o.get("vwap"), o.get("limit"))
            else o["vwap"] - o["limit"], fl),
        basis="fill VWAP - order limit", why_empty=nofill, cls=cls)
    m["slippage_vs_decision_best_pp"] = _metric(
        "slippage_vs_decision_best_pp", mean_of(
            lambda o: None if None in (o.get("vwap"),
                                       o.get("best_at_decision"))
            else o["vwap"] - o["best_at_decision"], fl),
        basis="fill VWAP - best acquisition at the decision book",
        why_empty=nofill, cls=cls)
    for h in HORIZONS:
        name = "adverse_selection_%ds_pp" % int(h)
        unmeasured = sum(1 for o in fl if (o.get("adverse") or {}).get(h)
                         is None)
        m[name] = _metric(name, mean_of(
            lambda o, h=h: (o.get("adverse") or {}).get(h), fl),
            basis=("mid at the reference instant - mid at the first book "
                   "%d..%d s later (positive = adverse)"
                   % (h, h + HORIZON_TOLERANCE_S[h])),
            why_empty=(nofill if not fl else
                       "NO_BOOK_OBSERVED_IN_THE_HORIZON_WINDOW"), cls=cls)
        m[name]["unmeasured_filled_orders"] = unmeasured
    m["cancel_rate"] = _metric("cancel_rate", EE.clustered_proportion(
        [o["cancelled_remainder"] for o in term], cl),
        basis="remainder cancelled or expired / terminal", why_empty=none,
        cls=cls)
    rc = [o for o in term if o.get("recovered") is not None]
    m["recovery_rate"] = _metric("recovery_rate", EE.clustered_proportion(
        [o["recovered"] for o in rc], [o.get("cluster") for o in rc]),
        basis=("cancelled remainders whose opportunity a later intent "
               "filled within %d s" % RECOVERY_WINDOW_S),
        why_empty="NO_CANCELLED_REMAINDER_IN_THE_WINDOW", cls=cls)
    base.update(orders=len(term),
                independent_events=len({c for c in cl if c is not None}),
                metrics=m, status="OK", why=None)
    return base


def live_estimates(classes: dict) -> dict:
    """The LIVE estimate of every metric by execution_evidence's rule: only
    the classes ELIGIBLE for the metric's family, the mean metrics widened
    with their declared floor."""
    return {m: EE.choose_live(
        {cls: (classes.get(cls) or {}).get("metrics", {}).get(m)
         for cls in EE.CLASSES}, kind=KIND[m],
        eligible=EE.LIVE_ELIGIBLE[FAMILY[m]],
        ineligible_why={EE.LIVE_SHADOW: EE.R_SHADOW_TAUTOLOGICAL},
        floor_unit=FLOOR_UNIT.get(m)) for m in METRICS}


# ═════════════════════════════════════════════════════════════════════
# THE ESTIMATES ON THE LIVE DECISION PATH (what each is fitted on)
# ═════════════════════════════════════════════════════════════════════

#: the twin's declaration (twin/common.EXECUTION_EVIDENCE_CLASS), restated
#: because no production module may import the research twin
#: (tests/test_twin_authority.py); pinned equal by
#: tests/test_execution_calibration.py
TWIN_EXECUTION_EVIDENCE_CLASS = {"PAPER": EE.PAPER_SIMULATION,
                                 "ACTUAL": EE.ACTUAL}
TWIN_ACTUAL_BOOK_PATH = ("LEGACY_EXECUTION_MIRROR (execution_intents -> "
                         "execmirror_fills), not the canonical SMALL LIVE "
                         "path")
#: the micro-calibration lane's declarations (calibration_store /
#: calibration_execute .EXECUTION_EVIDENCE_CLASS), RESTATED because a module
#: a request can reach must never import the venue submit / cancel module
#: (calibration_execute: "WIRED TO NOTHING. No route reaches this module");
#: pinned equal by tests/test_execution_calibration.py, which also checks
#: the route's runtime import closure
CALIBRATION_LANE_EXECUTION_EVIDENCE_CLASS = {
    "calibration_store": EE.ACTUAL, "calibration_execute": EE.ACTUAL}


def estimates_in_use() -> list:
    """Every execution estimate the LIVE decision path reads, with the class
    it is fitted on -- from each module's own declaration (pinned by a
    test)."""
    from . import bettor_entry_execution as EX
    from .agents import eddie as E
    from . import opportunity_score_v2 as V2
    from .lost_opportunity import score as SC
    rows = [
        {"estimate": "Eddie expected fill probability / time to fill / "
                     "adverse selection (agents/eddie.estimate), carried on "
                     "every canonical decision intent",
         "module": "agents/eddie.py",
         "fitted_on": E.EXECUTION_EVIDENCE_CLASS,
         "fitted_on_by_input": dict(E.FITTED_ON)},
        {"estimate": "Opportunity Score V1 P(fill) (lost_opportunity/score)",
         "module": "lost_opportunity/score.py",
         "fitted_on": SC.EXECUTION_EVIDENCE_CLASS},
        {"estimate": "Opportunity Score V2 fill lower bound / adverse upper "
                     "bound (opportunity_score_v2), widened by the "
                     "class transfer penalty; SHADOW tournament only",
         "module": "opportunity_score_v2.py",
         "fitted_on": E.EXECUTION_EVIDENCE_CLASS,
         "spec_sha": V2.SPEC_SHA},
        {"estimate": "Allie executable net profit (Eddie's EV) and capacity "
                     "ceiling (Eddie's walk of the displayed book)",
         "module": "canonical_components.py (allie_capital.allocate)",
         "fitted_on": E.EXECUTION_EVIDENCE_CLASS,
         "capacity_fitted_on": EE.NO_FILL_EVIDENCE},
        {"estimate": "external-valuation lane marketable coverage "
                     "(bettor_entry_execution.estimate)",
         "module": "bettor_entry_execution.py",
         "fitted_on": EX.EXECUTION_EVIDENCE_CLASS},
        {"estimate": "twin execution figures by book (twin/*)",
         "module": "twin/common.py",
         "fitted_on": dict(TWIN_EXECUTION_EVIDENCE_CLASS),
         "actual_book_path": TWIN_ACTUAL_BOOK_PATH},
        {"estimate": "micro-execution calibration lifecycle "
                     "(calibration_store / calibration_execute): real-venue "
                     "machinery of its own lane, wired to no route; no LIVE "
                     "estimate is fitted on it",
         "module": "calibration_store.py / calibration_execute.py",
         "fitted_on": dict(CALIBRATION_LANE_EXECUTION_EVIDENCE_CLASS)},
    ]
    for r in rows:
        f = r["fitted_on"]
        vals = list(f.values()) if isinstance(f, dict) else [f]
        r["proof_of_live_execution"] = False
        r["live_use"] = sorted({EE.LIVE_USE.get(v, "UNMEASURED_NOT_USED")
                                for v in vals})
        r["actual_on_the_canonical_path"] = {"status": EE.UNMEASURED,
                                             "why": EE.R_NO_ACTUAL}
    return rows
