"""Edge conditioned on what capturing it cost.

THE ARITHMETIC THAT DECIDES THE FILL RULE. Proof at 95% needs roughly
n = (1.645 * sigma / mu)^2 settled copies, so it is a function of the
achieved edge SQUARED and not of volume. rn1's edge is real (+4.4% to
+6.9% on his own fills) and our copies of him return ~0%, because his
buy moves the ask and we pay that impact to get in: edge minus impact
rounds to zero.

If that is the whole story, the copies where impact was SMALL should
carry his edge, and the copies where it was large should not. That is
directly measurable on the ledger we already have -- every settled copy
records what he paid and what we paid -- and it needs no new data. If
the low-impact bucket carries mu > 0 with an interval that says so, the
fill rule is "only take fills within X cents of his price", and the
sample size to prove it is a number, not an argument.

Impact is measured in OUR cost space: cost_per_share(fill_price, intent)
minus his_price, so a BUY_SHORT is compared leg to leg and not across
the complement. Positive impact = we paid MORE than he did. Negative =
we filled cheaper (the price differential the audit found is a credit).
"""
from __future__ import annotations

from typing import Any

from .proof import MIN_PROOF_CLUSTERS, required_n, roi_with_ci

# Bucket edges in cents of impact, half-open on the upper edge. The
# first bucket is the credit case; the venue ticks in whole cents so
# the interior buckets are one tick wide.
IMPACT_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("<=-1c (cheaper)", float("-inf"), -0.01),
    ("(-1c,0]", -0.01, 0.0 + 1e-9),
    ("(0,1c]", 0.0 + 1e-9, 0.01 + 1e-9),
    ("(1c,2c]", 0.01 + 1e-9, 0.02 + 1e-9),
    ("(2c,3c]", 0.02 + 1e-9, 0.03 + 1e-9),
    (">3c", 0.03 + 1e-9, float("inf")),
)
# THE REST LANE IS ITS OWN POPULATION (round three, 2026-09-01). A rest
# fill lands at his exact price by construction -- impact <= 0 -- so
# pooled into the ladder it would sit in precisely the buckets whose
# EARNS verdict sets the IOC impact cap, and a fill rule read there
# would be partly the rest lane's adverse-selection population. Reported
# beside the ladder, never inside it, and never named by the reading.
REST_BUCKET = "rest lane (his price)"


def impact_of(row: dict) -> float | None:
    """Our cost per share minus his price, in dollars; None if unknowable."""
    from ..live_executor import cost_per_share

    try:
        hp = float(row.get("his_price"))
        fp = float(row.get("fill_price"))
    except (TypeError, ValueError):
        return None
    if not (0.0 < hp < 1.0) or not (0.0 < fp < 1.0):
        return None
    return cost_per_share(fp, row.get("intent")) - hp


def _bucket_name(x: float) -> str | None:
    for name, lo, hi in IMPACT_BUCKETS:
        if lo <= x < hi:
            return name
    return None


