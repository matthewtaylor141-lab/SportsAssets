"""PROFITABLE COMPLETION AND RATIONAL LOSS CONTAINMENT. FORWARD CASH ONLY.

Owner directive, "COMPLETE THE AUTONOMOUS TRADING SYSTEM" §3 and §4:

    §3 "Historical acquisition cost belongs in accounting; it must not
        force a worse decision because the system refuses to recognize
        a loss."

    §4 "Before entering a first leg estimate probability, cost and time
        of completion plus the one-leg-only outcome... Do not evaluate
        the policy only on completed pairs. Every initiated position
        must enter the results... Learn decision conditions rather
        than hardcoding $0.84 and $1.15."

────────────────────────────────────────────────────────────────────
THE ARITHMETIC REASON SUNK COST CANNOT ENTER A RANKING, WHICH IS ALSO
THE DIRECTIVE'S WORKED EXAMPLE.

§4 specifies a position and three actions and states what each is worth:

    held YES basis            $0.60
    qualified win probability   35%
    NO executable at          $0.55
    YES sellable at           $0.44

    complete the pair  -> locks a $0.15 loss
    sell the YES       -> realizes a $0.16 loss
    hold to settlement -> expected $0.25 loss

Every one of those three numbers is measured against the $0.60 already
spent. Written as FORWARD cash instead -- what each action pays from
here, ignoring what the position cost -- the same three actions are:

    complete  -$0.55 now, +$1.00 at settlement   = +$0.45
    sell      +$0.44 now                         = +$0.44
    hold      +$0.35 expected at settlement      = +$0.35

The two framings rank the actions IDENTICALLY, and they must, because
each forward value is exactly its loss framing plus the same $0.60:

    +0.45 - 0.60 = -0.15      +0.44 - 0.60 = -0.16      +0.35 - 0.60 = -0.25

That is the whole argument, and it is an identity rather than a
preference. Sunk cost is a CONSTANT added to every action in the set,
so it can never change which action is largest -- it can only change
whether the largest one looks like a profit. A system that ranks on the
loss framing gets the right answer; a system that REFUSES the largest
action because all three are losses gets a worse one. So this module
ranks on forward cash, reports the accounting loss beside it, and has a
test asserting the constant-shift identity directly.

    `rank` decides.            `accounting_view` reports.
    They never swap jobs.

────────────────────────────────────────────────────────────────────
EVERY INITIATED POSITION ENTERS THE RESULTS (§4).

The case studies show why this is not a bookkeeping nicety. Ferrari's
paired book returned +$10.8M gross while its 510,107,761 unpaired
residual shares returned -$9.5M, and the study says plainly that "this
directional result, not the pairing result, dominates the account's
total." RN1 is the same shape: +$9.9M paired against a residual of
$180.9M cost returning $184.1M.

An evaluation restricted to completed pairs would have reported both
accounts' pairing machinery as excellent -- which it is -- and missed
the term that decides the account. `Cohort` therefore takes every
INITIATION and closes it into exactly one of four dispositions, with
the count identity enforced rather than assumed.

────────────────────────────────────────────────────────────────────
NO PRICE THRESHOLD IS A DECISION RULE HERE (§4).

$0.84 and $1.15 are the case studies' OBSERVED average pair costs
(RN1 below-$1 $0.8484, above-$1 $1.1529; Ferrari $0.8328 / $1.1541).
They are four measurements of what two accounts paid, on two different
venues' books, over one window. Freezing either as a buy-below/
sell-above rule would encode another account's realised fills as our
forward economics. This module carries them as `OBSERVED_NOT_A_RULE`
and computes every decision from the CURRENT executable prices, the
current completion forecast and the current cost of capital.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

# ── actions this module prices. Names match bettor_ev_actions. ────────

COMPLETE_PAIR = "COMPLETE_PAIR"
DIRECT_EXIT = "DIRECT_EXIT"
HOLD_TO_SETTLEMENT = "HOLD_TO_SETTLEMENT"
HOLD = "HOLD"
NO_TRADE = "NO_TRADE"

IDENTIFIED = "IDENTIFIED"
NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ── the observed case-study averages, carried as measurements ─────────

OBSERVED_NOT_A_RULE = {
    "RN1_below_1_avg_pair_cost": 0.8484,
    "RN1_above_1_avg_pair_cost": 1.1529,
    "Ferrari_below_1_avg_pair_cost": 0.8328,
    "Ferrari_above_1_avg_pair_cost": 1.1541,
    "what_these_are": (
        "quantity-weighted averages of what two accounts actually paid "
        "for complete pairs, on the Polymarket global CLOB, over one "
        "observation window each, under a FIFO lot convention whose "
        "sensitivity case moves them (RN1 moving-average: $0.8682 / "
        "$1.1265; Ferrari: $0.8526 / $1.1270)"),
    "what_these_are_not": (
        "a threshold to buy below or sell above. They are outcomes of "
        "another account's fills, they move with the lot convention, and "
        "they carry no information about what is executable for us now"),
}


# ═════════════════════════════════════════════════════════════════════
# FORWARD VALUATION
# ═════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Position:
    """A held one-sided leg and what it cost. Basis is for ACCOUNTING."""
    contracts: float
    basis_per_contract: float
    leg: str = "YES"


@dataclass(frozen=True)
class Quotes:
    """Executable prices, not mid-marks.

    `complement_ask` is what we would PAY to buy the complement;
    `own_bid` is what we would RECEIVE selling the held leg. Either may
    be None -- an absent side is NOT a zero, it makes that action
    NOT_IDENTIFIED.
    """
    complement_ask: float | None = None
    own_bid: float | None = None
    complement_ask_size: float | None = None
    own_bid_size: float | None = None


@dataclass(frozen=True)
class Costs:
    """Everything that turns a gross comparison into a net one (§4)."""
    fee_per_contract_taker: float = 0.0
    fee_per_contract_maker: float = 0.0
    # Cost of the capital a settlement-carried position occupies, per
    # contract, over the expected hold. Not a discount rate: an explicit
    # charge, because the case studies' merge route released capital in
    # ~20 minutes and a settlement carry does not.
    capital_charge_per_contract: float = 0.0
    slippage_per_contract: float = 0.0


@dataclass
class ActionValue:
    action: str
    status: str
    forward_value: float | None = None      # cash from here, per contract
    cash_now: float | None = None
    cash_later_expected: float | None = None
    accounting_result: float | None = None  # forward - basis; REPORTING ONLY
    why: str = ""
    blocker: str | None = None
    net_of: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


def forward_values(pos: Position, q: Quotes, p_win: float | None,
                   *, costs: Costs | None = None,
                   gross: bool = False) -> list[ActionValue]:
    """Forward cash per contract for each eligible action.

    `gross=True` computes the directive's own before-costs comparison so
    the worked example is checkable against the numbers it states;
    production always calls with gross=False and a real `Costs`.
    """
    c = costs or Costs()
    fee = 0.0 if gross else c.fee_per_contract_taker
    slip = 0.0 if gross else c.slippage_per_contract
    carry = 0.0 if gross else c.capital_charge_per_contract
    out: list[ActionValue] = []

    # ── COMPLETE_PAIR: pay the complement, receive $1 at settlement ───
    if q.complement_ask is None:
        out.append(ActionValue(
            COMPLETE_PAIR, NOT_IDENTIFIED,
            blocker="no executable complement ask",
            why=("the complement's offer side is absent; an absent price "
                 "is not a free completion")))
    else:
        now = -(q.complement_ask + fee + slip)
        # A completed pair pays exactly $1 whichever way the event goes,
        # so this leg of the value is certain, not expected. It still
        # waits for settlement or a merge, so it still pays carry.
        later = 1.0 - carry
        out.append(ActionValue(
            COMPLETE_PAIR, IDENTIFIED,
            forward_value=now + later, cash_now=now,
            cash_later_expected=later,
            accounting_result=now + later - pos.basis_per_contract,
            why=("buy the complement at %.4f; the pair then pays $1.00 "
                 "regardless of the outcome" % q.complement_ask),
            net_of=() if gross else ("taker fee", "slippage",
                                     "capital charge")))

    # ── DIRECT_EXIT: sell the held leg now ───────────────────────────
    if q.own_bid is None:
        out.append(ActionValue(
            DIRECT_EXIT, NOT_IDENTIFIED,
            blocker="no executable bid on the held leg",
            why="nothing is bidding for this leg, so it cannot be sold"))
    else:
        now = q.own_bid - fee - slip
        out.append(ActionValue(
            DIRECT_EXIT, IDENTIFIED,
            forward_value=now, cash_now=now, cash_later_expected=0.0,
            accounting_result=now - pos.basis_per_contract,
            why="sell the held leg at %.4f and release the capital"
                % q.own_bid,
            net_of=() if gross else ("taker fee", "slippage")))

    # ── HOLD_TO_SETTLEMENT: the probability is the whole value ────────
    if p_win is None:
        out.append(ActionValue(
            HOLD_TO_SETTLEMENT, NOT_IDENTIFIED,
            blocker="no qualified win probability",
            why=("holding is worth p x $1 and nothing else; without a "
                 "qualified p there is no value, and 0 is not the "
                 "default")))
    elif not 0.0 <= p_win <= 1.0:
        out.append(ActionValue(
            HOLD_TO_SETTLEMENT, NOT_IDENTIFIED,
            blocker="p_win %r is not a probability" % (p_win,)))
    else:
        later = p_win * 1.0 - carry
        out.append(ActionValue(
            HOLD_TO_SETTLEMENT, IDENTIFIED,
            forward_value=later, cash_now=0.0, cash_later_expected=later,
            accounting_result=later - pos.basis_per_contract,
            why="carry to settlement: %.1f%% x $1.00" % (100 * p_win),
            net_of=() if gross else ("capital charge",)))
    return out


SUNK_COST_IDENTITY = (
    "every action's accounting_result is its forward_value minus the "
    "SAME basis, so basis is a constant added to the whole set and "
    "cannot change which action ranks first. It changes only whether "
    "the best action looks like a profit")


def rank(pos: Position, q: Quotes, p_win: float | None,
         *, costs: Costs | None = None,
         gross: bool = False) -> dict:
    """Rank the eligible actions on forward cash. Basis never enters."""
    vals = forward_values(pos, q, p_win, costs=costs, gross=gross)
    scored = [v for v in vals if v.status == IDENTIFIED]
    refused = [v for v in vals if v.status != IDENTIFIED]
    scored.sort(key=lambda v: v.forward_value, reverse=True)
    return {
        "ranked": [v.to_dict() for v in scored],
        "refused": [v.to_dict() for v in refused],
        "best": scored[0].action if scored else None,
        "ranked_on": "FORWARD_CASH_PER_CONTRACT",
        "basis_excluded_because": SUNK_COST_IDENTITY,
        "accounting_basis_per_contract": pos.basis_per_contract,
    }


def accounting_view(pos: Position, q: Quotes, p_win: float | None,
                    *, costs: Costs | None = None,
                    gross: bool = False) -> dict:
    """The same actions in loss terms. REPORTING. Never ranks anything."""
    vals = forward_values(pos, q, p_win, costs=costs, gross=gross)
    return {
        "basis_per_contract": pos.basis_per_contract,
        "results": {v.action: v.accounting_result
                    for v in vals if v.status == IDENTIFIED},
        "this_is": "REALISED_OR_EXPECTED_RESULT_AGAINST_HISTORICAL_BASIS",
        "this_is_not": "A_RANKING_INPUT",
    }


# ═════════════════════════════════════════════════════════════════════
# BEFORE THE FIRST LEG: WHAT COMPLETION IS WORTH BEFORE WE OWN ANYTHING
# ═════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CompletionForecast:
    """§4's three estimates, plus the branch where completion never comes.

    Each is required. A forecast missing p_completion is not a forecast
    with p_completion = 1; it is not a forecast.
    """
    p_completion: float | None = None
    expected_complement_cost: float | None = None
    expected_minutes_to_complete: float | None = None
    # The one-leg-only branch: what the unpaired leg is worth if the
    # complement never fills. This is the Ferrari residual term.
    p_win_if_unpaired: float | None = None
    expected_unpaired_exit_price: float | None = None
    unpaired_disposition: str | None = None   # SELL / CARRY / NOT_DECIDED
    # Adverse selection: the complement is likeliest to fill exactly
    # when the price has moved against the first leg, so the cost
    # CONDITIONAL ON completing exceeds the unconditional quote. A
    # forecast that ignores this prices the good branch at the bad
    # branch's price.
    adverse_selection_markup: float | None = None
    p_partial_fill: float | None = None
    expected_partial_fraction: float | None = None

    def missing(self) -> list[str]:
        gaps = []
        for name in ("p_completion", "expected_complement_cost",
                     "expected_minutes_to_complete", "p_win_if_unpaired",
                     "unpaired_disposition", "adverse_selection_markup"):
            if getattr(self, name) is None:
                gaps.append(name)
        if (self.unpaired_disposition == "SELL"
                and self.expected_unpaired_exit_price is None):
            gaps.append("expected_unpaired_exit_price")
        if self.p_completion is not None and not 0 <= self.p_completion <= 1:
            gaps.append("p_completion is not a probability")
        return gaps


def initiation_value(first_leg_price: float, f: CompletionForecast,
                     *, costs: Costs | None = None) -> dict:
    """EV of entering a first leg, over BOTH branches (§4).

    The completed branch and the one-leg-only branch are both priced and
    both reported. A policy evaluated on completed pairs alone is the
    error §4 names, and the case studies show it flips the sign.
    """
    gaps = f.missing()
    if gaps:
        return {"status": NOT_IDENTIFIED, "missing": gaps,
                "why": ("entering a first leg without the completion "
                        "forecast prices only the branch we hope for")}
    c = costs or Costs()
    entry = first_leg_price + c.fee_per_contract_taker + c.slippage_per_contract

    # Completed branch: pay the complement at the ADVERSELY SELECTED
    # cost, receive $1, pay carry for the time it was committed.
    comp_cost = (f.expected_complement_cost + f.adverse_selection_markup
                 + c.fee_per_contract_taker + c.slippage_per_contract)
    completed = 1.0 - comp_cost - c.capital_charge_per_contract

    # One-leg-only branch: the residual. This is the term that decided
    # both case-study accounts.
    if f.unpaired_disposition == "SELL":
        unpaired = (f.expected_unpaired_exit_price
                    - c.fee_per_contract_taker - c.slippage_per_contract)
    elif f.unpaired_disposition == "CARRY":
        unpaired = f.p_win_if_unpaired - c.capital_charge_per_contract
    else:
        return {"status": NOT_IDENTIFIED,
                "missing": ["unpaired_disposition is %r"
                            % (f.unpaired_disposition,)],
                "why": ("the residual's disposition is the term that "
                        "dominated both case-study accounts; it cannot "
                        "be left undecided")}

    p = f.p_completion
    ev = p * completed + (1 - p) * unpaired - entry

    # Partial fills: a partially completed pair is neither branch. It is
    # reported rather than averaged into one, because its capital and
    # its management obligation are both real.
    partial = None
    if f.p_partial_fill is not None and f.expected_partial_fraction is not None:
        partial = {
            "p": f.p_partial_fill,
            "expected_fraction_completed": f.expected_partial_fraction,
            "note": ("a partially completed pair holds a locked fraction "
                     "and an unpaired remainder simultaneously; both are "
                     "managed, and neither branch above describes it"),
        }

    return {
        "status": IDENTIFIED,
        "ev_per_contract": ev,
        "entry_cost": entry,
        "completed_branch": {"p": p, "value": completed,
                             "complement_cost_used": comp_cost,
                             "includes_adverse_selection":
                                 f.adverse_selection_markup},
        "one_leg_only_branch": {"p": 1 - p, "value": unpaired,
                                "disposition": f.unpaired_disposition},
        "expected_minutes_committed": f.expected_minutes_to_complete,
        "partial_fill": partial,
        "both_branches_priced": True,
        "not_established": ("that p_completion, the adverse-selection "
                            "markup or the unpaired exit price are "
                            "calibrated; initiation_value is arithmetic "
                            "over supplied estimates, and their "
                            "calibration is a separate measured result"),
    }


# ═════════════════════════════════════════════════════════════════════
# COHORT ACCOUNTING: EVERY INITIATION CLOSES SOMEWHERE (§4)
# ═════════════════════════════════════════════════════════════════════

D_COMPLETED = "COMPLETED_PAIR"
D_SOLD_UNPAIRED = "SOLD_UNPAIRED"
D_CARRIED_UNPAIRED = "CARRIED_UNPAIRED_TO_SETTLEMENT"
D_OPEN = "STILL_OPEN"
DISPOSITIONS = (D_COMPLETED, D_SOLD_UNPAIRED, D_CARRIED_UNPAIRED, D_OPEN)


@dataclass
class Cohort:
    """Every initiated position, closed into exactly one disposition."""
    initiated: int = 0
    by_disposition: dict = field(default_factory=lambda:
                                 {d: 0 for d in DISPOSITIONS})
    result_by_disposition: dict = field(default_factory=lambda:
                                        {d: 0.0 for d in DISPOSITIONS})

    def initiate(self, n: int = 1) -> None:
        self.initiated += n

    def close(self, disposition: str, result: float, n: int = 1) -> None:
        if disposition not in DISPOSITIONS:
            raise ValueError("unknown disposition %r" % (disposition,))
        self.by_disposition[disposition] += n
        self.result_by_disposition[disposition] += result

    def report(self) -> dict:
        closed = sum(self.by_disposition.values())
        completed_only = self.result_by_disposition[D_COMPLETED]
        total = sum(self.result_by_disposition.values())
        return {
            "initiated": self.initiated,
            "accounted_for": closed,
            "identity_holds": closed == self.initiated,
            "unaccounted": self.initiated - closed,
            "by_disposition": dict(self.by_disposition),
            "result_by_disposition": dict(self.result_by_disposition),
            "net_result_all_initiations": total,
            "result_on_completed_pairs_only": completed_only,
            "difference": total - completed_only,
            "why_the_difference_matters": (
                "Ferrari's paired book returned +$10.8M gross while its "
                "510,107,761 unpaired residual shares returned -$9.5M; "
                "the study states the directional result, not the "
                "pairing result, dominates the account's total. A policy "
                "scored on result_on_completed_pairs_only would have "
                "been right about the machine and wrong about the "
                "strategy"),
        }

    def assert_identity(self) -> None:
        r = self.report()
        if not r["identity_holds"]:
            raise AssertionError(
                "%d initiation(s) are not accounted for in any "
                "disposition; an evaluation over the remainder is an "
                "evaluation over a survivor-selected subset"
                % r["unaccounted"])


def describe() -> dict:
    return {
        "ranks_on": "FORWARD_CASH_PER_CONTRACT",
        "sunk_cost_identity": SUNK_COST_IDENTITY,
        "observed_not_a_rule": dict(OBSERVED_NOT_A_RULE),
        "dispositions": DISPOSITIONS,
        "worked_example": WORKED_EXAMPLE,
    }


# The directive's own example, kept beside the code that satisfies it so
# the numbers cannot drift apart silently.
WORKED_EXAMPLE = {
    "held_yes_basis": 0.60,
    "qualified_win_probability": 0.35,
    "no_executable_at": 0.55,
    "yes_sellable_at": 0.44,
    "before_costs": {
        "complete_locks": -0.15,
        "sell_realizes": -0.16,
        "hold_expected": -0.25,
    },
    "correct_order": (COMPLETE_PAIR, DIRECT_EXIT, HOLD_TO_SETTLEMENT),
    "then": ("apply actual fees, liquidity and risk limits; the gross "
             "ordering is the starting point, not the decision"),
}
