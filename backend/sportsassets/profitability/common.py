"""Shared constants and pure helpers for the profitability layer. No I/O.

Reuses the intelligence layer's pure helpers (intel.common: num, epoch,
jload, Out, book levels, exit walk, cost space) rather than duplicating
them.
"""
from __future__ import annotations

import hashlib
import json
import math
import random

from ..intel import common as IC

LABEL = "RESEARCH"
AUTHORITY = "SHADOW_NO_AUTHORITY"
DISCLOSURE = (
    "RESEARCH / SHADOW: computed from the existing records, persisted only to "
    "the append-only pos_* tables and displayed. No venue, order, sizing, "
    "limit, threshold or capital authority. PAPER, ACTUAL and COUNTERFACTUAL "
    "are never summed. Unmeasured values are null with a named reason, never "
    "zero.")
PAPER_ACCOUNT = IC.PAPER_ACCOUNT
BOOKS = ("PAPER", "ACTUAL")
DRAW_BUDGET = 300000
ALL_BOOKS = ("PAPER", "ACTUAL", "COUNTERFACTUAL")

# metric / capacity statuses
MEASURED = "MEASURED"
INSUFFICIENT = "INSUFFICIENT_SAMPLE"
UNAVAILABLE = "UNAVAILABLE"
UNPROVEN = "UNPROVEN"

# ── THE SLEEVES: PRODUCTION CONFIDENCE IS INVESTMENT-ONLY (migration 227) ─
#
# THE DEFECT (owner audit 2026-10-04, P0 #5). Every metric and forecast here
# was computed per BOOK, so the PAPER figures pooled every paper strategy: a
# TRAINING (exploration) loss -- a research cost by design -- or a BENCHMARK
# control arm's win moved the realized net edge, the profit per capital-hour,
# the drawdown, the edge calibration and the 30-day forecast a live-capital
# decision would read. Migration 223 had already given every paper position
# group ONE durable sleeve; the statistics did not use it.
#
# THE RULE. A position's sleeve is its group's durable classification
# (paper_sleeve_current_v, read by reads.sleeve_classes); a position with
# none is UNCLASSIFIED -- never INVESTMENT. Every production-confidence
# number is computed over the INVESTMENT sleeve ONLY; TRAINING, BENCHMARK and
# UNCLASSIFIED are computed and shown SEPARATELY and labelled
# RESEARCH_NOT_PRODUCTION_CONFIDENCE. Sleeves are never pooled. Every row and
# aggregate carries book, sleeve, strategy and policy_version(s).
INVESTMENT, TRAINING, BENCHMARK, UNCLASSIFIED = (
    "INVESTMENT", "TRAINING", "BENCHMARK", "UNCLASSIFIED")
#: pinned equal to bettor_paper_sleeves.SLEEVES by a test (this package may
#: not import a paper module)
SLEEVES = (INVESTMENT, TRAINING, BENCHMARK, UNCLASSIFIED)
PRODUCTION_SLEEVE = INVESTMENT
#: the strategy -> sleeve map of migration 223's classifier, pinned equal to
#: bettor_paper_sleeves.STRATEGY_SLEEVE by a test. Used ONLY for an ACTUAL
#: position whose paper group has no durable classification (the ACTUAL
#: lane records the strategy that decided it) and to name the INVESTMENT
#: strategies; a PAPER position's sleeve is always its durable
#: classification.
STRATEGY_SLEEVE = {
    "PINNACLE_COMPLETED_GAME_PAPER": INVESTMENT,
    "DEREK_ENTRY_POLICY_V2": INVESTMENT,
    "PINNACLE_EXPLORATION_PAPER": TRAINING,
    "PINNACLE_ONLY_PAPER_BENCHMARK": BENCHMARK,
    "PINNACLE_COMPLETED_GAME_MAKER_PAPER": BENCHMARK,
}
INVESTMENT_STRATEGIES = tuple(sorted(
    s for s, v in STRATEGY_SLEEVE.items() if v == INVESTMENT))
#: a sleeve aggregate over every strategy of the sleeve
ALL_STRATEGIES = "ALL"
PRODUCTION = "PRODUCTION_CONFIDENCE"
RESEARCH_SCOPE = "RESEARCH_NOT_PRODUCTION_CONFIDENCE"
#: how a pre-227 row (sleeve NULL: every strategy pooled) is shown
BOOK_WIDE_PRE_227 = "BOOK_WIDE_PRE_227"
SLEEVE_ROLE = {
    INVESTMENT: "PRODUCTION_CONFIDENCE: the only sleeve whose numbers may "
                "inform live capital",
    TRAINING: "RESEARCH: exploration -- a loss is research cost, a win is "
              "not production alpha",
    BENCHMARK: "RESEARCH: control arm -- never production evidence",
    UNCLASSIFIED: "RESEARCH: economic purpose not established -- never "
                  "counted as INVESTMENT",
}


def sleeve_of(row) -> str:
    """The row's sleeve; a missing or unknown one is UNCLASSIFIED -- never
    INVESTMENT."""
    s = (row or {}).get("sleeve")
    return s if s in SLEEVES else UNCLASSIFIED


