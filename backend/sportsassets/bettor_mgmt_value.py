"""VALUING THE MANAGEMENT ACTIONS ON A POSITION WE ALREADY HOLD.

WHY THIS MODULE EXISTS: THERE WERE TWO ACTION VOCABULARIES AND ONLY ONE
OF THEM WAS VALUED.

    bettor_decision_engine   PAIR_BUY / TAKE_* / MAKE_* / MERGE / NO_TRADE
                             every one priced, or refused with a named
                             blocker and its uncertainty

    bettor_desk.Policy       ENTER / COMPLETE_PAIR / REDUCE / EXIT / HOLD
                             / AMEND / CANCEL -- decided by RULES, with
                             no EV attached to any of them

The engine's vocabulary is about ACQUIRING: is there an opportunity to
open. The desk's is about MANAGING: we are already long a leg, what now.
The second is the primary objective and it was the unvalued one. A rule
that says "complete the pair when the complement is within 3 cents" is a
policy, not a valuation -- it cannot say what completing is WORTH, so it
cannot be compared against holding, reducing or exiting, and there is
nothing for an improvement loop to improve against.

WHAT THIS PROVIDES. The same `Candidate` contract the acquisition engine
uses -- action, status, EV or a named blocker, assumptions, uncertainty
-- for the management actions. One position state in, one ranked
comparison out, with every action either priced or refused by name.

    IDENTICAL CASH-FLOW DISCIPLINE. Every action's value is the sum of
    the dated cash flows it causes, measured as an INCREMENTAL change
    against HOLD. Nothing is counted twice under a second description:
    "locking in the spread" and "avoiding adverse selection" are not
    addends, they are two ways of describing the same cash.

    HOLD IS THE BASELINE AND ITS VALUE IS NOT ZERO. This differs from
    the flat case, and getting it wrong is how a management engine talks
    itself into churning. From flat, NO_TRADE costs nothing. Holding a
    position is not free: it commits capital and carries the position to
    settlement. So HOLD is priced, and everything else is priced as a
    difference from it.

WHERE AN ACTION CANNOT BE VALUED IT IS REFUSED, NOT DEFAULTED.
NOT_IDENTIFIED is not zero, and an unscored action never wins by
default. Three refusals here are load-bearing and each names a verified
cause rather than a guess:

    HOLD without a settlement estimate. Holding to settlement is worth
    payout x qty, and payout is unknown while the market is open. On
    HISTORICAL positions the market has resolved and the payout is a
    FACT -- so hold is EXACT there and refused live. That asymmetry is
    the reason the historical management test is deliverable before the
    live one, and it is not a defect of either.

    COMPLETE_PAIR without an OBSERVED complement ask. Verified on the
    stored payloads: /v1/markets/{slug}/book returns six keys -- bids,
    offers, stats, marketSlug, transactTime, state -- one instrument's
    ladder, with no second side in it. So the complement ask is not
    merely unparsed, it is absent from what we hold. And a DERIVED
    complement (1 - own bid) is worse than useless: it makes
    own_ask + comp_ask = 1 + spread by construction, so the pair can
    never be at or below par. Class C measured 3,732 such pairs and 0
    were at or below par.

    WAIT and REPRICE. Both are worth the difference between acting now
    and acting later, which is a claim about future prices. Priced only
    where a bound can be stated, refused otherwise -- never priced at
    zero, which would make waiting free.

WHAT THIS MODULE IS NOT.

    It is NOT a fill probability. Where an action requires one of OUR
    resting orders to fill, that requirement is reported and P_FILL
    stays NOT_IDENTIFIED. An action needing a fill is valued CONDITIONAL
    on the fill, with the condition attached, and the condition is never
    quietly assigned a probability.

    It does NOT decide. It ranks and returns. The caller selects, and
    the risk and capital checks sit between this and any order.
"""

from __future__ import annotations

from dataclasses import dataclass, field

VERSION = "BETTOR_MGMT_VALUE_V1"

NOT_IDENTIFIED = "NOT_IDENTIFIED"
BLOCKED = "BLOCKED"
IDENTIFIED = "IDENTIFIED"

