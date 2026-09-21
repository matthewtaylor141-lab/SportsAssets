"""MEASUREMENTS THAT NEED NO ORDER OF OURS.

The standing position has been that BETTOR's economics cannot be
measured until a BETTOR order rests. That is true of exactly two
quantities and false of everything else:

    NEEDS OUR ORDER                 NEEDS ONLY PUBLIC OBSERVATION
    p_fill                          market activity
    realized inventory duration     hypothetical-quote markout
    queue position                  opportunity persistence
    realized P&L                    spread and depth at the touch
                                    counter-differenced volume

A hypothetical markout is not a fill-conditioned result. It asks: IF a
quote had rested at this price at time t, what did the midpoint do by
t+dt? Every input is public. It cannot tell us whether we would have
been filled -- and precisely because it is unconditional on filling, it
is not contaminated by the selection that makes fill-conditioned
estimates so hard. It can eliminate a weak policy now: a quote rule
whose unconditional post-quote drift is adverse at every horizon does
not become profitable once you condition on being filled, because
filling selects the adverse cases.

WHAT EACH MEASURE IS, AND WHAT IT IS NOT

  ACTIVITY        counter-differenced traded volume per market per
                  unit time. NOT the level of a cumulative counter,
                  which says nothing about a window.

  MARKOUT         mid(t+dt) - quote(t), signed for the side quoted.
                  NOT adverse selection: adverse selection is markout
                  CONDITIONAL ON BEING FILLED, and that conditioning
                  is what we cannot do. Reported as
                  UNCONDITIONAL_HYPOTHETICAL_MARKOUT, always.

  PERSISTENCE     how long a market continuously satisfies the frozen
                  universe rule. NOT how long a quote would live: an
                  order's lifetime depends on the queue, which is not
                  observable.

EVIDENCE CLASSES ARE ON EVERY RESULT.

  PUBLIC_OBSERVATION      measured from observed public book state
  PUBLIC_COUNTER_DELTA    measured by differencing a venue counter
  NOT_IDENTIFIED          the data cannot answer it, with the reason

THE REASON THIS MODULE MOSTLY RETURNS NOT_IDENTIFIED TODAY. Measuring
any of this needs the SAME market observed MORE THAN ONCE. The
research capture is a coverage sampler, not a time series: measured
2026-09-21, 617 of 620 markets carrying a numeric traded-volume
counter were observed EXACTLY ONCE in a 24.24-hour window, the whole
table contains 3 consecutive observation pairs, and their summed
separation is 0.9 seconds. None of these measures is impossible; the
instrument for them does not exist yet. `bettor_live_loop` is that
instrument -- it observes every subscribed market on every update and
journals bid, ask and the traded-volume counter with three clocks.
"""

from __future__ import annotations

from . import bettor_universe as uni

MEASURES_VERSION = "BETTOR_MARKET_MEASURES_V1"

PUBLIC_OBSERVATION = "PUBLIC_OBSERVATION"
PUBLIC_COUNTER_DELTA = "PUBLIC_COUNTER_DELTA"
NOT_IDENTIFIED = "NOT_IDENTIFIED"

# Horizons, in seconds, for the hypothetical markout. Chosen to bracket
# the 300 s exit the v3 maker protocol proposes.
MARKOUT_HORIZONS_S = (10.0, 30.0, 60.0, 300.0, 900.0)

# A series point further from the horizon than this is not a
# measurement of that horizon. Without a tolerance a 24-hour gap would
# be reported as a 10-second markout.
HORIZON_TOLERANCE = 0.5     # fraction of the horizon

MIN_PAIRS_FOR_A_NUMBER = 30


def _f(v):
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("value")
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _first(r, *keys):
    """The first key PRESENT, not the first key truthy.

    `r.get("at") or r.get("observed_at")` silently skips a clock of
    0.0 and a bid of 0.0, and both are legal values here.
    """
    for k in keys:
        if k in r and r[k] is not None:
            return r[k]
    return None


