"""THE CAPITAL ALLOCATOR. WHICH CLAIM ON THE SAME DOLLAR WINS.

Owner directive, "CONTINUE THE BUILD" §17:

    "The North Star is EXPECTED NET DOLLARS PER UNIT OF CAPITAL PER
    UNIT OF TIME... Capital-hours already exist. Use them. Do not rank
    opportunities only by percentage EV."

WHAT THIS LAYER IS FOR. Every engine below it answers "is this action
worth doing". None of them answers "is it worth doing INSTEAD OF the
other thing this dollar could be doing". A new maker quote, completing
an existing pair, paying a hedge tax to release capital, and holding a
high-EV residual are all bids for the same balance, and they are not
comparable on EV alone because they occupy capital for different
lengths of time.

────────────────────────────────────────────────────────────────────
WHY PERCENTAGE EV IS THE WRONG RANKING, AND WHY THE RATE ALONE IS TOO.

A 2% edge held for thirty seconds and a 2% edge held to settlement in
three days are the same percentage and not remotely the same business.
The first recycles the dollar 8,640 times; the second does it once.
Ranking on percentage picks them indifferently.

But `inventory_state` already states the opposite trap, and it is the
one a rate-based allocator falls into:

    "A trade earning one cent in one second has a spectacular hourly
    rate and still earns one cent."
    "Reporting only the rate makes tiny, fast, operationally expensive
    trades look like the best in the book."

So BOTH figures travel together, always -- EXPECTED_NET_DOLLARS and
EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR -- and this module refuses to
emit a ranking built on either alone. The arithmetic is
`inventory_state.capital_hours`, imported rather than rewritten.
────────────────────────────────────────────────────────────────────

WHAT IT CANNOT DO TODAY, AND WHY IT IS STILL WORTH HAVING. Not one
candidate has an identified EXPECTED_NET_DOLLARS: that needs an
independent fair value, and FV_BETTOR_INDEPENDENT does not exist. So
every rate is NOT_IDENTIFIED and no ranking is produced.

The structure still earns its place. It names the candidates, computes
the capital each would occupy -- which IS identified, from the book and
the inventory -- applies the risk rails, and reports exactly which term
is missing. When an EV arrives, the allocator does not need designing;
it needs one field filled.

    A RANKING PRODUCED FROM PARTIAL TERMS WOULD BE WORSE THAN NONE. It
    would order candidates by whichever term happened to be measurable
    -- capital, or duration, or nothing at all -- and present that
    order as an economic judgement.

THE RISK RAILS BIND FIRST, AND SEPARATELY. A candidate the risk engine
refuses is not ranked at all, however attractive. Allocation is a
question asked only of admissible candidates, and mixing the two would
let a high number argue with a limit.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from . import bettor_ev_bridge as evb
from . import bettor_risk_engine as risk

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ── §17: the claims on the same dollar ───────────────────────────────

CANDIDATES = (
    "NEW_MAKER_QUOTE",
    "COMPLETE_EXISTING_PAIR",
    "PAY_HEDGE_TAX_TO_RELEASE_CAPITAL",
    "HOLD_HIGH_EV_RESIDUAL",
    "NO_TRADE",
)

# Which canonical action each candidate would execute, so the allocator
# and the action table cannot drift into separate vocabularies.
CANDIDATE_ACTION = {
    "NEW_MAKER_QUOTE": "MAKE_YES",
    "COMPLETE_EXISTING_PAIR": "COMPLETE_PAIR",
    "PAY_HEDGE_TAX_TO_RELEASE_CAPITAL": "HEDGE",
    "HOLD_HIGH_EV_RESIDUAL": "HOLD",
    "NO_TRADE": "NO_TRADE",
}

NORTH_STAR = "EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR"

BOTH_FIGURES_OR_NEITHER = (
    "EXPECTED_NET_DOLLARS and EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR "
    "are reported together or not at all. The level without the rate "
    "cannot compare a fast trade against a slow one; the rate without "
    "the level makes a penny earned in a second look like the best "
    "line in the book")

NOT_PERCENTAGE_EV = (
    "ranking on percentage EV treats a 2% edge held thirty seconds and "
    "a 2% edge held three days as the same opportunity. One recycles "
    "the dollar thousands of times and the other does not, so time in "
    "capital is part of the ranking, not a footnote to it")

PARTIAL_RANKING_IS_WORSE_THAN_NONE = (
    "a ranking built from the terms that happen to be measurable would "
    "order candidates by capital, or by duration, or by nothing, and "
    "present that order as an economic judgement. No ranking is "
    "emitted until EXPECTED_NET_DOLLARS is identified")

RISK_BINDS_FIRST = (
    "a candidate the risk engine refuses is not ranked at all. "
    "Allocation is a question asked only of admissible candidates, and "
    "merging the two would let an attractive number argue with a limit")

# §17's named constraints. None is a number here: the risk engine owns
# the limits, and it has none predeclared.
CONSTRAINTS = (
    "UNCERTAINTY",
    "RESIDUAL_INVENTORY",
    "EVENT_CONCENTRATION",
    "CORRELATED_EXPOSURE",
    "LIQUIDITY",
    "DRAWDOWN",
    "EXECUTION_CAPACITY",
    "RISK_RAILS",
)


def _d(v):
    if v is None or v == "" or v == NOT_IDENTIFIED:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def score(candidate: str, *, expected_net_dollars=None,
          capital_required=None, expected_occupancy_seconds=None,
          root=None) -> dict:
    """One candidate's capital economics. Both figures, or neither."""
    row = {
        "candidate": candidate,
        "action": CANDIDATE_ACTION.get(candidate, NOT_IDENTIFIED),
        "northStar": NORTH_STAR,
        "bothFiguresOrNeither": BOTH_FIGURES_OR_NEITHER,
    }
    try:
        INV = evb.machinery(root)["inventory_state"]
    except evb.MachineryUnavailable as exc:
        row.update({NORTH_STAR: NOT_IDENTIFIED,
                    "status": evb.MACHINERY_UNAVAILABLE, "why": str(exc)})
        return row

    ch = INV.capital_hours(
        expected_net_dollars=expected_net_dollars,
        capital_required=capital_required,
        expected_occupancy_seconds=expected_occupancy_seconds)
    row.update({
        "EXPECTED_NET_DOLLARS": ch["EXPECTED_NET_DOLLARS"],
        "CAPITAL_REQUIRED": ch["CAPITAL_REQUIRED"],
        "EXPECTED_CAPITAL_OCCUPANCY_SECONDS":
            ch["EXPECTED_CAPITAL_OCCUPANCY_SECONDS"],
        NORTH_STAR: ch[NORTH_STAR],
        "rateWithoutLevelIsMisleading":
            ch["RATE_WITHOUT_LEVEL_IS_MISLEADING"],
        "derivedBy": "research/beta48/shadow/inventory_state.py",
    })
    if ch[NORTH_STAR] == NOT_IDENTIFIED:
        row["status"] = NOT_IDENTIFIED
        row["why"] = ch.get("WHY_NOT", "a term is not identified")
    else:
        row["status"] = "IDENTIFIED"
    return row


