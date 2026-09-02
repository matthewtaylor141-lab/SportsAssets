"""Is the strategy profitable, and how would we know?

The owner asked for confidence that the company runs as designed AND
that the strategy is mathematically proven profitable. Those are two
different claims needing two different kinds of evidence, and only one
of them is a matter of reading code.

WHAT THE LEDGER SAYS TODAY, unvarnished: 3,351 settled copies,
-$5,398.57 on $149,190.40 staked, -3.62% on dollar deployed. There is
no reading of that number under which the strategy is currently proven
profitable, and no amount of engineering confidence substitutes for it.

WHY THAT NUMBER DOES NOT SETTLE THE QUESTION EITHER. Every copy in it
was placed by a system with at least one defect that is now closed, and
the largest was not a rounding error:

  * EXIT DOUBLING. These whales exit by buying the complementary leg.
    Until 2026-08-25 that arrived labelled BUY and was copied as a
    fresh ENTRY on the leg he was ABANDONING — so our exposure went to
    2x rather than to zero, at a price summing to about $1.00 with his
    entry. Every exit he made cost us twice. The production census now
    shows 79 such buys correctly classified as exits in a single
    window, which is the size of what was being done backwards.
  * SHORT MISDENOMINATION. Cost was computed as filled*price on a leg
    that costs (1-price)*qty, which is what produced "BUY_SHORT n=6
    over=6 clean=0" and got the whole short branch banned.
  * MAPPING COVERAGE. The fill rate was 0.55%: the sample is not only
    contaminated, it is thin.

A ledger produced by a different system does not measure this one. So
the honest instrument is a COHORT: count only copies placed after the
last known money-path defect was closed, state that cutoff out loud,
and refuse to draw a conclusion until the sample can carry one.

THE STATISTICS. ROI on dollar deployed is a RATIO of two sums, not a
mean, so its standard error is the ratio estimator's, not sd/sqrt(n):

    R    = sum(pnl) / sum(stake)
    d_i  = pnl_i - R * stake_i          (residuals, sum to zero)
    SE   = sqrt( n/(n-1) * sum(d^2) ) / sum(stake)

Using sd/sqrt(n) on per-copy ROI would weight a $3 copy equally with a
$250 one and report a tighter interval than the data supports.

WHAT WOULD COUNT AS PROOF. The 95% interval excluding zero on the
positive side. Until then this reports INSUFFICIENT and says how many
more settled copies are needed at the currently observed dispersion —
which is the number the owner actually needs, because it converts
"are we there yet" into a date.

This module is PURE and takes rows. It cannot fabricate a sample.
"""

from __future__ import annotations

import math
from typing import Any

# Two-sided 95%.
Z95 = 1.959963985
# 80% power, one-sided, for the sample-size projection.
Z80 = 0.8416212336

# THE CLEAN-COHORT CUTOFF.
#
# 2026-08-25T14:00Z is when the exit classifier reached production —
# the fix that stopped a whale's exit being copied as a doubled entry.
# It is the last change that altered WHAT WE BUY rather than what we
# report, so it is the first moment the ledger describes the system we
# actually run.
#
# Stated as a constant and reported in every response on purpose. A
# cohort boundary chosen quietly is how a bad result gets tuned away by
# moving the start date, and this number exists to be argued with in
# the open.
COHORT_START = "2026-08-25T14:00:00+00:00"

# THE FLOOR UNDER A PROJECTION (round three, 2026-09-01). required_n
# squares sigma, and sigma from g clusters carries a relative error of
# about 1/sqrt(2(g-1)) -- 32% at g=6, 13% at g=30 -- so a proof horizon
# sized on six games is noise squared. The interval itself uses the
# normal z where a t on five degrees of freedom is 2.57, so a "95%"
# verdict on six games has ~88% coverage. At thirty games the t/z gap
# is under 5% and the projection's own error is smaller than the
# horizon it reports. Below this: no n_needed, no days, and the verdict
# is marked PROVISIONAL rather than EARNS or LOSES.
MIN_PROOF_CLUSTERS = 30


