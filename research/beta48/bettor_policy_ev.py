"""BETTOR policy EV — one accounting model, and policies that use it.

Pure stdlib. No database, no network, no credential. Not copied into the
deployment image (`backend/Dockerfile` copies `research/beta48/shadow`
and `research/beta48/*.json`, not this file).

WHAT THIS IS
    An executable statement of what BETTOR would do and what each action
    is worth, in cash, from the current state. Every action a policy can
    take is priced by ONE function, `incremental_ev`, so two actions are
    never compared on two different accounting bases.

WHAT IT REFUSES TO DO
    Invent an input. Three quantities are not identified from any
    evidence we hold:

        P_FILL              P(our resting quote fills | state, quote)
        AS_FILL             E[settlement - quote | FILLED, state]
        REBATE_ELIGIBILITY  whether a given fill earns the maker rebate

    An EV that depends on one of them returns NOT_IDENTIFIED naming it,
    or a BOUND with the assumption written into the result. It never
    returns a number with a guessed input inside.

THE FEE SCHEDULE IS VERIFIED, AND IT IS THE ONLY ONE USED
    PMUS, effective 2026-07-01:  fee = THETA * C * p * (1 - p)
        THETA_TAKER = +0.06     (charged)
        THETA_MAKER = -0.0125   (rebated)
    rounded to the cent, BANKER'S ROUNDING, PER FILL. At small clips the
    rounding is not a detail: a 1-contract fill at p = 0.445 earns a
    $0.00 rebate, and 10 contracts earn $0.03.

    The Kalshi schedule is NOT in this module. The only Kalshi number in
    the repository is our own constant, never read back from the venue.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

# ── the PMUS schedule: TWO REGIMES, and our capture straddles them ────
#
# VERIFIED 2026-09-22 against https://docs.polymarket.us/fees, whose five
# worked examples all use 0.0695:
#
#   Buy 1,000 @ 0.10  taker 0.0695 x 1000 x 0.10 x 0.90 = -$6.26
#   Buy 1,000 @ 0.50  taker 0.0695 x 1000 x 0.50 x 0.50 = -$17.38
#   maker on the same fills: 0.0125 x ... = +$1.12 / +$3.12
#
# THIS FILE HAD 0.06 -- the JUL2026 coefficient -- as a single constant.
# Every taker fee it computed after the cutover was 13.7% too small, and
# the policies that exit inventory as takers are exactly the ones that
# error favours. `forward/fees_v2.py` already carried the SEP2026
# coefficient with THETA_TAKER_SEP2026_VERIFIED = False; the docs page
# is that verification.
#
# THE CUTOVER MATTERS HERE SPECIFICALLY. The capture runs
# 2026-09-13 -> 2026-09-20 and the cutover is 2026-09-17T03:59Z, so a
# single constant is wrong for one side of the corpus whichever value
# it takes. `fee()` selects by timestamp.
THETA_TAKER_JUL2026 = 0.06
THETA_TAKER_SEP2026 = 0.0695
# THE BOUNDARY IS 00:00 EASTERN ON 2026-09-17, which in EDT (UTC-4) is
# 04:00:00Z -- NOT 03:59Z. Both this file and forward/fees_v2.py had
# 03:59, one minute early, so any fill in that minute was charged the
# new coefficient a minute before it applied. One minute of one day is
# a small error and it is still a wrong boundary.
REGIME_CUTOVER_EPOCH = 1789617600.0        # 2026-09-17T04:00:00+00:00
REGIME_CUTOVER_ISO = "2026-09-17T04:00:00+00:00"
REGIME_CUTOVER_LOCAL = "2026-09-17 00:00 America/New_York (EDT, UTC-4)"
THETA_TAKER = THETA_TAKER_SEP2026          # the current regime
THETA_MAKER = -0.0125                      # unchanged across both
FEE_EFFECTIVE_FROM = "2026-09-17"
FEE_SOURCE = "https://docs.polymarket.us/fees fetched 2026-09-22"


def theta_taker_at(at_epoch=None) -> float:
    """The taker coefficient in force at `at_epoch` (None = current)."""
    if at_epoch is None:
        return THETA_TAKER_SEP2026
    # AT the boundary is the NEW regime; strictly before it is the old.
    return (THETA_TAKER_SEP2026 if at_epoch >= REGIME_CUTOVER_EPOCH
            else THETA_TAKER_JUL2026)

# THE TICK IS PER MARKET, AND IT IS NOT ALWAYS A CENT. The venue carries
# `market.orderPriceMinTickSize` on the listing (verified 20/20 in the
# sealed PX1 records), and the recorded holdout shows 493 of 788 tight-
# cohort observations quoted on a HALF-cent grid -- e.g. 0.600 / 0.605 on
# aec-nfl-chi-car-2026-09-13. A hard-coded cent would misprice "one tick
# inside the spread" on 63% of this cohort, and would quote THROUGH the
# touch on a 0.005-wide book. `Book.tick` carries the market's own value;
# DEFAULT_TICK is only the fallback when the listing did not supply one.
DEFAULT_TICK = 0.01

# ── the three unidentified inputs, named once ──────────────────────────
U_P_FILL = "P_FILL"
U_AS_FILL = "ADVERSE_SELECTION_GIVEN_FILL"
U_REBATE = "REBATE_ELIGIBILITY"

NOT_IDENTIFIED = "NOT_IDENTIFIED"


def banker_cents(x: float) -> float:
    """Round a dollar amount to the cent, half-to-even, as the venue does."""
    cents = x * 100.0
    lo = math.floor(cents)
    frac = cents - lo
    if abs(frac - 0.5) < 1e-9:
        r = lo if lo % 2 == 0 else lo + 1
    else:
        r = math.floor(cents + 0.5)
    return r / 100.0


def fee(p: float, contracts: float, *, maker: bool,
        at_epoch: float | None = None) -> float:
    """Signed cash effect of the fee on ONE fill. Negative = we pay.

    `at_epoch` selects the taker regime. The maker coefficient is the
    same in both.
    """
    theta = THETA_MAKER if maker else theta_taker_at(at_epoch)
    raw = theta * contracts * p * (1.0 - p)
    return -banker_cents(raw)


def taker_fee_for_order(fills, *, at_epoch: float | None = None) -> float:
    """Total taker charge for ONE aggressive order that swept several
    resting orders. Negative = we pay.

    THE PUBLISHED RULE, verbatim from the fee page:

      "When an aggressive order fills against multiple resting orders,
       each fill is charged its banker's-rounded fee, adjusted so that
       the total commission collected across the order's fills never
       exceeds the banker's rounding of the cumulative exact fee. The
       adjustment can only reduce a fill's charge, never increase it.
       Maker rebates are computed per fill, independently."

    So a multi-level sweep is CAPPED at the rounding of the cumulative
    exact fee -- per-fill rounding alone over-charges. This is the only
    place that cap is applied; maker rebates stay per-fill and are not
    routed through here.

    `fills` is an iterable of (price, contracts).
    """
    theta = theta_taker_at(at_epoch)
    per_fill = sum(banker_cents(theta * n * p * (1.0 - p))
                   for p, n in fills)
    exact = sum(theta * n * p * (1.0 - p) for p, n in fills)
    return -min(per_fill, banker_cents(exact))


# ── state ──────────────────────────────────────────────────────────────
@dataclass
class Book:
    slug: str
    bid: float | None
    ask: float | None
    bid_qty: float | None = None
    ask_qty: float | None = None
    queue_ahead: float | None = None     # shares at our price, ahead of us
    tick: float | None = None            # market.orderPriceMinTickSize

    @property
    def t(self):
        """The market's own tick, never a global constant."""
        return self.tick if self.tick else DEFAULT_TICK

    @property
    def spread(self):
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    @property
    def mid(self):
        if self.bid is None or self.ask is None:
            return None
        return 0.5 * (self.bid + self.ask)


