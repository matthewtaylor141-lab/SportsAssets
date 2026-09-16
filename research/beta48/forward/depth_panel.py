#!/usr/bin/env python3
"""Would a BETTOR quote be INSIDE the scoring range? Contacts nothing.

THE RULE THIS IMPLEMENTS, from the detailed liquidity documentation:

  * each SIDE is scored independently;
  * Target Size is the aggregate resting size that side needs to qualify;
  * the exchange walks OUTWARD FROM BEST PRICE until Target Size is reached;
  * orders beyond that qualifying range score ZERO;
  * RAW size, not discounted size, determines the threshold.

That last clause is the one that is easy to get wrong. The discount factor
decides how much a qualifying order SCORES; it has nothing to do with whether
the order qualifies at all. Two different questions, and this file keeps them
apart: `qualifying_depth` uses raw size only, and the discount appears solely
in the score.

WHAT THIS CAN AND CANNOT TELL US.

CAN: whether a hypothetical quote at a given level is inside the qualifying
range at the observed instant, and how much size is already ahead of it.

CANNOT: our share of any reward pool. That needs the qualifying score of ALL
participants THROUGH TIME, which no public endpoint publishes and which a
single book snapshot could not contain even if it did. `ESTIMATED_REWARD`
therefore stays NOT_IDENTIFIED here, permanently and by construction.

THE LADDER IS SPARSE. Observed books skip levels -- 0.1450, 0.1430, 0.1420,
0.1380 -- so "one tick from best" is a PRICE, best -/+ 1 * tick, which may hold
no order at all. Ticks are computed from price distance divided by tick size,
never from the index of a rung in the returned ladder. Getting this wrong would
silently treat the fourth populated rung as "three ticks back" when it is
seven, and would overstate every score.
"""
from __future__ import annotations

from decimal import Decimal as D

NOT_IDENTIFIED = "NOT_IDENTIFIED"


def _levels(side_rows):
    """[(price, qty)] from the venue's ladder, cleaned and typed."""
    out = []
    for r in side_rows or []:
        try:
            px = D(str((r.get("px") or {}).get("value")))
            qty = D(str(r.get("qty")))
        except Exception:                                    # noqa: BLE001
            continue
        if qty > 0:
            out.append((px, qty))
    return out


def book_sides(body):
    """(bids, offers) from a /v1/markets/{slug}/book response body.

    Bids descend from best, offers ascend from best. The venue's own order is
    not trusted -- it is imposed here so a re-ordered response cannot silently
    invert "outward from best".
    """
    md = (body or {}).get("marketData") or {}
    bids = sorted(_levels(md.get("bids")), key=lambda x: -x[0])
    offers = sorted(_levels(md.get("offers")), key=lambda x: x[0])
    return bids, offers


def ticks_from_best(price, best, tick, side):
    """How many TICKS away from best a price sits. Never a ladder index.

    A bid is worse as it falls; an offer is worse as it rises. Returns a
    non-negative integer, or None if the price is better than best (inside the
    touch), which a post-only maker quote cannot be.
    """
    p, b, t = D(str(price)), D(str(best)), D(str(tick))
    if t <= 0:
        return None
    dist = (b - p) if side == "bid" else (p - b)
    if dist < 0:
        return None
    return int((dist / t).to_integral_value(rounding="ROUND_HALF_EVEN"))


def qualifying_depth(levels, best, tick, target_size, side):
    """Walk outward from best until RAW cumulative size reaches Target Size.

    Returns the level at which the threshold is met and the cumulative size at
    0, 1, 2 and 3 ticks back. `FIRST_LEVEL_WHERE_TARGET_SIZE_REACHED` is None
    when the whole visible book never reaches Target Size -- which is itself a
    finding, not a failure, and is reported rather than smoothed away.
    """
    cum = D("0")
    first_level = None
    at_tick = {0: D("0"), 1: D("0"), 2: D("0"), 3: D("0")}
    tgt = D(str(target_size)) if target_size is not None else None

    for px, qty in levels:
        n = ticks_from_best(px, best, tick, side)
        if n is None:
            continue
        cum += qty                      # RAW size, never discounted
        for k in at_tick:
            if n <= k:
                at_tick[k] += qty
        if tgt is not None and first_level is None and cum >= tgt:
            first_level = n

    return {
        "CUMULATIVE_SIZE_AT_BEST": at_tick[0],
        "CUMULATIVE_SIZE_1_TICK": at_tick[1],
        "CUMULATIVE_SIZE_2_TICKS": at_tick[2],
        "CUMULATIVE_SIZE_3_TICKS": at_tick[3],
        "CUMULATIVE_SIZE_WHOLE_VISIBLE_BOOK": cum,
        "FIRST_LEVEL_WHERE_TARGET_SIZE_REACHED": first_level,
        "TARGET_SIZE_REACHED_IN_VISIBLE_BOOK": (
            NOT_IDENTIFIED if tgt is None else (first_level is not None)),
    }


