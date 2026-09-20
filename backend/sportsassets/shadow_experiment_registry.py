"""THE FROZEN EXPERIMENTS. Declared before the first outcome is known.

Owner directive 2026-09-19 21:2xZ §4: "Freeze these BEFORE the first
outcome is known. A later change creates a new version. Never rewrite
prior experimental trades."

EVERY VALUE IN THIS FILE IS A LITERAL, and that is the point. A
threshold read from an environment variable, a start timestamp computed
at import, a notional taken from a config -- each would let a running
experiment's rules move without a new version, and the hash that is
supposed to make that impossible would move with them silently. So the
rules are typed here, the declaration hashes them, and the hash is
stored on the first row the experiment ever writes. `verify()` then
re-derives it at any later date: if a rule was edited under a live
experiment, the stored and re-derived hashes disagree and the
leaderboard says so instead of quietly reporting the new rule's results
under the old rule's history.

WHY START_UTC IS A TYPED STRING AND NOT `_now()`. A start stamped at
import changes every boot, so the declaration's hash would change every
boot, and every row would claim a different experiment. The freeze
instant is a fact about the experiment, not about the process that
loaded it.

WHAT IS ARMED, AND WHY THE REST ARE NOT. §3 names five candidate
families. Two can be computed from evidence the collector genuinely
captures; three cannot, and are declared AWAITING_FEATURE rather than
approximated. This is the §3 constraint applied honestly in both
directions: we do not invent a model to create activity, AND we do not
silently drop a model the directive named -- it is on the leaderboard
saying exactly which feature it is waiting for.

    X1  M1 SHORT_HORIZON_DIRECTION_SCORE   ARMED
    X1C the frozen null beside X1           ARMED   (§9)
    X2  M4 RELATIVE_VALUE_SIGNAL            ARMED once both legs land
    X3  M2 MICROPRICE_SIGNAL                AWAITING touch sizes
    X4  M3 OFI_SIGNAL                       AWAITING size deltas
    X5  M5 EXTERNAL_DISAGREEMENT_SIGNAL     AWAITING an external feed

NOTHING HERE IS PROMOTED AND NOTHING HERE PROMOTES ITSELF (§13).
Promotion to the decision-grade lane remains governed by the existing
frozen promotion framework, which this module neither reads nor calls.
"""

from __future__ import annotations

from . import shadow as sh
from . import shadow_exit_spec as xspec
from . import shadow_exit_spec_v3 as xspec3
from . import shadow_experiment_signals as sig
from . import shadow_experiments as xp

# The instant the first experimental policy was frozen. A LITERAL --
# see the module docstring.
START_UTC = "2026-09-19T21:30:00Z"

REGISTRY_VERSION = "BETTOR_EXPERIMENT_REGISTRY_V1"

# The latency the reconstruction charges every experimental arrival.
# OBSERVED where we have a measurement, SCENARIO where we do not; the
# word travels on the trade so a later reader never mistakes an
# assumption for a measurement.
ARRIVAL_LATENCY_MS = 250
LATENCY_ASSUMPTION = (
    "SCENARIO %dms from signal to simulated arrival, charged against the "
    "book observed at arrival rather than at decision; replaced by the "
    "OBSERVED figure on any row where one is measured" % ARRIVAL_LATENCY_MS)

# Shared rules, typed once so two experiments cannot drift apart by a
# typo. They are still hashed into each declaration separately.
EXIT_RULE_HORIZON = (
    "exit at the frozen horizon by marketable reconstruction against the "
    "book observed at that instant; no discretionary hold, no averaging "
    "down, no re-entry inside the horizon")
PAIRING_RULE_NONE = (
    "no pairing: a single leg is taken and exited on the same leg; the "
    "complement is never bought to lock a loss")
CASHOUT_RULE_NONE = (
    "no cash-out: the venue's cash-out mechanic is not identified for "
    "this lane, so it is never assumed available")


