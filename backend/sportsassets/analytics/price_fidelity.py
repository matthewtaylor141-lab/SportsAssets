"""Did we actually get the same price or better?

The owner's requirement, stated in his own words and repeated:

    "I want to copy as many of their actual trades (buys and sells) as
     possible (same or better price)"

It has never had an instrument. The closest thing is one median on the
status page:

    percentile_cont(0.5) ... ORDER BY (fill_price - his_price) * 100

and that number is wrong for an entire class of copy. On a SHORT the
venue's `fill_price` names the LONG leg while `his_price` is the price
he paid for the short leg, so their difference is not slippage at all —
it is roughly (1 - 2p), which on the six shorts we filled read as ~66
cents of "slippage" that never happened. Same misdenomination family as
`spent = filled * fill_price`, one more place it was left un-converted.

WHAT COSTS WHAT. Our cost per share is the cash the fill actually
takes, which is exactly what fill_cash already computes:

    long   fill_price
    short  1 - fill_price

His cost per share is his_price on the leg he took, in both cases. So

    edge_per_share = his_price - our_cost_per_share

positive meaning WE DID BETTER. This deliberately reuses fill_cash
rather than restating the rule: one function owns the denomination
decision, and a second copy of it is how the first one got missed.

REPORTED AS A DISTRIBUTION, NOT A MEDIAN. "Same or better on 70% of
fills" and "same or better on 99%" have the same median if the tail is
one-sided, and the tail is where the money is. The dollar-weighted
total is the headline: cents per share matter in proportion to the
shares behind them, and a bad price on a $250 clip is not the same
event as a bad price on a $3 one.

Pure, so it can be tested without a venue or a database.
"""

from __future__ import annotations

from typing import Any

# At or better, allowing for the venue's own rounding on a whole-unit
# fill. A tenth of a cent is below the tick everywhere we trade, so a
# fill inside it is the same price, not a worse one.
SAME_PRICE_EPS = 0.001


def fill_edge(his_price: float | None, fill_price: float | None,
              intent: str | None) -> float | None:
    """His price minus OUR cost per share. Positive = we did better.

    None when either side is unknown — an unmeasurable fill must not
    be scored as a neutral one, because a pile of zeros drags every
    average toward "we matched him exactly".
    """
    from ..live_executor import cost_per_share

    try:
        hp = float(his_price)
        fp = float(fill_price)
    except (TypeError, ValueError):
        return None
    if not (0.0 < hp < 1.0) or not (0.0 < fp < 1.0):
        return None
    # OUR COST PER SHARE, UNROUNDED. This used to read
    # fill_cash(1.0, fp, intent), and fill_cash ends in
    # round(shares * per, 2) -- so asking it for a one-share cost
    # quantized the RATE to a whole cent before the subtraction below.
    # Sub-cent slippage, which is the entire size of the edge, came out
    # as exactly zero; see cost_per_share for the arithmetic.
    return hp - cost_per_share(fp, intent)


def assess(rows: list[dict]) -> dict:
    """Score a set of fills for price fidelity.

    Rows carry his_price, fill_price, intent, filled_shares.
    """
    from ..live_executor import fill_cash

    # (edge_per_share, shares, our_cost_dollars)
    scored: list[tuple[float, float, float]] = []
    unmeasurable = 0
    for r in rows:
        e = fill_edge(r.get("his_price"), r.get("fill_price"),
                      r.get("intent"))
        if e is None:
            unmeasurable += 1
            continue
        try:
            sh = max(float(r.get("filled_shares") or 0), 0.0)
        except (TypeError, ValueError):
            sh = 0.0
        scored.append((e, sh, fill_cash(sh, r.get("fill_price"),
                                        r.get("intent"))))
    n = len(scored)
    out: dict[str, Any] = {"n": n, "unmeasurable": unmeasurable}
    if not n:
        out["verdict"] = ("NO MEASURABLE FILLS — every row is missing his "
                          "price, our fill price, or both")
        return out
    edges = sorted(e for e, _, _ in scored)
    at_or_better = sum(1 for e in edges if e >= -SAME_PRICE_EPS)

    def _pct(p: float) -> float:
        """Linear-interpolated percentile, in cents.

        Nearest-rank would make the median of a two-fill sample the
        WORSE of the two, which reads as a systematically bad median on
        exactly the small samples this will be run on first."""
        if len(edges) == 1:
            return round(edges[0] * 100, 3)
        pos = p * (len(edges) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(edges) - 1)
        frac = pos - lo
        return round((edges[lo] + (edges[hi] - edges[lo]) * frac) * 100, 3)

    shares = sum(s for _, s, _ in scored)
    dollars = sum(e * s for e, s, _ in scored)
    deployed = sum(c for _, _, c in scored)
    out.update({
        "at_or_better": at_or_better,
        "at_or_better_share": round(at_or_better / n, 4),
        "median_edge_cents": _pct(0.5),
        "p10_edge_cents": _pct(0.10),
        "p90_edge_cents": _pct(0.90),
        "worst_edge_cents": round(edges[0] * 100, 3),
        "shares": round(shares, 2),
        # THE HEADLINE. Cents per share matter in proportion to the
        # shares behind them: a bad price on a $250 clip is not the
        # same event as a bad price on a $3 one.
        "deployed": round(deployed, 2),
        "dollar_edge_vs_his_price": round(dollars, 2),
        # Per 100 DOLLARS deployed, not per 100 shares. A cent saved on
        # a 4-cent contract is a far bigger edge than a cent saved on a
        # 90-cent one, and only the dollar denominator says so.
        "edge_per_100_deployed": (round(100.0 * dollars / deployed, 4)
                                  if deployed > 0 else None),
    })
    out["verdict"] = (
        f"{out['at_or_better_share']:.1%} of {n} fills at his price or "
        f"better; median {out['median_edge_cents']:+.2f}c, worst "
        f"{out['worst_edge_cents']:+.2f}c; "
        f"${out['dollar_edge_vs_his_price']:+,.2f} versus paying exactly "
        f"what he paid")
    return out


async def cohort_fidelity(pool: Any, since: str) -> dict:
    """Price fidelity over filled copies placed since a cutoff."""
    import datetime as _dt

    from ..live_executor import ORDER_INTENT_SQL

    ts = _dt.datetime.fromisoformat(str(since))
    rows = await pool.fetch(
        f"""
        SELECT whale_username,
               his_price::float8   AS his_price,
               fill_price::float8  AS fill_price,
               filled_shares::float8 AS filled_shares,
               {ORDER_INTENT_SQL}  AS intent
          FROM live_orders
         WHERE placed_at >= $1
           AND fill_price IS NOT NULL
           AND COALESCE(filled_shares, 0) > 0
           AND COALESCE(whale_username, '') NOT IN ('manual', 'underdog')
        """, ts)
    rows = [dict(r) for r in rows]
    per: dict[str, dict] = {}
    for w in {str(r["whale_username"] or "?").lower() for r in rows}:
        per[w] = assess([r for r in rows
                         if str(r["whale_username"] or "?").lower() == w])
    return {"since": since, "overall": assess(rows), "by_whale": per}
