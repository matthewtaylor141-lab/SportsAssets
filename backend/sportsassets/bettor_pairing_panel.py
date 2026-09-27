"""§8's PAIRING AND LEARNING LINES, IN BUSINESS VOCABULARY, NEVER SUMMED.

Owner directive, "COMPLETE THE AUTONOMOUS TRADING SYSTEM" §8:

    "Complete the Command Centre with business-facing vocabulary; show
     strategy/model version, serving build, scheduler health,
     opportunities and refusal reasons, orders/acks/fills/unresolved,
     direct pairs/indirect structures/unpaired inventory, gross pairing
     gains/pairing losses/residual results/net portfolio P&L, expected
     vs observed fees, account-wide exposure/limits/authorization,
     learning results/promotions/rollbacks. Keep research,
     demonstrations, shadow and funded separate and unsummed. Do not
     extrapolate a database microbenchmark into trading capacity or
     force turnover to meet a target."

`bettor_command_view` already carries the §18 thirty-one lines. This
module supplies the lines §8 adds and that module does not have: the
indirect structures, the pairing gain/loss split, the fee comparison and
the learning results. It follows the same three rules, which are the only
interesting thing about it.

────────────────────────────────────────────────────────────────────
RULE 1 · ASSEMBLED, NOT COMPUTED.

Every figure comes from the module that owns it -- `bettor_pair_engine`,
`bettor_indirect_structures`, `bettor_completion_policy`,
`bettor_fee_schedule`, `bettor_learning_authority`. Nothing is
re-derived. `bettor_command_view` states the reason and it holds here:
two implementations of the same number eventually disagree and the nicer
one wins.

────────────────────────────────────────────────────────────────────
RULE 2 · NOT IDENTIFIED, NEVER 0.

An unmeasured pairing gain rendered as $0 tells management the pairing
machine broke even. It did not; nobody measured it. And a zero averages,
sums and charts while NOT IDENTIFIED does none of those.

────────────────────────────────────────────────────────────────────
RULE 3 · THE FOUR BOOKS ARE NEVER SUMMED, AND THIS IS WHERE THAT GOES
WRONG MOST EASILY.

Research, demonstration, shadow and funded are four separate books.
Summing them produces a "total P&L" in which a simulated profit offsets a
real loss, and on a page headed with a dollar sign nobody reads the
footnote. So `panel` returns them as four keyed blocks with NO total, and
`total_across_books` does not exist as a function -- there is nothing to
call. A test asserts no key anywhere in the output reads like a total.

The case studies are why the pairing/residual split is broken out rather
than netted. Ferrari's paired book returned **+$10.8M gross** while its
510,107,761 unpaired shares returned **-$9.5M**, and the study states the
directional result dominates the account's total. A single "pairing P&L"
line would have shown the first number and hidden the second.

────────────────────────────────────────────────────────────────────
WHAT THIS MODULE IS NOT. It places, sizes and funds nothing; it is a
read. And it reports no capacity figure at all, because §8 forbids
extrapolating a database microbenchmark into trading capacity -- a line
this module does not have cannot be filled in with the wrong number.
"""

from __future__ import annotations

NOT_IDENTIFIED = "NOT_IDENTIFIED"
DISPLAY_NOT_IDENTIFIED = "NOT IDENTIFIED"

#: The four books §8 requires kept apart. Order is display order.
BOOKS = ("RESEARCH", "DEMONSTRATION", "SHADOW", "FUNDED")

BOOK_MEANING = {
    "RESEARCH": "historical study of other accounts. No orders of ours.",
    "DEMONSTRATION": "controlled inputs, substituted transport. Labelled.",
    "SHADOW": "our decisions, priced against real books, not submitted.",
    "FUNDED": "real orders against a funded account.",
}

NEVER_SUMMED = (
    "research, demonstration, shadow and funded are four separate books "
    "and this panel returns no total. A summed figure lets a simulated "
    "profit offset a real loss, and on a page headed with a dollar sign "
    "nobody reads the footnote")

NEVER_ZERO = (
    "an unmeasured quantity reads NOT IDENTIFIED, never 0. A pairing "
    "gain shown as $0 says the machine broke even, which nobody "
    "measured -- and zeros average, sum and chart while NOT IDENTIFIED "
    "does none of those")

ASSEMBLED_NOT_COMPUTED = (
    "every figure comes from the module that owns it and nothing is "
    "re-derived here")

