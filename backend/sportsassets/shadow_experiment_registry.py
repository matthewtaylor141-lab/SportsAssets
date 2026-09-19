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

EXPERIMENTS = (X1, X1C, X2, X3, X4, X5)

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
