"""ADRIANA: CROSS-VENUE ARBITRAGE PROVED FROM PAYOFFS -- SHADOW ONLY, NO AUTHORITY.

Adriana answers one question and refuses every other: "if we bought these
contracts, at the asks actually on the books, in the sizes the books can
actually fill, after every fee and buffer we can name, is the worst outcome
of the event still a profit?" When the answer is provably yes the verdict is
GUARANTEED_AFTER_COSTS. When anything needed for that proof is missing the
verdict is REFUSED and the missing thing is named with a refusal code. There
is no third verdict, and the word "guaranteed" appears in no other verdict.

────────────────────────────────────────────────────────────────────
WHAT THIS MODULE CANNOT DO, BY CONSTRUCTION.

It never places, amends or withdraws an order; never reads a credential or
the process environment; never opens a network connection; never writes a
database row; never reserves or moves capital. It imports the standard
library and one pure module (`bettor_fee_schedule`, the dated Polymarket US
fee schedule) and nothing else. `AUTHORITY` declares that, a test walks this
file's imports by AST and its text by regex to hold it to the declaration,
and `assert_no_authority()` fails loudly if the declaration ever drifts.

The leg-risk state machine at the bottom models what WOULD happen to a
two-leg structure in SHADOW. Its "events" are inputs a caller supplies; it
emits nothing.

────────────────────────────────────────────────────────────────────
WHY PRICES ARE COMPARED LAST.

The obvious arbitrage scanner reads "Lakers win" at 0.45 on one venue and
"Celtics win" at 0.45 on another, adds them to 0.90 and reports ten cents.
That scanner is wrong for a reason that is not about prices: the two titles
do not carry the facts that decide whether the contracts are complements.
They are complements only if, in EVERY state of the world the event can
reach, their payouts sum to exactly one dollar. The states that break this
are precisely the ones a title never mentions:

    VOID / CANCELLED   one venue settles 50/50, the other at last price,
                       or refunds -- the pair pays 0.50 + 0.00
    POSTPONED          one venue's settle window expires and it voids,
                       the other waits for the rescheduled game
    TIE / PUSH         one contract has no tie outcome (it resolves NO),
                       the other refunds -- both NO legs win, both YES lose
    SOURCE             two venues read two different official sources and
                       a stat correction lands between them

So a contract here is not a price on a title. It is an explicit PAYOFF MAP
(outcome -> dollars per contract, in [0, 1]) over an explicitly ENUMERATED
outcome space that must contain VOID and POSTPONED, and must contain the tie
/ push outcomes whenever the contract's tie rule says a tie is possible. A
structure is evaluated by summing its legs' payoff maps outcome by outcome.
The guaranteed payout per matched set is the MINIMUM of that sum over every
outcome, the non-standard ones included. Prices are not looked at until the
outcome space is proved exhaustive, every leg's settlement terms agree, and
that minimum is known.

────────────────────────────────────────────────────────────────────
STRUCTURE KINDS, READ OFF THE ARITHMETIC, NEVER OFF A NAME.

    COMPLEMENT    two legs whose payoffs sum to exactly 1 in every outcome
    YES_BASKET    one YES per outcome of an exhaustive, mutually exclusive
                  set; pays exactly 1 in every outcome
    NO_BASKET     one NO per outcome of such a set of N; pays exactly N-1
    MIDDLE_FLOOR  anything else: the payout vector is not constant, and the
                  guarantee is its floor. "Long OVER 210.5 + long UNDER 211.5"
                  pays 1 / 2 / 1 over the buckets (<=210, 211, >=212), so its
                  floor is 1 and the middle bucket is upside, not edge.

A MIDDLE_FLOOR whose floor does not even cover the raw asks is refused as
PAYOFF_FLOOR_BELOW_COST. "Cannot lose both legs" is not a profit: a middle
bought at $1.17 against a $1.00 floor is a bet on the middle, not a hedge,
and this module will never call it anything else.

────────────────────────────────────────────────────────────────────
FEES ARE PER ORDER, AND AN UNKNOWN FEE IS NOT A ZERO FEE.

KALSHI        ceil_to_cent(0.07 x C x p x (1 - p)) for ONE order. The rounding
              is applied to the order, not to each contract: four contracts at
              0.50 cost $0.07, not 4 x $0.02. Implemented locally and pinned by
              test against `kalshi_orders.fee_for`, which this module may not
              import (its own imports reach the order path).
POLYMARKET_US the dated published schedule from `bettor_fee_schedule`, read
              with `for_date` -- never `LATEST`. Under the CUMULATIVE_PER_ORDER
              terms the venue caps an order's total at the banker's rounding
              of its cumulative exact fee, which is what is charged here. On a
              schedule-change day the date of a UTC instant and the venue's
              ET effective instant can disagree, so the LARGER of the two
              candidate days' fees is charged. A sport that carries its own
              coefficient (PMUS_SPORTS_WITH_OWN_THETA) is refused, because the
              exchange-wide schedule would understate it; so is a leg that
              does not declare its sport at all.
POLYMARKET    (international) and every other venue: FEE_SCHEDULE_UNKNOWN.

When one order sweeps several price levels the fee is modelled as one fill
per level. For Kalshi that per-level ceiling is never below the ceiling of the
order total, so it is an upper bound. On top of fees every leg carries an
explicit slippage buffer (default one cent per contract) and an optional fixed
per-order cost.

────────────────────────────────────────────────────────────────────
SIZE IS SOLVED, NOT ASSUMED.

Total net profit at Q matched sets is floor x Q - cost(Q), where cost(Q) walks
every leg's ask ladder level by level and charges each leg's fee on the WHOLE
order of Q at that venue. Because fees round per order the function is not
linear and its optimum need not sit at the edge of the book: a deeper level
can be unprofitable at the margin, and a size of one can be unprofitable
where a size of a hundred is not. Every integer Q up to the executable depth
is evaluated and the best is reported, with the marginal profit of the next
contract alongside it so the reader can see why the size stopped where it did.

────────────────────────────────────────────────────────────────────
TIME IS AN INPUT.

Every decision takes `now` as an argument and no decision code reads a clock.
Each book must be no older than `max_age_s` (default 30 s) relative to `now`,
and every pair of books in a structure must have been observed within
`max_skew_s` (default 5 s) of each other: a "cross" between a fresh book and
a 25-second-old one is usually the older book's ghost. Exactly at either
limit passes; one microsecond past it does not.

────────────────────────────────────────────────────────────────────
A NO ASK IS NOT A YES BID.

On a venue that lists a single YES-denominated book, "buy NO at 1 - best YES
bid" is the same instrument. On a venue that lists YES and NO as separate
books it is not, and a NO ask synthesised from a YES bid is a price nobody
is offering. `may_synthesize_no_ask` decides, and it defaults to NOT
PERMITTED for every venue and contract: the caller must declare the specific
(venue, market_id) as a single-instrument book.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from itertools import combinations
from typing import Any, Iterable, Mapping, Sequence

from .. import bettor_fee_schedule as FS

VERSION = "ADRIANA_ARB_ENGINE_V1"
MODE = "SHADOW"

#: What this module may do. Every power is False and the mode is SHADOW.
#: `assert_no_authority()` refuses to return if any of that changes.
AUTHORITY = {
    "submit": False,
    "cancel": False,
    "credentials": False,
    "capital": False,
    "mode": MODE,
}


def assert_no_authority() -> bool:
    """Raise if AUTHORITY grants anything; return True otherwise."""
    powers = {k: v for k, v in AUTHORITY.items() if k != "mode"}
    if any(v is not False for v in powers.values()):
        raise AssertionError("adriana_arb grants authority: %r" % (AUTHORITY,))
    if AUTHORITY.get("mode") != "SHADOW":
        raise AssertionError("adriana_arb is SHADOW only, got %r"
                             % (AUTHORITY.get("mode"),))
    if len(powers) != 4:
        raise AssertionError("AUTHORITY keys drifted: %r" % (sorted(AUTHORITY),))
    return True


# ═════════════════════════════════════════════════════════════════════
# VOCABULARY
# ═════════════════════════════════════════════════════════════════════

# ── venues ───────────────────────────────────────────────────────────
POLYMARKET_US = "POLYMARKET_US"
KALSHI = "KALSHI"
POLYMARKET = "POLYMARKET"          # international; no fee schedule known here
VENUES = (POLYMARKET_US, KALSHI, POLYMARKET)

YES, NO = "YES", "NO"
SIDES = (YES, NO)

# ── market families ──────────────────────────────────────────────────
MONEYLINE, SPREAD, TOTAL, FUTURE, PROP = (
    "MONEYLINE", "SPREAD", "TOTAL", "FUTURE", "PROP")
FAMILIES = (MONEYLINE, SPREAD, TOTAL, FUTURE, PROP)

# ── non-standard outcomes, enumerated, never assumed ─────────────────
OUTCOME_VOID = "VOID"              # cancelled / abandoned
OUTCOME_POSTPONED = "POSTPONED"    # not played inside the settle window
OUTCOME_TIE = "TIE"
OUTCOME_PUSH = "PUSH"
REQUIRED_NONSTANDARD = (OUTCOME_VOID, OUTCOME_POSTPONED)

#: The one tie rule that says no tie / push outcome needs to be enumerated.
TIE_IMPOSSIBLE = "TIE_IMPOSSIBLE"
#: Tie rules that are not rules. A contract carrying one is refused.
UNKNOWN_TIE_RULES = ("", "UNKNOWN", "TIE_RULE_UNKNOWN")

# ── structure kinds ──────────────────────────────────────────────────
COMPLEMENT = "COMPLEMENT"
MIDDLE_FLOOR = "MIDDLE_FLOOR"
YES_BASKET = "YES_BASKET"
NO_BASKET = "NO_BASKET"
STRUCTURE_KINDS = (COMPLEMENT, MIDDLE_FLOOR, YES_BASKET, NO_BASKET)

# ── verdicts. Exactly two. ───────────────────────────────────────────
GUARANTEED_AFTER_COSTS = "GUARANTEED_AFTER_COSTS"
REFUSED = "REFUSED"
VERDICTS = (GUARANTEED_AFTER_COSTS, REFUSED)

# ── refusal codes ────────────────────────────────────────────────────
PARAMETER_INVALID = "PARAMETER_INVALID"
CLOCK_INVALID = "CLOCK_INVALID"
CONTRACT_SPEC_INCOMPLETE = "CONTRACT_SPEC_INCOMPLETE"
SIDE_INVALID = "SIDE_INVALID"
NONSTANDARD_OUTCOMES_MISSING = "NONSTANDARD_OUTCOMES_MISSING"
DUPLICATE_OUTCOME = "DUPLICATE_OUTCOME"
OUTCOME_SPACE_NOT_PROVEN_EXHAUSTIVE = "OUTCOME_SPACE_NOT_PROVEN_EXHAUSTIVE"
OUTCOME_SPACE_DIFFERS = "OUTCOME_SPACE_DIFFERS"
OUTCOME_NOT_COVERED = "OUTCOME_NOT_COVERED"
PAYOFF_INCOMPLETE = "PAYOFF_INCOMPLETE"
PAYOFF_INVALID = "PAYOFF_INVALID"
PAYOFF_NOT_INDICATOR = "PAYOFF_NOT_INDICATOR"
PAYOFF_NOT_COMPLEMENTARY = "PAYOFF_NOT_COMPLEMENTARY"
BASKET_PAYOFF_NOT_CONSTANT = "BASKET_PAYOFF_NOT_CONSTANT"
PAYOFF_FLOOR_BELOW_COST = "PAYOFF_FLOOR_BELOW_COST"
EVENT_IDENTITY_UNPROVEN = "EVENT_IDENTITY_UNPROVEN"
MARKET_DEFINITION_DIFFERS = "MARKET_DEFINITION_DIFFERS"
SETTLEMENT_SOURCE_DIFFERS = "SETTLEMENT_SOURCE_DIFFERS"
SETTLE_WINDOW_INVALID = "SETTLE_WINDOW_INVALID"
SETTLE_WINDOW_DIFFERS = "SETTLE_WINDOW_DIFFERS"
VOID_RULE_DIFFERS = "VOID_RULE_DIFFERS"
TIE_RULE_DIFFERS = "TIE_RULE_DIFFERS"
TIE_RULE_UNKNOWN = "TIE_RULE_UNKNOWN"
LEG_ALTERNATIVES_NOT_EQUIVALENT = "LEG_ALTERNATIVES_NOT_EQUIVALENT"
DUPLICATE_LEG = "DUPLICATE_LEG"
BOOK_MISSING = "BOOK_MISSING"
BOOK_CONTRACT_MISMATCH = "BOOK_CONTRACT_MISMATCH"
BOOK_INVALID = "BOOK_INVALID"
BOOK_TIME_MISSING = "BOOK_TIME_MISSING"
BOOK_TIME_IN_FUTURE = "BOOK_TIME_IN_FUTURE"
STALE_BOOK = "STALE_BOOK"
UNSYNCHRONIZED_BOOKS = "UNSYNCHRONIZED_BOOKS"
FEE_SCHEDULE_UNKNOWN = "FEE_SCHEDULE_UNKNOWN"
NO_EXECUTABLE_DEPTH = "NO_EXECUTABLE_DEPTH"
NOT_PROFITABLE_AFTER_COSTS = "NOT_PROFITABLE_AFTER_COSTS"
NO_SIDE_NOT_LISTED = "NO_SIDE_NOT_LISTED"
INVALID_TRANSITION = "INVALID_TRANSITION"
OVERFILL = "OVERFILL"

REFUSAL_CODES = (
    PARAMETER_INVALID, CLOCK_INVALID, CONTRACT_SPEC_INCOMPLETE, SIDE_INVALID,
    NONSTANDARD_OUTCOMES_MISSING, DUPLICATE_OUTCOME,
    OUTCOME_SPACE_NOT_PROVEN_EXHAUSTIVE, OUTCOME_SPACE_DIFFERS,
    OUTCOME_NOT_COVERED, PAYOFF_INCOMPLETE, PAYOFF_INVALID,
    PAYOFF_NOT_INDICATOR, PAYOFF_NOT_COMPLEMENTARY, BASKET_PAYOFF_NOT_CONSTANT,
    PAYOFF_FLOOR_BELOW_COST, EVENT_IDENTITY_UNPROVEN, MARKET_DEFINITION_DIFFERS,
    SETTLEMENT_SOURCE_DIFFERS, SETTLE_WINDOW_INVALID, SETTLE_WINDOW_DIFFERS,
    VOID_RULE_DIFFERS, TIE_RULE_DIFFERS, TIE_RULE_UNKNOWN,
    LEG_ALTERNATIVES_NOT_EQUIVALENT, DUPLICATE_LEG, BOOK_MISSING,
    BOOK_CONTRACT_MISMATCH, BOOK_INVALID, BOOK_TIME_MISSING,
    BOOK_TIME_IN_FUTURE, STALE_BOOK, UNSYNCHRONIZED_BOOKS,
    FEE_SCHEDULE_UNKNOWN, NO_EXECUTABLE_DEPTH, NOT_PROFITABLE_AFTER_COSTS,
    NO_SIDE_NOT_LISTED, INVALID_TRANSITION, OVERFILL,
)

# ── defaults ─────────────────────────────────────────────────────────
DEFAULT_MAX_AGE_S = 30
DEFAULT_MAX_SKEW_S = 5
DEFAULT_SLIPPAGE_PER_CONTRACT = Decimal("0.01")     # per contract, per leg
DEFAULT_FIXED_COST_PER_ORDER = Decimal("0")
DEFAULT_MAX_SCAN_QTY = 100_000

ONE = Decimal(1)
ZERO = Decimal(0)
CENT = Decimal("0.01")

# ── fees ─────────────────────────────────────────────────────────────
KALSHI_TAKER_COEFFICIENT = Decimal("0.07")
#: Sports whose Polymarket US taker coefficient differs from the
#: exchange-wide default. The authority is `calibration_fees.TAKER_BY_SPORT`
#: (which this module does not import); a test pins this set equal to its
#: non-default keys, so the two cannot drift apart silently.
PMUS_SPORTS_WITH_OWN_THETA = frozenset({"TABLE_TENNIS"})


class RefusedError(ValueError):
    """A builder (not an evaluator) refusing its input. Carries `.code`."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__("%s: %s" % (code, detail) if detail else code)
        self.code = code
        self.detail = detail


# ═════════════════════════════════════════════════════════════════════
# DATA MODEL
# ═════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SettlementSpec:
    """What a contract is graded against and how it settles.

    `event_key` is the canonical identity of the event (fixture). `line` is
    informational: two legs of a middle legitimately carry different lines,
    and what the line DOES is encoded in the payoff map over the shared
    outcome space. `settle_window` is (start_iso, end_iso), both timezone
    aware. `void_rule` and `tie_rule` are the venue's rule text or a
    canonical code for it, compared for exact equality.
    """
    event_key: str
    family: str
    period: str
    subject: str
    line: Decimal | None
    resolution_source: str
    settle_window: tuple[str, str]
    void_rule: str
    tie_rule: str


@dataclass(frozen=True)
class Contract:
    """One side of one market on one venue, with its explicit payoff map.

    `payoff` maps every outcome of the event's declared OutcomeSpace to the
    dollars one contract pays in that outcome. A value must be a Decimal, an
    int or a numeric string in [0, 1]; a float is refused because 0.1 is not
    a tenth in binary. `sport` is required for Polymarket US fee lookup.
    """
    venue: str
    market_id: str
    side: str
    spec: SettlementSpec
    payoff: Mapping[str, Any]
    sport: str | None = None
    #: KALSHI: the published fee terms in force (kalshi_fees.effective_terms
    #: -- series / event multiplier, versioned). When set, the leg is priced
    #: on them; the production claim path never builds a Kalshi leg without
    #: them (Kalshi rep 2026-10-07: unknown multiplier = ineligible).
    fee_terms: Any = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.venue, self.market_id, self.side)


@dataclass(frozen=True)
class Book:
    """Asks for one (venue, market_id, side): [(price, qty)], any order.

    `observed_at` is when WE observed the book (aware datetime); `venue_ts`
    is the venue's own timestamp if it sent one, echoed but not trusted for
    freshness.
    """
    venue: str
    market_id: str
    side: str
    asks: tuple
    observed_at: datetime | None
    venue_ts: datetime | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.venue, self.market_id, self.side)


@dataclass(frozen=True)
class OutcomeSpace:
    """The declared outcome universe of one event.

    `exhaustive` is the caller's attestation that `outcomes` partitions every
    state the event can reach under the contracts' rules (a FIELD / OTHER
    outcome included when the venue lists one); `basis` says where that
    attestation comes from. `tie_outcomes` names the outcomes that are a tie
    or a push. `line_buckets` builds a space whose exhaustiveness is proved
    by construction.
    """
    event_key: str
    outcomes: tuple[str, ...]
    exhaustive: bool = False
    basis: str = ""
    tie_outcomes: tuple[str, ...] = ()
    buckets: tuple = ()   # ((label, lo|None, hi|None), ...) for line buckets

    @property
    def regular_outcomes(self) -> tuple[str, ...]:
        return tuple(o for o in self.outcomes if o not in REQUIRED_NONSTANDARD)


# ═════════════════════════════════════════════════════════════════════
# SMALL PURE HELPERS
# ═════════════════════════════════════════════════════════════════════

def _dec(x) -> Decimal | None:
    """A finite Decimal from Decimal / int / numeric str; None otherwise.
    Floats and bools are refused rather than converted."""
    if isinstance(x, bool) or isinstance(x, float):
        return None
    if isinstance(x, Decimal):
        return x if x.is_finite() else None
    if isinstance(x, int):
        return Decimal(x)
    if isinstance(x, str):
        try:
            d = Decimal(x)
        except InvalidOperation:
            return None
        return d if d.is_finite() else None
    return None


def _s(x) -> str | None:
    return None if x is None else str(x)


def _aware(t) -> bool:
    return isinstance(t, datetime) and t.tzinfo is not None \
        and t.utcoffset() is not None


def _parse_iso(s) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if _aware(t) else None


def _reason(code: str, detail: str = "", **extra) -> dict:
    r = {"code": code, "detail": detail}
    r.update(extra)
    return r


def _num_ok(x) -> bool:
    if isinstance(x, bool):
        return False
    if isinstance(x, (int, Decimal)):
        return x >= 0
    if isinstance(x, float):
        return math.isfinite(x) and x >= 0
    return False


# ═════════════════════════════════════════════════════════════════════
# FEES
# ═════════════════════════════════════════════════════════════════════

def ceil_to_cent(x: Decimal) -> Decimal:
    return (x * 100).to_integral_value(rounding=ROUND_CEILING) / 100


def kalshi_taker_fee(count, price) -> Decimal:
    """Kalshi taker fee for ONE order: ceil_to_cent(0.07 x C x p x (1-p)).

    Rounding is per order. Pinned by test against kalshi_orders.fee_for.
    """
    c, p = _dec(count), _dec(price)
    if c is None or c <= 0 or c != c.to_integral_value():
        raise ValueError("count must be a positive whole number of contracts")
    if p is None or not (ZERO < p < ONE):
        raise ValueError("price must be strictly between 0 and 1")
    return ceil_to_cent(KALSHI_TAKER_COEFFICIENT * c * p * (ONE - p))


@dataclass(frozen=True)
class FeeQuote:
    known: bool
    fee: Decimal | None
    code: str | None
    basis: str


def _pmus_candidate_schedules(at: datetime) -> list:
    """The schedules that could be in force at `at`: its UTC date and the
    previous date (the venue's effective instants are in ET). Unknown dates
    are skipped; none at all means the fee is unknown."""
    out = []
    for d in (at.date(), at.date() - timedelta(days=1)):
        try:
            s = FS.for_date(d.isoformat())
        except ValueError:
            continue
        if s not in out:
            out.append(s)
    return out


#: the published Kalshi General Trading Fees Table coefficient (equal to
#: kalshi_fees.TAKER_COEFFICIENT; tests/test_kalshi_ws_market_data pins it)
KALSHI_PUBLISHED_TAKER_COEFFICIENT = Decimal("0.07")


def order_fee(venue: str, fills: Sequence[tuple[Decimal, int]], *,
              at: datetime, sport: str | None = None,
              terms: Any = None) -> FeeQuote:
    """Taker fee for ONE order at `venue` that fills `fills` [(price, n)],
    one fill per price level. Unknown -> FeeQuote(known=False, ...).
    KALSHI with `terms` (kalshi_fees): the published schedule x the series /
    event multiplier, trade fee per fill ceil to $0.000001, the order's cash
    debit aligned up to the cent; terms that do not price -> unknown."""
    if not fills:
        return FeeQuote(True, ZERO, None, "EMPTY_ORDER")
    if venue == KALSHI and terms is not None:
        if not terms.get("priced") or terms.get("effective_at") is None:
            return FeeQuote(False, None, FEE_SCHEDULE_UNKNOWN,
                            "KALSHI fee terms do not price: %s"
                            % terms.get("version"))
        try:
            mult = Decimal(str(terms["multiplier"]))
        except (InvalidOperation, KeyError, TypeError):
            return FeeQuote(False, None, FEE_SCHEDULE_UNKNOWN,
                            "KALSHI fee multiplier unreadable")
        # the published General Trading Fees Table, quadratic, x the bound
        # multiplier (kalshi_fees.model_fee; pinned equal by a test)
        trade = sum(((mult * KALSHI_PUBLISHED_TAKER_COEFFICIENT * n * p
                      * (Decimal(1) - p)).quantize(
                          Decimal("0.000001"), rounding=ROUND_CEILING)
                     for p, n in fills), ZERO)
        principal = sum((p * n for p, n in fills), ZERO)
        debit = ((principal + trade) / Decimal("0.01")).to_integral_value(
            rounding=ROUND_CEILING) * Decimal("0.01")
        return FeeQuote(True, debit - principal, None,
                        "KALSHI published %s (%s, effective %s)" % (
                            terms.get("version"), terms.get("schedule_id"),
                            terms.get("effective_at")))
    if venue == KALSHI:
        fee = sum((kalshi_taker_fee(n, p) for p, n in fills), ZERO)
        return FeeQuote(True, fee, None,
                        "KALSHI ceil_to_cent(0.07*C*p*(1-p)) per order, "
                        "one fill per level")
    if venue == POLYMARKET_US:
        if not sport:
            return FeeQuote(False, None, FEE_SCHEDULE_UNKNOWN,
                            "POLYMARKET_US leg declares no sport; the "
                            "coefficient is per sport")
        sk = str(sport).upper().replace(" ", "_")
        if sk in PMUS_SPORTS_WITH_OWN_THETA:
            return FeeQuote(False, None, FEE_SCHEDULE_UNKNOWN,
                            "sport %s carries its own coefficient, not the "
                            "exchange-wide schedule" % sk)
        if not _aware(at):
            return FeeQuote(False, None, FEE_SCHEDULE_UNKNOWN, "no fee date")
        scheds = _pmus_candidate_schedules(at)
        if not scheds:
            return FeeQuote(False, None, FEE_SCHEDULE_UNKNOWN,
                            "no published POLYMARKET_US schedule covers %s"
                            % at.date().isoformat())
        best, basis = None, ""
        for s in scheds:
            if s.taker_rounding == FS.CUMULATIVE_PER_ORDER:
                exact = sum((s.exact(s.theta_taker, n, p) for p, n in fills),
                            ZERO)
                f = FS.bankers_cents(exact)
            else:
                f = sum((s.taker_fee(n, p) for p, n in fills), ZERO)
            if best is None or f > best:
                best, basis = f, "%s %s" % (s.schedule_id, s.taker_rounding)
        return FeeQuote(True, best, None, basis)
    return FeeQuote(False, None, FEE_SCHEDULE_UNKNOWN,
                    "no known fee schedule for venue %r" % (venue,))


# ═════════════════════════════════════════════════════════════════════
# OUTCOME SPACES AND PAYOFF BUILDERS
# ═════════════════════════════════════════════════════════════════════

def _is_whole(x: Decimal) -> bool:
    return x == x.to_integral_value()


def _is_half(x: Decimal) -> bool:
    return (x * 2) == (x * 2).to_integral_value() and not _is_whole(x)


def line_buckets(event_key: str, lines: Iterable, *,
                 push_rule_declared: bool = False) -> OutcomeSpace:
    """The exhaustive integer-bucket outcome space cut by `lines`.

    The underlying (a total, a margin) is an integer, so a half-point line
    k+0.5 cuts between k and k+1 and the buckets partition the integers by
    construction -- that is the proof of exhaustiveness. A whole-number line
    k needs its own bucket {k}, where the contract pushes; that is refused
    as TIE_RULE_UNKNOWN unless `push_rule_declared`, in which case the
    {k} bucket is listed in `tie_outcomes` and each contract's payoff map
    must say what it pays there. VOID and POSTPONED are appended.

    Example: lines (210.5, 211.5) -> LE_210, EQ_211, GE_212, VOID, POSTPONED.
    """
    if not event_key:
        raise RefusedError(EVENT_IDENTITY_UNPROVEN, "no event_key")
    cuts: set[int] = set()        # bucket boundaries: an interval ends at c
    pushes: set[int] = set()
    parsed = []
    for raw in lines:
        x = _dec(raw)
        if x is None:
            raise RefusedError(PARAMETER_INVALID, "line %r is not a decimal"
                               % (raw,))
        parsed.append(x)
        if _is_half(x):
            cuts.add(int((x - Decimal("0.5")).to_integral_value()))
        elif _is_whole(x):
            if not push_rule_declared:
                raise RefusedError(
                    TIE_RULE_UNKNOWN,
                    "whole-number line %s can push and no push / void rule "
                    "is declared" % x)
            k = int(x)
            cuts.update({k - 1, k})
            pushes.add(k)
        else:
            raise RefusedError(PARAMETER_INVALID,
                               "line %s is neither whole nor half-point" % x)
    if not parsed:
        raise RefusedError(PARAMETER_INVALID, "no lines")
    cs = sorted(cuts)
    buckets = []
    lo = None
    for c in cs:
        buckets.append((lo, c))
        lo = c + 1
    buckets.append((lo, None))

    def label(a, b):
        if a is None:
            return "LE_%d" % b
        if b is None:
            return "GE_%d" % a
        return "EQ_%d" % a if a == b else "B_%d_%d" % (a, b)

    labelled = tuple((label(a, b), a, b) for a, b in buckets)
    ties = tuple(lb for lb, a, b in labelled if a is not None and a == b
                 and a in pushes)
    outcomes = tuple(lb for lb, _, _ in labelled) + REQUIRED_NONSTANDARD
    return OutcomeSpace(event_key=event_key, outcomes=outcomes,
                        exhaustive=True,
                        basis="LINE_BUCKETS: integer partition by construction",
                        tie_outcomes=ties, buckets=labelled)


def over_under_payoff(space: OutcomeSpace, direction: str, line, *,
                      void_payout, postponed_payout,
                      push_payout=None) -> dict:
    """Payoff map of 'OVER line' / 'UNDER line' (per contract held) over a
    `line_buckets` space. A bucket straddling the line is refused (the
    space was not cut at this line). A push bucket at a whole line pays
    `push_payout`, which must be declared."""
    if direction not in ("OVER", "UNDER"):
        raise RefusedError(PARAMETER_INVALID, "direction is OVER or UNDER")
    L = _dec(line)
    vp, pp = _dec(void_payout), _dec(postponed_payout)
    if L is None or vp is None or pp is None:
        raise RefusedError(PARAMETER_INVALID, "line / void / postponed payout")
    out: dict[str, Decimal] = {}
    for lb, a, b in space.buckets:
        above = a is not None and Decimal(a) > L
        below = b is not None and Decimal(b) < L
        on = a is not None and b is not None and a == b and Decimal(a) == L
        if on:
            pu = _dec(push_payout)
            if pu is None:
                raise RefusedError(TIE_RULE_UNKNOWN,
                                   "bucket %s is a push and no push payout "
                                   "is declared" % lb)
            out[lb] = pu
        elif above:
            out[lb] = ONE if direction == "OVER" else ZERO
        elif below:
            out[lb] = ZERO if direction == "OVER" else ONE
        else:
            raise RefusedError(OUTCOME_SPACE_DIFFERS,
                               "bucket %s straddles line %s" % (lb, L))
    out[OUTCOME_VOID] = vp
    out[OUTCOME_POSTPONED] = pp
    return out