def _declare(**kw):
    return xp.declare(latency_assumption=LATENCY_ASSUMPTION,
                      start_timestamp=START_UTC, **kw)


# ── X1: the first armed candidate ────────────────────────────────────

X1 = _declare(
    experiment_id="X1_SHORT_HORIZON_DIRECTION",
    policy_version="BETTOR_EXP_SHORT_HORIZON_V1",
    model_version="short_horizon_direction_v1",
    feature_set=("microstructure.mid", "microstructure.spreadRelative"),
    required_features=("mid",),
    target="mid at +60s versus mid at arrival",
    horizon="60S",
    direction_rule=(
        "signed drift of the mid over the last %d captured samples "
        "(minimum %d); LONG above +%.4f, SHORT below -%.4f, otherwise "
        "FLAT and no trade"
        % (sig.M1_LOOKBACK_SAMPLES, sig.M1_MIN_SAMPLES,
           sig.M1_ENTRY_THRESHOLD, sig.M1_ENTRY_THRESHOLD)),
    entry_rule=(
        "marketable reconstruction of $%d intended notional against the "
        "observed executable book at simulated arrival; refuse the trade "
        "when relative spread exceeds %.2f, when the input is blocked, "
        "or when no executable depth is observed -- never a filled size "
        "the book did not show"
        % (1000, sig.M1_MAX_SPREAD_RELATIVE)),
    exit_rule=EXIT_RULE_HORIZON,
    pairing_rule=PAIRING_RULE_NONE,
    cashout_rule=CASHOUT_RULE_NONE,
    readiness=xp.ARMED,
    notes=("The first prospective BETTOR experimental candidate. Its "
           "purpose is to be cheaply falsifiable: if naive momentum on "
           "this venue's books has no edge, that is a real finding and "
           "only a prospective commitment can produce it."),
)

# ── X1C: §9's frozen null, beside X1 ─────────────────────────────────

X1C = _declare(
    experiment_id="X1C_NULL_CONTROL",
    role=xp.CONTROL,
    control_for="X1_SHORT_HORIZON_DIRECTION",
    policy_version="BETTOR_EXP_NULL_CONTROL_V1",
    model_version="null_control_v1",
    feature_set=(),
    required_features=(),
    target="mid at +60s versus mid at arrival",
    horizon="60S",
    direction_rule=(
        "always LONG; reads no feature and ignores every signal, so any "
        "difference against X1 on the same markets at the same instants "
        "is X1's information and not the market's direction"),
    entry_rule=(
        "identical to X1's entry in every respect except the direction "
        "rule -- same notional, same book, same arrival, same input "
        "gate; a control that traded a different tape would compare "
        "nothing"),
    exit_rule=EXIT_RULE_HORIZON,
    pairing_rule=PAIRING_RULE_NONE,
    cashout_rule=CASHOUT_RULE_NONE,
    readiness=xp.ARMED,
    notes=("§9: 'We need to learn: DID THE MODEL ADD VALUE? not merely: "
           "DID THE MARKET GO UP?'"),
)

# ── X2: relative value, armed once the complement is captured ────────

X2 = _declare(
    experiment_id="X2_RELATIVE_VALUE",
    policy_version="BETTOR_EXP_RELATIVE_VALUE_V1",
    model_version="relative_value_v1",
    feature_set=("microstructure.ask", "complement.ask"),
    required_features=("legAsk", "complementAsk"),
    target=("realised value of the completed pair against its cost at "
            "arrival"),
    horizon="300S",
    direction_rule=(
        "both legs' asks summed against the $%.2f pair basis; actionable "
        "when the dislocation is at least %.4f, LONG the pair when it is "
        "cheap and SHORT when it is dear"
        % (sig.M4_PAIR_BASIS, sig.M4_ENTRY_THRESHOLD)),
    entry_rule=(
        "marketable reconstruction on BOTH legs at one observed instant; "
        "a pair filled on one leg only is a naked leg, so a partial "
        "reconstruction on either side is recorded as UNFILLED rather "
        "than as half a pair"),
    exit_rule=EXIT_RULE_HORIZON,
    pairing_rule=(
        "the pair IS the position: both legs are entered together and "
        "measured together; neither leg is held alone"),
    cashout_rule=CASHOUT_RULE_NONE,
    readiness=xp.AWAITING_FEATURE,
    notes=("Arms as soon as the collector captures the complement leg "
           "beside its subject at the same instant. The only candidate "
           "of the five whose edge is a price relation rather than a "
           "forecast, and so the cheapest to falsify."),
)

