#!/usr/bin/env python3
"""The shadow position-state recorder. BETTOR_EXIT_ENGINE_V1 section 5, in code.

WHY THIS FILE EXISTS.

The four-whale archive is aggregate. It records what each account DID and never
what the alternative was worth at the moment of the decision, and no amount of
further analysis recovers a per-position book state that was never written down.
`whale_exit_priors_v1.json` is the most that archive can yield, and its own
header says so:

    GRANULARITY            AGGREGATE
    PER_POSITION_ROWS      NOT_PRESENT
    HISTORICAL_BOOK_STATE  NOT_PRESENT
    EV_EXIT_HISTORICAL     NOT_IDENTIFIED

This module is the fix, and it only works FORWARD. From the first shadow
position onward every decision tick writes the book that priced it, every
feasible action, and the EV of every action WE DID NOT TAKE. `ACTIONS_NOT_CHOSEN`
is the field that makes the dataset worth having: it creates the counterfactual
prospectively instead of trying to manufacture it afterwards.

WHAT THIS FILE CANNOT DO, BY CONSTRUCTION.

    SHADOW_ONLY         YES
    ORDER_PATH_EXISTS   NO
    CREDENTIAL_PATH     NONE
    mirror_live         false

There is no order path here to switch off. This module contacts nothing at all:
it imports no HTTP client, it takes the book as an ARGUMENT from a caller that
did the public read, and `test_position_state.py` proves both facts by walking
the AST rather than by grepping for strings. (A substring scan trips on the
prose that states the guarantee. That has happened three times in this
programme; the structural test is the fix.)

WHAT IT WILL OUTPUT ON DAY ONE, stated in advance so the result cannot be
quietly reinterpreted later:

    ACTION_CHOSEN = A_NO_ACTION_RECORDED on essentially every tick,
    REASON = COMPARISON_NOT_IDENTIFIED.

That is the CORRECT behaviour, not a failure to launch. `EV_WAIT` depends on
`EXPECTED_CONTINUATION_VALUE`, which needs a fair value and a transition model,
and neither is measured. By the propagation rule a comparison containing a
NOT_IDENTIFIED term is not a comparison. The engine therefore records the state
in full and declines to act -- exercising every part of the machinery except the
one number nobody has yet earned the right to write down.
"""
from __future__ import annotations

import json
import math
import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "forward"))

NOT_IDENTIFIED = "NOT_IDENTIFIED"

SHADOW_ONLY = True
READ_ONLY = True
ORDER_CAPABLE = False
ORDER_PATH_EXISTS = False
CREDENTIAL_PATH = "NONE"
MICRO_LIVE_AUTHORIZED = False
mirror_live = False

# Every row written by this module carries this label. A shadow P&L is never
# reported as a realised P&L, and the label travels with the row rather than
# being applied by whoever reads it later.
COUNTERFACTUAL = "COUNTERFACTUAL"


# ---------------------------------------------------------------------------
# TIME_UNPAIRED -- a first-class state variable, on the frozen grid
# ---------------------------------------------------------------------------
#
# These bucket edges are NOT a formatting choice. They are the grid the whale
# priors were computed on, so a bucket label here addresses a lambda there
# directly. A 5-second leg and a 60-minute orphan differ by roughly two orders
# of magnitude in pairing intensity (first/last lambda ratio 58x-105x across the
# four accounts) and the engine is not permitted to average across that.
TIME_UNPAIRED_BUCKETS = (
    ("0s-5s", 0, 5),
    ("5s-10s", 5, 10),
    ("10s-30s", 10, 30),
    ("30s-60s", 30, 60),
    ("60s-120s", 60, 120),
    ("120s-300s", 120, 300),
    ("300s-600s", 300, 600),
    ("600s-1800s", 600, 1800),
    ("1800s-3600s", 1800, 3600),
    ("3600s-settlement", 3600, None),
)

# The last bucket has no well-defined elapsed duration -- "until settlement" is
# not a number of minutes -- so no lambda exists for it and none is invented.
NO_LAMBDA_BUCKET = "3600s-settlement"


def time_unpaired_bucket(seconds):
    """Which frozen bucket a leg's age falls in. Never interpolated."""
    if seconds is None or isinstance(seconds, str):
        return NOT_IDENTIFIED
    if seconds < 0:
        raise ValueError("TIME_UNPAIRED cannot be negative: %r" % (seconds,))
    for name, lo, hi in TIME_UNPAIRED_BUCKETS:
        if hi is None or (lo <= seconds < hi):
            return name
    raise AssertionError("unreachable: buckets do not cover %r" % (seconds,))


# ---------------------------------------------------------------------------
# THE DECISION STATE MACHINE
# ---------------------------------------------------------------------------
#
# STATE DETERMINES WHICH ACTIONS ARE FEASIBLE. ECONOMICS CHOOSES THE ACTION.
# No action is ever hard-coded from a state -- that is how a stop-loss gets
# written by accident, and section 8 of the architecture forbids it explicitly.
FRESH_UNPAIRED = "FRESH_UNPAIRED"
AGING_UNPAIRED = "AGING_UNPAIRED"
PAIR_AVAILABLE = "PAIR_AVAILABLE"
PASSIVE_EXIT_AVAILABLE = "PASSIVE_EXIT_AVAILABLE"
AGGRESSIVE_EXIT_AVAILABLE = "AGGRESSIVE_EXIT_AVAILABLE"
HEDGE_AVAILABLE = "HEDGE_AVAILABLE"
DIRECTIONAL_HOLD = "DIRECTIONAL_HOLD"
COMPLETED_PAIR = "COMPLETED_PAIR"
SETTLED = "SETTLED"

STATES = (FRESH_UNPAIRED, AGING_UNPAIRED, PAIR_AVAILABLE,
          "PASSIVE_PAIR_AVAILABLE", "AGGRESSIVE_PAIR_AVAILABLE",
          PASSIVE_EXIT_AVAILABLE, AGGRESSIVE_EXIT_AVAILABLE, HEDGE_AVAILABLE,
          DIRECTIONAL_HOLD, COMPLETED_PAIR, SETTLED)

FRESH_SECONDS = 60

# PHASE 2 EXTENSION: THE PASSIVE / AGGRESSIVE SPLIT.
#
# BETTOR IS MAKER-FIRST, so "pair" and "exit" are each TWO actions, not one.
# The distinction is not cosmetic -- it changes the economics in three places
# at once. Resting earns the spread and may earn a maker rebate; crossing pays
# the spread and a taker fee. Collapsing them would price a maker strategy at
# taker costs, or worse, credit a crossing trade with a rebate it never earned.
#
# The split also changes WHEN the action is available: a passive complement
# pair needs somewhere to rest, an aggressive one needs depth to cross, and a
# market can offer either, both, or neither.
A_PASSIVE_COMPLEMENT_PAIR = "A_PASSIVE_COMPLEMENT_PAIR"
A_AGGRESSIVE_COMPLEMENT_PAIR = "A_AGGRESSIVE_COMPLEMENT_PAIR"
A_WAIT = "A_WAIT"
A_PASSIVE_SELL_EXIT = "A_PASSIVE_SELL_EXIT"
A_AGGRESSIVE_SELL_EXIT = "A_AGGRESSIVE_SELL_EXIT"
A_HEDGE = "A_HEDGE"
A_DIRECTIONAL_HOLD = "A_DIRECTIONAL_HOLD"
A_SETTLEMENT_HOLD = "A_SETTLEMENT_HOLD"
A_REALIZE_AND_RECYCLE = "A_REALIZE_AND_RECYCLE"
A_HOLD_LOCKED_PAIR = "A_HOLD_LOCKED_PAIR"

ACTIONS = (A_PASSIVE_COMPLEMENT_PAIR, A_AGGRESSIVE_COMPLEMENT_PAIR, A_WAIT,
           A_PASSIVE_SELL_EXIT, A_AGGRESSIVE_SELL_EXIT, A_HEDGE,
           A_DIRECTIONAL_HOLD, A_SETTLEMENT_HOLD, A_REALIZE_AND_RECYCLE,
           A_HOLD_LOCKED_PAIR)

PASSIVE_ACTIONS = (A_PASSIVE_COMPLEMENT_PAIR, A_PASSIVE_SELL_EXIT)
AGGRESSIVE_ACTIONS = (A_AGGRESSIVE_COMPLEMENT_PAIR, A_AGGRESSIVE_SELL_EXIT)

PASSIVE_PAIR_AVAILABLE = "PASSIVE_PAIR_AVAILABLE"
AGGRESSIVE_PAIR_AVAILABLE = "AGGRESSIVE_PAIR_AVAILABLE"

# What each state MAKES POSSIBLE. Not what it recommends.
FEASIBLE_BY_STATE = {
    FRESH_UNPAIRED: (A_WAIT, A_SETTLEMENT_HOLD),
    AGING_UNPAIRED: (A_WAIT, A_SETTLEMENT_HOLD),
    PAIR_AVAILABLE: (A_AGGRESSIVE_COMPLEMENT_PAIR,),
    PASSIVE_PAIR_AVAILABLE: (A_PASSIVE_COMPLEMENT_PAIR,),
    AGGRESSIVE_PAIR_AVAILABLE: (A_AGGRESSIVE_COMPLEMENT_PAIR,),
    PASSIVE_EXIT_AVAILABLE: (A_PASSIVE_SELL_EXIT,),
    AGGRESSIVE_EXIT_AVAILABLE: (A_AGGRESSIVE_SELL_EXIT,),
    HEDGE_AVAILABLE: (A_HEDGE,),
    DIRECTIONAL_HOLD: (A_DIRECTIONAL_HOLD,),
    COMPLETED_PAIR: (A_REALIZE_AND_RECYCLE, A_HOLD_LOCKED_PAIR),
    SETTLED: (),
}


