"""Every measured number, computed once, from the ledger.

A figure is never a bare float. It carries the sample it was computed from
and, when it cannot be computed, the reason — because "we measured zero" and
"we have not measured this" are the same number to a reader and must never
be. Every consumer (the document, the JSON export, the diagnostic) reads the
same objects, so they cannot disagree with each other or with the engine.

Windowing: `since` is an absolute UTC instant, `days` a rolling window ending
now. Absolute wins when both are given. A re-baseline needs the former; a
live dashboard wants the latter.
"""

from __future__ import annotations

import time
from typing import Any

# Below this many settlements, a return figure is noise dressed as a result.
# Not a hard truth — the point is that SOMETHING must gate the headline, and
# an explicit constant is auditable where a judgement call in prose is not.
MIN_SETTLED_FOR_RETURN = 30

# Sigma below which we decline to call a result distinguishable from zero.
SIGMA_MEANINGFUL = 2.0

# A category earns a bigger ticket only on its own settled record: this far
# from zero, on at least this many settlements, positive. The thresholds are
# deliberately the slow, boring kind — the reference account's top-500
# LARGEST fills lost $1.07M, and size discipline is the strategy.
PROMOTION_SIGMA = 2.0
PROMOTION_MIN_N = 200


def fig(value: Any, *, n: int | None = None, unit: str = "",
        source: str = "", null_reason: str | None = None,
        min_n: int | None = None, note: str | None = None) -> dict:
    """One figure. Nulls itself when the sample cannot support it.

    `min_n` is the honest gate: pass the threshold the metric needs and the
    figure nulls rather than reporting a number a reader would over-trust.
    """
    out: dict[str, Any] = {"value": value, "n": n, "unit": unit,
                           "source": source}
    if value is None and null_reason is None:
        null_reason = "not_computed"
    if min_n is not None and (n is None or n < min_n):
        out["value"] = None
        null_reason = f"n_below_threshold: {n if n is not None else 0} < {min_n}"
    if out["value"] is None:
        out["null_reason"] = null_reason
    if note:
        out["note"] = note
    return out