# The management actions, and each one's meaning stated once so two
# call sites cannot disagree about what REDUCE means.
HOLD = "HOLD"                    # carry the position as it stands
EXIT_NOW = "EXIT_NOW"            # sell the whole leg into the bid
REDUCE = "REDUCE"                # sell a declared fraction into the bid
COMPLETE_PAIR = "COMPLETE_PAIR"  # buy the complement to lock par
WAIT = "WAIT"                    # act later at a better price
REPRICE = "REPRICE"              # move a resting order

ACTIONS = (HOLD, EXIT_NOW, REDUCE, COMPLETE_PAIR, WAIT, REPRICE)

# Settlement-estimate provenance. The distinction is the whole reason
# the historical test can run and the live one cannot.
SETTLED_FACT = "SETTLED_OBSERVED_PAYOUT"
SETTLE_BOUNDED = "SETTLEMENT_BOUNDED_ESTIMATE"
SETTLE_UNKNOWN = "SETTLEMENT_NOT_ESTIMATED"


@dataclass
class Candidate:
    """One action, priced or refused. Deliberately the same shape as
    bettor_decision_engine.Candidate so one comparison table can hold
    acquisition and management actions side by side."""
    action: str
    status: str
    ev_usd: float | None = None
    # Cash now (negative = we pay) and cash at settlement, kept apart so
    # a reader can see WHICH leg of the cash flow an action moves rather
    # than only the net.
    cash_now_usd: float | None = None
    cash_at_settlement_usd: float | None = None
    blocker: str | None = None
    why: str = ""
    requires_our_fill: bool = False
    assumptions: list = field(default_factory=list)
    uncertainty: str = ""

    def to_dict(self) -> dict:
        return {
            "action": self.action, "status": self.status,
            "ev_usd": self.ev_usd,
            "cash_now_usd": self.cash_now_usd,
            "cash_at_settlement_usd": self.cash_at_settlement_usd,
            "blocker": self.blocker, "why": self.why,
            "requires_our_fill": self.requires_our_fill,
            "assumptions": list(self.assumptions),
            "uncertainty": self.uncertainty,
        }


@dataclass
class Position:
    """What we hold, and what it cost. `basis_usd` is the cash already
    spent, INCLUDING fees already paid, because that is what the desk's
    Portfolio carries and a second convention here would drift."""
    condition_id: str
    outcome_index: int
    qty: float
    basis_usd: float

    @property
    def avg_cost(self) -> float | None:
        return (self.basis_usd / self.qty) if self.qty > 0 else None


@dataclass
class Market:
    """The evidence available about this position's market at one
    instant. Every field may be absent, and absence is reported rather
    than filled in."""
    # OUR LEG's own book.
    bid: float | None = None
    bid_size: float = 0.0
    ask: float | None = None
    ask_size: float = 0.0
    # THE COMPLEMENT LEG's own book, and where it came from. OBSERVED
    # only when the sibling instrument's own book was read; a value
    # computed from our own book is DERIVED and refused.
    comp_ask: float | None = None
    comp_ask_size: float = 0.0
    comp_source: str = "ABSENT"
    # SETTLEMENT. `payout` is the cash one contract of OUR leg pays.
    payout: float | None = None
    payout_low: float | None = None
    payout_high: float | None = None
    payout_source: str = SETTLE_UNKNOWN


def _fee(fee_fn, qty, price) -> float | None:
    """Fees are an input, never an assumption. A missing schedule yields
    None, which makes the action NOT_IDENTIFIED rather than free -- the
    exact failure that booked 334 live fills at zero cost for 37
    minutes while the ledger reported invariant_ok."""
    if fee_fn is None:
        return None
    try:
        return float(fee_fn(qty=qty, price=price))
    except Exception:                                       # noqa: BLE001
        return None