@dataclass
class Inventory:
    contracts: float = 0.0          # >0 long the YES leg
    avg_cost: float = 0.0           # cash paid per contract, fees included
    opened_at: float | None = None  # epoch seconds, for capital duration


@dataclass
class Verdict:
    """One action, priced. `ev` is None exactly when it is unidentified."""
    action: str
    ev: float | None
    identified: bool
    missing: list = field(default_factory=list)
    bound: str | None = None
    terms: dict = field(default_factory=dict)
    note: str = ""

    def to_dict(self):
        return {"action": self.action,
                "ev": NOT_IDENTIFIED if self.ev is None else round(self.ev, 6),
                "identified": self.identified,
                "missing": list(self.missing),
                "bound": self.bound,
                "terms": {k: (NOT_IDENTIFIED if v is None else v)
                          for k, v in self.terms.items()},
                "note": self.note}


# ── the one accounting model ───────────────────────────────────────────
def incremental_ev(action: str, book: Book, inv: Inventory, *,
                   contracts: float = 1.0,
                   fv: float | None = None,
                   as_fill: float | None = None,
                   p_fill: float | None = None,
                   rebate_eligible: bool | None = None) -> Verdict:
    """Incremental expected cash from `action`, FROM THE CURRENT STATE.

    `fv` is our own fair value for the YES leg, if we have one. It is
    NOT supplied by default: FV_BETTOR_INDEPENDENT is NOT_IDENTIFIED and
    the incremental-signal test came back NOT_DETECTED at the available
    sample size, so a caller that wants a directional number must pass
    one in and own it.

    Cash conventions: buying pays -p per contract, selling receives +p.
    Settlement pays +1 on the winning leg. Fees are signed by `fee()`.
    Nothing here double-counts the spread: a pair is priced as its two
    legs' cash flows, never as "the spread" plus the legs.
    """
    a = action.upper()
    t = {}

    if a == "DO_NOTHING":
        return Verdict(a, 0.0, True, terms={"cash": 0.0},
                       note="the baseline every other action is measured "
                            "against")

    if book.bid is None or book.ask is None:
        return Verdict(a, None, False, missing=["TWO_SIDED_BOOK"],
                       note="a one-sided book prices no action")

    # ── TAKER actions: fully identified except the future value ────────
    if a in ("CROSS_BUY", "CROSS_SELL"):
        px = book.ask if a == "CROSS_BUY" else book.bid
        sign = -1.0 if a == "CROSS_BUY" else +1.0
        cash = sign * px * contracts
        f = fee(px, contracts, maker=False)
        t.update(price=px, cash=round(cash, 6), fee=round(f, 6))
        if fv is None:
            return Verdict(a, None, False, missing=["FV_BETTOR_INDEPENDENT"],
                           terms=t,
                           note="crossing is priced exactly; what it is "
                                "WORTH needs a fair value, and BETTOR has "
                                "no identified one")
        terminal = (fv if a == "CROSS_BUY" else -fv) * contracts
        t["expected_terminal"] = round(terminal, 6)
        return Verdict(a, cash + f + terminal, True, terms=t,
                       note="identified ONLY because a fair value was "
                            "supplied by the caller")

    # ── the complementary TAKER pair: no future value needed at all ────
    if a == "CROSS_PAIR":
        # Buy YES at ask, buy NO at (1 - bid). One of them settles at 1.
        yes, no = book.ask, 1.0 - book.bid
        cost = (yes + no) * contracts
        f = fee(yes, contracts, maker=False) + fee(no, contracts,
                                                   maker=False)
        ev = 1.0 * contracts - cost + f
        t.update(yes_leg=yes, no_leg=round(no, 6),
                 pair_cost=round(cost, 6), payout=1.0 * contracts,
                 fees=round(f, 6), basis=round(yes + no, 6))
        return Verdict(a, ev, True, terms=t,
                       note="FULLY IDENTIFIED. ask + (1 - bid) = 1 + spread, "
                            "so the pair costs par plus the spread before a "
                            "fee is added. No fill model and no fair value "
                            "enters this line.")

    # ── MAKER actions: the binding unknowns live here ──────────────────
    if a in ("QUOTE_BID", "QUOTE_OFFER"):
        # Rest INSIDE the spread by one tick where there is room, else at
        # the touch. This is a quoting rule, not a prediction.
        # ROOM means STRICTLY more than one tick, with a tolerance:
        # 0.605 - 0.600 evaluates to 0.0050000000000000044, so a plain
        # `>` treats a book exactly one tick wide as having room and
        # quotes THROUGH the opposite touch.
        room = book.spread is not None and book.spread > book.t * 1.5
        if a == "QUOTE_BID":
            px = book.bid + book.t if room else book.bid
            sign = -1.0
        else:
            px = book.ask - book.t if room else book.ask
            sign = +1.0
        px = round(px, 6)
        t["quote_price"] = round(px, 6)
        t["tick_used"] = book.t
        t["at_touch"] = (px in (book.bid, book.ask))
        missing = []
        rebate = None
        if rebate_eligible is None:
            missing.append(U_REBATE)
        elif rebate_eligible:
            rebate = fee(px, contracts, maker=True)    # already + = received
            t["rebate_if_filled"] = round(rebate, 6)
        else:
            rebate = 0.0
            t["rebate_if_filled"] = 0.0
        if as_fill is None:
            missing.append(U_AS_FILL)
        else:
            t["adverse_selection_given_fill"] = as_fill
        if p_fill is None:
            missing.append(U_P_FILL)
        else:
            t["p_fill"] = p_fill
        if missing:
            return Verdict(
                a, None, False, missing=missing, terms=t,
                note="a resting quote earns nothing until it fills, and "
                     "what it earns WHEN it fills is conditional on the "
                     "flow that filled it. Neither is identified from any "
                     "evidence BETTOR holds.")
        # The economics of a fill are (settlement - price) on the side we
        # end up holding. That quantity IS `as_fill` by definition, so it
        # is used directly rather than reconstructed from the two legs --
        # reconstructing it is how the spread gets counted twice.
        per = as_fill * contracts + rebate
        t["value_if_filled"] = round(per, 6)
        return Verdict(a, p_fill * per, True, terms=t,
                       note="identified only because the caller supplied "
                            "p_fill, adverse selection and rebate "
                            "eligibility")

    if a == "CANCEL":
        return Verdict(a, 0.0, True, terms={"cash": 0.0},
                       note="cancelling a resting quote is cashless on "
                            "PMUS; its cost is the option value given up, "
                            "which is NOT_IDENTIFIED for the same reason "
                            "quoting is")

    # ── inventory actions ──────────────────────────────────────────────
    if a in ("HOLD", "REDUCE", "CLOSE", "COMPLETE_PAIR"):
        if inv.contracts == 0:
            return Verdict(a, 0.0, True, terms={"cash": 0.0},
                           note="no inventory; the action is a no-op")
        n = abs(inv.contracts) if a in ("CLOSE", "COMPLETE_PAIR") else min(
            contracts, abs(inv.contracts))
        if a == "HOLD":
            if fv is None:
                return Verdict(a, None, False,
                               missing=["FV_BETTOR_INDEPENDENT"],
                               note="holding is a bet that settlement beats "
                                    "the price we could sell at now; that "
                                    "needs a fair value")
            now = book.bid * n + fee(book.bid, n, maker=False)
            t.update(sell_now=round(now, 6), hold_value=round(fv * n, 6))
            return Verdict(a, fv * n - now, True, terms=t)
        if a in ("REDUCE", "CLOSE"):
            px = book.bid if inv.contracts > 0 else book.ask
            sign = +1.0 if inv.contracts > 0 else -1.0
            cash = sign * px * n
            f = fee(px, n, maker=False)
            t.update(price=px, contracts=n, cash=round(cash, 6),
                     fee=round(f, 6),
                     realised_vs_cost=round(cash + f - sign * inv.avg_cost * n,
                                            6))
            return Verdict(a, cash + f, True, terms=t,
                           note="crossing out is priced exactly; whether it "
                                "BEATS holding needs a fair value")
        # COMPLETE_PAIR: buy the complement so the pair settles at 1
        comp = 1.0 - book.bid if inv.contracts > 0 else book.ask
        cost = comp * n
        f = fee(comp, n, maker=False)
        ev = 1.0 * n - cost + f - abs(inv.avg_cost) * n
        t.update(complement_price=round(comp, 6), cost=round(cost, 6),
                 fees=round(f, 6), already_paid=round(inv.avg_cost * n, 6))
        return Verdict(a, ev, True, terms=t,
                       note="completing the pair is fully identified: the "
                            "payout is 1 per pair and both legs are priced. "
                            "It is NOT free money -- the first leg's cost "
                            "is already sunk and is subtracted here so the "
                            "spread is not counted twice.")

    return Verdict(a, None, False, missing=["UNKNOWN_ACTION"],
                   note="no accounting defined for %r" % action)