def strategy_sleeve(strategy) -> str:
    """The classifier's sleeve of a strategy name (ACTUAL fallback only)."""
    return STRATEGY_SLEEVE.get(str(strategy), UNCLASSIFIED) \
        if strategy else UNCLASSIFIED


def confidence_scope(sleeve) -> str:
    return PRODUCTION if sleeve == INVESTMENT else RESEARCH_SCOPE


def in_scope(row, *, book=None, sleeve=None, strategy=None) -> bool:
    """`sleeve` None = every sleeve (research / accounting only);
    `strategy` None or ALL = every strategy of the sleeve."""
    if book is not None and row.get("book") != book:
        return False
    if sleeve is not None and sleeve_of(row) != sleeve:
        return False
    if strategy not in (None, ALL_STRATEGIES) and \
            row.get("strategy") != strategy:
        return False
    return True


def scope_fields(rows, *, book, sleeve, strategy=ALL_STRATEGIES) -> dict:
    """book, sleeve, strategy, policy_version(s), classifier version(s) and
    the confidence scope of an aggregate over `rows` (already scoped).
    `sleeve` None means every sleeve pooled: research / accounting only,
    never production confidence."""
    pvs = sorted({str(r["policy_version"]) for r in rows
                  if r.get("policy_version")})
    cvs = sorted({str(r["classifier_version"]) for r in rows
                  if r.get("classifier_version")})
    return {"book": book, "sleeve": sleeve,
            "strategy": strategy or ALL_STRATEGIES,
            "policy_versions": pvs,
            "policy_version": pvs[0] if len(pvs) == 1 else None,
            "policy_version_why": (None if len(pvs) == 1 else
                                   "NO_POLICY_VERSION_IN_SAMPLE" if not pvs
                                   else "SEVERAL_POLICY_VERSIONS_IN_SAMPLE"),
            "classifier_version": ",".join(cvs) if cvs else None,
            "confidence_scope": (confidence_scope(sleeve) if sleeve
                                 else RESEARCH_SCOPE),
            "sleeve_role": SLEEVE_ROLE.get(
                sleeve, "RESEARCH: every sleeve pooled -- never production "
                        "confidence")}


def scopes(rows, *, book) -> list:
    """Every (sleeve, strategy) scope to report for `book`: each sleeve's
    aggregate (strategy ALL -- always, even when empty: a sleeve with no
    position is UNAVAILABLE with its reason, never absent) and each strategy
    present in the rows, under its own sleeve."""
    seen = set()
    for r in rows:
        if r.get("book") != book or not r.get("strategy"):
            continue
        seen.add((sleeve_of(r), str(r["strategy"])))
    return [(s, ALL_STRATEGIES) for s in SLEEVES] + sorted(seen)


num = IC.num
epoch = IC.epoch
jload = IC.jload
rnd = IC.rnd
Out = IC.Out
median = IC.median
mean = IC.mean
day_start = IC.day_start


def envelope(status, why=None, *, computed_at=None, data=None, **extra):
    """The read envelope every profitability endpoint answers with."""
    out = {"label": LABEL, "authority": AUTHORITY, "status": status,
           "why": why, "computed_at": computed_at, "data": data,
           "disclosure": DISCLOSURE}
    out.update(extra)
    return out


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                          .encode()).hexdigest()


def seed_of(obj) -> int:
    """A deterministic bootstrap seed from the inputs (reproducible)."""
    return int(sha(obj)[:12], 16)


def quantile(sorted_xs, q):
    """Linear-interpolated quantile of an already sorted list."""
    if not sorted_xs:
        return None
    if len(sorted_xs) == 1:
        return float(sorted_xs[0])
    pos = q * (len(sorted_xs) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_xs) - 1)
    frac = pos - lo
    return float(sorted_xs[lo] * (1 - frac) + sorted_xs[hi] * frac)


def bootstrap_ratio_ci(pairs, *, seed, resamples=1000, alpha=0.10):
    """Percentile bootstrap CI of sum(a)/sum(b) over (a, b) pairs. None when
    fewer than 2 pairs or a resample has a non-positive denominator only."""
    if len(pairs) < 2:
        return None, None
    rng = random.Random(seed)
    n = len(pairs)
    # bounded cost: at most DRAW_BUDGET draws, never fewer than 200 resamples
    resamples = max(200, min(resamples, DRAW_BUDGET // n))
    stats = []
    for _ in range(resamples):
        sa = sb = 0.0
        for _ in range(n):
            a, b = pairs[rng.randrange(n)]
            sa += a
            sb += b
        if sb > 0:
            stats.append(sa / sb)
    if len(stats) < resamples * 0.5:
        return None, None
    stats.sort()
    return quantile(stats, alpha / 2), quantile(stats, 1 - alpha / 2)


def max_drawdown(values) -> float:
    """Peak-to-trough fall of a cumulative path that starts at 0 (>= 0)."""
    peak = 0.0
    cum = 0.0
    dd = 0.0
    for v in values:
        cum += v
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd
