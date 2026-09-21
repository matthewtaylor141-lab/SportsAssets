"""BETTOR's own decision. One market state in, one decision record out.

THE GAP THIS FILLS, established by inspection rather than asserted:

    bettor_fair_value          0 non-test callers, 0 in workers/api
    bettor_replay_harness      0 callers anywhere, not even a test
    bettor_preregistration     0 non-test callers
    bettor_rehearsal           imports 11 modules, is imported by none

and every terminal quantity resolves to NOT_IDENTIFIED when run:

    ev_bridge.evaluate({...})  -> bestAction NO_TRADE,
                                  actionEvStatus NOT_IDENTIFIED
    fair_value.fair_value()    -> FV_BETTOR_INDEPENDENT NOT_IDENTIFIED
    p_fill.p_fill()            -> P_FILL NOT_IDENTIFIED

What exists is a carefully built framework of REFUSALS -- a system that
knows what it may not claim. That is worth keeping and it is why nothing
bad has been traded. It is not a decision engine: no module takes a market
observation and returns an action, a size and a reason.

WHAT THIS MODULE DECIDES, AND ON WHAT AUTHORITY.

It does not invent an edge. Directional action is blocked because
P_BETTOR_INDEPENDENT_V3 measured the blend as WORSE than the venue price
by 0.00926 log loss on held-out events (95% event-clustered CI
[-0.00222, +0.02036], INCREMENTAL_SIGNAL_STATUS NOT_DETECTED). A decision
engine that opened directional positions anyway would be overriding its
own evidence.

So the engine ranks actions by INCREMENTAL NET CASH against a baseline,
and an action only scores when every term in its cash flow is identified:

  PAIR_BUY       buy YES and NO together. The pair pays exactly 1.00 at
                 settlement whatever happens, so
                     EV = 1.00 - yes_ask - no_ask - fees
                 contains NO forecast. IDENTIFIED whenever both asks are
                 readable and executable size is known.
  PAIR_SELL      the mirror, from held inventory of both legs.
  HOLD           the baseline for existing inventory. Zero incremental
                 cash by construction; it is the thing others are
                 measured against.
  NO_TRADE       the baseline from flat. Always feasible, always 0.
  TAKE_YES/NO    directional. BLOCKED by fair value, not merely unscored.
  MAKE_YES/NO    maker. Requires P_FILL, which is NOT_IDENTIFIED, so the
                 EV is NOT_IDENTIFIED -- and NOT_IDENTIFIED IS NOT ZERO.
                 An unscored action never wins by default.
  MERGE          requires a venue mechanism that has not been observed.
                 bettor_merge returns permitted=False for BOTH retail and
                 institutional, and "no merge mechanism has been observed"
                 is not "the venue lacks one". Refused as UNSUPPORTED.

ONE RECONCILED CASH-FLOW MODEL, WHICH IS HOW DOUBLE COUNTING IS AVOIDED.
Every action's value is the sum of dated cash flows it causes and nothing
else: cash out at execution, cash in at settlement, fees where they are
charged. Spread capture, fair-value gain, pairing benefit and markout are
NOT separate addends -- they are descriptions of the same cash. A pair
buy is not "spread capture plus arbitrage"; it is -yes_ask -no_ask -fees
now and +1.00 at settlement.

UNVERIFIED INCENTIVES ARE ZERO. No rebate, maker credit or promotional
payment enters a base-case EV until it has been verified against a
settled statement. `rebate_verified_per_contract` defaults to 0.0 and the
decision record names it as an assumption every time.

WHAT A DECISION RECORD MUST CONTAIN, because a decision nobody can audit
is not a decision: every alternative considered, its EV or the reason it
has none, the assumptions, the uncertainty, the selected action, the
size, and why. Emitted even when the answer is NO_TRADE -- especially
then, since that is the answer most of the time and an engine that only
records its trades cannot be evaluated.

NOTHING HERE SUBMITS AN ORDER. This module computes and returns. It holds
no adapter, no credentials and no pool. Execution is a separate layer
behind the execution gate, and the standing restrictions are unchanged:
mirror_live=false, no real orders, no capital authorization.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

from . import bettor_fair_value as fv
from . import bettor_merge as merge
from . import bettor_p_fill as pf
from . import bettor_venue_contract as vc

ENGINE_VERSION = "BETTOR_DECISION_ENGINE_V1"

NOT_IDENTIFIED = "NOT_IDENTIFIED"
BLOCKED = "BLOCKED"
UNSUPPORTED = "UNSUPPORTED"
IDENTIFIED = "IDENTIFIED"

# Actions, and the leg each touches.
NO_TRADE = "NO_TRADE"
HOLD = "HOLD"
PAIR_BUY = "PAIR_BUY"
PAIR_SELL = "PAIR_SELL"
TAKE_YES = "TAKE_YES"
TAKE_NO = "TAKE_NO"
MAKE_YES = "MAKE_YES"
MAKE_NO = "MAKE_NO"
SELL_YES = "SELL_YES"
SELL_NO = "SELL_NO"
MERGE = "MERGE"

BASELINE_FLAT = NO_TRADE
BASELINE_HELD = HOLD

# Settlement pays exactly this for one YES plus one NO on one market.
PAIR_SETTLEMENT_PAR = 1.0

# How stale a book may be before it stops being evidence about now. A
# decision taken on a book older than this is taken on history.
MAX_BOOK_AGE_S = 10.0


class Unsupported(Exception):
    """The venue does not support the action, as far as we have observed."""


@dataclass(frozen=True)
class Fees:
    """An EXPLICIT fee schedule. There is no default.

    REVIEW REPRODUCED THIS: omitting fees produced PAIR_BUY with
    data_quality "OK". `Fees()` with all-zero fields is not "no fees
    apply" -- it is FREE EXECUTION, the most optimistic assumption
    available, applied silently because nobody passed anything.

    So a schedule now has to declare itself verified. An unverified one
    is treated as an unknown cost, which blocks the action rather than
    pricing it at zero. `Fees.free_for_demonstration()` exists for the
    hypothetical arithmetic examples and is marked so it can never be
    mistaken for a live schedule.

    `rebate_verified_per_contract` stays separate and zero by default:
    an incentive not seen on a settled statement contributes nothing.
    PMUS rounds fees to the nearest cent with banker's rounding PER
    FILL, so a rebate is not linear in size and a small fill can round
    the whole incentive away.
    """
    taker_per_contract: float = 0.0
    maker_per_contract: float = 0.0
    settlement_per_contract: float = 0.0
    rebate_verified_per_contract: float = 0.0
    verified: bool = False
    hypothetical: bool = False
    source: str = "UNVERIFIED"

    @classmethod
    def free_for_demonstration(cls) -> "Fees":
        """Zero fees, labelled. For arithmetic demonstrations only."""
        return cls(verified=False, hypothetical=True,
                   source="HYPOTHETICAL_DEMONSTRATION_NOT_A_LIVE_SCHEDULE")

    def entry_cost(self, contracts: float, *, maker: bool) -> float:
        per = self.maker_per_contract if maker else self.taker_per_contract
        return contracts * (per - self.rebate_verified_per_contract)


@dataclass(frozen=True)
class Book:
    """One market's two-sided book at one instant, with its age.

    Depth is the executable size AT that price, not the total book. A
    price with no depth is not executable and the engine treats it as
    absent rather than as an opportunity of unknown size.
    """
    market_id: str
    yes_bid: float | None = None
    yes_ask: float | None = None
    no_bid: float | None = None
    no_ask: float | None = None
    yes_bid_size: float = 0.0
    yes_ask_size: float = 0.0
    no_bid_size: float = 0.0
    no_ask_size: float = 0.0
    age_s: float | None = None
    venue_state: str | None = None

    # HOW THE COMPLEMENT PRICE GOT HERE. OBSERVED means the sibling
    # instrument's own book was read. DERIVED means it was computed from
    # this book (1 - bid), in which case ask_yes + ask_no is 1 + spread
    # BY CONSTRUCTION and no pair is ever positive. ABSENT means there is
    # no complement price at all, which is the state of every captured
    # row today.
    complement_source: str = vc.ABSENT

    @property
    def fresh(self) -> bool:
        return (self.age_s is not None and 0.0 <= self.age_s
                <= MAX_BOOK_AGE_S)

    @property
    def tradeable(self) -> bool:
        return (self.venue_state or "").upper() in ("OPEN", "ACTIVE")

    def unreadable_reason(self) -> str | None:
        """Every way this book fails to be usable evidence about now.

        REVIEW REPRODUCED THREE HOLES HERE, all of which returned
        data_quality "OK": age_s=-20 (a book from the future), a price
        of -0.1, and an unverified venue. The checks below are the
        repair, and they are deliberately exhaustive rather than
        minimal -- a validator that only rejects what somebody thought
        of is the shape of the original defect.
        """
        if self.age_s is None:
            return "book age is unknown, so the book cannot be dated"
        if not math.isfinite(self.age_s):
            return "book age is not finite"
        if self.age_s < 0:
            return ("book age is %.1fs, which is in the future; a negative "
                    "age is a clock fault, not a fresh book" % self.age_s)
        if self.age_s > MAX_BOOK_AGE_S:
            return ("book is %.1fs old, past the %.0fs bound"
                    % (self.age_s, MAX_BOOK_AGE_S))
        # VENUE STATE MUST BE STATED. It used to default to OPEN when
        # absent, so a row that never said whether the market was
        # tradeable was treated as tradeable.
        if not self.venue_state:
            return ("venue_state is absent; an unstated market state is "
                    "not an open one")
        if not self.tradeable:
            return "venue_state is %r" % (self.venue_state,)
        for name in ("yes_bid", "yes_ask", "no_bid", "no_ask"):
            bad = _bad_price(getattr(self, name), name)
            if bad:
                return bad
        for name in ("yes_bid_size", "yes_ask_size", "no_bid_size",
                     "no_ask_size"):
            bad = _bad_size(getattr(self, name), name)
            if bad:
                return bad
        return None


@dataclass(frozen=True)
class Inventory:
    """What BETTOR already holds on this market, and what it cost.

    Basis is recorded because HOLD's alternative is a sale at the bid,
    and the difference between them is a realised cash flow. It is NOT
    used to justify holding a loser: sunk cost does not enter any EV
    here, and `basis` appears only in the accounting, never in a ranking.
    """
    yes_contracts: float = 0.0
    no_contracts: float = 0.0
    yes_basis_per_contract: float | None = None
    no_basis_per_contract: float | None = None

    @property
    def flat(self) -> bool:
        return self.yes_contracts == 0 and self.no_contracts == 0

    @property
    def paired_contracts(self) -> float:
        return min(self.yes_contracts, self.no_contracts)


@dataclass
class Candidate:
    """One action considered, scored or refused. Never silently dropped."""
    action: str
    status: str                       # IDENTIFIED / NOT_IDENTIFIED / BLOCKED / UNSUPPORTED
    ev_net: float | None = None       # incremental net cash vs the baseline
    size_contracts: float = 0.0
    cash_now: float | None = None     # negative = paid out
    cash_at_settlement: float | None = None
    # CONDITIONAL PAYOFF IS NOT EXECUTABLE EV. Kept as its own field so
    # a reader cannot mistake one for the other, and so a candidate can
    # report "this is what it pays IF it completes" while still scoring
    # NOT_IDENTIFIED overall.
    conditional_payoff: float | None = None
    leg_risk_cost: float | None = None
    why: str = ""
    blocker: str | None = None
    assumptions: list[str] = field(default_factory=list)
    uncertainty: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _bad_price(v, name: str) -> str | None:
    """A price must be absent, or a real probability. Nothing else.

    A contract that settles at 0 or 1 cannot trade at -0.1 or at 2.5,
    and both of those were accepted. 0.0 and 1.0 are excluded too: a
    quote at a settlement bound is either a stale artefact or a market
    that has already resolved, and in neither case is it executable.
    """
    if v is None:
        return None
    x = _f(v)
    if x is None:
        return None                      # unparsable: handled per action
    if not math.isfinite(x):
        return "%s is not finite" % name
    if x <= 0.0 or x >= 1.0:
        return ("%s is %.4f, outside (0, 1); a contract settling at 0 or 1 "
                "cannot trade there" % (name, x))
    return None


def _bad_size(v, name: str) -> str | None:
    """Depth must be a finite, non-negative number of contracts."""
    x = _f(v)
    if x is None:
        return "%s is not a number" % name
    if not math.isfinite(x):
        return "%s is not finite; infinite depth is not a quantity" % name
    if x < 0:
        return "%s is %.4g, and negative depth is not a quantity" % (name, x)
    return None


def _f(v) -> float | None:
    """A quote, or None. '' and 'None' and nonsense are None, not zero.

    A price that failed to parse is not a price of nothing; treating it
    as 0.0 would make every unreadable book look like free money.
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


# ── the action evaluators ────────────────────────────────────────────

def _eval_no_trade() -> Candidate:
    return Candidate(
        action=NO_TRADE, status=IDENTIFIED, ev_net=0.0,
        cash_now=0.0, cash_at_settlement=0.0,
        why="the baseline from flat: no cash moves",
        uncertainty="none; this is a definition, not an estimate")


def _eval_hold(inv: Inventory) -> Candidate:
    return Candidate(
        action=HOLD, status=IDENTIFIED, ev_net=0.0,
        size_contracts=inv.yes_contracts + inv.no_contracts,
        cash_now=0.0, cash_at_settlement=None,
        why=("the baseline for existing inventory: no cash moves now, and "
             "settlement value is whatever the position is already worth"),
        assumptions=["basis is excluded; sunk cost does not enter any EV"],
        uncertainty="none as a baseline; it carries the position's own risk")


def _eval_pair_buy(book: Book, fees: Fees, max_contracts: float,
                   caps) -> Candidate:
    """A COMPLETED pair's conditional payoff is 1.00. That is not its EV.

    THE CORRECTION. I wrote `EV = 1 - yes_ask - no_ask - fees` and called
    it forecast-free. It is the payoff of a pair that ALREADY EXISTS,
    conditional on both legs having filled and on the account being able
    to hold both independently to settlement. None of those conditions
    was checked, and listing partial-fill risk in the explanation text
    priced nothing.

    Four gates now sit in front of it, in order of how badly each one
    bites:

    1. PROVENANCE. If the complement ask is DERIVED as 1 - yes_bid then
       yes_ask + no_ask == 1 + spread identically, and there is no fee
       schedule or size at which that is positive. Class C measured it:
       3,732 observations, 0 at or below par, min basis 1.0050. A
       derived complement is arithmetic, not an opportunity.
    2. CAPABILITY. If the account cannot hold both legs independently,
       buying the complement NETS or CLOSES rather than acquiring, and
       the par-settlement model does not describe the cash flow at all.
       UNKNOWN blocks; it does not permit.
    3. FEE SCHEDULE. An unverified schedule is an unknown cost, not a
       zero one.
    4. EXECUTION. Two legs, so two fills, and the failure mode is a
       naked directional position the engine is not permitted to hold
       deliberately. Priced below, not narrated.
    """
    # 1. The identity check comes first because it is unconditional.
    if vc.pair_is_an_identity(book.complement_source):
        return Candidate(
            action=PAIR_BUY, status=BLOCKED,
            blocker="COMPLEMENT_IS_DERIVED_NOT_OBSERVED",
            why=("the complement ask is derived from this book, so "
                 "yes_ask + no_ask = 1 + spread by construction. Class C "
                 "measured 3,732 such pairs: 0 at or below par, minimum "
                 "basis 1.0050"),
            assumptions=["a derived complement cannot disagree with par"],
            uncertainty="none; this is an identity, not an estimate")
    if book.complement_source != vc.OBSERVED:
        return Candidate(
            action=PAIR_BUY, status=NOT_IDENTIFIED,
            blocker="COMPLEMENT_%s" % book.complement_source,
            why=("no independently observed complement book; the sibling "
                 "instrument was not read this cycle"),
            uncertainty="the complement price is unknown, not absent")

    # 2. Can this ACCOUNT even hold the resulting position?
    blk = caps.blocker_for("holds_both_legs_independently")
    if blk:
        return Candidate(
            action=PAIR_BUY, status=BLOCKED, blocker=blk,
            why=("it is not established that this account holds both legs "
                 "independently. If buying the complement nets against the "
                 "existing leg the cash flow is a CLOSE, not an "
                 "acquisition, and the par model does not apply"),
            assumptions=["a reference account's token-pair mechanics are "
                         "not inherited by the institutional account"],
            uncertainty="unknown capability, which blocks rather than permits")

    # 3. An unverified fee schedule is an unknown cost.
    if not fees.verified:
        return Candidate(
            action=PAIR_BUY, status=NOT_IDENTIFIED,
            blocker="FEE_SCHEDULE_UNVERIFIED",
            why=("fees are %s; an unverified schedule is an unknown cost "
                 "and must not be priced at zero" % fees.source),
            uncertainty="execution cost unknown, so net EV is unknown")

    ya, na = _f(book.yes_ask), _f(book.no_ask)
    if ya is None or na is None:
        return Candidate(
            action=PAIR_BUY, status=NOT_IDENTIFIED,
            blocker="ASK_UNREADABLE",
            why="one or both asks did not parse, so no cash flow is known",
            uncertainty="unbounded: the price is unknown, not zero")

    size = min(book.yes_ask_size, book.no_ask_size, max_contracts)
    if size <= 0:
        return Candidate(
            action=PAIR_BUY, status=NOT_IDENTIFIED,
            blocker="NO_EXECUTABLE_DEPTH",
            why=("a price with no depth behind it is not executable; "
                 "yes_ask_size=%.4g no_ask_size=%.4g"
                 % (book.yes_ask_size, book.no_ask_size)),
            uncertainty="a quote without size is not an opportunity")

    cash_now = -(ya + na) * size - fees.entry_cost(2 * size, maker=False)
    cash_settle = (PAIR_SETTLEMENT_PAR * size
                   - fees.settlement_per_contract * size)
    conditional_payoff = cash_now + cash_settle

    # EXECUTABLE EV IS NOT THE CONDITIONAL PAYOFF. Two legs means two
    # fills. The bad branch is not "we get nothing" -- it is a NAKED
    # DIRECTIONAL POSITION on a venue where directional action is
    # blocked, which must then be unwound across the spread.
    #
    #   p_both    * conditional_payoff
    # + p_one_leg * (-unwind_cost)
    #
    # p_both is a conditional fill probability and P_FILL is
    # NOT_IDENTIFIED, so the product is NOT_IDENTIFIED too. Naming the
    # decomposition is worth doing; pretending the first term is the
    # answer is not.
    spread_y = (ya - _f(book.yes_bid)) if _f(book.yes_bid) is not None else None
    unwind = (spread_y * size) if spread_y is not None else None
    return Candidate(
        action=PAIR_BUY,
        status=NOT_IDENTIFIED,
        blocker="P_BOTH_LEGS_FILL_NOT_IDENTIFIED",
        ev_net=None,
        conditional_payoff=conditional_payoff,
        leg_risk_cost=unwind,
        size_contracts=size,
        cash_now=cash_now,
        cash_at_settlement=cash_settle,
        why=("CONDITIONAL payoff %.4f if BOTH legs fill. Executable EV is "
             "p_both x %.4f - p_one_leg x %s, and p_both is not identified, "
             "so the product is not either"
             % (conditional_payoff, conditional_payoff,
                ("%.4f" % unwind) if unwind is not None else "unwind_cost")),
        assumptions=[
            "the venue settles YES+NO at exactly %.2f" % PAIR_SETTLEMENT_PAR,
            "verified rebate per contract = %.4f (unverified incentives are 0)"
            % fees.rebate_verified_per_contract,
        ],
        uncertainty=("a one-leg fill leaves a naked directional position on "
                     "a venue where directional action is blocked; unwinding "
                     "it costs the spread and that cost is real whether or "
                     "not it is estimated"))


def _eval_pair_sell(book: Book, inv: Inventory, fees: Fees) -> Candidate:
    """Sell a held pair back. Also forecast-free: it unwinds par."""
    yb, nb = _f(book.yes_bid), _f(book.no_bid)
    paired = inv.paired_contracts
    if paired <= 0:
        return Candidate(
            action=PAIR_SELL, status=UNSUPPORTED,
            blocker="NO_PAIRED_INVENTORY",
            why="there is no paired inventory to sell")
    if yb is None or nb is None:
        return Candidate(
            action=PAIR_SELL, status=NOT_IDENTIFIED, blocker="BID_UNREADABLE",
            why="one or both bids did not parse")

    size = min(paired, book.yes_bid_size, book.no_bid_size)
    if size <= 0:
        return Candidate(
            action=PAIR_SELL, status=NOT_IDENTIFIED,
            blocker="NO_EXECUTABLE_DEPTH",
            why="no depth at the bid on one or both legs")

    cash_now = (yb + nb) * size - fees.entry_cost(2 * size, maker=False)
    forgone = PAIR_SETTLEMENT_PAR * size - fees.settlement_per_contract * size
    ev = cash_now - forgone
    return Candidate(
        action=PAIR_SELL, status=IDENTIFIED, ev_net=ev, size_contracts=size,
        cash_now=cash_now, cash_at_settlement=-forgone,
        why=("takes %.4f now and gives up %.4f at settlement; positive only "
             "if the bids sum above par net of fees" % (cash_now, forgone)),
        assumptions=["selling forgoes the par settlement this pair would pay"],
        uncertainty="execution risk on both legs")


