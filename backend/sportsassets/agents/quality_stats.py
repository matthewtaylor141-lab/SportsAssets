"""PURE STATISTICS shared by the quality scorecard and the research twin.

Moved verbatim out of agents/quality_scorecard.py (which re-exports every
name) so a research module can use them without importing the scorecard's
database readers -- and, through those, Xavier's management view. Nothing
here touches a database, a venue or an order.
"""
from __future__ import annotations

import datetime as _dt
import math
import statistics

FORWARD_SAMPLE_RULE = {
    "id": "BETTOR_FORWARD_SAMPLE_V1",
    "declared_at": "2026-10-03T00:00:00Z",
    "forward_start": "2026-10-04T00:00:00Z",
    "population": ("closed positions reconstructed by Audrey's postmortems "
                   "(position_postmortems), PAPER and ACTUAL evaluated "
                   "separately, opened at or after forward_start"),
    "independence": ("one position per fixture: the first closed position "
                     "on a fixture counts; later positions on the same "
                     "fixture are dependent and excluded"),
    "min_positions": 300,
    "min_calendar_days": 30,
    "test": ("one-sided 95%: the lower bound of the mean realized net P&L "
             "per position (normal approximation, z=1.645) is above 0"),
    "verdict_until_sufficient": "UNPROVEN",
    "verdicts": ["UNPROVEN", "SUPPORTED_BY_FORWARD_SAMPLE",
                 "NOT_SUPPORTED_BY_FORWARD_SAMPLE"],
}


def _epoch_iso(s: str) -> float:
    return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def wilson(k: int, n: int, z: float = 1.96) -> dict | None:
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"low": round(max(0.0, c - h), 6), "high": round(min(1.0, c + h),
                                                             6),
            "level": 0.95, "method": "WILSON"}


def mean_ci(xs: list, z: float = 1.96) -> dict | None:
    if len(xs) < 2:
        return None
    m = statistics.fmean(xs)
    se = statistics.stdev(xs) / math.sqrt(len(xs))
    return {"low": round(m - z * se, 6), "high": round(m + z * se, 6),
            "level": 0.95, "method": "NORMAL_MEAN"}


def forward_verdict(rows: list, *, now: float,
                    rule: dict = FORWARD_SAMPLE_RULE) -> dict:
    """Apply the predeclared rule to closed-position rows (dicts with
    opened_at, closed_at, fixture/us_market_slug, realized_pnl_usd). Pure."""
    start = _epoch_iso(rule["forward_start"])
    seen, sample = set(), []
    for r in sorted(rows, key=lambda x: (x.get("closed_at") or 0)):
        if (r.get("opened_at") or 0) < start:
            continue
        fx = r.get("fixture") or r.get("us_market_slug")
        if fx in seen:
            continue
        seen.add(fx)
        sample.append(float(r["realized_pnl_usd"]))
    days = max(0.0, (now - start) / 86400.0)
    sufficient = (len(sample) >= rule["min_positions"]
                  and days >= rule["min_calendar_days"])
    m = round(statistics.fmean(sample), 6) if sample else None
    lower = None
    if len(sample) >= 2:
        lower = round(m - 1.645 * statistics.stdev(sample)
                      / math.sqrt(len(sample)), 6)
    if not sufficient:
        verdict = rule["verdict_until_sufficient"]
    else:
        verdict = ("SUPPORTED_BY_FORWARD_SAMPLE" if lower is not None
                   and lower > 0 else "NOT_SUPPORTED_BY_FORWARD_SAMPLE")
    return {"verdict": verdict, "sufficient": sufficient,
            "positions": len(sample), "required_positions":
            rule["min_positions"], "calendar_days": round(days, 2),
            "required_days": rule["min_calendar_days"],
            "mean_pnl_per_position_usd": m,
            "one_sided_lower_95_usd": lower, "rule_id": rule["id"]}