def opposite_side(contract: Contract) -> Contract:
    """The other side of the SAME market: side flipped, payoff 1 - x in every
    outcome. Valid only where the venue's rules define the NO side as paying
    exactly 1 - YES in every state (VOID and POSTPONED included); a venue
    that refunds both sides at cost on a void does not satisfy that, and the
    caller must then build the NO payoff from the venue's rules instead."""
    if contract.side not in SIDES:
        raise RefusedError(SIDE_INVALID, repr(contract.side))
    pay = {}
    for o, v in dict(contract.payoff).items():
        d = _dec(v)
        if d is None:
            raise RefusedError(PAYOFF_INVALID, "%s=%r" % (o, v))
        pay[o] = ONE - d
    return replace(contract, side=NO if contract.side == YES else YES,
                   payoff=pay)


# ── synthesising a NO ask from a YES bid: refused unless declared ────

#: (venue, market_id) pairs the caller has proved list ONE book in which
#: "buy NO at 1 - YES bid" is the same instrument. Empty: nothing is.
SINGLE_INSTRUMENT_BOOKS: frozenset = frozenset()


def may_synthesize_no_ask(contract: Contract, *,
                          declared_single_instrument: Iterable = ()
                          ) -> tuple[bool, str]:
    """Whether a NO ask may be derived from YES bids for this contract.
    Default: NOT PERMITTED for every venue and market (fail closed)."""
    allowed = set(SINGLE_INSTRUMENT_BOOKS) | set(declared_single_instrument)
    if contract.side != NO:
        return False, "only a NO side can be synthesised from YES bids"
    if (contract.venue, contract.market_id) not in allowed:
        return False, ("(%s, %s) is not declared a single-instrument book; a "
                       "NO ask must be listed by the venue"
                       % (contract.venue, contract.market_id))
    return True, "declared single-instrument book"


def synthesize_no_asks(contract: Contract, yes_bids: Iterable, *,
                       declared_single_instrument: Iterable = ()) -> tuple:
    """NO asks (1 - bid, qty) from YES bids, or raise NO_SIDE_NOT_LISTED."""
    ok, why = may_synthesize_no_ask(
        contract, declared_single_instrument=declared_single_instrument)
    if not ok:
        raise RefusedError(NO_SIDE_NOT_LISTED, why)
    out = []
    for p, q in yes_bids:
        dp, dq = _dec(p), _dec(q)
        if dp is None or dq is None or not (ZERO < dp < ONE) or dq <= 0:
            raise RefusedError(BOOK_INVALID, "bad YES bid %r" % ((p, q),))
        out.append((ONE - dp, dq))
    return tuple(sorted(out, key=lambda t: t[0]))


# ═════════════════════════════════════════════════════════════════════
# VALIDATION: CONTRACTS, SPACES, SETTLEMENT EQUIVALENCE
# ═════════════════════════════════════════════════════════════════════

def validate_space(space: OutcomeSpace) -> list[dict]:
    rs = []
    if not isinstance(space, OutcomeSpace):
        return [_reason(OUTCOME_SPACE_NOT_PROVEN_EXHAUSTIVE,
                        "no declared outcome space")]
    if not space.event_key:
        rs.append(_reason(EVENT_IDENTITY_UNPROVEN, "outcome space has no event_key"))
    outs = list(space.outcomes or ())
    if len(set(outs)) != len(outs):
        dups = sorted({o for o in outs if outs.count(o) > 1})
        rs.append(_reason(DUPLICATE_OUTCOME, "duplicated: %s" % dups))
    missing = [o for o in REQUIRED_NONSTANDARD if o not in outs]
    if missing:
        rs.append(_reason(NONSTANDARD_OUTCOMES_MISSING,
                          "outcome space lacks %s" % missing))
    if not space.regular_outcomes:
        rs.append(_reason(OUTCOME_SPACE_NOT_PROVEN_EXHAUSTIVE,
                          "no regular outcomes"))
    if space.exhaustive is not True:
        rs.append(_reason(OUTCOME_SPACE_NOT_PROVEN_EXHAUSTIVE,
                          "exhaustiveness is not attested"))
    bad_ties = [t for t in space.tie_outcomes if t not in outs]
    if bad_ties:
        rs.append(_reason(OUTCOME_SPACE_DIFFERS,
                          "tie outcomes not in the space: %s" % bad_ties))
    return rs


def validate_contract(c: Contract, space: OutcomeSpace) -> list[dict]:
    """Every reason this contract cannot be evaluated over `space`."""
    rs = []
    tag = "%s:%s:%s" % (getattr(c, "venue", "?"), getattr(c, "market_id", "?"),
                        getattr(c, "side", "?"))
    if not isinstance(c, Contract) or not isinstance(c.spec, SettlementSpec):
        return [_reason(CONTRACT_SPEC_INCOMPLETE, "%s is not a Contract" % tag)]
    sp = c.spec
    if not c.venue or not c.market_id:
        rs.append(_reason(CONTRACT_SPEC_INCOMPLETE, "%s: venue / market_id" % tag))
    if c.side not in SIDES:
        rs.append(_reason(SIDE_INVALID, "%s: side %r" % (tag, c.side)))
    for f in ("event_key", "family", "period", "subject", "resolution_source",
              "void_rule"):
        if not getattr(sp, f):
            rs.append(_reason(CONTRACT_SPEC_INCOMPLETE, "%s: %s missing" % (tag, f)))
    if sp.family and sp.family not in FAMILIES:
        rs.append(_reason(CONTRACT_SPEC_INCOMPLETE,
                          "%s: family %r unknown" % (tag, sp.family)))
    if sp.line is not None and _dec(sp.line) is None:
        rs.append(_reason(CONTRACT_SPEC_INCOMPLETE, "%s: line %r" % (tag, sp.line)))
    if str(sp.tie_rule or "").upper() in UNKNOWN_TIE_RULES:
        rs.append(_reason(TIE_RULE_UNKNOWN, "%s: tie rule not declared" % tag))
    elif sp.tie_rule != TIE_IMPOSSIBLE and isinstance(space, OutcomeSpace) \
            and not space.tie_outcomes:
        rs.append(_reason(NONSTANDARD_OUTCOMES_MISSING,
                          "%s: tie rule %r admits a tie but the space "
                          "enumerates no tie / push outcome" % (tag, sp.tie_rule)))
    w = sp.settle_window
    if not (isinstance(w, tuple) and len(w) == 2 and _parse_iso(w[0])
            and _parse_iso(w[1]) and _parse_iso(w[0]) <= _parse_iso(w[1])):
        rs.append(_reason(SETTLE_WINDOW_INVALID, "%s: %r" % (tag, w)))
    if isinstance(space, OutcomeSpace) and space.event_key and \
            sp.event_key != space.event_key:
        rs.append(_reason(EVENT_IDENTITY_UNPROVEN,
                          "%s: event %r is not the space's %r"
                          % (tag, sp.event_key, space.event_key)))
    pay = c.payoff if isinstance(c.payoff, Mapping) else None
    if pay is None:
        rs.append(_reason(PAYOFF_INCOMPLETE, "%s: no payoff map" % tag))
        return rs
    outs = set(space.outcomes) if isinstance(space, OutcomeSpace) else set()
    keys = set(pay)
    if outs - keys:
        rs.append(_reason(PAYOFF_INCOMPLETE, "%s: no payout for %s"
                          % (tag, sorted(outs - keys))))
    if keys - outs:
        rs.append(_reason(OUTCOME_SPACE_DIFFERS, "%s: outcomes %s are not in "
                          "the declared space" % (tag, sorted(keys - outs))))
    for o, v in pay.items():
        d = _dec(v)
        if d is None or d < 0 or d > 1:
            rs.append(_reason(PAYOFF_INVALID, "%s: %s=%r" % (tag, o, v)))
    return rs


def settlement_differences(a: Contract, b: Contract, *,
                           settle_window_tolerance_s: float = 0) -> list[dict]:
    """Why two contracts are not graded by the same settlement process.
    Empty list: same event, period, family, source, window, void and tie
    rules. Payoffs are compared separately."""
    rs = []
    sa, sb = a.spec, b.spec
    if not sa.event_key or not sb.event_key or sa.event_key != sb.event_key:
        rs.append(_reason(EVENT_IDENTITY_UNPROVEN, "%r vs %r"
                          % (sa.event_key, sb.event_key)))
    if sa.period != sb.period:
        rs.append(_reason(MARKET_DEFINITION_DIFFERS, "period %r vs %r"
                          % (sa.period, sb.period)))
    if sa.family != sb.family:
        rs.append(_reason(MARKET_DEFINITION_DIFFERS, "family %r vs %r"
                          % (sa.family, sb.family)))
    if sa.resolution_source != sb.resolution_source:
        rs.append(_reason(SETTLEMENT_SOURCE_DIFFERS, "%r vs %r"
                          % (sa.resolution_source, sb.resolution_source)))
    wa = tuple(_parse_iso(x) for x in (sa.settle_window or ("", "")))
    wb = tuple(_parse_iso(x) for x in (sb.settle_window or ("", "")))
    if None in wa or None in wb or len(wa) != 2 or len(wb) != 2:
        rs.append(_reason(SETTLE_WINDOW_INVALID, "%r vs %r"
                          % (sa.settle_window, sb.settle_window)))
    else:
        tol = timedelta(seconds=float(settle_window_tolerance_s))
        if abs(wa[0] - wb[0]) > tol or abs(wa[1] - wb[1]) > tol:
            rs.append(_reason(SETTLE_WINDOW_DIFFERS, "%r vs %r"
                              % (sa.settle_window, sb.settle_window)))
    if sa.void_rule != sb.void_rule:
        rs.append(_reason(VOID_RULE_DIFFERS, "%r vs %r"
                          % (sa.void_rule, sb.void_rule)))
    if sa.tie_rule != sb.tie_rule:
        rs.append(_reason(TIE_RULE_DIFFERS, "%r vs %r"
                          % (sa.tie_rule, sb.tie_rule)))
    return rs


def payoff_vector(c: Contract, space: OutcomeSpace) -> tuple[Decimal, ...]:
    return tuple(_dec(c.payoff[o]) for o in space.outcomes)