# ── X3, X4, X5: declared, unarmed, and saying why ────────────────────

X3 = _declare(
    experiment_id="X3_MICROPRICE",
    policy_version="BETTOR_EXP_MICROPRICE_V1",
    model_version="microprice_v1",
    feature_set=("microstructure.bid", "microstructure.ask",
                 "microstructure.bidSize", "microstructure.askSize"),
    required_features=("bidSize", "askSize"),
    target="mid at +30s versus microprice at arrival",
    horizon="30S",
    direction_rule=(
        "size-weighted touch against the midpoint; LONG when the "
        "microprice sits above the mid, SHORT when below"),
    entry_rule=("marketable reconstruction of the standard notional "
                "against the observed executable book at arrival"),
    exit_rule=EXIT_RULE_HORIZON,
    pairing_rule=PAIRING_RULE_NONE,
    cashout_rule=CASHOUT_RULE_NONE,
    readiness=xp.AWAITING_FEATURE,
    notes=("UNARMED: the collector reads the venue's BBO feed for "
           "bestBid and bestAsk only; no touch size reaches the row. A "
           "microprice with sizes assumed equal IS the midpoint, which "
           "would duplicate X1 under a second name."),
)

X4 = _declare(
    experiment_id="X4_ORDER_FLOW_IMBALANCE",
    policy_version="BETTOR_EXP_OFI_V1",
    model_version="ofi_v1",
    feature_set=("microstructure.bid", "microstructure.ask",
                 "microstructure.bidSize", "microstructure.askSize"),
    required_features=("bidSize", "askSize"),
    target="mid at +30s versus mid at arrival",
    horizon="30S",
    direction_rule=(
        "Cont order-flow imbalance across two consecutive observed "
        "touches; LONG on positive imbalance, SHORT on negative"),
    entry_rule=("marketable reconstruction of the standard notional "
                "against the observed executable book at arrival"),
    exit_rule=EXIT_RULE_HORIZON,
    pairing_rule=PAIRING_RULE_NONE,
    cashout_rule=CASHOUT_RULE_NONE,
    readiness=xp.AWAITING_FEATURE,
    notes=("UNARMED: OFI is defined on CHANGES in resting size at the "
           "touch. With no sizes there is no OFI at all -- not a noisy "
           "one. A price-change signal wearing an order-flow name is "
           "exactly what §3 forbids."),
)

X5 = _declare(
    experiment_id="X5_EXTERNAL_DISAGREEMENT",
    policy_version="BETTOR_EXP_EXTERNAL_DISAGREEMENT_V1",
    model_version="external_disagreement_v1",
    feature_set=("microstructure.mid", "externalConsensus.probability"),
    required_features=("externalConsensusProbability",),
    target="settlement outcome versus venue mid at arrival",
    horizon="SETTLEMENT",
    direction_rule=(
        "exact-timestamp external consensus against the venue mid; LONG "
        "when the external probability is higher, SHORT when lower"),
    entry_rule=("marketable reconstruction of the standard notional "
                "against the observed executable book at arrival"),
    exit_rule=("held to settlement; the target is the settled outcome, "
               "so there is no intermediate exit"),
    pairing_rule=PAIRING_RULE_NONE,
    cashout_rule=CASHOUT_RULE_NONE,
    readiness=xp.AWAITING_FEATURE,
    notes=("UNARMED: no writer populates external_consensus. The "
           "provenance is EXACT_TIMESTAMP_EXTERNAL_CONSENSUS for a "
           "reason -- a consensus read at a different instant than the "
           "book is a latency artefact, not a disagreement."),
)

