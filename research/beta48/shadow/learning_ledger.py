"""The event-sourced learning ledger, point-in-time feature store, lineage.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING IS TRAINED HERE. NO ORDER IS PLACED.

THE FLYWHEEL
------------
    OBSERVE -> FEATURE -> PREDICT -> DECIDE -> LOG -> OBSERVE OUTCOME
    -> LABEL -> UPDATE DATASET -> TRAIN CHALLENGERS -> VALIDATE
    -> PROMOTION GATE -> SHADOW/CANARY -> CHAMPION -> MONITOR
    -> RETRAIN OR ROLLBACK

Learning may be automatic. PROMOTION MAY NOT BE, merely because training
finished.

WHY NO_TRADE IS LOGGED LIKE A TRADE
-----------------------------------
If only traded states are recorded, the dataset contains exactly the states
the policy already liked. Every later model then learns the policy's existing
opinion and calls it a discovery. NO_TRADE decisions are logged with the same
state, predictions, candidate actions and future outcomes, so the dataset
covers the decision space rather than the policy's footprint in it.

POINT-IN-TIME, OR NOT AT ALL
----------------------------
A feature may train a model only if INFORMATION_AVAILABLE_AT <= the decision
it informs. Reconstructing a historical value from today's file is the classic
silent leak: the number is right today and was unknowable then.
"""

import datetime
import hashlib
import json
import uuid

NOT_IDENTIFIED = "NOT_IDENTIFIED"

NOTHING_IS_TRAINED_HERE = True
NO_ORDER_IS_PLACED = True
APPEND_ONLY = True
AUTO_PRODUCTION_PROMOTION = "DISABLED"

FLYWHEEL = ("OBSERVE", "FEATURE", "PREDICT", "DECIDE", "LOG",
            "OBSERVE_OUTCOME", "LABEL", "UPDATE_DATASET",
            "TRAIN_CHALLENGERS", "VALIDATE", "PROMOTION_GATE",
            "SHADOW_CANARY", "CHAMPION", "MONITOR", "RETRAIN_OR_ROLLBACK")

LEARNING_MAY_BE_AUTOMATIC_PROMOTION_MAY_NOT = (
    "training completing is not evidence that a model deserves production. "
    "The loop runs continuously; the gate does not open automatically")


# --- Section 2. The event types. -------------------------------------------

EVENT_TYPES = (
    "MARKET_STATE_EVENT", "MODEL_PREDICTION_EVENT", "DECISION_EVENT",
    "SHADOW_DECISION_EVENT", "ORDER_INTENT_EVENT", "ORDER_EVENT",
    "FILL_EVENT", "CANCEL_EVENT", "MARKOUT_EVENT", "SETTLEMENT_EVENT",
    "INVENTORY_EVENT", "CAPITAL_EVENT", "LABEL_EVENT",
    "MODEL_TRAINING_EVENT", "MODEL_VALIDATION_EVENT",
    "MODEL_PROMOTION_EVENT", "MODEL_ROLLBACK_EVENT",
)

REQUIRED_ENVELOPE = ("EVENT_UUID", "EVENT_TYPE", "RECORDED_AT_UTC",
                     "SOURCE_TIMESTAMP", "SOURCE", "SCHEMA_VERSION",
                     "CODE_SHA")

RECORDED_AT_IS_NOT_SOURCE_TIMESTAMP = (
    "RECORDED_AT_UTC is when WE wrote the row; SOURCE_TIMESTAMP is when the "
    "world produced it. Collapsing them destroys every latency measurement "
    "and hides a stale feed behind a fresh write")


def _now_stub():
    return NOT_IDENTIFIED


def event(event_type, source_timestamp, source, code_sha, payload=None,
          recorded_at_utc=None, schema_version="1", event_uuid=None):
    """One append-only ledger event. Refuses an undeclared type."""
    if event_type not in EVENT_TYPES:
        return {"STATUS": "UNKNOWN_EVENT_TYPE", "GOT": event_type,
                "DECLARED": EVENT_TYPES}
    row = {
        "EVENT_UUID": event_uuid or str(uuid.uuid4()),
        "EVENT_TYPE": event_type,
        "RECORDED_AT_UTC": recorded_at_utc or NOT_IDENTIFIED,
        "SOURCE_TIMESTAMP": source_timestamp or NOT_IDENTIFIED,
        "SOURCE": source or NOT_IDENTIFIED,
        "SCHEMA_VERSION": schema_version,
        "CODE_SHA": code_sha or NOT_IDENTIFIED,
        "RECORDED_AT_IS_NOT_SOURCE_TIMESTAMP":
            RECORDED_AT_IS_NOT_SOURCE_TIMESTAMP,
    }
    row.update(dict(payload or {}))
    row["MISSING_ENVELOPE_FIELDS"] = [f for f in REQUIRED_ENVELOPE
                                      if row.get(f) == NOT_IDENTIFIED]
    row["ENVELOPE_COMPLETE"] = not row["MISSING_ENVELOPE_FIELDS"]
    return row