def _eval_directional(action: str) -> Candidate:
    """TAKE_YES / TAKE_NO / SELL_YES / SELL_NO.

    BLOCKED, and the distinction from NOT_IDENTIFIED matters. This is not
    a quantity we failed to measure. It is a quantity we measured, whose
    answer was that the challenger made the held-out forecast WORSE.
    """
    d = fv.directional_permitted()
    return Candidate(
        action=action, status=BLOCKED,
        blocker=d.get("blocker", "FV_BETTOR_INDEPENDENT_NOT_VALIDATED"),
        why=("a directional action is a bet that the venue price is wrong. "
             "P_BETTOR_INDEPENDENT_V3 measured the blend as WORSE than the "
             "market by 0.00926 log loss on held-out events, so the engine "
             "would be overriding its own evidence"),
        assumptions=["the venue midpoint is a price, never a BETTOR belief"],
        uncertainty=("NOT_DETECTED_AT_THIS_SAMPLE_SIZE is not a refutation; "
                     "this may unblock with more data and must not be read "
                     "as proof that no alpha exists"))


def _eval_maker(action: str, book: Book) -> Candidate:
    """MAKE_YES / MAKE_NO. Unscored, and unscored never wins.

    Maker EV is (edge captured) x P(fill) - (adverse selection) x P(fill).
    P_FILL is NOT_IDENTIFIED -- bettor_p_fill says so and names what would
    identify it -- so the product is NOT_IDENTIFIED. The engine records
    the action, the reason it cannot be scored, and refuses to let a
    missing number act as a zero.
    """
    status = pf.p_fill()
    return Candidate(
        action=action, status=NOT_IDENTIFIED,
        blocker="P_FILL_NOT_IDENTIFIED",
        why=("maker EV is a conditional expectation and its condition is "
             "unmeasured; bettor_p_fill reports %s"
             % status.get("P_FILL", NOT_IDENTIFIED)),
        assumptions=["NOT_IDENTIFIED is not zero: an unscored action cannot "
                     "win a ranking by default"],
        uncertainty=("the prize is bounded by the spread, which is measured; "
                     "the probability of collecting it is not"))