# ── the successor: entry carried forward, exit completely frozen ─────
#
# Owner directive 2026-09-20, "DO NOT RETROFIT MISSING EXIT SEMANTICS
# INTO X1 V1":
#
#     "Create a new prospective experiment version... Do not overwrite
#     X1 V1. The new declaration must completely specify entry AND exit
#     before its first position exists. Do not change the
#     signal/threshold merely because X1 V1 lost. Carry forward the
#     existing entry rule unchanged unless there is a separate
#     pre-existing reason to change it."
#
# SO THE ENTRY HALF IS COPIED, NOT REWRITTEN. direction_rule,
# entry_rule, target, horizon, feature_set and notional are the same
# expressions X1 V1 was built from -- the same sig.M1 constants, the
# same $1,000, the same spread gate. There is no pre-existing reason to
# change any of them, and "the first cohort lost" is explicitly not
# one. Only the exit is new, because only the exit was incomplete.
#
# ITS OWN FREEZE INSTANT. A LITERAL, for the reason in the module
# docstring: the successor is a different experiment frozen on a
# different day, and it says so rather than inheriting V1's date.
SUCCESSOR_START_UTC = "2026-09-20T04:45:00Z"


def _declare_v2(**kw):
    return xp.declare(latency_assumption=LATENCY_ASSUMPTION,
                      start_timestamp=SUCCESSOR_START_UTC,
                      exit_semantics=xspec.exit_semantics(), **kw)


# The exit rule SENTENCE the successor freezes. It says the same thing
# EXIT_RULE_HORIZON said and then keeps going, because the sentence
# alone was never enough -- the structured contract beside it is what
# makes the lifecycle completable.
EXIT_RULE_HORIZON_V2 = (
    "exit at the frozen horizon by marketable reconstruction against "
    "the first legitimately observed book at or after the target "
    "instant, within the declared maximum observation delay; no "
    "discretionary hold, no averaging down, no re-entry inside the "
    "horizon; the intended exit quantity is all remaining open "
    "quantity, a thin book yields a PARTIAL_EXIT for the depth it "
    "genuinely showed, and the residual remains an exit obligation "
    "attempted against every subsequent observed book until it is "
    "flat -- never invented depth, never an interpolated price, and "
    "never a close merely because the horizon expired")

X1V2 = _declare_v2(
    experiment_id="X1_SHORT_HORIZON_DIRECTION_V2",
    supersedes="X1_SHORT_HORIZON_DIRECTION",
    policy_version="BETTOR_EXP_SHORT_HORIZON_V2",
    model_version="short_horizon_direction_v1",      # THE SAME MODEL
    feature_set=("microstructure.mid", "microstructure.spreadRelative"),
    required_features=("mid",),
    target="mid at +60s versus mid at arrival",
    horizon="60S",
    direction_rule=(
        "signed drift of the mid over the last %d captured samples "
        "(minimum %d); LONG above +%.4f, SHORT below -%.4f, otherwise "
        "FLAT and no trade"
        % (sig.M1_LOOKBACK_SAMPLES, sig.M1_MIN_SAMPLES,
           sig.M1_ENTRY_THRESHOLD, sig.M1_ENTRY_THRESHOLD)),
    entry_rule=(
        "marketable reconstruction of $%d intended notional against the "
        "observed executable book at simulated arrival; refuse the trade "
        "when relative spread exceeds %.2f, when the input is blocked, "
        "or when no executable depth is observed -- never a filled size "
        "the book did not show"
        % (1000, sig.M1_MAX_SPREAD_RELATIVE)),
    exit_rule=EXIT_RULE_HORIZON_V2,
    pairing_rule=PAIRING_RULE_NONE,
    cashout_rule=CASHOUT_RULE_NONE,
    readiness=xp.DECLARED_AWAITING_REVIEW,
    notes=("Successor to X1_SHORT_HORIZON_DIRECTION. The ENTRY half is "
           "carried forward unchanged -- same signal, same threshold, "
           "same notional, same spread gate -- because the V1 result is "
           "not a reason to change it. The EXIT half is completely "
           "frozen for the first time, including the residual "
           "obligation and a maximum observation delay derived from "
           "measured capture cadence rather than from any outcome. "
           "V1's four positions are untouched and keep "
           "EXIT_MECHANISM_UNAVAILABLE_AT_ENTRY."),
)

