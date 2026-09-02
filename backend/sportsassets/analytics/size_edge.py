"""His edge by the SIZE of his bet: is the dust floor discarding edge?

First day of the $50 measuring clips (2026-09-02): the copy census's
largest refusal became below_min_clip -- 822 for rn1, 756 for
0x076daa87 in one morning. The cause is proportional sizing meeting the
$10 dust floor: a copy is capped at HIS notional, so every probe he
places under $10 is refused, on the theory that spread and fees eat a
few dollars of edge. Whether his small probes carry the same edge as
his large bets is not a theory; it is a number on his own resolved
book. This module computes it.

Every resolved BUY of his in the window is bucketed by the dollars he
staked (size x price) and scored at his fill with the same
ratio-estimator ROI and cluster-robust interval proof.roi_with_ci gives
our copies, clustered by his GAME. The reading compares the smallest
bucket to the floor:

  <$10 lower bound > 0 on 30+ games    SMALL PROBES EARN: the floor is
                                       discarding proven edge for this
                                       whale
  <$10 upper bound < 0 on 30+ games    SMALL PROBES LOSE: the floor is
                                       right
  otherwise                            NOT DEMONSTRATED; see n

The floor is a copy-side rule and a change to it is a fill rule, so
this instrument only reads; the rule moves when the number says so.
"""
from __future__ import annotations

import math
from typing import Any

from .decompose import WHALES, payout_of
from .proof import MIN_PROOF_CLUSTERS, roi_with_ci

# (lower inclusive, upper exclusive, label) in dollars of HIS stake.
BUCKETS: tuple[tuple[float, float, str], ...] = (
    (0.0, 10.0, "<$10"), (10.0, 50.0, "$10-50"), (50.0, 250.0, "$50-250"),
    (250.0, math.inf, ">=$250"))
FLOOR_BUCKET = "<$10"


def bucket_of(notional: float) -> str | None:
    """The label whose [lo, hi) holds `notional`; None for a non-positive
    stake."""
    try:
        v = float(notional)
    except (TypeError, ValueError):
        return None
    if not (v > 0.0):
        return None
    for lo, hi, label in BUCKETS:
        if lo <= v < hi:
            return label
    return None


def _verdict(out: dict) -> str:
    ci = out.get("ci95")
    g = int(out.get("clusters") or 0)
    if not ci:
        return "INSUFFICIENT — no interval"
    if g < MIN_PROOF_CLUSTERS:
        return f"PROVISIONAL (games<{MIN_PROOF_CLUSTERS})"
    if ci[0] > 0:
        return "POSITIVE at 95%"
    if ci[1] < 0:
        return "NEGATIVE at 95%"
    return "NOT DEMONSTRATED — contains zero"


def score(rows: list[dict]) -> dict:
    """rows: [{size, price, payout, event_key}] -> {buckets: {label:
    roi_with_ci + verdict + stake_share}, all: ..., reading}."""
    per: dict[str, list[dict]] = {label: [] for _, _, label in BUCKETS}
    everything: list[dict] = []
    total_stake = 0.0
    for r in rows:
        try:
            size = float(r.get("size") or 0)
            p = float(r.get("price")) if r.get("price") is not None else None
        except (TypeError, ValueError):
            continue
        payout = r.get("payout")
        if p is None or payout is None or size <= 0 or not (0.0 < p < 1.0):
            continue
        stake = size * p
        label = bucket_of(stake)
        if label is None:
            continue
        s = {"stake": stake, "pnl": size * (float(payout) - p),
             "event_key": r.get("event_key")}
        per[label].append(s)
        everything.append(s)
        total_stake += stake
    buckets: dict[str, dict] = {}
    for _, _, label in BUCKETS:
        b = roi_with_ci(per[label])
        b["verdict"] = _verdict(b)
        b["stake_share"] = (round(sum(x["stake"] for x in per[label]) / total_stake, 4)
                            if total_stake > 0 else None)
        buckets[label] = b
    allb = roi_with_ci(everything)
    allb["verdict"] = _verdict(allb)
    out: dict[str, Any] = {"buckets": buckets, "all": allb, "n_rows": len(everything),
                           "floor_bucket": FLOOR_BUCKET,
                           "min_proof_clusters": MIN_PROOF_CLUSTERS}
    small = buckets[FLOOR_BUCKET]
    ci, g = small.get("ci95"), int(small.get("clusters") or 0)
    if ci and g >= MIN_PROOF_CLUSTERS and ci[0] > 0:
        out["reading"] = (
            f"SMALL PROBES EARN: his buys under $10 return {small['roi']:+.2%} "
            f"[{ci[0]:+.2%}, {ci[1]:+.2%}] on {g} games — the $10 dust floor "
            f"is discarding proven edge for this whale")
    elif ci and g >= MIN_PROOF_CLUSTERS and ci[1] < 0:
        out["reading"] = (
            f"SMALL PROBES LOSE: his buys under $10 return {small['roi']:+.2%} "
            f"[{ci[0]:+.2%}, {ci[1]:+.2%}] on {g} games — the floor is right")
    else:
        out["reading"] = (
            f"NOT DEMONSTRATED: {small.get('n', 0)} buys under $10 on {g} games "
            f"(needs {MIN_PROOF_CLUSTERS}+ games and an interval that leaves "
            f"zero)")
    return out


async def cohort_size_edge(pool: Any, whale: str, days: int = 30) -> dict:
    """Score one whale's resolved BUYs over the window, by his stake."""
    w = whale.lower()
    rows = await pool.fetch(
        """
        SELECT t.size::float8 AS size, t.price::float8 AS price,
               t.outcome_index, m.resolved_prices,
               COALESCE(NULLIF(m.event_slug, ''), NULLIF(t.event_slug, ''),
                        t.condition_id) AS event_key
          FROM trades t
          JOIN whales wh ON wh.id = t.whale_id
          JOIN markets m ON m.condition_id = t.condition_id
         WHERE lower(wh.username) = $1
           AND t.side = 'BUY'
           AND t.ts >= now() - make_interval(days => $2)
           AND COALESCE(m.resolved, false) = true
           AND m.resolved_prices IS NOT NULL
           AND t.outcome_index IS NOT NULL
        """, w, int(days))
    scored = []
    unresolvable = 0
    for r in rows:
        d = dict(r)
        d["payout"] = payout_of(d.pop("resolved_prices"), d.get("outcome_index"))
        if d["payout"] is None:
            unresolvable += 1
            continue
        scored.append(d)
    out = score(scored)
    out["whale"] = w
    out["days"] = int(days)
    out["unresolvable_payout"] = unresolvable
    return out


__all__ = ["BUCKETS", "FLOOR_BUCKET", "WHALES", "bucket_of", "score",
           "cohort_size_edge"]