# ── policies ───────────────────────────────────────────────────────────
def policy_taker_pair(book: Book, inv: Inventory, **kw):
    """CLASS C. Cross both legs whenever the pair costs less than par.

    Fully decidable: it needs no fill model and no fair value.
    """
    v = incremental_ev("CROSS_PAIR", book, inv, **kw)
    if v.ev is not None and v.ev > 0:
        return "CROSS_PAIR", v
    return "DO_NOTHING", incremental_ev("DO_NOTHING", book, inv)


def policy_passive_maker(book: Book, inv: Inventory, **kw):
    """CLASSES A and B. Rest inside the spread, complete or close later.

    Returns DO_NOTHING whenever the EV of quoting is NOT_IDENTIFIED,
    which -- on today's evidence -- is always. A policy that cannot
    price its own action does not take it.
    """
    q = incremental_ev("QUOTE_OFFER", book, inv, **kw)
    if not q.identified:
        d = incremental_ev("DO_NOTHING", book, inv)
        d.note = ("REFUSED: quoting is unpriceable here (%s). Absence of "
                  "an EV is not evidence of a good one."
                  % ", ".join(q.missing))
        return "DO_NOTHING", d
    if q.ev > 0:
        return "QUOTE_OFFER", q
    return "DO_NOTHING", incremental_ev("DO_NOTHING", book, inv)