def _eval_merge(venue: str) -> Candidate:
    """MERGE. Refused because the mechanism has not been observed.

    bettor_merge returns permitted=False for BOTH retail and
    institutional, and is explicit that silence is not evidence: not
    having seen a merge mechanism is not the same as the venue lacking
    one. Either way the engine may not plan around a mechanism it cannot
    demonstrate -- and it must not assume the institutional account has
    what a reference retail account might.
    """
    m = merge.merge_permitted(venue)
    return Candidate(
        action=MERGE, status=UNSUPPORTED,
        blocker="MERGE_NOT_OBSERVED_ON_%s" % venue.upper(),
        why=str(m.get("why", "merge is not available"))[:240],
        assumptions=["a mechanism available to a reference account is not "
                     "thereby available to the institutional account"],
        uncertainty="absence of observation, not observation of absence")


# ── the decision ─────────────────────────────────────────────────────

def decide(book: Book, *, inventory: Inventory | None = None,
           fees: Fees | None = None, max_contracts: float = 0.0,
           venue: str = "polymarket-us",
           account_class: str = "institutional",
           min_ev_to_act: float = 0.0) -> dict:
    """Rank every feasible action and select one. Never raises.

    Returns a decision record: the selected action and size, every
    alternative with its EV or the reason it has none, the assumptions
    behind the winner, and the uncertainty that survives. NO_TRADE is a
    decision and is recorded like any other.

    `min_ev_to_act` is the threshold above the baseline an action must
    clear. Zero is not a safe default in production -- it acts on any
    positive number however small relative to its own uncertainty -- so
    callers are expected to set it from measured execution variance.
    """
    inv = inventory or Inventory()
    # AN OMITTED FEE SCHEDULE IS NOT A FREE ONE. Fees() with all-zero
    # fields and verified=False is an UNKNOWN cost, and every action
    # that needs a cost refuses on it.
    fee = fees if fees is not None else Fees(source="OMITTED_BY_CALLER")
    caps = vc.capabilities(venue, account_class)
    baseline = BASELINE_HELD if not inv.flat else BASELINE_FLAT

    record: dict = {
        "engine": ENGINE_VERSION,
        "market_id": book.market_id,
        "baseline": baseline,
        "book_age_s": book.age_s,
        "inventory": {"yes": inv.yes_contracts, "no": inv.no_contracts,
                      "paired": inv.paired_contracts},
        "venue": venue,
        "account_class": account_class,
        "complement_source": book.complement_source,
        "fee_schedule": {"verified": fee.verified, "source": fee.source,
                         "hypothetical": fee.hypothetical},
    }

    # A book we cannot date or trade produces no action at all, and the
    # refusal is recorded with its reason rather than returned as an
    # empty result that a caller might read as "nothing to do".
    bad = book.unreadable_reason()
    if bad is not None:
        cands = [_eval_no_trade()]
        if not inv.flat:
            cands.append(_eval_hold(inv))
        record.update({
            "selected": baseline,
            "size_contracts": 0.0,
            "reason": "no action is evaluable: %s" % bad,
            "data_quality": "REJECTED",
            "candidates": [c.to_dict() for c in cands],
            "assumptions": [],
            "uncertainty": "the book is not usable evidence about now",
        })
        return record

    # EVERY ACTION IS EVALUATED AND RECORDED, including the ones that are
    # not feasible from this state. An action that silently never appears
    # in the record cannot be distinguished by a reader from one that was
    # considered and refused -- and "we never even looked at selling" is a
    # different failure from "selling was worse".
    cands: list[Candidate] = [_eval_no_trade()]
    if not inv.flat:
        cands.append(_eval_hold(inv))
    cands.append(_eval_pair_sell(book, inv, fee))
    cands.append(_eval_directional(SELL_YES))
    cands.append(_eval_directional(SELL_NO))
    cands.append(_eval_pair_buy(book, fee, max_contracts, caps))
    cands.append(_eval_directional(TAKE_YES))
    cands.append(_eval_directional(TAKE_NO))
    cands.append(_eval_maker(MAKE_YES, book))
    cands.append(_eval_maker(MAKE_NO, book))
    cands.append(_eval_merge(account_class))

    # SELECTION. Only an IDENTIFIED candidate may win, and it must beat
    # the baseline by more than the threshold. A NOT_IDENTIFIED action is
    # not a zero and never wins by being the only thing left.
    scored = [c for c in cands
              if c.status == IDENTIFIED and c.ev_net is not None
              and c.action not in (NO_TRADE, HOLD)]
    best = max(scored, key=lambda c: c.ev_net, default=None)

    if best is not None and best.ev_net > min_ev_to_act:
        chosen, size = best.action, best.size_contracts
        reason = ("%s: incremental net %.6f over the %s baseline, above the "
                  "%.6f threshold. %s"
                  % (best.action, best.ev_net, baseline, min_ev_to_act,
                     best.why))
        assumptions = list(best.assumptions)
        uncertainty = best.uncertainty
    else:
        chosen, size = baseline, 0.0
        if best is None:
            reason = ("no action has an identified EV. Blockers: %s"
                      % ", ".join(sorted({c.blocker for c in cands
                                          if c.blocker})) or "none recorded")
        else:
            reason = ("best identified action %s scores %.6f, at or below "
                      "the %.6f threshold" % (best.action, best.ev_net,
                                              min_ev_to_act))
        assumptions = ["an action with no identified EV does not become "
                       "attractive by being unmeasured"]
        uncertainty = ("the unscored actions may or may not be positive; "
                       "that is exactly why they were not selected")

    record.update({
        "selected": chosen,
        "size_contracts": size,
        "reason": reason,
        "data_quality": "OK",
        "candidates": [c.to_dict() for c in cands],
        "assumptions": assumptions,
        "uncertainty": uncertainty,
        "fair_value_basis": fv.FV_VENUE_IMPLIED,
        "directional_permitted": fv.directional_permitted().get("permitted"),
    })
    return record


def describe() -> dict:
    """What this engine can and cannot decide, for the operator view."""
    return {
        "engine": ENGINE_VERSION,
        "decides": [PAIR_BUY, PAIR_SELL, HOLD, NO_TRADE],
        "blocked": {
            TAKE_YES: "FV_BETTOR_INDEPENDENT_NOT_VALIDATED",
            TAKE_NO: "FV_BETTOR_INDEPENDENT_NOT_VALIDATED",
            SELL_YES: "FV_BETTOR_INDEPENDENT_NOT_VALIDATED",
            SELL_NO: "FV_BETTOR_INDEPENDENT_NOT_VALIDATED",
        },
        "unscored": {MAKE_YES: "P_FILL_NOT_IDENTIFIED",
                     MAKE_NO: "P_FILL_NOT_IDENTIFIED"},
        "unsupported": {MERGE: "no merge mechanism has been observed"},
        "baseline": {"flat": BASELINE_FLAT, "held": BASELINE_HELD},
        "cash_flow_model": ("one reconciled model: dated cash flows only. "
                            "Spread capture, fair-value gain and pairing "
                            "benefit are descriptions of the same cash, not "
                            "separate addends."),
        "unverified_incentives": "zero in the base case",
        "submits_orders": False,
    }