def states(obs):
    """The feasible-state set for one observation. A position is in SEVERAL.

    `PAIR_AVAILABLE` and `AGING_UNPAIRED` are simultaneously true all the time;
    collapsing them to a single "the" state is how the pairing decision gets
    made by the labeller instead of by the economics.
    """
    if obs.get("SETTLED"):
        return (SETTLED,)
    if obs.get("PAIR_COMPLETE"):
        return (COMPLETED_PAIR,)

    out = []
    t = obs.get("TIME_UNPAIRED_S")
    if t is not None and not isinstance(t, str):
        out.append(FRESH_UNPAIRED if t <= FRESH_SECONDS else AGING_UNPAIRED)
    if obs.get("COMPLEMENT_EXECUTABLE_NOW"):
        out.append(PAIR_AVAILABLE)
        out.append(AGGRESSIVE_PAIR_AVAILABLE)
    if obs.get("COMPLEMENT_PASSIVE_PLACEABLE"):
        out.append(PASSIVE_PAIR_AVAILABLE)
    if obs.get("PASSIVE_EXIT_PLACEABLE"):
        out.append(PASSIVE_EXIT_AVAILABLE)
    if obs.get("AGGRESSIVE_EXIT_DEPTH_EXISTS"):
        out.append(AGGRESSIVE_EXIT_AVAILABLE)
    if obs.get("HEDGE_INSTRUMENT_EXECUTABLE"):
        out.append(HEDGE_AVAILABLE)
    # DIRECTIONAL_HOLD is the state of having no feasible pair OR exit. It is
    # NOT a decision to run the position directionally -- that decision is
    # A_DIRECTIONAL_HOLD, and it is taken by the allocator on economics.
    if not ({PAIR_AVAILABLE, PASSIVE_PAIR_AVAILABLE, AGGRESSIVE_PAIR_AVAILABLE,
             PASSIVE_EXIT_AVAILABLE, AGGRESSIVE_EXIT_AVAILABLE,
             HEDGE_AVAILABLE} & set(out)):
        out.append(DIRECTIONAL_HOLD)
    return tuple(out)


def feasible_actions(state_set):
    """Union of what the states allow, in the canonical ACTIONS order."""
    allowed = set()
    for s in state_set:
        if s not in FEASIBLE_BY_STATE:
            raise KeyError("unknown state: %r" % (s,))
        allowed |= set(FEASIBLE_BY_STATE[s])
    return tuple(a for a in ACTIONS if a in allowed)


# ---------------------------------------------------------------------------
# FEASIBILITY IS THREE-VALUED. INFEASIBLE IS NOT UNKNOWN.
# ---------------------------------------------------------------------------
#
# An action that CANNOT PHYSICALLY OCCUR must not block the allocator. There
# is no hedge instrument, no complement is quoted, the market has settled --
# none of those is a missing number, and treating them as one would freeze the
# engine on the absence of a thing that does not exist.
#
# An action whose feasibility we DID NOT OBSERVE is a different claim, and it
# does block: we can neither rule it out nor price it, so the comparison is
# still incomplete. Absence of observation never becomes evidence -- the same
# rule that caught a cluster of missing start times being promoted because
# len({None}) == 1.
FEASIBLE = "FEASIBLE"
INFEASIBLE = "INFEASIBLE"

# What each conditional action needs the book to show.
PROBES = {
    A_PASSIVE_COMPLEMENT_PAIR: "COMPLEMENT_PASSIVE_PLACEABLE",
    A_AGGRESSIVE_COMPLEMENT_PAIR: "COMPLEMENT_EXECUTABLE_NOW",
    A_PASSIVE_SELL_EXIT: "PASSIVE_EXIT_PLACEABLE",
    A_AGGRESSIVE_SELL_EXIT: "AGGRESSIVE_EXIT_DEPTH_EXISTS",
    A_HEDGE: "HEDGE_INSTRUMENT_EXECUTABLE",
}

# Actions that need no counterparty. Holding, waiting and carrying a leg to
# settlement are always physically available on an open unpaired position.
UNCONDITIONAL_UNPAIRED = (A_WAIT, A_DIRECTIONAL_HOLD, A_SETTLEMENT_HOLD)


def feasibility(obs):
    """{action: FEASIBLE | INFEASIBLE | NOT_IDENTIFIED} for one observation."""
    out = {a: INFEASIBLE for a in ACTIONS}

    if obs.get("SETTLED"):
        # Terminal. Nothing can physically occur, and nothing is unknown
        # about that -- so nothing blocks either.
        return out

    if obs.get("PAIR_COMPLETE"):
        out[A_REALIZE_AND_RECYCLE] = FEASIBLE
        out[A_HOLD_LOCKED_PAIR] = FEASIBLE
        return out

    t = obs.get("TIME_UNPAIRED_S")
    known_age = t is not None and not isinstance(t, str)
    for a in UNCONDITIONAL_UNPAIRED:
        out[a] = FEASIBLE if known_age else NOT_IDENTIFIED

    for a, probe in PROBES.items():
        v = obs.get(probe, NOT_IDENTIFIED)
        if v is None or (isinstance(v, str) and v == NOT_IDENTIFIED):
            # NOT OBSERVED. Not "no". The engine must not conclude that a
            # complement is unavailable because nobody looked.
            out[a] = NOT_IDENTIFIED
        else:
            out[a] = FEASIBLE if v else INFEASIBLE
    return out


def actions_in_play(feas):
    """The actions the allocator must account for: FEASIBLE or unobserved.

    INFEASIBLE actions are dropped entirely -- they are not alternatives.
    """
    return tuple(a for a in ACTIONS if feas.get(a, INFEASIBLE) != INFEASIBLE)


# ---------------------------------------------------------------------------
# THE HAZARD PRIOR -- LEVEL_A evidence, read, never re-derived here
# ---------------------------------------------------------------------------

LEVEL_A = "LEVEL_A_DIRECT_WHALE_EVIDENCE"
LEVEL_B = "LEVEL_B_WHALE_DERIVED_PRIOR"
LEVEL_C = "LEVEL_C_BETTOR_PROSPECTIVE_ONLY"

CONSENSUS = "CROSS_WHALE_CONSENSUS"


def load_priors(path):
    """The priors artifact, as written by build_exit_priors.py."""
    return json.loads(Path(path).read_text())


class SubdistributionMisuse(RuntimeError):
    """Raised when a basis-ceiling lambda is asked for as though it were the
    hazard of a live unpaired leg."""


def lambda_for(priors, bucket, account=CONSENSUS, ceiling="any_basis",
               subdistribution_acknowledged=False):
    """The continuous hazard lambda for one bucket. NOT_IDENTIFIED where absent.

    `account=CONSENSUS` reads the risk-set-pooled cross-whale prior; naming an
    account reads that account's own series. The consensus excludes swisstony
    -- both facts live in the artifact, and this reader never re-averages.

    A BASIS CEILING IS REFUSED UNLESS EXPLICITLY ACKNOWLEDGED. For a ceiling B
    the archive's curve is

        F_B(t) = P(completed by t at basis <= B)

    whose denominator is every opening leg, so a position that ALREADY
    COMPLETED ABOVE B is still counted as eligible for a future <=B completion.
    It is not: it is gone. That makes the ceiling lambda a SUB-DISTRIBUTION
    quantity, systematically below the true cause-specific hazard, and feeding
    it to EV_WAIT would answer "how much of the original cohort eventually
    pairs cheaply" when the question asked was "will THIS live leg pair cheaply
    next interval". Use `p_complete_at_or_below_basis` instead.
    """
    if ceiling != "any_basis" and not subdistribution_acknowledged:
        raise SubdistributionMisuse(
            "basis-ceiling lambda is a SUBDISTRIBUTION quantity, not a "
            "cause-specific hazard. Use p_complete_at_or_below_basis(), or "
            "pass subdistribution_acknowledged=True for descriptive use.")
    if bucket == NOT_IDENTIFIED:
        return NOT_IDENTIFIED
    if account == CONSENSUS:
        rows = priors["CROSS_WHALE_CONSENSUS_PRIOR"]["HAZARD_any_basis"]
        field = "CONSENSUS_CONTINUOUS_HAZARD_LAMBDA"
        if ceiling != "any_basis":
            # The consensus is published at any_basis only. Asking it for a
            # basis ceiling it does not carry returns NOT_IDENTIFIED rather
            # than silently answering a different question.
            return NOT_IDENTIFIED
    else:
        acct = priors["ACCOUNT_PRIORS"].get(account)
        if acct is None:
            return NOT_IDENTIFIED
        block = acct["HAZARD_BY_BASIS_CEILING"].get(ceiling)
        if block is None:
            return NOT_IDENTIFIED
        rows = block["ROWS"]
        field = "CONTINUOUS_HAZARD_LAMBDA"
    for r in rows:
        if r.get("INTERVAL") == bucket:
            v = r.get(field, NOT_IDENTIFIED)
            return NOT_IDENTIFIED if isinstance(v, str) else v
    return NOT_IDENTIFIED


def p_complete_next_interval(lam, minutes):
    """P(complete within the next `minutes`) under a constant hazard lam.

        P = 1 - exp(-lambda * minutes)

    This is the ONLY place a lambda becomes a probability, and it is the reason
    lambda is the metric carried rather than H/minutes. H/minutes is a
    probability divided by a duration: not additive across intervals, and
    capable of exceeding 1.0/min, so it cannot be exponentiated. Lambda can.
    """
    if isinstance(lam, str) or lam is None:
        return NOT_IDENTIFIED
    if minutes is None or isinstance(minutes, str) or minutes <= 0:
        return NOT_IDENTIFIED
    if lam < 0:
        raise ValueError("a hazard rate cannot be negative: %r" % (lam,))
    return 1.0 - math.exp(-lam * minutes)


