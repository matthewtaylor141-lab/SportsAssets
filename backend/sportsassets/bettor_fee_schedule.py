"""The PUBLISHED Polymarket US fee schedules, by effective date.

    fee = theta x contracts x price x (1 - price)

WHAT WAS ACTUALLY WRONG, STATED PRECISELY.

I first wrote this file claiming the published schedule was newly
available and that "every prior evaluation used a hypothetical schedule".
The second half is true of the BETTOR engine. The first half is false,
and the correction matters more than the arithmetic.

`research/run85_trackb_fees.py` has carried the published schedule since
2026-07-01: the formula, both thetas, the sign convention, the rounding,
and reproductions of the venue documentation's own worked examples. It is
sourced, dated, and correct.

`bettor_decision_engine.Fees` never imported it. The engine ran on
`taker_per_contract=0.02, maker_per_contract=0.01` typed into three
scripts by hand, with the maker side entered AS A CHARGE.

So this was not missing information. It was an unconnected module -- the
same failure as `record_settlement()` being defined and never called, and
as my declaring `client.portfolio.activities` a reconciliation blocker
when it existed the whole time. Three instances now. The pattern is that
I check whether a capability is BUILT far less often than I assert that
it is MISSING, and each time the assertion was the expensive part.

TWO SCHEDULES, NOT ONE. The dates are part of the schedule:

    2026-07-01  theta_taker +0.06      per-fill independent rounding
    2026-09-17  theta_taker +0.0695    cumulative taker adjustment

    theta_maker -0.0125 in both        a REBATE in both

A fee applied to a fill on 2026-08-01 is a 2026-07-01 fee. `for_date()`
selects; there is no "current" default, because a schedule without a date
is how the 0.06/0.0695 distinction gets silently lost.

THE SIGN ERROR AND ITS SIZE. The engine subtracted +0.01 per contract on
the maker side. The published term at p=0.485 is
-0.0125 x 0.249775 = -0.00312 per contract RECEIVED. That is a swing of
0.0131 per contract in the maker's favour, and it is the single largest
input error in the evaluation so far.

A DIFFERENT FEE SCHEDULE DOES NOT VALIDATE AN EDGE. It corrects one term
in a model whose other terms -- p_fill and conditional_reference_move --
remain NOT_IDENTIFIED. Under the frozen v1 rules a candidate needing a
NOT_IDENTIFIED term is UNRESOLVED however the identified terms move.
Prior scenario results stay on the record as superseded assumptions
rather than being deleted, because "we changed one input and the sign
flipped" is a fact about the model's sensitivity worth keeping.

PUBLISHED IS NOT VERIFIED-AS-APPLIED. `PUBLISHED` means the venue
documents these terms. `VERIFIED_APPLIED` would mean we matched them
against a settled statement for THIS account. Nothing here is
VERIFIED_APPLIED. The engine's fee gate reads the difference: a
NOT_ESTABLISHED schedule blocks an action as an unknown cost, and a
PUBLISHED one lets the cash flow be COMPUTED and REPORTED but still not
SELECTED for execution.

NO VOLUME TIER IS ASSUMED. Published schedules commonly carry tiers; we
have established eligibility for none, so base theta is used and `tier`
stays NOT_ESTABLISHED. Assuming a discount we have not earned is the same
error as assuming a rebate we have not verified.

ROUNDING, AND A DISAGREEMENT BETWEEN THE TWO SOURCES.

  The 2026-07-01 documentation, as recorded in run85, says fees and
  rebates are "computed per fill INDEPENDENTLY" on BOTH sides.

  The 2026-09-17 terms specify a CUMULATIVE taker adjustment: the charge
  is computed on the order's total filled quantity and the amount already
  charged is deducted, so rounding does not accumulate across an order's
  fills. The maker side stays independent per fill.

  These are different rules for the taker side. Rather than overwrite the
  older one, each schedule carries its own `taker_rounding` and the
  disagreement is recorded in `describe()`. Which is right for a given
  fill is settled by a settled statement, not by preferring the newer
  document.

Independent per-fill rounding makes the rebate SUB-ADDITIVE: ten
1-contract fills can pay less than one 10-contract fill, and a small fill
can round its entire rebate to zero. Cumulative rounding does not have
that property, which is precisely the difference.

Cancelled and expired orders incur nothing on either schedule: fees and
rebates occur only on execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_EVEN

SOURCE = "https://docs.polymarket.us/fees"

# Schedule application status. The engine's fee gate reads these.
NOT_ESTABLISHED = "NOT_ESTABLISHED"
PUBLISHED = "PUBLISHED"
VERIFIED_APPLIED = "VERIFIED_APPLIED"

# Taker rounding policies, which is where the two schedules disagree.
PER_FILL_INDEPENDENT = "PER_FILL_INDEPENDENT"
CUMULATIVE_PER_ORDER = "CUMULATIVE_PER_ORDER"

CENT = Decimal("0.01")


def _d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def bankers_cents(x) -> Decimal:
    """Nearest $0.01, half to even -- the documented rounding."""
    return _d(x).quantize(CENT, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class Schedule:
    """One dated fee schedule. Sign convention is the venue's.

    Positive theta is a CHARGE, negative is a REBATE, and every function
    here returns that sign unchanged so a caller that adds fees adds a
    negative number for the maker side without having to remember which
    way it goes. The one place the magnitude is wanted instead is
    `maker_rebate_income()`, which says so in its name.
    """
    schedule_id: str
    effective_from: str
    theta_taker: Decimal
    theta_maker: Decimal
    taker_rounding: str
    maker_rounding: str = PER_FILL_INDEPENDENT
    status: str = PUBLISHED
    volume_tier: str = NOT_ESTABLISHED
    source: str = SOURCE

    # ── the arithmetic ───────────────────────────────────────────────
    def exact(self, theta, contracts, price) -> Decimal:
        """theta x C x p x (1-p), unrounded.

        The p(1-p) term peaks at 0.25 when p = 0.50 and falls to zero at
        both settlement bounds, so a contract at 0.02 is nearly free to
        trade and a coin-flip costs the most. A flat per-contract fee --
        which is what the engine used -- gets that shape wrong at every
        price, not just at one.

        It is also symmetric: exact(p) == exact(1-p). That is why a
        short leg may be evaluated at either spelling of its price. It
        does NOT make two legs of a pair equal, because they sit at
        different prices.
        """
        p = _d(price)
        return _d(theta) * _d(contracts) * p * (Decimal(1) - p)

    def taker_fee(self, contracts, price) -> Decimal:
        """Rounded taker CHARGE for one whole fill. Positive."""
        return bankers_cents(self.exact(self.theta_taker, contracts, price))

    def maker_rebate(self, contracts, price) -> Decimal:
        """Rounded maker REBATE for one fill, venue sign: NEGATIVE."""
        return bankers_cents(self.exact(self.theta_maker, contracts, price))

    def maker_rebate_income(self, contracts, price) -> Decimal:
        """The same number as a positive amount received."""
        return -self.maker_rebate(contracts, price)

    def fill_fee(self, contracts, price, *, maker: bool) -> Decimal:
        """One fill's fee under this schedule, signed.

        Charge positive, rebate negative. This is the single entry point
        the decision engine and shadow loop use, so neither has to
        branch on the sign convention.
        """
        return (self.maker_rebate(contracts, price) if maker
                else self.taker_fee(contracts, price))

    def min_contracts_for_a_cent(self, price) -> int:
        """Smallest single fill whose rebate does not round to zero.

        Half a cent is the boundary and banker's rounding sends exactly
        half to the even cent -- $0.00 -- so the threshold is strict.
        At p = 0.50 a contract earns $0.003125 and a fill needs about 2
        contracts; at p = 0.01 it earns $0.000124 and needs about 41.
        Below that a fill's entire rebate rounds away.
        """
        per = -self.exact(self.theta_maker, 1, price)
        if per <= 0:
            return 0
        c = 1
        while bankers_cents(per * c) < CENT:
            c += 1
            if c > 10 ** 7:
                return -1
        return c

    # ── accruals ─────────────────────────────────────────────────────
    def taker_accrual(self, price) -> "TakerAccrual":
        return TakerAccrual(schedule=self, price=_d(price))

    def maker_accrual(self, price) -> "MakerAccrual":
        return MakerAccrual(schedule=self, price=_d(price))

    def describe(self) -> dict:
        d = {
            "schedule_id": self.schedule_id,
            "effective_from": self.effective_from,
            "formula": "fee = theta x contracts x price x (1 - price)",
            "theta_taker": str(self.theta_taker),
            "theta_maker": str(self.theta_maker),
            "maker_is": "REBATE (negative theta)",
            "status": self.status,
            "verified_applied": self.status == VERIFIED_APPLIED,
            "verified_applied_requires": (
                "matching these terms against a settled statement for "
                "THIS account"),
            "volume_tier": self.volume_tier,
            "volume_tier_note": (
                "no tier eligibility has been established, so base theta "
                "is used; assuming a discount we have not earned is the "
                "same error as assuming a rebate we have not verified"),
            "rounding": {"unit": "0.01", "mode": "ROUND_HALF_EVEN",
                         "taker": self.taker_rounding,
                         "maker": self.maker_rounding},
            "source": self.source,
            "cancelled_orders": "incur nothing; fees occur only on execution",
        }
        if self.taker_rounding == CUMULATIVE_PER_ORDER:
            d["rounding_disagreement"] = (
                "the 2026-07-01 documentation records per-fill independent "
                "rounding on BOTH sides; these terms specify a cumulative "
                "taker adjustment. Both are recorded rather than one "
                "overwriting the other, and a settled statement decides "
                "which applied to a given fill")
        return d


# ── the dated schedules ──────────────────────────────────────────────

PMUS_2026_07_01 = Schedule(
    schedule_id="PMUS_PUBLISHED_2026_07_01",
    effective_from="2026-07-01",
    theta_taker=Decimal("0.06"),
    theta_maker=Decimal("-0.0125"),
    taker_rounding=PER_FILL_INDEPENDENT,
)

PMUS_2026_09_17 = Schedule(
    schedule_id="PMUS_PUBLISHED_2026_09_17",
    effective_from="2026-09-17",
    theta_taker=Decimal("0.0695"),
    theta_maker=Decimal("-0.0125"),
    taker_rounding=CUMULATIVE_PER_ORDER,
)

# Newest last. `for_date` walks it backwards.
SCHEDULES = (PMUS_2026_07_01, PMUS_2026_09_17)

LATEST = PMUS_2026_09_17


def for_date(iso_date: str) -> Schedule:
    """The schedule in force on `iso_date` (YYYY-MM-DD).

    There is deliberately no default date. A fee is a fact about WHEN it
    was charged, and a caller that cannot name the date is a caller that
    would silently apply 0.0695 to a July fill.
    """
    if not isinstance(iso_date, str) or len(iso_date) < 10:
        raise ValueError("a fee date must be an ISO date, got %r" % (iso_date,))
    day = iso_date[:10]
    chosen = None
    for s in SCHEDULES:
        if s.effective_from <= day:
            chosen = s
    if chosen is None:
        raise ValueError(
            "no published schedule covers %s; the earliest is %s. A fill "
            "before the first published schedule has no published fee and "
            "must not be priced at the nearest one"
            % (day, SCHEDULES[0].effective_from))
    return chosen


# ── accruals ─────────────────────────────────────────────────────────

@dataclass
class TakerAccrual:
    """Taker charges across ONE ORDER's fills.

    Under CUMULATIVE_PER_ORDER the charge is recomputed on the order's
    total filled quantity each time and the already-charged amount is
    deducted, so a partially filled order that fills again is not
    charged twice on the same contracts and rounding does not accumulate.
    Under PER_FILL_INDEPENDENT each fill is charged on its own.

    The difference is real money at small clip sizes: three 1-contract
    fills at p=0.50 on the 2026-09-17 terms cost $0.05 cumulatively and
    $0.06 independently.
    """
    schedule: Schedule
    price: Decimal
    filled: Decimal = Decimal("0")
    charged: Decimal = Decimal("0")
    fills: list = field(default_factory=list)

    def add_fill(self, contracts) -> Decimal:
        """The charge for THIS fill, after any cumulative adjustment."""
        self.filled += _d(contracts)
        if self.schedule.taker_rounding == CUMULATIVE_PER_ORDER:
            due = self.schedule.taker_fee(self.filled, self.price)
            delta = due - self.charged
        else:
            delta = self.schedule.taker_fee(contracts, self.price)
            due = self.charged + delta
        self.charged = due
        self.fills.append({"contracts": str(_d(contracts)),
                           "cumulative_filled": str(self.filled),
                           "cumulative_charged": str(due),
                           "charged_now": str(delta)})
        return delta


@dataclass
class MakerAccrual:
    """Maker rebates, each fill rounded INDEPENDENTLY by design.

    That is what makes the rebate sub-additive, and it is not a rounding
    detail to be smoothed over: multiplying a continuous rebate rate by
    total volume overstates income for small clips, and at low prices
    overstates it by the whole amount.
    """
    schedule: Schedule
    price: Decimal
    filled: Decimal = Decimal("0")
    credited: Decimal = Decimal("0")
    fills: list = field(default_factory=list)

    def add_fill(self, contracts) -> Decimal:
        """This fill's rebate, venue sign (negative = we receive)."""
        r = self.schedule.maker_rebate(contracts, self.price)
        self.filled += _d(contracts)
        self.credited += r
        self.fills.append({"contracts": str(_d(contracts)),
                           "rebate_this_fill": str(r)})
        return r


