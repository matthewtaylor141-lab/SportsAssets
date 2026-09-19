"""TWO SHADOW LANES, PERMANENTLY SEPARATED. No network, no orders.

Owner directive 2026-09-19: build both, do not choose mirror-only, and
do not invent P_BETTOR merely to make the BETTOR lane produce trades.

  LANE A  RN1_SHADOW        "what would have happened if BETTOR
                            prospectively observed RN1 and applied its
                            OWN latency, execution, pairing, cash-out
                            and inventory logic?" A MECHANISM benchmark.
                            P_BETTOR and INFORMATION_EV are
                            NOT_ESTABLISHED here, permanently, because a
                            fair value manufactured from RN1 activity
                            would be RN1's opinion wearing our name.

  LANE B  BETTOR_EV_SHADOW  the proprietary intelligence lane. It exists
                            and records from today; it trades only when
                            independent EV is earned. Until then its
                            honest output is
                            NO_TRADE / INDEPENDENT_EV_NOT_ESTABLISHED,
                            which is itself training evidence.

They share infrastructure, execution reconstruction, accounting and
COMMAND presentation. They share NO predictive label, model output or
feature that would destroy attribution.

WHY LANE TAGGING IS IN THE FOUNDATION rather than added later: the same
reason the ledger is immutable. Attribution lost at write time cannot be
recovered by any later query. A row that does not know its lane is not
repairable evidence -- it is a guess about its own origin.

THE LINEAGE RULE FAILS CLOSED. A BETTOR-lane feature whose provenance is
undeclared is refused, not admitted with a warning. Ambiguity resolved
in favour of "probably fine" is how contamination enters a dataset that
is supposed to prove independence.
"""
from __future__ import annotations

from sportsassets import shadow as sh

# ── the two lanes ─────────────────────────────────────────────────────

RN1_SHADOW = "RN1_SHADOW"
BETTOR_EV_SHADOW = "BETTOR_EV_SHADOW"
LANES = (RN1_SHADOW, BETTOR_EV_SHADOW)

SIGNAL_RN1 = "RN1"
SIGNAL_BETTOR_INDEPENDENT = "BETTOR_INDEPENDENT"

NOT_ESTABLISHED = "NOT_ESTABLISHED"
NOT_IDENTIFIED = sh.NOT_IDENTIFIED
ESTABLISHED = "ESTABLISHED"

REASON_EV_NOT_ESTABLISHED = "INDEPENDENT_EV_NOT_ESTABLISHED"


class LaneViolation(sh.ShadowRefusal):
    """A claim that would destroy attribution between the lanes."""


class LineageViolation(LaneViolation):
    """RN1 evidence reaching the independent lane."""


class LineageAmbiguous(LaneViolation):
    """Provenance undeclared. Refused rather than assumed harmless."""


# ── 7. LINEAGE ────────────────────────────────────────────────────────
#
# The five things management named, as declared provenances rather than
# as a substring search over feature names. A name-sniffing rule misses
# `signal_17` and catches `rn1_free_indicator`; a declaration cannot be
# wrong by accident, only by lying, and a lie is a different problem.

PROV_RN1_ACTION = "RN1_ACTION"
PROV_RN1_ACCOUNT_IDENTITY = "RN1_ACCOUNT_IDENTITY"
PROV_RN1_FUTURE_ACTION = "RN1_FUTURE_ACTION"
PROV_RN1_DERIVED_TARGET = "RN1_DERIVED_TARGET"
PROV_RN1_MIRROR_DECISION = "RN1_MIRROR_DECISION"

RN1_PROVENANCES = frozenset({
    PROV_RN1_ACTION, PROV_RN1_ACCOUNT_IDENTITY, PROV_RN1_FUTURE_ACTION,
    PROV_RN1_DERIVED_TARGET, PROV_RN1_MIRROR_DECISION})

# The independent lane's admissible sources. Note what is NOT here.
PROV_MARKET_MICROSTRUCTURE = "MARKET_MICROSTRUCTURE"
PROV_L2 = "L2"
PROV_ORDER_FLOW = "ORDER_FLOW"
PROV_SPREAD = "SPREAD"
PROV_DEPTH = "DEPTH"
PROV_CROSS_MARKET_RELATIVE_VALUE = "CROSS_MARKET_RELATIVE_VALUE"
PROV_EXTERNAL_CONSENSUS = "EXACT_TIMESTAMP_EXTERNAL_CONSENSUS"
PROV_SPORT_FUNDAMENTALS = "SPORT_FUNDAMENTALS"
PROV_PLAYER_DATA = "XG_PLAYER_DATA"
PROV_MODEL_DISAGREEMENT = "MODEL_DISAGREEMENT"
PROV_SHORT_HORIZON_PRICE = "SHORT_HORIZON_PRICE_PREDICTION"
PROV_EXECUTION_STATE = "EXECUTION_STATE"

