"""THE MARKOUTS: where the position went, and what could have been got out.

Owner directive 2026-09-19 22:4xZ §12. Pure -- no database, no clock of
its own.

TWO NUMBERS, NEVER COLLAPSED INTO ONE. A MID markout says where the
quote went; an EXECUTABLE markout says what walking the bid side would
actually have returned. On a thin book they disagree by a lot, and a
lane that reported only the first would be measuring a price nobody
could have traded at.

THE MARKOUT'S LAG IS PART OF THE MARKOUT. A markout is a statement
about an instant. Through the GitHub bridge the nearest institutional
book to T+60s is usually minutes away, and a book eight minutes late
priced as a 60-second markout is a different measurement wearing the
label of the one we wanted. So each horizon carries a tolerance; a book
outside it is NOT_IDENTIFIED with its lag recorded, which is a real
finding about this regime rather than a gap in the data.
"""

from __future__ import annotations

from datetime import timedelta

from . import shadow as sh

OBSERVED = "OBSERVED"
NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_YET_MATURE = "NOT_YET_MATURE"

# The horizons §12 names, in seconds. SETTLEMENT is a later fact of a
# different kind and is not taken here.
HORIZONS = (("30S", 30), ("60S", 60), ("300S", 300))

# HOW LATE A BOOK MAY BE AND STILL BE THAT HORIZON'S MARKOUT. Half the
# horizon, floored at 30 seconds so the shortest one is not a
# hair-trigger. These are frozen literals for the same reason the
# experiment's thresholds are: a tolerance widened after seeing the
# results would be a tolerance chosen to produce them.
TOLERANCE_FRACTION = 0.5
TOLERANCE_FLOOR_S = 30


def tolerance_s(horizon_s) -> float:
    return max(float(horizon_s) * TOLERANCE_FRACTION, float(
        TOLERANCE_FLOOR_S))


def target_at(decision_at, horizon_s):
    return decision_at + timedelta(seconds=int(horizon_s))


def _mid_of(book) -> float | None:
    bids = (book or {}).get("bids") or []
    asks = (book or {}).get("asks") or []
    if not bids or not asks:
        # A ONE-SIDED BOOK HAS NO MID. Taking the one side that exists
        # would mark the position at a price with no counterparty on
        # the other end of it.
        return None
    return (bids[0][0] + asks[0][0]) / 2.0


def markout(*, horizon, horizon_s, decision_at, position, book=None,
            observed_at=None, now=None) -> dict:
    """One horizon's markout for one open position.

    `position` is {qty, vwap, side}. Only a long of the YES contract is
    marked here, because that is the only side whose identity is
    execution-eligible today; anything else returns NOT_IDENTIFIED
    rather than being marked against the wrong book.
    """
    target = target_at(decision_at, horizon_s)
    tol = tolerance_s(horizon_s)
    base = {
        "horizon": horizon,
        "targetAt": target,
        "toleranceMs": tol * 1000.0,
        "observedAt": observed_at,
        "observedLagMs": (None if observed_at is None
                          else round((observed_at - target)
                                     .total_seconds() * 1000, 1)),
        "midMarkoutUsd": None, "executableMarkoutUsd": None,
        "markPrice": None, "exitableQty": None, "exitVwap": None,
        "positionQty": (position or {}).get("qty"),
        "entryVwap": (position or {}).get("vwap"),
        "l2BookSha": (book or {}).get("l2BookSha"),
        "l2EvidenceId": (book or {}).get("l2EvidenceId"),
        "l2SourceTimestamp": (book or {}).get("l2SourceTimestamp"),
        "latencyRegime": (book or {}).get("latencyRegime"),
        "why": None,
    }

    if now is not None and now < target:
        # NOT WRITTEN YET. The row is unique per (decision, horizon), so
        # filing NOT_YET_MATURE would block the real answer later.
        return dict(base, status=NOT_YET_MATURE,
                    why="the horizon has not elapsed")

    if book is None or observed_at is None:
        return dict(base, status=NOT_IDENTIFIED,
                    why="no institutional book was observed for this "
                        "instrument after the horizon")

    if abs(base["observedLagMs"]) > base["toleranceMs"]:
        return dict(base, status=NOT_IDENTIFIED,
                    why=("the nearest observed book is %.0fs from the "
                         "%s target, outside the %.0fs tolerance; a book "
                         "that late is not this horizon's markout"
                         % (base["observedLagMs"] / 1000.0, horizon, tol)))

    qty = (position or {}).get("qty")
    entry = (position or {}).get("vwap")
    if not qty or not entry:
        return dict(base, status=NOT_IDENTIFIED,
                    why="the position carries no filled quantity to mark")

    mid = _mid_of(book)
    out = dict(base, status=OBSERVED, markPrice=mid)
    if mid is not None:
        out["midMarkoutUsd"] = round(qty * (mid - entry), 6)
    else:
        out["why"] = ("the book is one-sided, so it has no mid; the "
                      "executable mark stands alone")

    # WHAT COULD ACTUALLY HAVE BEEN GOT OUT: walk the BID side for the
    # position's size. A book that can absorb only part of it gives an
    # executable markout over THAT PART, and exitableQty says so --
    # valuing the remainder at the last price would invent the
    # liquidity this lane exists to refuse to invent.
    fill = sh.marketable_fill(sh.SELL, qty, None, book)
    exit_qty, exit_vwap = fill.get("shadowFilledQty"), fill.get("vwap")
    if exit_qty and exit_vwap:
        out["exitableQty"] = exit_qty
        out["exitVwap"] = exit_vwap
        out["executableMarkoutUsd"] = round(exit_qty * (exit_vwap - entry), 6)
    else:
        out["why"] = (out["why"] or "") + (
            "; no bid-side depth: the position could not have been "
            "exited at this instant at any price")
    if out["midMarkoutUsd"] is None and out["executableMarkoutUsd"] is None:
        return dict(out, status=NOT_IDENTIFIED,
                    why=out["why"] or "the book yielded neither a mid nor "
                                      "an exitable quantity")
    return out