X1CV2 = _declare_v2(
    experiment_id="X1C_NULL_CONTROL_V2",
    role=xp.CONTROL,
    control_for="X1_SHORT_HORIZON_DIRECTION_V2",
    supersedes="X1C_NULL_CONTROL",
    policy_version="BETTOR_EXP_NULL_CONTROL_V2",
    model_version="null_control_v1",
    feature_set=(),
    required_features=(),
    target="mid at +60s versus mid at arrival",
    horizon="60S",
    direction_rule=(
        "always LONG; reads no feature and ignores every signal, so any "
        "difference against X1 V2 on the same markets at the same "
        "instants is the model's information and not the market's "
        "direction"),
    entry_rule=(
        "identical to X1 V2's entry in every respect except the "
        "direction rule -- same notional, same book, same arrival, same "
        "input gate; a control that traded a different tape would "
        "compare nothing"),
    exit_rule=EXIT_RULE_HORIZON_V2,
    pairing_rule=PAIRING_RULE_NONE,
    cashout_rule=CASHOUT_RULE_NONE,
    readiness=xp.DECLARED_AWAITING_REVIEW,
    notes=("THE CONTROL IS PART OF THE SUCCESSOR, not an optional "
           "extra. §9 of the founding directive -- 'We need to learn: "
           "DID THE MODEL ADD VALUE? not merely: DID THE MARKET GO "
           "UP?' -- is only answerable if the null runs beside the "
           "candidate on the same population at the same instants. A "
           "successor declared without one would silently drop that "
           "property. It carries V2's complete exit contract for the "
           "same reason X1C carried V1's: a control that exits by a "
           "different rule compares two things at once."),
)

# ── V2's review result, recorded WITHOUT touching V2 ─────────────────
#
# Owner review 2026-09-20 §7: "Preserve them exactly. Do not mutate
# them. Do not delete them. Do not reuse their SHAs."
#
# So the verdict lives here, beside the declarations, never inside
# them. Writing NOT_APPROVED into X1V2 would change its hash -- and a
# rejected experiment whose hash moved is no longer the thing that was
# rejected.

V2_REVIEW = {
    "X1_SHORT_HORIZON_DIRECTION_V2": {
        "result": "NOT_APPROVED",
        "sha": "0a1319e68fe02ff9",
        "positions": 0,
        "reviewedAt": "2026-09-20",
        "reasons": [
            "MAX_EXIT_OBSERVATION_DELAY was derived from "
            "bettor_l2_evidence row spacing, which is the collector's "
            "60s background trail and not the capture cadence the "
            "DIRECT execution path reads",
            "the 65,000ms bound was contradicted by contemporaneous "
            "measurement of that same trail (N=1,351 P50 62.127s "
            "P95 66.901s MAX 70.992s), so it could refuse an exit "
            "merely because our own collector took 67-71s",
            "the bound was computed at import from "
            "shadow_markout_observability, a telemetry module whose "
            "values are explicitly replaceable, so a re-measurement "
            "could have changed a frozen experiment's sha without "
            "anyone changing its economic policy",
            "SUCCESSOR_START_UTC 2026-09-20T04:45:00Z precedes the "
            "instant the rules actually froze in production "
            "(~2026-09-20T13:11:40Z) by more than eight hours; a "
            "prospective experiment cannot begin before its own rules "
            "exist",
        ],
    },
    "X1C_NULL_CONTROL_V2": {
        "result": "NOT_APPROVED",
        "sha": "d7643c9f622b9e3d",
        "positions": 0,
        "reviewedAt": "2026-09-20",
        "reasons": ["carries V2's exit contract; rejected with its "
                    "candidate"],
    },
}