def p_basis_at_or_below(priors, bucket, ceiling, account="rn1"):
    """P(basis <= B | A COMPLETION HAPPENS IN THIS INTERVAL).

    A conditional on completion, read from the interval completion counts. It
    is a DIFFERENT object from the hazard: the hazard says whether a pairing
    happens at all, this says what quality it is if one does.
    """
    if bucket == NOT_IDENTIFIED or ceiling == "any_basis":
        return NOT_IDENTIFIED
    acct = priors.get("ACCOUNT_PRIORS", {}).get(account)
    if acct is None:
        return NOT_IDENTIFIED
    for cell in acct.get("BASIS_QUALITY_GIVEN_COMPLETION", []):
        if cell.get("INTERVAL") != bucket:
            continue
        block = cell.get("BY_CEILING", {}).get(ceiling)
        if not block:
            return NOT_IDENTIFIED
        v = block.get("P_BASIS_LE_B_GIVEN_COMPLETION_IN_INTERVAL",
                      NOT_IDENTIFIED)
        return NOT_IDENTIFIED if isinstance(v, str) else v
    return NOT_IDENTIFIED


def p_complete_at_or_below_basis(p_any, p_basis):
    """P(complete next interval AND basis <= B), decomposed conservatively.

        P(complete AND basis<=B) = P(complete) x P(basis<=B | complete)

    An EXACT identity, and both factors are separately identified -- the
    any-basis hazard from the risk set, the conditional from the interval
    completion counts. This replaces the subdistribution lambda everywhere a
    quality-conditioned completion probability is wanted.

    TIME-TO-COMPLETION AND COMPLETION-QUALITY STAY SEPARATE right up to this
    multiplication, which is the whole point: conflating them is what the
    ceiling curve does.
    """
    if isinstance(p_any, str) or isinstance(p_basis, str):
        return NOT_IDENTIFIED
    if p_any is None or p_basis is None:
        return NOT_IDENTIFIED
    if not (0.0 <= p_basis <= 1.0):
        raise ValueError("a conditional probability outside [0,1]: %r"
                         % (p_basis,))
    return p_any * p_basis


# ---------------------------------------------------------------------------
# EV -- with the propagation rule enforced, not documented
# ---------------------------------------------------------------------------

def _term(v):
    """One EV term as a Decimal, or NOT_IDENTIFIED. Floats are refused.

    A float here would reintroduce exactly the corruption the Decimal pipeline
    exists to prevent, and a money term is precisely where it matters.
    """
    if v is None or (isinstance(v, str) and v == NOT_IDENTIFIED):
        return NOT_IDENTIFIED
    if isinstance(v, float):
        raise TypeError(
            "refusing a float EV term: pass a Decimal or an exact decimal "
            "string. float(0.1) is not 0.1.")
    if isinstance(v, D):
        return v
    return D(str(v))


def ev_sum(terms):
    """Sum of named EV terms, NOT_IDENTIFIED if ANY of them is.

    THE PROPAGATION RULE, in one function so it cannot be implemented three
    slightly different ways. A NOT_IDENTIFIED term does not default to zero and
    does not silently drop out of the sum. A sum containing one is not a number,
    and this returns the fact rather than a number that looks measured.
    """
    total = D("0")
    missing = []
    for name, v in terms:
        t = _term(v)
        if t == NOT_IDENTIFIED:
            missing.append(name)
        else:
            total += t
    if missing:
        return NOT_IDENTIFIED, tuple(missing)
    return total, ()


def ev_pair_now(locked_pair_value=None, execution_costs=None,
                maker_rebate=None, verified_incentives=None,
                incremental_risk=None, capital_recycling_value=None):
    """EV of completing the pair at the currently executable complement.

    NEVER PAIR MERELY BECAUSE A PAIR EXISTS. This returns a number to be
    compared, not a recommendation; the allocator decides.

    `maker_rebate` is expected to be EXACTLY zero -- not a small number --
    whenever the clip is below MINIMUM_CLIP_FOR_NONZERO_REBATE(p). The rebate
    rounds to the cent per fill, so a one-contract maker fill earns $0.00.
    """
    return ev_sum((
        ("LOCKED_PAIR_VALUE", locked_pair_value),
        ("EXECUTION_COSTS", execution_costs),
        ("MAKER_REBATE", maker_rebate),
        ("VERIFIED_INCENTIVES", verified_incentives),
        ("INCREMENTAL_RISK", incremental_risk),
        ("CAPITAL_RECYCLING_VALUE", capital_recycling_value),
    ))


def ev_wait(p_complete, ev_if_completed=None,
            expected_continuation_value=NOT_IDENTIFIED,
            capital_opportunity_cost=NOT_IDENTIFIED,
            inventory_risk_cost=NOT_IDENTIFIED):
    """EV of waiting one more interval.

        EV_WAIT = P x EV_IF_COMPLETED
                + (1-P) x EXPECTED_CONTINUATION_VALUE
                - CAPITAL_OPPORTUNITY_COST
                - INVENTORY_RISK_COST

    P comes from the whale lambda -- this is the single point where the case
    study becomes machine logic. The other three default to NOT_IDENTIFIED
    because that is what they are in V1: EXPECTED_CONTINUATION_VALUE needs a
    fair value and a transition model, CAPITAL_OPPORTUNITY_COST needs an
    opportunity set BETTOR does not yet have, and INVENTORY_RISK_COST needs the
    fair value again. So EV_WAIT is NOT_IDENTIFIED in V1 shadow BY DERIVATION,
    and the engine says so instead of substituting a plausible number.
    """
    if isinstance(p_complete, str) or p_complete is None:
        return NOT_IDENTIFIED, ("P_COMPLETE_NEXT_INTERVAL",)
    if not (0.0 <= p_complete <= 1.0):
        raise ValueError("P_COMPLETE_NEXT_INTERVAL out of [0,1]: %r"
                         % (p_complete,))
    p = D(str(p_complete))

    completed = _term(ev_if_completed)
    cont = _term(expected_continuation_value)
    missing = []
    total = D("0")
    if completed == NOT_IDENTIFIED:
        missing.append("EV_IF_COMPLETED")
    else:
        total += p * completed
    if cont == NOT_IDENTIFIED:
        missing.append("EXPECTED_CONTINUATION_VALUE")
    else:
        total += (D("1") - p) * cont
    for name, v in (("CAPITAL_OPPORTUNITY_COST", capital_opportunity_cost),
                    ("INVENTORY_RISK_COST", inventory_risk_cost)):
        t = _term(v)
        if t == NOT_IDENTIFIED:
            missing.append(name)
        else:
            total -= t
    if missing:
        return NOT_IDENTIFIED, tuple(missing)
    return total, ()


def ev_simple(name, **terms):
    """The remaining actions' EVs, each a named sum under the same rule."""
    return ev_sum(tuple(terms.items()))


# ---------------------------------------------------------------------------
# THE ALLOCATOR
# ---------------------------------------------------------------------------

A_NO_ACTION_RECORDED = "A_NO_ACTION_RECORDED"

COMPARISON_NOT_IDENTIFIED = "COMPARISON_NOT_IDENTIFIED"
FEASIBILITY_NOT_IDENTIFIED = "FEASIBILITY_NOT_IDENTIFIED"
DOMINATES = "DOMINATES"
ROBUSTLY_DOMINATES = "ROBUSTLY_DOMINATES"
RISK_KILL = "RISK_KILL"
NO_FEASIBLE_ACTION = "NO_FEASIBLE_ACTION"

# ---------------------------------------------------------------------------
# EV INTERVALS AND ROBUST DOMINANCE -- DESIGNED, NOT ACTIVATED
# ---------------------------------------------------------------------------
#
# The current rule is deliberately blunt: one unpriced feasible alternative
# stops the whole comparison. It is correct and conservative, and it is not the
# end state, because it conflates "we know nothing about this action" with "we
# know this action's value lies in a range that cannot win".
#
# The better rule prices a BOUND where a point estimate is unavailable, and
# acts only on ROBUST DOMINANCE:
#
#     LOWER_BOUND(A) > MAX( UPPER_BOUND(every other action in play) )
#
# Then A may be chosen even though another action has no precise point
# estimate -- because no value inside that action's own bound could have beaten
# A. That is substantially smarter than either ignoring missing alternatives or
# never acting until everything is known exactly.
#
# IT STAYS OFF UNTIL THE BOUNDS THEMSELVES ARE VALIDATED. A dominance test run
# on bounds that are too narrow is not conservative at all -- it is the old
# error wearing an interval, and it would license trades on the strength of an
# upper bound nobody checked.
ROBUST_DOMINANCE_ACTIVE = False
BOUND_METHODOLOGY_VALIDATED = False

EV_PRICED = "PRICED"
EV_BOUNDED = "BOUNDED"
EV_INFEASIBLE = "INFEASIBLE"
EV_STATUSES = (EV_PRICED, EV_BOUNDED, NOT_IDENTIFIED, EV_INFEASIBLE)


def ev_cell(point=NOT_IDENTIFIED, lower=NOT_IDENTIFIED,
            upper=NOT_IDENTIFIED, status=None):
    """One action's EV as a point estimate AND a bound, with its status.

    Status is derived, not asserted, unless the caller names INFEASIBLE:
        a point estimate            -> PRICED
        both bounds, no point       -> BOUNDED
        anything less               -> NOT_IDENTIFIED
    A one-sided bound is NOT_IDENTIFIED: dominance needs both ends, and a lone
    lower bound would let an action with no ceiling look safe.
    """
    p, lo, hi = _term(point), _term(lower), _term(upper)
    if status == EV_INFEASIBLE:
        return {"EV_POINT_ESTIMATE": NOT_IDENTIFIED,
                "EV_LOWER_BOUND": NOT_IDENTIFIED,
                "EV_UPPER_BOUND": NOT_IDENTIFIED,
                "EV_STATUS": EV_INFEASIBLE}
    if lo != NOT_IDENTIFIED and hi != NOT_IDENTIFIED and lo > hi:
        raise ValueError("EV_LOWER_BOUND above EV_UPPER_BOUND: %s > %s"
                         % (lo, hi))
    if p != NOT_IDENTIFIED:
        st = EV_PRICED
    elif lo != NOT_IDENTIFIED and hi != NOT_IDENTIFIED:
        st = EV_BOUNDED
    else:
        st = NOT_IDENTIFIED
    return {"EV_POINT_ESTIMATE": p, "EV_LOWER_BOUND": lo,
            "EV_UPPER_BOUND": hi, "EV_STATUS": st}