def impact_buckets(rows: list[dict], flow_per_day: float | None = None) -> dict:
    """Per-bucket ROI with cluster-robust interval, plus what proof costs.

    `flow_per_day` is the settled-copy rate that would land in the
    bucket, used to turn n_needed into a horizon. It is reported beside
    the count as a SCALE, never a promise: the point estimate it is
    sized against will move as the bucket fills.
    """
    by: dict[str, list[dict]] = {name: [] for name, _, _ in IMPACT_BUCKETS}
    by[REST_BUCKET] = []
    unmeasurable = 0
    for r in rows:
        x = impact_of(r)
        if x is None:
            unmeasurable += 1
            continue
        if str(r.get("lane") or "").lower() == "rest":
            b = REST_BUCKET
        else:
            b = _bucket_name(x)
        if b is None:      # NaN or a shape the ladder does not cover
            unmeasurable += 1
            continue
        by[b].append({"stake": r.get("stake"), "pnl": r.get("pnl"),
                      "event_key": r.get("event_key"), "impact": x})
    out: dict[str, Any] = {"unmeasurable": unmeasurable, "buckets": {},
                           "min_proof_clusters": MIN_PROOF_CLUSTERS}
    for name in [n for n, _, _ in IMPACT_BUCKETS] + [REST_BUCKET]:
        sel = by[name]
        ci = roi_with_ci(sel)
        imps = sorted(s["impact"] for s in sel)
        ci["impact_p50_cents"] = (round(imps[len(imps) // 2] * 100, 2)
                                  if imps else None)
        lo_hi = ci.get("ci95")
        games = int(ci.get("clusters") or 0)
        if not lo_hi:
            ci["verdict"] = "NO INTERVAL — fewer than two settled copies"
        elif lo_hi[0] > 0:
            ci["verdict"] = "EARNS at 95%"
        elif lo_hi[1] < 0:
            ci["verdict"] = "LOSES at 95%"
        else:
            ci["verdict"] = "NOT DEMONSTRATED — contains zero"
        # BELOW THE FLOOR THE VERDICT IS PROVISIONAL AND THERE IS NO
        # HORIZON. Reproduced in review: six copies at +30% read "EARNS
        # at 95%, n_still_needed=0, days=0.0" and the reading named a
        # fill rule -- a policy recommendation on six games whose
        # interval is not actually 95%. See proof.MIN_PROOF_CLUSTERS.
        provisional = bool(lo_hi) and games < MIN_PROOF_CLUSTERS
        if provisional:
            ci["verdict"] = (f"PROVISIONAL (games<{MIN_PROOF_CLUSTERS}) — "
                             + ci["verdict"])
        need = None
        if (not provisional and ci.get("sigma_per_dollar")
                and ci.get("roi") and ci["roi"] > 0):
            need = required_n(ci["sigma_per_dollar"], ci["roi"])
        ci["n_needed_at_observed"] = need
        ci["n_still_needed"] = (max(0, need - ci["n"]) if need else None)
        ci["days_to_proof_at_flow"] = (
            round(ci["n_still_needed"] / flow_per_day, 1)
            if need and flow_per_day and flow_per_day > 0 else None)
        out["buckets"][name] = ci
    # THE READING IS THE POINT. Name the cheapest bucket that earns at
    # 95%, if any: that is the fill rule the data supports, stated as
    # the impact ceiling it implies.
    earning = [name for name, _, _ in IMPACT_BUCKETS
               if out["buckets"][name].get("verdict") == "EARNS at 95%"]
    if earning:
        out["reading"] = (f"the data supports a fill rule: buckets "
                          f"{earning} earn at 95% on {MIN_PROOF_CLUSTERS}+ "
                          f"games. Cap impact at the top of the last "
                          f"earning bucket.")
    else:
        best = max(((name, out["buckets"][name].get("roi"))
                    for name, _, _ in IMPACT_BUCKETS
                    if out["buckets"][name].get("roi") is not None),
                   key=lambda t: t[1], default=(None, None))
        out["reading"] = (
            f"no bucket earns at 95% on {MIN_PROOF_CLUSTERS}+ games yet; "
            f"best point estimate is {best[0]} at {best[1]:+.2%} — see "
            f"n_still_needed"
            if best[0] else "no measurable settled copies")
    return out


async def cohort_impact(pool: Any, since: str, whale: str | None = None,
                        flow_per_day: float | None = None) -> dict:
    """Impact-conditioned edge over the proof cohort's exact population."""
    import datetime as _dt

    from ..live_executor import ORDER_INTENT_SQL

    ts = _dt.datetime.fromisoformat(str(since))
    args: list[Any] = [ts]

    def _q(with_lane: bool) -> str:
        lane = "lo.lane AS lane," if with_lane else "NULL::text AS lane,"
        # NOT THE MIRROR BOOK (position mirroring P1, owner order
        # 2026-09-02 "go for it, let's get this working"; the panel
        # review's predicate audit). The ladder scores each fill against
        # HIS fill; a book's his_price is an open-time level under a
        # lifetime of buys and sells, and it would land in exactly the
        # buckets whose verdict sets the IOC impact cap. Guarded only
        # when the column exists: without it no row can carry
        # lane='mirror', and naming it would break the retry below.
        guard = ("           AND COALESCE(lo.lane,'') <> 'mirror'\n"
                 if with_lane else "")
        q = f"""
        SELECT lower(COALESCE(lo.whale_username, '?')) AS whale,
               lo.his_price::float8 AS his_price,
               lo.fill_price::float8 AS fill_price,
               COALESCE(lo.filled_usd, lo.requested_usd)::float8 AS stake,
               lo.pnl::float8 AS pnl,
               {lane}
               {ORDER_INTENT_SQL} AS intent,
               COALESCE(NULLIF(m.event_slug, ''),
                        NULLIF(lo.us_market_slug, '')) AS event_key
          FROM live_orders lo
          LEFT JOIN market_tokens mt ON mt.token_id = lo.asset
          LEFT JOIN markets m ON m.condition_id = mt.condition_id
         WHERE lo.placed_at >= $1
           AND lo.pnl IS NOT NULL
           AND lo.status IN ('settled', 'cashed_out')
           AND COALESCE(lo.whale_username, '') NOT IN ('manual', 'underdog')
           AND COALESCE(lo.filled_usd, lo.requested_usd) > 0
{guard}        """
        if whale:
            q += "           AND lower(COALESCE(lo.whale_username,'')) = $2\n"
        return q

    if whale:
        args.append(whale.lower())
    # lo.lane arrives with migration 041; the workers never run
    # migrations, so the column can be absent on a live database. Retry
    # without it rather than report nothing -- the rest lane then reads
    # as IOC, which is today's (pre-041) truth.
    try:
        rows = [dict(r) for r in await pool.fetch(_q(True), *args)]
    except Exception as exc:  # noqa: BLE001 — missing column only
        if "lane" not in str(exc):
            raise
        rows = [dict(r) for r in await pool.fetch(_q(False), *args)]
    out = impact_buckets(rows, flow_per_day)
    out["since"] = since
    out["whale"] = whale.lower() if whale else "(all copied whales)"
    out["n_settled"] = len(rows)
    return out