# --- Section 7. NO_TRADE memory. -------------------------------------------

NO_TRADE_IS_LOGGED_LIKE_A_TRADE = (
    "recording only traded states leaves a dataset containing exactly the "
    "states the policy already liked. Every later model then learns the "
    "policy's existing opinion and calls it a discovery")

NO_TRADE_REQUIRED_FIELDS = ("STATE", "PREDICTIONS", "CANDIDATE_ACTIONS",
                            "EXPECTED_EV", "REASON_REJECTED")


def no_trade_decision(state, predictions, candidate_actions, expected_ev,
                      reason_rejected, source_timestamp=None, code_sha=None,
                      policy_version=None):
    """A NO_TRADE carries everything a trade would, plus why it was rejected."""
    missing = [f for f, v in (("STATE", state), ("PREDICTIONS", predictions),
                              ("CANDIDATE_ACTIONS", candidate_actions),
                              ("EXPECTED_EV", expected_ev),
                              ("REASON_REJECTED", reason_rejected))
               if v in (None, "", [], {})]
    ev = event("DECISION_EVENT", source_timestamp, "BETTOR_SHADOW_POLICY",
               code_sha, payload={
                   "ACTION": "NO_TRADE",
                   "STATE": state, "PREDICTIONS": predictions,
                   "CANDIDATE_ACTIONS": candidate_actions,
                   "EXPECTED_EV": expected_ev,
                   "REASON_REJECTED": reason_rejected,
                   "POLICY_VERSION_ACTIVE_AT_T": policy_version
                   or NOT_IDENTIFIED,
                   "FUTURE_OUTCOMES_STILL_LABELLED": True,
                   "NO_TRADE_IS_LOGGED_LIKE_A_TRADE":
                       NO_TRADE_IS_LOGGED_LIKE_A_TRADE,
                   "MISSING_NO_TRADE_FIELDS": missing,
               })
    return ev


SELECTION_BIAS_CHECK = (
    "compare the feature distribution of TRADED states against NON_TRADED "
    "states. If they differ sharply, every model trained on traded states "
    "alone is learning the policy, not the market")


def selection_bias(traded_rows, nontraded_rows, feature):
    """How different are the states the policy chose from the ones it skipped?"""
    def _vals(rows):
        out = []
        for r in rows or ():
            v = (r.get("STATE") or {}).get(feature, r.get(feature))
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                continue
        return out
    a, b = _vals(traded_rows), _vals(nontraded_rows)
    if not a or not b:
        return {"FEATURE": feature, "STATUS": "NOT_COMPARABLE",
                "TRADED_N": len(a), "NONTRADED_N": len(b),
                "WHY": "both arms need observations",
                "SELECTION_BIAS_CHECK": SELECTION_BIAS_CHECK}
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    return {"FEATURE": feature, "STATUS": "COMPARED",
            "TRADED_MEAN": round(ma, 10), "NONTRADED_MEAN": round(mb, 10),
            "DIFFERENCE": round(ma - mb, 10),
            "TRADED_N": len(a), "NONTRADED_N": len(b),
            "SELECTION_BIAS_CHECK": SELECTION_BIAS_CHECK}


# --- Section 3. The point-in-time feature store. ---------------------------

FEATURE_FIELDS = ("FEATURE_NAME", "FEATURE_VALUE", "FEATURE_VERSION",
                  "FEATURE_COMPUTED_AT", "INFORMATION_AVAILABLE_AT",
                  "SOURCE_PROVENANCE", "SOURCE_ROW_IDS",
                  "POINT_IN_TIME_VALID")

POINT_IN_TIME_RULE = (
    "a feature may train a model only if INFORMATION_AVAILABLE_AT <= the "
    "DECISION_TIMESTAMP it informs")

NO_CURRENT_FILE_RECONSTRUCTION = (
    "reconstructing a historical value from today's file is the classic "
    "silent leak: the number is correct today and was unknowable then. The "
    "value that ACTUALLY existed at T is the only admissible one")