POLICIES = {"taker_pair": policy_taker_pair,
            "passive_maker": policy_passive_maker}


def describe() -> dict:
    return {
        "module": "BETTOR_POLICY_EV_V1",
        "fee_schedule": {"venue": "polymarket_us", "form": "THETA*C*p*(1-p)",
                         "theta_taker": THETA_TAKER,
                         "theta_maker": THETA_MAKER,
                         "rounding": "banker's, to the cent, per fill",
                         "effective_from": FEE_EFFECTIVE_FROM,
                         "status": "VERIFIED"},
        "unidentified_inputs": [U_P_FILL, U_AS_FILL, U_REBATE],
        "actions_priced": ["DO_NOTHING", "QUOTE_BID", "QUOTE_OFFER",
                           "CANCEL", "CROSS_BUY", "CROSS_SELL", "CROSS_PAIR",
                           "HOLD", "REDUCE", "COMPLETE_PAIR", "CLOSE"],
        "double_counting_guard": (
            "a pair is priced as its two legs' cash flows; the spread is "
            "never added as a separate term, and COMPLETE_PAIR subtracts "
            "the sunk first leg"),
        "tick": "PER MARKET, from market.orderPriceMinTickSize; DEFAULT_TICK=%s is a fallback only" % DEFAULT_TICK,
        "kalshi": "ABSENT -- the venue's schedule was never read back",
    }


if __name__ == "__main__":
    print(json.dumps(describe(), indent=2))