def allocate(candidates=None, *, risk_observed=None, risk_state=None,
             root=None) -> dict:
    """Rank the claims on the same dollar, or say why none can be.

    `candidates` maps candidate name -> {expectedNetDollars,
    capitalRequired, expectedOccupancySeconds}. Absent terms stay
    absent.
    """
    given = candidates or {c: {} for c in CANDIDATES}
    rows = []
    for name in CANDIDATES:
        terms = given.get(name) or {}
        row = score(
            name,
            expected_net_dollars=terms.get("expectedNetDollars"),
            capital_required=terms.get("capitalRequired"),
            expected_occupancy_seconds=terms.get("expectedOccupancySeconds"),
            root=root)
        # RISK FIRST, AND SEPARATELY.
        verdict = risk.evaluate(row["action"], observed=risk_observed,
                                state=risk_state)
        row["risk"] = {
            "permitted": verdict["permitted"],
            "direction": verdict["direction"],
            "railsNotPassed": verdict["railsNotPassed"],
        }
        row["admissible"] = verdict["permitted"]
        rows.append(row)

    rankable = [r for r in rows
                if r["admissible"] and r["status"] == "IDENTIFIED"]
    unrankable = [r["candidate"] for r in rows if r not in rankable]

    out = {
        "candidates": rows,
        "constraints": list(CONSTRAINTS),
        "northStar": NORTH_STAR,
        "notPercentageEv": NOT_PERCENTAGE_EV,
        "bothFiguresOrNeither": BOTH_FIGURES_OR_NEITHER,
        "riskBindsFirst": RISK_BINDS_FIRST,
        "unrankableCandidates": unrankable,
    }

    if not rankable:
        out.update({
            "ranking": [],
            "allocation": NOT_IDENTIFIED,
            "why": ("no candidate has both an identified "
                    "EXPECTED_NET_DOLLARS and an admissible risk "
                    "verdict, so no ranking is produced. "
                    "EXPECTED_NET_DOLLARS requires an independent fair "
                    "value and FV_BETTOR_INDEPENDENT is NOT_IDENTIFIED"),
            "partialRankingIsWorseThanNone":
                PARTIAL_RANKING_IS_WORSE_THAN_NONE,
        })
        return out

    ranked = sorted(rankable, key=lambda r: Decimal(r[NORTH_STAR]),
                    reverse=True)
    out.update({
        "ranking": [{"candidate": r["candidate"],
                     NORTH_STAR: r[NORTH_STAR],
                     "EXPECTED_NET_DOLLARS": r["EXPECTED_NET_DOLLARS"]}
                    for r in ranked],
        "allocation": ranked[0]["candidate"],
        "why": ("ranked on %s among admissible candidates, with "
                "EXPECTED_NET_DOLLARS reported beside every rate so a "
                "fast penny cannot outrank a slow dollar unnoticed"
                % NORTH_STAR),
    })
    return out


def describe() -> dict:
    return {
        "purpose": "rank competing claims on the same capital",
        "candidates": list(CANDIDATES),
        "northStar": NORTH_STAR,
        "constraints": list(CONSTRAINTS),
        "notPercentageEv": NOT_PERCENTAGE_EV,
        "bothFiguresOrNeither": BOTH_FIGURES_OR_NEITHER,
        "riskBindsFirst": RISK_BINDS_FIRST,
        "partialRankingIsWorseThanNone": PARTIAL_RANKING_IS_WORSE_THAN_NONE,
        "arithmeticDelegatedTo": (
            "research/beta48/shadow/inventory_state.py capital_hours, "
            "imported not copied"),
    }