def roi_with_ci(rows: list[dict]) -> dict:
    """ROI on dollar deployed, with a real confidence interval.

    `rows` carry `stake` and `pnl` in dollars. Rows with a
    non-positive stake are dropped and counted — a copy that staked
    nothing cannot inform a return on dollars deployed, and leaving it
    in inflates n while contributing no information.
    """
    pairs: list[tuple[float, float, str]] = []
    dropped = 0
    for r in rows:
        try:
            s = float(r.get("stake") or 0)
            p = float(r.get("pnl") or 0)
        except (TypeError, ValueError):
            dropped += 1
            continue
        if s <= 0:
            dropped += 1
            continue
        pairs.append((s, p, str(r.get("event_key") or "")))
    n = len(pairs)
    tot_s = sum(s for s, _, _ in pairs)
    tot_p = sum(p for _, p, _ in pairs)
    out: dict[str, Any] = {
        "n": n, "dropped_rows": dropped,
        "staked": round(tot_s, 2), "pnl": round(tot_p, 2),
    }
    if n == 0 or tot_s <= 0:
        out.update({"roi": None, "se": None, "ci95": None,
                    "verdict": "NO SAMPLE — nothing settled in this cohort"})
        return out
    r = tot_p / tot_s
    out["roi"] = round(r, 6)
    if n < 2:
        out.update({"se": None, "ci95": None,
                    "verdict": "INSUFFICIENT — one settled copy cannot "
                               "carry an interval"})
        return out
    ss = sum((p - r * s) ** 2 for s, p, _ in pairs)
    se_iid = math.sqrt(n / (n - 1) * ss) / tot_s

    # ── OUR COPIES CLUSTER TOO (2026-08-30) ─────────────────────────
    #
    # The whale statistic was corrected for this in merge_pnl; the same
    # error lived here, on the number that decides whether WE are
    # proven. When we copy three legs of one game we hold three rows
    # driven by one result, and counting them as three independent
    # settled copies makes this interval too narrow — the direction
    # that declares us proven early. Measured on zero-edge simulated
    # books, the iid form rejected the null 13.8% of the time against a
    # nominal 5%.
    #
    # Identical algebra to merge_pnl._lot_interval: sum the residual
    # WITHIN a game before squaring it. With one copy per game this is
    # exactly the old number (G == n), so it is a strict generalisation
    # and test_proof_clustering pins that. Rows without an event_key
    # each form their own cluster, so an unjoined row degrades to the
    # old treatment rather than being silently merged with strangers.
    clus: dict[str, list[float]] = {}
    for i, (s, p, k) in enumerate(pairs):
        kk = k or f"\x00{i}"          # unkeyed rows stay singletons
        c = clus.get(kk)
        if c is None:
            clus[kk] = [s, p]
        else:
            c[0] += s
            c[1] += p
    g = len(clus)
    if g >= 2:
        acc = 0.0
        for s_g, p_g in clus.values():
            e = p_g - r * s_g
            acc += e * e
        se = math.sqrt(max(0.0, g / (g - 1) * acc)) / tot_s
    else:
        se = se_iid
    lo, hi = r - Z95 * se, r + Z95 * se
    out["se"] = round(se, 6)
    out["ci95"] = [round(lo, 6), round(hi, 6)]
    out["clusters"] = g
    out["deff"] = round(se / se_iid, 4) if se_iid > 0 else None
    # Per-DOLLAR dispersion, which is what a projection needs. Derived
    # from the CLUSTERED residuals so the sample-size projection cannot
    # disagree with the interval it is projecting toward — sizing off
    # the iid sigma would promise a proof date the interval can never
    # reach. Scaled by G, not n: the effective sample is games.
    sigma = se * tot_s / math.sqrt(g)
    out["sigma_per_dollar"] = round(sigma * g / tot_s, 6) if tot_s else None
    return out


def required_n(sigma_per_dollar: float, target_edge: float,
               power: bool = True) -> int | None:
    """Settled copies needed for a 95% interval to exclude zero.

    `sigma_per_dollar` is the per-copy dispersion of return per dollar
    staked; `target_edge` the ROI we are trying to demonstrate. With
    `power`, sizes for 80% power (the honest number — an interval that
    excludes zero only half the time it should is not a plan).
    """
    try:
        s, e = float(sigma_per_dollar), abs(float(target_edge))
    except (TypeError, ValueError):
        return None
    if s <= 0 or e <= 0:
        return None
    z = Z95 + (Z80 if power else 0.0)
    return int(math.ceil((z * s / e) ** 2))


