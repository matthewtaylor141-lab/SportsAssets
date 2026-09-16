#!/usr/bin/env python3
"""BOOK-STATE measurements from a SAMPLED capture. Not one of them is a fill rate.

WHAT THIS FILE IS FOR.

The capture cannot identify a fill, and that is settled -- this is a BOOK-STATE
experiment, not a fill-identification one. It does not follow that the capture is
worth nothing: everything below is a property of the BOOK, and every one is an
execution input a maker needs before it quotes anything.

TWO THINGS THE NAMES HAVE TO CARRY, BECAUSE PROSE DOES NOT TRAVEL WITH A NUMBER.

1. SNAPSHOT POLLING IS NOT CONTINUOUS OBSERVATION. The collector revisits each
   market at a finite cadence. Between two polls a quote may move away, trade,
   return, change depth, or disappear and reappear, and NONE of that is
   observed. So every quantity derived from the sequence is suffixed _OBSERVED
   and means "measured from sampled snapshots at the revisit cadence reported
   beside it", never "this is what the market did".

       TRUE_CONTINUOUS_QUOTE_LIFETIME    NOT_IDENTIFIED
       TRUE_CONTINUOUS_BOOK_UPDATE_RATE  NOT_IDENTIFIED

   and they stay NOT_IDENTIFIED unless the feed itself provides event-complete
   semantics, which this capture does not establish. The revisit distribution
   (P10 / MEDIAN / P90 / MAX) is reported per market so a reader can see how
   coarse the sampling was that produced every other figure.

   The direction of the bias is knowable even where its size is not: unobserved
   round trips make a quote look LONGER-lived and the book look CALMER than it
   was. Every _OBSERVED rate is therefore a LOWER bound on activity.

2. MOVE-THROUGH IS A PRICE PATH, NOT AN EXECUTION.
       MOVE_THROUGH != TRADE
       MOVE_THROUGH != COUNTERFACTUAL_FILL
   It is useful for execution-opportunity diagnostics, markout analysis and
   quote placement. Repeated move-through observations are NOT converted into a
   fill probability -- `move_through_fill_probability()` exists only to raise.

THE NAMING RULE, ENFORCED BY TEST. No measured key may be named like an
execution outcome. FILL_RATE, MAKER_FILL_RATE, EXECUTION_RATE, FILL_PROBABILITY
and HIT_RATE are listed as forbidden, and a test walks every numeric key.

EVENT CLUSTERING. Several captured markets can belong to one event, and those
are not independent observations. Where an event map is supplied, each rate is
reported BOTH market-weighted and event-weighted, so one large event family
cannot carry the result.
"""
from __future__ import annotations

from decimal import Decimal as D

NOT_IDENTIFIED = "NOT_IDENTIFIED"

TICK_SIZE = D("0.01")

# Names this module refuses to produce, however convenient the shorthand.
FORBIDDEN_RENAMES = ("FILL_RATE", "MAKER_FILL_RATE", "EXECUTION_RATE",
                     "FILL_PROBABILITY", "HIT_RATE")
THESE_ARE_BOOK_QUANTITIES_NOT_EXECUTION_OUTCOMES = True

# What sampling cannot see, listed so nobody has to remember it.
UNOBSERVED_BETWEEN_POLLS = ("QUOTE_MOVED_AWAY_AND_RETURNED", "TRADED",
                            "DEPTH_CHANGED_AND_CHANGED_BACK",
                            "DISAPPEARED_AND_REAPPEARED")
TRUE_CONTINUOUS_QUOTE_LIFETIME = NOT_IDENTIFIED
TRUE_CONTINUOUS_BOOK_UPDATE_RATE = NOT_IDENTIFIED
FEED_IS_EVENT_COMPLETE = NOT_IDENTIFIED
OBSERVED_RATES_ARE_A_LOWER_BOUND_ON_ACTIVITY = True

