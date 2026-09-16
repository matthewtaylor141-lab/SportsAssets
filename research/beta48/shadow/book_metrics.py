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

   THE BIAS DIRECTION IS NOT ONE ANSWER FOR ALL OF THEM. An earlier version of
   this file said "every _OBSERVED rate is a lower bound on activity". That is
   too broad and is withdrawn. The metrics split into two classes that behave
   differently under discrete polling:

   CLASS A -- EVENT / TRANSITION COUNTS. Polling can only MISS a transition;
   it cannot manufacture one. So for counts, and for counts alone:

       OBSERVED_TRANSITION_COUNT <= TRUE_TRANSITION_COUNT

   subject to the snapshots themselves being valid. A legitimate undercount.
   (The FREQUENCIES built from those counts are shares per OBSERVED transition,
   not per unit time, so they inherit no such bound -- their relation to a
   continuous-time rate is NOT_IDENTIFIED and is labelled so.)

   CLASS B -- STATE OCCUPANCY AND DURATION. These are interval-censored
   samples, and the error runs BOTH ways:

       a quote that disappears and returns between polls   looks TOO LONG-lived
       a quote alive before the first or after the last poll  looks TOO SHORT
       a spread that widens and tightens between two reads  moves the sampled
                                                            share either way

   So SAMPLING_BIAS_DIRECTION = NOT_IDENTIFIED for every Class B quantity,
   unless a specific mathematical bound is proved for it. None is claimed here.

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
TRUE_TIME_WEIGHTED_ONE_TICK_UPTIME = NOT_IDENTIFIED
FEED_IS_EVENT_COMPLETE = NOT_IDENTIFIED

# ---------------------------------------------------------------------------
# THE TWO METRIC CLASSES. The bias statement is per class, never global.
# ---------------------------------------------------------------------------

CLASS_A_TRANSITION_COUNTS = (
    "OBSERVED_BOOK_CHANGES", "OBSERVED_MID_CHANGES",
    "OBSERVED_MOVE_THROUGH_EVENTS", "OBSERVED_PRICE_IMPROVEMENTS",
    "OBSERVED_DEPTH_CHANGES",
)
CLASS_A_BOUND = "OBSERVED_TRANSITION_COUNT_LE_TRUE_TRANSITION_COUNT"
CLASS_A_SAMPLING_BIAS_DIRECTION = "UNDERCOUNT"
CLASS_A_BOUND_SUBJECT_TO = "THE_SNAPSHOTS_THEMSELVES_BEING_VALID"

CLASS_B_STATE_OCCUPANCY = (
    "ONE_TICK_SNAPSHOT_SHARE", "OBSERVED_RUN_SPAN_S",
    "TIME_AT_PRICE_OBSERVED_S", "TOUCH_SIZE_BID", "TOUCH_SIZE_ASK",
    "QUEUE_AHEAD_AT_HYPOTHETICAL_ENTRY", "SPREAD",
)
CLASS_B_SAMPLING_BIAS_DIRECTION = NOT_IDENTIFIED
CLASS_B_WHY = ("interval-censored: an unobserved round trip lengthens a "
               "sampled duration, a state alive outside the window shortens "
               "it, and a state that changes and changes back between two "
               "reads moves a sampled share in either direction")

# The frequencies are shares per OBSERVED transition, not per unit time.
FREQUENCIES_ARE_PER_OBSERVED_TRANSITION = True
FREQUENCIES_AS_CONTINUOUS_TIME_RATES = NOT_IDENTIFIED

WITHDRAWN_TOO_BROAD = "EVERY_OBSERVED_RATE_IS_A_LOWER_BOUND_ON_ACTIVITY"

MOVE_THROUGH_IS_A_PRICE_PATH_OBSERVATION = True
MOVE_THROUGH_IS_NOT_TRADE = True
MOVE_THROUGH_IS_NOT_A_COUNTERFACTUAL_FILL = True

MARKOUT_HORIZONS_S = (30, 60, 300)

