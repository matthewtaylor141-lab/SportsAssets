"""Maker economics from ACTUAL ENTRY AND EXIT PRICES. No spread algebra.

WHAT THIS REPLACES, AND WHY THE OLD VERSION WAS WRONG.

The previous model read

    value_if_filled = half_spread - adverse_selection - carry - exit_cost

and concluded that a quote exited by crossing is negative for any
non-negative adverse selection, "because a maker earns half a spread and
a taker exit pays a whole one". Review supplied the counterexample and it
is decisive:

    bid 0.485, ask 0.515, book unchanged, no fees
    our passive buy fills at            0.485
    we immediately sell at the bid      0.485
    actual cash P&L                     0.0000
    the old model reported             -0.0150

The error is a mixed reference. `half_spread` credits the entry against
the MIDPOINT (0.500 - 0.485 = +0.015) and `exit_cost = spread` charges
the exit against the BID (0.515 - 0.485 = 0.030). The same 0.015 is
earned once and paid twice. Buying at 0.485 and selling at 0.485 is zero,
and no amount of spread algebra changes that.

BOTH FINDINGS FROM THE OLD MODEL ARE WITHDRAWN:

  * "a crossed round trip is necessarily negative" -- FALSE. Passive in,
    aggressive out is ZERO gross on an unchanged book. What makes it
    negative is fees, an adverse move, and carry -- each of which has to
    be sourced, not assumed.
  * "breakeven p_fill = 0.0092 held to settlement" -- WITHDRAWN. It was
    computed from the broken filled-value term and from inputs I never
    sourced.

THE CORRECT MODEL IS THE ONE THE CASH ACTUALLY FOLLOWS:

    round_trip = (exit_price - entry_price) x qty
               - fees_total
               + verified_rebates
               - carry(duration)

Every term is a price or a dated cash amount. There is no "spread
captured" line, because spread capture is not a cash flow -- it is a
DESCRIPTION of the difference between two prices that are already in the
formula.

HOW ADVERSE SELECTION ENTERS -- ONCE. It is not a separate subtraction.
A resting buy fills when someone chooses to sell to us, so the reference
price CONDITIONAL ON OUR FILL is not the reference price we quoted
against. That shows up as `conditional_reference_move`, a signed change
in the midpoint given a fill, and the exit price is then built from the
MOVED reference. Subtracting an "adverse selection" term on top of an
exit price that already embeds the move would double-count it, which is
the same mistake in a different place.

HOLDING TO SETTLEMENT IS NOT A HALF-SPREAD. The payoff is
E[settlement | our fill], a conditional expectation over outcomes
selected by whoever traded with us. A fill may select precisely the
markets where our quote was mispriced. That expectation is
NOT_IDENTIFIED and this module will not substitute anything for it.

EVERY INPUT IS SOURCED OR THE ANSWER IS NOT_IDENTIFIED. `Assumption`
carries a value AND where it came from. MEASURED, HYPOTHETICAL and
NOT_IDENTIFIED are different things and a result built on a HYPOTHETICAL
is labelled hypothetical all the way out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

NOT_IDENTIFIED = "NOT_IDENTIFIED"
IDENTIFIED = "IDENTIFIED"
HYPOTHETICAL = "HYPOTHETICAL"
MEASURED = "MEASURED"
# A term read off the venue's own published schedule. Stronger than
# HYPOTHETICAL -- nobody invented it -- and weaker than MEASURED, which
# here means measured on OUR fills. A result carrying a PUBLISHED term
# is not hypothetical and is not verified either.
PUBLISHED = "PUBLISHED"
SHADOW = "SHADOW"

# Exit routes. The price each one gets is different, and the difference
# is the whole question -- so it is a choice the caller states, never a
# default this module picks.
EXIT_PASSIVE = "PASSIVE_AT_ASK"        # rest on the other side; may not fill
EXIT_AGGRESSIVE = "AGGRESSIVE_AT_BID"  # cross out; fills now
EXIT_SETTLEMENT = "HOLD_TO_SETTLEMENT"  # no exit trade at all

# The one term with a measurement behind it: PMUS half-spread, from the
# sprint verdict. It is NOT used as a P&L term -- it is a book statistic.
MEASURED_HALF_SPREAD_PER_SHARE = 0.0050


@dataclass(frozen=True)
class Assumption:
    """A number and where it came from. The source travels with it."""
    value: float | None
    source: str = NOT_IDENTIFIED        # MEASURED | HYPOTHETICAL | ...
    note: str = ""

    @property
    def known(self) -> bool:
        return self.value is not None and math.isfinite(self.value)


def measured(v: float, note: str = "") -> Assumption:
    return Assumption(v, MEASURED, note)


def hypothetical(v: float, note: str = "") -> Assumption:
    return Assumption(v, HYPOTHETICAL, note)


def unknown(note: str = "") -> Assumption:
    return Assumption(None, NOT_IDENTIFIED, note)


def published(v: float, note: str = "") -> Assumption:
    return Assumption(v, PUBLISHED, note)


def round_trip_fee(schedule, *, entry_price, exit_price, exit_route,
                   qty: float = 1.0) -> Assumption:
    """The round trip's TOTAL fee per contract, under a dated schedule.

    A ROUND TRIP HAS TWO FEES AT TWO PRICES, and until now this module
    took one flat `fee_per_contract` for the whole thing. Under the
    published schedule that is wrong in kind, not just in size: the fee
    is proportional to p(1-p), so entry at 0.485 and exit at 0.465 are
    charged different amounts, and the exit route decides which side of
    the schedule applies.

        EXIT_PASSIVE      maker in, maker out    -> rebate on BOTH legs
        EXIT_AGGRESSIVE   maker in, taker out    -> rebate then charge
        EXIT_SETTLEMENT   maker in, no exit fill -> rebate on entry only

    Returned signed and per contract: positive is a net cost, negative
    is a net credit. The entry leg is always a maker fill, because that
    is what a MakerQuote is.

    ROUNDING IS PER FILL AND THE QUANTITY MATTERS. The rebate is
    computed on `qty` and divided back, so a 1-contract quote whose
    rebate rounds to zero reports zero per contract rather than the
    continuous rate. Multiplying a continuous rate by volume is how
    small clips get credited with income they never receive.
    """
    if qty is None or not math.isfinite(qty) or qty <= 0:
        return unknown("a fee needs a positive quantity; rounding is per fill")
    for p in (entry_price, exit_price):
        if exit_route != EXIT_SETTLEMENT or p is entry_price:
            if p is None or not math.isfinite(p) or not 0 < p < 1:
                return unknown("a published fee needs a price in (0,1)")

    total = float(schedule.maker_rebate(qty, entry_price))      # negative
    if exit_route == EXIT_AGGRESSIVE:
        total += float(schedule.taker_fee(qty, exit_price))
    elif exit_route == EXIT_PASSIVE:
        total += float(schedule.maker_rebate(qty, exit_price))
    elif exit_route != EXIT_SETTLEMENT:
        return unknown("unknown exit route %r" % (exit_route,))

    return published(
        total / qty,
        note=("%s, %s, entry %.4f exit %s, %g contracts, rounded per fill"
              % (schedule.schedule_id, exit_route, entry_price,
                 ("%.4f" % exit_price) if exit_route != EXIT_SETTLEMENT
                 else "none", qty)))


@dataclass(frozen=True)
class MakerQuote:
    """One passive quote and the assumptions needed to value it."""
    name: str = "unnamed"
    side: str = "BUY"                   # BUY rests at the bid
    entry_price: float | None = None    # what we actually pay / receive
    qty: float = 1.0
    exit_route: str = EXIT_AGGRESSIVE

    # Book at entry.
    bid: float | None = None
    ask: float | None = None

    # Conditional on OUR FILL, how does the midpoint move? Negative is
    # adverse for a buy. This is where adverse selection lives, and it
    # lives here ONLY -- the exit price is built from the moved
    # reference, so there is no second subtraction anywhere.
    conditional_reference_move: Assumption = field(default_factory=unknown)

    # Spread at the moment of exit. Not necessarily the entry spread.
    exit_spread: Assumption = field(default_factory=unknown)

    p_fill: Assumption = field(default_factory=unknown)
    fee_per_contract: Assumption = field(default_factory=unknown)
    rebate_verified_per_contract: Assumption = field(default_factory=unknown)
    carry_per_contract_per_hour: Assumption = field(default_factory=unknown)
    duration_hours: Assumption = field(default_factory=unknown)

    # E[settlement payout | our fill]. Only for EXIT_SETTLEMENT, and
    # there is no substitute for it.
    conditional_settlement_value: Assumption = field(default_factory=unknown)

    authority: str = SHADOW


@dataclass
class RoundTrip:
    status: str
    per_contract: float | None = None
    total: float | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    terms: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    evidence: str = NOT_IDENTIFIED      # MEASURED if every source is
    why: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    if not (math.isfinite(bid) and math.isfinite(ask)) or ask <= bid:
        return None
    return (bid + ask) / 2.0


def exit_price(q: MakerQuote) -> tuple[float | None, list]:
    """The price the exit actually gets, from the MOVED reference.

    One reference for both legs: the midpoint at entry, plus the move
    conditional on our fill, plus or minus half the exit spread
    depending on which side of it we have to trade.
    """
    missing = []
    m0 = mid(q.bid, q.ask)
    if m0 is None:
        missing.append("book (bid/ask) at entry")
    if not q.conditional_reference_move.known:
        missing.append("conditional_reference_move")
    if q.exit_route == EXIT_SETTLEMENT:
        if not q.conditional_settlement_value.known:
            missing.append("conditional_settlement_value "
                           "(E[settlement | our fill]) -- a half-spread is "
                           "not a substitute for this")
        return ((q.conditional_settlement_value.value
                 if q.conditional_settlement_value.known else None), missing)

    if not q.exit_spread.known:
        missing.append("exit_spread")
    if missing:
        return None, missing

    m1 = m0 + q.conditional_reference_move.value
    half = q.exit_spread.value / 2.0
    if q.side.upper() == "BUY":
        # We are long and must sell: passive sells at the ask, aggressive
        # hits the bid.
        return (m1 + half if q.exit_route == EXIT_PASSIVE else m1 - half), []
    return (m1 - half if q.exit_route == EXIT_PASSIVE else m1 + half), []


def round_trip(q: MakerQuote) -> RoundTrip:
    """Conditional round-trip P&L, given a fill. Actual prices only."""
    terms: dict = {"exit_route": q.exit_route, "side": q.side}
    missing: list = []

    entry = q.entry_price
    if entry is None:
        missing.append("entry_price")
    xp, xmiss = exit_price(q)
    missing.extend(xmiss)

    for label, a in (("fee_per_contract", q.fee_per_contract),
                     ("carry_per_contract_per_hour",
                      q.carry_per_contract_per_hour),
                     ("duration_hours", q.duration_hours)):
        if not a.known:
            missing.append(label)
    # A rebate that is not verified is ZERO, not missing. Absence of a
    # verified incentive is a known quantity: nothing.
    rebate = (q.rebate_verified_per_contract.value
              if q.rebate_verified_per_contract.known else 0.0)
    terms["rebate_per_contract"] = rebate
    terms["rebate_source"] = q.rebate_verified_per_contract.source

    if missing:
        return RoundTrip(status=NOT_IDENTIFIED, terms=terms,
                         entry_price=entry, exit_price=xp,
                         missing=sorted(set(missing)),
                         why=("%d input(s) unsourced. The model is "
                              "(exit - entry) x qty - fees + rebates - "
                              "carry; every term must come from somewhere"
                              % len(set(missing))))

    carry = (q.carry_per_contract_per_hour.value * q.duration_hours.value)
    gross = xp - entry if q.side.upper() == "BUY" else entry - xp
    per = gross - q.fee_per_contract.value + rebate - carry

    terms.update({
        "gross_per_contract": gross,
        "fee_per_contract": q.fee_per_contract.value,
        "carry_per_contract": carry,
        "mid_at_entry": mid(q.bid, q.ask),
        "conditional_reference_move": q.conditional_reference_move.value,
    })
    sources = {q.conditional_reference_move.source, q.exit_spread.source,
               q.fee_per_contract.source, q.carry_per_contract_per_hour.source,
               q.duration_hours.source}
    # THE WEAKEST SOURCE WINS. A result is only as good as its worst
    # input, and the ordering is not cosmetic: PUBLISHED sits strictly
    # between HYPOTHETICAL (someone chose the number) and MEASURED
    # (measured on OUR fills). Mixing a published fee into an otherwise
    # hypothetical grid does not make the grid published.
    evidence = NOT_IDENTIFIED
    for level in (NOT_IDENTIFIED, HYPOTHETICAL, PUBLISHED, MEASURED):
        if level in sources:
            evidence = level
            break
    else:
        evidence = MEASURED if sources == {MEASURED} else NOT_IDENTIFIED
    return RoundTrip(status=IDENTIFIED, per_contract=per,
                     total=per * q.qty, entry_price=entry, exit_price=xp,
                     terms=terms, missing=[], evidence=evidence,
                     why=("(%.6f exit - %.6f entry) = %.6f gross, less %.6f "
                          "fee, plus %.6f rebate, less %.6f carry"
                          % (xp, entry, gross, q.fee_per_contract.value,
                             rebate, carry)))


def expected_value(q: MakerQuote) -> dict:
    """p_fill x round_trip + (1 - p_fill) x 0.

    An unfilled quote has no cash flow. Its real cost is opportunity and
    risk-budget occupancy, which are not cash and are not modelled as
    cash here -- inventing a number for them would be the same class of
    error as the half-spread credit.
    """
    rt = round_trip(q)
    if rt.status != IDENTIFIED:
        return {"status": NOT_IDENTIFIED, "missing": rt.missing,
                "round_trip": rt.to_dict(),
                "why": "the conditional round trip is not identified"}
    if not q.p_fill.known:
        return {"status": NOT_IDENTIFIED, "missing": ["p_fill"],
                "conditional_round_trip_per_contract": rt.per_contract,
                "round_trip": rt.to_dict(),
                "why": ("the conditional round trip IS identified at "
                        "%.6f/contract; only the probability of reaching it "
                        "is not" % rt.per_contract)}
    ev = q.p_fill.value * rt.total
    return {"status": IDENTIFIED, "expected_value": ev,
            "evidence": rt.evidence, "round_trip": rt.to_dict(),
            "why": "p_fill %.4f x %.6f" % (q.p_fill.value, rt.total)}


def breakeven_p_fill(q: MakerQuote) -> dict:
    """What p_fill makes this zero?

    WITH AN UNFILLED QUOTE COSTING NOTHING IN CASH, the answer is
    degenerate and saying so is more honest than producing a number: if
    the conditional round trip is positive, ANY p_fill > 0 has positive
    expected value; if it is negative, no p_fill does. The interesting
    quantity is therefore the SIGN of the round trip, not a threshold.

    The old 0.0092 came from a quote-cost term I never sourced and a
    filled-value term that was wrong. Both are withdrawn.
    """
    rt = round_trip(q)
    if rt.status != IDENTIFIED:
        return {"status": NOT_IDENTIFIED, "missing": rt.missing}
    if rt.per_contract > 0:
        return {"status": IDENTIFIED, "breakeven_p_fill": 0.0,
                "verdict": "POSITIVE_FOR_ANY_NONZERO_FILL_RATE",
                "conditional_round_trip_per_contract": rt.per_contract,
                "evidence": rt.evidence,
                "why": ("a fill is worth %+.6f, so any fill rate above zero "
                        "is positive in expectation. The binding question "
                        "is SIZE and opportunity cost, not probability"
                        % rt.per_contract)}
    if rt.per_contract < 0:
        return {"status": IDENTIFIED, "breakeven_p_fill": None,
                "verdict": "NEGATIVE_AT_ANY_FILL_RATE",
                "conditional_round_trip_per_contract": rt.per_contract,
                "evidence": rt.evidence,
                "why": ("a fill is worth %+.6f, so filling more often is "
                        "worse. This is a statement about THESE sourced "
                        "inputs, not a universal claim about crossing out"
                        % rt.per_contract)}
    return {"status": IDENTIFIED, "breakeven_p_fill": None,
            "verdict": "EXACTLY_ZERO", "evidence": rt.evidence,
            "conditional_round_trip_per_contract": 0.0}


def describe() -> dict:
    return {
        "model": ("round_trip = (exit_price - entry_price) x qty "
                  "- fees + verified_rebates - carry(duration)"),
        "reference_discipline": ("one reference for both legs: the midpoint "
                                 "at entry, moved by "
                                 "conditional_reference_move. Adverse "
                                 "selection enters there and NOWHERE else"),
        "withdrawn": {
            "universal_negative_crossing": (
                "FALSE. bid 0.485 / ask 0.515: passive buy at 0.485, sell at "
                "the unchanged bid 0.485, P&L 0.0000. The old model said "
                "-0.0150 by crediting entry against the mid and charging "
                "exit against the bid"),
            "breakeven_p_fill_0.0092": (
                "WITHDRAWN. Computed from the broken filled-value term and "
                "from inputs that were never sourced"),
        },
        "settlement": ("E[settlement | our fill] is a conditional "
                       "expectation over outcomes selected by whoever "
                       "traded with us. NOT_IDENTIFIED, and a half-spread "
                       "is not a substitute"),
        "measured_book_statistic": {
            "pmus_half_spread_per_share": MEASURED_HALF_SPREAD_PER_SHARE,
            "note": "a book statistic, NOT a P&L term",
        },
        "authority": SHADOW,
    }