# ── V3: the corrected successor ──────────────────────────────────────
#
# §5's defect fixed by construction: this instant is AFTER the review
# that produced V3 and after the deploy that carries it, so the
# experiment cannot claim to have begun before its own rules existed.
# A literal, for the reason in the module docstring.
V3_START_UTC = "2026-09-20T15:00:00Z"


def _declare_v3(**kw):
    return xp.declare(latency_assumption=LATENCY_ASSUMPTION,
                      start_timestamp=V3_START_UTC,
                      exit_semantics=_V3_EXIT_SEMANTICS, **kw)


# The exit contract, assembled from LITERALS in shadow_exit_spec_v3.
# §6: nothing here is read from a telemetry module at import.
_V3_EXIT_SEMANTICS = {
    "exitAnchor": "ARRIVAL",
    "exitAnchorRule": xspec.EXIT_ANCHOR_RULE,
    "exitAction": xspec.EXIT_ACTION,
    "exitTimingRule": xspec3.INITIAL_EXIT_DELAY_BASIS,
    "exitIntendedQtyRule": xspec.EXIT_INTENDED_QTY_RULE,
    "partialExitRule": xspec.PARTIAL_EXIT_RULE,
    "residualRule": xspec.RESIDUAL_RULE,
    "exitRetryRule": xspec3.RESIDUAL_RETRY_DELAY_BASIS,
    "maxExitObservationDelayMs": xspec3.INITIAL_EXIT_MAX_DELAY_MS,
    "maxExitDelayBasis": xspec3.OPERATIONAL_BOUND_BASIS,
    "exitHorizonS": 60,
    "exitAtMarketCloseRule": xspec.EXIT_AT_CLOSE_RULE,
    "maxExitDelayRevisionRule": (
        "a change to the collector's configured contract -- sweep "
        "period, instrument count, request timeout, retry policy or "
        "the store's freshness limit -- requires a NEW experiment "
        "version carrying a re-derived bound. The bound is never "
        "widened in place, never widened because of which exits it "
        "refused, and never recomputed from a telemetry module at "
        "import"),
    "exitEvidenceFields": list(xspec.EXIT_EVIDENCE_FIELDS) + [
        "EXIT_ATTEMPT_NO", "EXIT_PREVIOUS_BOOK_TIMESTAMP"],
}

EXIT_RULE_HORIZON_V3 = EXIT_RULE_HORIZON_V2

X1V3 = _declare_v3(
    experiment_id="X1_SHORT_HORIZON_DIRECTION_V3",
    supersedes="X1_SHORT_HORIZON_DIRECTION_V2",
    policy_version="BETTOR_EXP_SHORT_HORIZON_V3",
    model_version="short_horizon_direction_v1",      # THE SAME MODEL
    feature_set=("microstructure.mid", "microstructure.spreadRelative"),
    required_features=("mid",),
    target="mid at +60s versus mid at arrival",
    horizon="60S",
    direction_rule=X1["directionRule"],              # byte-identical
    entry_rule=X1["entryRule"],                      # byte-identical
    exit_rule=EXIT_RULE_HORIZON_V3,
    pairing_rule=PAIRING_RULE_NONE,
    cashout_rule=CASHOUT_RULE_NONE,
    readiness=xp.DECLARED_AWAITING_REVIEW,
    notes=("Corrected successor. The ENTRY half is the SAME STRING as "
           "X1 V1's -- taken from the declaration rather than retyped, "
           "so it cannot drift. The EXIT bound is the execution-"
           "admissibility contract the entry already answers to "
           "(FRESHNESS_LIMIT_S = 5.0s), not a figure derived from the "
           "60s evidence trail V2 wrongly bounded on, and every "
           "provenance value is a literal so future telemetry cannot "
           "move this hash."),
)