# Rates that exist in both weightings when an event map is supplied.
WEIGHTED_RATES = ("ONE_TICK_SNAPSHOT_SHARE", "BOOK_UPDATE_RATE_OBSERVED",
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


def persistence_runs(rows, key="BID"):
    """The SPAN between matching sampled endpoints. Not a duration of anything.

    WHY THIS IS NOT CALLED A MINIMUM PERSISTENCE, WHICH IS WHAT I CALLED IT
    FIRST. A run here is consecutive OBSERVATIONS whose price matched. Two
    matching endpoints do not establish that the price held between them:

        quote at t1 = X      quote at t2 = X
        -> X may have disappeared, changed, and returned to X in between

    So `OBSERVED_RUN_SPAN_S` is elapsed time spanning consecutive sampled
    observations that match the run definition, and nothing more. Calling it a
    MINIMUM would assert a continuity we did not observe -- a minimum of a
    quantity we cannot bound at all is not a minimum.

    A genuine floor would need a venue-supplied timestamp saying the order
    rested from some instant. We have none, so:

        PROVEN_CONTINUOUS_PERSISTENCE_S = NOT_IDENTIFIED

    and the censoring flags below describe the SAMPLED RUN only:

        LEFT_CENSORED   the run starts at our first observation of this market
        RIGHT_CENSORED  the run is still open at our last observation
        INTRAINTERVAL_STATE_CHANGES = NOT_OBSERVED, always

    Survival-analysis vocabulary is borrowed deliberately and bounded
    deliberately: it applies to the run we sampled, NOT to the underlying
    continuous lifetime, because a disappear-and-return between two endpoints
    breaks the correspondence entirely.
    """
    by_slug, _ = _rows_by_slug(rows)
    spans, left, right, uncensored, runs = [], 0, 0, 0, 0
    for _, rs in by_slug.items():
        if not rs:
            continue
        start_i, cur = 0, _d(rs[0].get(key))
        for i in range(1, len(rs) + 1):
            v = _d(rs[i].get(key)) if i < len(rs) else object()
            if i == len(rs) or v != cur:
                first, last = rs[start_i], rs[i - 1]
                spans.append(D(str(round(
                    last["ELAPSED_S"] - first["ELAPSED_S"], 3))))
                runs += 1
                lc = start_i == 0
                rc = i == len(rs)
                left += lc
                right += rc
                uncensored += (not lc and not rc)
                start_i, cur = i, v
    out = _dist(spans, "OBSERVED_RUN_SPAN_S")
    out.update({
        "OBSERVED_RUN_SPAN_MEANS": ("ELAPSED_TIME_SPANNING_CONSECUTIVE_"
                                    "SAMPLED_OBSERVATIONS_THAT_MATCH"),
        "OBSERVED_RUN_SPAN_IS_NOT_A_MINIMUM_CONTINUOUS_LIFETIME": True,
        "PERSISTENCE_RUNS": runs,

        # Censoring, scoped to what it actually describes.
        "LEFT_CENSORED_RUNS": left,
        "RIGHT_CENSORED_RUNS": right,
        "UNCENSORED_RUNS": uncensored,
        "CENSORING_APPLIES_TO_SAMPLED_RUN": "YES",
        "CENSORING_APPLIES_TO_TRUE_CONTINUOUS_QUOTE_LIFETIME": NOT_IDENTIFIED,
        "WHY": ("a disappear-and-return between two sampled endpoints breaks "
                "the correspondence between the sampled run and any "
                "continuous lifetime, so the censoring flags bound the run "
                "and not the lifetime"),

        "INTRAINTERVAL_STATE_CHANGES": "NOT_OBSERVED",
        "PROVEN_CONTINUOUS_PERSISTENCE_S": NOT_IDENTIFIED,
        "TRUE_CONTINUOUS_QUOTE_LIFETIME": NOT_IDENTIFIED,
        "SAMPLING_BIAS_DIRECTION": CLASS_B_SAMPLING_BIAS_DIRECTION,
        "METRIC_CLASS": "B_STATE_OCCUPANCY",
        "KEY": key,
    })
    return out


def _slug_metrics(rs, tick_size):
    """Per-market counts and rates, so they can be weighted two ways."""
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

        # CLASS A. Counts. Each one is <= its true count, because a poll can
        # miss a transition but cannot invent one.
        "OBSERVED_BOOK_CHANGES": moves,
        "OBSERVED_MID_CHANGES": mid_moves,
        "OBSERVED_PRICE_IMPROVEMENTS": improvements,
        "OBSERVED_DEPTH_CHANGES": depth_changes,
        "OBSERVED_MOVE_THROUGH_EVENTS": through,

        # The shares built from them. Per OBSERVED transition, not per second.
        "ONE_TICK_SNAPSHOT_SHARE": r_(one_tick, spreads),
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


# numerator, denominator -- the raw counts each rate is built from, so an event
# can be aggregated INTERNALLY before it votes.
RATE_COUNTS = {
    "ONE_TICK_SNAPSHOT_SHARE": ("one_tick", "spreads"),
    "BOOK_UPDATE_RATE_OBSERVED": ("moves", "obs"),
    "MID_MOVE_FREQUENCY_OBSERVED": ("mid_moves", "obs"),
    "PRICE_IMPROVEMENT_FREQUENCY_OBSERVED": ("improvements", "obs"),
    "DEPTH_CHANGE_RATE_OBSERVED": ("depth_changes", "obs"),
    "MARKET_MOVED_THROUGH_QUOTE_OBSERVED": ("through", "obs"),
}


COUNT_KEYS = ("one_tick", "spreads", "moves", "mid_moves", "improvements",
              "depth_changes", "through", "obs")


def _event_weighted(counts_by_slug, event_of):
    """Three figures per rate, because two of them differ for two reasons.

    THE ORDER MATTERS AND IT IS THE WHOLE POINT. Each event's own counts are
    pooled into a single event-level rate -- so a market inside the event
    contributes in proportion to how much of that event we actually observed --
    and only then does each event contribute ONE value to the mean. What this
    is NOT is a reweighting of individual market rows after pooling, which
    would leave an event carrying more markets, or more observations, with more
    influence than an event carrying one.

    AND THE COMPARISON NEEDS A THIRD FIGURE. Market-weighted over ALL markets
    against event-weighted over RESOLVED markets differs for two reasons at
    once: the weights changed AND the population changed. So the resolved-only
    market weighting is computed too, and only the last two isolate the effect
    of weighting:

        MARKET_WEIGHTED_ALL_MARKETS      every captured market
        MARKET_WEIGHTED_RESOLVED_ONLY    same weights, resolved subset
        EVENT_WEIGHTED_RESOLVED_ONLY     one vote per event, resolved subset

    Event-weighted statistics are CONDITIONAL on the event-resolved subset, and
    the coverage that makes them conditional travels in the same dict.
    """
    total = len(counts_by_slug)
    if not event_of:
        out = {}
        for k in WEIGHTED_RATES:
            out[k + "_MARKET_WEIGHTED_RESOLVED_ONLY"] = NOT_IDENTIFIED
            out[k + "_EVENT_WEIGHTED_RESOLVED_ONLY"] = NOT_IDENTIFIED
        out.update({
            "EVENT_WEIGHTED_POPULATION": NOT_IDENTIFIED,
            "CAPTURE_MARKETS_TOTAL": total,
            "CAPTURE_MARKETS_EVENT_RESOLVED": 0,
            "CAPTURE_MARKETS_EVENT_UNRESOLVED": total,
            "EVENT_WEIGHTING_MARKET_COVERAGE_PCT": NOT_IDENTIFIED,
            "EVENTS_IN_WEIGHTING": 0,
        })
        return out

    by_event, resolved = {}, {k: 0 for k in COUNT_KEYS}
    n_resolved = 0
    for slug, c in counts_by_slug.items():
        ev = event_of.get(slug)
        if ev is None:
            # An unresolved market gets no vote rather than an event of its own.
            continue
        n_resolved += 1
        acc = by_event.setdefault(ev, {k: 0 for k in COUNT_KEYS})
        for k in COUNT_KEYS:
            acc[k] += c[k]
            resolved[k] += c[k]

    out = {}
    for key, (num, den) in RATE_COUNTS.items():
        # Same weights, smaller population. Isolates the DROP.
        out[key + "_MARKET_WEIGHTED_RESOLVED_ONLY"] = (
            D(resolved[num]) / D(resolved[den]) if resolved[den]
            else NOT_IDENTIFIED)
        # One vote per event, same population. Against the line above, this
        # isolates the WEIGHTING.
        ev_vals = [D(a[num]) / D(a[den]) for a in by_event.values() if a[den]]
        out[key + "_EVENT_WEIGHTED_RESOLVED_ONLY"] = (
            sum(ev_vals) / D(len(ev_vals)) if ev_vals else NOT_IDENTIFIED)

    out.update({
        "EVENTS_IN_WEIGHTING": len(by_event),
        "ONE_EVENT_ONE_VOTE": True,
        "EVENT_AGGREGATED_INTERNALLY_FIRST": True,
        "MARKET_ROWS_REWEIGHTED_AFTER_POOLING": False,
        "MARKETS_WITHOUT_AN_EVENT_GET_NO_VOTE": True,

        # The coverage that makes the event figures conditional. It travels
        # with them so a reader cannot pick the number up without it.
        "EVENT_WEIGHTED_POPULATION": "EVENT_IDENTITY_RESOLVED_SUBSET",
        "CAPTURE_MARKETS_TOTAL": total,
        "CAPTURE_MARKETS_EVENT_RESOLVED": n_resolved,
        "CAPTURE_MARKETS_EVENT_UNRESOLVED": total - n_resolved,
        "EVENT_WEIGHTING_MARKET_COVERAGE_PCT": (
            (D(n_resolved) * D(100) / D(total)) if total else NOT_IDENTIFIED),
        "EVENT_WEIGHTED_COVERS_ALL_CAPTURED_MARKETS": n_resolved == total,
        "THREE_WAY_COMPARISON": ("MARKET_WEIGHTED_ALL_MARKETS vs "
                                 "MARKET_WEIGHTED_RESOLVED_ONLY isolates the "
                                 "population drop; RESOLVED_ONLY vs "
                                 "EVENT_WEIGHTED_RESOLVED_ONLY isolates the "
                                 "weighting"),
    })
    return out


def book_metrics(rows, tick_size=TICK_SIZE, horizons_s=MARKOUT_HORIZONS_S,
                 event_of=None):
    """Every book-state quantity, over one SAMPLED capture. Contacts nothing."""
    by_slug, errs = _rows_by_slug(rows)
    cadence = revisit_cadence(rows)

    spreads, touch_bid_sz, touch_ask_sz, queue_ahead = [], [], [], []
    time_at_price = []
    spread_runs, queue_deltas = [], []
    markout = {h: [] for h in horizons_s}
    per_slug, counts_by_slug = {}, {}
    totals = {"one_tick": 0, "spreads": 0, "moves": 0, "mid_moves": 0,
              "improvements": 0, "depth_changes": 0, "through": 0, "obs": 0}

    for slug, rs in by_slug.items():
        m = _slug_metrics(rs, tick_size)
        counts = m.pop("_counts")
        counts_by_slug[slug] = counts
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
            # The collector's own accumulated time at an unchanged touch. A
            # sampled occupancy (Class B), not a lifetime -- the per-side
            # interval-censored runs below are the duration measure.
            v = r.get("QUOTE_LIFETIME_S")
            if isinstance(v, (int, str)) and v != NOT_IDENTIFIED:
                try:
                    time_at_price.append(D(str(v)))
                except Exception:                              # noqa: BLE001
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

        # --- CLASS A: counts. Each <= its true count. ---
        "OBSERVED_BOOK_CHANGES": t["moves"],
        "OBSERVED_MID_CHANGES": t["mid_moves"],
        "OBSERVED_PRICE_IMPROVEMENTS": t["improvements"],
        "OBSERVED_DEPTH_CHANGES": t["depth_changes"],
        "OBSERVED_MOVE_THROUGH_EVENTS": t["through"],
        "CLASS_A_TRANSITION_COUNTS": list(CLASS_A_TRANSITION_COUNTS),
        "CLASS_A_BOUND": CLASS_A_BOUND,
        "CLASS_A_SAMPLING_BIAS_DIRECTION": CLASS_A_SAMPLING_BIAS_DIRECTION,
        "CLASS_A_BOUND_SUBJECT_TO": CLASS_A_BOUND_SUBJECT_TO,

        # --- the shares built from them. MARKET-WEIGHTED, ALL MARKETS. ---
        "ONE_TICK_SNAPSHOT_SHARE": r_(t["one_tick"], t["spreads"]),
        "BOOK_UPDATE_RATE_OBSERVED": r_(t["moves"], obs),
        "MID_MOVE_FREQUENCY_OBSERVED": r_(t["mid_moves"], obs),
        "PRICE_IMPROVEMENT_FREQUENCY_OBSERVED": r_(t["improvements"], obs),
        "DEPTH_CHANGE_RATE_OBSERVED": r_(t["depth_changes"], obs),
        "MARKET_MOVED_THROUGH_QUOTE_OBSERVED": r_(t["through"], obs),
        "WEIGHTING": "MARKET_WEIGHTED_ALL_MARKETS",
        "MARKET_WEIGHTED_POPULATION": "ALL_CAPTURED_MARKETS",
        "FREQUENCIES_ARE_PER_OBSERVED_TRANSITION":
            FREQUENCIES_ARE_PER_OBSERVED_TRANSITION,
        "FREQUENCIES_AS_CONTINUOUS_TIME_RATES":
            FREQUENCIES_AS_CONTINUOUS_TIME_RATES,

        # --- CLASS B: state occupancy. Interval-censored, BOTH directions. ---
        "CLASS_B_STATE_OCCUPANCY": list(CLASS_B_STATE_OCCUPANCY),
        "CLASS_B_SAMPLING_BIAS_DIRECTION": CLASS_B_SAMPLING_BIAS_DIRECTION,
        "CLASS_B_WHY": CLASS_B_WHY,

        # --- what sampling cannot give ---
        "TRUE_CONTINUOUS_QUOTE_LIFETIME": TRUE_CONTINUOUS_QUOTE_LIFETIME,
        "TRUE_CONTINUOUS_BOOK_UPDATE_RATE": TRUE_CONTINUOUS_BOOK_UPDATE_RATE,
        "TRUE_TIME_WEIGHTED_ONE_TICK_UPTIME":
            TRUE_TIME_WEIGHTED_ONE_TICK_UPTIME,
        "FEED_IS_EVENT_COMPLETE": FEED_IS_EVENT_COMPLETE,
        "WITHDRAWN_TOO_BROAD": WITHDRAWN_TOO_BROAD,
        "EVERY_INTRAINTERVAL_TRANSITION_OBSERVED": False,

        # --- the price-path observation, and what it is not ---
        "MOVE_THROUGH_IS_NOT_TRADE": MOVE_THROUGH_IS_NOT_TRADE,
        "MOVE_THROUGH_IS_NOT_A_COUNTERFACTUAL_FILL":
            MOVE_THROUGH_IS_NOT_A_COUNTERFACTUAL_FILL,

        "PER_SLUG": per_slug,
    }
    out.update(_event_weighted(counts_by_slug, event_of))
    out.update(_dist(spreads, "SPREAD"))
    out["SPREAD_IS_A_SAMPLED_STATE_DISTRIBUTION"] = True
    out.update(_dist(spread_runs, "SPREAD_PERSISTENCE_OBSERVED_S"))
    out.update(_dist(touch_bid_sz, "TOUCH_SIZE_BID"))
    out.update(_dist(touch_ask_sz, "TOUCH_SIZE_ASK"))
    out.update(_dist(queue_ahead, "QUEUE_AHEAD_AT_HYPOTHETICAL_ENTRY"))
    out.update(_dist(queue_deltas, "DISPLAYED_QUEUE_CHANGE"))
    out.update(_dist(time_at_price, "TIME_AT_PRICE_OBSERVED_S"))

    # Quote duration as an INTERVAL with its censoring, per side. There is no
    # QUOTE_LIFETIME field: an observed duration is a minimum, not a lifetime.
    for side in ("BID", "ASK"):
        for k, v in persistence_runs(rows, key=side).items():
            out["%s_%s" % (side, k)] = v
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