def value_hold(pos: Position, mk: Market) -> Candidate:
    """Carry the position to settlement.

    THE BASELINE, AND IT IS PRICED. Holding pays payout x qty at
    settlement against a basis already spent, and it commits that
    capital until then. Treating HOLD as zero would make every action
    that releases cash look free.
    """
    if mk.payout is not None:
        ev = mk.payout * pos.qty - pos.basis_usd
        exact = mk.payout_source == SETTLED_FACT
        return Candidate(
            action=HOLD, status=IDENTIFIED, ev_usd=ev,
            cash_now_usd=0.0,
            cash_at_settlement_usd=mk.payout * pos.qty,
            why=("hold to settlement: %.4f x %.4g = %.4f against a basis "
                 "of %.4f" % (mk.payout, pos.qty, mk.payout * pos.qty,
                              pos.basis_usd)),
            assumptions=([] if exact else
                         ["the payout is an ESTIMATE, not an observed "
                          "settlement"]),
            uncertainty=("none: the market has settled and this payout "
                         "was observed" if exact else
                         "the payout estimate carries the settlement "
                         "model's error, which is not quantified here"))
    if mk.payout_low is not None and mk.payout_high is not None:
        lo = mk.payout_low * pos.qty - pos.basis_usd
        hi = mk.payout_high * pos.qty - pos.basis_usd
        return Candidate(
            action=HOLD, status=IDENTIFIED, ev_usd=None,
            cash_now_usd=0.0,
            why=("hold is bounded, not pointed: settlement value lies in "
                 "[%.4f, %.4f]" % (lo, hi)),
            assumptions=["the bounds are asserted by the caller and are "
                         "only as good as their derivation"],
            uncertainty=("an interval of %.4f. A point EV is NOT stated, "
                         "because naming the midpoint would invent a "
                         "distribution over the interval" % (hi - lo)))
    # REFUSED, and this is the live case.
    return Candidate(
        action=HOLD, status=NOT_IDENTIFIED,
        blocker="SETTLEMENT_NOT_ESTIMATED",
        why=("holding is worth payout x qty and no payout estimate or "
             "bound was supplied. The market has not settled and no "
             "independent settlement model is validated: "
             "P_BETTOR_INDEPENDENT_V3 measured the blend WORSE than the "
             "venue price by 0.00926 log loss, CI [-0.00222, +0.02036], "
             "INCREMENTAL_SIGNAL_STATUS NOT_DETECTED"),
        uncertainty=("unbounded. NOT a value of zero: an unpriced hold "
                     "must not lose to a priced exit by default"))


def value_exit(pos: Position, mk: Market, *, fee_fn=None,
               fraction: float = 1.0) -> Candidate:
    """Sell into the bid, all of it or a declared fraction.

    ONE FUNCTION FOR BOTH BECAUSE THEY ARE ONE CASH FLOW at different
    sizes. Two functions drift: the first time a fee rule changed, one
    of them would keep the old one.

    NO FILL PROBABILITY IS NEEDED. Selling into a displayed bid with
    depth is a TAKER action -- it executes against liquidity that is
    there, so P_FILL does not enter. That is why exit is valuable
    evidence while maker actions are not.
    """
    act = EXIT_NOW if fraction >= 1.0 else REDUCE
    if not (0.0 < fraction <= 1.0):
        return Candidate(action=act, status=BLOCKED,
                         blocker="FRACTION_OUT_OF_RANGE",
                         why="a reduce fraction must lie in (0, 1]",
                         uncertainty="none; this is a caller error")
    if mk.bid is None:
        return Candidate(
            action=act, status=NOT_IDENTIFIED, blocker="NO_BID",
            why="no bid was readable, so no exit price is known",
            uncertainty="the exit price is unknown, not zero")
    want = pos.qty * fraction
    size = min(want, mk.bid_size)
    if size <= 0:
        return Candidate(
            action=act, status=NOT_IDENTIFIED, blocker="NO_EXECUTABLE_DEPTH",
            why=("a bid with no size behind it is not an exit; "
                 "bid_size=%.4g" % mk.bid_size),
            uncertainty="a quote without depth is not an opportunity")
    fee = _fee(fee_fn, size, mk.bid)
    if fee is None:
        return Candidate(
            action=act, status=NOT_IDENTIFIED,
            blocker="FEE_SCHEDULE_NOT_ESTABLISHED",
            why=("exiting costs a fee that is not established, and an "
                 "unknown cost must not be priced at zero"),
            uncertainty="execution cost unknown, so net value is unknown")
    proceeds = mk.bid * size - fee
    # THE BASIS RELEASED IS PRO-RATA ON THE SHARE ACTUALLY SOLD, which
    # may be less than the share we wanted when depth is short. Using
    # the intended fraction here would release basis for contracts still
    # held and overstate the exit.
    released = pos.basis_usd * (size / pos.qty) if pos.qty > 0 else 0.0
    ev = proceeds - released
    short = size < want - 1e-12
    return Candidate(
        action=act, status=IDENTIFIED, ev_usd=ev,
        cash_now_usd=proceeds, cash_at_settlement_usd=0.0,
        why=("sell %.4g of %.4g into the bid at %.4f for %.4f net of "
             "%.4f fees, releasing %.4f of basis%s"
             % (size, want, mk.bid, proceeds, fee, released,
                "; DEPTH-LIMITED, the rest stays held" if short else "")),
        assumptions=["a displayed bid with size is executable at that "
                     "price for that size"],
        uncertainty=("realized proceeds may be lower if the bid moves "
                     "between the decision and the send; that latency "
                     "is not measured here"))