def robust_dominance(cells, in_play, active=None):
    """Does one action's floor clear every other action's ceiling?

    Returns (action_or_None, reason). Refuses to answer while the bound
    methodology is unvalidated: the caller gets NOT_IDENTIFIED, not a verdict
    computed from bounds nobody has checked.
    """
    if active is None:
        active = ROBUST_DOMINANCE_ACTIVE
    if not active or not BOUND_METHODOLOGY_VALIDATED:
        return None, "ROBUST_DOMINANCE_INACTIVE_BOUND_METHODOLOGY_UNVALIDATED"
    best = None
    for a in in_play:
        lo = cells.get(a, {}).get("EV_LOWER_BOUND", NOT_IDENTIFIED)
        if lo == NOT_IDENTIFIED:
            continue
        ceilings = [cells.get(o, {}).get("EV_UPPER_BOUND", NOT_IDENTIFIED)
                    for o in in_play if o != a]
        if any(c == NOT_IDENTIFIED for c in ceilings):
            continue
        if not ceilings or lo > max(ceilings):
            best = a if best is None else best
    return (best, ROBUSTLY_DOMINATES) if best else (None,
                                                    COMPARISON_NOT_IDENTIFIED)


def allocate(evs, feasible, risk_kill=None, feas=None):
    """Choose among the feasible actions, or decline.

    Two rules, and the second is the one that matters today:

    1. A RISK KILL is the ONLY thing that acts without a complete comparison.
       It is a bound on loss, not an expectation, so it does not need one.
    2. Otherwise, if ANY feasible action's EV is NOT_IDENTIFIED, there is no
       comparison to make and the engine DECLINES. It does not fall back to the
       best identified EV -- an unpriced alternative could have dominated it,
       and choosing among the priced subset would quietly assume it did not.

    Returns (chosen, reason, ranked) where `ranked` prices every feasible
    action including the ones not chosen. That is `ACTIONS_NOT_CHOSEN`, and it
    is the whole point of the dataset.
    """
    if risk_kill:
        return risk_kill["ACTION"], RISK_KILL, tuple(
            (a, evs.get(a, (NOT_IDENTIFIED, ()))[0]) for a in feasible)
    if not feasible:
        return A_NO_ACTION_RECORDED, NO_FEASIBLE_ACTION, ()

    priced = []
    for a in feasible:
        v = evs.get(a, (NOT_IDENTIFIED, ("NOT_EVALUATED",)))[0]
        priced.append((a, v))

    # An action we could not even establish to be POSSIBLE is a different
    # blocker from an action we could not PRICE, and the row says which.
    # Neither is overridden, because neither can be ruled out.
    if feas is not None and any(feas.get(a) == NOT_IDENTIFIED
                                for a in feasible):
        return A_NO_ACTION_RECORDED, FEASIBILITY_NOT_IDENTIFIED, tuple(priced)

    if any(v == NOT_IDENTIFIED for _, v in priced):
        return A_NO_ACTION_RECORDED, COMPARISON_NOT_IDENTIFIED, tuple(priced)

    best = max(priced, key=lambda kv: kv[1])
    # A tie is not a decision. Ties go to the action that changes nothing,
    # which is the same rule the execution-mode selector uses (NO_TRADE wins
    # ties) and for the same reason.
    if sum(1 for _, v in priced if v == best[1]) > 1:
        return A_NO_ACTION_RECORDED, COMPARISON_NOT_IDENTIFIED, tuple(priced)
    return best[0], DOMINATES, tuple(priced)


# ---------------------------------------------------------------------------
# THE ROW
# ---------------------------------------------------------------------------

DECISION_ROW_FIELDS = (
    "TIMESTAMP", "ENTRY_STATE", "CURRENT_BOOK", "COMPLEMENT_BOOK",
    "PAIR_BASIS", "FAIR_VALUE", "ALL_FEASIBLE_EXIT_PRICES", "QUEUE_AHEAD",
    "TRADE_ACTIVITY", "TIME_UNPAIRED", "EVENT_STATE", "CAPITAL_OCCUPANCY",
    "ALL_ACTION_EVS", "ACTION_CHOSEN", "ACTIONS_NOT_CHOSEN", "LATER_OUTCOME",
)


def _jsonable(o):
    """Decimal -> its EXACT decimal string. Never a float.

    Same rule and same reason as the census writer: a money term computed in
    Decimal that is written as a float has already lost the value we measured.
    """
    if isinstance(o, D):
        return str(o)
    raise TypeError("not JSON serializable: %s" % type(o).__name__)


CANONICAL_NUMERIC_PIPELINE = "DECIMAL -> EXACT_DECIMAL_STRING -> DECIMAL"


def decision_row(obs, priors, account=CONSENSUS, ceiling="any_basis",
                 horizon_minutes=None, evs=None, risk_kill=None,
                 quality_account="rn1"):
    """One decision tick, fully recorded, whether or not anything is done.

    `obs` carries the book the caller read publicly; this function contacts
    nothing. `evs` maps an action to an (value, missing_terms) pair as returned
    by the ev_* functions above; actions left out are priced NOT_IDENTIFIED with
    reason NOT_EVALUATED, which is a different claim from zero.
    """
    t = obs.get("TIME_UNPAIRED_S")
    bucket = time_unpaired_bucket(t)
    # THE HAZARD IS ALWAYS THE ANY-BASIS ONE. `ceiling` never selects a
    # lambda here; it selects the conditional quality factor, which is a
    # separate object multiplied in afterwards.
    lam = lambda_for(priors, bucket, account=account, ceiling="any_basis")
    p = p_complete_next_interval(lam, horizon_minutes)
    p_q = p_basis_at_or_below(priors, bucket, ceiling, account=quality_account)
    p_joint = p_complete_at_or_below_basis(p, p_q)

    state_set = states(obs)
    fmap = feasibility(obs)
    feas = actions_in_play(fmap)
    evs = dict(evs or {})
    chosen, reason, ranked = allocate(evs, feas, risk_kill=risk_kill,
                                      feas=fmap)

    row = {
        "LABEL": COUNTERFACTUAL,
        "ORDER_PATH_EXISTS": "NO",
        "TIMESTAMP": obs.get("TIMESTAMP", NOT_IDENTIFIED),
        "ENTRY_STATE": obs.get("ENTRY_STATE", NOT_IDENTIFIED),
        "CURRENT_BOOK": obs.get("CURRENT_BOOK", NOT_IDENTIFIED),
        "COMPLEMENT_BOOK": obs.get("COMPLEMENT_BOOK", NOT_IDENTIFIED),
        "PAIR_BASIS": obs.get("PAIR_BASIS", NOT_IDENTIFIED),
        "FAIR_VALUE": obs.get("FAIR_VALUE", NOT_IDENTIFIED),
        "FV_BASIS": obs.get("FV_BASIS", NOT_IDENTIFIED),
        "FV_CONFIDENCE": obs.get("FV_CONFIDENCE", NOT_IDENTIFIED),
        "ALL_FEASIBLE_EXIT_PRICES": obs.get("ALL_FEASIBLE_EXIT_PRICES",
                                            NOT_IDENTIFIED),
        "QUEUE_AHEAD": obs.get("QUEUE_AHEAD", NOT_IDENTIFIED),
        # Count AND elapsed, together. A recency alone is not a rate, and the
        # north-star metric must never be computed from one as though it were.
        "TRADE_ACTIVITY": obs.get("TRADE_ACTIVITY", NOT_IDENTIFIED),
        "TIME_UNPAIRED": {
            "SECONDS": t if t is not None else NOT_IDENTIFIED,
            "BUCKET": bucket,
            "CONTINUOUS_HAZARD_LAMBDA": lam,
            "LAMBDA_SOURCE": account,
            "LAMBDA_BASIS_CEILING": ceiling,
            "LAMBDA_EVIDENCE_LEVEL": LEVEL_B if account == CONSENSUS
                                     else LEVEL_A,
            "HORIZON_MINUTES": (horizon_minutes if horizon_minutes is not None
                                else NOT_IDENTIFIED),
            "P_COMPLETE_NEXT_INTERVAL": p,
            # Quality is a SEPARATE factor, never folded into the hazard.
            "BASIS_CEILING_ASKED": ceiling,
            "P_BASIS_LE_B_GIVEN_COMPLETION": p_q,
            "P_COMPLETE_AND_BASIS_LE_B": p_joint,
            "P_JOINT_IS_A_DECOMPOSITION_NOT_A_SUBDISTRIBUTION_LAMBDA": True,
            "QUALITY_PRIOR_ACCOUNT": quality_account,
            "CAUSE_SPECIFIC_HAZARD_MODEL": NOT_IDENTIFIED,
            "COMPETING_RISK_MODEL": NOT_IDENTIFIED,
        },
        "EVENT_STATE": obs.get("EVENT_STATE", NOT_IDENTIFIED),
        "CAPITAL_OCCUPANCY": obs.get("CAPITAL_OCCUPANCY", NOT_IDENTIFIED),
        "STATES": state_set,
        "FEASIBLE_ACTIONS": feas,
        # THREE-VALUED, and the third value is not the second. An action that
        # cannot physically occur is dropped from the comparison; one whose
        # feasibility was never observed stays in it and blocks.
        "ACTION_FEASIBILITY": fmap,
        "ROBUST_DOMINANCE_ACTIVE": ROBUST_DOMINANCE_ACTIVE,
        "BOUND_METHODOLOGY_VALIDATED": BOUND_METHODOLOGY_VALIDATED,
        "ALL_ACTION_EVS": {
            a: {"EV": evs.get(a, (NOT_IDENTIFIED, ("NOT_EVALUATED",)))[0],
                "MISSING_TERMS": list(
                    evs.get(a, (NOT_IDENTIFIED, ("NOT_EVALUATED",)))[1])}
            for a in feas},
        "ACTION_CHOSEN": chosen,
        "ACTION_REASON": reason,
        # The counterfactual, recorded LIVE, at the moment of rejection,
        # alongside the book that priced it.
        "ACTIONS_NOT_CHOSEN": [
            {"ACTION": a, "EV": v} for a, v in ranked if a != chosen],
        "LATER_OUTCOME": NOT_IDENTIFIED,
    }
    return row