def classify_pair(a: Contract, b: Contract, space: OutcomeSpace, *,
                  settle_window_tolerance_s: float = 0) -> dict:
    """EQUIVALENT | COMPLEMENTARY | STRUCTURE | REFUSED, with reasons.

    EQUIVALENT: identical payoff vectors (and settlement). COMPLEMENTARY:
    payoffs sum to exactly 1 in every outcome, VOID / POSTPONED / TIE
    included. STRUCTURE: valid, neither; its floor is min(a + b)."""
    rs = validate_space(space) + validate_contract(a, space) + \
        validate_contract(b, space)
    if not rs:
        rs = settlement_differences(
            a, b, settle_window_tolerance_s=settle_window_tolerance_s)
    if rs:
        return {"relation": REFUSED, "reasons": rs}
    va, vb = payoff_vector(a, space), payoff_vector(b, space)
    sums = [x + y for x, y in zip(va, vb)]
    if va == vb:
        rel = "EQUIVALENT"
    elif all(s == ONE for s in sums):
        rel = "COMPLEMENTARY"
    else:
        rel = "STRUCTURE"
    return {"relation": rel, "reasons": [],
            "sum_by_outcome": {o: str(s) for o, s in zip(space.outcomes, sums)},
            "floor": str(min(sums))}


# ═════════════════════════════════════════════════════════════════════
# BOOKS: VALIDATION, FRESHNESS, LADDER WALK
# ═════════════════════════════════════════════════════════════════════

def normalize_asks(asks) -> tuple[tuple[Decimal, int], ...]:
    """Asks sorted best first, quantities floored to whole contracts (a
    fractional remainder is not executable depth). Raises RefusedError
    BOOK_INVALID on any malformed level."""
    out = []
    try:
        levels = list(asks or ())
    except TypeError:
        raise RefusedError(BOOK_INVALID, "asks are not a list") from None
    for lv in levels:
        try:
            p, q = lv
        except (TypeError, ValueError):
            raise RefusedError(BOOK_INVALID, "level %r" % (lv,)) from None
        dp, dq = _dec(p), _dec(q)
        if dp is None or dq is None or not (ZERO < dp < ONE) or dq <= 0:
            raise RefusedError(BOOK_INVALID, "level %r" % (lv,))
        n = int(dq.to_integral_value(rounding=ROUND_FLOOR))
        if n >= 1:
            out.append((dp, n))
    out.sort(key=lambda t: t[0])
    return tuple(out)


def book_reasons(c: Contract, book: Book | None, now: datetime, *,
                 max_age_s: float) -> list[dict]:
    """Presence, identity, validity and age of one leg's book."""
    tag = "%s:%s:%s" % c.key
    if book is None:
        return [_reason(BOOK_MISSING, tag)]
    if not isinstance(book, Book) or book.key != c.key:
        return [_reason(BOOK_CONTRACT_MISMATCH, "%s vs %r"
                        % (tag, getattr(book, "key", book)))]
    rs = []
    try:
        normalize_asks(book.asks)
    except RefusedError as e:
        rs.append(_reason(BOOK_INVALID, "%s: %s" % (tag, e.detail)))
    if not _aware(book.observed_at):
        rs.append(_reason(BOOK_TIME_MISSING, "%s: observed_at %r"
                          % (tag, book.observed_at)))
        return rs
    age = (now - book.observed_at).total_seconds()
    if age < 0:
        rs.append(_reason(BOOK_TIME_IN_FUTURE, "%s: observed %.6fs after now"
                          % (tag, -age)))
    elif age > max_age_s:
        rs.append(_reason(STALE_BOOK, "%s: age %.6fs > %ss"
                          % (tag, age, max_age_s), age_s=age))
    return rs


def walk_asks(levels: Sequence[tuple[Decimal, int]], qty: int) -> list | None:
    """Fills [(price, n)] buying `qty` best-first; None if depth < qty."""
    left, fills = int(qty), []
    for p, n in levels:
        if left <= 0:
            break
        take = min(left, n)
        fills.append((p, take))
        left -= take
    return fills if left <= 0 else None


def leg_cost(c: Contract, levels, qty: int, *, at: datetime,
             slippage_per_contract: Decimal = DEFAULT_SLIPPAGE_PER_CONTRACT,
             fixed_cost_per_order: Decimal = DEFAULT_FIXED_COST_PER_ORDER
             ) -> dict | None:
    """All-in cost of ONE order of `qty` on this leg, or None if the book
    cannot fill it or the fee is unknown."""
    fills = walk_asks(levels, qty)
    if fills is None:
        return None
    fq = order_fee(c.venue, fills, at=at, sport=c.sport,
                   terms=getattr(c, "fee_terms", None))
    if not fq.known:
        return None
    notional = sum((p * n for p, n in fills), ZERO)
    slip = slippage_per_contract * qty
    fixed = fixed_cost_per_order if qty > 0 else ZERO
    total = notional + fq.fee + slip + fixed
    return {"venue": c.venue, "market_id": c.market_id, "side": c.side,
            "qty": qty, "levels_consumed": [[str(p), n] for p, n in fills],
            "notional": notional, "vwap": notional / qty, "fee": fq.fee,
            "fee_basis": fq.basis, "slippage": slip, "fixed_cost": fixed,
            "total_cost": total}


# ═════════════════════════════════════════════════════════════════════
# THE CORE: evaluate_structure
# ═════════════════════════════════════════════════════════════════════

def _index_books(books) -> dict:
    if books is None:
        return {}
    if isinstance(books, Mapping):
        return dict(books)
    return {b.key: b for b in books}


