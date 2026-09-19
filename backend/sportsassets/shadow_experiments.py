"""BETTOR_EXPERIMENTAL_SHADOW: the frozen candidate registry.

Owner directive 2026-09-19 21:2xZ, "MAJOR SHADOW RESEARCH CORRECTION":

    FORCE OUR CANDIDATE MODELS TO MAKE FALSIFIABLE PROSPECTIVE TRADING
    DECISIONS SO WE CAN MEASURE WHETHER THEY HAVE EDGE.

THE TWO LANES ARE NOT TWO STANDARDS OF THE SAME THING. The
decision-grade lane answers "what would BETTOR be PERMITTED to do on
decision-grade evidence"; it may honestly stay at zero trades forever
and §1 forbids weakening it. This lane answers a different question --
"does this candidate have edge" -- and a candidate cannot answer it
without being made to commit, prospectively, to falsifiable trades.
Nothing here relaxes the other lane; this is a SEPARATE lane with its
own tables, its own books and its own word on every row.

WHY THIS MODULE TOUCHES NOTHING IN THE V2 BOUNDARY. BETTOR_EV_SHADOW_V2
froze a semantic hash over a named set of symbols in shadow_bettor.py,
shadow_lanes.py and shadow.py (see shadow_bettor_codesha.py). That hash
is ENFORCED: if the running code's digest stops matching the frozen
value, decision writing fails closed. So this module imports those
symbols and defines its own; it edits none of them. The test
`test_the_experimental_lane_did_not_move_the_v2_code_sha` is the proof,
and it is not decoration -- an accidental edit to `assert_lineage` here
would silently stop the decision-grade lane from writing at all.

THE DISTINCTION §5 TURNS ON, and the whole reason this lane can exist:

    MODEL NOT YET PROVEN   -> ALLOWED. That is the experiment.
    INPUT INVALID          -> REFUSED. That is not an experiment, it is
                              a measurement of nothing.

A candidate that has not proven positive EV is exactly what we are
testing, so "unproven" can never be a blocker here. A stale quote, an
unreadable book, a missing required feature or a market we cannot
identify are a different thing entirely: they corrupt the measurement
rather than being its subject. `input_blockers_for` is the closed
vocabulary of the second kind and nothing else may refuse a trade.

NOTHING HERE IS DECISION-GRADE AND EVERY ROW SAYS SO. `NOT_DECISION_GRADE
= True` is on the declaration, on the decision, on the trade and on the
book. Promotion to the decision-grade lane remains governed by the
existing frozen promotion framework, and §13 is explicit that it is
never automatic.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from . import shadow as sh
from . import shadow_lanes as lanes

# ── the lane ─────────────────────────────────────────────────────────
#
# A THIRD LANE, not a mode of the second. It is deliberately NOT added
# to lanes.LANES: that tuple gates `register_specialist` and
# `assert_lineage`, both of which belong to the decision-grade lane, and
# widening it would let an experimental row be written through a
# decision-grade path by a later caller who did not read this comment.

EXPERIMENTAL_LANE = "BETTOR_EXPERIMENTAL_SHADOW"
DECISION_GRADE_LANE = lanes.BETTOR_EV_SHADOW

# The words that must appear on every experimental artefact. §12: "Every
# record remains visibly: EXPERIMENTAL / NO REAL CAPITAL."
EXPERIMENTAL_DISCLOSURE = (
    "BETTOR EXPERIMENTAL SHADOW · NOT DECISION-GRADE · NO REAL CAPITAL · "
    "SIMULATED · COUNTERFACTUAL EXECUTION")

# ── §5: the two kinds of refusal, kept apart by name ─────────────────
#
# INPUT blockers only. "The former is allowed in EXPERIMENTAL SHADOW.
# The latter is not."

I_STALE_DATA = "STALE_DATA"
I_MISSING_MARKET_IDENTITY = "MISSING_MARKET_IDENTITY"
I_UNREADABLE_BOOK = "UNREADABLE_BOOK"
I_OUT_OF_DOMAIN = "OUT_OF_DOMAIN"
I_MISSING_REQUIRED_FEATURE = "MISSING_REQUIRED_FEATURE"
I_INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
I_NO_EXECUTABLE_DEPTH = "EXECUTABLE_DEPTH_NOT_IDENTIFIED"

INPUT_BLOCKERS = (I_STALE_DATA, I_MISSING_MARKET_IDENTITY,
                  I_UNREADABLE_BOOK, I_OUT_OF_DOMAIN,
                  I_MISSING_REQUIRED_FEATURE, I_INVALID_TIMESTAMP,
                  I_NO_EXECUTABLE_DEPTH)

# NOT A BLOCKER HERE, and named so that a later reader cannot quietly
# promote it into one. Every one of these is a reason the DECISION-GRADE
# lane refuses, and refusing on them here would rebuild that lane under
# a different name and measure nothing.
NOT_BLOCKERS_HERE = ("INDEPENDENT_EV_NOT_ESTABLISHED", "NO_FAIR_VALUE",
                     "ACTION_EV_BELOW_THRESHOLD", "P_FILL_NOT_IDENTIFIED",
                     "P_BETTOR_NOT_ESTABLISHED")

# ── §15 readiness: declared is not armed ─────────────────────────────
#
# A candidate may be declared long before the evidence it needs is
# captured. Saying so on the row is the difference between "we have five
# experiments" and "we have five experiments, one of which can run".

ARMED = "ARMED"
AWAITING_FEATURE = "AWAITING_FEATURE"
RETIRED = "RETIRED"
READINESS = (ARMED, AWAITING_FEATURE, RETIRED)

# ── §9: the control is a policy too ──────────────────────────────────

CANDIDATE = "CANDIDATE"
CONTROL = "CONTROL"
ROLES = (CANDIDATE, CONTROL)


class ExperimentRefusal(sh.ShadowRefusal):
    """A declaration or a decision this lane will not write."""


# ── §4: what must be frozen BEFORE the first outcome is known ────────

REQUIRED_DECLARATION_FIELDS = (
    "experimentId", "policyVersion", "modelVersion", "featureSet",
    "target", "horizon", "directionRule", "entryRule", "exitRule",
    "pairingRule", "cashoutRule", "latencyAssumption",
    "intendedNotionalUsd", "startTimestamp",
)


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def experiment_sha(declaration: dict) -> str:
    """The declaration's own hash, over everything but the hash.

    A LATER CHANGE IS A NEW VERSION, NEVER AN EDIT (§4). This is what
    makes that checkable rather than promised: two runs whose rules
    differ cannot share a hash, so a silently-edited rule shows up as a
    row whose stored sha no longer matches its own content.
    """
    body = {k: v for k, v in declaration.items()
            if k not in ("experimentSha", "writtenAt")}
    return hashlib.sha256(_canonical(body).encode()).hexdigest()[:16]


def declare(*, experiment_id, policy_version, model_version, feature_set,
            target, horizon, direction_rule, entry_rule, exit_rule,
            pairing_rule, cashout_rule, latency_assumption,
            start_timestamp, intended_notional_usd=None,
            role=CANDIDATE, readiness=AWAITING_FEATURE,
            control_for=None, required_features=(), notes=None) -> dict:
    """One frozen experimental policy. Pure -- no database.

    EVERY §4 FIELD IS REQUIRED, and none of them may be empty. An
    experiment whose exit rule is unstated is not an experiment: its
    results cannot be reproduced and its P&L is whatever the reader
    assumes. The refusal is deliberately at declaration time, because
    after the first outcome is known it is too late to add one honestly.
    """
    from . import shadow_bettor_sizing as sizing

    if role not in ROLES:
        raise ExperimentRefusal("refused: %r is not an experiment role"
                                % role)
    if readiness not in READINESS:
        raise ExperimentRefusal("refused: %r is not a readiness" % readiness)
    if role == CONTROL and not control_for:
        raise ExperimentRefusal(
            "refused: a CONTROL must name the candidate it is a control "
            "for; a baseline beside nothing answers no question")
    if horizon not in sh.HORIZONS:
        raise ExperimentRefusal(
            "refused: %r is not a declared horizon; the markout columns "
            "are %s" % (horizon, ", ".join(sh.HORIZONS)))

    # §6 / §15: the standard notional is the SIZING policy's, not a
    # number retyped here. Retyping it is how two $1,000s drift apart.
    notional = (sizing.STANDARD_BETTOR_SHADOW_NOTIONAL_USD
                if intended_notional_usd is None else intended_notional_usd)
    if not notional or float(notional) <= 0:
        raise ExperimentRefusal(
            "refused: intended notional must be positive")

    declaration = {
        "lane": EXPERIMENTAL_LANE,
        "experimentId": experiment_id,
        "role": role,
        "controlFor": control_for,
        "policyVersion": policy_version,
        "modelVersion": model_version,
        "featureSet": list(feature_set),
        "requiredFeatures": list(required_features),
        "target": target,
        "horizon": horizon,
        "directionRule": direction_rule,
        "entryRule": entry_rule,
        "exitRule": exit_rule,
        "pairingRule": pairing_rule,
        "cashoutRule": cashout_rule,
        "latencyAssumption": latency_assumption,
        "intendedNotionalUsd": float(notional),
        "sizingPolicyVersion": sizing.SIZING_POLICY_VERSION,
        "startTimestamp": start_timestamp,
        "readiness": readiness,
        # THE THREE THAT ARE NOT NEGOTIABLE AND ARE ON EVERY ROW.
        "notDecisionGrade": True,
        "realOrderSubmissionEnabled": False,
        "capitalAtRisk": 0,
        "disclosure": EXPERIMENTAL_DISCLOSURE,
        "notes": notes,
    }

    # A CONTROL'S EMPTY FEATURE SET IS ITS DEFINING PROPERTY, not an
    # omission: §9's null reads nothing on purpose, and demanding a
    # feature from it would force a comparator that is no longer null.
    # For a CANDIDATE an empty feature set is a rule with no inputs, and
    # that is still refused.
    required = [f for f in REQUIRED_DECLARATION_FIELDS
                if not (f == "featureSet" and role == CONTROL)]
    missing = [f for f in required
               if declaration.get(f) in (None, "", [], ())]
    if missing:
        raise ExperimentRefusal(
            "refused: %s declares no %s; §4 requires every rule frozen "
            "before the first outcome is known, and a rule added after "
            "an outcome is not a rule, it is a description"
            % (experiment_id, ", ".join(missing)))

    declaration["experimentSha"] = experiment_sha(declaration)
    return declaration


def verify(declaration: dict) -> dict:
    """Re-derive the stored hash. A mismatch is a rewritten rule."""
    stored = declaration.get("experimentSha")
    actual = experiment_sha(declaration)
    return {"experimentId": declaration.get("experimentId"),
            "storedSha": stored, "actualSha": actual,
            "matches": stored == actual}


# ── §5: the input gate, and ONLY the input gate ──────────────────────

# How old a quote may be before it stops being evidence of now. The
# collector's own cadence is the floor: a quote cannot be fresher than
# the interval at which it is read.
MAX_STALENESS_S = 120


def _aware(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def input_blockers_for(*, observed_at=None, now=None, symbol=None,
                       market_state=None, features=None,
                       required_features=(), domain_ok=True,
                       executable_book=None,
                       require_executable_depth=True) -> list:
    """Every reason the INPUT is unfit, and nothing about the model.

    RETURNING [] DOES NOT MEAN THE MODEL IS GOOD. It means the
    measurement is honest: we know which market this is, we read its
    book, the read is recent, the features the frozen policy named are
    present, and -- when the policy executes marketably -- there is
    observed depth to walk. Whether the candidate then makes money is
    the experiment.
    """
    out = []

    if not symbol:
        out.append(I_MISSING_MARKET_IDENTITY)

    at = _aware(observed_at)
    if at is None:
        out.append(I_INVALID_TIMESTAMP)
    else:
        ref = _aware(now) or datetime.now(tz=timezone.utc)
        age = (ref - at).total_seconds()
        # A TIMESTAMP FROM THE FUTURE IS NOT A FRESH ONE. Treating it as
        # fresh would make a clock fault look like the best data we have.
        if age < -5:
            out.append(I_INVALID_TIMESTAMP)
        elif age > MAX_STALENESS_S:
            out.append(I_STALE_DATA)

    if not market_state or not market_state.get("readable"):
        out.append(I_UNREADABLE_BOOK)
    else:
        staleness_ms = market_state.get("stalenessMs")
        if (staleness_ms is not None
                and float(staleness_ms) > MAX_STALENESS_S * 1000):
            out.append(I_STALE_DATA)

    if not domain_ok:
        out.append(I_OUT_OF_DOMAIN)

    have = features or {}
    for name in required_features:
        value = have.get(name)
        if value is None or value == sh.NOT_IDENTIFIED:
            out.append(I_MISSING_REQUIRED_FEATURE)
            break

    if require_executable_depth:
        # §6: "Never invent liquidity." With no observed depth there is
        # no honest executed notional, so the trade is refused rather
        # than filled at an assumed size. This is an INPUT failure, not
        # a judgement about the candidate.
        levels = (executable_book or {})
        if not (levels.get("bids") or levels.get("asks")):
            out.append(I_NO_EXECUTABLE_DEPTH)

    # Stable order, no duplicates: these are counted and grouped.
    return [b for b in INPUT_BLOCKERS if b in out]


def input_is_valid(**kwargs) -> bool:
    return not input_blockers_for(**kwargs)


# ── the experimental decision record ─────────────────────────────────


def experimental_decision(*, experiment, symbol, observed_at,
                          proposed_action, features,
                          declared_provenances=None,
                          input_blockers=(), signal=None,
                          bettor_opportunity_id=None, market_state_id=None,
                          event_id=None, outcome_leg=None,
                          intended_notional_usd=None, latency=None,
                          direction=None, why=None) -> dict:
    """One prospective experimental decision.

    §3: P_BETTOR IS NEVER MANUFACTURED HERE. The justification for an
    experimental action is the frozen candidate hypothesis named on the
    row, not a belief this lane does not hold. pBettorStatus stays
    NOT_ESTABLISHED exactly as it is in the decision-grade lane, and the
    row says which hypothesis stood behind the action instead.
    """
    if experiment.get("lane") != EXPERIMENTAL_LANE:
        raise ExperimentRefusal(
            "refused: %r is not an experimental declaration"
            % experiment.get("experimentId"))
    if proposed_action not in sh.ACTIONS:
        raise ExperimentRefusal("refused: %r is not an action"
                                % proposed_action)

    blockers = list(input_blockers)
    if blockers and proposed_action not in sh.NO_EXECUTION_ACTIONS:
        raise ExperimentRefusal(
            "refused: %s proposed %s with the input blocked by %s; an "
            "invalid input is not an experiment, it is a measurement of "
            "nothing" % (experiment["experimentId"], proposed_action,
                         ", ".join(blockers)))

    # THE INDEPENDENCE WALL STILL APPLIES. This lane is more permissive
    # about EVIDENCE OF EDGE; it is not more permissive about reading
    # RN1. A candidate that consumed an RN1 feature would be measuring
    # RN1, and the benchmark would be inside the thing it benchmarks.
    #
    # PROVENANCE IS DECLARED, NOT SNIFFED FROM THE VALUES. assert_lineage
    # takes {featureName: provenance}; `features` carries the VALUES and
    # is recorded, not inspected. Passing the values here would both
    # raise on a nested dict and, worse, invite a name-sniffing rule --
    # which "misses signal_17 and catches rn1_free_indicator".
    declared = declared_provenances
    if declared is None:
        declared = {name: lanes.PROV_MARKET_MICROSTRUCTURE
                    for name in (features or {})}
    lineage = lanes.assert_lineage(DECISION_GRADE_LANE, declared)
    if lineage["rn1FeaturesUsed"]:
        raise ExperimentRefusal(
            "refused: an experimental decision declared an RN1 feature; "
            "the independent lane does not consume RN1")

    return {
        "lane": EXPERIMENTAL_LANE,
        "experimentId": experiment["experimentId"],
        "experimentSha": experiment["experimentSha"],
        "policyVersion": experiment["policyVersion"],
        "modelVersion": experiment["modelVersion"],
        "role": experiment["role"],
        "symbol": symbol,
        "eventId": event_id,
        "outcomeLeg": outcome_leg,
        "observedAt": observed_at,
        "proposedAction": proposed_action,
        "direction": direction,
        "signal": signal,
        "features": features,
        "featureLineage": lineage["featureLineage"],
        "rn1FeaturesUsed": False,
        "inputBlockers": blockers,
        "inputValid": not blockers,
        "bettorOpportunityId": bettor_opportunity_id,
        "marketStateId": market_state_id,
        "intendedNotionalUsd": (experiment["intendedNotionalUsd"]
                                if intended_notional_usd is None
                                else intended_notional_usd),
        "latency": latency,
        # §3, on every row: the belief is NOT manufactured to justify
        # the action. The hypothesis is.
        "pBettor": None,
        "pBettorStatus": lanes.NOT_ESTABLISHED,
        "pFillStatus": lanes.NOT_IDENTIFIED,
        "justification": "FROZEN_CANDIDATE_HYPOTHESIS",
        # §12 / §14
        "notDecisionGrade": True,
        "decisionGrade": False,
        "shadowMode": True,
        "realOrderSubmissionEnabled": False,
        "capitalAtRisk": 0,
        "disclosure": EXPERIMENTAL_DISCLOSURE,
        "why": why,
    }


def decision_id(decision: dict) -> str:
    raw = "|".join(str(decision.get(k)) for k in (
        "experimentId", "experimentSha", "symbol", "outcomeLeg",
        "observedAt", "proposedAction"))
    return "xdec_" + hashlib.sha256(raw.encode()).hexdigest()[:40]