INDEPENDENT_PROVENANCES = frozenset({
    PROV_MARKET_MICROSTRUCTURE, PROV_L2, PROV_ORDER_FLOW, PROV_SPREAD,
    PROV_DEPTH, PROV_CROSS_MARKET_RELATIVE_VALUE, PROV_EXTERNAL_CONSENSUS,
    PROV_SPORT_FUNDAMENTALS, PROV_PLAYER_DATA, PROV_MODEL_DISAGREEMENT,
    PROV_SHORT_HORIZON_PRICE, PROV_EXECUTION_STATE})

DECLARED_PROVENANCES = RN1_PROVENANCES | INDEPENDENT_PROVENANCES


def assert_lineage(lane: str, features, specialist=None) -> dict:
    """Refuse RN1 evidence in the independent lane. Fail closed.

    `features` maps feature name -> declared provenance. A feature with
    no declaration, or a declaration outside the registry, is AMBIGUOUS
    and is refused: "fail closed on lineage ambiguity" means the
    unknown case is the refused case, not the permitted one.

    The one exception management allowed: a model explicitly registered
    as an RN1 specialist may carry RN1 provenance. When it does, the
    fact travels on the record as `rn1FeaturesUsed`, so nothing is
    silently reclassified as independent later.
    """
    if lane not in LANES:
        raise LaneViolation("refused: %r is not a lane" % lane)

    if features is None:
        raise LineageAmbiguous(
            "refused: %s declared no feature lineage at all. An empty "
            "declaration and a clean one are different claims." % lane)

    declared = dict(features)
    undeclared = [name for name, prov in declared.items()
                  if not prov or prov not in DECLARED_PROVENANCES]
    if undeclared:
        raise LineageAmbiguous(
            "refused: provenance undeclared or unrecognised for %s. "
            "Ambiguity resolved in favour of 'probably fine' is how "
            "contamination enters a dataset meant to prove independence."
            % ", ".join(sorted(undeclared)))

    rn1_used = sorted(name for name, prov in declared.items()
                      if prov in RN1_PROVENANCES)

    if lane == BETTOR_EV_SHADOW and rn1_used:
        registered = bool(specialist
                          and specialist.get("rn1Specialist") is True)
        if not registered:
            raise LineageViolation(
                "refused: %s carries RN1 provenance (%s) and no model is "
                "registered as an RN1 specialist. The independent lane's "
                "whole claim is that it did not look at RN1."
                % (BETTOR_EV_SHADOW, ", ".join(rn1_used)))

    return {"lane": lane,
            "featureLineage": declared,
            "rn1FeaturesUsed": bool(rn1_used),
            "rn1Features": rn1_used,
            "specialist": (specialist or {}).get("specialistId")}


# ── 4. THREE PROBABILITY OBJECTS, NEVER COLLAPSED ────────────────────

def probabilities(p_market=None, p_bettor=None, p_fill=None,
                  lane: str = None) -> dict:
    """P_MARKET, P_BETTOR and P_FILL answer different questions.

    P_MARKET  what the market implies.
    P_BETTOR  BETTOR's own independently validated estimate.
    P_FILL    the chance an order executes under its mechanics.

    "If P_BETTOR does not exist: P_BETTOR = NOT_ESTABLISHED, not
    P_MARKET and not zero." Both of those substitutions are silent and
    both produce a system that looks like it has an opinion.

    In RN1_SHADOW, P_BETTOR is NOT_ESTABLISHED by construction and
    supplying one is refused -- RN1's activity is not our fair value.
    """
    if lane == RN1_SHADOW and p_bettor is not None:
        raise LaneViolation(
            "refused: RN1_SHADOW does not carry an independent belief. "
            "A fair value manufactured from RN1 activity is RN1's "
            "opinion wearing our name.")

    return {
        "pMarket": p_market,
        "pMarketStatus": ESTABLISHED if p_market is not None
                         else NOT_IDENTIFIED,
        "pBettor": p_bettor,
        "pBettorStatus": ESTABLISHED if p_bettor is not None
                         else NOT_ESTABLISHED,
        "pFill": p_fill,
        "pFillStatus": ESTABLISHED if p_fill is not None
                       else NOT_IDENTIFIED,
    }


# ── 5. ACTION EV ──────────────────────────────────────────────────────
#
# "Evaluate actions rather than merely predictions." Every component is
# named; a missing LOAD-BEARING one leaves the EV NOT_IDENTIFIED rather
# than being quietly treated as zero, because zero is a claim.