def would_quote_score(depth, ticks_back, target_size):
    """YES / NO / NOT_IDENTIFIED for a quote resting `ticks_back` from best.

    A quote is inside the scoring range when the qualifying walk has not yet
    been satisfied by the time it reaches that level. If Target Size is already
    met AT BEST by other participants, a quote one tick back scores nothing
    however large it is -- which is the practical question the panel exists to
    answer.

    NOT_IDENTIFIED when Target Size is unknown, or when the visible book never
    reaches it: we then cannot tell whether hidden or later size would.
    """
    if target_size is None:
        return NOT_IDENTIFIED
    first = depth.get("FIRST_LEVEL_WHERE_TARGET_SIZE_REACHED")
    if first is None:
        # The whole visible book is inside the qualifying range, so a quote at
        # any visible level qualifies -- ON THIS SNAPSHOT. Said plainly rather
        # than upgraded to a general YES.
        return "YES_ON_VISIBLE_BOOK"
    return "YES" if int(ticks_back) <= int(first) else "NO"


def size_ahead_of_quote(depth, ticks_back):
    """Raw size already resting at or better than our level -- the queue we
    would join BEHIND. Time priority within a level is not observable, so this
    is a LOWER bound on what sits ahead of us, never an estimate of our place.
    """
    key = {0: "CUMULATIVE_SIZE_AT_BEST", 1: "CUMULATIVE_SIZE_1_TICK",
           2: "CUMULATIVE_SIZE_2_TICKS", 3: "CUMULATIVE_SIZE_3_TICKS"}
    k = key.get(int(ticks_back))
    return depth.get(k) if k else NOT_IDENTIFIED


def panel_row(body, tick, target_size, slug=None, captured_at=None):
    """One snapshot, both sides, scored independently as the rule requires."""
    bids, offers = book_sides(body)
    row = {"slug": slug, "captured_at_utc": captured_at,
           "tick": tick, "TARGET_SIZE": target_size,
           "ESTIMATED_REWARD": NOT_IDENTIFIED,
           "ACTUAL_REWARD": NOT_IDENTIFIED,
           "REWARD_SHARE": NOT_IDENTIFIED}

    for name, levels in (("BID", bids), ("ASK", offers)):
        if not levels:
            row[name] = {"BOOK_SIDE_EMPTY": True}
            row["%s_WOULD_BETTOR_QUOTE_SCORE" % name] = NOT_IDENTIFIED
            continue
        best = levels[0][0]
        side = "bid" if name == "BID" else "ask"
        d = qualifying_depth(levels, best, tick, target_size, side)
        d["BEST_PRICE"] = best
        d["LEVELS_VISIBLE"] = len(levels)
        row[name] = d
        for k in (0, 1, 2, 3):
            row["%s_QUOTE_%d_TICKS_BACK_SCORE_ELIGIBILITY" % (name, k)] = (
                would_quote_score(d, k, target_size))
            row["%s_SIZE_AHEAD_%d_TICKS" % (name, k)] = (
                size_ahead_of_quote(d, k))
        row["%s_TARGET_ALREADY_REACHED_AT_BEST" % name] = (
            NOT_IDENTIFIED if target_size is None
            else d["FIRST_LEVEL_WHERE_TARGET_SIZE_REACHED"] == 0)
        row["%s_WOULD_BETTOR_QUOTE_SCORE" % name] = (
            row["%s_QUOTE_0_TICKS_BACK_SCORE_ELIGIBILITY" % name])
    return row


def liquidity_score(order_size, ticks_back, discount_factor):
    """SCORE = DiscountFactor^ticks * OrderSize, for a QUALIFYING order only.

    The discount decides how much a qualifying order scores. It never decides
    whether the order qualifies -- that is `qualifying_depth`, on raw size.
    """
    return D(str(discount_factor)) ** int(ticks_back) * D(str(order_size))
