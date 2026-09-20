"""THE STRUCTURAL PAIR ENGINE. WHAT COMPLETING A PAIR IS ACTUALLY WORTH.

Owner directive, "CONTINUE THE BUILD" §5:

    "PAIR EV must not equal 1 - YES_PRICE - NO_PRICE alone. The Ferrari
    result already proved why. Matched machinery can be excellent while
    residual inventory destroys the strategy."

THE FERRARI NUMBERS, WHICH ARE THE WHOLE ARGUMENT. In the 0.10-0.30
band Ferrari's merge economics were +$3.84M and its settled residual
was -$4.44M, for -$0.56M in total. An engine that priced pairs as
`1 - YES - NO` would have seen the +3.84M, called the machine
excellent, and been right about the pairs and wrong about the
strategy. The residual is not a rounding error on the pair: it is the
term that changed the sign.

    KEEP FERRARI'S PAIR MACHINE. DO NOT KEEP FERRARI'S RESIDUAL POLICY.

SO WHAT THIS MODULE REFUSES TO DO. It will not return a single "pair
EV" number. `1 - PAIR_BASIS` is computable from the book today and it
is reported -- as EXPECTED_PAIR_MARGIN_GROSS, which is a STRUCTURAL
FACT BEFORE INCENTIVES and explicitly NOT an established net outcome.
The frozen whale priors use those exact words about basis above par:

    PAIR_BASIS_ABOVE_PAR_IS     GROSS_STRUCTURAL_FACT_BEFORE_INCENTIVES
    PAIR_BASIS_ABOVE_PAR_IS_NOT ESTABLISHED_FINAL_NET_LOSS

Turning that margin into a decision needs P_PAIR_COMPLETION, the
residual distribution, the capital-hours to completion and the
incentives. Those are NOT_IDENTIFIED for BETTOR, so the engine reports
the structure with the identified cells filled and the rest named.

────────────────────────────────────────────────────────────────────
THE WHALE HAZARD IS A PRIOR, NOT A TRIGGER, AND NOT A FILL RATE (§6).

WHALE_REFERENCE_PRIORS_V1.json carries a real completion-hazard table:
three accounts (rn1, ferrarichampions2026, homerunhazard), ten time
intervals, continuous lambda with a CI95 and the risk set at each
step. SwissTony is excluded from the primary prior by a freeze made
before the result was known.

Every row carries its own restrictions, and they are carried through
here rather than summarised away:

    IS_NOT                     BETTOR_P_FILL
    MAY_SEED_BETTOR_P_FILL     false
    SELECTION_CONDITION        OBSERVED_WHALE_ENTERED_POSITIONS_ONLY
    CAUSE_SPECIFIC_HAZARD      NOT_COMPUTED
    WHALE_COMPLETION_AS_P_FILL FORBIDDEN

WHY IT IS NOT P_PAIR_COMPLETION FOR US. It measures how fast a whale
completed THEIR pairs, on positions selected because that whale chose
to enter them, with their size, their markets and their order policy
-- and WHALE_ORDER_POLICY is itself NOT_IDENTIFIED. BETTOR completing a
pair is a different event in a different population. The hazard informs
the SHAPE of the problem: completion is front-loaded and decays hard
(RN1's lambda falls from 0.595 in the first five seconds to 0.003 at
half an hour), so a pair that has not completed quickly is unlikely to
complete at all. That is a mechanism lesson. It is not our number.

    INDEPENDENT_EFFECTIVE_N is NOT_IDENTIFIED, so even the CI95 is a
    LOWER BOUND on the interval width. A confidence interval computed
    on correlated positions as though they were independent is narrower
    than the truth, and the file says so rather than letting a reader
    assume otherwise.
────────────────────────────────────────────────────────────────────

THE LEDGER BUCKETS ARE NEVER BLENDED (§5). PAIR_PNL, DIRECTIONAL_PNL,
RESIDUAL_INVENTORY_PNL, EXIT_HEDGE_PNL, REBATES, INCENTIVES, FEES and
SLIPPAGE are declared separately and this module never sums them into
one figure. Ferrari's total was the sum of a large positive and a
larger negative; reporting only the total would have hidden both.

NOTHING HERE PLACES, SIZES OR FUNDS AN ORDER.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import bettor_ev_bridge as evb
from . import bettor_inventory as binv

NOT_IDENTIFIED = "NOT_IDENTIFIED"
PRIORS_UNAVAILABLE = "WHALE_PRIORS_UNAVAILABLE"

PRIORS_FILENAME = "WHALE_REFERENCE_PRIORS_V1.json"

# ── §5: the ledger buckets, declared apart ───────────────────────────

PNL_BUCKETS = (
    "PAIR_PNL",
    "DIRECTIONAL_PNL",
    "RESIDUAL_INVENTORY_PNL",
    "EXIT_HEDGE_PNL",
    "REBATES",
    "INCENTIVES",
    "FEES",
    "SLIPPAGE",
)

NEVER_BLENDED = (
    "these buckets are never summed into one unexplained number. "
    "Ferrari's 0.10-0.30 band was +$3.84M of merge economics and "
    "-$4.44M of settled residual: a single total of -$0.56M would have "
    "hidden a pair machine that worked and an inventory policy that "
    "did not, which are different problems with different fixes")

PAIR_EV_IS_NOT = (
    "PAIR_EV is NOT 1 - YES_PRICE - NO_PRICE. That quantity is the "
    "GROSS STRUCTURAL MARGIN at current prices, before completion "
    "probability, before the residual that fails to complete, before "
    "the capital those legs occupy while waiting, and before "
    "incentives. Each of those can change its sign")

# ── §6: the restrictions, carried rather than summarised ─────────────

WHALE_RESTRICTIONS = {
    "WHALE_COMPLETION_AS_P_FILL": "FORBIDDEN",
    "WHALE_ORDER_POLICY": NOT_IDENTIFIED,
    "COUNTERFACTUAL_ENTRY_OUTCOME": NOT_IDENTIFIED,
    "INDEPENDENT_EFFECTIVE_N": NOT_IDENTIFIED,
    "NET_OF_INCENTIVES_PAIR_OUTCOME": NOT_IDENTIFIED,
    "WHALE_EVIDENCE_ALONE_CAN_CREATE_A_TRADE": False,
    "SELECTION_CONDITION": "OBSERVED_WHALE_ENTERED_POSITIONS_ONLY",
}

WHALE_IS_A_TEACHER_NOT_A_TRIGGER = (
    "'RN1 DID X' never becomes 'BETTOR MUST DO X'. The hazard informs "
    "completion shape, pair-basis sign, residual risk and time in "
    "inventory as coarse mechanism priors with explicit provenance. It "
    "cannot manufacture BETTOR's fill probability or an independent "
    "fair value, and no whale observation on its own creates a trade")

# SwissTony is excluded from the primary prior, and the exclusion was
# frozen before the result was known. Recorded so a later reader cannot
# mistake the exclusion for a reaction to what the data showed.
SENSITIVITY_ONLY_ACCOUNTS = ("swisstony",)

_PRIORS_CACHE: dict = {}


def _d(v):
    if v is None or v == "" or v == NOT_IDENTIFIED:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


# ── the frozen priors, loaded from the shipped file ──────────────────

def priors_path(root=None):
    """WHALE_REFERENCE_PRIORS_V1.json sits BESIDE shadow/, not in it."""
    shadow = evb.research_root(root)
    if shadow is None:
        return None
    candidate = Path(shadow).parent / PRIORS_FILENAME
    return candidate if candidate.is_file() else None


def priors(root=None) -> dict | None:
    path = priors_path(root)
    if path is None:
        return None
    key = str(path)
    if key not in _PRIORS_CACHE:
        with open(path, "r", encoding="utf-8") as fh:
            _PRIORS_CACHE[key] = json.load(fh)
    return _PRIORS_CACHE[key]


def completion_hazard(seconds_unpaired, *, account="rn1", root=None) -> dict:
    """The whale completion hazard for this time-in-inventory bucket.

    Returned with its restrictions attached, always. A caller that
    wants the lambda has to carry the sentence saying it is not a fill
    probability, because the two have been confused before.
    """
    p = priors(root)
    if p is None:
        return {"status": PRIORS_UNAVAILABLE,
                "WHALE_COMPLETION_HAZARD": NOT_IDENTIFIED,
                "why": ("%s is not present, so no completion-hazard "
                        "prior is available" % PRIORS_FILENAME)}
    if str(account).lower() in SENSITIVITY_ONLY_ACCOUNTS:
        return {"status": "SENSITIVITY_ONLY",
                "WHALE_COMPLETION_HAZARD": NOT_IDENTIFIED,
                "account": account,
                "why": ("this account is held out of the primary prior "
                        "by an exclusion frozen before the result was "
                        "known. It may be used for a sensitivity run "
                        "and never as a canonical prior")}

    try:
        PS = evb.machinery(root)["position_state"]
        bucket = PS.time_unpaired_bucket(seconds_unpaired)
    except (evb.MachineryUnavailable, Exception):       # noqa: BLE001
        return {"status": NOT_IDENTIFIED,
                "WHALE_COMPLETION_HAZARD": NOT_IDENTIFIED,
                "why": "the time-in-inventory bucket could not be resolved"}

    rows = p.get("COMPLETION_HAZARD_BY_ACCOUNT_AND_INTERVAL") or []
    match = [r for r in rows
             if str(r.get("ACCOUNT", "")).lower() == str(account).lower()
             and r.get("INTERVAL") == bucket]
    if not match:
        return {"status": NOT_IDENTIFIED, "interval": bucket,
                "WHALE_COMPLETION_HAZARD": NOT_IDENTIFIED,
                "why": "no hazard row for this account and interval"}

    row = match[0]
    lam = row.get("CONTINUOUS_HAZARD_LAMBDA")
    return {
        "status": ("IDENTIFIED" if lam != NOT_IDENTIFIED
                   else NOT_IDENTIFIED),
        "account": row.get("ACCOUNT"),
        "interval": bucket,
        "WHALE_COMPLETION_HAZARD": lam,
        "HAZARD_LAMBDA_CI95": row.get("HAZARD_LAMBDA_CI95"),
        "CUMULATIVE_COMPLETION_F": row.get("CUMULATIVE_COMPLETION_F"),
        "N_AT_RISK_AT_START": row.get("N_AT_RISK_AT_START"),
        "CAUSE_SPECIFIC_HAZARD": row.get("CAUSE_SPECIFIC_HAZARD"),
        # THE RESTRICTIONS TRAVEL WITH THE NUMBER.
        "IS_NOT": row.get("IS_NOT"),
        "MAY_SEED_BETTOR_P_FILL": row.get("MAY_SEED_BETTOR_P_FILL"),
        "SELECTION_CONDITION": row.get("SELECTION_CONDITION"),
        "intervalWidthIsALowerBound": (
            "INDEPENDENT_EFFECTIVE_N is NOT_IDENTIFIED, so the CI95 was "
            "computed on positions treated as independent when they are "
            "not. The true interval is WIDER than this one"),
        "isATeacherNotATrigger": WHALE_IS_A_TEACHER_NOT_A_TRIGGER,
    }


# ── §5: the pair view ────────────────────────────────────────────────

def pair_view(inventory: dict, complement_book: dict | None = None, *,
              seconds_unpaired=None, hazard_account="rn1",
              root=None) -> dict:
    """What completing this pair would take, and what is not identified.

    `inventory` is a `bettor_inventory.inventory()` state.
    `complement_book` is the book for the leg we do NOT yet hold.
    """
    held_leg = None
    yq = _d(inventory.get("YES_QTY")) or Decimal("0")
    nq = _d(inventory.get("NO_QTY")) or Decimal("0")
    if yq > 0 and nq == 0:
        held_leg, want_leg = binv.LEG_YES, binv.LEG_NO
    elif nq > 0 and yq == 0:
        held_leg, want_leg = binv.LEG_NO, binv.LEG_YES

    view = {
        "heldLeg": held_leg or NOT_IDENTIFIED,
        "complementLeg": (want_leg if held_leg else NOT_IDENTIFIED),
        "pairEvIsNot": PAIR_EV_IS_NOT,
        "pnlBuckets": list(PNL_BUCKETS),
        "neverBlended": NEVER_BLENDED,
        "whaleRestrictions": dict(WHALE_RESTRICTIONS),
    }

    if held_leg is None:
        view.update({
            "status": NOT_IDENTIFIED,
            "why": ("a pair view needs exactly one leg held and the "
                    "other open. Holding neither is flat; holding both "
                    "is an inventory question, not a completion one"),
        })
        return view

    own_basis = _d(inventory.get("%s_AVG_BASIS" % held_leg))
    residual_qty = _d(inventory.get("RESIDUAL_%s_QTY" % held_leg)) or \
        (yq if held_leg == binv.LEG_YES else nq)

    # EXPECTED_COMPLEMENT_PRICE: the ask on the leg we would buy. This
    # IS observable, and it is the only input here that is.
    ask = _d((complement_book or {}).get("ask"))
    depth = _d((complement_book or {}).get("availableDepth"))
    view["EXPECTED_COMPLEMENT_PRICE"] = str(ask) if ask is not None \
        else NOT_IDENTIFIED
    view["complementPriceBasis"] = (
        "the ask on the complement leg: what acquiring it would cost "
        "aggressively RIGHT NOW. It is not a forecast of the price at "
        "which the pair would actually complete")
    view["EXPECTED_COMPLEMENT_DEPTH"] = str(depth) if depth is not None \
        else NOT_IDENTIFIED

    if own_basis is not None and ask is not None:
        basis = own_basis + ask
        view["EXPECTED_PAIR_BASIS"] = str(basis)
        view["EXPECTED_PAIR_MARGIN_GROSS"] = str(Decimal("1") - basis)
        view["marginIs"] = "GROSS_STRUCTURAL_FACT_BEFORE_INCENTIVES"
        view["marginIsNot"] = "ESTABLISHED_FINAL_NET_OUTCOME"
        view["whyMarginIsNotEv"] = PAIR_EV_IS_NOT
    else:
        view["EXPECTED_PAIR_BASIS"] = NOT_IDENTIFIED
        view["EXPECTED_PAIR_MARGIN_GROSS"] = NOT_IDENTIFIED

    # EVERYTHING BELOW NEEDS A COMPLETION MODEL BETTOR DOES NOT HAVE.
    # Each is named rather than defaulted, and each says what it would
    # take to identify it.
    view.update({
        "P_PAIR_COMPLETION": NOT_IDENTIFIED,
        "whyPCompletionNotIdentified": (
            "BETTOR has never rested an order, so it has no completion "
            "evidence of its own. The whale hazard below is a DIFFERENT "
            "quantity on a DIFFERENT population and is forbidden from "
            "seeding it"),
        "EXPECTED_TIME_TO_COMPLETION": NOT_IDENTIFIED,
        "EXPECTED_RESIDUAL_QTY": NOT_IDENTIFIED,
        "whyResidualNotIdentified": (
            "the residual is whatever fails to complete, so it is a "
            "function of P_PAIR_COMPLETION. Without that it cannot be "
            "estimated -- and it is the term that changed Ferrari's "
            "sign, so estimating it by assumption is the one shortcut "
            "that must not be taken"),
        "EXPECTED_RESIDUAL_VALUE": NOT_IDENTIFIED,
        "EXPECTED_RESIDUAL_LOSS": NOT_IDENTIFIED,
        "RESIDUAL_UNCERTAINTY": NOT_IDENTIFIED,
        "CAPITAL_HOURS_TO_COMPLETION": NOT_IDENTIFIED,
        "whyCapitalHoursNotIdentified": (
            "capital-hours to completion is CAPITAL x TIME, and the "
            "time is EXPECTED_TIME_TO_COMPLETION. The capital is "
            "identified; the hours are not"),
        "INCENTIVES": NOT_IDENTIFIED,
        "REBATES": NOT_IDENTIFIED,
        "whyIncentivesNotIdentified": (
            "the CLOB rebate schedule is unverified and liquidity-"
            "reward eligibility is unconfirmed, so "
            "NET_OF_INCENTIVES_PAIR_OUTCOME is NOT_IDENTIFIED. The "
            "gross margin above is therefore not a net result"),
        "CAPITAL_AT_RISK_ON_HELD_LEG": (
            str(residual_qty * own_basis)
            if own_basis is not None and residual_qty is not None
            else NOT_IDENTIFIED),
        "status": "STRUCTURE_IDENTIFIED_COMPLETION_NOT_IDENTIFIED",
    })

    # The mechanism prior, clearly separated from anything BETTOR-native.
    if seconds_unpaired is not None:
        view["WHALE_COMPLETION_HAZARD_PRIOR"] = completion_hazard(
            seconds_unpaired, account=hazard_account, root=root)
        view["priorIsNotPCompletion"] = (
            "WHALE_COMPLETION_HAZARD_PRIOR and P_PAIR_COMPLETION are "
            "different fields on purpose. The first is observed on "
            "whales; the second is BETTOR's and remains NOT_IDENTIFIED")
    return view


def describe() -> dict:
    return {
        "purpose": "structural pair economics, with the residual kept visible",
        "pairEvIsNot": PAIR_EV_IS_NOT,
        "pnlBuckets": list(PNL_BUCKETS),
        "neverBlended": NEVER_BLENDED,
        "whaleRestrictions": dict(WHALE_RESTRICTIONS),
        "whaleIsATeacherNotATrigger": WHALE_IS_A_TEACHER_NOT_A_TRIGGER,
        "sensitivityOnlyAccounts": list(SENSITIVITY_ONLY_ACCOUNTS),
        "priorsAvailable": priors_path() is not None,
    }
