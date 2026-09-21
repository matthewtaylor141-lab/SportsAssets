"""Which markets BETTOR watches, and why each one was chosen.

A SWEEP IS NOT A UNIVERSE. The capture samples 1,526 markets on a
rotation built for coverage. Streaming all of them would cost 16
subscriptions, most of them on books that never trade: the measured
distribution is p10 **2.23 shares traded**, median **100**, and only
**116 of 606** markets traded 1,000 shares or more in a day.

So the universe is SELECTED, the selection rule is frozen here, and
every market carries the reason it was included. A universe whose
membership cannot be explained is a universe whose results cannot be
attributed.

THE RULE. A market is eligible when ALL of:

  * the venue lists it OPEN and not closed/archived;
  * it is two-sided -- both a bid and an ask;
  * spread >= MIN_SPREAD_TICKS ticks. A maker earns at most the spread
    and pays adverse selection out of it, so a one-tick book has
    nothing to pay with. Measured: 451 of 795 books clear two ticks;
    p90 spread is 0.9400, so the top decile is untradeable and this is
    what excludes it;
  * ask <= MAX_PRICE. The worst case per contract is what we paid;
  * traded volume >= MIN_SHARES_TRADED over the observation window.
    THIS IS THE ACTIVITY TEST and it is the one the old capacity
    analysis never applied: a market that does not trade cannot fill
    our quote however good the book looks.

RANKED BY VOLUME, NOT BY SPREAD. The widest books are wide because
nobody wants them. Ranking by spread would select exactly the markets
where a resting order sits longest and gets picked off hardest --
BLOCK_4 measured a 22,297-share displayed queue against 180 shares
traded in sixteen minutes, with zero touches. Volume is the closest
public proxy for whether a quote would ever be hit.

DISPLAYED DEPTH IS NOT EXECUTABLE DEPTH, and this module never treats
it as such. `top_of_book_qty` is carried for sizing; it bounds what
could trade at the touch and nothing more.
"""

from __future__ import annotations

UNIVERSE_VERSION = "BETTOR_UNIVERSE_V1"

# Frozen selection parameters. Changing one changes which results
# belong to which regime, so the version moves with them.
MIN_SPREAD_TICKS = 2
DEFAULT_TICK = 0.01
MAX_PRICE = 0.50
MIN_SHARES_TRADED = 100.0     # the measured MEDIAN of traded markets
MAX_MARKETS = 100             # one subscription's worth, by documented limit

# Reasons a market is excluded, counted rather than silently dropped.
R_NOT_OPEN = "NOT_OPEN"
R_ONE_SIDED = "ONE_SIDED_BOOK"
R_SPREAD = "SPREAD_BELOW_MIN_TICKS"
R_PRICE = "ASK_ABOVE_MAX_PRICE"
R_VOLUME = "TRADED_VOLUME_BELOW_MIN"
R_NO_VOLUME = "NO_TRADED_VOLUME_FIGURE"
R_UNPARSABLE = "PRICE_UNPARSABLE"