def _t(v):
    """Observation time -> epoch seconds. Returns None rather than
    guessing: an unparsable clock is not time zero."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    from datetime import datetime
    s = str(v).replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        from datetime import timezone
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def to_series(rows) -> dict:
    """Group observation rows into per-market series ordered by time.

    Accepts the capture's spellings and the live journal's, because
    they are the same measurement from two instruments and the point of
    this module is that the second one can answer what the first
    cannot.
    """
    by: dict = {}
    dropped = {"no_slug": 0, "no_clock": 0, "no_book": 0}
    for r in rows:
        slug = (r.get("slug") or r.get("market_id") or r.get("marketSlug"))
        if not slug:
            dropped["no_slug"] += 1
            continue
        at = _t(_first(r, "at", "observed_at", "source_ts",
                       "book_source_ts"))
        if at is None:
            dropped["no_clock"] += 1
            continue
        top = r.get("top_of_book") or {}
        bid = _f(_first(r, "bid", "yes_bid") if ("bid" in r or "yes_bid" in r)
                 else top.get("bid"))
        ask = _f(_first(r, "ask", "yes_ask") if ("ask" in r or "yes_ask" in r)
                 else top.get("ask"))
        if bid is None or ask is None:
            dropped["no_book"] += 1
            continue
        mid = _f(_first(r, "mid"))
        if mid is None:
            mid = (bid + ask) / 2.0
        by.setdefault(slug, []).append({
            "at": at, "bid": bid, "ask": ask, "mid": mid,
            "vol": _f(_first(r, "vol", "stats_shares_traded",
                             "sharesTraded")),
            "state": _first(r, "state", "venue_state"),
        })
    for pts in by.values():
        pts.sort(key=lambda p: p["at"])
    return {"series": by, "markets": len(by), "dropped": dropped,
            "points": sum(len(v) for v in by.values())}


def coverage(series: dict) -> dict:
    """CAN ANY OF THIS BE MEASURED AT ALL? Answered before anything is
    computed, because a measure over one observation per market is not
    a small sample -- it is a category error."""
    counts = sorted(len(v) for v in series.values())
    pairs = sum(max(0, n - 1) for n in counts)
    gaps = []
    for pts in series.values():
        for a, b in zip(pts, pts[1:]):
            gaps.append(b["at"] - a["at"])
    gaps.sort()

    def q(xs, p):
        return round(xs[min(len(xs) - 1, int(p * len(xs)))], 3) if xs else None

    return {
        "markets": len(counts),
        "observations": sum(counts),
        "consecutive_pairs": pairs,
        "markets_seen_once_only": sum(1 for n in counts if n == 1),
        "observations_per_market": {
            "min": counts[0] if counts else None,
            "median": q(counts, 0.5), "max": counts[-1] if counts else None},
        "gap_seconds": {"n": len(gaps), "min": q(gaps, 0.0),
                        "median": q(gaps, 0.5), "max": q(gaps, 1.0),
                        "total": round(sum(gaps), 3)},
        "sufficient_for_a_number": pairs >= MIN_PAIRS_FOR_A_NUMBER,
        "threshold": MIN_PAIRS_FOR_A_NUMBER,
    }


# ── 1. activity ──────────────────────────────────────────────────────

def activity(series: dict, *, min_pairs: int = None) -> dict:
    """Traded volume by DIFFERENCING the venue's counter.

    The level of a cumulative counter measures the market's life, not
    a window. Only differences between two timestamped observations of
    the SAME market measure anything about the interval between them.

    A backwards step is counted, never clipped: it means the counter is
    not what we think it is, and silently taking max(0, delta) would
    hide exactly that.
    """
    floor = MIN_PAIRS_FOR_A_NUMBER if min_pairs is None else int(min_pairs)
    pairs = resets = unchanged = 0
    shares = notional = covered_s = 0.0
    per_market: dict = {}
    for slug, pts in series.items():
        for a, b in zip(pts, pts[1:]):
            if a["vol"] is None or b["vol"] is None:
                continue
            pairs += 1
            dt = b["at"] - a["at"]
            covered_s += max(0.0, dt)
            d = b["vol"] - a["vol"]
            if d < 0:
                resets += 1
                continue
            if d == 0:
                unchanged += 1
                continue
            # PRICED AT THE INTERVAL'S OWN MID, not at one arbitrary
            # mid for the whole table: shares and dollars must share a
            # unit basis or the product means nothing.
            px = (a["mid"] + b["mid"]) / 2.0
            shares += d
            notional += d * px
            m = per_market.setdefault(slug, {"shares": 0.0, "usd": 0.0,
                                             "covered_s": 0.0})
            m["shares"] += d
            m["usd"] += d * px
            m["covered_s"] += max(0.0, dt)

    if pairs < floor:
        # THE SAME THRESHOLD AS EVERY OTHER MEASURE HERE. One interval
        # is not a measurement of market activity, and reporting it as
        # PUBLIC_COUNTER_DELTA would put an evidence class on a single
        # pair of observations.
        return {"measure": "ACTIVITY", "evidence_class": NOT_IDENTIFIED,
                "why": ("%d consecutive observation pairs carry a "
                        "numeric counter; %d are needed. With none, no "
                        "interval exists to difference at all"
                        % (pairs, floor)),
                "pairs": pairs, "threshold": floor}
    out = {
        "measure": "ACTIVITY",
        "evidence_class": PUBLIC_COUNTER_DELTA,
        "pairs": pairs, "counter_went_backwards": resets,
        "unchanged": unchanged,
        "shares": round(shares, 4), "notional_usd": round(notional, 2),
        "covered_seconds": round(covered_s, 3),
        "markets_that_traded": len(per_market),
        "effective_price_usd": (round(notional / shares, 4)
                                if shares else None),
    }
    # A RATE, NOT A DAY. Extrapolating a measured interval to 24 hours
    # is an assumption about the other hours, so the interval is
    # reported beside the rate and the extrapolation is named.
    if covered_s > 0:
        out["usd_per_covered_second"] = round(notional / covered_s, 6)
        out["extrapolated_usd_per_day"] = round(
            notional / covered_s * 86400.0, 2)
        out["extrapolation_warning"] = (
            "a rate measured over %.1f s of observed market-time, "
            "multiplied by 86,400. It assumes the unobserved time "
            "trades like the observed time, which nothing here "
            "establishes." % covered_s)
    if resets:
        out["counter_semantics"] = (
            "%d backwards steps: the counter is not monotonically "
            "cumulative, so neither a max() nor a plain difference is "
            "safe without establishing its reset rule" % resets)
    return out


# ── 2. hypothetical quote markout ────────────────────────────────────

def markout(series: dict, *, side: str = "buy",
            horizons=MARKOUT_HORIZONS_S) -> dict:
    """IF a quote had rested here, what did the midpoint do next?

    UNCONDITIONAL. This is not adverse selection and is never reported
    as such: adverse selection is markout conditional on being filled,
    and nothing observable tells us whether we would have been.

    Being unconditional is also what makes it useful right now. Filling
    selects the adverse cases, so a quote rule whose UNCONDITIONAL
    drift is already adverse cannot be rescued by conditioning on a
    fill. That eliminates weak policies without an order of ours.

    `side` names which price the hypothetical quote sits at: "buy"
    rests at the bid and gains when the mid rises.

    REPORTED AS A DECOMPOSITION, because the single number is easy to
    misread. A quote sits half a spread away from the mid, so BOTH
    sides show a positive markout in a quiet market -- that half-spread
    is what a maker is paid, not evidence that the drift is
    favourable:

        markout = half_spread + drift
        half_spread = sign * (mid(t)   - quote(t))   ... always >= 0
        drift       = sign * (mid(t+dt) - mid(t))    ... the adverse part

    DRIFT is the component adverse selection would eat. A rule whose
    drift is more negative than its half-spread loses before any fee.
    """
    sign = 1.0 if side == "buy" else -1.0
    out = {"measure": "HYPOTHETICAL_QUOTE_MARKOUT", "side": side,
           "horizons": {}, "conditional_on_fill": False,
           "is_adverse_selection": False,
           "decomposition": "markout = half_spread + drift",
           "note": ("adverse selection is this quantity CONDITIONAL ON "
                    "BEING FILLED. That conditioning needs an order of "
                    "ours and is not performed here")}
    any_measured = False
    for h in horizons:
        tol = h * HORIZON_TOLERANCE
        deltas, drifts, edges = [], [], []
        for pts in series.values():
            for i, a in enumerate(pts):
                quote = a["bid"] if side == "buy" else a["ask"]
                # The nearest later point within tolerance of the
                # horizon. A point far from the horizon measures a
                # different horizon, so it is not used for this one.
                best = None
                for b in pts[i + 1:]:
                    d = b["at"] - a["at"]
                    if d > h + tol:
                        break
                    if abs(d - h) <= tol:
                        if best is None or abs(d - h) < abs(
                                best["at"] - a["at"] - h):
                            best = b
                if best is not None:
                    deltas.append(sign * (best["mid"] - quote))
                    drifts.append(sign * (best["mid"] - a["mid"]))
                    edges.append(sign * (a["mid"] - quote))
        if len(deltas) >= MIN_PAIRS_FOR_A_NUMBER:
            any_measured = True
            n = len(deltas)
            srt = sorted(deltas)
            sdr = sorted(drifts)
            out["horizons"]["%gs" % h] = {
                "evidence_class": PUBLIC_OBSERVATION, "n": n,
                "mean_usd_per_share": round(sum(deltas) / n, 6),
                "median_usd_per_share": round(srt[n // 2], 6),
                "p10": round(srt[int(0.10 * n)], 6),
                "p90": round(srt[int(0.90 * n)], 6),
                # THE ADVERSE COMPONENT, on its own. A positive markout
                # that is entirely half-spread is not a favourable
                # market; it is a market that has not moved yet.
                "mean_drift_usd_per_share": round(sum(drifts) / n, 6),
                "median_drift_usd_per_share": round(sdr[n // 2], 6),
                "mean_half_spread_usd_per_share": round(sum(edges) / n, 6),
            }
        else:
            out["horizons"]["%gs" % h] = {
                "evidence_class": NOT_IDENTIFIED, "n": len(deltas),
                "why": ("fewer than %d observation pairs separated by "
                        "%gs +/- %d%%" % (MIN_PAIRS_FOR_A_NUMBER, h,
                                          int(HORIZON_TOLERANCE * 100))),
            }
    out["evidence_class"] = (PUBLIC_OBSERVATION if any_measured
                             else NOT_IDENTIFIED)
    return out


# ── 3. opportunity persistence ───────────────────────────────────────

def persistence(series: dict, *, tick: float = uni.DEFAULT_TICK) -> dict:
    """How long a market CONTINUOUSLY satisfies the frozen universe
    rule, measured as observed runs.

    NOT how long a quote would live. An order's lifetime depends on the
    queue in front of it, which is not observable, and on whether it is
    filled, which is the thing we cannot condition on. This measures
    the OPPORTUNITY, not the order: if a market stops qualifying eight
    seconds after it starts, a policy that takes a minute to decide has
    nothing to trade whatever its fill rate.
    """
    runs = []
    qualifying_points = total_points = 0
    censored = 0
    for slug, pts in series.items():
        run_start = None
        last_at = None
        for p in pts:
            total_points += 1
            a = uni.assess({"slug": slug, "bestBid": p["bid"],
                            "bestAsk": p["ask"], "sharesTraded": p["vol"],
                            "state": p["state"]}, tick=tick)
            if a["included"]:
                qualifying_points += 1
                if run_start is None:
                    run_start = p["at"]
                last_at = p["at"]
            elif run_start is not None:
                runs.append({"slug": slug,
                             "seconds": last_at - run_start,
                             "censored": False})
                run_start = last_at = None
        if run_start is not None:
            # STILL QUALIFYING WHEN OBSERVATION STOPPED. Counted as
            # censored rather than as a run that ended, because the
            # series ending is a fact about us, not about the market.
            runs.append({"slug": slug, "seconds": last_at - run_start,
                         "censored": True})
            censored += 1

    measured = [r["seconds"] for r in runs if not r["censored"]]
    out = {"measure": "OPPORTUNITY_PERSISTENCE",
           "universe_rule": uni.UNIVERSE_VERSION,
           "observations": total_points,
           "qualifying_observations": qualifying_points,
           "runs": len(runs), "runs_censored": censored,
           "is_quote_lifetime": False,
           "note": ("this is how long the OPPORTUNITY lasts, not how "
                    "long an order would rest: queue position is not "
                    "observable")}
    if len(measured) < MIN_PAIRS_FOR_A_NUMBER:
        out["evidence_class"] = NOT_IDENTIFIED
        out["why"] = ("%d uncensored runs; a run needs a market observed "
                      "at least twice while it qualifies, and %d of %d "
                      "runs are censored by the series ending"
                      % (len(measured), censored, len(runs)))
        return out
    measured.sort()
    n = len(measured)
    out.update({
        "evidence_class": PUBLIC_OBSERVATION,
        "seconds": {"n": n, "min": round(measured[0], 3),
                    "p25": round(measured[n // 4], 3),
                    "median": round(measured[n // 2], 3),
                    "p75": round(measured[(3 * n) // 4], 3),
                    "max": round(measured[-1], 3)},
    })
    return out


def measure_all(rows, *, side: str = "buy", min_pairs: int = None) -> dict:
    built = to_series(rows)
    series = built["series"]
    cov = coverage(series)
    return {
        "measures": MEASURES_VERSION,
        "needs_no_order_of_ours": True,
        "input": {k: v for k, v in built.items() if k != "series"},
        "coverage": cov,
        "activity": activity(series, min_pairs=min_pairs),
        "markout": markout(series, side=side),
        "persistence": persistence(series),
        "still_needs_our_orders": [
            "p_fill", "queue position", "realized inventory duration",
            "realized P&L", "adverse selection (markout CONDITIONAL on "
            "a fill)"],
    }


def describe() -> dict:
    return {
        "measures": MEASURES_VERSION,
        "evidence_classes": [PUBLIC_OBSERVATION, PUBLIC_COUNTER_DELTA,
                             NOT_IDENTIFIED],
        "measurable_without_our_orders": [
            "market activity, by counter differencing",
            "hypothetical quote markout, unconditional",
            "opportunity persistence under the frozen universe rule",
            "spread and displayed depth at the touch"],
        "not_measurable_without_our_orders": [
            "p_fill", "queue position", "realized inventory duration",
            "realized P&L", "adverse selection"],
        "why_unconditional_markout_still_eliminates_policies": (
            "filling selects the adverse cases, so a quote rule whose "
            "UNCONDITIONAL post-quote drift is already adverse does not "
            "become profitable once conditioned on a fill"),
        "what_the_capture_cannot_do": (
            "measured 2026-09-21: 617 of 620 markets carrying a numeric "
            "traded-volume counter were observed exactly once in a "
            "24.24-hour window; the table holds 3 consecutive pairs "
            "totalling 0.9 seconds of separation. Every measure here "
            "needs the same market observed more than once"),
        "the_instrument_that_can": (
            "bettor_live_loop observes every subscribed market on every "
            "update and journals bid, ask and the traded-volume counter "
            "with three clocks"),
    }