NO_CAPACITY_LINE = (
    "this panel carries NO capacity figure. §8 forbids extrapolating a "
    "database microbenchmark into trading capacity, and a line that does "
    "not exist cannot be filled in with the wrong number")

# ── the lines, grouped as §8 names them ──────────────────────────────

INVENTORY_LINES = (
    "DIRECT_PAIRS_HELD",
    "INDIRECT_STRUCTURES_HELD",
    "UNPAIRED_INVENTORY",
    "UNPAIRED_INVENTORY_COST",
)

RESULT_LINES = (
    "GROSS_PAIRING_GAINS",
    "PAIRING_LOSSES",
    "RESIDUAL_RESULTS",
    "NET_PORTFOLIO_PNL",
)

FEE_LINES = (
    "EXPECTED_FEES",
    "OBSERVED_FEES",
    "FEE_VARIANCE",
)

LEARNING_LINES = (
    "STRATEGY_VERSION",
    "MODEL_VERSION",
    "LEARNING_RESULTS",
    "PROMOTIONS",
    "ROLLBACKS",
    "PROTECTED_SURFACE_MOVED",
)

STRUCTURE_LINES = (
    "MIDDLES_HELD",
    "MIDDLES_ABOVE_PAR",
    "GAPS_HELD",
    "STRUCTURES_REFUSED_UNESTABLISHABLE",
    "INVENTORY_DOUBLE_ALLOCATED",
)

LINES = (INVENTORY_LINES + STRUCTURE_LINES + RESULT_LINES + FEE_LINES
         + LEARNING_LINES)

#: A line whose value IS a status, not a quantity. A status line reading
#: NOT IDENTIFIED is the measured answer; a quantity line reading it means
#: nobody measured. They look identical on screen and are different facts.
STATUS_LINES = ("STRATEGY_VERSION", "MODEL_VERSION",
                "PROTECTED_SURFACE_MOVED")

QUANTITY = "QUANTITY"
STATUS = "STATUS"

WHY_KIND_MATTERS = (
    "a STATUS line reading NOT IDENTIFIED is the measured answer. A "
    "QUANTITY line reading it means nobody has measured it")

#: Business vocabulary. §8 asks for a management-facing page, and
#: `GROSS_PAIRING_GAINS` is not a phrase anyone says out loud.
BUSINESS_LABEL = {
    "DIRECT_PAIRS_HELD": "Matched pairs held",
    "INDIRECT_STRUCTURES_HELD": "Cross-market structures held",
    "UNPAIRED_INVENTORY": "One-sided inventory",
    "UNPAIRED_INVENTORY_COST": "Cost of one-sided inventory",
    "MIDDLES_HELD": "Structures that cannot lose both legs",
    "MIDDLES_ABOVE_PAR": "...of those, bought above $1 (a bet, not a hedge)",
    "GAPS_HELD": "Structures that cannot win both legs",
    "STRUCTURES_REFUSED_UNESTABLISHABLE": "Pairings refused for missing rules",
    "INVENTORY_DOUBLE_ALLOCATED": "Inventory counted in two structures",
    "GROSS_PAIRING_GAINS": "Gains from completing pairs",
    "PAIRING_LOSSES": "Losses from completing pairs",
    "RESIDUAL_RESULTS": "Result on one-sided inventory",
    "NET_PORTFOLIO_PNL": "Net result, this book only",
    "EXPECTED_FEES": "Fees the schedule predicts",
    "OBSERVED_FEES": "Fees the venue actually charged",
    "FEE_VARIANCE": "Difference",
    "STRATEGY_VERSION": "Strategy version",
    "MODEL_VERSION": "Model version",
    "LEARNING_RESULTS": "Evaluation results",
    "PROMOTIONS": "Models promoted",
    "ROLLBACKS": "Models rolled back",
    "PROTECTED_SURFACE_MOVED": "Did learning change its own gates?",
}

#: The line every reader of a MIDDLE count needs beside it.
MIDDLE_IS_NOT_A_HEDGE = (
    "a structure that cannot lose both legs is not necessarily hedged. "
    "Both case-study accounts' middle books averaged ABOVE $1 -- RN1 "
    "$1.2024, Ferrari $1.1711 -- so an above-par middle carries a "
    "guaranteed shortfall unless the middle lands often enough to pay "
    "for it. MIDDLES_ABOVE_PAR is shown beside MIDDLES_HELD for that "
    "reason and the two are never collapsed")