EV_COMPONENTS = (
    "valueAdvantage", "fillProbability", "arrivalPrice", "spread",
    "depth", "slippage", "feesRebates", "toxicity", "inventoryCost",
    "capitalTime", "pairingOpportunity", "exitAlternatives",
    "settlementValue")

# Without these the number is not an expectation, it is a wish.
LOAD_BEARING = ("valueAdvantage", "arrivalPrice", "slippage", "feesRebates")
# A resting order's EV depends on whether it fills at all.
LOAD_BEARING_PASSIVE = LOAD_BEARING + ("fillProbability",)


def action_ev(action: str, components: dict) -> dict:
    """Sum the identified components, or say which one is missing.

    Returns `ev=None` with `status=NOT_IDENTIFIED` whenever a
    load-bearing term is absent. The missing terms are NAMED, so the gap
    is a work item rather than a mystery.
    """
    if action not in sh.ACTIONS:
        raise sh.ShadowRefusal("refused: %r is not in the action "
                               "vocabulary" % action)
    supplied = dict(components or {})
    unknown_keys = [k for k in supplied if k not in EV_COMPONENTS]
    if unknown_keys:
        raise sh.ShadowRefusal(
            "refused: %s are not named EV components. An unnamed term "
            "cannot be audited." % ", ".join(sorted(unknown_keys)))

    required = (LOAD_BEARING_PASSIVE if action in sh.PASSIVE_ACTIONS
                else LOAD_BEARING)
    missing = [k for k in required if supplied.get(k) is None]
    identified = {k: float(v) for k, v in supplied.items()
                  if v is not None}
    absent = [k for k in EV_COMPONENTS if supplied.get(k) is None]

    if action in sh.NO_EXECUTION_ACTIONS:
        # NO_TRADE and HOLD have no execution to price. That is not a
        # missing measurement.
        return {"action": action, "ev": None, "status": "NO_EXECUTION",
                "components": identified, "missing": [],
                "notIdentified": absent,
                "why": "%s makes no execution, so there is no action EV "
                       "to compute" % action}

    if missing:
        return {"action": action, "ev": None, "status": NOT_IDENTIFIED,
                "components": identified, "missing": sorted(missing),
                "notIdentified": absent,
                "why": "load-bearing terms are not identified: %s. "
                       "Treating them as zero would make the EV a wish."
                       % ", ".join(sorted(missing))}

    return {"action": action,
            "ev": sum(identified.values()),
            "status": "IDENTIFIED",
            "components": identified,
            "missing": [],
            "notIdentified": absent,
            "why": None}


# ── 6. SPECIALIST INTERFACES ──────────────────────────────────────────

SPECIALIST_KINDS = (
    "SETTLEMENT_FAIR_VALUE", "SHORT_HORIZON_30S", "SHORT_HORIZON_60S",
    "SHORT_HORIZON_300S", "RELATIVE_VALUE", "PAIR_COMPLETION",
    "TOXICITY", "FILL", "CASHOUT", "INVENTORY", "SPORT_SPECIFIC")


def register_specialist(specialist_id, kind, name, version, lane,
                        rn1_specialist=False, promoted=False) -> dict:
    """An interface, not a model. Registering one trains nothing.

    `promoted` stays False: promotion remains prospective and gated, and
    a registry entry is not a promotion.
    """
    if kind not in SPECIALIST_KINDS:
        raise LaneViolation("refused: %r is not a specialist kind" % kind)
    if lane not in LANES:
        raise LaneViolation("refused: %r is not a lane" % lane)
    if promoted:
        raise LaneViolation(
            "refused: a specialist cannot be registered already promoted. "
            "Promotion is prospective and gated, and registering an "
            "interface is not evidence about a model.")
    return {"specialistId": specialist_id, "kind": kind, "name": name,
            "version": version, "lane": lane,
            "rn1Specialist": bool(rn1_specialist), "promoted": False}


# ── 8. THE DISAGREEMENT DATASET ───────────────────────────────────────

RN1_ONLY = "RN1_ONLY"
BETTOR_ONLY = "BETTOR_ONLY"
BOTH_AGREE = "BOTH_AGREE"
DISAGREE_DIRECTION = "DISAGREE_DIRECTION"
BOTH_NO_TRADE = "BOTH_NO_TRADE"
BETTOR_NOT_YET_ELIGIBLE = "BETTOR_NOT_YET_ELIGIBLE"

DISAGREEMENT_CLASSES = (RN1_ONLY, BETTOR_ONLY, BOTH_AGREE,
                        DISAGREE_DIRECTION, BOTH_NO_TRADE,
                        BETTOR_NOT_YET_ELIGIBLE)

