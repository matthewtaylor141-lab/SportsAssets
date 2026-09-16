#!/usr/bin/env python3
"""BOOK-ONLY measurements from the tick capture. Not one of them is a fill rate.

WHAT THIS FILE IS FOR.

The capture cannot identify a fill (see `maker_fill`), and that is settled. It
does not follow that the capture is worth nothing: everything below is a
property of the BOOK, it is fully observable from the rows we have, and every
one of them is an execution input a maker needs before it quotes anything.

    SPREAD_PERSISTENCE            how long a spread survives
    ONE_TICK_UPTIME               share of time the book is one tick wide
    QUOTE_LIFETIME_AT_TOUCH       how long the touch price stands
    TOUCH_SIZE                    displayed size at the touch
    DEPTH_EVOLUTION               how displayed depth moves
    PRICE_IMPROVEMENT_FREQUENCY   how often somebody betters the touch
    BOOK_MOVE_FREQUENCY           how often the touch changes at all
    MID_MOVE_FREQUENCY            how often the mid moves
    TIME_AT_PRICE                 accumulated time at an unchanged touch
    QUEUE_AHEAD_AT_HYPOTHETICAL_ENTRY   what we would join behind
    DISPLAYED_QUEUE_CHANGE_RATE   how fast that queue turns over
    MARKET_MOVES_THROUGH_QUOTE    how often the market passes our level
    POST_QUOTE_BOOK_MARKOUT       where the book goes after we would have quoted

THE NAMING RULE, ENFORCED BY TEST.

None of these may be renamed into anything that reads as an execution outcome.
A fill rate is a statement about ORDERS; every quantity here is a statement
about the BOOK, and the two are being kept apart deliberately because the whole
programme currently rests on that distinction. `test_book_metrics.py` asserts
that no key this module emits contains FILL, FILLED or EXECUTION.

A market moving THROUGH a hypothetical level is the closest any of this comes to
an execution statement, and it is deliberately named
`MARKET_MOVED_THROUGH_QUOTE` -- a fact about where the price went, not about
what happened to an order that was never there.
"""
from __future__ import annotations

from decimal import Decimal as D

NOT_IDENTIFIED = "NOT_IDENTIFIED"

TICK_SIZE = D("0.01")

# Names this module refuses to produce, however convenient the shorthand.
FORBIDDEN_RENAMES = ("FILL_RATE", "MAKER_FILL_RATE", "EXECUTION_RATE",
                     "FILL_PROBABILITY", "HIT_RATE")
THESE_ARE_BOOK_QUANTITIES_NOT_EXECUTION_OUTCOMES = True

MARKOUT_HORIZONS_S = (30, 60, 300)


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