def write_row(path, row):
    """Append one decision row. Decimal money survives as an exact string."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(row, default=_jsonable, sort_keys=True) + "\n")
    return p


def stamp_outcome(row, outcome):
    """Fill LATER_OUTCOME once it is known. It starts NOT_IDENTIFIED.

    Refuses to overwrite a stamped outcome: a decision row is a record of one
    moment, and re-stamping it would let a later revision rewrite what the
    engine actually saw.
    """
    if row.get("LATER_OUTCOME", NOT_IDENTIFIED) != NOT_IDENTIFIED:
        raise ValueError("LATER_OUTCOME already stamped: %r"
                         % (row["LATER_OUTCOME"],))
    out = dict(row)
    out["LATER_OUTCOME"] = outcome
    return out


# ---------------------------------------------------------------------------
# THE POSITION EVENT HISTORY -- the thing the archive could never provide
# ---------------------------------------------------------------------------
#
# The whale grid is a set of cumulative counts. It cannot say which INDIVIDUAL
# leg went where, so it cannot distinguish "paired at a good basis" from
# "paired at a worse basis" from "sold" from "settled" as COMPETING terminal
# transitions. That is exactly why
#
#     CAUSE_SPECIFIC_HAZARD_MODEL = NOT_IDENTIFIED
#     COMPETING_RISK_MODEL        = NOT_IDENTIFIED
#
# and why neither is manufactured. An ordered per-position event history is the
# minimum structure from which those ARE estimable, so BETTOR writes one from
# the first shadow position. This is the target, recorded prospectively.

EVENT_KINDS = (
    "ENTRY", "BOOK_STATE", "PAIR_OPPORTUNITY_APPEARED",
    "PASSIVE_EXIT_AVAILABLE", "AGGRESSIVE_EXIT_AVAILABLE", "HEDGE_AVAILABLE",
    "ACTION_SELECTED", "ACTION_NOT_SELECTED", "FILL", "SETTLEMENT",
)

TERMINAL_STATES = ("PAIRED", "EXITED_PASSIVE", "EXITED_AGGRESSIVE", "HEDGED",
                   "SETTLED", "OTHER")

# What such a history makes estimable, once enough of it exists. None of these
# is claimed from it today.
ESTIMABLE_FROM_EVENT_HISTORY = (
    "CAUSE_SPECIFIC_COMPLETION_HAZARD", "CAUSE_SPECIFIC_EXIT_HAZARD",
    "COMPETING_RISKS", "OPTIMAL_STOPPING_POLICY",
)


def new_history(position_id, entry_time, entry_state=None):
    """Open an event history for one shadow position."""
    return {
        "POSITION_ID": position_id,
        "ENTRY_TIME": entry_time,
        "LABEL": COUNTERFACTUAL,
        "EVENTS": [{"KIND": "ENTRY", "AT": entry_time,
                    "ENTRY_STATE": entry_state
                    if entry_state is not None else NOT_IDENTIFIED}],
        "TERMINAL_STATE": NOT_IDENTIFIED,
        "TERMINAL_AT": NOT_IDENTIFIED,
    }


def append_event(history, kind, at, **fields):
    """Append one ORDERED transition. Order is the data, so it is enforced.

    A history whose events are not monotone in time cannot support a hazard
    estimate, and silently sorting them would hide a clock bug rather than
    surface it.
    """
    if kind not in EVENT_KINDS:
        raise ValueError("unknown event kind: %r" % (kind,))
    if history.get("TERMINAL_STATE", NOT_IDENTIFIED) != NOT_IDENTIFIED:
        raise ValueError("position already terminal: %r"
                         % (history["TERMINAL_STATE"],))
    last = history["EVENTS"][-1]["AT"]
    if (at is not None and last is not None
            and not isinstance(at, str) and not isinstance(last, str)
            and at < last):
        raise ValueError("event out of order: %r before %r" % (at, last))
    ev = {"KIND": kind, "AT": at}
    ev.update(fields)
    history["EVENTS"].append(ev)
    return history


def close_history(history, terminal_state, at):
    """Stamp the terminal transition. A position ends once."""
    if terminal_state not in TERMINAL_STATES:
        raise ValueError("unknown terminal state: %r" % (terminal_state,))
    if history.get("TERMINAL_STATE", NOT_IDENTIFIED) != NOT_IDENTIFIED:
        raise ValueError("already closed as %r" % (history["TERMINAL_STATE"],))
    history["TERMINAL_STATE"] = terminal_state
    history["TERMINAL_AT"] = at
    return history


def record_tick(history, row, at):
    """Fold one decision row into the history, INCLUDING the roads not taken.

    `ACTION_NOT_SELECTED` is written as its own event, one per rejected action
    with the EV it was rejected at. A history that recorded only the chosen
    action would reproduce the whale archive's central defect at higher
    resolution.
    """
    append_event(history, "BOOK_STATE", at,
                 CURRENT_BOOK=row.get("CURRENT_BOOK", NOT_IDENTIFIED),
                 COMPLEMENT_BOOK=row.get("COMPLEMENT_BOOK", NOT_IDENTIFIED),
                 PAIR_BASIS=row.get("PAIR_BASIS", NOT_IDENTIFIED),
                 QUEUE_AHEAD=row.get("QUEUE_AHEAD", NOT_IDENTIFIED))
    fmap = row.get("ACTION_FEASIBILITY", {})
    for action, kind in ((A_PASSIVE_COMPLEMENT_PAIR,
                          "PAIR_OPPORTUNITY_APPEARED"),
                         (A_AGGRESSIVE_COMPLEMENT_PAIR,
                          "PAIR_OPPORTUNITY_APPEARED"),
                         (A_PASSIVE_SELL_EXIT, "PASSIVE_EXIT_AVAILABLE"),
                         (A_AGGRESSIVE_SELL_EXIT, "AGGRESSIVE_EXIT_AVAILABLE"),
                         ("A_HEDGE", "HEDGE_AVAILABLE")):
        if fmap.get(action) == FEASIBLE:
            append_event(history, kind, at, ACTION=action)
    append_event(history, "ACTION_SELECTED", at,
                 ACTION=row.get("ACTION_CHOSEN"),
                 REASON=row.get("ACTION_REASON"))
    for d in row.get("ACTIONS_NOT_CHOSEN", []):
        append_event(history, "ACTION_NOT_SELECTED", at,
                     ACTION=d.get("ACTION"), EV=d.get("EV"))
    return history


# ---------------------------------------------------------------------------
# GHOST POLICIES
# ---------------------------------------------------------------------------

POLICY_WHALE_BASELINE = "POLICY_WHALE_BASELINE"
POLICY_ALWAYS_HOLD = "POLICY_ALWAYS_HOLD"
POLICY_PAIR_FIRST = "POLICY_PAIR_FIRST"
POLICY_EXIT_ENGINE_V1 = "POLICY_EXIT_ENGINE_V1"
POLICY_NO_PAIR_DIRECTIONAL = "POLICY_NO_PAIR_DIRECTIONAL"

GHOST_POLICIES = (POLICY_WHALE_BASELINE, POLICY_ALWAYS_HOLD, POLICY_PAIR_FIRST,
                  POLICY_EXIT_ENGINE_V1, POLICY_NO_PAIR_DIRECTIONAL)

FILL_MODELS = ("F0", "F1", "F2", "F3")


def ghost_fill_permitted(fill_evidence, model="F1"):
    """May a ghost policy's resting order be counted as filled?

    ONLY under the counterfactual fill model, never because the touch traded.
    A ghost policy allowed to assume its own fills beats every other policy BY
    CONSTRUCTION and measures nothing -- which would make the entire
    policy-comparison dataset worthless while looking like a result.

    `fill_evidence` is a row from tape.fill_model_v2.evaluate(). This function
    reads it; it never re-derives a fill.
    """
    if model not in FILL_MODELS:
        raise ValueError("unknown fill model: %r" % (model,))
    if not fill_evidence:
        return NOT_IDENTIFIED
    key = ("F3_TOUCH_ONLY" if model == "F3"
           else "COUNTERFACTUAL_FILL_SUPPORTED_%s" % model)
    v = fill_evidence.get(key)
    if v is None:
        return NOT_IDENTIFIED
    # The precondition every model shares. A block print, or an execution whose
    # type we could not determine, is not a CLOB execution and cannot fill a
    # resting order: UNKNOWN_EXECUTION_TYPE != CLOB_EXECUTION.
    if fill_evidence.get("TRADED_VOLUME_AT_PRICE", 0) <= 0 and model != "F3":
        return "NO"
    return v


def ghost_actions(obs, priors, account=CONSENSUS, horizon_minutes=None,
                  evs=None, risk_kill=None):
    """Every policy's action on the SAME observation, evaluated together.

    No capital moves for any of them. The point is the comparison dataset the
    whale archive could never provide: the same book, priced by five policies,
    with each one's choice recorded next to the others'.
    """
    state_set = states(obs)
    feas = set(feasible_actions(state_set))
    evs = dict(evs or {})

    def hold(*preferred):
        """The first preferred hold that this state actually permits.

        A ghost policy is still bound by feasibility. A policy that "always
        holds to settlement" cannot hold to settlement a position that is
        already an economically locked pair, and emitting the infeasible label
        anyway would put an action in the comparison dataset that no book ever
        allowed.
        """
        for a in preferred:
            if a in feas:
                return a
        return A_NO_ACTION_RECORDED

    out = {}
    # The whale baseline is the policy the archive can actually price:
    # hold to settlement, and complete a pair when the basis allows it. It is
    # deliberately crude -- claiming a richer reconstruction would be claiming
    # per-position evidence the archive does not contain.
    out[POLICY_WHALE_BASELINE] = (
        A_AGGRESSIVE_COMPLEMENT_PAIR if A_AGGRESSIVE_COMPLEMENT_PAIR in feas
        else hold(A_HOLD_LOCKED_PAIR, A_SETTLEMENT_HOLD, A_DIRECTIONAL_HOLD))
    out[POLICY_ALWAYS_HOLD] = hold(A_HOLD_LOCKED_PAIR, A_SETTLEMENT_HOLD,
                                   A_DIRECTIONAL_HOLD)
    out[POLICY_PAIR_FIRST] = (
        A_AGGRESSIVE_COMPLEMENT_PAIR if A_AGGRESSIVE_COMPLEMENT_PAIR in feas
        else hold(A_WAIT, A_HOLD_LOCKED_PAIR, A_SETTLEMENT_HOLD,
                  A_DIRECTIONAL_HOLD))
    out[POLICY_NO_PAIR_DIRECTIONAL] = hold(
        A_DIRECTIONAL_HOLD, A_HOLD_LOCKED_PAIR, A_SETTLEMENT_HOLD)
    chosen, _, _ = allocate(evs, feasible_actions(state_set),
                            risk_kill=risk_kill)
    out[POLICY_EXIT_ENGINE_V1] = chosen

    bad = [p for p, a in out.items()
           if a not in feas and a != A_NO_ACTION_RECORDED]
    if bad:
        raise AssertionError("ghost policy chose an infeasible action: %r"
                             % (bad,))

    return {
        "LABEL": COUNTERFACTUAL,
        "POLICY_ACTIONS": out,
        # Every ghost P&L stays NOT_IDENTIFIED until a fill is supported under
        # the counterfactual model AND the position resolves. An action is not
        # an outcome.
        "POLICY_PNL": {p: NOT_IDENTIFIED for p in GHOST_POLICIES},
        "PASSIVE_FILLS_ASSUMED": "NO",
    }


# ---------------------------------------------------------------------------
# THE POSTERIOR LOOP
# ---------------------------------------------------------------------------

BETTOR_SERIES = (
    "BETTOR_COMPLETION_HAZARD", "BETTOR_BASIS_FRONTIER",
    "BETTOR_PASSIVE_EXIT_FILL", "BETTOR_AGGRESSIVE_EXIT_VALUE",
    "BETTOR_DIRECTIONAL_HOLD_OUTCOME", "BETTOR_CAPITAL_OCCUPANCY",
    "BETTOR_EXIT_ACTION_VALUE",
)

# n=30 IS A SCALE CONSTANT, NOT A SWITCH. Stated precisely because the
# difference matters:
#
#     POSTERIOR_WEIGHT_FORMULA = w = n / (n + 30)          CONTINUOUS
#     HARD_N30_SWITCH          = NO
#
# There is no cliff: nothing changes discontinuously at n=30, which is merely
# where the two sources carry equal weight. What n=30 IS, is a V1 HEURISTIC --
# it shrinks on COUNT alone and is blind to how noisy either estimate is. A
# whale cell with 250,000 at risk and a whale cell with 40 would both be
# displaced at exactly the same rate, which is wrong, and the formula below
# cannot see it.
#
#     V1_HEURISTIC                            = YES
#     STATISTICALLY_OPTIMAL_POSTERIOR_WEIGHTING = NOT_ESTABLISHED
#
# The frozen shadow experiment keeps this rule as preregistered. The successor
# is `posterior_weight_precision` -- designed below, deliberately NOT wired in,
# and to be tested prospectively against this one rather than substituted for
# it on the strength of being better-looking mathematics.
POSTERIOR_CREDIBILITY_N = 30
POSTERIOR_WEIGHT_FORMULA = "w = n / (n + 30)"
HARD_N30_SWITCH = "NO"
POSTERIOR_WEIGHTING_IS_CONTINUOUS = True
V1_HEURISTIC = True
STATISTICALLY_OPTIMAL_POSTERIOR_WEIGHTING = "NOT_ESTABLISHED"
PRECISION_WEIGHTING_ACTIVE = False


def posterior_weight(bettor_n, credibility_n=POSTERIOR_CREDIBILITY_N):
    """How much of the blended hazard is BETTOR's own evidence.

    A pre-registered CONTINUOUS shrinkage: w = n / (n + credibility_n). At n=0
    the whale prior is the whole answer; at n=30 the two are equal; the prior
    never reaches zero weight and never stays at one, and no value of n makes
    the weight jump. The whale prior is an INITIAL prior -- four accounts on a
    different venue is a starting point, not a law.
    """
    if bettor_n is None or isinstance(bettor_n, str) or bettor_n < 0:
        return NOT_IDENTIFIED
    return bettor_n / float(bettor_n + credibility_n)


def posterior_weight_precision(whale_var, bettor_var, active=None):
    """THE SUCCESSOR RULE, designed and inert: inverse-variance weighting.

        w_bettor = (1/bettor_var) / (1/whale_var + 1/bettor_var)

    This is what the count rule is a proxy for. It lets a tight whale cell hold
    its ground against a noisy BETTOR cell of the same n, which
    `posterior_weight` cannot do because it never sees either variance.

    It stays OFF. Switching the frozen shadow experiment's blending rule
    mid-collection would make the two halves of the run incomparable, and the
    right way to adopt it is to run it alongside and compare -- exactly the
    prospective test the whale archive could never give us.
    """
    if active is None:
        active = PRECISION_WEIGHTING_ACTIVE
    if not active:
        return NOT_IDENTIFIED
    if (isinstance(whale_var, str) or isinstance(bettor_var, str)
            or whale_var is None or bettor_var is None
            or whale_var <= 0 or bettor_var <= 0):
        return NOT_IDENTIFIED
    pw, pb = 1.0 / whale_var, 1.0 / bettor_var
    return pb / (pw + pb)


# THE UPDATE BELONGS AT THE CELL, NOT THE AGGREGATE.
#
# A pooled BETTOR hazard would let observations from one kind of market wash
# out a strongly different prior in another: a thousand fast-pairing tennis
# moneyline legs would displace the prior for a thin futures market they say
# nothing about. The update key is therefore:
POSTERIOR_UPDATE_KEY = ("SPORT", "MARKET_TYPE", "PRICE_BAND", "TIME_UNPAIRED",
                        "PAIR_BASIS_STATE")
# with a minimum-support floor per cell, and a documented back-off path for
# cells that never reach it -- coarsen the key rather than pool everything.
POSTERIOR_CELL_MIN_SUPPORT = 30
POSTERIOR_BACKOFF_ORDER = ("PAIR_BASIS_STATE", "PRICE_BAND", "MARKET_TYPE",
                           "SPORT")
# NOT ACTIVE: no shadow observation exists, so there is nothing to key yet.
# It is recorded now so the collector writes the fields the update will need,
# which is the one thing that cannot be fixed retrospectively.
POSTERIOR_CELL_UPDATE_ACTIVE = False


def blended_lambda(prior_lambda, bettor_lambda, bettor_n):
    """WHALE_PRIOR -> BETTOR_POSTERIOR, with the prior's weight decaying.

    Applied PER CELL (see POSTERIOR_UPDATE_KEY), never to a pooled BETTOR
    hazard, so evidence from one market type cannot displace another's prior.
    """
    w = posterior_weight(bettor_n)
    if w == NOT_IDENTIFIED or isinstance(prior_lambda, str):
        return NOT_IDENTIFIED
    if isinstance(bettor_lambda, str) or bettor_lambda is None:
        # No BETTOR evidence yet: the prior stands, undiluted and unhidden.
        return prior_lambda
    return (1.0 - w) * prior_lambda + w * bettor_lambda


# ===========================================================================
# PHASE 2 -- SHADOW_EXIT_LEARNING_V1
# ===========================================================================

PHASE_2A = "LIVE_PUBLIC_MARKET_STATE_AND_DECISION_TELEMETRY"
PHASE_2B = "COUNTERFACTUAL_POSITION_AND_FILL_LEARNING"
PHASE_2C = "EXIT_POLICY_COMPARISON"
PHASE_2_STAGE = PHASE_2A

# THE JUMP THAT IS FORBIDDEN. Market snapshots do not become a strategy P&L
# by being numerous. Every stage below 2C leaves this NOT_IDENTIFIED, and the
# recorder refuses to name it at all until a stage that could support it.
BETTOR_EXIT_ENGINE_PNL = NOT_IDENTIFIED
PROFITABILITY_REPORTABLE = False


# ---------------------------------------------------------------------------
# POSITION PROVENANCE -- exactly one class per position
# ---------------------------------------------------------------------------

OBSERVED_ACTUAL_POSITION = "OBSERVED_ACTUAL_POSITION"
COUNTERFACTUAL_MAKER_FILL = "COUNTERFACTUAL_MAKER_FILL"
COUNTERFACTUAL_TAKER_ENTRY = "COUNTERFACTUAL_TAKER_ENTRY"
SYNTHETIC_RESEARCH_POSITION = "SYNTHETIC_RESEARCH_POSITION"

PROVENANCE_CLASSES = (OBSERVED_ACTUAL_POSITION, COUNTERFACTUAL_MAKER_FILL,
                      COUNTERFACTUAL_TAKER_ENTRY, SYNTHETIC_RESEARCH_POSITION)

# What each class is ALLOWED to be used for. The second column is the one that
# matters: a synthetic position exercises the machinery and proves nothing
# about whether the strategy makes money.
PROVENANCE_ADMISSIBLE_AS_STRATEGY_EVIDENCE = {
    OBSERVED_ACTUAL_POSITION: True,
    COUNTERFACTUAL_MAKER_FILL: True,      # only under the fill model
    COUNTERFACTUAL_TAKER_ENTRY: True,     # a cross is observable, not assumed
    SYNTHETIC_RESEARCH_POSITION: False,   # machinery only, never evidence
}

# In this phase there is no real money anywhere in the system.
ACTUAL_POSITIONS_POSSIBLE_THIS_PHASE = False


class ProvenanceViolation(RuntimeError):
    """Raised when a position is created without the evidence its class needs."""


def open_position(position_id, provenance, entry, fill_evidence=None,
                  fill_model="F1"):
    """Create one shadow position, or refuse.

    THE RULE THAT DOES THE WORK: a COUNTERFACTUAL_MAKER_FILL is only a
    position if the counterfactual fill model SUPPORTS a fill. Touching our
    hypothetical quote is not a fill -- a price can trade at our level while
    the queue ahead of us absorbs every contract, and a book of positions
    built from touches would show a fill rate nobody earned.
    """
    if provenance not in PROVENANCE_CLASSES:
        raise ProvenanceViolation("unknown provenance: %r" % (provenance,))
    if provenance == OBSERVED_ACTUAL_POSITION:
        raise ProvenanceViolation(
            "no ACTUAL position is possible in this phase: no order path "
            "exists, no credential exists, mirror_live is false")
    if provenance == COUNTERFACTUAL_MAKER_FILL:
        supported = ghost_fill_permitted(fill_evidence, fill_model)
        if supported != "YES":
            raise ProvenanceViolation(
                "COUNTERFACTUAL_MAKER_FILL requires the fill model to support "
                "a fill; got %r under %s. TOUCH != FILL."
                % (supported, fill_model))
    return {
        "POSITION_ID": position_id,
        "ENTRY_PROVENANCE": provenance,
        "ADMISSIBLE_AS_STRATEGY_EVIDENCE":
            PROVENANCE_ADMISSIBLE_AS_STRATEGY_EVIDENCE[provenance],
        "FILL_MODEL": fill_model if provenance == COUNTERFACTUAL_MAKER_FILL
                      else NOT_IDENTIFIED,
        "ENTRY": entry,
        "LABEL": COUNTERFACTUAL,
        "SUNK_PNL": NOT_IDENTIFIED,
    }


# ---------------------------------------------------------------------------
# FAIR VALUE -- a dependency, never quietly populated
# ---------------------------------------------------------------------------

FV_VALIDATED = "VALIDATED"
FV_VENUE_IMPLIED = "VENUE_IMPLIED_NOT_INDEPENDENT"


def fair_value_status(obs):
    """What we actually know about fair value on this row.

    `VENUE_IMPLIED` is the market's own opinion restated and is NOT an
    independent fair value: using it would make every EV a tautology in which
    the market is always correctly priced and no edge can ever exist. It is
    recorded, and it does not count.
    """
    fv = obs.get("FAIR_VALUE", NOT_IDENTIFIED)
    basis = obs.get("FV_BASIS", NOT_IDENTIFIED)
    if fv == NOT_IDENTIFIED or fv is None:
        return NOT_IDENTIFIED
    if basis in (NOT_IDENTIFIED, None, "VENUE_IMPLIED", FV_VENUE_IMPLIED):
        return FV_VENUE_IMPLIED
    return FV_VALIDATED


def fair_value_dependent_ev(status, *terms):
    """Any EV that needs fair value is NOT_IDENTIFIED unless FV is VALIDATED.

    The allocator is NOT weakened to force a decision. An unpriced action stays
    unpriced, the comparison stays incomplete, and the engine declines. That is
    the expected Phase-2A output, not a fault to be engineered around.
    """
    if status != FV_VALIDATED:
        return NOT_IDENTIFIED, ("FAIR_VALUE",)
    return ev_sum(tuple(terms))


# ---------------------------------------------------------------------------
# THE TWO PRIORS, RUN IN PARALLEL
# ---------------------------------------------------------------------------
#
# PRIOR_3 is the frozen preregistered primary. PRIOR_4 is a GHOST run beside
# it because the swisstony sensitivity is MATERIAL and the exclusion reason is
# NOT_IDENTIFIED_IN_THIS_WORKSPACE -- so the 3-account prior is preregistered,
# which is a claim about PROCEDURE, not about being economically more correct.
# Recording both prospectively is the only way to find out which describes
# BETTOR, and it costs nothing but a column.
PRIOR_3 = "POLICY_PRIOR_3"
PRIOR_4 = "POLICY_PRIOR_4"
PRIMARY_PRIOR = PRIOR_3
PRIOR_3_IS_PREREGISTERED_NOT_PROVEN_BETTER = True


def lambda_prior(priors, bucket, which=PRIOR_3):
    """The consensus lambda under either membership. Same pooled method."""
    if bucket == NOT_IDENTIFIED:
        return NOT_IDENTIFIED
    if which == PRIOR_3:
        return lambda_for(priors, bucket, account=CONSENSUS)
    if which == PRIOR_4:
        sens = priors.get("SWISSTONY_SENSITIVITY") or {}
        for r in sens.get("BY_INTERVAL", []):
            if r.get("INTERVAL") == bucket:
                v = r.get("LAMBDA_4", NOT_IDENTIFIED)
                return NOT_IDENTIFIED if isinstance(v, str) else v
        return NOT_IDENTIFIED
    raise ValueError("unknown prior: %r" % (which,))


def dual_prior_block(priors, bucket, horizon_minutes, evs3=None, evs4=None,
                     feas=None, in_play=(), risk_kill=None):
    """Both priors evaluated on the SAME state, with disagreement recorded.

    Neither is allowed to overwrite the other. The frozen prior decides; the
    ghost is measured beside it, and `ACTION_DISAGREEMENT` is the column that
    eventually answers whether slower swisstony-like pairing describes BETTOR
    better than the preregistered three.
    """
    l3 = lambda_prior(priors, bucket, PRIOR_3)
    l4 = lambda_prior(priors, bucket, PRIOR_4)
    p3 = p_complete_next_interval(l3, horizon_minutes)
    p4 = p_complete_next_interval(l4, horizon_minutes)

    w3 = ev_wait(p3, **(evs3 or {}))
    w4 = ev_wait(p4, **(evs4 or {}))

    a3, r3, _ = allocate(dict(evs3 or {}).get("_actions", {}) or {},
                         in_play, risk_kill=risk_kill, feas=feas)
    a4, r4, _ = allocate(dict(evs4 or {}).get("_actions", {}) or {},
                         in_play, risk_kill=risk_kill, feas=feas)

    delta = (w4[0] - w3[0]
             if w3[0] != NOT_IDENTIFIED and w4[0] != NOT_IDENTIFIED
             else NOT_IDENTIFIED)
    return {
        "PRIMARY_PRIOR": PRIMARY_PRIOR,
        "COMPLETION_HAZARD_PRIOR3": l3,
        "COMPLETION_HAZARD_PRIOR4": l4,
        "P_COMPLETE_PRIOR3": p3,
        "P_COMPLETE_PRIOR4": p4,
        "EV_WAIT_PRIOR3": w3[0],
        "EV_WAIT_PRIOR4": w4[0],
        "EV_WAIT_PRIOR3_MISSING": list(w3[1]),
        "EV_WAIT_PRIOR4_MISSING": list(w4[1]),
        "DELTA_EV_WAIT": delta,
        "ACTION_PRIOR3": a3,
        "ACTION_PRIOR4": a4,
        "ACTION_PRIOR3_REASON": r3,
        "ACTION_PRIOR4_REASON": r4,
        "ACTION_DISAGREEMENT": "YES" if a3 != a4 else "NO",
        "PRIOR_4_IS_A_GHOST_NOT_THE_PROTOCOL": True,
    }


# ---------------------------------------------------------------------------
# INCENTIVES ARE NEVER MIXED INTO TRADING ECONOMICS
# ---------------------------------------------------------------------------

INCENTIVE_TERMS = ("MAKER_REBATE", "LIQUIDITY_INCENTIVE", "OTHER_INCENTIVE")


def incentive_split(trading_terms, incentive_terms):
    """TRADING_NET_EX_INCENTIVES first, TOTAL_NET last, never one number.

    NEVER HOLD A MATERIALLY NEGATIVE-EV POSITION TO COLLECT A REBATE. The rule
    is enforced by reporting: a run whose trading economics are negative is
    labelled INCENTIVE_DEPENDENT in every output regardless of TOTAL_NET, so
    the rebate cannot be used to make the trade look good.
    """
    tnet, tmiss = ev_sum(tuple(trading_terms.items()))
    inet, imiss = ev_sum(tuple(incentive_terms.items()))
    total = (tnet + inet if tnet != NOT_IDENTIFIED and inet != NOT_IDENTIFIED
             else NOT_IDENTIFIED)
    dependent = (tnet != NOT_IDENTIFIED and tnet <= 0
                 and inet != NOT_IDENTIFIED and inet > 0)
    return {
        "TRADING_NET_EX_INCENTIVES": tnet,
        "TRADING_MISSING_TERMS": list(tmiss),
        "MAKER_REBATE": incentive_terms.get("MAKER_REBATE", NOT_IDENTIFIED),
        "LIQUIDITY_INCENTIVE": incentive_terms.get("LIQUIDITY_INCENTIVE",
                                                   NOT_IDENTIFIED),
        "OTHER_INCENTIVE": incentive_terms.get("OTHER_INCENTIVE",
                                               NOT_IDENTIFIED),
        "INCENTIVE_CONTRIBUTION": inet,
        "INCENTIVE_MISSING_TERMS": list(imiss),
        "TOTAL_NET": total,
        "INCENTIVE_DEPENDENT": "YES" if dependent else "NO",
        "REPORTED_TRADING_FIRST": True,
    }


# ---------------------------------------------------------------------------
# CAPACITY -- 788 HIGH_ACTIVITY MARKETS ARE NOT 788 OPPORTUNITIES
# ---------------------------------------------------------------------------

HIGH_ACTIVITY_CANDIDATE_MARKETS = 788
HIGH_ACTIVITY_MEANS = "OBSERVED_HIGH_ACTIVITY_CANDIDATE_MARKETS"
HIGH_ACTIVITY_DOES_NOT_MEAN = (
    "INDEPENDENT_OPPORTUNITIES", "ELIGIBLE_POSITIONS", "POSITIVE_EV_ENTRIES")


def candidate_universe(markets, event_key_of=None):
    """Count candidates and capacity SEPARATELY, and never conflate them.

    Market count is not capacity. Where a validated event identity exists the
    universe is stratified by event; where it does not, the uncertainty is
    RETAINED rather than resolved by pretending markets are independent.
    """
    n = len(markets)
    out = {
        "CANDIDATE_MARKETS": n,
        "MEANING": HIGH_ACTIVITY_MEANS,
        "IS_NOT": list(HIGH_ACTIVITY_DOES_NOT_MEAN),
        "MARKET_COUNT_USED_AS_CAPACITY": False,
    }
    if event_key_of is None:
        out["EVENT_STRATIFIED"] = False
        out["VALIDATED_EVENTS"] = NOT_IDENTIFIED
        out["INDEPENDENT_CAPACITY"] = NOT_IDENTIFIED
        out["CORRELATION_TREATMENT"] = (
            "ALL_MARKETS_TREATED_AS_FULLY_CORRELATED -- conservative, and "
            "deliberate, because EVENT_KEY_VALIDATED = NO")
        return out
    keys, unresolved = {}, 0
    for m in markets:
        k = event_key_of(m)
        if k in (None, NOT_IDENTIFIED):
            unresolved += 1
            continue
        keys.setdefault(k, []).append(m)
    out["EVENT_STRATIFIED"] = True
    out["VALIDATED_EVENTS"] = len(keys)
    out["MARKETS_WITH_UNRESOLVED_EVENT_IDENTITY"] = unresolved
    # A lower bound: the resolved events plus, at most, one event per market
    # whose identity we could not establish. The upper end is not claimed.
    out["INDEPENDENT_CAPACITY_LOWER_BOUND"] = len(keys)
    out["INDEPENDENT_CAPACITY"] = (
        len(keys) if unresolved == 0 else NOT_IDENTIFIED)
    out["CORRELATION_TREATMENT"] = (
        "STRATIFIED_BY_VALIDATED_EVENT; unresolved markets retain their "
        "uncertainty and are never assumed independent")
    return out


# ===========================================================================
# PMUS EXECUTION VOCABULARY -- a correction to my own language
# ===========================================================================
#
# I wrote "the pair trade on this venue is maker-only". That is TOO STRONG and
# I am withdrawing it. A profitable round trip may perfectly well end in an
# AGGRESSIVE close after favourable movement: buy at 0.41, the market moves to
# 0.45, cross the bid at 0.44 and the round trip is profitable with one passive
# leg and one aggressive one. What the census arithmetic actually establishes
# is narrower and should be stated as:
#
#     STRUCTURAL_SPREAD_CAPTURE_REQUIRES_PASSIVE_EXECUTION_ON_THE_RELEVANT_LEGS
#
# Capturing THE SPREAD ITSELF needs both legs passive. Making money does not.
STRUCTURAL_SPREAD_CAPTURE_REQUIRES_PASSIVE_EXECUTION_ON_THE_RELEVANT_LEGS = True
PAIR_TRADE_IS_MAKER_ONLY = "WITHDRAWN_TOO_STRONG"

# And the arithmetic is GROSS. It is a spread, not a profit.
GROSS_TWO_SIDED_MAKER_EDGE = "OBSERVED"
REALIZED_BETTOR_TWO_SIDED_EDGE = "NOT_ESTABLISHED"
# Everything between the two:
UNPRICED_BETWEEN_GROSS_AND_REALIZED = (
    "MAKER_FEE", "TAKER_FEE", "MAKER_REBATE", "LIQUIDITY_INCENTIVE",
    "QUEUE_POSITION", "PARTIAL_FILLS", "ADVERSE_SELECTION",
    "INVENTORY_DURATION", "REQUOTE_COST", "CAPITAL_OCCUPANCY",
)
# The condition that must travel with every spread statement.
SPREAD_STATEMENT_QUALIFIER = "IF_BOTH_FILLS_OCCUR"


# TWO VOCABULARIES, DELIBERATELY KEPT APART.
#
# PAIRING is an ANALYTICAL concept. It is how the whale archive is organised --
# a first leg, a complement, a basis, a completion hazard -- and it is the only
# language in which those priors can be read. It stays, in the prior-mapping
# layer, and nowhere else.
#
# On PMUS there is ONE BINARY BOOK per market, so acquiring the economic
# complement IS reducing the position. An execution action here is an INVENTORY
# action, and letting Polymarket's two-token vocabulary leak into it would
# describe a trade this venue cannot make.
PAIRING_ANALYTICAL_CONCEPT = "WHALE_PRIOR_MAPPING_ONLY"
PMUS_EXECUTION_ACTION = "INVENTORY_CLOSE_ON_THE_SAME_BINARY_BOOK"

X_OPEN_MAKER_INVENTORY = "OPEN_MAKER_INVENTORY"
X_PASSIVE_INVENTORY_CLOSE = "PASSIVE_INVENTORY_CLOSE"
X_AGGRESSIVE_INVENTORY_CLOSE = "AGGRESSIVE_INVENTORY_CLOSE"
X_REQUOTED_PASSIVE_CLOSE = "REQUOTED_PASSIVE_CLOSE"
X_HOLD_INVENTORY = "HOLD_INVENTORY"
X_HEDGE_EXTERNALLY = "HEDGE_EXTERNALLY"
X_SETTLE = "SETTLE"

PMUS_ACTIONS = (X_OPEN_MAKER_INVENTORY, X_PASSIVE_INVENTORY_CLOSE,
                X_AGGRESSIVE_INVENTORY_CLOSE, X_REQUOTED_PASSIVE_CLOSE,
                X_HOLD_INVENTORY, X_HEDGE_EXTERNALLY, X_SETTLE)

# How the whale-prior action vocabulary maps onto what PMUS can actually do.
# The mapping is EXPLICIT so the translation is auditable rather than implied:
# "complete the pair passively" and "close the inventory passively" are the
# same venue instruction, and saying so once here is safer than letting two
# names for one act drift apart in the code.
WHALE_ACTION_TO_PMUS = {
    A_PASSIVE_COMPLEMENT_PAIR: X_PASSIVE_INVENTORY_CLOSE,
    A_AGGRESSIVE_COMPLEMENT_PAIR: X_AGGRESSIVE_INVENTORY_CLOSE,
    A_PASSIVE_SELL_EXIT: X_PASSIVE_INVENTORY_CLOSE,
    A_AGGRESSIVE_SELL_EXIT: X_AGGRESSIVE_INVENTORY_CLOSE,
    A_WAIT: X_HOLD_INVENTORY,
    A_DIRECTIONAL_HOLD: X_HOLD_INVENTORY,
    A_HEDGE: X_HEDGE_EXTERNALLY,
    A_SETTLEMENT_HOLD: X_SETTLE,
    A_REALIZE_AND_RECYCLE: X_PASSIVE_INVENTORY_CLOSE,
    A_HOLD_LOCKED_PAIR: X_HOLD_INVENTORY,
}


def pmus_action(whale_action):
    """Translate a prior-layer action into the venue instruction it really is.

    Note the many-to-one: BOTH 'complete the pair passively' and 'exit
    passively' are one venue act on a single binary book. The collapse is the
    point -- it is what makes the PMUS vocabulary honest.
    """
    if whale_action == A_NO_ACTION_RECORDED:
        return A_NO_ACTION_RECORDED
    if whale_action not in WHALE_ACTION_TO_PMUS:
        raise ValueError("no PMUS translation for %r" % (whale_action,))
    return WHALE_ACTION_TO_PMUS[whale_action]


# ---------------------------------------------------------------------------
# THE CENTRAL PHASE-2 QUESTION, RESTATED FOR THIS VENUE
# ---------------------------------------------------------------------------
#
# Not "how often does a pair complete" -- that is the whale question. Ours is:
# AFTER A HYPOTHETICAL MAKER FILL CREATES ONE-SIDED INVENTORY, what happens?
# These are the distributions the shadow run exists to produce, and the
# BETTOR-native analogue of the whale completion process.
INVENTORY_OUTCOME_FIELDS = (
    "TIME_TO_OPPOSITE_FILL", "PRICE_OF_OPPOSITE_FILL", "NET_SPREAD_CAPTURE",
    "INVENTORY_MARKOUT", "MAX_ADVERSE_EXCURSION", "MAX_FAVORABLE_EXCURSION",
    "CAPITAL_OCCUPANCY", "FAILURE_TO_CLOSE", "SETTLEMENT_OUTCOME",
)


def orphan_conservatism(fv_status, time_unpaired_s):
    """An unpriced directional hold makes a long orphan MORE conservative.

    The temptation runs the other way: if nothing is priced, nothing forbids
    holding, so inventory drifts. That is backwards. A position whose
    directional value is NOT_IDENTIFIED is being held on no evidence at all,
    and the longer it is held the larger the unmeasured bet. So the engine
    records an escalating conservatism flag rather than an escalating licence.
    """
    if fv_status == FV_VALIDATED:
        return {"DIRECTIONAL_HOLD_PRICED": True,
                "ORPHAN_CONSERVATISM": "NORMAL"}
    b = time_unpaired_bucket(time_unpaired_s)
    late = b in ("600s-1800s", "1800s-3600s", NO_LAMBDA_BUCKET)
    return {
        "DIRECTIONAL_HOLD_PRICED": False,
        "EV_DIRECTIONAL_HOLD": NOT_IDENTIFIED,
        "ORPHAN_CONSERVATISM": "ELEVATED" if late else "NORMAL",
        "WHY": ("directional value is NOT_IDENTIFIED, so continuing to hold is "
                "an unmeasured bet that grows with TIME_UNPAIRED -- this is a "
                "reason for MORE caution, never a licence to drift"),
        "TIME_UNPAIRED_BUCKET": b,
    }