_DIRECTIONAL = {sh.BUY: "LONG", sh.SELL: "SHORT", sh.CASH_OUT: "SHORT",
                sh.REDUCE: "SHORT"}


def classify_disagreement(rn1_action=None, bettor_action=None,
                          bettor_reason=None) -> dict:
    """What the two lanes said about the same opportunity.

    BETTOR_NOT_YET_ELIGIBLE is its own class and is checked FIRST: a
    NO_TRADE because independent EV does not exist yet is a different
    fact from a NO_TRADE because the opportunity was judged and
    declined, and collapsing them would make the eventual comparison
    meaningless.
    """
    acted = lambda a: bool(a) and a != sh.NO_TRADE          # noqa: E731

    if bettor_reason == REASON_EV_NOT_ESTABLISHED:
        return {"classification": BETTOR_NOT_YET_ELIGIBLE,
                "why": "BETTOR has no independent EV yet; this is not a "
                       "judgement about the opportunity"}

    if not acted(rn1_action) and not acted(bettor_action):
        return {"classification": BOTH_NO_TRADE,
                "why": "both lanes declined, which is first-class "
                       "evidence and is retained"}
    if acted(rn1_action) and not acted(bettor_action):
        return {"classification": RN1_ONLY, "why": None}
    if acted(bettor_action) and not acted(rn1_action):
        return {"classification": BETTOR_ONLY, "why": None}

    rn1_dir = _DIRECTIONAL.get(rn1_action)
    bettor_dir = _DIRECTIONAL.get(bettor_action)
    if rn1_dir and bettor_dir and rn1_dir != bettor_dir:
        return {"classification": DISAGREE_DIRECTION,
                "why": "opposite directions on the same leg"}
    return {"classification": BOTH_AGREE, "why": None}


# ── 9. COMMAND: TWO ENVIRONMENTS, NEVER ONE HEADLINE ─────────────────

def lane_report(rows_by_lane: dict) -> dict:
    """Economics per lane, with no combined P&L anywhere.

    "Never aggregate them into one headline P&L." A mechanism benchmark
    and an intelligence claim are different assertions; adding them
    produces a number that supports neither.
    """
    unknown = [k for k in (rows_by_lane or {}) if k not in LANES]
    if unknown:
        raise LaneViolation("refused: %r is not a lane"
                            % ", ".join(sorted(unknown)))
    out = {lane: sh.economics((rows_by_lane or {}).get(lane) or [])
           for lane in LANES}
    return {"byLane": out,
            "combinedPnl": None,
            "disclosure": sh.DISCLOSURE,
            "note": ("no combined P&L is reported: RN1_SHADOW is a "
                     "mechanism benchmark and BETTOR_EV_SHADOW is an "
                     "intelligence claim, and one number would support "
                     "neither")}


def rn1_lane_decision(**fields) -> dict:
    """An RN1_SHADOW decision, with the belief fields held closed."""
    fields.setdefault("evidenceSource", SIGNAL_RN1)
    record = sh.decision_record(**fields)
    record["lane"] = RN1_SHADOW
    record["signalSource"] = SIGNAL_RN1
    record["pBettorStatus"] = NOT_ESTABLISHED
    record["informationEv"] = None
    record["rn1FeaturesUsed"] = True
    return record


def bettor_lane_decision(features, specialist=None, **fields) -> dict:
    """A BETTOR_EV_SHADOW decision. Lineage is checked before anything.

    An honest NO_TRADE here is the expected output until independent EV
    is earned, and it is recorded rather than suppressed.
    """
    lineage = assert_lineage(BETTOR_EV_SHADOW, features, specialist)
    fields.setdefault("evidenceSource", SIGNAL_BETTOR_INDEPENDENT)
    record = sh.decision_record(**fields)
    record["lane"] = BETTOR_EV_SHADOW
    record["signalSource"] = SIGNAL_BETTOR_INDEPENDENT
    record["featureLineage"] = lineage["featureLineage"]
    record["rn1FeaturesUsed"] = lineage["rn1FeaturesUsed"]
    return record


def not_yet_eligible(**fields) -> dict:
    """The BETTOR lane's honest output before independent EV exists."""
    fields["proposedAction"] = sh.NO_TRADE
    fields.setdefault("reasonCodes", [REASON_EV_NOT_ESTABLISHED])
    fields.setdefault("features", {})
    features = fields.pop("features")
    record = bettor_lane_decision(features, **fields)
    record["pBettor"] = None
    record["pBettorStatus"] = NOT_ESTABLISHED
    record["informationEv"] = None
    return record