def _parse(ts):
    if isinstance(ts, datetime.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=datetime.timezone.utc)
    s = str(ts).replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(s)
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def feature(name, value, version, computed_at, information_available_at,
            source_provenance=None, source_row_ids=(),
            decision_timestamp=None):
    """One feature row, with its point-in-time verdict already decided."""
    iat = _parse(information_available_at)
    dts = _parse(decision_timestamp) if decision_timestamp else None
    if iat is None:
        valid, why = "NO", "NO_INFORMATION_AVAILABLE_AT"
    elif dts is not None and iat > dts:
        valid, why = "NO", "INFORMATION_AVAILABLE_AFTER_THE_DECISION"
    else:
        valid, why = "YES", None
    row = {
        "FEATURE_NAME": name,
        "FEATURE_VALUE": value,
        "FEATURE_VERSION": version,
        "FEATURE_COMPUTED_AT": computed_at or NOT_IDENTIFIED,
        "INFORMATION_AVAILABLE_AT": (iat.isoformat() if iat
                                     else NOT_IDENTIFIED),
        "SOURCE_PROVENANCE": source_provenance or NOT_IDENTIFIED,
        "SOURCE_ROW_IDS": tuple(source_row_ids or ()),
        "POINT_IN_TIME_VALID": valid,
        "POINT_IN_TIME_RULE": POINT_IN_TIME_RULE,
    }
    if why:
        row["WHY_NOT_VALID"] = why
    row["SOURCE_HASH"] = hashlib.sha256(
        json.dumps(sorted(str(x) for x in (source_row_ids or ())),
                   default=str).encode()).hexdigest()[:32]
    return row


def trainable(rows):
    """Split a feature set into what may train and what may not."""
    ok = [r for r in rows or () if r.get("POINT_IN_TIME_VALID") == "YES"]
    bad = [r for r in rows or () if r.get("POINT_IN_TIME_VALID") != "YES"]
    return {
        "TRAINABLE": ok, "REFUSED": bad,
        "TRAINABLE_N": len(ok), "REFUSED_N": len(bad),
        "MAY_TRAIN": bool(ok) and not bad,
        "POINT_IN_TIME_RULE": POINT_IN_TIME_RULE,
        "NO_CURRENT_FILE_RECONSTRUCTION": NO_CURRENT_FILE_RECONSTRUCTION,
        "WHY_NOT": (None if not bad else
                    "%d feature row(s) are not point-in-time valid. A single "
                    "leaked feature contaminates the whole fit" % len(bad)),
    }


# --- Section 4. Feature lineage. -------------------------------------------

CORRECTION_CREATES_A_VERSION = (
    "when an upstream source is corrected, historical training data is NOT "
    "rewritten. A new feature VERSION is created. Every trained model stays "
    "reproducible against the versions it actually saw -- otherwise last "
    "month's validated model silently becomes a model nobody validated")


class Lineage:
    """A dependency DAG over feature versions."""

    def __init__(self):
        self.deps = {}          # (name, version) -> [(name, version), ...]

    def declare(self, name, version, depends_on=()):
        self.deps[(name, version)] = [tuple(d) for d in depends_on or ()]
        return self

    def upstream(self, name, version, _seen=None):
        seen = _seen if _seen is not None else set()
        for d in self.deps.get((name, version), []):
            if d in seen:
                continue
            seen.add(d)
            self.upstream(d[0], d[1], seen)
        return sorted(seen)

    def downstream_of(self, name, version):
        """What must be re-versioned if this source is corrected?"""
        hit = []
        for node in self.deps:
            if (name, version) in self.upstream(node[0], node[1]) or \
                    (name, version) in self.deps[node]:
                hit.append(node)
        return sorted(hit)

    def correct(self, name, old_version, new_version):
        """A correction NEVER rewrites. It creates a new version and names
        every derived feature that must also be re-versioned."""
        affected = self.downstream_of(name, old_version)
        return {
            "CORRECTED": [name, old_version, "->", new_version],
            "HISTORICAL_DATA_REWRITTEN": False,
            "NEW_VERSION_CREATED": True,
            "DERIVED_FEATURES_NEEDING_NEW_VERSIONS": affected,
            "CORRECTION_CREATES_A_VERSION": CORRECTION_CREATES_A_VERSION,
        }


# --- Section 30. Policy feedback guard. ------------------------------------

POLICY_FEEDBACK_GUARD = (
    "a production policy changes the data it later trains on. Every "
    "observation records POLICY_VERSION_ACTIVE_AT_T, and every retraining "
    "report breaks its population down by policy version, so a self-induced "
    "selection effect is visible instead of being learned")