MOVE_THROUGH_IS_A_PRICE_PATH_OBSERVATION = True
MOVE_THROUGH_IS_NOT_TRADE = True
MOVE_THROUGH_IS_NOT_A_COUNTERFACTUAL_FILL = True

MARKOUT_HORIZONS_S = (30, 60, 300)

# Rates that exist in both weightings when an event map is supplied.
WEIGHTED_RATES = ("ONE_TICK_UPTIME_OBSERVED", "BOOK_UPDATE_RATE_OBSERVED",
                  "MID_MOVE_FREQUENCY_OBSERVED",
                  "PRICE_IMPROVEMENT_FREQUENCY_OBSERVED",
                  "DEPTH_CHANGE_RATE_OBSERVED",
                  "MARKET_MOVED_THROUGH_QUOTE_OBSERVED")


class MoveThroughIsNotAFill(RuntimeError):
    """Raised when a price path is asked to become a fill probability."""


def move_through_fill_probability(*_a, **_k):
    """The named conversion this module refuses. Calling it raises.

    A price moving through a level we were not quoting at says the level was
    reachable. It says nothing about queue position, about which side consumed
    the depth, or about whether anything traded at all.
    """
    raise MoveThroughIsNotAFill(
        "MOVE_THROUGH != TRADE != COUNTERFACTUAL_FILL. A price path does not "
        "become a fill probability by being observed repeatedly.")


def _d(v):
    if v is None or v == NOT_IDENTIFIED:
        return NOT_IDENTIFIED
    if isinstance(v, float):
        raise TypeError("refusing a float book quantity")
    return v if isinstance(v, D) else D(str(v))


def _mid(r):
    b, a = _d(r.get("BID")), _d(r.get("ASK"))
    if b == NOT_IDENTIFIED or a == NOT_IDENTIFIED:
        return NOT_IDENTIFIED
    return (b + a) / D("2")