#: Why the pairing result and the residual result are separate lines.
WHY_THE_SPLIT = (
    "Ferrari's paired book returned +$10.8M gross while its 510,107,761 "
    "unpaired shares returned -$9.5M, and the study states the "
    "directional result dominates the account's total. A single pairing "
    "P&L line shows the first figure and hides the second")


def _line(name, *, value=None, source=None, why=None, note=None) -> dict:
    kind = STATUS if name in STATUS_LINES else QUANTITY
    identified = value is not None and value != NOT_IDENTIFIED
    answered = identified or (kind == STATUS and value is not None)
    return {
        "line": name,
        "label": BUSINESS_LABEL.get(name, name.replace("_", " ").title()),
        "kind": kind,
        "value": value if value is not None else NOT_IDENTIFIED,
        "display": (str(value) if answered else DISPLAY_NOT_IDENTIFIED),
        "status": "IDENTIFIED" if answered else NOT_IDENTIFIED,
        "source": source or NOT_IDENTIFIED,
        "why": None if answered else (
            why or "no module has supplied this figure for this book"),
        "neverZero": None if answered else NEVER_ZERO,
        "whyKindMatters": WHY_KIND_MATTERS if kind == STATUS else None,
        "note": note,
    }


def book_panel(book: str, supplied: dict | None = None) -> dict:
    """One book's lines. Every declared line present, absences visible."""
    if book not in BOOKS:
        raise ValueError(
            "%r is not one of the four books; a fifth book would be "
            "summed into one of these by whoever renders it" % (book,))
    s = dict(supplied or {})
    rows = []
    for name in LINES:
        note = None
        if name == "MIDDLES_HELD":
            note = MIDDLE_IS_NOT_A_HEDGE
        elif name in ("GROSS_PAIRING_GAINS", "RESIDUAL_RESULTS"):
            note = WHY_THE_SPLIT
        elif name == "NET_PORTFOLIO_PNL":
            note = ("this book only. There is no figure on this page that "
                    "adds the four books together")
        entry = s.get(name)
        if isinstance(entry, dict):
            rows.append(_line(name, value=entry.get("value"),
                              source=entry.get("source"),
                              note=entry.get("note") or note))
        else:
            rows.append(_line(name, value=entry, note=note))
    return {
        "book": book,
        "means": BOOK_MEANING[book],
        "lines": rows,
        "identified": sum(1 for r in rows if r["status"] == "IDENTIFIED"),
        "declared": len(rows),
    }


def panel(*, research=None, demonstration=None, shadow=None,
          funded=None, learning_guard=None) -> dict:
    """§8's pairing and learning panel. Four books, no total.

    `learning_guard` is a `bettor_learning_authority.GuardResult.to_dict()`
    if a learning run has been guarded; absent, the line reads NOT
    IDENTIFIED rather than claiming the surface held.
    """
    supplied = {"RESEARCH": research, "DEMONSTRATION": demonstration,
                "SHADOW": shadow, "FUNDED": funded}
    books = {}
    for b in BOOKS:
        per = dict(supplied.get(b) or {})
        if learning_guard is not None and "PROTECTED_SURFACE_MOVED" not in per:
            per["PROTECTED_SURFACE_MOVED"] = {
                "value": ("YES -- PROMOTION VOID"
                          if learning_guard.get("protected_surface_moved")
                          else "no, over the probed surface"),
                "source": "bettor_learning_authority.guard",
                "note": learning_guard.get("not_established"),
            }
        books[b] = book_panel(b, per)
    return {
        "books": books,
        "bookOrder": list(BOOKS),
        "neverSummed": NEVER_SUMMED,
        "neverZero": NEVER_ZERO,
        "assembledNotComputed": ASSEMBLED_NOT_COMPUTED,
        "noCapacityLine": NO_CAPACITY_LINE,
        "middleIsNotAHedge": MIDDLE_IS_NOT_A_HEDGE,
        "whyTheSplit": WHY_THE_SPLIT,
        "lines": list(LINES),
    }