def describe() -> dict:
    """Every schedule, plus what this supersedes in the BETTOR engine."""
    return {
        "schedules": [s.describe() for s in SCHEDULES],
        "latest": LATEST.schedule_id,
        "no_default_date": ("for_date() requires a date; a schedule "
                            "without one loses the 0.06/0.0695 "
                            "distinction silently"),
        "prior_implementation": {
            "module": "research/run85_trackb_fees.py",
            "since": "2026-07-01",
            "note": ("the published schedule was already implemented and "
                     "sourced there. The BETTOR engine never imported it "
                     "and ran on hand-typed flat constants instead. This "
                     "was an unconnected module, not missing information"),
        },
        "supersedes_in_bettor_engine": {
            "hypothetical_taker_per_contract": "0.02 flat",
            "hypothetical_maker_per_contract": "+0.01 flat, AS A CHARGE",
            "shape_error": ("a flat per-contract fee is wrong at every "
                            "price, not merely mis-sized: the real term "
                            "is proportional to p(1-p)"),
            "sign_error": ("the maker side is a REBATE. At p=0.485 the "
                           "published term is -0.00312/contract received "
                           "where the engine subtracted +0.01: a swing of "
                           "0.0131/contract in the maker's favour"),
            "does_not_follow": ("a corrected fee schedule does not "
                                "validate an edge. p_fill and "
                                "conditional_reference_move remain "
                                "NOT_IDENTIFIED and a candidate needing a "
                                "NOT_IDENTIFIED term is UNRESOLVED"),
        },
    }
