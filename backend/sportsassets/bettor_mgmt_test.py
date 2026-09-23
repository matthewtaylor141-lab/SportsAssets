"""THE COHORT-SEEDED MANAGEMENT TEST: one position, managed through time.

WHAT THIS DELIBERATELY REMOVES FROM THE PROBLEM. Independent entry
selection. Every policy is HANDED the same starting inventory, taken
from a cohort account's observed fill, so nothing here depends on an
independent EV engine that is not validated. What is being tested is
only what happens AFTERWARDS: complete, hold, reduce or exit.

That is the point. Entry selection needs independent fair value, which
measured WORSE than the venue price (P_BETTOR_INDEPENDENT_V3: blend
-0.00926 log loss, CI [-0.00222, +0.02036], NOT_DETECTED). Management
does not need it, because the position already exists. Separating them
is what makes a management result obtainable now rather than after a
research outcome nobody can schedule.

────────────────────────────────────────────────────────────────────
WHAT IS FACT HERE AND WHAT IS SCENARIO. The distinction is the whole
value of the exercise, so it is structural rather than a footnote.

  FACT   the seed: a cohort account's own executed fill -- price, size,
         side, instant. An on-chain execution.
  FACT   the subsequent prints on that condition: other accounts'
         executed trades, prices and sizes, in time order.
  FACT   the settlement payout: markets.resolved_prices, observed.
  FACT   the cohort benchmark: what that account itself did next.

  SCENARIO  every fill of OURS. We placed no orders. Our hypothetical
            order fills only when an observed print trades AT OR
            THROUGH its price, and then only for queue_share of that
            print's size. queue_share is an ASSUMPTION, not a
            measurement, so the runner sweeps it instead of picking one
            -- a single value would read as a result.
  SCENARIO  our exit price. `trades` holds EXECUTIONS, not quotes:
            there is no bid in this dataset. An exit is valued at the
            next observed print on our leg, which is an upper-ish bound
            on what we could have sold into and is NOT a bid.

P_FILL REMAINS NOT_IDENTIFIED AND THIS DOES NOT TOUCH IT. Nothing below
measures whether our order would have filled. A print-through scenario
at an assumed queue share is a way of asking "what would this policy
have done", not evidence about our execution. Reporting it as a fill
rate would be the substitution the standing directives forbid by name.

AND IT PROVES NOTHING ABOUT PROFITABILITY. A policy that wins on these
seeds has won on a sample selected by a rule declared below, under an
assumed execution model, on one venue, with no capacity constraint and
no adverse selection against our own presence in the book.
────────────────────────────────────────────────────────────────────

THE SEED RULE IS DECLARED BEFORE ANY OUTCOME IS READ, because a seed set
chosen after seeing which positions did well is not a test. It is stated
in SEED_RULE and asserted by a test that no outcome field appears in it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import bettor_mgmt_value as mv

VERSION = "BETTOR_MGMT_TEST_V1"

# THE SEED RULE. Fixed here, and it references no outcome, no payout and
# no profitability -- only structure and time.
SEED_RULE = {
    "id": "COHORT_SEEDED_MGMT_V1",
    "source": "trades (cohort fills) joined to markets.resolved_prices",
    "venue": "POLYMARKET_GLOBAL_POLYGON",
    "include": [
        "the account's FIRST fill on a condition, which becomes the "
        "assigned starting inventory",
        "the condition has resolved and resolved_prices is not null, so "
        "hold-to-settlement is a FACT rather than a forecast",
        "at least one subsequent print exists on that condition, "
        "otherwise there is nothing to manage through",
    ],
    "excludes_by_construction": [
        "no filter on the payout, the cohort's profit, or ours",
        "no filter on whether the position ended up in the money",
        "no filter on price movement after entry",
    ],
    "why_this_matters": (
        "a seed set chosen after seeing which positions did well is not "
        "a test of a policy, it is a description of a subset"),
}

EXEC_SCENARIO = "PRINT_THROUGH_WITH_QUEUE_SHARE_V1"
QUEUE_SHARES = (0.10, 0.25, 0.50)
P_FILL = "NOT_IDENTIFIED"


@dataclass
class Print:
    """One observed execution by someone else, on some leg of the
    condition. NOT our fill and never described as one."""
    at: float
    outcome_index: int
    side: str            # the printing account's side
    price: float
    size: float
    by_seed_account: bool = False


@dataclass
class Seed:
    condition_id: str
    outcome_index: int
    qty: float
    entry_price: float
    entry_at: float
    account: str
    # Observed settlement payout for EACH outcome index, so the
    # complement leg can be settled too when a policy acquired it.
    payouts: dict = field(default_factory=dict)
    resolved_at: float | None = None

    @property
    def basis_usd(self) -> float:
        return self.qty * self.entry_price


@dataclass
class Leg:
    qty: float = 0.0
    basis: float = 0.0


class Book:
    """The managed position. Deliberately minimal: the desk's Portfolio
    is the production accounting and this is the test's own arithmetic
    over one condition, kept separate so a bug here cannot reach the
    live ledger."""

    def __init__(self, seed: Seed):
        self.cash = 0.0
        self.fees = 0.0
        self.legs = {seed.outcome_index: Leg(seed.qty, seed.basis_usd)}
        # CAPITAL COMMITTED is the seed's cost plus anything bought
        # since. It is tracked as a PEAK because the question a capital
        # allocator asks is how much was tied up at the worst moment,
        # not on average.
        self.committed_peak = seed.basis_usd
        self.turnover = 0.0
        self.trace = []

    def _leg(self, oi) -> Leg:
        return self.legs.setdefault(oi, Leg())

    def buy(self, oi, qty, price, fee):
        lg = self._leg(oi)
        lg.qty += qty
        lg.basis += qty * price + fee
        self.cash -= qty * price + fee
        self.fees += fee
        self.turnover += qty * price
        self.committed_peak = max(self.committed_peak,
                                  sum(l.basis for l in self.legs.values()))

    def sell(self, oi, qty, price, fee):
        lg = self._leg(oi)
        if lg.qty <= 0:
            return 0.0
        qty = min(qty, lg.qty)
        released = lg.basis * (qty / lg.qty)
        lg.qty -= qty
        lg.basis -= released
        self.cash += qty * price - fee
        self.fees += fee
        self.turnover += qty * price
        return qty

    def settle(self, payouts: dict):
        """Residual inventory is settled at the OBSERVED payout, so the
        book ends with no unvalued inventory. That is only possible
        because these conditions have resolved; live, the residual would
        be NOT_IDENTIFIED and would have to be reported as such."""
        out = {}
        for oi, lg in sorted(self.legs.items()):
            if lg.qty <= 1e-12:
                continue
            p = payouts.get(oi)
            if p is None:
                out[oi] = {"qty": lg.qty, "basis": lg.basis,
                           "settled": None,
                           "why": "NO_OBSERVED_PAYOUT_FOR_THIS_LEG"}
                continue
            self.cash += p * lg.qty
            out[oi] = {"qty": lg.qty, "basis": lg.basis,
                       "settled": p * lg.qty, "payout": p}
            lg.qty, lg.basis = 0.0, 0.0
        return out


def _fill_qty(pr: Print, want: float, our_price: float, side: str,
              queue_share: float) -> float:
    """How much of OUR hypothetical order this print could have filled.

    THE ASYMMETRY IS THE MODEL. A print at or through our price is
    evidence that trading happened where we were; it is not evidence
    that it happened WITH US. queue_share is the declared fraction we
    claim of it, and it is an assumption every time.
    """
    if side == "SELL" and pr.price < our_price - 1e-12:
        return 0.0
    if side == "BUY" and pr.price > our_price + 1e-12:
        return 0.0
    return max(0.0, min(want, pr.size * queue_share))


# ── the declared policies ────────────────────────────────────────────
#
# Each is (name, description, decide). `decide` sees the book, the seed,
# the print, and a market view, and returns a request or None. They are
# frozen here: a policy edited after seeing its result is a new policy
# and must be given a new name.

def _mk_market(seed: Seed, pr: Print, comp_ask, comp_size) -> mv.Market:
    """The market view offered to the valuation at this instant.

    THE OBSERVED PAYOUT IS DELIBERATELY WITHHELD, and my first version of
    this function supplied it. That was LOOK-AHEAD: at the instant of
    this print the condition had not resolved, so a policy handed the
    settled payout is deciding with information it could not have had,
    and every comparison it makes against HOLD is contaminated. The
    payout is used in exactly one place -- `Book.settle`, to SCORE what
    the policy did -- and nowhere in deciding it.

    That withholding is what makes HOLD come back NOT_IDENTIFIED here,
    exactly as it does live. The difference between the historical and
    the live test is therefore NOT that the historical one knows the
    future. It is only that the historical one can score the outcome.

    `bid` IS NOT A BID. There are no quotes in this dataset. It is the
    price of the print we are standing at, on our own leg, and using it
    as an exit price is a declared scenario. Named `bid` only because
    that is the field the valuation reads; the label travels in the
    result.
    """
    own = pr.price if pr.outcome_index == seed.outcome_index else None
    return mv.Market(
        bid=own, bid_size=(pr.size if own is not None else 0.0),
        comp_ask=comp_ask, comp_ask_size=comp_size or 0.0,
        comp_source="OBSERVED" if comp_ask is not None else "ABSENT",
        payout=None, payout_source=mv.SETTLE_UNKNOWN)


def policy_hold(book, seed, pr, mk, qs, fee_fn):
    """BENCHMARK 1. Do nothing; carry to settlement. This is what the
    directive names as the thing to beat, and it is the honest incumbent
    for any position already held."""
    return None


def policy_cohort_mirror(book, seed, pr, mk, qs, fee_fn):
    """BENCHMARK 2, the OBSERVABLE COHORT BENCHMARK. Replicate what the
    seed account itself did next on this condition.

    IT IS A BENCHMARK, NOT AN ACHIEVABLE RETURN. Their fills were at
    their size, with their order policy, and WHALE_ORDER_POLICY is
    NOT_IDENTIFIED. Mirroring their executed price assumes we could have
    had it, which is exactly the substitution p_fill is missing.
    """
    if not pr.by_seed_account:
        return None
    if pr.side == "SELL" and pr.outcome_index == seed.outcome_index:
        return ("SELL", seed.outcome_index, pr.size, pr.price)
    if pr.side == "BUY":
        return ("BUY", pr.outcome_index, pr.size, pr.price)
    return None


def policy_value_ranked(book, seed, pr, mk, qs, fee_fn):
    """OURS. Act only when the valuation says an action beats HOLD.

    This is the one under test, and it is deliberately the thinnest
    possible policy over the valuation: no thresholds, no tuning, no
    parameters. Whatever it achieves is attributable to the valuation
    rather than to a rule fitted on top of it.
    """
    lg = book.legs.get(seed.outcome_index)
    if lg is None or lg.qty <= 1e-12:
        return None
    pos = mv.Position(condition_id=seed.condition_id,
                      outcome_index=seed.outcome_index,
                      qty=lg.qty, basis_usd=lg.basis)
    out = mv.compare(pos, mk, fee_fn=fee_fn)
    book.trace.append({"at": pr.at, "comparison": out})
    sel = out["selected"]
    if sel in (None, mv.HOLD):
        return None
    if sel == mv.EXIT_NOW and mk.bid is not None:
        return ("SELL", seed.outcome_index, lg.qty, mk.bid)
    if sel == mv.REDUCE and mk.bid is not None:
        return ("SELL", seed.outcome_index, lg.qty * 0.5, mk.bid)
    if sel == mv.COMPLETE_PAIR and mk.comp_ask is not None:
        other = 1 - seed.outcome_index
        return ("BUY", other, lg.qty, mk.comp_ask)
    return None


# THE HURDLE IS A RISK PREFERENCE AND IT IS DECLARED, NOT DISCOVERED.
# Completing a pair converts an uncertain payout into a certain one. It
# is therefore NOT EV-dominant over holding -- holding a leg that
# settles at 1.00 beats any completion -- and ranking the two needs the
# settlement view we do not have. What CAN be stated without a forecast
# is the locked-in gain itself, which is exact. So the rule is: take a
# certain gain of at least this many cents per contract, and say plainly
# that preferring certainty is a choice about risk rather than a claim
# about expected value.
COMPLETE_HURDLE_PER_CONTRACT = 0.01


def policy_complete_on_locked_gain(book, seed, pr, mk, qs, fee_fn):
    """OURS, AND THE ONE THAT CAN ACT WITHOUT A FORECAST.

    WHY IT DOES NOT GO THROUGH `mv.compare`. That comparison refuses to
    select anything when HOLD is unpriced, and HOLD is unpriced at every
    decision instant here because the payout is withheld. The refusal is
    right for actions that must be RANKED against holding. Completion is
    different in kind: its value is exact and contains no forecast, so
    it can be taken on its own terms -- provided the reason is a
    declared risk preference and not a smuggled view on settlement.

    IT IS NOT A FREE LUNCH AND MUST NOT BE REPORTED AS ONE. Locking
    +6.52 forgoes a hold worth anywhere in [-45, +55] on the same
    position. This policy will therefore LOSE to HOLD on seeds that
    settled in our favour and beat it on those that did not, and the
    honest summary of it is variance reduction at a cost in mean.
    """
    lg = book.legs.get(seed.outcome_index)
    if lg is None or lg.qty <= 1e-12:
        return None
    pos = mv.Position(condition_id=seed.condition_id,
                      outcome_index=seed.outcome_index,
                      qty=lg.qty, basis_usd=lg.basis)
    c = mv.value_complete_pair(pos, mk, fee_fn=fee_fn)
    book.trace.append({"at": pr.at, "action_considered": c.to_dict(),
                       "hold_priceable": False,
                       "why_not_ranked": (
                           "HOLD is NOT_IDENTIFIED at decision time -- "
                           "the payout is withheld as it would be live "
                           "-- so this is taken on a declared risk "
                           "preference, not on an EV ranking")})
    if c.status != mv.IDENTIFIED or c.ev_usd is None:
        return None
    if c.ev_usd < COMPLETE_HURDLE_PER_CONTRACT * lg.qty:
        return None
    return ("BUY", 1 - seed.outcome_index, lg.qty, mk.comp_ask)


POLICIES = (
    ("HOLD_TO_SETTLEMENT", "benchmark: carry the assigned inventory to "
     "the observed settlement", policy_hold),
    ("COHORT_MIRROR", "benchmark: do what the seed account did next",
     policy_cohort_mirror),
    ("VALUE_RANKED_V1", "act only when the management valuation prices "
     "an action above a priced HOLD -- which, with no settlement view, "
     "means it never acts. That degeneracy is a reported result",
     policy_value_ranked),
    ("COMPLETE_ON_LOCKED_GAIN_V1", "complete the pair when the locked-in "
     "gain clears a DECLARED per-contract hurdle; no forecast, and a "
     "stated preference for certainty over mean",
     policy_complete_on_locked_gain),
)


def run_one(seed: Seed, prints: list, *, policy, queue_share=0.25,
            fee_fn=None) -> dict:
    """Drive ONE seed through ONE policy over the observed prints."""
    name, desc, decide = policy
    book = Book(seed)
    acted = []
    for pr in sorted(prints, key=lambda p: p.at):
        if seed.resolved_at is not None and pr.at > seed.resolved_at:
            continue
        # The complement's price is only known when a print lands on the
        # complement leg. Otherwise it is ABSENT, and COMPLETE_PAIR is
        # refused by name rather than estimated.
        comp = (pr.price if pr.outcome_index != seed.outcome_index
                else None)
        mk = _mk_market(seed, pr, comp,
                        pr.size if comp is not None else 0.0)
        req = decide(book, seed, pr, mk, queue_share, fee_fn)
        if not req:
            continue
        side, oi, want, px = req
        got = _fill_qty(pr, want, px, side, queue_share)
        if got <= 0:
            acted.append({"at": pr.at, "wanted": side, "qty": want,
                          "price": px, "filled": 0.0,
                          "why": "NO_PRINT_THROUGH_OUR_PRICE"})
            continue
        fee = 0.0 if fee_fn is None else float(fee_fn(qty=got, price=px))
        if side == "SELL":
            got = book.sell(oi, got, px, fee)
        else:
            book.buy(oi, got, px, fee)
        acted.append({"at": pr.at, "side": side, "outcome_index": oi,
                      "qty": got, "price": px, "fee": fee,
                      "fill_basis": EXEC_SCENARIO})
    residual = book.settle(seed.payouts)
    return {
        "policy": name, "policy_description": desc,
        "queue_share": queue_share,
        "seed": {"condition_id": seed.condition_id,
                 "outcome_index": seed.outcome_index,
                 "qty": seed.qty, "entry_price": seed.entry_price,
                 "basis_usd": seed.basis_usd, "account": seed.account},
        # THE RESULT IS CASH RELATIVE TO THE BASIS WE WERE HANDED. The
        # seed's cost is not a decision any policy made, so charging it
        # to them equally is what makes the comparison about management.
        "net_usd": book.cash - seed.basis_usd,
        "cash_usd": book.cash, "fees_usd": book.fees,
        "turnover_usd": book.turnover,
        "committed_peak_usd": book.committed_peak,
        "actions": acted,
        "residual_settled": residual,
        "unvalued_residual": [oi for oi, r in residual.items()
                              if r.get("settled") is None],
        "decision_trace": book.trace,
        "execution_basis": EXEC_SCENARIO,
        "p_fill": P_FILL,
        "labels": {
            "our_fills": "SCENARIO -- we placed no orders",
            "exit_price": ("SCENARIO -- `trades` holds executions, not "
                           "quotes, so there is no bid. An exit is "
                           "priced at the next observed print on our "
                           "leg, which is not a bid"),
            "cohort_benchmark": ("OBSERVED, at their size and their "
                                 "order policy, which is not ours"),
            "settlement": "OBSERVED payout, not a forecast",
        },
    }


def run_seed_all_policies(seed: Seed, prints: list, *, fee_fn=None,
                          queue_shares=QUEUE_SHARES) -> dict:
    """Every declared policy on the IDENTICAL assigned inventory, across
    the queue-share sweep.

    THE SWEEP IS NOT DECORATION. queue_share is an assumption, and
    reporting one value would present an assumption as a result. A
    policy whose ranking against the benchmarks flips across the sweep
    has not been shown to work; that is a finding, not a footnote.
    """
    runs = []
    for qs in queue_shares:
        for pol in POLICIES:
            # HOLD is queue-share invariant by construction -- it never
            # sends an order -- so it is run once and reused, which also
            # makes an accidental dependence on qs visible if it ever
            # appears.
            runs.append(run_one(seed, prints, policy=pol,
                                queue_share=qs, fee_fn=fee_fn))
    by = {}
    for r in runs:
        by.setdefault(r["policy"], {})[r["queue_share"]] = r["net_usd"]
    hold = by.get("HOLD_TO_SETTLEMENT", {})
    ranking_stable = None
    if hold:
        beats = {p: [by[p][q] > hold[q] for q in sorted(hold)]
                 for p in by if p != "HOLD_TO_SETTLEMENT"}
        ranking_stable = {p: (all(v) or not any(v))
                          for p, v in beats.items()}
    return {
        "version": VERSION, "seed_rule": SEED_RULE,
        "runs": runs, "net_by_policy_by_queue_share": by,
        "ranking_stable_across_queue_share": ranking_stable,
        "ranking_note": ("False means the policy beats the benchmark at "
                         "some assumed queue shares and not others. That "
                         "is a result about the assumption, not about "
                         "the policy"),
        "does_not_establish": [
            "our fill probability -- P_FILL is NOT_IDENTIFIED and "
            "nothing here measures it",
            "profitability -- one seed set, one venue, an assumed "
            "execution model, no capacity limit and no adverse "
            "selection against our own presence",
            "that the cohort's prices were available to us",
        ],
    }