def data_by_policy_version(rows, key="POLICY_VERSION_ACTIVE_AT_T"):
    counts = {}
    for r in rows or ():
        counts[r.get(key, NOT_IDENTIFIED)] = \
            counts.get(r.get(key, NOT_IDENTIFIED), 0) + 1
    return {"DATA_BY_POLICY_VERSION": counts,
            "POLICY_VERSIONS": len(counts),
            "POLICY_FEEDBACK_GUARD": POLICY_FEEDBACK_GUARD}


# --- Section 6. Label maturity. --------------------------------------------

LABEL_STATUSES = ("PENDING", "MATURE", "MISSING", "INVALIDATED")

PENDING_IS_NOT_ZERO = (
    "a PENDING label is not a negative outcome and not a zero. A settlement "
    "label pending for two days means the answer has not arrived, and "
    "training on it as though it were zero invents the answer")

MATURITY_CLASSES = {"FAST": ("PRICE_MOVE_5S", "PRICE_MOVE_30S"),
                    "MEDIUM": ("PRICE_MOVE_60S", "PRICE_MOVE_300S",
                               "MARKET_STATE_TOXICITY"),
                    "SLOW": ("SETTLEMENT_OUTCOME",)}


def label_maturity(label_name, observed_future_seconds, required_seconds,
                   invalidated=False):
    if invalidated:
        return {"LABEL": label_name, "LABEL_STATUS": "INVALIDATED",
                "PENDING_IS_NOT_ZERO": PENDING_IS_NOT_ZERO}
    if observed_future_seconds is None:
        return {"LABEL": label_name, "LABEL_STATUS": "MISSING"}
    if observed_future_seconds >= required_seconds:
        return {"LABEL": label_name, "LABEL_STATUS": "MATURE"}
    return {"LABEL": label_name, "LABEL_STATUS": "PENDING",
            "OBSERVED_FUTURE_S": observed_future_seconds,
            "REQUIRED_S": required_seconds,
            "PENDING_IS_NOT_ZERO": PENDING_IS_NOT_ZERO}


def consumable(rows, maturity_class, declared_class):
    """A training job declares which maturity class it consumes. Fails closed."""
    if declared_class != maturity_class:
        return {"MAY_CONSUME": False,
                "WHY": "the job declared %s but these labels are %s"
                       % (declared_class, maturity_class)}
    mature = [r for r in rows or () if r.get("LABEL_STATUS") == "MATURE"]
    pending = [r for r in rows or () if r.get("LABEL_STATUS") == "PENDING"]
    return {"MAY_CONSUME": True, "MATURE_N": len(mature),
            "PENDING_EXCLUDED_N": len(pending),
            "PENDING_IS_NOT_ZERO": PENDING_IS_NOT_ZERO}


def describe():
    return {
        "FLYWHEEL": FLYWHEEL,
        "LEARNING_MAY_BE_AUTOMATIC_PROMOTION_MAY_NOT":
            LEARNING_MAY_BE_AUTOMATIC_PROMOTION_MAY_NOT,
        "AUTO_PRODUCTION_PROMOTION": AUTO_PRODUCTION_PROMOTION,
        "EVENT_TYPES": EVENT_TYPES,
        "REQUIRED_ENVELOPE": REQUIRED_ENVELOPE,
        "RECORDED_AT_IS_NOT_SOURCE_TIMESTAMP":
            RECORDED_AT_IS_NOT_SOURCE_TIMESTAMP,
        "NO_TRADE_IS_LOGGED_LIKE_A_TRADE": NO_TRADE_IS_LOGGED_LIKE_A_TRADE,
        "SELECTION_BIAS_CHECK": SELECTION_BIAS_CHECK,
        "FEATURE_FIELDS": FEATURE_FIELDS,
        "POINT_IN_TIME_RULE": POINT_IN_TIME_RULE,
        "NO_CURRENT_FILE_RECONSTRUCTION": NO_CURRENT_FILE_RECONSTRUCTION,
        "CORRECTION_CREATES_A_VERSION": CORRECTION_CREATES_A_VERSION,
        "POLICY_FEEDBACK_GUARD": POLICY_FEEDBACK_GUARD,
        "LABEL_STATUSES": LABEL_STATUSES,
        "PENDING_IS_NOT_ZERO": PENDING_IS_NOT_ZERO,
        "MATURITY_CLASSES": {k: v for k, v in MATURITY_CLASSES.items()},
        "APPEND_ONLY": APPEND_ONLY,
        "NOTHING_IS_TRAINED_HERE": NOTHING_IS_TRAINED_HERE,
        "NO_ORDER_IS_PLACED": NO_ORDER_IS_PLACED,
    }