X1CV3 = _declare_v3(
    experiment_id="X1C_NULL_CONTROL_V3",
    role=xp.CONTROL,
    control_for="X1_SHORT_HORIZON_DIRECTION_V3",
    supersedes="X1C_NULL_CONTROL_V2",
    policy_version="BETTOR_EXP_NULL_CONTROL_V3",
    model_version="null_control_v1",
    feature_set=(),
    required_features=(),
    target="mid at +60s versus mid at arrival",
    horizon="60S",
    direction_rule=X1C["directionRule"],
    entry_rule=X1C["entryRule"],
    exit_rule=EXIT_RULE_HORIZON_V3,
    pairing_rule=PAIRING_RULE_NONE,
    cashout_rule=CASHOUT_RULE_NONE,
    readiness=xp.DECLARED_AWAITING_REVIEW,
    notes=("The null beside X1 V3, on V3's exit contract. §9 of the "
           "founding directive is only answerable with a control on "
           "the same population at the same instants."),
)

EXPERIMENTS = (X1, X1C, X2, X3, X4, X5, X1V2, X1CV2, X1V3, X1CV3)

BY_ID = {e["experimentId"]: e for e in EXPERIMENTS}

# Which signal function each experiment's direction rule is implemented
# by. Kept OUT of the declaration so that the hash covers the RULE AS
# STATED rather than the name of the callable implementing it.
SIGNAL_FOR = {
    "X1_SHORT_HORIZON_DIRECTION": sig.M1,
    "X1C_NULL_CONTROL": sig.C0,
    "X2_RELATIVE_VALUE": sig.M4,
    "X3_MICROPRICE": sig.M2,
    "X4_ORDER_FLOW_IMBALANCE": sig.M3,
    "X5_EXTERNAL_DISAGREEMENT": sig.M5,
    # THE SUCCESSOR RUNS THE SAME SIGNAL FUNCTIONS. "Do not change the
    # signal/threshold merely because X1 V1 lost" -- so the mapping
    # points at the same callables, not at new ones.
    "X1_SHORT_HORIZON_DIRECTION_V2": sig.M1,
    "X1C_NULL_CONTROL_V2": sig.C0,
    "X1_SHORT_HORIZON_DIRECTION_V3": sig.M1,
    "X1C_NULL_CONTROL_V3": sig.C0,
}


def armed() -> tuple:
    """The experiments that may actually trade right now."""
    return tuple(e for e in EXPERIMENTS if e["readiness"] == xp.ARMED)


def awaiting() -> tuple:
    return tuple(e for e in EXPERIMENTS
                 if e["readiness"] == xp.AWAITING_FEATURE)


def verify_all() -> list:
    """Re-derive every stored hash. A mismatch is an edited rule."""
    return [xp.verify(e) for e in EXPERIMENTS]


def registry_report() -> dict:
    """What COMMAND shows above the leaderboard (§10, §11)."""
    checks = verify_all()
    return {
        "registryVersion": REGISTRY_VERSION,
        "startUtc": START_UTC,
        "declared": len(EXPERIMENTS),
        "armed": [e["experimentId"] for e in armed()],
        "awaitingFeature": [
            {"experimentId": e["experimentId"],
             "requiredFeatures": e["requiredFeatures"],
             "why": e["notes"]}
            for e in awaiting()],
        "hashesVerified": all(c["matches"] for c in checks),
        "mismatched": [c["experimentId"] for c in checks
                       if not c["matches"]],
        "notDecisionGrade": True,
        "realOrderActivity": "NONE",
        "realCapitalAtRisk": 0,
        "disclosure": xp.EXPERIMENTAL_DISCLOSURE,
        "shadowMode": sh.SHADOW_MODE,
    }
