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
          PASSIVE_EXIT_AVAILABLE, AGGRESSIVE_EXIT_AVAILABLE, HEDGE_AVAILABLE,
          DIRECTIONAL_HOLD, COMPLETED_PAIR, SETTLED)

FRESH_SECONDS = 60

A_PAIR_NOW = "A_PAIR_NOW"
A_WAIT = "A_WAIT"
A_PASSIVE_EXIT = "A_PASSIVE_EXIT"
A_AGGRESSIVE_EXIT = "A_AGGRESSIVE_EXIT"
A_HEDGE = "A_HEDGE"
A_DIRECTIONAL_HOLD = "A_DIRECTIONAL_HOLD"
A_SETTLEMENT_HOLD = "A_SETTLEMENT_HOLD"
A_REALIZE_AND_RECYCLE = "A_REALIZE_AND_RECYCLE"
A_HOLD_LOCKED_PAIR = "A_HOLD_LOCKED_PAIR"

ACTIONS = (A_PAIR_NOW, A_WAIT, A_PASSIVE_EXIT, A_AGGRESSIVE_EXIT, A_HEDGE,
           A_DIRECTIONAL_HOLD, A_SETTLEMENT_HOLD, A_REALIZE_AND_RECYCLE,
           A_HOLD_LOCKED_PAIR)

# What each state MAKES POSSIBLE. Not what it recommends.
FEASIBLE_BY_STATE = {
    FRESH_UNPAIRED: (A_WAIT, A_SETTLEMENT_HOLD),
    AGING_UNPAIRED: (A_WAIT, A_SETTLEMENT_HOLD),
    PAIR_AVAILABLE: (A_PAIR_NOW,),
    PASSIVE_EXIT_AVAILABLE: (A_PASSIVE_EXIT,),
    AGGRESSIVE_EXIT_AVAILABLE: (A_AGGRESSIVE_EXIT,),
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
    if obs.get("PASSIVE_EXIT_PLACEABLE"):
        out.append(PASSIVE_EXIT_AVAILABLE)
    if obs.get("AGGRESSIVE_EXIT_DEPTH_EXISTS"):
        out.append(AGGRESSIVE_EXIT_AVAILABLE)
    if obs.get("HEDGE_INSTRUMENT_EXECUTABLE"):
        out.append(HEDGE_AVAILABLE)
    # DIRECTIONAL_HOLD is the state of having no feasible pair OR exit. It is
    # NOT a decision to run the position directionally -- that decision is
    # A_DIRECTIONAL_HOLD, and it is taken by the allocator on economics.
    if not ({PAIR_AVAILABLE, PASSIVE_EXIT_AVAILABLE,
             AGGRESSIVE_EXIT_AVAILABLE, HEDGE_AVAILABLE} & set(out)):
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
# THE HAZARD PRIOR -- LEVEL_A evidence, read, never re-derived here
# ---------------------------------------------------------------------------

LEVEL_A = "LEVEL_A_DIRECT_WHALE_EVIDENCE"
LEVEL_B = "LEVEL_B_WHALE_DERIVED_PRIOR"
LEVEL_C = "LEVEL_C_BETTOR_PROSPECTIVE_ONLY"

CONSENSUS = "CROSS_WHALE_CONSENSUS"


def load_priors(path):
    """The priors artifact, as written by build_exit_priors.py."""
    return json.loads(Path(path).read_text())


def lambda_for(priors, bucket, account=CONSENSUS, ceiling="any_basis"):
    """The continuous hazard lambda for one bucket. NOT_IDENTIFIED where absent.

    `account=CONSENSUS` reads the FIRST_SIDE_ACQUISITIONS-weighted cross-whale
    prior; naming an account reads that account's own series. The consensus is
    weighted and excludes swisstony -- both facts live in the artifact, and this
    reader never re-averages anything.
    """
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
DOMINATES = "DOMINATES"
RISK_KILL = "RISK_KILL"
NO_FEASIBLE_ACTION = "NO_FEASIBLE_ACTION"


def allocate(evs, feasible, risk_kill=None):
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
                 horizon_minutes=None, evs=None, risk_kill=None):
    """One decision tick, fully recorded, whether or not anything is done.

    `obs` carries the book the caller read publicly; this function contacts
    nothing. `evs` maps an action to an (value, missing_terms) pair as returned
    by the ev_* functions above; actions left out are priced NOT_IDENTIFIED with
    reason NOT_EVALUATED, which is a different claim from zero.
    """
    t = obs.get("TIME_UNPAIRED_S")
    bucket = time_unpaired_bucket(t)
    lam = lambda_for(priors, bucket, account=account, ceiling=ceiling)
    p = p_complete_next_interval(lam, horizon_minutes)

    state_set = states(obs)
    feas = feasible_actions(state_set)
    evs = dict(evs or {})
    chosen, reason, ranked = allocate(evs, feas, risk_kill=risk_kill)

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
        },
        "EVENT_STATE": obs.get("EVENT_STATE", NOT_IDENTIFIED),
        "CAPITAL_OCCUPANCY": obs.get("CAPITAL_OCCUPANCY", NOT_IDENTIFIED),
        "STATES": state_set,
        "FEASIBLE_ACTIONS": feas,
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
        A_PAIR_NOW if A_PAIR_NOW in feas
        else hold(A_HOLD_LOCKED_PAIR, A_SETTLEMENT_HOLD, A_DIRECTIONAL_HOLD))
    out[POLICY_ALWAYS_HOLD] = hold(A_HOLD_LOCKED_PAIR, A_SETTLEMENT_HOLD,
                                   A_DIRECTIONAL_HOLD)
    out[POLICY_PAIR_FIRST] = (
        A_PAIR_NOW if A_PAIR_NOW in feas
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

# Below this many of BETTOR's own completions in a bucket, BETTOR's own hazard
# is not yet credible and the whale prior carries full weight. The threshold is
# the same n>=30 the inference gate uses elsewhere in the programme, and it is
# frozen here before any shadow data exists so it cannot be tuned to a result.
POSTERIOR_CREDIBILITY_N = 30


def posterior_weight(bettor_n, credibility_n=POSTERIOR_CREDIBILITY_N):
    """How much of the blended hazard is BETTOR's own evidence.

    A simple, pre-registered shrinkage: w = n / (n + credibility_n). At n=0 the
    whale prior is the whole answer; at n=30 the two are equal; the prior never
    reaches zero weight and never stays at one. The whale prior is an INITIAL
    prior -- four accounts on a different venue is a starting point, not a law.
    """
    if bettor_n is None or isinstance(bettor_n, str) or bettor_n < 0:
        return NOT_IDENTIFIED
    return bettor_n / float(bettor_n + credibility_n)


def blended_lambda(prior_lambda, bettor_lambda, bettor_n):
    """WHALE_PRIOR -> BETTOR_POSTERIOR, with the prior's weight decaying."""
    w = posterior_weight(bettor_n)
    if w == NOT_IDENTIFIED or isinstance(prior_lambda, str):
        return NOT_IDENTIFIED
    if isinstance(bettor_lambda, str) or bettor_lambda is None:
        # No BETTOR evidence yet: the prior stands, undiluted and unhidden.
        return prior_lambda
    return (1.0 - w) * prior_lambda + w * bettor_lambda