def _f(v):
    if v is None:
        return None
    if isinstance(v, dict):          # an Amount
        v = v.get("value")
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def assess(row: dict, *, tick: float = DEFAULT_TICK) -> dict:
    """One candidate -> included/excluded, with the reason and the rank.

    `row` is whatever the caller has: a market listing entry, a BBO, or
    a captured observation. The fields are looked up by several
    spellings because the venue's REST listing, its BBO and our capture
    do not agree on them -- and disagreeing about a field name is not a
    reason to drop a market.
    """
    slug = (row.get("slug") or row.get("marketSlug") or row.get("market_id"))
    bid = _f(row.get("bestBid") or row.get("best_bid") or row.get("yes_bid"))
    ask = _f(row.get("bestAsk") or row.get("best_ask") or row.get("yes_ask"))
    vol = _f(row.get("sharesTraded") or row.get("shares_traded")
             or row.get("stats_shares_traded"))
    state = (row.get("state") or row.get("venue_state")
             or ("MARKET_STATE_OPEN" if row.get("active") else None))
    out = {"slug": slug, "bid": bid, "ask": ask, "shares_traded": vol,
           "state": state, "included": False, "reason": None,
           "spread": None, "spread_ticks": None,
           "top_of_book_qty": _f(row.get("bidDepth")),
           # CARRIED, NOT FILTERED ON. Which side of the contract these
           # prices describe is needed downstream -- an observation
           # whose leg is unnamed is refused as NO_OUTCOME_IDENTITY by
           # the adapter, and that refusal is counted there. The
           # SELECTION RULE is untouched: adding a leg test here would
           # be a new rule under an old version.
           "outcome_leg": (row.get("outcomeLeg") or row.get("outcome_leg")
                           or row.get("leg")),
           "universe": UNIVERSE_VERSION}

    if row.get("closed") or row.get("archived"):
        return dict(out, reason=R_NOT_OPEN)
    if state is not None and state != "MARKET_STATE_OPEN":
        return dict(out, reason=R_NOT_OPEN)
    if bid is None or ask is None:
        return dict(out, reason=R_ONE_SIDED if slug else R_UNPARSABLE)
    if not (0 < bid < 1 and 0 < ask < 1) or ask <= bid:
        return dict(out, reason=R_UNPARSABLE)

    spread = ask - bid
    ticks = spread / tick if tick else 0
    out.update({"spread": round(spread, 6), "spread_ticks": round(ticks, 2)})
    if ticks < MIN_SPREAD_TICKS - 1e-9:
        return dict(out, reason=R_SPREAD)
    if ask > MAX_PRICE:
        return dict(out, reason=R_PRICE)
    if vol is None:
        # NOT the same as zero volume. A market we have no figure for is
        # excluded for a different reason, and the two are counted apart.
        return dict(out, reason=R_NO_VOLUME)
    if vol < MIN_SHARES_TRADED:
        return dict(out, reason=R_VOLUME)
    return dict(out, included=True, reason=None)


def select(rows, *, max_markets: int = MAX_MARKETS,
           tick: float = DEFAULT_TICK) -> dict:
    """The universe, its ranking, and every exclusion by reason.

    Ranked by traded volume descending. Ties break on the slug, so the
    same input always gives the same universe -- a universe that
    reshuffles between runs makes two runs incomparable.
    """
    assessed = [assess(r, tick=tick) for r in rows]
    included = [a for a in assessed if a["included"]]
    included.sort(key=lambda a: (-(a["shares_traded"] or 0), a["slug"] or ""))
    chosen = included[:max_markets]
    reasons: dict = {}
    for a in assessed:
        if not a["included"]:
            reasons[a["reason"]] = reasons.get(a["reason"], 0) + 1
    return {
        "universe": UNIVERSE_VERSION,
        "considered": len(assessed),
        "eligible": len(included),
        "selected": len(chosen),
        # VISIBLE BEFORE THE LOOP STARTS. A selected market whose side
        # is unnamed produces a REJECTED record on every update, and
        # discovering that from the refusal histogram after an hour is
        # too late.
        "selected_without_outcome_leg": sum(
            1 for a in chosen if not a.get("outcome_leg")),
        "over_cap": max(0, len(included) - len(chosen)),
        "slugs": [a["slug"] for a in chosen],
        "detail": chosen,
        "excluded_by_reason": reasons,
        "rule": describe()["rule"],
    }


def describe() -> dict:
    return {
        "universe": UNIVERSE_VERSION,
        "rule": {
            "open": "venue state MARKET_STATE_OPEN, not closed/archived",
            "two_sided": "both a bid and an ask",
            "min_spread_ticks": MIN_SPREAD_TICKS,
            "max_price": MAX_PRICE,
            "min_shares_traded": MIN_SHARES_TRADED,
            "rank_by": "traded volume, descending; slug breaks ties",
            "max_markets": MAX_MARKETS,
        },
        "why_volume_not_spread": (
            "the widest books are wide because nobody wants them. "
            "Ranking by spread selects exactly the markets where a "
            "resting order sits longest and is picked off hardest -- "
            "BLOCK_4: a 22,297-share displayed queue against 180 shares "
            "traded in 16 minutes, zero touches"),
        "measured_basis": {
            "shares_traded_p10": 2.23, "median": 100.0, "p90": 4401.09,
            "markets_at_or_above_1000_shares": "116 of 606",
            "books_clearing_2_ticks": "451 of 795",
            "spread_p90": 0.94,
            "source": "DATA_FINDINGS_2026_09_21.md sections 8 and 9",
        },
        "displayed_is_not_executable": (
            "top_of_book_qty bounds what COULD trade at the touch and "
            "nothing more"),
    }