def from_allocation(alloc, legs=None, *, book: str = "SHADOW") -> dict:
    """Turn an indirect-structure allocation into this panel's lines.

    The one place a figure is derived, and it derives nothing new: it
    counts what `bettor_indirect_structures.allocate` already decided.

    `legs` is the list handed to `allocate`. It is needed for the
    double-allocation line and nothing else: without the supplied
    quantities there is no denominator, so that line reads NOT IDENTIFIED
    rather than 0. Reporting an unmeasured 0 there would be the worst
    single failure on this page -- it would say "no share is hedged twice"
    on a page that never checked.
    """
    from . import bettor_indirect_structures as ins

    structures = list(getattr(alloc, "structures", ()) or ())
    refused = list(getattr(alloc, "refused", ()) or ())
    middles = [s for s in structures if s.taxonomy == ins.MIDDLE]
    above = [s for s in middles
             if s.cost_cents is not None and s.cost_cents > ins.CENTS]
    gaps = [s for s in structures if s.taxonomy == ins.GAP]

    consumed: dict = {}
    for s in structures:
        for cid in s.legs:
            consumed[cid] = consumed.get(cid, 0) + s.units
    unallocated = dict(getattr(alloc, "unallocated", {}) or {})

    # Double allocation: a condition whose consumed units exceed what was
    # supplied means the same share was placed in two structures.
    # `allocate` decrements remaining quantity to prevent it; this counts
    # it anyway, so a regression in the allocator shows on the page.
    over = None
    if legs is not None:
        supplied = {l.condition_id: l.quantity for l in legs}
        over = sum(max(0, consumed.get(cid, 0) - q)
                   for cid, q in supplied.items())

    return {
        "INDIRECT_STRUCTURES_HELD": {
            "value": len(structures),
            "source": "bettor_indirect_structures.allocate"},
        "MIDDLES_HELD": {
            "value": len(middles),
            "source": "bettor_indirect_structures.allocate"},
        "MIDDLES_ABOVE_PAR": {
            "value": len(above),
            "source": "bettor_indirect_structures.allocate",
            "note": MIDDLE_IS_NOT_A_HEDGE},
        "GAPS_HELD": {
            "value": len(gaps),
            "source": "bettor_indirect_structures.allocate"},
        "STRUCTURES_REFUSED_UNESTABLISHABLE": {
            "value": len(refused),
            "source": "bettor_indirect_structures.allocate",
            "note": ("a refusal is a missing fact about the contracts, not "
                     "a structure that scored badly")},
        "INVENTORY_DOUBLE_ALLOCATED": {
            "value": over,
            "source": ("bettor_indirect_structures.allocate, against the "
                       "supplied legs" if over is not None else None),
            "note": ("shown rather than assumed so a regression in the "
                     "allocator is visible. Without the supplied legs there "
                     "is no denominator, so this reads NOT IDENTIFIED "
                     "instead of 0 -- an unmeasured 0 here would claim no "
                     "share is hedged twice on a page that never checked")},
        "UNPAIRED_INVENTORY": {
            "value": sum(unallocated.values()),
            "source": "bettor_indirect_structures.allocate"},
    }


def from_cohort(report: dict, *, book: str = "SHADOW") -> dict:
    """Turn a `bettor_completion_policy.Cohort.report()` into panel lines."""
    r = dict(report or {})
    by = dict(r.get("result_by_disposition") or {})
    completed = by.get("COMPLETED_PAIR")
    residual = None
    parts = [by.get(k) for k in ("SOLD_UNPAIRED",
                                "CARRIED_UNPAIRED_TO_SETTLEMENT")
             if by.get(k) is not None]
    if parts:
        residual = sum(parts)
    gains = completed if (completed is not None and completed > 0) else None
    losses = completed if (completed is not None and completed < 0) else None
    return {
        "GROSS_PAIRING_GAINS": {
            "value": gains, "source": "bettor_completion_policy.Cohort",
            "note": WHY_THE_SPLIT},
        "PAIRING_LOSSES": {
            "value": losses, "source": "bettor_completion_policy.Cohort"},
        "RESIDUAL_RESULTS": {
            "value": residual, "source": "bettor_completion_policy.Cohort",
            "note": WHY_THE_SPLIT},
        "NET_PORTFOLIO_PNL": {
            "value": r.get("net_result_all_initiations"),
            "source": "bettor_completion_policy.Cohort",
            "note": ("every initiation, not only the completed pairs. "
                     "This book only")},
    }


def describe() -> dict:
    return {
        "books": BOOKS,
        "bookMeaning": BOOK_MEANING,
        "lines": LINES,
        "businessLabels": BUSINESS_LABEL,
        "neverSummed": NEVER_SUMMED,
        "neverZero": NEVER_ZERO,
        "noCapacityLine": NO_CAPACITY_LINE,
        "middleIsNotAHedge": MIDDLE_IS_NOT_A_HEDGE,
        "whyTheSplit": WHY_THE_SPLIT,
        "placesNoOrder": "this module is a read; it places, sizes and funds "
                         "nothing",
    }