def compute_figures(ledger, policy, *, days: int = 7,
                    since: float | None = None,
                    live_only: bool = True) -> dict:
    """The whole measured picture, in one object.

    Reuses the ledger's own report functions throughout. A second
    implementation of any of these that disagreed with the engine's would be
    worse than not reporting them: the engine trades on its numbers, and a
    document that quotes different ones describes a different system.
    """
    now = time.time()
    start = float(since) if since is not None else now - days * 86_400

    perf = ledger.performance(days=days, since=since, live_only=live_only)
    daily = ledger.performance_daily(days=days, since=since, live_only=live_only)
    drift = ledger.drift_report(days=days, since=since)
    penalties = ledger.drift_penalties(days=days, since=since)
    free = ledger.price_drift_report(days=days, since=since)
    rev = ledger.reversion(days=days, since=since)
    by_cat = ledger.performance_by_category(days=days, since=since,
                                            live_only=live_only)
    margin = ledger.entry_margin(days=days, since=since, live_only=live_only)
    by_band = ledger.performance_by_band(days=days, since=since,
                                         live_only=live_only)
    spread = ledger.spread_report(days=days, since=since, live_only=live_only)

    n_settled = int(perf.get("settled") or 0)
    P = "ledger.performance"

    headline = {
        "n_settled": fig(n_settled, n=n_settled, unit="count", source=P),
        "wins": fig(perf.get("wins"), n=n_settled, unit="count", source=P),
        "losses": fig(perf.get("losses"), n=n_settled, unit="count", source=P),
        "staked": fig(perf.get("staked"), n=int(perf.get("fills") or 0),
                      unit="usd", source=P),
        "net": fig(perf.get("realized"), n=n_settled, unit="usd", source=P),
        # The return itself is gated: a point estimate on a handful of
        # settlements is the single most over-read number in the system.
        "return_pct": fig(perf.get("roi"), n=n_settled, unit="fraction",
                          source=P, min_n=MIN_SETTLED_FOR_RETURN),
        "win_rate": fig(perf.get("win_rate"), n=n_settled, unit="fraction",
                        source=P, min_n=MIN_SETTLED_FOR_RETURN),
        "sd_per_trade": fig(perf.get("sd_per_trade"), n=n_settled,
                            unit="fraction", source=P, min_n=2),
        "se": fig(perf.get("se"), n=n_settled, unit="fraction", source=P,
                  min_n=2),
        "sigma_from_zero": fig(perf.get("sigma_from_zero"), n=n_settled,
                               unit="sigma", source=P, min_n=2),
        "open_cost": fig(perf.get("open_cost"), unit="usd", source=P),
    }

    drift_by_cat = {}
    for cat, stats in (drift.get("by_category") or {}).items():
        n = int(stats.get("n") or 0)
        drift_by_cat[cat] = {
            "drift_cents": fig(stats.get("mean_drift_c"), n=n, unit="cents",
                               source="ledger.drift_report",
                               min_n=ledger.DRIFT_MIN_N),
            "retention": fig(stats.get("retention"), n=n, unit="fraction",
                             source="ledger.drift_report",
                             min_n=ledger.DRIFT_MIN_N,
                             note=("retention compares our fair value against "
                                   "ITSELF a minute later, so a stably-wrong "
                                   "number scores 1.00 — it cannot detect a "
                                   "mapping error")),
            "surcharge_cents": fig(
                round(penalties.get(cat, penalties.get("*", 0.0)) * 100, 2),
                n=n, unit="cents", source="ledger.drift_penalties",
                note=("categories under the observation minimum inherit the "
                      "overall surcharge rather than trading free")),
        }

    keep = rev.get("keep")
    # The runner activates shrinkage on exactly this condition (runner.py:426):
    # a keep fraction exists only when the sample and the spread of claim
    # sizes were both large enough for the slope to mean anything.
    shrinkage_active = keep is not None
    return {
        "window": {
            "start_ts": start,
            "end_ts": now,
            "days": days,
            "absolute_since": since is not None,
            "live_only": live_only,
        },
        "headline": headline,
        "daily": daily,
        "drift_overall": {
            "drift_cents": fig(drift.get("mean_drift_c"),
                               n=int(drift.get("n") or 0), unit="cents",
                               source="ledger.drift_report",
                               min_n=ledger.DRIFT_MIN_N),
            "retention": fig(drift.get("retention"),
                             n=int(drift.get("n") or 0), unit="fraction",
                             source="ledger.drift_report",
                             min_n=ledger.DRIFT_MIN_N),
        },
        "drift_by_category": drift_by_cat,
        "free_samples": {
            "n_observations": fig(free.get("n"), n=int(free.get("n") or 0),
                                  unit="count",
                                  source="ledger.price_drift_report"),
            "drift_cents": fig(free.get("mean_drift_c"),
                               n=int(free.get("n") or 0), unit="cents",
                               source="ledger.price_drift_report",
                note=("outcomes we PRICED but did not buy: staleness without "
                      "selection, so it is a floor on drift, never a "
                      "substitute for the figure measured on our own fills")),
        },
        "reversion": {
            "keep": fig(keep, n=int(rev.get("n") or 0), unit="fraction",
                        source="ledger.reversion",
                        min_n=ledger.REVERSION_MIN_N),
            "slope": fig(rev.get("slope"), n=int(rev.get("n") or 0),
                         unit="fraction", source="ledger.reversion",
                         min_n=ledger.REVERSION_MIN_N),
            "shrinkage_active": fig(shrinkage_active, unit="bool",
                                    source="ledger.reversion"),
        },
        "by_category": by_cat,
        "by_band": by_band,
        "spread_cost": spread,
        # Profit per dollar of turnover measured AT ENTRY — the fast,
        # continuous predictor of the settled result. Converges in
        # hundreds of fills; blind to stable mapping errors, which is why
        # settlement still outranks it.
        "entry_margin": {
            cat: {
                "gross_margin": fig(m["gross_margin"], n=m["n_fills"],
                                    unit="fraction",
                                    source="ledger.entry_margin"),
                "net_margin": fig(m["net_margin"], n=m["n_fills"],
                                  unit="fraction",
                                  source="ledger.entry_margin",
                                  null_reason=(None if m["net_margin"]
                                               is not None else
                                               "retention_unmeasured: "
                                               f"n={m['retention_n']} < "
                                               f"{ledger.DRIFT_MIN_N}")),
            } for cat, m in margin.items()},
        # Which categories have EARNED a bigger ticket, on their own settled
        # record. Report-only on purpose: approve() clamps every order to
        # the config caps, so acting on readiness is a reviewed config
        # edit, never an automatic path that can run away on a hot streak.
        "promotion_readiness": {
            cat: {
                "settled": b.get("settled"),
                "roi": b.get("roi"),
                "sigma": b.get("sigma"),
                "ready": bool(b.get("sigma") is not None
                              and b["sigma"] >= PROMOTION_SIGMA
                              and (b.get("settled") or 0) >= PROMOTION_MIN_N
                              and (b.get("roi") or 0) > 0),
            } for cat, b in by_cat.items()},
    }


def verdict(figures: dict) -> str:
    """One sentence naming what the evidence currently supports.

    Deliberately conservative and deliberately symmetric: the same rule that
    refuses to call a losing stretch a disproof refuses to call a winning one
    an edge. Whoever reads this document should not have to work out for
    themselves whether the headline means anything.
    """
    h = figures["headline"]
    n = h["n_settled"]["value"] or 0
    if n < MIN_SETTLED_FOR_RETURN:
        return (f"NO EVIDENCE EITHER WAY. {n} settlements is below the "
                f"{MIN_SETTLED_FOR_RETURN} this report requires before "
                f"quoting a return at all.")
    sigma = h["sigma_from_zero"]["value"]
    roi = h["return_pct"]["value"]
    if sigma is None:
        return f"NO EVIDENCE EITHER WAY. {n} settlements, dispersion not computable."
    if sigma < SIGMA_MEANINGFUL:
        return (f"INDISTINGUISHABLE FROM ZERO. {roi:+.2%} over {n} settlements "
                f"is {sigma:.2f} sigma from nothing — consistent with a real "
                f"edge, with no edge, and with a small negative one.")
    direction = "POSITIVE" if (roi or 0) > 0 else "NEGATIVE"
    return (f"{direction}, AND MEASURABLE. {roi:+.2%} over {n} settlements, "
            f"{sigma:.2f} sigma from zero.")