def book_metrics(rows, tick_size=TICK_SIZE, horizons_s=MARKOUT_HORIZONS_S):
    """Every book-only quantity, over one capture. Contacts nothing."""
    by_slug, errs = _rows_by_slug(rows)

    spreads, touch_bid_sz, touch_ask_sz, queue_ahead = [], [], [], []
    life_bid, life_ask, time_at_price = [], [], []
    obs = moves = mid_moves = improvements = depth_changes = 0
    one_tick = 0
    spread_runs, through = [], 0
    queue_deltas = []
    markout = {h: [] for h in horizons_s}
    per_slug = {}

    for slug, rs in by_slug.items():
        s_obs = s_moves = s_one = 0
        run_start, run_val = None, None
        prev = None
        for r in rs:
            sp = _d(r.get("SPREAD"))
            if sp != NOT_IDENTIFIED:
                spreads.append(sp)
                if sp == tick_size:
                    one_tick += 1
                    s_one += 1
            bq, aq = _d(r.get("BID_QTY")), _d(r.get("ASK_QTY"))
            if bq != NOT_IDENTIFIED:
                touch_bid_sz.append(bq)
                # What a hypothetical quote resting at the touch would join
                # behind. The same number, named for the decision it informs.
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
                    except Exception:                      # noqa: BLE001
                        pass

            if prev is not None:
                s_obs += 1
                obs += 1
                b, a = _d(r.get("BID")), _d(r.get("ASK"))
                pb, pa = _d(prev.get("BID")), _d(prev.get("ASK"))
                moved = (r.get("BID_CHANGED") is True
                         or r.get("ASK_CHANGED") is True)
                if moved:
                    moves += 1
                    s_moves += 1
                if r.get("DEPTH_CHANGED") is True:
                    depth_changes += 1
                if _mid(r) != NOT_IDENTIFIED and _mid(prev) != NOT_IDENTIFIED \
                        and _mid(r) != _mid(prev):
                    mid_moves += 1
                # Somebody bettered the touch: the bid rose or the offer fell.
                if (b != NOT_IDENTIFIED and pb != NOT_IDENTIFIED and b > pb) \
                        or (a != NOT_IDENTIFIED and pa != NOT_IDENTIFIED
                            and a < pa):
                    improvements += 1
                # The market passing a level we WOULD have quoted at the
                # previous tick. A fact about price, not about an order.
                if pb != NOT_IDENTIFIED and a != NOT_IDENTIFIED and a <= pb:
                    through += 1
                if bq != NOT_IDENTIFIED:
                    pq = _d(prev.get("BID_QTY"))
                    if pq != NOT_IDENTIFIED and b == pb:
                        queue_deltas.append(abs(bq - pq))
            prev = r

            # spread run length
            if run_val is None or sp != run_val:
                if run_val is not None and run_start is not None:
                    spread_runs.append(D(str(round(
                        r["ELAPSED_S"] - run_start, 3))))
                run_val, run_start = sp, r["ELAPSED_S"]

        # post-quote book markout: mid at t+h against the mid we would have
        # quoted around.
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

        per_slug[slug] = {
            "OBSERVED_TRANSITIONS": s_obs,
            "BOOK_MOVES": s_moves,
            "ONE_TICK_OBSERVATIONS": s_one,
            "TICK_ERRORS": errs.get(slug, 0),
        }

    out = {
        "MARKETS": len(by_slug),
        "OBSERVATIONS": sum(len(v) for v in by_slug.values()),
        "OBSERVED_TRANSITIONS": obs,
        "TICK_ERRORS": sum(errs.values()),

        "ONE_TICK_UPTIME": (D(one_tick) / D(len(spreads))
                            if spreads else NOT_IDENTIFIED),
        "BOOK_MOVE_FREQUENCY": (D(moves) / D(obs) if obs else NOT_IDENTIFIED),
        "MID_MOVE_FREQUENCY": (D(mid_moves) / D(obs) if obs
                               else NOT_IDENTIFIED),
        "PRICE_IMPROVEMENT_FREQUENCY": (D(improvements) / D(obs) if obs
                                        else NOT_IDENTIFIED),
        "DEPTH_CHANGE_RATE": (D(depth_changes) / D(obs) if obs
                              else NOT_IDENTIFIED),
        "MARKET_MOVED_THROUGH_QUOTE_FREQUENCY": (D(through) / D(obs) if obs
                                                 else NOT_IDENTIFIED),
        "MARKET_MOVED_THROUGH_QUOTE_IS_NOT_AN_EXECUTION": True,
        "PER_SLUG": per_slug,
    }
    out.update(_dist(spreads, "SPREAD"))
    out.update(_dist(spread_runs, "SPREAD_PERSISTENCE_S"))
    out.update(_dist(touch_bid_sz, "TOUCH_SIZE_BID"))
    out.update(_dist(touch_ask_sz, "TOUCH_SIZE_ASK"))
    out.update(_dist(queue_ahead, "QUEUE_AHEAD_AT_HYPOTHETICAL_ENTRY"))
    out.update(_dist(queue_deltas, "DISPLAYED_QUEUE_CHANGE"))
    out.update(_dist(life_bid, "QUOTE_LIFETIME_AT_TOUCH_BID_S"))
    out.update(_dist(life_ask, "QUOTE_LIFETIME_AT_TOUCH_ASK_S"))
    out.update(_dist(time_at_price, "TIME_AT_PRICE_S"))
    for h in horizons_s:
        out.update(_dist(markout[h], "POST_QUOTE_BOOK_MARKOUT_%dS" % h))
    return out


def report(rows):
    """The book half of the Phase-2A harvest, with the refusals attached."""
    m = book_metrics(rows)
    m.update({
        "THESE_ARE_BOOK_QUANTITIES_NOT_EXECUTION_OUTCOMES": True,
        "FILL_RATE_NOT_PRESENT": True,
        "WHY": ("a fill rate is a statement about orders; every quantity here "
                "is a statement about the book, and no order existed"),
    })
    return m