def assess(rows: list[dict], target_edge: float | None = None) -> dict:
    """The full read: interval, verdict, and what is still needed."""
    out = roi_with_ci(rows)
    if out.get("ci95") is None:
        out.setdefault("verdict", "INSUFFICIENT")
        out["target_edge"] = target_edge
        return out
    lo, hi = out["ci95"]
    sigma = out.get("sigma_per_dollar") or 0.0
    if lo > 0:
        out["verdict"] = (
            f"PROVEN POSITIVE — 95% interval [{lo:+.2%}, {hi:+.2%}] on "
            f"{out['n']} settled copies excludes zero")
    elif hi < 0:
        out["verdict"] = (
            f"PROVEN NEGATIVE — 95% interval [{lo:+.2%}, {hi:+.2%}] on "
            f"{out['n']} settled copies excludes zero. This is a real "
            f"result, not noise: STOP AND DIAGNOSE.")
    else:
        out["verdict"] = (
            f"INSUFFICIENT — 95% interval [{lo:+.2%}, {hi:+.2%}] on "
            f"{out['n']} settled copies still contains zero. The point "
            f"estimate is {out['roi']:+.2%} and it is not yet evidence "
            f"of anything.")
    # HOW MANY MORE. Sized against the whale's own measured edge when we
    # have one, because that is the return the strategy is trying to
    # inherit — sizing against our own noisy point estimate would
    # demand an absurd sample whenever the estimate is near zero.
    out["target_edge"] = target_edge
    if sigma > 0:
        if target_edge:
            need = required_n(sigma, target_edge)
            out["n_needed_at_target"] = need
            out["n_still_needed"] = max(0, (need or 0) - out["n"])
        # NO SAMPLE SIZE PROVES PROFIT FROM A NEGATIVE ESTIMATE.
        # required_n takes abs(), so feeding it a negative observed ROI
        # returns the n needed to prove a LOSS of that size — a
        # different claim entirely, and one that reads as "almost
        # there" on a dashboard. Say which it is.
        if out["roi"] and out["roi"] > 0:
            out["n_needed_at_observed"] = required_n(sigma, out["roi"])
            out["observed_provable"] = True
        elif out["roi"] is not None and out["roi"] <= 0:
            out["n_needed_at_observed"] = None
            out["observed_provable"] = False
            out["observed_note"] = (
                f"the point estimate is {out['roi']:+.2%}; no sample "
                f"size demonstrates profit from a non-positive estimate "
                f"— the estimate has to turn positive first")
    return out


async def cohort_assess(pool: Any, since: str = COHORT_START,
                        whales: list[str] | None = None) -> dict:
    """Run the assessment over settled copies placed since the cutoff."""
    import datetime as _dt

    since_ts = _dt.datetime.fromisoformat(str(since))
    q = """
        SELECT lo.whale_username,
               COALESCE(lo.filled_usd, lo.requested_usd)::float8 AS stake,
               lo.pnl::float8 AS pnl,
               -- THE GAME THIS COPY BELONGS TO. Three copies of one
               -- whale's three legs on one match are ONE result, not
               -- three, and counting them as three made this interval
               -- too narrow in the direction that declares us proven.
               -- LEFT JOIN on purpose: a row we cannot place into a
               -- game keeps its own cluster (see roi_with_ci) rather
               -- than being merged with unrelated copies.
               COALESCE(NULLIF(m.event_slug, ''),
                        NULLIF(lo.us_market_slug, '')) AS event_key
          FROM live_orders lo
          LEFT JOIN market_tokens mt ON mt.token_id = lo.asset
          LEFT JOIN markets m ON m.condition_id = mt.condition_id
         WHERE lo.placed_at >= $1
           AND lo.pnl IS NOT NULL
           -- TERMINAL ROWS ONLY (2026-08-26).
           --
           -- `pnl IS NOT NULL` was the whole test, and it stopped being
           -- a proxy for "closed" the moment partial exits started
           -- accumulating P&L onto a row that stays 'filled'. Such a
           -- row still HOLDS SHARES, and it was entering the proof
           -- cohort at FULL stake with only its realised partial gain
           -- in the numerator.
           --
           -- That biases the headline in the optimistic direction: the
           -- gain from the part we sold is counted, the exposure we
           -- still carry is not. This is the number reported as
           -- evidence of whether the desk earns, so it has to be
           -- exactly the closed book and nothing else.
           --
           -- 'settled'   the market resolved
           -- 'cashed_out' mirror_exit closed it out entirely
           -- 'filled'    still open, partial exits included -- OUT
           AND lo.status IN ('settled', 'cashed_out')
           -- NOT THE MIRROR BOOK (position mirroring P1, owner order
           -- 2026-09-02 "go for it, let's get this working"; the panel
           -- review's predicate audit). A book is one standing row per
           -- market with an open-time his_price and a lifetime of buys
           -- and sells folded onto it -- not a per-fill copy. roster_auto
           -- reads this cohort to set a whale's PER-FILL clip, so the
           -- book stays out of it or the clip is decided on a blend of
           -- two regimes. Its own line: the status line above is pinned
           -- by name. NULL lanes (every row before 041) keep today's path.
           AND COALESCE(lo.lane,'') <> 'mirror'
           AND COALESCE(lo.whale_username, '') NOT IN ('manual', 'underdog')
           AND COALESCE(lo.filled_usd, lo.requested_usd) > 0
    """
    rows = [dict(r) for r in await pool.fetch(q, since_ts)]
    overall = assess(rows)
    per: dict[str, dict] = {}
    for w in {str(r["whale_username"] or "?").lower() for r in rows}:
        per[w] = assess([r for r in rows
                         if str(r["whale_username"] or "?").lower() == w])
    return {
        "cohort_start": since,
        "cohort_start_is": (
            "the deploy of the exit classifier — the last change that "
            "altered WHAT WE BUY rather than what we report. Copies "
            "before it were placed by a system that copied a whale's "
            "exit as a doubled entry."),
        "overall": overall,
        "by_whale": per,
    }