def _pct(sorted_vals, p):
    """Nearest-rank percentile. No interpolation between two observed books."""
    if not sorted_vals:
        return NOT_IDENTIFIED
    k = max(0, min(len(sorted_vals) - 1,
                   int(round((p / 100.0) * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def _dist(vals, name):
    v = sorted(x for x in vals if x != NOT_IDENTIFIED)
    if not v:
        return {name + "_N": 0, name + "_P10": NOT_IDENTIFIED,
                name + "_P50": NOT_IDENTIFIED, name + "_P90": NOT_IDENTIFIED,
                name + "_MIN": NOT_IDENTIFIED, name + "_MAX": NOT_IDENTIFIED}
    return {name + "_N": len(v), name + "_P10": _pct(v, 10),
            name + "_P50": _pct(v, 50), name + "_P90": _pct(v, 90),
            name + "_MIN": v[0], name + "_MAX": v[-1]}


def _rows_by_slug(rows):
    out, errs = {}, {}
    for r in rows:
        s = r.get("slug")
        if r.get("kind") == "TICK_ERROR":
            errs[s] = errs.get(s, 0) + 1
            continue
        if r.get("ELAPSED_S") is None:
            continue
        out.setdefault(s, []).append(r)
    for s in out:
        out[s].sort(key=lambda r: r["ELAPSED_S"])
    return out, errs


# ---------------------------------------------------------------------------
# HOW OFTEN WE ACTUALLY LOOKED -- reported before anything derived from it
# ---------------------------------------------------------------------------

def revisit_cadence(rows):
    """The gap between consecutive reads of the SAME market.

    This is the resolution limit of every other number in this module. A
    MEDIAN of 12 s means no _OBSERVED lifetime below 12 s exists, and a MAX of
    300 s means somewhere in the capture there is a five-minute hole in which
    anything could have happened.
    """
    by_slug, _ = _rows_by_slug(rows)
    pooled, per_slug = [], {}
    for slug, rs in by_slug.items():
        gaps = [D(str(round(b["ELAPSED_S"] - a["ELAPSED_S"], 3)))
                for a, b in zip(rs, rs[1:])]
        pooled.extend(gaps)
        g = sorted(gaps)
        per_slug[slug] = {
            "REVISITS": len(gaps),
            "MEDIAN_REVISIT_INTERVAL_S": _pct(g, 50),
            "P10_REVISIT_INTERVAL_S": _pct(g, 10),
            "P90_REVISIT_INTERVAL_S": _pct(g, 90),
            "MAX_REVISIT_INTERVAL_S": g[-1] if g else NOT_IDENTIFIED,
        }
    p = sorted(pooled)
    return {
        "MEDIAN_REVISIT_INTERVAL_S": _pct(p, 50),
        "P10_REVISIT_INTERVAL_S": _pct(p, 10),
        "P90_REVISIT_INTERVAL_S": _pct(p, 90),
        "MAX_REVISIT_INTERVAL_S": p[-1] if p else NOT_IDENTIFIED,
        "REVISIT_INTERVALS_OBSERVED": len(p),
        "PER_SLUG": per_slug,
        "SAMPLING_IS_NOT_CONTINUOUS_OBSERVATION": True,
        "UNOBSERVED_BETWEEN_POLLS": list(UNOBSERVED_BETWEEN_POLLS),
    }


def _slug_metrics(rs, tick_size):
    """Per-market rates, so they can be weighted two different ways."""
    obs = moves = mid_moves = improvements = depth_changes = through = 0
    one_tick = spreads = 0
    prev = None
    for r in rs:
        sp = _d(r.get("SPREAD"))
        if sp != NOT_IDENTIFIED:
            spreads += 1
            if sp == tick_size:
                one_tick += 1
        if prev is not None:
            obs += 1
            b, a = _d(r.get("BID")), _d(r.get("ASK"))
            pb, pa = _d(prev.get("BID")), _d(prev.get("ASK"))
            if r.get("BID_CHANGED") is True or r.get("ASK_CHANGED") is True:
                moves += 1
            if r.get("DEPTH_CHANGED") is True:
                depth_changes += 1
            m, pm = _mid(r), _mid(prev)
            if m != NOT_IDENTIFIED and pm != NOT_IDENTIFIED and m != pm:
                mid_moves += 1
            if (b != NOT_IDENTIFIED and pb != NOT_IDENTIFIED and b > pb) \
                    or (a != NOT_IDENTIFIED and pa != NOT_IDENTIFIED and a < pa):
                improvements += 1
            # The market reaching a level we WOULD have quoted at the previous
            # read. A price path, and nothing more.
            if pb != NOT_IDENTIFIED and a != NOT_IDENTIFIED and a <= pb:
                through += 1
        prev = r
    r_ = lambda n, d: (D(n) / D(d)) if d else NOT_IDENTIFIED   # noqa: E731
    return {
        "OBSERVATIONS": len(rs),
        "OBSERVED_TRANSITIONS": obs,
        "ONE_TICK_UPTIME_OBSERVED": r_(one_tick, spreads),
        "BOOK_UPDATE_RATE_OBSERVED": r_(moves, obs),
        "MID_MOVE_FREQUENCY_OBSERVED": r_(mid_moves, obs),
        "PRICE_IMPROVEMENT_FREQUENCY_OBSERVED": r_(improvements, obs),
        "DEPTH_CHANGE_RATE_OBSERVED": r_(depth_changes, obs),
        "MARKET_MOVED_THROUGH_QUOTE_OBSERVED": r_(through, obs),
        "_counts": {"one_tick": one_tick, "spreads": spreads, "moves": moves,
                    "mid_moves": mid_moves, "improvements": improvements,
                    "depth_changes": depth_changes, "through": through,
                    "obs": obs},
    }


def _event_weighted(per_slug, event_of):
    """Average within each event, then across events. One event, one vote."""
    if not event_of:
        return {k + "_EVENT_WEIGHTED": NOT_IDENTIFIED for k in WEIGHTED_RATES}
    by_event = {}
    for slug, m in per_slug.items():
        by_event.setdefault(event_of.get(slug, NOT_IDENTIFIED), []).append(m)
    out = {}
    for key in WEIGHTED_RATES:
        ev_vals = []
        for _, ms in by_event.items():
            vals = [m[key] for m in ms if m[key] != NOT_IDENTIFIED]
            if vals:
                ev_vals.append(sum(vals) / D(len(vals)))
        out[key + "_EVENT_WEIGHTED"] = (sum(ev_vals) / D(len(ev_vals))
                                        if ev_vals else NOT_IDENTIFIED)
    out["EVENTS_IN_WEIGHTING"] = len(by_event)
    out["ONE_EVENT_ONE_VOTE"] = True
    return out


def book_metrics(rows, tick_size=TICK_SIZE, horizons_s=MARKOUT_HORIZONS_S,
                 event_of=None):
    """Every book-state quantity, over one SAMPLED capture. Contacts nothing."""
    by_slug, errs = _rows_by_slug(rows)
    cadence = revisit_cadence(rows)

    spreads, touch_bid_sz, touch_ask_sz, queue_ahead = [], [], [], []
    life_bid, life_ask, time_at_price = [], [], []
    spread_runs, queue_deltas = [], []
    markout = {h: [] for h in horizons_s}
    per_slug, totals = {}, {"one_tick": 0, "spreads": 0, "moves": 0,
                            "mid_moves": 0, "improvements": 0,
                            "depth_changes": 0, "through": 0, "obs": 0}

    for slug, rs in by_slug.items():
        m = _slug_metrics(rs, tick_size)
        counts = m.pop("_counts")
        for k in totals:
            totals[k] += counts[k]
        m["TICK_ERRORS"] = errs.get(slug, 0)
        m.update(cadence["PER_SLUG"].get(slug, {}))
        per_slug[slug] = m

        run_val, run_start = None, None
        prev = None
        for r in rs:
            sp = _d(r.get("SPREAD"))
            if sp != NOT_IDENTIFIED:
                spreads.append(sp)
            bq, aq = _d(r.get("BID_QTY")), _d(r.get("ASK_QTY"))
            if bq != NOT_IDENTIFIED:
                touch_bid_sz.append(bq)
                queue_ahead.append(bq)
            if aq != NOT_IDENTIFIED:
                touch_ask_sz.append(aq)
            for k, acc in (("TIME_AT_BID_S", life_bid),
                           ("TIME_AT_ASK_S", life_ask),
                           ("QUOTE_LIFETIME_S", time_at_price)):
                v = r.get(k)
                if isinstance(v, (int, str)) and v != NOT_IDENTIFIED:
                    try:
                        acc.append(D(str(v)))
                    except Exception:                          # noqa: BLE001
                        pass
            if prev is not None and bq != NOT_IDENTIFIED:
                pq = _d(prev.get("BID_QTY"))
                if pq != NOT_IDENTIFIED and _d(r.get("BID")) == _d(
                        prev.get("BID")):
                    queue_deltas.append(abs(bq - pq))
            if run_val is None or sp != run_val:
                if run_val is not None and run_start is not None:
                    spread_runs.append(D(str(round(
                        r["ELAPSED_S"] - run_start, 3))))
                run_val, run_start = sp, r["ELAPSED_S"]
            prev = r

        for i, r in enumerate(rs):
            m0 = _mid(r)
            if m0 == NOT_IDENTIFIED:
                continue
            for h in horizons_s:
                pick = None
                for later in rs[i + 1:]:
                    if later["ELAPSED_S"] - r["ELAPSED_S"] <= h:
                        pick = later
                    else:
                        break
                if pick is not None and _mid(pick) != NOT_IDENTIFIED:
                    markout[h].append(_mid(pick) - m0)

    t, obs = totals, totals["obs"]
    r_ = lambda n, d: (D(n) / D(d)) if d else NOT_IDENTIFIED    # noqa: E731
    out = {
        "MARKETS": len(by_slug),
        "OBSERVATIONS": sum(len(v) for v in by_slug.values()),
        "OBSERVED_TRANSITIONS": obs,
        "TICK_ERRORS": sum(errs.values()),

        # --- the resolution limit, before anything derived from it ---
        "REVISIT_CADENCE": {k: v for k, v in cadence.items()
                            if k != "PER_SLUG"},

        # --- sampled rates. MARKET-WEIGHTED (every observation counts once) ---
        "ONE_TICK_UPTIME_OBSERVED": r_(t["one_tick"], t["spreads"]),
        "BOOK_UPDATE_RATE_OBSERVED": r_(t["moves"], obs),
        "MID_MOVE_FREQUENCY_OBSERVED": r_(t["mid_moves"], obs),
        "PRICE_IMPROVEMENT_FREQUENCY_OBSERVED": r_(t["improvements"], obs),
        "DEPTH_CHANGE_RATE_OBSERVED": r_(t["depth_changes"], obs),
        "MARKET_MOVED_THROUGH_QUOTE_OBSERVED": r_(t["through"], obs),
        "WEIGHTING": "MARKET_WEIGHTED",

        # --- what sampling cannot give ---
        "TRUE_CONTINUOUS_QUOTE_LIFETIME": TRUE_CONTINUOUS_QUOTE_LIFETIME,
        "TRUE_CONTINUOUS_BOOK_UPDATE_RATE": TRUE_CONTINUOUS_BOOK_UPDATE_RATE,
        "FEED_IS_EVENT_COMPLETE": FEED_IS_EVENT_COMPLETE,
        "OBSERVED_RATES_ARE_A_LOWER_BOUND_ON_ACTIVITY":
            OBSERVED_RATES_ARE_A_LOWER_BOUND_ON_ACTIVITY,
        "EVERY_INTRAINTERVAL_TRANSITION_OBSERVED": False,

        # --- the price-path observation, and what it is not ---
        "MOVE_THROUGH_IS_NOT_TRADE": MOVE_THROUGH_IS_NOT_TRADE,
        "MOVE_THROUGH_IS_NOT_A_COUNTERFACTUAL_FILL":
            MOVE_THROUGH_IS_NOT_A_COUNTERFACTUAL_FILL,

        "PER_SLUG": per_slug,
    }
    out.update(_event_weighted(per_slug, event_of))
    out.update(_dist(spreads, "SPREAD"))
    out["SPREAD_IS_A_SAMPLED_STATE_DISTRIBUTION"] = True
    out.update(_dist(spread_runs, "SPREAD_PERSISTENCE_OBSERVED_S"))
    out.update(_dist(touch_bid_sz, "TOUCH_SIZE_BID"))
    out.update(_dist(touch_ask_sz, "TOUCH_SIZE_ASK"))
    out.update(_dist(queue_ahead, "QUEUE_AHEAD_AT_HYPOTHETICAL_ENTRY"))
    out.update(_dist(queue_deltas, "DISPLAYED_QUEUE_CHANGE"))
    out.update(_dist(life_bid, "QUOTE_LIFETIME_OBSERVED_BID_S"))
    out.update(_dist(life_ask, "QUOTE_LIFETIME_OBSERVED_ASK_S"))
    out.update(_dist(time_at_price, "TIME_AT_PRICE_OBSERVED_S"))
    for h in horizons_s:
        out.update(_dist(markout[h], "POST_QUOTE_BOOK_MARKOUT_%dS" % h))
    return out


def report(rows, event_of=None):
    """The book half of the Phase-2A harvest, with the refusals attached."""
    m = book_metrics(rows, event_of=event_of)
    m.update({
        "THESE_ARE_BOOK_QUANTITIES_NOT_EXECUTION_OUTCOMES": True,
        "FILL_RATE_NOT_PRESENT": True,
        "WHY": ("a fill rate is a statement about orders; every quantity here "
                "is a statement about the book at a sampled cadence, and no "
                "order existed"),
    })
    return m
