"""THE EXPERIMENTAL ENGINE: seal at T0, execute at arrival, never both at once.

Owner directive 2026-09-19 22:4xZ §6/§7/§10, and 23:0xZ (the bridge).

THE ONE RULE THIS MODULE EXISTS TO ENFORCE. At T0 the features, the
model output, the action and the intended $1,000 are SEALED, and the
seal is hashed. Everything that happens afterwards -- the arrival, the
book, the walk, the markouts -- may SCORE that decision and may never
CHANGE it. `seal()` and `execute()` are separate calls returning
separate objects for exactly that reason: a single function that did
both could not be prevented from letting the second half inform the
first, and no test could tell you it had.

WHY THE ACTION AND THE EXECUTION STATUS ARE DIFFERENT FIELDS. §9: "Do
not convert it to NO_TRADE after seeing the signal." If X1 says BUY_NO
and the NO side's identity is not execution-eligible, the decision
RECORDS BUY_NO and the execution records
BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE. Collapsing those two into
NO_TRADE would erase the model's actual output from the evidence, and
the later question -- was the model right when we could not act? --
would be unanswerable.

WHAT IS NOT A BLOCKER HERE. An unproven model is the experiment. Only
INPUT failures refuse (shadow_experiments.input_blockers_for), plus the
identity gate, which is about whether the instrument is the contract --
not about whether the candidate is any good.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from . import shadow as sh
from . import shadow_exec_contract as ec
from . import shadow_experiment_signals as sig
from . import shadow_experiments as xp
from . import shadow_identity as ident
from . import shadow_l2 as l2

# §9's execution statuses, as the migration's CHECK spells them.
EXECUTED = "EXECUTED"
PARTIAL = "PARTIAL"
UNFILLED = "UNFILLED"
NOT_IDENTIFIED = "NOT_IDENTIFIED"
BLOCKED_IDENTITY = "BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE"
BLOCKED_INPUT = "BLOCKED_INPUT_INVALID"
NO_EXECUTION_INTENDED = "NO_EXECUTION_INTENDED"

# The actions X1's frozen rule may emit (§6).
BUY_YES = "BUY_YES"
BUY_NO = "BUY_NO"
NO_TRADE = "NO_TRADE"

# Which retail leg each action executes against, and therefore which
# identity binding must be exact before it may be reconstructed.
LEG_FOR_ACTION = {BUY_YES: "yes", BUY_NO: "no"}


class EngineRefusal(sh.ShadowRefusal):
    """Something this engine will not record."""


def _now():
    return datetime.now(tz=timezone.utc)


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def features_sha(features: dict) -> str:
    return hashlib.sha256(_canon(features).encode()).hexdigest()[:16]


def seal_sha(seal: dict) -> str:
    body = {k: v for k, v in seal.items() if k != "sealSha"}
    return hashlib.sha256(_canon(body).encode()).hexdigest()[:16]


def population_id(opportunity_ids, sealed_at, rule_sha) -> str:
    """§7: the eligible population, identified by its MEMBERSHIP.

    Derived from the sorted opportunity ids, so a population that
    gained or lost a member cannot carry the id of the one that did
    not -- which is what stops X1 and its control being scored on
    quietly different sets.
    """
    raw = _canon({"ids": sorted(opportunity_ids or []),
                  "at": sealed_at, "rule": rule_sha})
    return "pop_" + hashlib.sha256(raw.encode()).hexdigest()[:32]


# ── X1's frozen rule, applied ────────────────────────────────────────


def x1_action(mid_series, market_state) -> dict:
    """The frozen X1 rule -> exactly one of BUY_YES / BUY_NO / NO_TRADE.

    §6: "the frozen rule must prospectively produce exactly one." The
    thresholds and lookback are the registry's literals; nothing here
    chooses them, and nothing here may be tuned after an outcome.
    """
    gate = sig.m1_gate(market_state)
    if gate:
        return {"action": NO_TRADE, "direction": None, "signal": None,
                "why": gate, "modelOutput": None, "signalStrength": None}

    out = sig.short_horizon_direction(mid_series)
    if out["status"] != "MEASURED" or not out.get("actionable"):
        return {"action": NO_TRADE, "direction": out.get("direction"),
                "signal": out, "why": out.get("why"),
                "modelOutput": out, "signalStrength": out.get("value")}

    # LONG on the YES contract is BUY_YES; SHORT of it is BUY_NO, which
    # is the retail `no` leg and therefore the complement basket.
    action = BUY_YES if out["direction"] == sig.LONG else BUY_NO
    return {"action": action, "direction": out["direction"],
            "signal": out, "why": None, "modelOutput": out,
            "signalStrength": out.get("value")}


def control_action(mid_series, market_state) -> dict:
    """X1C on the SAME opportunity. Frozen: always long, reads nothing.

    It is gated by the SAME book condition as X1 so the two see the
    same eligible population -- §9's requirement. A control admitted on
    markets X1 refused would not be a control.
    """
    gate = sig.m1_gate(market_state)
    if gate:
        return {"action": NO_TRADE, "direction": None, "signal": None,
                "why": gate, "modelOutput": None, "signalStrength": None}
    out = sig.null_control()
    return {"action": BUY_YES, "direction": sig.LONG, "signal": out,
            "why": None, "modelOutput": out, "signalStrength": 0.0}


ACTION_FOR = {"X1_SHORT_HORIZON_DIRECTION": x1_action,
              "X1C_NULL_CONTROL": control_action}


# ── T0: the seal ─────────────────────────────────────────────────────


def seal(*, experiment, opportunity, mid_series, binding,
         eligible_population_id=None, at=None,
         feature_source_version=l2.FEATURE_SOURCE_VERSION) -> dict:
    """Seal one decision BEFORE any future data exists.

    `binding` is the identity binding for this market. It is read here
    only to record what was known at T0; whether it PERMITS execution
    is decided at arrival, so a decision is never suppressed because
    the side could not be executed.
    """
    if experiment.get("readiness") != xp.ARMED:
        raise EngineRefusal("refused: %s is not ARMED"
                            % experiment.get("experimentId"))
    rule = ACTION_FOR.get(experiment["experimentId"])
    if rule is None:
        raise EngineRefusal("refused: %s has no frozen rule bound to it"
                            % experiment["experimentId"])

    micro = (opportunity or {}).get("microstructure") or {}
    # §2/§3: only a YES-BOUND book from the corrected collector may
    # feed a feature series. A duplicated-leg row is historical
    # evidence, not an input.
    if micro.get("featureSourceVersion") != feature_source_version:
        raise EngineRefusal(
            "refused: this opportunity carries feature source %r, not the "
            "corrected %r; X1 does not evaluate on rows whose leg binding "
            "was wrong" % (micro.get("featureSourceVersion"),
                           feature_source_version))
    if not l2.leg_is_execution_bound(micro):
        raise EngineRefusal(
            "refused: the opportunity's book is %r, not the YES contract's"
            % micro.get("bboBinding"))

    at = at or _now()
    decided = rule(mid_series, micro)

    features = {
        "midSeries": list(mid_series or []),
        "bid": micro.get("bid"), "ask": micro.get("ask"),
        "mid": micro.get("mid"), "spreadRelative": micro.get(
            "spreadRelative"),
        "bboBinding": micro.get("bboBinding"),
    }

    sealed = {
        "experimentId": experiment["experimentId"],
        "experimentVersion": experiment["policyVersion"],
        "experimentSha": experiment["experimentSha"],
        "controlId": experiment.get("controlFor"),
        "eligiblePopulationId": eligible_population_id,
        # THE SUBJECT, whichever feed it came from. The decision-grade
        # collector's opportunity id and this lane's own observation id
        # are kept in separate fields rather than one generic "subject":
        # a reader must always be able to tell which feed a decision
        # was made on, because the two sample at different rates and a
        # rule about short-horizon drift means different things on each.
        "bettorOpportunityId": (opportunity or {}).get(
            "bettorOpportunityId"),
        "experimentalObservationId": (opportunity or {}).get(
            "experimentalObservationId"),
        "marketId": (opportunity or {}).get("symbol"),
        "outcomeLeg": (opportunity or {}).get("outcomeLeg"),
        "institutionalInstrumentId": (binding or {}).get(
            "institutional", {}).get("symbol"),
        "identityBindingStatus": (binding or {}).get(
            "verdict", ident.NOT_IDENTIFIED),
        "identityBindingSha": (binding or {}).get("identityBindingSha"),
        "featureSourceVersion": feature_source_version,
        "featureAsof": (opportunity or {}).get("observedAt") or at,
        "features": features,
        "featuresSha": features_sha(features),
        "modelOutput": decided["modelOutput"],
        "signalStrength": decided["signalStrength"],
        "action": decided["action"],
        "direction": decided["direction"],
        "decisionTimestamp": at,
        "intendedNotionalUsd": float(experiment["intendedNotionalUsd"]),
        "why": decided["why"],
        # never anything but shadow
        "notDecisionGrade": True,
        "realOrderSubmitted": False,
        "capitalAtRisk": 0,
    }
    sealed["sealSha"] = seal_sha(sealed)
    return sealed


# ── arrival: the execution, which may only SCORE the seal ────────────


def execute(sealed: dict, *, evidence=None, binding=None,
            arrival_at=None, input_blockers=()) -> dict:
    """Reconstruct the arrival walk against institutional L2.

    NOTHING HERE MAY ALTER THE SEAL. It is read, never written: the
    returned object is the execution, and the caller persists both.
    """
    if not isinstance(sealed, dict) or "sealSha" not in sealed:
        raise EngineRefusal("refused: an execution without a sealed decision")
    if seal_sha(sealed) != sealed["sealSha"]:
        raise EngineRefusal(
            "refused: the seal does not re-derive; this decision was "
            "altered after T0 and its execution cannot be trusted")

    base = {
        "experimentalDecisionId": decision_id(sealed),
        "arrivalTimestamp": arrival_at,
        "executedNotionalUsd": None, "unfilledNotionalUsd": None,
        "filledQty": None, "vwap": None, "slippage": None,
        "spreadCost": None, "positionId": None,
        "l2EvidenceId": (evidence or {}).get("l2EvidenceId"),
        "latencyRegime": (evidence or {}).get("latencyRegime"),
        "observedArrivalLatencyMs": (evidence or {}).get("bridgeLatencyMs"),
        "executionContract": ec.CONTRACT_VERSION,
        "executionContractSha": ec.CONTRACT_SHA,
        # the evidence's own digest, which joins this row to the book
        "l2BookSha": (evidence or {}).get("l2BookSha"),
        # the frozen contract's digest over what the walk consumed
        "walkedBookSha": None,
        "l2SourceTimestamp": (evidence or {}).get("l2SourceTimestamp"),
        "l2ReceivedTimestamp": (evidence or {}).get("l2ReceivedTimestamp"),
        "priceScale": (evidence or {}).get("priceScale"),
        "quantityScale": (evidence or {}).get("qtyScale"),
    }

    if input_blockers:
        return dict(base, executionStatus=BLOCKED_INPUT,
                    why=", ".join(input_blockers))

    action = sealed.get("action")
    if action == NO_TRADE:
        # A refusal is a decision and is recorded as one. There is
        # nothing to execute, and that is not a failure.
        return dict(base, executionStatus=NO_EXECUTION_INTENDED,
                    why=sealed.get("why"))

    # §4 of the identity directive: only an exact binding may feed
    # execution, and the basket must additionally be walkable.
    leg = LEG_FOR_ACTION.get(action)
    try:
        ident.assert_execution_eligible(
            binding, basket_walkable=(binding or {}).get("basketWalkable"))
    except ident.IdentityRefusal as exc:
        # THE ACTION SURVIVES. "Do not convert it to NO_TRADE after
        # seeing the signal."
        return dict(base, executionStatus=BLOCKED_IDENTITY,
                    why="%s side %r: %s" % (action, leg, exc))

    # THE DEPTH THAT MATTERS IS THE SIDE WE WOULD TAKE. A book with
    # bids and no offers is not "a book"; for a BUY it is no depth at
    # all, and checking "either side is non-empty" would send it into
    # the walk to come back as a zero that looks measured.
    if not evidence or not evidence.get("asks"):
        return dict(base, executionStatus=NOT_IDENTIFIED,
                    why="no arrival L2 with observed depth on the side "
                        "taken; no P&L-bearing trade is created")

    decision_price = (sealed.get("features") or {}).get("ask") \
        or (sealed.get("features") or {}).get("mid")
    try:
        econ = ec.economics(
            book=evidence, side=sh.BUY,
            intended_notional_usd=sealed["intendedNotionalUsd"],
            limit_price=None, decision_price=decision_price)
    except ec.ContractViolation as exc:
        return dict(base, executionStatus=NOT_IDENTIFIED, why=str(exc))

    status = {sh.FILLED: EXECUTED, sh.PARTIAL: PARTIAL,
              sh.UNFILLED: UNFILLED,
              sh.NOT_IDENTIFIED: NOT_IDENTIFIED}.get(econ["status"],
                                                     NOT_IDENTIFIED)
    # TWO DIGESTS OVER ONE BOOK, AND THEY ARE NOT INTERCHANGEABLE. The
    # bridge's `l2BookSha` is taken over the VENUE'S RAW LEVELS and is
    # how this row joins back to the evidence that produced it; the
    # contract's is taken over the PARSED book this walk actually
    # consumed. Writing the second one into the first one's column
    # would make the decision and its evidence disagree about a book
    # neither of them changed -- so the evidence's sha is kept where a
    # reader expects it, and the walked digest travels beside it.
    walked_sha = econ.get("l2BookSha")
    if status == NOT_IDENTIFIED:
        # UNFILLED AND NOT_IDENTIFIED ARE DIFFERENT FACTS. Unfilled
        # means the book was walked and gave nothing; not-identified
        # means it could not be walked. Reporting $0.00 executed for
        # the second would assert a measurement nobody made, and the
        # accounting would then count it as a real zero.
        return dict(base, executionStatus=NOT_IDENTIFIED,
                    walkedBookSha=walked_sha,
                    why=econ.get("why") or "the arrival book could not be "
                                           "walked under the frozen contract")
    out = dict(base,
               executionStatus=status,
               executedNotionalUsd=econ["executedNotionalUsd"],
               unfilledNotionalUsd=econ["unfilledNotionalUsd"],
               filledQty=econ["filledQty"], vwap=econ["vwap"],
               slippage=econ["slippage"], spreadCost=econ["spreadCost"],
               walkedBookSha=walked_sha, why=econ.get("why"))
    if status in (EXECUTED, PARTIAL) and (econ["executedNotionalUsd"] or 0) > 0:
        out["positionId"] = position_id(out["experimentalDecisionId"])
    return out


def decision_id(sealed: dict) -> str:
    raw = "|".join(str(sealed.get(k)) for k in (
        "experimentId", "experimentSha", "marketId", "outcomeLeg",
        "decisionTimestamp", "sealSha"))
    return "xdec_" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def position_id(decision_identifier: str) -> str:
    return "xpos_" + hashlib.sha256(
        str(decision_identifier).encode()).hexdigest()[:36]