def value_complete_pair(pos: Position, mk: Market, *, fee_fn=None) -> Candidate:
    """Buy the complement leg so the pair pays exactly 1.00.

    THIS IS THE FERRARI-INSPIRED COMPLETION ACTION and it is the one
    that most wants a forecast and needs none: a completed pair pays
    1.00 whatever the outcome, so its value contains no settlement
    model at all.
    """
    if mk.comp_source == "DERIVED":
        return Candidate(
            action=COMPLETE_PAIR, status=BLOCKED,
            blocker="COMPLEMENT_IS_DERIVED_NOT_OBSERVED",
            why=("the complement ask was computed from our own book, so "
                 "own_ask + comp_ask = 1 + spread BY CONSTRUCTION and no "
                 "completion can be at or below par. Class C measured "
                 "3,732 such pairs: 0 at or below par, minimum basis "
                 "1.0050"),
            assumptions=["a derived complement cannot disagree with par"],
            uncertainty="none; this is an identity, not an estimate")
    if mk.comp_source != "OBSERVED" or mk.comp_ask is None:
        return Candidate(
            action=COMPLETE_PAIR, status=NOT_IDENTIFIED,
            blocker="COMPLEMENT_%s" % mk.comp_source,
            why=("the complement leg's own book was not read. Verified on "
                 "the stored payloads: /v1/markets/{slug}/book returns "
                 "bids, offers, stats, marketSlug, transactTime, state -- "
                 "one instrument's ladder, with no second side in it"),
            uncertainty="the completion price is unknown, not absent")
    size = min(pos.qty, mk.comp_ask_size)
    if size <= 0:
        return Candidate(
            action=COMPLETE_PAIR, status=NOT_IDENTIFIED,
            blocker="NO_EXECUTABLE_DEPTH",
            why=("no depth behind the complement ask; comp_ask_size=%.4g"
                 % mk.comp_ask_size),
            uncertainty="a quote without depth is not an opportunity")
    fee = _fee(fee_fn, size, mk.comp_ask)
    if fee is None:
        return Candidate(
            action=COMPLETE_PAIR, status=NOT_IDENTIFIED,
            blocker="FEE_SCHEDULE_NOT_ESTABLISHED",
            why="the completion fee is not established and is not zero",
            uncertainty="execution cost unknown, so net value is unknown")
    cost = mk.comp_ask * size + fee
    # PAIRED CONTRACTS PAY 1.00 EACH. Any contracts of our leg beyond
    # the paired size remain UNPAIRED and are NOT valued here -- they
    # are still an open directional position, and folding them in at the
    # paired price would be the double count this discipline forbids.
    released = pos.basis_usd * (size / pos.qty) if pos.qty > 0 else 0.0
    ev = 1.00 * size - cost - released
    unpaired = pos.qty - size
    return Candidate(
        action=COMPLETE_PAIR, status=IDENTIFIED, ev_usd=ev,
        cash_now_usd=-cost, cash_at_settlement_usd=1.00 * size,
        why=("buy %.4g complement at %.4f for %.4f including %.4f fees; "
             "%.4g pairs pay 1.00 at settlement against %.4f of our basis"
             "%s" % (size, mk.comp_ask, cost, fee, size, released,
                     ("; %.4g contracts remain UNPAIRED and directional, "
                      "not valued here" % unpaired) if unpaired > 1e-12
                     else "")),
        assumptions=["the account holds both legs independently, so "
                     "buying the complement is an acquisition and not a "
                     "netting close -- if it nets, the par model does "
                     "not apply and this value is wrong"],
        uncertainty=("no settlement forecast enters this number. The "
                     "residual risk is execution: two legs means two "
                     "fills, and a partial completion leaves a naked "
                     "directional position"))


