"""Shared helpers for the RESEARCH twin / profitability layer. Pure; no I/O.

THE RULES EVERY MODULE HERE FOLLOWS:

  * every output carries its LABEL: 'COUNTERFACTUAL' for a twin world,
    'PAPER' / 'ACTUAL' for a recorded baseline, 'RESEARCH' for everything
    else -- and PAPER, ACTUAL and COUNTERFACTUAL figures are never summed;
  * an unmeasured quantity is None with a NAMED REASON, never 0. A measured
    zero (a position the world did not take really has no P&L) is a number
    and carries its basis instead;
  * nothing here has authority: AUTHORITY = 'RESEARCH_NO_AUTHORITY'.
"""
from __future__ import annotations

import json
import math

from ..agents import quality_stats as QS
from ..intel import common as IC

LABEL = "RESEARCH"
COUNTERFACTUAL = "COUNTERFACTUAL"
BOOKS = ("PAPER", "ACTUAL")
#: R30C · THE EXECUTION-EVIDENCE CLASS OF EACH BOOK'S FILLS. PAPER fills are
#: the paper simulator's (PAPER_SIMULATION, never proof of live execution);
#: the twin's ACTUAL book reads execmirror_fills -- real venue fills of the
#: LEGACY execution-mirror lane (ACTUAL, but NOT the canonical SMALL LIVE
#: path, which has none while it is SHADOW). Literals, because the twin may
#: import nothing outside itself, intel and quality_stats; pinned equal to
#: execution_evidence by tests/test_execution_calibration.py.
EXECUTION_EVIDENCE_CLASS = {"PAPER": "PAPER_SIMULATION", "ACTUAL": "ACTUAL"}
ACTUAL_BOOK_PATH = ("LEGACY_EXECUTION_MIRROR (execution_intents -> "
                    "execmirror_fills), not the canonical SMALL LIVE path")
AUTHORITY = "RESEARCH_NO_AUTHORITY"
DISCLOSURE = (
    "RESEARCH: computed from the recorded stream, persisted to twin_* tables "
    "and displayed only. No authority: places, cancels, sizes and activates "
    "nothing; a kill-switch criterion only records RECOMMEND_PAUSE. Twin "
    "results are COUNTERFACTUAL research evidence, never production truth. "
    "ACTUAL, PAPER and COUNTERFACTUAL figures are never summed. Unmeasured "
    "values are null with a named reason, never zero.")

PAPER_ACCOUNT = IC.PAPER_ACCOUNT
SLEEVE_NOTIONAL_USD = IC.SLEEVE_NOTIONAL_USD

# re-exported pure helpers (one implementation, the intel layer's)
num, jload, epoch, ts, rnd, sha = (IC.num, IC.jload, IC.epoch, IC.ts, IC.rnd,
                                   IC.sha)
mean, stdev, median = IC.mean, IC.stdev, IC.median
book_view, exit_walk, cost_space = IC.book_view, IC.exit_walk, IC.cost_space
side_of_intent, is_buy_intent = IC.side_of_intent, IC.is_buy_intent
Out = IC.Out

# the quality scorecard's statistics (one implementation)
wilson, mean_ci = QS.wilson, QS.mean_ci
FORWARD_SAMPLE_RULE = QS.FORWARD_SAMPLE_RULE
forward_verdict = QS.forward_verdict

STATUSES = ("MEASURED", "INSUFFICIENT_SAMPLE", "UNAVAILABLE", "UNPROVEN")


def envelope(**extra) -> dict:
    out = {"label": LABEL, "authority": AUTHORITY, "disclosure": DISCLOSURE}
    out.update(extra)
    return out


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


def diff_ci(xs: list, z: float = 1.96) -> dict | None:
    """95% normal CI of a mean of paired differences (None below n=2)."""
    return mean_ci(list(xs), z)


def two_mean_diff(a: list, b: list, z: float = 1.96) -> dict:
    """mean(a) - mean(b) with a Welch normal CI. Pure."""
    ma, mb = mean(a), mean(b)
    if ma is None or mb is None:
        return {"diff": None, "low": None, "high": None,
                "why": "EMPTY_SAMPLE"}
    sa, sb = stdev(a), stdev(b)
    if sa is None or sb is None:
        return {"diff": rnd(ma - mb), "low": None, "high": None,
                "why": "FEWER_THAN_2_PER_SIDE"}
    se = math.sqrt(sa * sa / len(a) + sb * sb / len(b))
    d = ma - mb
    return {"diff": rnd(d), "low": rnd(d - z * se), "high": rnd(d + z * se),
            "why": None}


def max_drawdown(pnls_in_order: list):
    """Worst peak-to-trough of the cumulative P&L (<= 0). None if empty."""
    if not pnls_in_order:
        return None
    peak = cum = worst = 0.0
    for x in pnls_in_order:
        cum += x
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    return rnd(worst)


def metric(agent: str, name: str, *, book: str, basis: str, value=None,
           numerator=None, denominator=None, sample=None, ci=None,
           reason=None, unit=None, min_sample=None,
           unproven: bool = False) -> dict:
    """ONE SCORECARD METRIC: value, numerator, denominator, sample, CI,
    status. value None -> UNAVAILABLE (or UNPROVEN) with a reason, never 0;
    fewer samples than min_sample -> INSUFFICIENT_SAMPLE (shown, nothing
    concluded)."""
    if value is None:
        status = "UNPROVEN" if unproven else "UNAVAILABLE"
        reason = reason or "NOT_MEASURED"
    elif min_sample is not None and (sample or 0) < min_sample:
        status = "INSUFFICIENT_SAMPLE"
        reason = reason or ("fewer than %d samples (%s)"
                            % (min_sample, sample or 0))
    else:
        status, reason = "MEASURED", None
    ci = ci or {}
    return {"agent": agent, "metric": name, "book": book,
            "value": None if value is None else rnd(value),
            "numerator": None if numerator is None else rnd(numerator),
            "denominator": None if denominator is None else rnd(denominator),
            "sample_n": sample, "ci_low": ci.get("low"),
            "ci_high": ci.get("high"), "ci_method": ci.get("method"),
            "status": status, "reason": reason, "basis": basis,
            "unit": unit}