def _strify(x):
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, dict):
        return {k: _strify(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_strify(v) for v in x]
    if isinstance(x, datetime):
        return x.isoformat()
    return x


def _indicator_of(vec_by_outcome: dict, regular: tuple, flip: bool):
    """The regular outcome this payoff is the YES (or, flipped, NO)
    indicator of; None if it is not an indicator."""
    hits = []
    for o in regular:
        v = vec_by_outcome[o]
        v = ONE - v if flip else v
        if v == ONE:
            hits.append(o)
        elif v != ZERO:
            return None
    return hits[0] if len(hits) == 1 else None


def _infer_kind(legs_vecs: list[dict], space: OutcomeSpace,
                sums: list[Decimal]) -> str:
    regular = space.regular_outcomes
    n = len(legs_vecs)
    const = len(set(sums)) == 1
    if n == 2 and const and sums[0] == ONE:
        return COMPLEMENT
    for flip, kind, target in ((False, YES_BASKET, ONE),
                               (True, NO_BASKET, Decimal(n - 1))):
        inds = [_indicator_of(v, regular, flip) for v in legs_vecs]
        if None not in inds and len(set(inds)) == n == len(regular) \
                and const and sums[0] == target and n >= 2:
            if not (kind == NO_BASKET and n == 2):
                return kind
    return MIDDLE_FLOOR


def _basket_reasons(kind: str, legs_vecs: list[dict], space: OutcomeSpace,
                    sums: list[Decimal]) -> list[dict]:
    """Why legs do not form the expected YES / NO basket."""
    rs = []
    regular = space.regular_outcomes
    flip = kind == NO_BASKET
    inds = [_indicator_of(v, regular, flip) for v in legs_vecs]
    if None in inds:
        rs.append(_reason(PAYOFF_NOT_INDICATOR,
                          "leg %d is not a %s indicator of one outcome"
                          % (inds.index(None), "NO" if flip else "YES")))
        return rs
    seen = [o for o in inds if inds.count(o) > 1]
    if seen:
        rs.append(_reason(DUPLICATE_OUTCOME,
                          "outcomes covered twice: %s" % sorted(set(seen))))
    uncovered = [o for o in regular if o not in inds]
    if uncovered:
        rs.append(_reason(OUTCOME_NOT_COVERED, "no leg for %s" % uncovered))
    target = ONE if kind == YES_BASKET else Decimal(len(legs_vecs) - 1)
    off = {o: str(s) for o, s in zip(space.outcomes, sums) if s != target}
    if not rs and off:
        rs.append(_reason(BASKET_PAYOFF_NOT_CONSTANT,
                          "basket pays %s, not %s, in %s" % (
                              sorted(set(off.values())), target, sorted(off))))
    return rs


def evaluate_structure(legs: Sequence, books, outcome_space: OutcomeSpace,
                       now: datetime, *,
                       expect_kind: str | None = None,
                       max_age_s: float = DEFAULT_MAX_AGE_S,
                       max_skew_s: float = DEFAULT_MAX_SKEW_S,
                       slippage_per_contract=DEFAULT_SLIPPAGE_PER_CONTRACT,
                       fixed_cost_per_order=DEFAULT_FIXED_COST_PER_ORDER,
                       max_qty: int | None = None,
                       max_scan_qty: int = DEFAULT_MAX_SCAN_QTY,
                       settle_window_tolerance_s: float = 0) -> dict:
    """Evaluate buying one of each leg per matched set. THE core.

    `legs` is a sequence; each element is a Contract or a sequence of
    payoff-equivalent alternative Contracts (e.g. the same outcome on two
    venues), of which the cheapest fillable one is used at each size.
    Returns a dict-serialisable record whose verdict is
    GUARANTEED_AFTER_COSTS only when every condition in the module
    docstring holds at the reported size.
    """
    reasons: list[dict] = []
    params = {"max_age_s": max_age_s, "max_skew_s": max_skew_s,
              "slippage_per_contract": _s(slippage_per_contract),
              "fixed_cost_per_order": _s(fixed_cost_per_order),
              "max_qty": max_qty, "max_scan_qty": max_scan_qty,
              "settle_window_tolerance_s": settle_window_tolerance_s,
              "expect_kind": expect_kind}
    groups: list[list[Contract]] = []
    for lg in (legs or ()):
        groups.append(list(lg) if isinstance(lg, (list, tuple)) else [lg])
    flat = [c for g in groups for c in g]
    inputs = {"now": now.isoformat() if isinstance(now, datetime) else _s(now),
              "event_key": getattr(outcome_space, "event_key", None),
              "outcome_space": list(getattr(outcome_space, "outcomes", ()) or ()),
              "legs": [[{"venue": getattr(c, "venue", None),
                         "market_id": getattr(c, "market_id", None),
                         "side": getattr(c, "side", None)} for c in g]
                       for g in groups],
              "books": [], "skew_s": None, "params": params}

    def done(kind=None, economics=None, extra=None):
        return _record("STRUCTURE", kind, reasons, inputs, economics, extra)

    # ── parameters and clock ──────────────────────────────────────────
    if not _aware(now):
        reasons.append(_reason(CLOCK_INVALID, "now must be an aware datetime"))
    slip, fixed = _dec(slippage_per_contract), _dec(fixed_cost_per_order)
    if slip is None or slip < 0 or fixed is None or fixed < 0 \
            or not _num_ok(max_age_s) or not _num_ok(max_skew_s) \
            or not _num_ok(settle_window_tolerance_s) \
            or (max_qty is not None and (not isinstance(max_qty, int)
                                         or isinstance(max_qty, bool)
                                         or max_qty < 1)) \
            or not isinstance(max_scan_qty, int) or max_scan_qty < 1 \
            or expect_kind not in (None,) + STRUCTURE_KINDS:
        reasons.append(_reason(PARAMETER_INVALID, repr(params)))
    if len(groups) < 2 or any(not g for g in groups):
        reasons.append(_reason(PARAMETER_INVALID,
                               "a structure needs at least two non-empty legs"))
    if reasons:
        return done()

    # ── outcome space and contracts ───────────────────────────────────
    reasons.extend(validate_space(outcome_space))
    for c in flat:
        reasons.extend(validate_contract(c, outcome_space))

    # ── settlement equivalence across EVERY pair of contracts ────────
    # Reported alongside any validation failure, so a contract that is
    # both malformed and differently settled is refused for both reasons.
    if all(isinstance(c, Contract) and isinstance(c.spec, SettlementSpec)
           for c in flat):
        for a, b in combinations(flat, 2):
            for r in settlement_differences(
                    a, b, settle_window_tolerance_s=settle_window_tolerance_s):
                if r not in reasons:
                    reasons.append(r)
    if reasons:
        return done()
    keys = [c.key for c in flat]
    if len(set(keys)) != len(keys):
        reasons.append(_reason(DUPLICATE_LEG, "the same instrument appears "
                               "twice: %s" % sorted({k for k in keys
                                                     if keys.count(k) > 1})))
    for i, g in enumerate(groups):
        v0 = payoff_vector(g[0], outcome_space)
        for alt in g[1:]:
            if payoff_vector(alt, outcome_space) != v0:
                reasons.append(_reason(LEG_ALTERNATIVES_NOT_EQUIVALENT,
                                       "leg %d: %s:%s:%s vs %s:%s:%s"
                                       % ((i,) + g[0].key + alt.key)))
    if reasons:
        return done()

    # ── the payoff table: prices are still unread ────────────────────
    vecs = [{o: _dec(g[0].payoff[o]) for o in outcome_space.outcomes}
            for g in groups]
    sums = [sum((v[o] for v in vecs), ZERO) for o in outcome_space.outcomes]
    floor = min(sums)
    table = {"payout_by_outcome": {o: str(s) for o, s in
                                   zip(outcome_space.outcomes, sums)},
             "floor_payout_per_set": str(floor),
             "floor_outcomes": [o for o, s in zip(outcome_space.outcomes, sums)
                                if s == floor]}
    if expect_kind is None:
        kind = _infer_kind(vecs, outcome_space, sums)
    else:
        # The caller named the structure it expects; the payoffs must BE it.
        # (A two-outcome YES basket is also a complement; the caller's name
        # is kept when the arithmetic satisfies it.)
        if expect_kind == COMPLEMENT:
            off = [o for o, s in zip(outcome_space.outcomes, sums) if s != ONE]
            if len(groups) != 2:
                reasons.append(_reason(PAYOFF_NOT_COMPLEMENTARY,
                                       "a complement has exactly two legs"))
            elif off:
                reasons.append(_reason(PAYOFF_NOT_COMPLEMENTARY,
                                       "payoffs do not sum to 1 in %s" % off))
        elif expect_kind in (YES_BASKET, NO_BASKET):
            reasons.extend(_basket_reasons(expect_kind, vecs, outcome_space,
                                           sums))
        if reasons:
            return done(_infer_kind(vecs, outcome_space, sums), extra=table)
        kind = expect_kind
    if floor <= 0:
        reasons.append(_reason(PAYOFF_FLOOR_BELOW_COST,
                               "floor payout %s in %s" % (
                                   floor, table["floor_outcomes"])))
        return done(kind, extra=table)

    # ── books: presence, validity, freshness, fee schedule ───────────
    idx = _index_books(books)
    usable: list[list[tuple[Contract, tuple]]] = []
    excluded = []
    used_times = []
    for i, g in enumerate(groups):
        ok_alts, leg_rs = [], []
        for c in g:
            b = idx.get(c.key)
            rs = book_reasons(c, b, now, max_age_s=float(max_age_s))
            if not rs:
                probe = order_fee(c.venue, [(Decimal("0.5"), 1)], at=now,
                                  terms=getattr(c, "fee_terms", None),
                                  sport=c.sport)
                if not probe.known:
                    rs = [_reason(FEE_SCHEDULE_UNKNOWN, "%s:%s:%s: %s"
                                  % (c.key + (probe.basis,)))]
            echo = {"leg": i, "venue": c.venue, "market_id": c.market_id,
                    "side": c.side,
                    "observed_at": _s(b.observed_at.isoformat()
                                      if isinstance(b, Book) and _aware(b.observed_at)
                                      else getattr(b, "observed_at", None)),
                    "venue_ts": _s(getattr(b, "venue_ts", None)),
                    "age_s": ((now - b.observed_at).total_seconds()
                              if isinstance(b, Book) and _aware(b.observed_at)
                              else None)}
            inputs["books"].append(echo)
            if rs:
                leg_rs.extend(rs)
                if len(g) > 1:
                    excluded.append({"leg": i, "key": list(c.key),
                                     "reasons": rs})
                continue
            ok_alts.append((c, normalize_asks(b.asks)))
            used_times.append(b.observed_at)
        if not ok_alts:
            reasons.extend(leg_rs)
        usable.append(ok_alts)
    if used_times:
        skew = (max(used_times) - min(used_times)).total_seconds()
        inputs["skew_s"] = skew
        if skew > float(max_skew_s):
            reasons.append(_reason(UNSYNCHRONIZED_BOOKS, "skew %.6fs > %ss"
                                   % (skew, max_skew_s)))
    extra = dict(table, excluded_alternatives=excluded)
    if reasons:
        return done(kind, extra=extra)

    # ── depth ─────────────────────────────────────────────────────────
    depth_by_leg = [max(sum(n for _, n in lv) for _, lv in alts)
                    for alts in usable]
    if min(depth_by_leg) < 1:
        reasons.append(_reason(NO_EXECUTABLE_DEPTH, "depth by leg %s"
                               % depth_by_leg))
        return done(kind, extra=extra)
    best_asks = [min(lv[0][0] for _, lv in alts if lv) for alts in usable]
    if floor <= sum(best_asks, ZERO):
        reasons.append(_reason(PAYOFF_FLOOR_BELOW_COST,
                               "floor %s <= sum of best asks %s before fees"
                               % (floor, sum(best_asks, ZERO))))
        return done(kind, extra=extra)

    # ── the size: every integer Q up to depth ─────────────────────────
    qmax = min(depth_by_leg)
    capped = False
    if max_qty is not None:
        qmax = min(qmax, max_qty)
    if qmax > max_scan_qty:
        qmax, capped = max_scan_qty, True

    def cost_at(q):
        legs_out = []
        for alts in usable:
            best = None
            for c, lv in alts:
                lc = leg_cost(c, lv, q, at=now, slippage_per_contract=slip,
                              fixed_cost_per_order=fixed)
                if lc is not None and (best is None
                                       or lc["total_cost"] < best["total_cost"]):
                    best = lc
            if best is None:
                return None
            legs_out.append(best)
        return legs_out

    best_q, best_profit, best_legs = None, None, None
    profits = {}
    for q in range(1, qmax + 1):
        lg = cost_at(q)
        if lg is None:
            break
        profit = floor * q - sum((x["total_cost"] for x in lg), ZERO)
        profits[q] = profit
        if best_profit is None or profit > best_profit:
            best_q, best_profit, best_legs = q, profit, lg
    if best_q is None:
        reasons.append(_reason(NO_EXECUTABLE_DEPTH, "no fillable size"))
        return done(kind, extra=extra)
    total_cost = sum((x["total_cost"] for x in best_legs), ZERO)
    nxt = profits.get(best_q + 1)
    economics = dict(
        table,
        qty=best_q,
        legs=best_legs,
        total_cost=total_cost,
        floor_payout_total=floor * best_q,
        worst_case_net_profit=best_profit,
        edge_per_contract=best_profit / best_q,
        marginal_profit_next_contract=(None if nxt is None
                                       else nxt - best_profit),
        depth_by_leg=depth_by_leg,
        scan_capped=capped,
        excluded_alternatives=excluded,
    )
    if best_profit <= 0:
        reasons.append(_reason(NOT_PROFITABLE_AFTER_COSTS,
                               "best net %s at Q=%d" % (best_profit, best_q)))
    return done(kind, economics=economics, extra=extra)


def _record(evaluation: str, kind, reasons, inputs, economics, extra) -> dict:
    verdict = REFUSED
    if not reasons and economics is not None \
            and economics["worst_case_net_profit"] > 0 \
            and economics["qty"] >= 1:
        verdict = GUARANTEED_AFTER_COSTS
    rec = {"evaluation": evaluation, "engine": VERSION, "mode": MODE,
           "structure_kind": kind, "verdict": verdict,
           "reasons": list(reasons),
           "inputs": inputs,
           "economics": _strify(economics) if economics is not None else None,
           "prices_compared": economics is not None}
    if extra:
        rec["payoff_table"] = _strify({k: v for k, v in extra.items()
                                       if k in ("payout_by_outcome",
                                                "floor_payout_per_set",
                                                "floor_outcomes")})
        if extra.get("excluded_alternatives"):
            rec["excluded_alternatives"] = _strify(extra["excluded_alternatives"])
    assert rec["verdict"] in VERDICTS
    return rec


def reason_codes(record: dict) -> list[str]:
    return [r["code"] for r in record.get("reasons", ())]


# ═════════════════════════════════════════════════════════════════════
# PAIRS
# ═════════════════════════════════════════════════════════════════════

def evaluate_pair(a: Contract, b: Contract, books, outcome_space: OutcomeSpace,
                  now: datetime, **kw) -> dict:
    """Two legs through `evaluate_structure`. Pass expect_kind=COMPLEMENT to
    accept only an exact complement (else PAYOFF_NOT_COMPLEMENTARY)."""
    rec = evaluate_structure([a, b], books, outcome_space, now, **kw)
    rec["evaluation"] = "PAIR"
    return rec


def pair_scanner(contracts: Sequence[Contract], books, now: datetime, *,
                 outcome_spaces: Mapping[str, OutcomeSpace] | None = None,
                 **kw) -> tuple[list[dict], list[dict], dict]:
    """Every cross-venue, and same-venue cross-market, pair of contracts
    sharing an event_key, each evaluated exactly once.

    Returns (opportunities, refusals, census). Every considered pair yields
    exactly one record. Contracts without an event_key, and same-market
    pairs (the YES and NO of one market), are not paired; they are counted
    in the census rather than dropped silently.
    """
    spaces = dict(outcome_spaces or {})
    groups: dict[str, list[Contract]] = {}
    no_key = 0
    for c in contracts:
        ek = getattr(getattr(c, "spec", None), "event_key", None)
        if not ek:
            no_key += 1
            continue
        groups.setdefault(ek, []).append(c)
    opps, refs = [], []
    same_market = 0
    for ek in sorted(groups):
        for a, b in combinations(groups[ek], 2):
            if (a.venue, a.market_id) == (b.venue, b.market_id):
                same_market += 1
                continue
            space = spaces.get(ek)
            if space is None:
                rec = _record("PAIR", None,
                              [_reason(OUTCOME_SPACE_NOT_PROVEN_EXHAUSTIVE,
                                       "no declared outcome space for %r" % ek)],
                              {"now": now.isoformat() if _aware(now) else _s(now),
                               "event_key": ek,
                               "legs": [[{"venue": a.venue, "market_id":
                                          a.market_id, "side": a.side}],
                                        [{"venue": b.venue, "market_id":
                                          b.market_id, "side": b.side}]],
                               "books": [], "skew_s": None}, None, None)
            else:
                rec = evaluate_pair(a, b, books, space, now, **kw)
            (opps if rec["verdict"] == GUARANTEED_AFTER_COSTS
             else refs).append(rec)
    census = census_of(opps + refs)
    census.update(contracts_without_event_key=no_key,
                  same_market_pairs_not_considered=same_market,
                  events=len(groups))
    return opps, refs, census


def census_of(records: Sequence[dict]) -> dict:
    by_verdict: dict[str, int] = {}
    by_code: dict[str, int] = {}
    by_primary: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for r in records:
        by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1
        k = r.get("structure_kind") or "UNCLASSIFIED"
        by_kind[k] = by_kind.get(k, 0) + 1
        codes = reason_codes(r)
        for code in set(codes):
            by_code[code] = by_code.get(code, 0) + 1
        if codes:
            by_primary[codes[0]] = by_primary.get(codes[0], 0) + 1
    return {"pairs_considered": len(records), "by_verdict": by_verdict,
            "by_refusal_code": by_code, "by_primary_refusal_code": by_primary,
            "by_structure_kind": by_kind}


# ═════════════════════════════════════════════════════════════════════
# BASKETS (futures, multi-outcome)
# ═════════════════════════════════════════════════════════════════════

def basket_solver(candidates: Sequence[Contract], books,
                  outcome_space: OutcomeSpace, now: datetime, *,
                  side: str = YES, **kw) -> dict:
    """Buy one YES (or NO) per outcome of an exhaustive, mutually exclusive
    outcome set, choosing at each size the cheapest of the payoff-equivalent
    candidates for that outcome. A YES basket pays exactly 1 in every
    outcome, a NO basket of N pays exactly N-1, VOID and POSTPONED
    included; anything else is refused. Exhaustiveness is the space's,
    proved or attested; a regular outcome no candidate covers is
    OUTCOME_NOT_COVERED."""
    kind = YES_BASKET if side == YES else NO_BASKET
    inputs = {"now": now.isoformat() if _aware(now) else _s(now),
              "event_key": getattr(outcome_space, "event_key", None),
              "candidates": [list(getattr(c, "key", ("?",))) for c in candidates],
              "side": side}
    reasons = []
    if side not in SIDES:
        reasons.append(_reason(SIDE_INVALID, repr(side)))
    else:
        reasons.extend(validate_space(outcome_space))
        for c in candidates:
            reasons.extend(validate_contract(c, outcome_space))
            if not reasons and c.side != side:
                reasons.append(_reason(SIDE_INVALID, "%s:%s:%s in a %s basket"
                                       % (c.key + (side,))))
    if reasons:
        return _record("BASKET", kind, reasons, inputs, None, None)
    regular = outcome_space.regular_outcomes
    by_outcome: dict[str, list[Contract]] = {}
    for c in candidates:
        vec = {o: _dec(c.payoff[o]) for o in outcome_space.outcomes}
        o = _indicator_of(vec, regular, flip=(side == NO))
        if o is None:
            reasons.append(_reason(PAYOFF_NOT_INDICATOR,
                                   "%s:%s:%s is not the %s of one outcome"
                                   % (c.key + (side,))))
            continue
        by_outcome.setdefault(o, []).append(c)
    uncovered = [o for o in regular if o not in by_outcome]
    if uncovered:
        reasons.append(_reason(OUTCOME_NOT_COVERED, "no candidate for %s"
                               % uncovered))
    if reasons:
        return _record("BASKET", kind, reasons, inputs, None, None)
    legs = [tuple(by_outcome[o]) for o in regular]
    rec = evaluate_structure(legs, books, outcome_space, now,
                             expect_kind=kind, **kw)
    rec["evaluation"] = "BASKET"
    rec["inputs"]["outcome_legs"] = {o: [list(c.key) for c in by_outcome[o]]
                                     for o in regular}
    return rec


# ═════════════════════════════════════════════════════════════════════
# LEG RISK: A PURE STATE MACHINE OF WHAT WOULD HAPPEN, IN SHADOW
# ═════════════════════════════════════════════════════════════════════
#
# LEG ORDER RULE. The THINNER, LESS RELIABLE leg goes first. If the hard
# leg fails nothing has been bought and the structure is abandoned at zero
# cost; if the easy leg went first and the hard one failed, the position is
# naked and must be completed at a worse price or unwound through a spread.
# Thinner = less executable depth at the planned price; ties go to the
# less reliable venue (lower reliability score), then to venue name so the
# order is deterministic.

PLANNED = "PLANNED"
LEG_A_WORKING = "LEG_A_WORKING"
LEG_A_PARTIAL = "LEG_A_PARTIAL"
LEG_A_FILLED = "LEG_A_FILLED"
LEG_B_WORKING = "LEG_B_WORKING"
LEG_B_PARTIAL = "LEG_B_PARTIAL"
MATCHED = "MATCHED"
UNHEDGED_EXPOSURE = "UNHEDGED_EXPOSURE"
RECOVERY_COMPLETE_LEG = "RECOVERY_COMPLETE_LEG"
RECOVERY_UNWIND = "RECOVERY_UNWIND"
CLOSED_MATCHED = "CLOSED_MATCHED"
CLOSED_UNWOUND = "CLOSED_UNWOUND"
ABORTED = "ABORTED"
STATES = (PLANNED, LEG_A_WORKING, LEG_A_PARTIAL, LEG_A_FILLED, LEG_B_WORKING,
          LEG_B_PARTIAL, MATCHED, UNHEDGED_EXPOSURE, RECOVERY_COMPLETE_LEG,
          RECOVERY_UNWIND, CLOSED_MATCHED, CLOSED_UNWOUND, ABORTED)
TERMINAL = (CLOSED_MATCHED, CLOSED_UNWOUND, ABORTED)

# events
EV_WORK = "WORK"                  # the leg's order is deemed working
EV_FILL = "FILL"                  # qty filled at price (fee optional)
EV_REJECT = "REJECT"
EV_TIMEOUT = "TIMEOUT"
EV_BOOK_MOVED = "BOOK_MOVED"      # new best ask on the leg
EV_RECOVER_COMPLETE = "RECOVER_COMPLETE"
EV_RECOVER_UNWIND = "RECOVER_UNWIND"
EV_UNWIND_FILL = "UNWIND_FILL"    # leg A sold back: qty at price (a bid)
EV_CLOSE = "CLOSE"
EV_ABORT = "ABORT"
EVENTS = (EV_WORK, EV_FILL, EV_REJECT, EV_TIMEOUT, EV_BOOK_MOVED,
          EV_RECOVER_COMPLETE, EV_RECOVER_UNWIND, EV_UNWIND_FILL, EV_CLOSE,
          EV_ABORT)


def choose_leg_order(x: dict, y: dict) -> tuple[dict, dict]:
    """(first, second) of two legs {venue, depth, reliability}: thinner
    first, then less reliable, then by venue name."""
    def k(leg):
        return (int(leg.get("depth") or 0),
                float(leg.get("reliability") if leg.get("reliability")
                      is not None else 0.0),
                str(leg.get("venue") or ""), str(leg.get("market_id") or ""))
    return (x, y) if k(x) <= k(y) else (y, x)


@dataclass(frozen=True)
class LegState:
    venue: str
    market_id: str
    side: str
    filled: int = 0
    cost: Decimal = ZERO              # sum(qty x price) bought
    fees: Decimal = ZERO
    unwound: int = 0
    unwind_proceeds: Decimal = ZERO   # sum(qty x price) sold, net of fees
    last_ask: Decimal | None = None

    def all_in_avg(self) -> Decimal | None:
        return None if self.filled == 0 else (self.cost + self.fees) / self.filled

    def basis_of(self, n: int) -> Decimal:
        """All-in cost basis of `n` of this leg's contracts. Multiplied
        before dividing, so a whole-leg basis is exact."""
        return ZERO if self.filled == 0 else \
            (self.cost + self.fees) * n / self.filled


@dataclass(frozen=True)
class Event:
    kind: str
    leg: str | None = None            # "A" | "B"
    qty: int = 0
    price: Decimal | None = None
    fee: Decimal = ZERO


@dataclass(frozen=True)
class Execution:
    state: str
    target_qty: int
    leg_a: LegState
    leg_b: LegState
    payout_per_set: Decimal = ONE
    history: tuple = ()


@dataclass(frozen=True)
class Transition:
    ok: bool
    execution: Execution
    refusal: dict | None = None


def plan_execution(record: dict, *, reliability: Mapping[str, float] | None = None
                   ) -> Execution:
    """A PLANNED execution for a GUARANTEED two-leg record, legs ordered by
    the leg order rule. Refuses anything else."""
    if record.get("verdict") != GUARANTEED_AFTER_COSTS:
        raise RefusedError(INVALID_TRANSITION, "only a %s record is planned"
                           % GUARANTEED_AFTER_COSTS)
    eco = record["economics"]
    legs = eco["legs"]
    if len(legs) != 2:
        raise RefusedError(INVALID_TRANSITION, "the leg machine is two-leg")
    rel = dict(reliability or {})
    cands = []
    for i, lg in enumerate(legs):
        cands.append({"venue": lg["venue"], "market_id": lg["market_id"],
                      "side": lg["side"],
                      "depth": eco["depth_by_leg"][i],
                      "reliability": rel.get(lg["venue"], 0.0),
                      "ask": Decimal(lg["levels_consumed"][0][0])})
    first, second = choose_leg_order(cands[0], cands[1])
    return Execution(
        state=PLANNED, target_qty=int(eco["qty"]),
        leg_a=LegState(first["venue"], first["market_id"], first["side"],
                       last_ask=first["ask"]),
        leg_b=LegState(second["venue"], second["market_id"], second["side"],
                       last_ask=second["ask"]),
        payout_per_set=Decimal(eco["floor_payout_per_set"]))


def exposure(ex: Execution) -> dict:
    """Matched and unhedged quantities, and worst-case money, right now.

    Worst case of an unhedged residual: it settles at 0, so the loss is its
    all-in cost basis. The matched part pays `payout_per_set` per set in
    every outcome, so its P&L is locked."""
    a, b = ex.leg_a, ex.leg_b
    held_a = a.filled - a.unwound
    matched = min(held_a, b.filled)
    un_a = held_a - matched
    un_b = b.filled - matched
    matched_pnl = matched * ex.payout_per_set - a.basis_of(matched) \
        - b.basis_of(matched)
    unwind_pnl = a.unwind_proceeds - a.basis_of(a.unwound)
    worst_un = a.basis_of(un_a) + b.basis_of(un_b)
    return {"matched_qty": matched, "unhedged_qty_a": un_a,
            "unhedged_qty_b": un_b,
            "worst_case_loss_unhedged": worst_un,
            "matched_locked_pnl": matched_pnl,
            "realized_unwind_pnl": unwind_pnl,
            "worst_case_total_pnl": matched_pnl + unwind_pnl - worst_un}


def _refuse(ex: Execution, code: str, detail: str) -> Transition:
    return Transition(False, ex, _reason(code, detail, state=ex.state))


def _go(ex: Execution, ev: Event, state: str, **changes) -> Transition:
    hist = ex.history + ((ex.state, ev.kind, ev.leg, ev.qty, _s(ev.price),
                          state),)
    return Transition(True, replace(ex, state=state, history=hist, **changes))


def _fill(leg: LegState, ev: Event) -> LegState:
    return replace(leg, filled=leg.filled + ev.qty,
                   cost=leg.cost + ev.qty * ev.price,
                   fees=leg.fees + (_dec(ev.fee) or ZERO))


def transition(ex: Execution, ev: Event) -> Transition:
    """One pure step. An event that is not valid in the current state is
    refused (INVALID_TRANSITION / OVERFILL) and the execution unchanged."""
    s = ex.state
    if s in TERMINAL:
        return _refuse(ex, INVALID_TRANSITION, "%s is terminal" % s)
    if ev.kind not in EVENTS or ev.leg not in (None, "A", "B"):
        return _refuse(ex, INVALID_TRANSITION, "unknown event %r" % (ev,))
    if ev.kind in (EV_FILL, EV_UNWIND_FILL):
        p = _dec(ev.price)
        if not isinstance(ev.qty, int) or isinstance(ev.qty, bool) \
                or ev.qty < 1 or p is None or not (ZERO < p < ONE) \
                or _dec(ev.fee) is None or _dec(ev.fee) < 0:
            return _refuse(ex, INVALID_TRANSITION, "malformed fill %r" % (ev,))
        ev = replace(ev, price=p)
    a, b = ex.leg_a, ex.leg_b
    k, leg = ev.kind, ev.leg

    if k == EV_ABORT:
        if s in (PLANNED, LEG_A_WORKING) and a.filled == 0:
            return _go(ex, ev, ABORTED)
        return _refuse(ex, INVALID_TRANSITION, "abort with inventory")
    if k == EV_BOOK_MOVED:
        p = _dec(ev.price)
        if leg not in ("A", "B") or p is None or not (ZERO < p < ONE):
            return _refuse(ex, INVALID_TRANSITION, "malformed book move")
        if leg == "A":
            return _go(ex, ev, s, leg_a=replace(a, last_ask=p))
        nb = replace(b, last_ask=p)
        if s in (LEG_B_WORKING, LEG_B_PARTIAL):
            breakeven = ex.payout_per_set - (a.all_in_avg() or ZERO)
            if p > breakeven:
                return _go(ex, ev, UNHEDGED_EXPOSURE, leg_b=nb)
        return _go(ex, ev, s, leg_b=nb)

    if s == PLANNED:
        if k == EV_WORK and leg == "A":
            return _go(ex, ev, LEG_A_WORKING)
    elif s in (LEG_A_WORKING, LEG_A_PARTIAL):
        if k == EV_FILL and leg == "A":
            if a.filled + ev.qty > ex.target_qty:
                return _refuse(ex, OVERFILL, "leg A beyond target")
            na = _fill(a, ev)
            return _go(ex, ev, LEG_A_FILLED if na.filled == ex.target_qty
                       else LEG_A_PARTIAL, leg_a=na)
        if k in (EV_REJECT, EV_TIMEOUT) and leg == "A":
            if a.filled == 0:
                return _go(ex, ev, ABORTED)
            # leg A stops short: the structure shrinks to what A holds
            return _go(ex, ev, LEG_A_FILLED, target_qty=a.filled)
    elif s == LEG_A_FILLED:
        if k == EV_WORK and leg == "B":
            return _go(ex, ev, LEG_B_WORKING)
    elif s in (LEG_B_WORKING, LEG_B_PARTIAL, RECOVERY_COMPLETE_LEG):
        if k == EV_FILL and leg == "B":
            if b.filled + ev.qty > a.filled - a.unwound:
                return _refuse(ex, OVERFILL, "leg B beyond leg A")
            nb = _fill(b, ev)
            if nb.filled == a.filled - a.unwound:
                return _go(ex, ev, CLOSED_MATCHED if s == RECOVERY_COMPLETE_LEG
                           else MATCHED, leg_b=nb)
            return _go(ex, ev, s if s == RECOVERY_COMPLETE_LEG
                       else LEG_B_PARTIAL, leg_b=nb)
        if k in (EV_REJECT, EV_TIMEOUT) and leg == "B":
            return _go(ex, ev, UNHEDGED_EXPOSURE)
    elif s == UNHEDGED_EXPOSURE:
        if k == EV_RECOVER_COMPLETE:
            return _go(ex, ev, RECOVERY_COMPLETE_LEG)
        if k == EV_RECOVER_UNWIND:
            return _go(ex, ev, RECOVERY_UNWIND)
    elif s == RECOVERY_UNWIND:
        if k == EV_UNWIND_FILL and leg == "A":
            un = exposure(ex)["unhedged_qty_a"]
            if ev.qty > un:
                return _refuse(ex, OVERFILL, "unwinding more than unhedged")
            na = replace(a, unwound=a.unwound + ev.qty,
                         unwind_proceeds=a.unwind_proceeds + ev.qty * ev.price
                         - (_dec(ev.fee) or ZERO))
            nex = replace(ex, leg_a=na)
            return _go(ex, ev, CLOSED_UNWOUND
                       if exposure(nex)["unhedged_qty_a"] == 0
                       else RECOVERY_UNWIND, leg_a=na)
        if k in (EV_REJECT, EV_TIMEOUT) and leg == "A":
            return _go(ex, ev, UNHEDGED_EXPOSURE)
    elif s == MATCHED:
        if k == EV_CLOSE:
            return _go(ex, ev, CLOSED_MATCHED)
    return _refuse(ex, INVALID_TRANSITION, "%s does not accept %s(%s)"
                   % (s, k, leg))


def recommend_recovery(ex: Execution, *, complete_ask, complete_fee,
                       unwind_bid, unwind_fee) -> dict:
    """Which recovery loses less for the WHOLE unhedged residual of leg A:
    buy leg B at `complete_ask` (locking payout_per_set per set) or sell
    leg A back at `unwind_bid`. Fees are for the whole residual order and
    must be supplied -- an unknown fee is not a zero fee."""
    if ex.state != UNHEDGED_EXPOSURE:
        raise RefusedError(INVALID_TRANSITION, "no unhedged exposure")
    u = exposure(ex)["unhedged_qty_a"]
    ca, cf, ub, uf = (_dec(complete_ask), _dec(complete_fee),
                      _dec(unwind_bid), _dec(unwind_fee))
    if None in (ca, cf, ub, uf):
        raise RefusedError(FEE_SCHEDULE_UNKNOWN, "recovery prices / fees")
    basis = ex.leg_a.basis_of(u)
    complete_pnl = ex.payout_per_set * u - basis - ca * u - cf
    unwind_pnl = ub * u - basis - uf
    choice = EV_RECOVER_COMPLETE if complete_pnl >= unwind_pnl \
        else EV_RECOVER_UNWIND
    return {"unhedged_qty": u, "complete_pnl": complete_pnl,
            "unwind_pnl": unwind_pnl, "choice": choice,
            "worst_case_if_nothing_done": -basis}