def compare(pos: Position, mk: Market, *, fee_fn=None,
            reduce_fractions=(0.5,)) -> dict:
    """THE ACTION COMPARISON, which is the deliverable.

    Every action considered, priced or refused by name, ranked by
    incremental value against HOLD. The selection rule is stated here
    once rather than implied by an ordering:

      * Only IDENTIFIED actions may be selected. NOT_IDENTIFIED is not
        zero and never wins by default.
      * HOLD is the incumbent. Another action is selected only if it is
        priced AND beats a priced HOLD. If HOLD itself is unpriced,
        nothing is selected and the reason is returned -- we do not
        act merely because the alternative happens to have a number.
    """
    cands = [value_hold(pos, mk),
             value_exit(pos, mk, fee_fn=fee_fn, fraction=1.0),
             value_complete_pair(pos, mk, fee_fn=fee_fn)]
    for f in reduce_fractions:
        cands.append(value_exit(pos, mk, fee_fn=fee_fn, fraction=f))
    # WAIT AND REPRICE ARE NAMED AND REFUSED rather than omitted. An
    # action missing from the table reads as an action nobody thought
    # of; an action present and refused reads as one we cannot value.
    cands.append(Candidate(
        action=WAIT, status=NOT_IDENTIFIED,
        blocker="FUTURE_PRICE_NOT_IDENTIFIED",
        why=("waiting is worth the difference between acting now and "
             "acting later, which is a claim about a future price. No "
             "validated price-path model exists"),
        uncertainty=("unbounded, and specifically NOT zero: pricing "
                     "WAIT at zero would make delay free and let the "
                     "engine defer every decision at no cost")))
    cands.append(Candidate(
        action=REPRICE, status=NOT_IDENTIFIED,
        blocker="P_FILL_NOT_IDENTIFIED",
        why=("repricing changes where our order rests, so its value is "
             "the change in the probability that OUR order fills. That "
             "quantity requires an execution-tape join no observational "
             "dataset here supplies"),
        requires_our_fill=True,
        uncertainty="the fill rate is unmeasured, not assumed"))

    hold = next(c for c in cands if c.action == HOLD)
    priced = [c for c in cands
              if c.status == IDENTIFIED and c.ev_usd is not None]
    selected, reason = HOLD, ""
    if hold.status != IDENTIFIED or hold.ev_usd is None:
        selected = None
        reason = ("no action selected: HOLD is %s (%s), and an "
                  "alternative cannot be preferred to a baseline that "
                  "has no value. Acting on the strength of the only "
                  "number available is how an unpriced incumbent loses "
                  "by default."
                  % (hold.status, hold.blocker))
    else:
        best = max(priced, key=lambda c: c.ev_usd)
        if best.ev_usd > hold.ev_usd:
            selected, reason = best.action, (
                "%s at %.4f beats HOLD at %.4f by %.4f"
                % (best.action, best.ev_usd, hold.ev_usd,
                   best.ev_usd - hold.ev_usd))
        else:
            reason = ("HOLD at %.4f is not beaten; best alternative %s at "
                      "%.4f" % (hold.ev_usd, best.action, best.ev_usd))
    return {
        "version": VERSION,
        "position": {"condition_id": pos.condition_id,
                     "outcome_index": pos.outcome_index,
                     "qty": pos.qty, "basis_usd": pos.basis_usd,
                     "avg_cost": pos.avg_cost},
        "candidates": [c.to_dict() for c in cands],
        "selected": selected,
        "selection_reason": reason,
        "unvalued_actions": [c.action for c in cands
                             if c.status != IDENTIFIED],
        "p_fill": NOT_IDENTIFIED,
        "p_fill_note": ("no action selected above requires one of OUR "
                        "orders to fill. EXIT_NOW and COMPLETE_PAIR are "
                        "taker actions against displayed depth; REPRICE "
                        "is refused precisely because it would need a "
                        "fill rate"),
    }
