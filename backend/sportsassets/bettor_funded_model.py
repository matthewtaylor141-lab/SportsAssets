"""THE LEARNING CONNECTION: a model that is fitted, evaluated, promoted, used.

    "A ledger or bound check alone is not self-improvement."

That was the criticism and it was right. `bettor_funded_learning` records what was
decided and what resulted; nothing closed the loop back to a DECISION. This module
is that loop, and it has five parts, none of which can be skipped:

  1 PROSPECTIVE FEATURES AND A MODEL VERSION on every decision, written to the
    immutable decision row BEFORE the outcome exists.
  2 OUTCOME LABELS derived from the book, not from a snapshot or a model.
  3 EVALUATION on decisions the model COULD NOT HAVE SEEN -- checked against the
    registry's own `fit_through`, not against the evaluator's word for it.
  4 CONTROLLED PROMOTION AND ROLLBACK through a registry state machine, with one
    APPROVED version per key enforced by the database.
  5 AND THE DEMONSTRATION THAT IT MATTERS: an approved model changes a later
    decision. A registry nobody reads is a second ledger.

──────────────────────────────────────────────────────────────────────
WHAT THIS DOES NOT BUILD, DELIBERATELY.

NOT A SECOND KERNEL. `sportsassets.learn.kernel` already holds Ridge, Isotonic,
Stumps, Hazard and BaseRate, pure-Python and versioned; `sportsassets.learn.metrics`
already holds log loss, Brier, AUC, calibration, skill against a declared baseline
and a clustered jackknife. This module fits and scores THROUGH them. Writing a
third arithmetic would be the duplication that produced a fabricated hedge once
already in this codebase.

NOT A NEW PROBABILITY OVER THE WHOLE FIXTURE. The KEY_MIDDLE model answers ONE
question -- `p(the structure's both-win region occurs)` -- and the mass outside that
region has to come from somewhere the caller names. `region_probabilities` REFUSES
without an outside split rather than spreading the remainder uniformly, because a
uniform assumption nobody stated is an assertion about the fixture.

WHAT CLOSES THAT, AND IT IS STILL NOT A FIXTURE-WIDE MODEL. Every action's value
depends on a region only through its (primary, hedge) payout pair, so the measure a
decision needs is over PAYOUT CLASSES (`bettor_payout_states`), and it factors as
P(primary outcome) x P(hedge class | primary outcome) with a measured void mass.
P(primary outcome) is the external probability HOLD is already valued on;
P(hedge class | outcome) is 1 wherever the table admits one hedge payout, and where
it admits exactly two -- {0, 100} -- it is the one binary question the second key
learns: KEY_HEDGE_GIVEN_PRIMARY, P(hedge wins | primary outcome), fitted, bound to
its records, evaluated prospectively and promoted through exactly the registry path
KEY_MIDDLE uses. `predict_distribution` is where the three meet.

──────────────────────────────────────────────────────────────────────
THE TARGET, AND WHY THIS ONE.

`MIDDLE_REGION_OCCURRED`. It is binary, it is observable from the venue's own
settlement, and it is the single number the decision actually turns on: it enters
`bettor_funded_decision._regions_expected_cents`, which multiplies it by the
structure's joint payout. A better estimate of it changes `expected_net_usd` and
therefore changes which action wins. There is no chain of inference between the
thing learned and the thing decided, which is what makes the demonstration in
§5 a demonstration rather than a plausibility argument.

WHAT A GOOD SCORE HERE STILL DOES NOT SAY. Agreement between a prediction and an
outcome is not money. It says nothing about whether acting on the prediction would
have paid, which needs our own fills, our own fees and our own committed capital --
`sportsassets.learn.metrics.report` says so in its own output and this module does
not overwrite it.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .learn import kernel as K
from .learn import metrics as M

VERSION = "FUNDED_MODEL_V1"

#: ── THE QUESTIONS THIS LANE LEARNS ──────────────────────────────────
KEY_MIDDLE = "funded_pair_middle_region"
TARGET = "MIDDLE_REGION_OCCURRED"
#: THE CONDITIONAL THE PAYOUT-STATE DISTRIBUTION NEEDS: did the hedge leg win,
#: GIVEN the primary leg's outcome. Binary, observable from the venue's own
#: settlement of each leg, and asked only where the payoff table admits exactly
#: two hedge payouts {0, 100} for that primary outcome -- everywhere else the
#: table itself answers it (`bettor_payout_states`).
KEY_HEDGE_GIVEN_PRIMARY = "funded_pair_hedge_given_primary"
TARGET_HEDGE_GIVEN_PRIMARY = "HEDGE_WON_GIVEN_PRIMARY_OUTCOME"

#: The estimators this lane will register, by the kernel's own names. A name the
#: kernel does not know is refused rather than defaulted.
ESTIMATORS = ("RIDGE_LOGISTIC", "STUMPS", "BASE_RATE")
_ESTIMATOR_CLASS = {"RIDGE_LOGISTIC": K.Ridge, "STUMPS": K.Stumps,
                    "BASE_RATE": K.BaseRate}

STATE_CANDIDATE = "CANDIDATE"
STATE_APPROVED = "APPROVED"
STATE_RETIRED = "RETIRED"

#: ── REFUSALS ────────────────────────────────────────────────────────
R_SCHEMA_UNAVAILABLE = "THE_MODEL_REGISTRY_IS_NOT_IN_THIS_DATABASE"
R_NO_SUCH_MODEL = "NO_SUCH_MODEL"
R_ESTIMATOR_UNKNOWN = "THAT_IS_NOT_AN_ESTIMATOR_THIS_LANE_REGISTERS"
R_NO_APPROVED_MODEL = "NO_APPROVED_MODEL_FOR_THAT_KEY"
R_NOT_A_CANDIDATE = "ONLY_A_CANDIDATE_IS_PROMOTED"
R_NOT_EVALUATED = "THAT_CANDIDATE_HAS_NO_EVALUATION"
R_EVALUATION_NOT_PROSPECTIVE = "THE_EVALUATION_INCLUDES_ROWS_THE_FIT_COULD_SEE"
R_TOO_FEW_LABELS = "TOO_FEW_LABELLED_DECISIONS_TO_EVALUATE"
R_NO_SKILL = "THE_CANDIDATE_DID_NOT_BEAT_THE_INCUMBENT_BY_THE_DECLARED_MARGIN"
R_NO_APPROVER = "A_PROMOTION_NAMES_WHO_APPROVED_IT"
R_NOTHING_TO_ROLL_BACK_TO = "NO_EARLIER_APPROVED_VERSION_TO_ROLL_BACK_TO"
R_NO_OUTSIDE_SPLIT = "NO_PROBABILITY_WAS_STATED_FOR_THE_REGIONS_OUTSIDE_THE_MIDDLE"
R_SPLIT_DOES_NOT_SUM = "THE_OUTSIDE_SPLIT_DOES_NOT_SUM_TO_ONE"
R_NO_LABEL = "THE_FIXTURES_RESOLUTION_IS_NOT_RECORDED"
R_TRAINING_WINDOW_NOT_STATED = "THE_FIT_DOES_NOT_STATE_WHEN_ITS_ROWS_WERE_DECIDED"
R_FIT_WINDOW_UNDERSTATED = "THE_DECLARED_FIT_WINDOW_ENDS_BEFORE_ROWS_IT_WAS_FIT_ON"
R_EVALUATION_NOT_EVENT_LEVEL = "THE_EVALUATION_DOES_NOT_COUNT_DISTINCT_FIXTURES"
R_TRAINING_NOT_BOUND_TO_RECORDS = "THE_MODEL_WAS_NOT_FIT_ON_RECORDED_DECISIONS"
R_TRAINING_RECORDS_DO_NOT_REPRODUCE = "THE_TRAINING_RECORDS_DO_NOT_REPRODUCE"
#: THE RECORDS COULD NOT BE READ -- which is not the same as "they changed".
#: `approved` refuses on either (it cannot vouch for what it cannot read), and
#: `withdraw_invalidated` retires only on the second: a transient read failure
#: must not permanently strip a model of an approval its records still support.
R_TRAINING_RECORDS_UNREADABLE = "THE_TRAINING_RECORDS_COULD_NOT_BE_READ"
#: THE PARAMETERS WERE NOT FIT ON THE NAMED RECORDS. A provenance that names
#: real, reproducing records says nothing about the arithmetic unless the
#: arithmetic is what those records produce: `fit()` on other rows, relabelled
#: RECORDS, would otherwise pass (review of 599076c). The kernel is
#: deterministic, so the records are refit and the predictions compared.
R_PARAMS_NOT_FROM_THE_RECORDS = "THE_PARAMETERS_ARE_NOT_WHAT_THE_RECORDS_FIT"
R_OUTCOME_AFTER_FIT_WINDOW = \
    "A_TRAINING_OUTCOME_BECAME_KNOWN_AFTER_THE_FIT_WINDOW"
R_MODEL_ID_REUSED = "THAT_MODEL_ID_ALREADY_NAMES_A_DIFFERENT_FIT"
R_INCUMBENT_CANNOT_BE_SCORED = "THE_INCUMBENT_CANNOT_BE_SCORED_ON_THE_COHORT"
R_PROVENANCE_SCHEMA = "THE_REGISTRY_CANNOT_STORE_TRAINING_PROVENANCE"
#: THE APPROVED MODEL'S OWN TRAINING SET NO LONGER REPRODUCES -- a settlement
#: was corrected, a decision's vector changed, or a named decision is gone. The
#: approval was a judgement about a model fit on THOSE records; with them gone
#: it vouches for nothing, so the model stops pricing acquisitions at once and
#: the next learning pass withdraws it (`withdraw_invalidated`).
R_APPROVED_MODEL_EVIDENCE_INVALIDATED = \
    "THE_APPROVED_MODELS_TRAINING_RECORDS_NO_LONGER_REPRODUCE"
RETIRED_EVIDENCE_INVALIDATED = "TRAINING_EVIDENCE_INVALIDATED"
#: A FUNDED DECISION ROW DOES NOT CARRY THE STRUCTURE'S PAYOFF TABLE (migration
#: 132's columns and 135's model columns; the `ranked` jsonb carries candidate
#: summaries, not the table). The conditional's label needs the table to know
#: whether the hedge outcome was binary given the primary's, so funded records
#: cannot be labelled for KEY_HEDGE_GIVEN_PRIMARY and are refused, not guessed.
R_FUNDED_NO_PAYOFF_TABLE = "FUNDED_DECISIONS_DO_NOT_STORE_THE_PAYOFF_TABLE"
R_NOT_A_RECORD_SOURCE = "THAT_IS_NOT_A_RECORD_SOURCE"

#: ── WHERE A MODEL'S TRAINING SET CAME FROM ──────────────────────────
#: RECORDS: decisions in the ledger, re-read and verified at registration.
#: DECLARED: rows a caller supplied (a pure unit test); registrable as a
#: candidate, never approvable -- by `promote`, `rollback` and migration 138.
PROVENANCE_RECORDS = "RECORDS"
PROVENANCE_DECLARED = "DECLARED"

#: WHERE A RECORD-BOUND MODEL'S RECORDS COME FROM. Funded decisions label only
#: groups whose both legs were HELD and settled, which the lane cannot produce
#: without an approved model; non-funded pair observations
#: (`bettor_pair_observations`, migration 140) are labelled from the venue's own
#: settlement of both contracts and break that circle. A model names its source
#: in its provenance and is fit, verified, evaluated and compared on records of
#: that source only -- the bar and the approver are the same for both.
SOURCE_FUNDED = "FUNDED_DECISIONS"
SOURCE_OBSERVATIONS = "PAIR_OBSERVATIONS"
SOURCES = (SOURCE_FUNDED, SOURCE_OBSERVATIONS)

#: ── THE WEIGHTING EVERY SCORE AND EVERY FIT USES ────────────────────
#:
#: EVENT_BALANCED: each decision is weighted 1 / (decisions on its fixture), so
#: every FIXTURE carries a total weight of one. The label belongs to the
#: fixture, and the funded lane records a decision on a held position every
#: cycle, so decision-weighted scores let repeated cycles manufacture skill.
#: Reproduced by independent review: 40 fixtures at one decision each scored
#: log loss 3.30946 and were refused; the same 40 with the one successful
#: fixture repeated 1,000 times scored 0.16026 and were promoted, with
#: `n_events` 40 both times. Under this weighting the two cohorts score the
#: same, because they ARE the same forty observations.
WEIGHTING_EVENT_BALANCED = "EVENT_BALANCED"

#: ── THE SCHEDULED CANDIDATE GENERATOR'S DECLARED RULES ──────────────
#: A candidate is fit only on at least this many resolved fixtures...
MIN_TRAIN_EVENTS = 40
#: ...and only when the training set has grown by at least this many fixtures
#: since the last registered fit, so a pass that sees nothing new fits nothing.
CANDIDATE_REFIT_MIN_NEW_EVENTS = 10
#: The one estimator the schedule fits. Others remain available to an operator.
SCHEDULED_ESTIMATOR = "RIDGE_LOGISTIC"

#: ── THE PROMOTION BAR, DECLARED HERE AND NOT PER CALL ───────────────
#:
#: A threshold chosen at promotion time is a threshold chosen to be cleared. These
#: are the lane's, they are readable before any candidate exists, and `promote`
#: takes no argument that can loosen them.
#:
#: THE 40 IS FIXTURES, NOT DECISIONS. The funded lane records a decision for a
#: held position on every cycle, so forty decisions can be two fixtures seen
#: twenty times -- and repeated observations of one fixture are not independent
#: examples: they share one outcome. `evaluate` counts distinct fixtures
#: (`n_events`) and both it and `promote` apply the bar to that count.
MIN_EVALUATION_ROWS = 40
MIN_EVALUATION_EVENTS = MIN_EVALUATION_ROWS
MIN_SKILL_MARGIN = 0.01          # in log loss, against the incumbent
PROMOTION_METRIC = "log_loss"


def describe() -> dict:
    return {
        "version": VERSION,
        "target": TARGET,
        "model_key": KEY_MIDDLE,
        "fits_and_scores_through": ["sportsassets.learn.kernel",
                                    "sportsassets.learn.metrics"],
        "does_not_reimplement": ("no second kernel and no second metric. A third "
                                 "arithmetic for the same quantity is how two "
                                 "answers to one question get shipped"),
        "promotion_bar": {"min_rows": MIN_EVALUATION_ROWS,
                          "metric": PROMOTION_METRIC,
                          "min_margin_vs_incumbent": MIN_SKILL_MARGIN,
                          "declared_here_not_per_call": (
                              "a threshold chosen at promotion time is a "
                              "threshold chosen to be cleared")},
        "prospective_rule": ("an evaluation counts only decisions decided "
                            "strictly after the registry's own fit_through. A "
                            "model scored on rows it was fitted to is measuring "
                            "its memory"),
        "what_a_good_score_does_not_say": (
            "that acting on the prediction would have made money. That needs "
            "our own fills, our own fees and our own committed capital"),
    }


# ═════════════════════════════════════════════════════════════════════
# 1 · THE FEATURES, AS THEY STAND AT DECISION TIME
# ═════════════════════════════════════════════════════════════════════

#: EVERY ONE OF THESE IS KNOWN BEFORE THE FIXTURE RESOLVES, which is the only
#: property that makes a feature usable prospectively. They are read off the
#: classified structure and its two legs -- no score, no in-play state, nothing
#: that postdates the decision.
FEATURES = ("middle_width_points", "cost_cents", "min_payout_cents",
            "max_payout_cents", "primary_cost_cents", "hedge_cost_cents",
            "overtime_included", "both_win_regions")


def features_of(structure, *, primary_cost_cents, hedge_cost_cents,
                overtime_included) -> dict:
    """THE VECTOR, FROM THE CLASSIFIER'S OWN OUTPUT.

    `middle_width_points` is how many of the fixture's outcome regions pay BOTH
    legs -- the width of the window, which is the structure's whole economics. A
    one-point middle and a four-point middle are different bets and a model given
    only prices could not tell them apart.
    """
    d = structure if isinstance(structure, dict) else structure.to_dict()
    both = list(d.get("both_win_regions") or ())
    return {
        "middle_width_points": float(len(both)),
        "cost_cents": float(d.get("cost_cents") or 0),
        "min_payout_cents": float(d.get("min_payout_cents") or 0),
        "max_payout_cents": float(d.get("max_payout_cents") or 0),
        "primary_cost_cents": float(primary_cost_cents),
        "hedge_cost_cents": float(hedge_cost_cents),
        "overtime_included": 1.0 if overtime_included else 0.0,
        "both_win_regions": float(len(both)),
    }


#: THE CONDITIONAL'S OWN FEATURE LIST: the structure's vector plus the primary
#: outcome it is conditioned on. Its own list, so its schema sha -- and every
#: feature sha -- is distinct from KEY_MIDDLE's: a vector scored by one model is
#: never mistaken for the other's.
FEATURES_HEDGE_GIVEN_PRIMARY = FEATURES + ("primary_won",)

_FEATURES_BY_KEY = {KEY_MIDDLE: FEATURES,
                    KEY_HEDGE_GIVEN_PRIMARY: FEATURES_HEDGE_GIVEN_PRIMARY}
_TARGET_BY_KEY = {KEY_MIDDLE: TARGET,
                  KEY_HEDGE_GIVEN_PRIMARY: TARGET_HEDGE_GIVEN_PRIMARY}


def features_for(model_key: str | None) -> tuple:
    """The feature list a model of this key is fit and scored on."""
    return _FEATURES_BY_KEY.get(model_key or KEY_MIDDLE, FEATURES)


def target_for(model_key: str | None) -> str:
    return _TARGET_BY_KEY.get(model_key or KEY_MIDDLE, TARGET)


def conditional_features_of(structure, *, primary_cost_cents,
                            hedge_cost_cents, overtime_included,
                            primary_won) -> dict:
    """KEY_HEDGE_GIVEN_PRIMARY's vector: `features_of` plus the conditioning
    primary outcome, 1.0 for a primary WIN and 0.0 for a LOSS. The outcome is a
    CONDITION of the question asked, not a peek at the answer: the model is
    asked P(hedge wins | primary won) and P(hedge wins | primary lost)
    separately, and both answers are recorded."""
    return dict(features_of(structure, primary_cost_cents=primary_cost_cents,
                            hedge_cost_cents=hedge_cost_cents,
                            overtime_included=overtime_included),
                primary_won=1.0 if primary_won else 0.0)


def feature_sha(features: dict) -> str:
    """Identity of the exact vector scored.

    THE SAME FUNCTION `bettor_model_inventory` USES, for the same reason: a later
    re-fit changes the model, not what a recorded prediction was made from, and
    without this the difference is unprovable.
    """
    blob = json.dumps({k: features[k] for k in sorted(features)},
                      sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ═════════════════════════════════════════════════════════════════════
# 2 · FITTING, THROUGH THE EXISTING KERNEL
# ═════════════════════════════════════════════════════════════════════

def event_weights(fixtures) -> list:
    """1 / (rows on the same fixture), so every fixture sums to one."""
    fx = [str(f) for f in fixtures]
    n: dict = {}
    for f in fx:
        n[f] = n.get(f, 0) + 1
    return [1.0 / n[f] for f in fx]


def fit(rows, labels, *, estimator: str = "RIDGE_LOGISTIC", decided_at=None,
        weights=None, features=None, **kw) -> dict:
    """FIT ONE CANDIDATE. `rows` are feature dicts; `labels` are 0/1.

    Returns the model's own `to_dict()` plus the training base rate, which the
    evaluator NEEDS and must not compute from the evaluation set:
    `metrics.report` refuses a baseline taken from the rows being scored, because
    a model scored against a rate it could not have known is flattered on exactly
    the split where drift shows up.
    """
    out: dict[str, Any] = {"version": VERSION, "estimator": estimator}
    if estimator not in _ESTIMATOR_CLASS:
        return dict(out, ok=False, refusal=R_ESTIMATOR_UNKNOWN,
                    recognised=list(ESTIMATORS))
    ys = [float(y) for y in labels]
    if not ys:
        return dict(out, ok=False, refusal=R_TOO_FEW_LABELS, train_rows=0)
    # THE KEY'S OWN LIST, defaulting to KEY_MIDDLE's so every existing caller
    # fits exactly what it fit before.
    feats = list(features or FEATURES)
    # `BaseRate` TAKES NO FEATURE LIST, and that is not an inconsistency to
    # paper over: it predicts the training mean for every input, so there is no
    # vector for it to have. Its `features` column is still the lane's full list
    # -- what the model MAY be scored on -- so the registry rows are comparable.
    mdl = (_ESTIMATOR_CLASS[estimator]()
           if estimator == "BASE_RATE"
           else _ESTIMATOR_CLASS[estimator](list(feats), **kw))
    if weights is not None:
        weights = [float(w) for w in weights]
        if len(weights) != len(ys) or any(w < 0 for w in weights) \
                or sum(weights) <= 0:
            return dict(out, ok=False, refusal="THE_WEIGHTS_ARE_NOT_USABLE")
    mdl.fit(list(rows), ys, weights=weights)
    # ── WHEN THE ROWS IT WAS FIT ON WERE DECIDED ─────────────────────
    #
    # `register` needs this to check the caller's `fit_through`. Without it the
    # declared window was taken on trust, and a model fit on the very decisions
    # it was then scored on was reported PROSPECTIVE: the leak check compares
    # the evaluation rows with `fit_through`, and a `fit_through` declared
    # before the training rows makes that comparison vacuous.
    trained_through = None
    if decided_at is not None:
        ts = [float(t) for t in decided_at]
        if len(ts) != len(ys):
            return dict(out, ok=False, refusal=R_TRAINING_WINDOW_NOT_STATED,
                        why=("%d decision instants for %d labels"
                             % (len(ts), len(ys))))
        trained_through = max(ts)
    w = weights if weights is not None else [1.0] * len(ys)
    return dict(out, ok=True, refusal=None,
                trained_through_epoch_s=trained_through,
                # A FIT FROM ROWS HANDED IN IS DECLARED: nothing ties those
                # rows to the ledger. `fit_from_records` replaces this.
                training_provenance={"kind": PROVENANCE_DECLARED,
                                     "rows": len(ys)},
                params=mdl.to_dict(), kernel=K.VERSION,
                features=list(feats), train_rows=len(ys),
                train_base_rate=round(sum(y * wi for y, wi in zip(ys, w))
                                      / sum(w), 9),
                train_weighting=(WEIGHTING_EVENT_BALANCED
                                 if weights is not None else "PER_ROW"),
                baseline_is_the_training_rate=(
                    "carried with the model because an evaluation scored "
                    "against its own set's rate cannot detect drift"))


def load(params: dict):
    """Rebuild a registered model. Refuses across kernel versions, which is the
    kernel's own rule: a silently different prediction is worse than a refusal."""
    return K.load(params)


# ═════════════════════════════════════════════════════════════════════
# 3 · THE REGISTRY
# ═════════════════════════════════════════════════════════════════════

async def has_schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables "
            " WHERE table_schema='public' AND table_name='bettor_funded_models'"))
    except Exception:                                           # noqa: BLE001
        return False


def _row(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for f in ("params", "evaluation", "training_provenance"):
        if isinstance(d.get(f), str):
            try:
                d[f] = json.loads(d[f])
            except Exception:                                   # noqa: BLE001
                pass
    for f in ("train_base_rate",):
        if d.get(f) is not None:
            d[f] = float(d[f])
    return d


def _epoch(v) -> float | None:
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def _has_provenance_columns(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT count(*) = 3 FROM information_schema.columns "
            " WHERE table_name = 'bettor_funded_models' AND column_name IN "
            " ('training_provenance', 'trained_through', "
            "  'outcomes_available_through')"))
    except Exception:                                           # noqa: BLE001
        return False


async def register(conn, *, model_id: str, model_version: str, fitted: dict,
                   fit_through, model_key: str = KEY_MIDDLE) -> dict:
    """RECORD A CANDIDATE. It has no authority over any decision.

    `fit_through` is the last instant the fit could see, and it is NOT NULLABLE in
    the schema. Everything §4 checks about prospectiveness is checked against this
    column, so a caller cannot promote a model by describing its evaluation as
    held-out.

    THE TRAINING SET IS PART OF WHAT IS REGISTERED (migration 138):

      * RECORDS provenance (from `fit_from_records`) is RE-READ from the ledger
        here: every decision must still exist and be labelled, the set's
        `records_sha` must reproduce, and every decision instant AND every
        outcome-availability instant must lie inside `fit_through`. A fit that
        learned an outcome after the window it declares is refused
        (A_TRAINING_OUTCOME_BECAME_KNOWN_AFTER_THE_FIT_WINDOW).
      * DECLARED provenance (rows a caller handed to `fit`) is recorded as such:
        a candidate that can be scored and never approved.

    IDEMPOTENT BY IDENTITY. Registering the same id with the same fit is a no-op
    that says so; the same id with a DIFFERENT fit is refused, because an id is
    a claim about what the model is.
    """
    out: dict[str, Any] = {"version": VERSION, "model_id": str(model_id)}
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE)
    if not await _has_provenance_columns(conn):
        return dict(out, ok=False, refusal=R_PROVENANCE_SCHEMA,
                    why="migration 138 is not applied here")
    if not fitted.get("ok"):
        return dict(out, ok=False, refusal=fitted.get("refusal"),
                    why="the fit itself did not succeed")
    # ── THE DECLARED WINDOW IS CHECKED AGAINST THE ROWS, NOT BELIEVED ──
    trained = fitted.get("trained_through_epoch_s")
    if trained is None:
        return dict(out, ok=False, refusal=R_TRAINING_WINDOW_NOT_STATED,
                    why=("the fit does not say when the rows it learned from "
                         "were decided, so `fit_through` cannot be checked and "
                         "every prospective claim made against it would be "
                         "the caller's word. Pass `decided_at` to `fit`, or "
                         "fit from the ledger with `fit_from_records`"))
    declared = _epoch(fit_through)
    if declared is None or declared < float(trained):
        return dict(out, ok=False, refusal=R_FIT_WINDOW_UNDERSTATED,
                    declared_fit_through_epoch_s=declared,
                    trained_through_epoch_s=float(trained),
                    why=("the fit learned from a decision made at %r and "
                         "declares it could see nothing after %r. Every "
                         "evaluation row between the two would be scored as "
                         "prospective while the fit had seen it"
                         % (trained, declared)))
    prov = dict(fitted.get("training_provenance")
                or {"kind": PROVENANCE_DECLARED})
    outcomes_through = None
    if prov.get("kind") == PROVENANCE_RECORDS:
        ver = await verify_provenance(
            conn, {"training_provenance": prov, "model_key": model_key,
                   "params": fitted.get("params"),
                   "estimator": fitted.get("estimator")},
            check_params=True)
        if not ver.get("ok"):
            return dict(out, ok=False, refusal=ver["refusal"],
                        why=ver.get("why"),
                        changed_records=ver.get("changed_records"))
        records = ver["records"]
        late = [r["decision_id"] for r in records if r["decided_at"] > declared]
        if late:
            return dict(out, ok=False, refusal=R_FIT_WINDOW_UNDERSTATED,
                        decided_after_the_window=late[:5])
        if any(r["outcome_available_at"] is None for r in records):
            return dict(out, ok=False,
                        refusal=R_TRAINING_RECORDS_DO_NOT_REPRODUCE,
                        why="a training outcome has no availability instant")
        after = [r["decision_id"] for r in records
                 if r["outcome_available_at"] > declared]
        if after:
            return dict(out, ok=False, refusal=R_OUTCOME_AFTER_FIT_WINDOW,
                        outcomes_known_after_the_window=after[:5],
                        why=("the fit declares it could see nothing after %r, "
                             "and %d training outcome(s) only became known "
                             "later -- so the fit could not have been made at "
                             "the instant it declares" % (declared, len(after))))
        outcomes_through = max(r["outcome_available_at"] for r in records)
        prov = dict(prov, verified_at_registration=True)
    import datetime as _dt

    def _ts(e):
        return (None if e is None else
                _dt.datetime.fromtimestamp(float(e), _dt.timezone.utc))

    inserted = await conn.fetchval(
        "INSERT INTO bettor_funded_models "
        "(model_id, model_key, model_version, state, kernel, estimator, "
        " features, params, fit_through, train_rows, train_base_rate, "
        " training_provenance, trained_through, outcomes_available_through) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7::text[],$8::jsonb,$9,$10,$11,"
        " $12::jsonb,$13,$14) "
        "ON CONFLICT (model_id) DO NOTHING RETURNING true",
        str(model_id), str(model_key), str(model_version), STATE_CANDIDATE,
        fitted["kernel"], fitted["estimator"], list(fitted["features"]),
        json.dumps(fitted["params"], default=str), fit_through,
        int(fitted["train_rows"]), fitted.get("train_base_rate"),
        json.dumps(prov, default=str), _ts(trained), _ts(outcomes_through))
    row = _row(await conn.fetchrow(
        "SELECT * FROM bettor_funded_models WHERE model_id=$1",
        str(model_id)))
    if row is None:
        return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE)
    same = (json.dumps(row["params"], sort_keys=True, default=str)
            == json.dumps(json.loads(json.dumps(fitted["params"],
                                                default=str)),
                          sort_keys=True, default=str)
            and (row.get("training_provenance") or {}).get("records_sha")
            == prov.get("records_sha")
            and row["model_version"] == str(model_version))
    if not same:
        return dict(out, ok=False, refusal=R_MODEL_ID_REUSED,
                    why=("%r is already registered with a different fit. An "
                         "id names one fitted object" % model_id))
    return dict(out, ok=True, state=row["state"], model=row,
                inserted=bool(inserted), provenance=prov.get("kind"),
                authority=("NONE. A candidate is fitted and evaluated; the "
                           "decision path reads only the APPROVED row"))


async def approved(conn, *, model_key: str = KEY_MIDDLE,
                   verify: bool = True) -> dict:
    """THE MODEL THE DECISION PATH MUST USE, or a refusal naming its absence.

    NO APPROVED MODEL IS A REAL ANSWER AND IT IS NOT A FALLBACK. The caller then
    has no model-derived probability, and `bettor_funded_decision` already refuses
    an indirect candidate with no region probabilities -- so the lane declines the
    acquisition rather than deciding from an unregistered estimate.

    AND AN APPROVAL IS ONLY AS GOOD AS ITS RECORDS. With `verify` (the default,
    and what every pricing caller gets) the approved model's training set is
    re-read and re-hashed; if it no longer reproduces, this refuses with
    R_APPROVED_MODEL_EVIDENCE_INVALIDATED. That refusal reaches `decide` as "no
    region probabilities", which makes the indirect acquisition not rankable and
    leaves HOLD, exit and reduce ranked -- so servicing continues and only the
    model-dependent acquisition stops. `verify=False` is for display only.
    """
    out: dict[str, Any] = {"version": VERSION, "model_key": model_key}
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE)
    row = _row(await conn.fetchrow(
        "SELECT * FROM bettor_funded_models "
        " WHERE model_key=$1 AND state=$2", model_key, STATE_APPROVED))
    if row is None:
        return dict(out, ok=False, refusal=R_NO_APPROVED_MODEL,
                    why=("no model is approved for this key, so this lane has no "
                         "estimate it is permitted to decide from. That is a "
                         "refusal, not a reason to use an unregistered one"))
    if verify:
        chk = await verify_provenance(conn, row)
        if not chk.get("ok"):
            return dict(out, ok=False,
                        refusal=R_APPROVED_MODEL_EVIDENCE_INVALIDATED,
                        model_id=row["model_id"],
                        model_version=row["model_version"],
                        verification={k: v for k, v in chk.items()
                                      if k not in ("records", "lab")},
                        why=("model %s is approved, and the records it was fit "
                             "on no longer reproduce (%s). It prices nothing "
                             "until a person approves a model whose records do"
                             % (row["model_id"], chk.get("refusal"))))
        out["provenance_verified"] = True
    return dict(out, ok=True, refusal=None, model=row)


async def withdraw_invalidated(conn, *, model_key: str = KEY_MIDDLE) -> dict:
    """RETIRE AN APPROVED MODEL WHOSE TRAINING RECORDS NO LONGER REPRODUCE.

    The durable half of the rule `approved` enforces on every read. Retiring
    only ever REMOVES pricing authority, so a schedule may do it; restoring
    one -- by `promote` or `rollback` -- needs a named person and records
    that reproduce. The reason names what failed, so the registry says why
    the lane lost its model.
    """
    out: dict[str, Any] = {"version": VERSION, "model_key": model_key,
                           "withdrawn": False}
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE)
    row = _row(await conn.fetchrow(
        "SELECT * FROM bettor_funded_models "
        " WHERE model_key=$1 AND state=$2", model_key, STATE_APPROVED))
    if row is None:
        return dict(out, ok=True, approved_model=None)
    chk = await verify_provenance(conn, row)
    out["approved_model"] = row["model_id"]
    if chk.get("ok"):
        return dict(out, ok=True, provenance_verified=True)
    if chk.get("refusal") != R_TRAINING_RECORDS_DO_NOT_REPRODUCE:
        # NOT ESTABLISHED IS NOT INVALIDATED. `approved` already refuses to
        # price on it; retiring would turn a read failure into a verdict.
        return dict(out, ok=True, withdrawn=False,
                    not_withdrawn_because=chk.get("refusal"),
                    verification={k: v for k, v in chk.items()
                                  if k not in ("records", "lab")})
    reason = "%s: %s" % (RETIRED_EVIDENCE_INVALIDATED,
                         (chk.get("why") or chk.get("refusal") or "")[:200])
    status = await conn.execute(
        "UPDATE bettor_funded_models SET state=$2, retired_at=now(), "
        "  retired_reason=$3 WHERE model_id=$1 AND state=$4",
        row["model_id"], STATE_RETIRED, reason, STATE_APPROVED)
    return dict(out, ok=True, withdrawn=str(status).endswith(" 1"),
                reason=reason,
                verification={k: v for k, v in chk.items()
                              if k not in ("records", "lab")})


# ═════════════════════════════════════════════════════════════════════
# 4 · LABELS AND THE PROSPECTIVE EVALUATION
# ═════════════════════════════════════════════════════════════════════

LABEL_SQL = """
    SELECT d.decision_id, d.group_id, d.fixture, d.features, d.feature_sha,
           d.model_version, d.predicted,
           extract(epoch FROM d.decided_at) AS decided_epoch,
           d.decided_at,
           g.outcome_available_epoch,
           g.middle_occurred, g.any_push, g.roles, g.legs, g.leg_outcomes,
           ov.outcome_version
      FROM bettor_funded_decisions d
      JOIN LATERAL (
        SELECT count(*) AS legs,
               count(DISTINCT i.leg_role) AS roles,
               -- ── SETTLED MEANS THE VENUE STATED THE FIXTURE'S PRICE ──
               --
               -- A leg is settled for labelling only when it was closed BY THE
               -- VENUE'S SETTLEMENT and the record carries the settlement
               -- price and the instant we read it. A leg closed by our own
               -- exit, or whose reading was not authoritative, says nothing
               -- about the fixture and the group is excluded.
               bool_and(i.closed_reason = 'SETTLED_BY_THE_VENUE'
                        AND (i.settlement ->> 'payout_price') IS NOT NULL
                        AND (i.settlement ->> 'at') IS NOT NULL) AS settled,
               bool_and(i.order_intent IN ('ORDER_INTENT_BUY_LONG',
                                           'ORDER_INTENT_BUY_SHORT'))
                   AS sides_known,
               -- ── THE LABEL: DID EACH LEG'S OWN SIDE WIN, FROM ITS PRICE ──
               --
               -- THE DEFECT THIS REPLACES. The label was
               -- `bool_and(payout_usd > 0)`. `payout_usd` is the CASH our
               -- residual quantity received, so (a) a PUSH, which refunds at a
               -- price strictly between 0 and 1, read as a win, and (b) after a
               -- partial exit the payout measures what we still held, not what
               -- the fixture did. The fixture's outcome is the venue's
               -- long-side settlement price: a LONG leg won at 1, a SHORT leg
               -- won at 0, and anything else -- a loss or a push -- is not the
               -- both-win region.
               bool_and(CASE i.order_intent
                          WHEN 'ORDER_INTENT_BUY_LONG'
                            THEN (i.settlement ->> 'payout_price')::numeric = 1
                          WHEN 'ORDER_INTENT_BUY_SHORT'
                            THEN (i.settlement ->> 'payout_price')::numeric = 0
                        END) AS middle_occurred,
               bool_or((i.settlement ->> 'payout_price')::numeric > 0
                       AND (i.settlement ->> 'payout_price')::numeric < 1)
                   AS any_push,
               -- ── WHEN THE LABEL BECAME KNOWN ─────────────────────────
               --
               -- The instant each leg's settlement READING was recorded
               -- (`settlement.at`), and the later of the two -- not
               -- `closed_at`, which is when a row was written and says nothing
               -- about which reading closed it.
               max((i.settlement ->> 'at')::float8) AS outcome_available_epoch,
               -- A VOID IS NO OBSERVATION: the outcome space never resolved.
               bool_or(i.closed_reason = 'VOIDED_BY_THE_VENUE'
                       OR i.settlement ->> 'terminal_reading' = 'EXPLICIT_VOID')
                   AS any_leg_void,
               -- A CONTESTED READING IS NO LABEL (migration 141). Once a
               -- re-read of the leg's settlement has disagreed with what was
               -- booked, the booked reading is not the venue's settled word,
               -- so the group leaves the labelled set -- and a model whose
               -- provenance names it stops reproducing, which is what
               -- withdraws its approval.
               --
               -- UNLESS A BOOKED CORRECTION ANSWERS IT (migration 145). A
               -- correction answers the newest established re-read, and
               -- every disagreement at or before that re-read was with the
               -- reading the correction replaced; the corrected reading is
               -- now what `settlement` carries, so the label below is read
               -- from the CORRECTED price, and `settlement.at` is the instant
               -- the correction was booked -- a fit taken before then could
               -- not have learned it. A disagreement AFTER the answered
               -- re-read is a new one and contests again. A correction that
               -- turns a priced leg into a void (or a void into a price)
               -- leaves the group out either way: `any_leg_void`, or a
               -- closure reason that no longer says the venue settled it.
               bool_or(EXISTS (
                   SELECT 1 FROM bettor_funded_settlement_rechecks rc
                    WHERE rc.intent_id = i.intent_id
                      AND rc.verdict = 'DISAGREES'
                      AND NOT EXISTS (
                          SELECT 1
                            FROM bettor_funded_settlement_corrections c
                            JOIN bettor_funded_settlement_rechecks ra
                              ON ra.recheck_id = c.recheck_id
                             AND ra.intent_id = c.intent_id
                           WHERE c.intent_id = rc.intent_id
                             AND (ra.read_at, ra.recheck_id)
                                 >= (rc.read_at, rc.recheck_id))))
                   AS any_leg_contested,
               -- EACH LEG'S READING, which is the label's own version. It is
               -- bound into the training set's hash, so a corrected settlement
               -- after a fit makes the fit's records stop reproducing.
               jsonb_agg(jsonb_build_object(
                   'intent_id', i.intent_id,
                   'leg_role', i.leg_role,
                   'order_intent', i.order_intent,
                   'closed_reason', i.closed_reason,
                   'terminal_reading', i.settlement ->> 'terminal_reading',
                   'payout_price', i.settlement ->> 'payout_price',
                   'read_at', i.settlement ->> 'at')
                   ORDER BY i.leg_role, i.intent_id) AS leg_outcomes
          FROM bettor_funded_intents i
         WHERE i.portfolio_group_id = d.group_id AND i.kind = 'ENTRY'
      ) g ON TRUE
      LEFT JOIN LATERAL (
        -- THE REALISED-OUTCOME VERSION, for traceability only: it versions the
        -- P&L joined to the decision, not the label, so it is recorded beside
        -- the training set and not hashed into it.
        SELECT max(o.version) AS outcome_version
          FROM bettor_funded_decision_outcomes o
         WHERE o.decision_id = d.decision_id
      ) ov ON TRUE
     WHERE d.group_id IS NOT NULL
       AND d.features IS NOT NULL
       -- ── BOTH ROLES, OR THERE IS NO MIDDLE TO HAVE OCCURRED ──────
       AND g.roles = 2
       -- ── EVERY LEG SETTLED BY THE VENUE, WITH A STATED PRICE ─────
       AND g.settled
       AND g.sides_known
       -- ── AND NO LEG WAS VOIDED ───────────────────────────────────
       AND NOT g.any_leg_void
       -- ── OR HAS A SETTLEMENT THE VENUE HAS SINCE CONTRADICTED ────
       AND NOT g.any_leg_contested
"""


async def labelled(conn, *, model_key: str = KEY_MIDDLE, after=None,
                   account_id: str | None = None, through=None,
                   outcomes_through=None, decision_ids=None,
                   source: str = SOURCE_FUNDED) -> dict:
    """EVERY DECISION WHOSE FIXTURE HAS RESOLVED, with its prospective vector
    and the evidence its label came from.

    THE LABEL COMES FROM THE VENUE'S SETTLEMENT PRICE, per leg and side-aware:
    `middle_occurred` is true only when BOTH legs' own sides won. A push is
    not a win; a void, an unsettled leg, or a leg we exited before settlement
    is not a label at all.

    WINDOWS. `after` / `through` bound the DECISION instant;
    `outcomes_through` bounds the instant the LABEL became known. A training
    snapshot at T uses both `through=T` and `outcomes_through=T`: a decision
    made before T whose outcome was learned after T is not something a fit at
    T could have learned from.
    """
    if model_key == KEY_HEDGE_GIVEN_PRIMARY:
        # THE CONDITIONAL'S RECORDS: the same shape and windows, a different
        # label (did the hedge win) on the rows whose table makes that a
        # binary question given the primary's outcome. Only observations
        # store the table it is decided on.
        if source == SOURCE_OBSERVATIONS:
            from . import bettor_pair_observations as PO
            return dict(await PO.labelled_conditional(
                conn, after=after, through=through,
                outcomes_through=outcomes_through, ids=decision_ids),
                model_key=model_key, source=source)
        if source == SOURCE_FUNDED:
            return {"version": VERSION, "ok": False, "source": source,
                    "model_key": model_key,
                    "refusal": R_FUNDED_NO_PAYOFF_TABLE,
                    "why": ("a funded decision row does not carry the "
                            "structure's payoff table, so whether the hedge "
                            "outcome was binary given the primary's cannot be "
                            "read from it; the conditional is learned from "
                            "observations, which store the table")}
        return {"version": VERSION, "ok": False, "source": source,
                "refusal": R_NOT_A_RECORD_SOURCE}
    if source == SOURCE_OBSERVATIONS:
        # NON-FUNDED OBSERVATIONS: the same record shape, the same windows;
        # `account_id` does not apply -- nothing was held by any account.
        from . import bettor_pair_observations as PO
        return dict(await PO.labelled(conn, after=after, through=through,
                                      outcomes_through=outcomes_through,
                                      ids=decision_ids),
                    model_key=model_key, source=source)
    if source != SOURCE_FUNDED:
        return {"version": VERSION, "ok": False, "source": source,
                "refusal": R_NOT_A_RECORD_SOURCE}
    out: dict[str, Any] = {"version": VERSION, "model_key": model_key,
                           "rows": [], "labels": [], "source": source}
    sql, args = LABEL_SQL, []
    if after is not None:
        args.append(after)
        sql += " AND d.decided_at > $%d" % len(args)
    if through is not None:
        args.append(through)
        sql += " AND d.decided_at <= $%d" % len(args)
    if outcomes_through is not None:
        args.append(_epoch(outcomes_through))
        sql += " AND g.outcome_available_epoch <= $%d" % len(args)
    if decision_ids is not None:
        args.append([str(x) for x in decision_ids])
        sql += " AND d.decision_id = ANY($%d::text[])" % len(args)
    if account_id is not None:
        args.append(str(account_id))
        sql += " AND d.account_id = $%d" % len(args)
    sql += " ORDER BY d.decided_at, d.decision_id"
    try:
        got = await conn.fetch(sql, *args)
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, refusal="THE_LABELS_COULD_NOT_BE_READ",
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]))
    keys = ("decision_ids", "groups", "fixtures", "decided_at",
            "feature_shas", "outcome_available_at", "leg_outcomes",
            "pushes", "outcome_versions")
    for k in keys:
        out[k] = []
    for r in got:
        feats = r["features"]
        if isinstance(feats, str):
            feats = json.loads(feats)
        if r["middle_occurred"] is None:
            continue
        legs = r["leg_outcomes"]
        if isinstance(legs, str):
            legs = json.loads(legs)
        out["rows"].append(feats)
        out["labels"].append(1.0 if r["middle_occurred"] else 0.0)
        out["decision_ids"].append(r["decision_id"])
        out["groups"].append(r["group_id"])
        out["fixtures"].append(r["fixture"])
        out["decided_at"].append(float(r["decided_epoch"]))
        out["feature_shas"].append(r["feature_sha"])
        out["outcome_available_at"].append(
            None if r["outcome_available_epoch"] is None
            else float(r["outcome_available_epoch"]))
        out["leg_outcomes"].append(legs)
        out["pushes"].append(bool(r["any_push"]))
        out["outcome_versions"].append(r["outcome_version"])
    out["n_events"] = len({str(f) for f in out["fixtures"]})
    return dict(out, ok=True, refusal=None, n=len(out["labels"]),
                label_basis=("BOTH LEGS' OWN SIDES WON, from each leg's venue "
                             "settlement price (LONG at 1, SHORT at 0). A push "
                             "is not a win; a void or an unsettled or exited "
                             "leg is not a label"),
                only_two_role_groups=(
                    "a group carrying one role has no both-win region, so "
                    "'did both legs pay' is not a question about it. Labelling "
                    "one would train the model on the primary leg's win rate "
                    "while every consumer reads p(both legs pay)"),
                prospective_filter=("decided_at > %r" % (after,)
                                    if after is not None
                                    else "NONE -- every resolved decision"))


#: THE FEATURE SCHEMA a record-bound fit is made on. A decision whose vector
#: lacks one of these cannot be scored by the kernel and is not training data.
FEATURE_SCHEMA_SHA = hashlib.sha256(
    json.dumps(sorted(FEATURES)).encode()).hexdigest()[:16]
FEATURE_SCHEMA_SHA_HEDGE_GIVEN_PRIMARY = hashlib.sha256(
    json.dumps(sorted(FEATURES_HEDGE_GIVEN_PRIMARY)).encode()).hexdigest()[:16]


def feature_schema_sha_for(model_key: str | None) -> str:
    return (FEATURE_SCHEMA_SHA_HEDGE_GIVEN_PRIMARY
            if model_key == KEY_HEDGE_GIVEN_PRIMARY else FEATURE_SCHEMA_SHA)

EVIDENCE_RETROSPECTIVE = "RETROSPECTIVE_OUT_OF_SAMPLE"
EVIDENCE_PROSPECTIVE = "PROSPECTIVE"
NO_INDEPENDENT_COHORT = (
    "TRAINING CONSUMES EVERY AVAILABLE RESOLVED FIXTURE; NO INDEPENDENT "
    "EVALUATION COHORT REMAINS. The only evidence this model can acquire is "
    "PROSPECTIVE: its predictions on decisions made after it was frozen")


def _training_records(lab: dict) -> list:
    """ONE VERIFIABLE RECORD PER TRAINING DECISION: which decision, which
    fixture, which vector under which schema, which label, from which leg
    readings, known when."""
    recs = []
    for i in range(len(lab["labels"])):
        recs.append({
            "decision_id": str(lab["decision_ids"][i]),
            "fixture": str(lab["fixtures"][i]),
            "group_id": str(lab["groups"][i]),
            "feature_sha": str(lab["feature_shas"][i] or ""),
            "feature_schema": hashlib.sha256(json.dumps(
                sorted((lab["rows"][i] or {}).keys())).encode()
            ).hexdigest()[:16],
            "decided_at": round(float(lab["decided_at"][i]), 6),
            "label": float(lab["labels"][i]),
            "push": bool(lab["pushes"][i]),
            "outcome_available_at": (
                None if lab["outcome_available_at"][i] is None
                else round(float(lab["outcome_available_at"][i]), 6)),
            # THE LABEL'S OWN VERSION: each leg's settlement reading.
            "leg_outcomes": lab["leg_outcomes"][i],
        })
    return sorted(recs, key=lambda r: r["decision_id"])


def _records_sha(records: list) -> str:
    """The identity of a training set, over everything a label depends on."""
    return hashlib.sha256(json.dumps(records, sort_keys=True,
                                     default=str).encode()).hexdigest()


def _subset(lab: dict, keep: list) -> dict:
    out = dict(lab)
    for k in ("rows", "labels", "decision_ids", "groups", "fixtures",
              "decided_at", "feature_shas", "outcome_available_at",
              "leg_outcomes", "pushes", "outcome_versions"):
        if lab.get(k) is not None:
            out[k] = [lab[k][i] for i in keep]
    out["n"] = len(keep)
    out["n_events"] = len({str(f) for f in out.get("fixtures") or []})
    return out


async def fit_from_records(conn, *, through, model_key: str = KEY_MIDDLE,
                           account_id: str | None = None,
                           estimator: str = SCHEDULED_ESTIMATOR,
                           windows: dict | None = None,
                           source: str = SOURCE_FUNDED, **kw) -> dict:
    """FIT ON THE LEDGER AS IT STOOD AT `through`, AND BIND WHICH ROWS.

    `through` is the TRAINING CUTOFF and the LABEL CUTOFF at once: every
    labelled decision decided by it WHOSE OUTCOME WAS ALSO KNOWN by it -- a
    snapshot that could actually have been taken at that instant. Rows are
    weighted event-balanced, so a fixture decided on many cycles counts once
    in the fit as it does in every score. A vector missing a model feature is
    excluded and counted.

    The provenance lists every training record -- decision, fixture, vector
    and schema, label, each leg's settlement reading, availability instant --
    and hashes them; `register` and `promote` re-read and re-hash. `windows`
    is the evaluation plan the caller declared BEFORE fitting, stored with it.
    """
    out: dict[str, Any] = {"version": VERSION, "estimator": estimator,
                           "source": source}
    lab = await labelled(conn, model_key=model_key, through=through,
                         outcomes_through=through, account_id=account_id,
                         source=source)
    if not lab.get("ok"):
        return dict(out, ok=False, refusal=lab.get("refusal"),
                    error=lab.get("error"))
    # THE KEY'S OWN FEATURE LIST, for the schema filter and for the fit.
    feats = features_for(model_key)
    usable = [i for i in range(lab["n"])
              if set(feats) <= set((lab["rows"][i] or {}).keys())]
    excluded_schema = lab["n"] - len(usable)
    lab = _subset(lab, usable)
    if not lab["n"]:
        return dict(out, ok=False, refusal=R_TOO_FEW_LABELS, n=0, n_events=0,
                    excluded_for_feature_schema=excluded_schema)
    weights = event_weights(lab["fixtures"])
    fitted = fit(lab["rows"], lab["labels"], estimator=estimator,
                 decided_at=lab["decided_at"], weights=weights,
                 features=feats, **kw)
    if not fitted.get("ok"):
        return fitted
    records = _training_records(lab)
    prov = {"kind": PROVENANCE_RECORDS,
            "source": source,
            "records": records,
            "decision_ids": [r["decision_id"] for r in records],
            "fixtures": sorted({r["fixture"] for r in records}),
            "records_sha": _records_sha(records),
            "feature_schema_sha": feature_schema_sha_for(model_key),
            "model_key": model_key, "target": target_for(model_key),
            # EVERY RECORD THE LABEL RULE LEFT OUT, counted by name (the
            # conditional's labeller reports them; KEY_MIDDLE's has none).
            "label_exclusions": lab.get("excluded"),
            "n_rows": lab["n"], "n_events": lab["n_events"],
            "weighting": WEIGHTING_EVENT_BALANCED,
            "excluded_for_feature_schema": excluded_schema,
            "account_id": account_id,
            "training_cutoff_epoch_s": _epoch(through),
            "label_cutoff_epoch_s": _epoch(through),
            "trained_through_epoch_s": max(lab["decided_at"]),
            "outcomes_available_through_epoch_s": max(
                t for t in lab["outcome_available_at"] if t is not None),
            # RECORDED, NOT HASHED: the realised-P&L version joined to each
            # decision. It versions money, not the label.
            "realised_outcome_versions": {
                str(d): v for d, v in zip(lab["decision_ids"],
                                          lab["outcome_versions"])},
            "windows": dict(windows or {"declared_before_fitting": False})}
    return dict(fitted, training_provenance=prov, n_events=lab["n_events"])


def _hyper(estimator: str, params: dict) -> dict:
    """The constructor arguments a stored model was fit with."""
    if estimator == "RIDGE_LOGISTIC":
        return {"l2": params.get("l2", 1.0)}
    if estimator == "STUMPS":
        return {"rounds": params.get("rounds_requested", 0),
                "learning_rate": params.get("learning_rate", 0.1),
                "min_leaf": params.get("min_leaf", 20),
                "max_bins": params.get("max_bins", 32),
                "l2": params.get("l2", 1.0)}
    return {}


def params_reproduce(model: dict, lab: dict) -> dict:
    """REFIT THE NAMED RECORDS AND COMPARE: are these parameters what those
    records produce? Deterministic kernel, same order, same weights -- so the
    predictions on the training rows must agree to rounding."""
    params = model.get("params") or {}
    if isinstance(params, str):
        params = json.loads(params)
    est = str(model.get("estimator") or params.get("kind") or "")
    if est == "BASE_RATE" or params.get("kind") == "BASE_RATE":
        est = "BASE_RATE"
    # REFIT ON THE KEY'S OWN FEATURE LIST -- the list `fit_from_records` fit
    # it on. Refitting a conditional model on KEY_MIDDLE's list would drop the
    # conditioning feature and fail to reproduce a fit that was honest.
    feats = features_for(model.get("model_key"))
    usable = [i for i in range(lab["n"])
              if set(feats) <= set((lab["rows"][i] or {}).keys())]
    if not usable:
        return {"ok": False, "refusal": R_PARAMS_NOT_FROM_THE_RECORDS,
                "why": "no usable training row to refit"}
    rows = [lab["rows"][i] for i in usable]
    refit = fit(rows, [lab["labels"][i] for i in usable], estimator=est,
                weights=event_weights([lab["fixtures"][i] for i in usable]),
                features=feats, **_hyper(est, params))
    if not refit.get("ok", True) and refit.get("refusal"):
        return {"ok": False, "refusal": R_PARAMS_NOT_FROM_THE_RECORDS,
                "why": "the refit refused: %s" % refit.get("refusal")}
    try:
        stored, again = load(params), load(refit["params"])
        worst = max(abs(float(stored.predict(r)) - float(again.predict(r)))
                    for r in rows)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": R_PARAMS_NOT_FROM_THE_RECORDS,
                "why": "the comparison raised: %s" % type(exc).__name__}
    if worst > 1e-9:
        return {"ok": False, "refusal": R_PARAMS_NOT_FROM_THE_RECORDS,
                "max_prediction_difference": worst,
                "why": ("refitting the named records gives predictions that "
                        "differ by up to %.3g, so these parameters were not "
                        "fit on them" % worst)}
    return {"ok": True, "max_prediction_difference": worst}


async def verify_provenance(conn, model: dict, *,
                            check_params: bool = False) -> dict:
    """RE-READ A RECORD-BOUND MODEL'S TRAINING SET AND RE-HASH IT.

    A corrected settlement, a changed vector or a missing decision after the
    fit makes the records stop reproducing, and a model whose training set no
    longer exists as it was fit is not a model anyone can vouch for.
    """
    prov = model.get("training_provenance") or {}
    if isinstance(prov, str):
        prov = json.loads(prov)
    if prov.get("kind") != PROVENANCE_RECORDS:
        return {"ok": False, "refusal": R_TRAINING_NOT_BOUND_TO_RECORDS}
    ids = list(prov.get("decision_ids") or [])
    try:
        lab = await labelled(conn,
                             model_key=model.get("model_key") or KEY_MIDDLE,
                             decision_ids=ids, source=source_of(model))
    except Exception as exc:                                    # noqa: BLE001
        lab = {"ok": False, "error": type(exc).__name__}
    if not lab.get("ok"):
        return {"ok": False, "refusal": R_TRAINING_RECORDS_UNREADABLE,
                "why": "the label read failed (%s)" % (
                    lab.get("refusal") or lab.get("error"))}
    if lab["n"] != len(ids) or not ids:
        return {"ok": False, "refusal": R_TRAINING_RECORDS_DO_NOT_REPRODUCE,
                "why": "%d decision(s) named, %d still labelled"
                       % (len(ids), (lab or {}).get("n", 0))}
    records = _training_records(lab)
    if _records_sha(records) != prov.get("records_sha"):
        changed = [a["decision_id"] for a, b in
                   zip(records, prov.get("records") or []) if a != b]
        return {"ok": False, "refusal": R_TRAINING_RECORDS_DO_NOT_REPRODUCE,
                "changed_records": changed[:5],
                "why": ("the named decisions no longer carry the labels, "
                        "readings or vectors the fit was made from -- a "
                        "corrected settlement is one way that happens")}
    if check_params:
        pr = params_reproduce(model, lab)
        if not pr.get("ok"):
            return dict(pr, lab=lab, records=records)
    return {"ok": True, "records": records, "lab": lab}


def source_of(model: dict) -> str:
    """The record source a model was fit on. A provenance written before
    sources existed was fit on funded decisions, the only source there was."""
    prov = (model or {}).get("training_provenance") or {}
    if isinstance(prov, str):
        prov = json.loads(prov)
    return prov.get("source") or SOURCE_FUNDED


def _holdout(lab: dict, seen: set) -> dict:
    """The labelled rows with every fixture in `seen` removed, whole."""
    keep = [i for i, fx in enumerate(lab.get("fixtures") or [])
            if str(fx) not in seen]
    out = _subset(lab, keep)
    out["dropped_fixtures"] = sorted(
        {str(f) for f in lab.get("fixtures") or []} & seen)
    return out


async def _fixtures_seen_through(conn, boundary,
                                 source: str = SOURCE_FUNDED) -> set:
    """Every fixture with ANY record at or before `boundary`: the fit may
    have seen its outcome, so none of its records is held-out evidence."""
    if source == SOURCE_OBSERVATIONS:
        return {str(r["fixture"]) for r in await conn.fetch(
            "SELECT DISTINCT fixture FROM bettor_pair_observations "
            " WHERE observed_at <= $1", boundary)}
    return {str(r["fixture"]) for r in await conn.fetch(
        "SELECT DISTINCT fixture FROM bettor_funded_decisions "
        " WHERE decided_at <= $1", boundary)}


def _event_log_loss(obj, lab: dict) -> float:
    preds = [float(obj.predict(r)) for r in lab["rows"]]
    return M.log_loss(preds, lab["labels"], event_weights(lab["fixtures"]))


async def evidence_cohorts(conn, *, model_key: str, training_cutoff,
                           frozen_at, account_id: str | None = None,
                           source: str = SOURCE_FUNDED,
                           holdout_sources=None) -> dict:
    """THE TWO KINDS OF EVIDENCE A MODEL CAN HAVE, KEPT APART.

      RETROSPECTIVE_OUT_OF_SAMPLE  decisions made after the training cutoff and
                                   before the model was frozen, on fixtures the
                                   fit could not have seen. Genuinely held out,
                                   but historical: the model was chosen after
                                   they happened.
      PROSPECTIVE                  decisions made AFTER the model was frozen,
                                   whose outcomes were learned after it too.
                                   Only these test the model as it would have
                                   been used.
    """
    lab = await labelled(conn, model_key=model_key, after=training_cutoff,
                         account_id=account_id, source=source)
    if not lab.get("ok"):
        return {"ok": False, "refusal": lab.get("refusal")}
    # HELD OUT FROM EVERY SOURCE A SCORED MODEL WAS FIT ON. The two sources
    # share one fixture namespace, so a fixture an incumbent trained on in
    # the OTHER source is in-sample for it here (review of 599076c).
    seen: set = set()
    for src in (holdout_sources or (source,)):
        seen |= await _fixtures_seen_through(conn, training_cutoff, src)
    lab = _holdout(lab, seen)
    fz = _epoch(frozen_at)
    retro = _subset(lab, [i for i in range(lab["n"])
                          if lab["decided_at"][i] <= fz])
    pros = _subset(lab, [i for i in range(lab["n"])
                         if lab["decided_at"][i] > fz
                         and (lab["outcome_available_at"][i] or 0) > fz])
    return {"ok": True, "dropped_fixtures": lab["dropped_fixtures"],
            EVIDENCE_RETROSPECTIVE: retro, EVIDENCE_PROSPECTIVE: pros}


def _cohort_report(obj, cohort: dict, *, baseline_rate, kind: str,
                   label: str) -> dict:
    base = {"evidence_kind": kind, "n": cohort["n"],
            "n_events": cohort["n_events"],
            "fixtures_sha": hashlib.sha256(json.dumps(sorted(
                {str(f) for f in cohort["fixtures"]})).encode()).hexdigest(),
            "weighting": WEIGHTING_EVENT_BALANCED}
    if not cohort["n"]:
        return dict(base, report=None, why="no decision in this window")
    preds = [float(obj.predict(r)) for r in cohort["rows"]]
    rep = M.report(preds, cohort["labels"],
                   weights=event_weights(cohort["fixtures"]),
                   baseline_rate=baseline_rate, label=label)
    # THE CLUSTERED JACKKNIFE deletes one fixture at a time but scores the
    # remaining ROWS unweighted, so it is the decision-weighted statistic's
    # uncertainty, and named that -- not the event-balanced figure's.
    rep["clustered_by_fixture_decision_weighted"] = M.clustered_jackknife(
        preds, cohort["labels"], cohort["fixtures"],
        M.skill_stat(baseline_rate))
    per_row = M.report(preds, cohort["labels"], baseline_rate=baseline_rate)
    rep["decision_weighted_for_reference_only"] = {
        "log_loss": per_row["log_loss"],
        "baseline_log_loss": per_row["baseline"]["log_loss"]}
    return dict(base, report=rep, log_loss=rep["log_loss"])


async def evaluate(conn, *, model_id: str, account_id: str | None = None,
                   now: float | None = None) -> dict:
    """SCORE A MODEL ON WHAT IT COULD NOT HAVE SEEN, BY KIND OF EVIDENCE, and
    record it.

    The windows are read from the registry, never from the caller: the
    training cutoff is `fit_through` and the freeze is `created_at`, both
    immutable by trigger. Two cohorts are reported separately and never
    pooled -- RETROSPECTIVE_OUT_OF_SAMPLE and PROSPECTIVE (see
    `evidence_cohorts`) -- each scored event-balanced against the TRAINING base
    rate. Only the prospective cohort can support a promotion.

    `ok` is True when the prospective cohort reaches MIN_EVALUATION_EVENTS
    fixtures; otherwise the evaluation is still recorded and the refusal says
    which evidence is missing.
    """
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "model_id": str(model_id),
                           "at": at}
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE)
    mdl = _row(await conn.fetchrow(
        "SELECT * FROM bettor_funded_models WHERE model_id=$1", str(model_id)))
    if mdl is None:
        return dict(out, ok=False, refusal=R_NO_SUCH_MODEL)
    out.update(model_key=mdl["model_key"], fit_through=mdl["fit_through"],
               frozen_at=mdl["created_at"])
    coh = await evidence_cohorts(conn, model_key=mdl["model_key"],
                                 training_cutoff=mdl["fit_through"],
                                 frozen_at=mdl["created_at"],
                                 account_id=account_id,
                                 source=source_of(mdl))
    if not coh.get("ok"):
        return dict(out, ok=False, refusal=coh.get("refusal"))
    obj = load(mdl["params"])
    rate = mdl.get("train_base_rate")
    label = "%s@%s" % (mdl["model_key"], mdl["model_version"])
    retro = _cohort_report(obj, coh[EVIDENCE_RETROSPECTIVE],
                           baseline_rate=rate, kind=EVIDENCE_RETROSPECTIVE,
                           label=label)
    pros = _cohort_report(obj, coh[EVIDENCE_PROSPECTIVE], baseline_rate=rate,
                          kind=EVIDENCE_PROSPECTIVE, label=label)
    windows = (mdl.get("training_provenance") or {}).get("windows") or {}
    if not coh[EVIDENCE_RETROSPECTIVE]["n"] and \
            (windows.get("retrospective_holdout") or {}).get(
                "fixtures_planned") == 0:
        retro["why"] = NO_INDEPENDENT_COHORT
    # THE CONTAMINATION CHECK, re-read rather than asserted: no scored row may
    # precede the training cutoff.
    cut = mdl["fit_through"].timestamp()
    leaked = [d for c in (coh[EVIDENCE_RETROSPECTIVE],
                          coh[EVIDENCE_PROSPECTIVE]) for d in c["decided_at"]
              if d <= cut]
    doc = {"weighting": WEIGHTING_EVENT_BALANCED,
           "evaluated_at": at,
           "record_source": source_of(mdl),
           "training_cutoff_epoch_s": cut,
           "frozen_at_epoch_s": mdl["created_at"].timestamp(),
           "fixtures_held_out_because_the_fit_could_see_them":
               coh["dropped_fixtures"],
           EVIDENCE_RETROSPECTIVE: retro,
           EVIDENCE_PROSPECTIVE: pros,
           "contamination": {"rows_the_fit_could_have_seen": len(leaked),
                             "verdict": "CLEAN" if not leaked
                             else "CONTAMINATED"},
           "n_events": pros["n_events"],
           "promotable_evidence": (not leaked and pros["n_events"]
                                   >= MIN_EVALUATION_EVENTS),
           "what_promotion_reads": (
               "neither of these stored figures: `promote` re-scores the "
               "candidate and the incumbent together on the prospective "
               "cohort neither could have seen")}
    await conn.execute(
        "UPDATE bettor_funded_models SET evaluation=$2::jsonb "
        " WHERE model_id=$1", str(model_id), json.dumps(doc, default=str))
    out.update(evaluation=doc, n_events=pros["n_events"],
               retrospective_n_events=retro["n_events"])
    if leaked:
        return dict(out, ok=False, refusal=R_EVALUATION_NOT_PROSPECTIVE)
    if pros["n_events"] < MIN_EVALUATION_EVENTS:
        return dict(out, ok=False, refusal=R_TOO_FEW_LABELS,
                    required_events=MIN_EVALUATION_EVENTS,
                    why=("%d PROSPECTIVE fixture(s) -- decided after this model "
                         "was frozen -- and %d retrospective out-of-sample. "
                         "Only prospective evidence supports a promotion, and "
                         "repeated decisions on one fixture are one example"
                         % (pros["n_events"], retro["n_events"])))
    return dict(out, ok=True, refusal=None)


async def compare_on_common_cohort(conn, *, candidate: dict,
                                   incumbent: dict | None) -> dict:
    """SCORE CHALLENGER AND INCUMBENT TOGETHER, ON PROSPECTIVE EVIDENCE FOR BOTH.

    THE DEFECT THIS REPLACES. `promote` compared two stored evaluations,
    measured on different cohorts at different times, so a candidate could win
    because its cohort was easier.

    THE COHORT. Decisions made after BOTH models were frozen, whose outcomes
    were learned after that too, on fixtures neither training window touched --
    every row scored by both models under the same event-balanced weighting.
    With no incumbent the comparison is against the candidate's own declared
    baseline (its training base rate) on the same rows.
    """
    freeze = candidate["created_at"]
    cutoff = candidate["fit_through"]
    if incumbent is not None:
        freeze = max(freeze, incumbent["created_at"])
        cutoff = max(cutoff, incumbent["fit_through"])
    out: dict[str, Any] = {"metric": PROMOTION_METRIC,
                           "weighting": WEIGHTING_EVENT_BALANCED,
                           "evidence_kind": EVIDENCE_PROSPECTIVE,
                           "lower_is_better": True,
                           "frozen_after_epoch_s": _epoch(freeze),
                           "training_cutoff_epoch_s": _epoch(cutoff)}
    # THE CANDIDATE'S SOURCE decides the cohort, and BOTH models are scored on
    # it: the features are the same whichever source a model was fit on.
    out["record_source"] = source_of(candidate)
    sources = tuple(sorted({source_of(candidate)} | (
        {source_of(incumbent)} if incumbent is not None else set())))
    out["held_out_from_sources"] = list(sources)
    coh = await evidence_cohorts(conn, model_key=candidate["model_key"],
                                 training_cutoff=cutoff, frozen_at=freeze,
                                 source=source_of(candidate),
                                 holdout_sources=sources)
    if not coh.get("ok"):
        return dict(out, ok=False, refusal=coh.get("refusal"))
    lab = coh[EVIDENCE_PROSPECTIVE]
    out.update(n=lab["n"], n_events=lab["n_events"],
               fixtures_sha=hashlib.sha256(json.dumps(sorted(
                   {str(f) for f in lab["fixtures"]})).encode()).hexdigest())
    if lab["n_events"] < MIN_EVALUATION_EVENTS:
        return dict(out, ok=False, refusal=R_TOO_FEW_LABELS,
                    required_events=MIN_EVALUATION_EVENTS,
                    why=("%d fixture(s) were decided after both models were "
                         "frozen and are held out from both; the bar is %d"
                         % (lab["n_events"], MIN_EVALUATION_EVENTS)))
    try:
        cand_ll = _event_log_loss(load(candidate["params"]), lab)
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, refusal=R_NOT_EVALUATED,
                    error="%s: %s" % (type(exc).__name__, str(exc)[:160]))
    if incumbent is None:
        rate = candidate.get("train_base_rate")
        if rate is None:
            return dict(out, ok=False, refusal=R_NOT_EVALUATED,
                        why="the candidate declares no training base rate")
        against = M.log_loss([float(rate)] * lab["n"], lab["labels"],
                             event_weights(lab["fixtures"]))
        against_what = "ITS_OWN_DECLARED_BASELINE"
    else:
        try:
            against = _event_log_loss(load(incumbent["params"]), lab)
        except Exception as exc:                                # noqa: BLE001
            return dict(out, ok=False, refusal=R_INCUMBENT_CANNOT_BE_SCORED,
                        error="%s: %s" % (type(exc).__name__, str(exc)[:160]))
        against_what = "THE_INCUMBENT"
    return dict(out, ok=True, refusal=None, candidate=round(cand_ll, 9),
                against=round(against, 9), against_what=against_what,
                improvement=round(against - cand_ll, 9),
                required=MIN_SKILL_MARGIN)


async def promote(conn, *, model_id: str, approved_by: str,
                  now: float | None = None) -> dict:
    """APPROVE A CANDIDATE, or refuse and name which condition it failed.

    SIX CONDITIONS, NONE OF THEM PASSABLE BY ARGUMENT:

      * it is a CANDIDATE (a retired model does not return),
      * it was fit on RECORDED decisions, re-verified at registration
        (a DECLARED fit can be scored and never approved),
      * it has a recorded evaluation whose own verdict is PROSPECTIVE,
      * that evaluation is EVENT-BALANCED and saw at least
        `MIN_EVALUATION_EVENTS` distinct fixtures,
      * and, re-scored NOW on the fixtures neither it nor the incumbent could
        have seen, it beats the incumbent's event-balanced `PROMOTION_METRIC`
        by `MIN_SKILL_MARGIN`. With NO incumbent it must beat its own declared
        baseline on that cohort -- a first model is not admitted merely for
        being first. Stored evaluations from different cohorts are never
        compared with each other.
      * a named approver. Nothing scheduled calls this.

    ATOMIC WITH THE RETIREMENT IT CAUSES. The database permits ONE approved
    version per key, so retiring the incumbent and approving the candidate must be
    one transaction: either order, committed separately, leaves the lane with
    either two approved models or none, and `approved()` would then either pick
    by row order or refuse every decision.
    """
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "model_id": str(model_id),
                           "at": at}
    if not str(approved_by or "").strip():
        return dict(out, ok=False, refusal=R_NO_APPROVER,
                    why=("an approval with no approver is a deploy wearing the "
                         "word. The schema refuses it too"))
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE)
    cand = _row(await conn.fetchrow(
        "SELECT * FROM bettor_funded_models WHERE model_id=$1", str(model_id)))
    if cand is None:
        return dict(out, ok=False, refusal=R_NO_SUCH_MODEL)
    out["model_key"] = cand["model_key"]
    if cand["state"] != STATE_CANDIDATE:
        return dict(out, ok=False, refusal=R_NOT_A_CANDIDATE,
                    state=cand["state"])
    # ── A MODEL NOT FIT ON RECORDED DECISIONS IS NEVER APPROVED ──────
    if (cand.get("training_provenance") or {}).get("kind") \
            != PROVENANCE_RECORDS:
        return dict(out, ok=False, refusal=R_TRAINING_NOT_BOUND_TO_RECORDS,
                    why=("this candidate was fit on rows a caller supplied, "
                         "not on ledger records re-verified at registration. "
                         "It can be scored; it cannot decide"))
    ev = cand.get("evaluation") or None
    if not ev:
        return dict(out, ok=False, refusal=R_NOT_EVALUATED,
                    why="run `evaluate` first; there is nothing to promote on")
    if ev.get("weighting") != WEIGHTING_EVENT_BALANCED \
            or EVIDENCE_PROSPECTIVE not in ev:
        return dict(out, ok=False, refusal=R_EVALUATION_NOT_EVENT_LEVEL,
                    why=("this evaluation predates event-balanced scoring by "
                         "kind of evidence. Re-run `evaluate`"))
    if (ev.get("contamination") or {}).get("verdict") != "CLEAN":
        return dict(out, ok=False, refusal=R_EVALUATION_NOT_PROSPECTIVE,
                    contamination=ev.get("contamination"))
    # ── THE TRAINING SET MUST STILL BE WHAT IT WAS FIT ON ───────────
    ver = await verify_provenance(conn, cand, check_params=True)
    if not ver.get("ok"):
        return dict(out, ok=False, refusal=ver["refusal"],
                    why=ver.get("why"),
                    changed_records=ver.get("changed_records"))
    # ── THE BAR, MEASURED NOW ON ONE COHORT FOR BOTH ────────────────
    inc = _row(await conn.fetchrow(
        "SELECT * FROM bettor_funded_models "
        " WHERE model_key=$1 AND state=$2", cand["model_key"], STATE_APPROVED))
    cmp_ = await compare_on_common_cohort(conn, candidate=cand, incumbent=inc)
    out["comparison"] = cmp_
    if not cmp_.get("ok"):
        return dict(out, ok=False, refusal=cmp_.get("refusal"),
                    why=cmp_.get("why"))
    if cmp_["improvement"] < MIN_SKILL_MARGIN:
        return dict(out, ok=False, refusal=R_NO_SKILL,
                    why=("on %d fixtures neither model could have seen, "
                         "event-balanced %s of %.6f against %.6f (%s) is an "
                         "improvement of %.6f, and the declared bar is %.6f"
                         % (cmp_["n_events"], PROMOTION_METRIC,
                            cmp_["candidate"], cmp_["against"],
                            cmp_["against_what"], cmp_["improvement"],
                            MIN_SKILL_MARGIN)))
    async with conn.transaction():
        if inc is not None:
            await conn.execute(
                "UPDATE bettor_funded_models SET state=$2, retired_at=now(), "
                "  retired_reason=$3, superseded_by=$4 WHERE model_id=$1",
                inc["model_id"], STATE_RETIRED,
                "SUPERSEDED_BY_A_PROMOTED_CANDIDATE", str(model_id))
        await conn.execute(
            "UPDATE bettor_funded_models SET state=$2, approved_at=now(), "
            "  approved_by=$3, "
            "  evaluation = coalesce(evaluation, '{}'::jsonb) "
            "     || jsonb_build_object('promotion_comparison', $4::jsonb) "
            " WHERE model_id=$1",
            str(model_id), STATE_APPROVED, str(approved_by),
            json.dumps(cmp_, default=str))
    return dict(out, ok=True, refusal=None, promoted=True,
                retired=(None if inc is None else inc["model_id"]),
                model=_row(await conn.fetchrow(
                    "SELECT * FROM bettor_funded_models WHERE model_id=$1",
                    str(model_id))),
                atomic=("the retirement and the approval are one transaction: "
                        "the database permits one approved version per key, so "
                        "either order committed separately leaves the lane with "
                        "two approved models or none"))


async def rollback(conn, *, to_model_id: str, reason: str) -> dict:
    """PULL AN APPROVAL BACK, to a version that was approved before.

    A ROLLBACK IS A TRANSITION, NOT A DEPLOY, and this is why it exists as a
    function: reverting a model by shipping different code leaves the registry
    claiming the pulled version is still approved, and every prediction after
    that point is attributed to a model that is not deciding.

    THE TARGET MUST HAVE BEEN APPROVED ONCE. Promoting something that never was
    is `promote`'s job and has to clear its bar; a rollback restores a state the
    lane has already stood behind, so it does not re-run that evaluation -- and it
    cannot be used to sidestep it.
    """
    out: dict[str, Any] = {"version": VERSION, "to_model_id": str(to_model_id)}
    if not str(reason or "").strip():
        return dict(out, ok=False, refusal="A_ROLLBACK_STATES_ITS_REASON")
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE)
    target = _row(await conn.fetchrow(
        "SELECT * FROM bettor_funded_models WHERE model_id=$1",
        str(to_model_id)))
    if target is None:
        return dict(out, ok=False, refusal=R_NO_SUCH_MODEL)
    if target["approved_at"] is None:
        return dict(out, ok=False, refusal=R_NOTHING_TO_ROLL_BACK_TO,
                    why=("%r was never approved. Restoring a state the lane "
                         "never stood behind is a promotion, and a promotion "
                         "has to clear its own bar" % to_model_id))
    out["model_key"] = target["model_key"]
    if (target.get("training_provenance") or {}).get("kind") \
            != PROVENANCE_RECORDS:
        # Migration 138 refuses it in the database too; saying so here names
        # the reason instead of surfacing a constraint violation.
        return dict(out, ok=False, refusal=R_TRAINING_NOT_BOUND_TO_RECORDS,
                    why=("%r was not fit on recorded decisions, so it cannot "
                         "be the approved model" % to_model_id))
    # ── AND ITS RECORDS MUST STILL REPRODUCE ─────────────────────────
    #
    # THE DEFECT (review of a8de639). Rollback checked only that the provenance
    # SAID "RECORDS", so a model whose training set had since been invalidated
    # -- by a corrected settlement, say -- was restored and served by
    # `approved`. "Stood behind once" was a judgement about those records; it
    # does not survive them changing.
    chk = await verify_provenance(conn, target, check_params=True)
    if not chk.get("ok"):
        return dict(out, ok=False,
                    refusal=chk.get("refusal")
                    or R_TRAINING_RECORDS_DO_NOT_REPRODUCE,
                    verification={k: v for k, v in chk.items()
                                  if k not in ("records", "lab")},
                    why=("%r was fit on records that no longer reproduce, so "
                         "the approval it once had vouches for nothing"
                         % to_model_id))
    current = _row(await conn.fetchrow(
        "SELECT * FROM bettor_funded_models "
        " WHERE model_key=$1 AND state=$2", target["model_key"], STATE_APPROVED))
    if current is not None and current["model_id"] == str(to_model_id):
        return dict(out, ok=True, refusal=None, rolled_back=False,
                    already=True,
                    why="that version is already the approved one")
    async with conn.transaction():
        if current is not None:
            await conn.execute(
                "UPDATE bettor_funded_models SET state=$2, retired_at=now(), "
                "  retired_reason=$3, superseded_by=$4 WHERE model_id=$1",
                current["model_id"], STATE_RETIRED,
                "ROLLED_BACK: %s" % str(reason)[:200], str(to_model_id))
        # THE RESTORED ROW KEEPS ITS ORIGINAL `approved_at` AND `approved_by`,
        # and its retirement fields are cleared. Rewriting the approval as
        # today's would erase who stood behind it and when.
        await conn.execute(
            "UPDATE bettor_funded_models SET state=$2, retired_at=NULL, "
            "  retired_reason=NULL, superseded_by=NULL WHERE model_id=$1",
            str(to_model_id), STATE_APPROVED)
    return dict(out, ok=True, refusal=None, rolled_back=True,
                pulled=(None if current is None else current["model_id"]),
                reason=str(reason),
                restored=_row(await conn.fetchrow(
                    "SELECT * FROM bettor_funded_models WHERE model_id=$1",
                    str(to_model_id))),
                approval_record_is_the_original=(
                    "approved_at and approved_by are left as they were. "
                    "Rewriting them as today's would erase who stood behind "
                    "this version and when"))


async def plan_windows(conn, *, now: float, model_key: str = KEY_MIDDLE,
                       account_id: str | None = None,
                       source: str = SOURCE_FUNDED) -> dict:
    """DECLARE THE TRAINING AND EVALUATION WINDOWS BEFORE ANYTHING IS FIT.

    From the resolved fixtures whose outcomes are known NOW:
      * if there are at least MIN_TRAIN_EVENTS + MIN_EVALUATION_EVENTS, the
        latest MIN_EVALUATION_EVENTS fixtures (by first decision) are held out
        as a RETROSPECTIVE out-of-sample cohort, and the training cutoff -- for
        decisions AND for labels -- is set just before the first of them;
      * otherwise training takes every resolved fixture, the cutoff is now,
        and the plan says plainly that NO INDEPENDENT EVALUATION COHORT
        REMAINS: the only evidence the model can acquire is prospective.
    Fixtures decided before the cutoff whose outcome was learned after it
    belong to neither window, and are counted.
    """
    now_dt = _dt_of(now)
    res = await labelled(conn, model_key=model_key, through=now_dt,
                         outcomes_through=now_dt, account_id=account_id,
                         source=source)
    if not res.get("ok"):
        return {"ok": False, "refusal": res.get("refusal")}
    first: dict = {}
    for f, t in zip(res["fixtures"], res["decided_at"]):
        first[str(f)] = min(t, first.get(str(f), t))
    n = len(first)
    plan = {"ok": True, "declared_before_fitting": True,
            "record_source": source,
            "planned_at_epoch_s": now,
            "resolved_fixtures_at_planning": n}
    ordered = sorted(first.items(), key=lambda kv: (kv[1], kv[0]))
    if n >= MIN_TRAIN_EVENTS + MIN_EVALUATION_EVENTS:
        hold = ordered[-MIN_EVALUATION_EVENTS:]
        cutoff = hold[0][1] - 1e-6
        train = await labelled(conn, model_key=model_key,
                               through=_dt_of(cutoff),
                               outcomes_through=_dt_of(cutoff),
                               account_id=account_id, source=source)
        if train.get("ok") and train["n_events"] >= MIN_TRAIN_EVENTS:
            straddle = n - train["n_events"] - len(hold)
            return dict(plan, training_cutoff_epoch_s=cutoff,
                        label_cutoff_epoch_s=cutoff,
                        training_fixtures_planned=train["n_events"],
                        retrospective_holdout={
                            "fixtures_planned": len(hold),
                            "from_epoch_s": cutoff, "to_epoch_s": now,
                            "fixtures": [f for f, _ in hold]},
                        excluded_between_windows=max(0, straddle),
                        prospective="decisions made after registration")
    return dict(plan, training_cutoff_epoch_s=now, label_cutoff_epoch_s=now,
                training_fixtures_planned=n,
                retrospective_holdout={"fixtures_planned": 0,
                                       "why": NO_INDEPENDENT_COHORT},
                excluded_between_windows=0,
                prospective="decisions made after registration")


def _dt_of(epoch: float):
    import datetime as _dt
    return _dt.datetime.fromtimestamp(float(epoch), _dt.timezone.utc)


async def generate_candidate(conn, *, now: float | None = None,
                             model_key: str = KEY_MIDDLE,
                             account_id: str | None = None,
                             estimator: str = SCHEDULED_ESTIMATOR,
                             source: str = SOURCE_FUNDED) -> dict:
    """THE SCHEDULE'S ONE WAY TO ADD A CANDIDATE: plan the windows, fit on the
    ledger within them, register it, and nothing more.

    GOVERNED BY DECLARED RULES, never by the caller:
      * windows planned BEFORE fitting (`plan_windows`) and stored with the
        model, including the statement that no independent cohort remains
        when that is the case;
      * at least MIN_TRAIN_EVENTS resolved fixtures inside the training window;
      * at least CANDIDATE_REFIT_MIN_NEW_EVENTS more resolved fixtures than
        the last record-bound fit had at ITS planning, or nothing is fit.

    IDEMPOTENT ACROSS RESTARTS AND REPEATED CYCLES. The model id derives from
    the training set's `records_sha`: a pass that re-runs on the same ledger --
    after a crash mid-pass, or concurrently -- reaches the same id and a
    registration that is a no-op, and a pass that sees nothing new fits
    nothing. A different training set is a different id.

    SERIALISED PER MODEL KEY. Two passes racing (two workers, or a restart
    that overlaps the pass it replaces) take one transaction-scoped advisory
    lock, so the second always reads the first's registration: it reports
    NOT_ENOUGH_NEW_EVENTS naming that model under `last_fit`, rather than an
    outcome that depends on which insert reached the table first.

    IT NEVER PROMOTES.
    """
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "at": at,
                           "model_key": model_key, "generated": False,
                           "promoted": False, "record_source": source}
    if source not in SOURCES:
        return dict(out, ok=False, refusal="THAT_IS_NOT_A_RECORD_SOURCE")
    if not await has_schema(conn) or not await _has_provenance_columns(conn):
        return dict(out, ok=False, refusal=R_PROVENANCE_SCHEMA)
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                           GENERATION_LOCK_PREFIX + model_key + ":" + source)
        return await _generate_locked(conn, at=at, out=out,
                                      model_key=model_key,
                                      account_id=account_id,
                                      estimator=estimator, source=source)


#: Namespaces the per-key advisory lock `generate_candidate` holds.
GENERATION_LOCK_PREFIX = "bettor_funded_model.generate:"


async def _generate_locked(conn, *, at: float, out: dict, model_key: str,
                           account_id: str | None, estimator: str,
                           source: str = SOURCE_FUNDED) -> dict:
    plan = await plan_windows(conn, now=at, model_key=model_key,
                              account_id=account_id, source=source)
    if not plan.get("ok"):
        return dict(out, ok=False, refusal=plan.get("refusal"))
    out["windows"] = {k: v for k, v in plan.items() if k != "ok"}
    out["training_events_available"] = plan["training_fixtures_planned"]
    if plan["training_fixtures_planned"] < MIN_TRAIN_EVENTS:
        return dict(out, ok=True, reason="TOO_FEW_TRAINING_EVENTS",
                    required=MIN_TRAIN_EVENTS)
    last = await conn.fetchrow(
        "SELECT model_id, training_provenance FROM bettor_funded_models "
        " WHERE model_key=$1 AND training_provenance->>'kind' = $2 "
        "   AND coalesce(training_provenance->>'source', $4) = $3 "
        " ORDER BY created_at DESC, model_id DESC LIMIT 1",
        model_key, PROVENANCE_RECORDS, source, SOURCE_FUNDED)
    if last is not None:
        prov = last["training_provenance"]
        prov = json.loads(prov) if isinstance(prov, str) else (prov or {})
        before = int(((prov.get("windows") or {}).get(
            "resolved_fixtures_at_planning")) or prov.get("n_events") or 0)
        grown = plan["resolved_fixtures_at_planning"] - before
        out["last_fit"] = {"model_id": last["model_id"],
                           "resolved_fixtures_at_its_planning": before,
                           "new_since": grown}
        if grown < CANDIDATE_REFIT_MIN_NEW_EVENTS:
            return dict(out, ok=True, reason="NOT_ENOUGH_NEW_EVENTS",
                        required_new=CANDIDATE_REFIT_MIN_NEW_EVENTS)
    cutoff = _dt_of(plan["training_cutoff_epoch_s"])
    fitted = await fit_from_records(
        conn, through=cutoff, model_key=model_key, account_id=account_id,
        estimator=estimator, windows=out["windows"], source=source)
    if not fitted.get("ok"):
        return dict(out, ok=False, refusal=fitted.get("refusal"))
    # THE BAR ON WHAT THE FIT USED, not on the plan's count: rows whose vector
    # lacks a model feature are excluded by the fit, and a plan of 40 fixtures
    # can fit on fewer.
    if int(fitted.get("n_events") or 0) < MIN_TRAIN_EVENTS:
        return dict(out, ok=True, reason="TOO_FEW_TRAINING_EVENTS",
                    required=MIN_TRAIN_EVENTS,
                    fitted_events=fitted.get("n_events"))
    sha = fitted["training_provenance"]["records_sha"]
    tag = estimator.lower() if source == SOURCE_FUNDED \
        else "obs-" + estimator.lower()
    model_id = "fmc:%s:%s:%s" % (model_key, tag, sha[:16])
    version = "sched-%s-%s" % (tag, sha[:12])
    reg = await register(conn, model_id=model_id, model_version=version,
                         fitted=fitted, fit_through=cutoff,
                         model_key=model_key)
    if not reg.get("ok"):
        return dict(out, ok=False, refusal=reg.get("refusal"),
                    why=reg.get("why"), model_id=model_id)
    fresh = bool(reg.get("inserted"))
    return dict(out, ok=True, generated=fresh,
                reason=("REGISTERED" if fresh else "ALREADY_REGISTERED"),
                model_id=model_id, model_version=version,
                n_events=fitted["n_events"], records_sha=sha,
                state=reg["model"]["state"])



# ═════════════════════════════════════════════════════════════════════
# 6 · FROM A PREDICTION TO THE DECISION'S OWN INPUT
# ═════════════════════════════════════════════════════════════════════

#: THE OUTSIDE SPLIT HAS NO ADMISSIBLE SOURCE TODAY, AND ONE OBVIOUS
#: INADMISSIBLE ONE. Recorded here because the shortcut is easy to reach for and
#: would be almost invisible once taken.
#:
#: WHAT IS MISSING. `region_probabilities` needs a share of `1 - p_middle` for
#: every non-middle region. On a margin structure those regions are bands --
#: (-inf,-2), -2, -1, 0, 1, and the void -- so what is needed is a distribution
#: over the fixture's margin, not another single number.
#:
#: THE SOURCE THAT EXISTS AND MUST NOT BE USED. The venue lists spreads at many
#: lines on the same fixture (run 263 saw asc- slugs at 1.5, 2.5 ... 21.5), so
#: differencing the implied probabilities across consecutive lines yields exactly
#: a distribution over those bands. It is real, measured, per-contract data we
#: already read.
#:
#: It is still not admissible HERE. The outside split is not a presentational
#: detail: which non-middle region occurs decides whether a unit pays $0, $1 or
#: $2, so the split enters the expected value of a capital decision directly.
#: Feeding venue prices into it would make the lane's edge a function of the
#: venue's own quotes -- the substitution the standing instruction forbids, and
#: the one that makes a measured edge circular. A venue-implied split would also
#: pass silently: `probabilities` would be populated and no field would say the
#: shape came from the prices the trade is against.
#:
#: WHAT CLOSES IT -- AND IT IS LESS THAN WAS WRITTEN HERE. This said closing it
#: meant predicting the FULL region distribution with per-region labels. No
#: decision needs that: every action's value depends on a region only through
#: its (primary, hedge) payout pair, so the measure a decision needs is the mass
#: per PAYOUT CLASS, and per-leg outcomes -- which the observations already
#: store -- label it. `predict_distribution` prices the classes from the
#: primary probability HOLD is valued on, the table's own structure, the
#: approved KEY_HEDGE_GIVEN_PRIMARY model and a measured void rate. The outside
#: split itself is still unsourced, and `region_probabilities` still refuses it.
OUTSIDE_SPLIT_HAS_NO_ADMISSIBLE_SOURCE = (
    "no source in this repository states how 1 - p_middle distributes over the "
    "non-middle regions. The venue's own spread ladder would give one, and is "
    "refused: the split enters the expected value directly, so a venue-implied "
    "shape would make the edge a function of the prices being traded against. "
    "A decision does not need the full region distribution either -- only the "
    "mass per payout class, which predict_distribution prices from the primary "
    "probability HOLD is valued on, the table's structure, the approved "
    "hedge-given-primary model and a measured void rate, with no split")


def region_probabilities(structure, *, p_middle: float,
                         outside_split: dict | None) -> dict:
    """TURN ONE PREDICTION INTO THE MEASURE `decide` NEEDS.

    The model answers a single question -- does the both-win region occur -- and
    `bettor_funded_decision` needs a probability on EVERY region the classifier
    partitions the fixture into. The remaining mass has to be distributed, and

        THIS FUNCTION WILL NOT DISTRIBUTE IT UNIFORMLY.

    `outside_split` is required: a map from each non-middle region to its share of
    `1 - p_middle`, summing to one. Spreading the remainder evenly would be an
    assertion about the fixture that nobody made, and it would be invisible in the
    output -- the decision would carry a probability with a made-up shape and no
    field saying so.
    """
    d = structure if isinstance(structure, dict) else structure.to_dict()
    table = list(d.get("table") or ())
    both = set(d.get("both_win_regions") or ())
    out: dict[str, Any] = {"version": VERSION, "p_middle": float(p_middle)}
    if not table:
        return dict(out, ok=False, refusal="THE_PAYOFF_TABLE_IS_EMPTY")
    outside = [r["region"] for r in table if r["region"] not in both]
    if not outside:
        return dict(out, ok=True, refusal=None,
                    probabilities={r: 1.0 / len(both) for r in both},
                    why="every region pays both legs, so there is no outside")
    split = {str(k): float(v) for k, v in dict(outside_split or {}).items()}
    if not split:
        return dict(out, ok=False, refusal=R_NO_OUTSIDE_SPLIT,
                    outside_regions=outside,
                    why=("the model prices the middle only. How the remaining "
                         "%.4f is distributed over %d other region(s) is a "
                         "separate statement about the fixture, and spreading "
                         "it uniformly would make that statement silently"
                         % (1.0 - float(p_middle), len(outside))))
    missing = [r for r in outside if r not in split]
    if missing:
        return dict(out, ok=False, refusal=R_NO_OUTSIDE_SPLIT,
                    unpriced_regions=missing,
                    why=("%d outside region(s) carry no share. A region left "
                         "out is treated as impossible, which is an assertion"
                         % len(missing)))
    total = round(sum(split[r] for r in outside), 9)
    if abs(total - 1.0) > 1e-6:
        return dict(out, ok=False, refusal=R_SPLIT_DOES_NOT_SUM, sums_to=total)
    rest = 1.0 - float(p_middle)
    probs = {r: round(float(p_middle) / len(both), 9) for r in both}
    for r in outside:
        probs[r] = round(rest * split[r], 9)
    return dict(out, ok=True, refusal=None, probabilities=probs,
                middle_regions=sorted(both), outside_regions=outside,
                basis=("p_middle from the approved model, split across the "
                       "both-win cells; the remainder from the caller's stated "
                       "outside split"))


async def predict_for(conn, *, structure, primary_cost_cents,
                      hedge_cost_cents, overtime_included,
                      outside_split=None, model_key: str = KEY_MIDDLE) -> dict:
    """THE APPROVED MODEL'S ANSWER, WITH EVERYTHING A DECISION MUST RECORD.

    Returns the region probabilities `decide` consumes AND the four things the
    decision row has to carry so the prediction is falsifiable afterwards: the
    model key, its version, the exact feature vector and that vector's sha.

    WITH NO APPROVED MODEL IT REFUSES. The lane then has no estimate it is
    permitted to decide from, and `bettor_funded_decision` declines the indirect
    candidate for want of region probabilities. Falling back to an unregistered
    number would make the registry decorative.
    """
    out: dict[str, Any] = {"version": VERSION, "model_key": model_key}
    got = await approved(conn, model_key=model_key)
    if not got.get("ok"):
        return dict(out, ok=False, refusal=got["refusal"], why=got.get("why"))
    mdl = got["model"]
    feats = features_of(structure, primary_cost_cents=primary_cost_cents,
                        hedge_cost_cents=hedge_cost_cents,
                        overtime_included=overtime_included)
    obj = load(mdl["params"])
    p = float(obj.predict(feats))
    regions = region_probabilities(structure, p_middle=p,
                                   outside_split=outside_split)
    if not regions.get("ok"):
        return dict(out, ok=False, refusal=regions["refusal"],
                    why=regions.get("why"), p_middle=p,
                    unpriced_regions=regions.get("unpriced_regions"),
                    outside_regions=regions.get("outside_regions"))
    return dict(out, ok=True, refusal=None,
                model_version=mdl["model_version"],
                model_id=mdl["model_id"],
                features=feats, feature_sha=feature_sha(feats),
                predicted={"target": TARGET, "p_middle": p,
                           "estimator": mdl["estimator"],
                           "kernel": mdl["kernel"],
                           "model_version": mdl["model_version"]},
                region_probabilities=regions["probabilities"],
                probability_basis=regions["basis"])


# ═════════════════════════════════════════════════════════════════════
# 7 · THE PAYOUT-STATE DISTRIBUTION: WHAT PRICES THE INDIRECT CANDIDATE
# ═════════════════════════════════════════════════════════════════════

PRIMARY_PROBABILITY_IS = (
    "THE PROBABILITY HOLD, DIRECT_EXIT AND REDUCE ARE VALUED ON -- the held "
    "position's own external probability, passed through unchanged so all four "
    "actions rest on one primary marginal")

#: THE ENTRY LANE'S TWO GATES ON AN EXTERNAL PROBABILITY, APPLIED WHERE IT
#: PRICES NEW CAPITAL HERE TOO. HOLD, DIRECT_EXIT and REDUCE only keep or shed
#: what is held; an acquisition priced on the primary probability commits new
#: money on it, which is exactly what `bettor_entry_execution`'s
#: MODEL_TRUST_DRIFT forbids an unvalidated external valuation to do ("it
#: would state that an unvalidated external valuation may size a position. It
#: may not"). So the distribution refuses unless the source has a CURRENT,
#: PASSING calibration measurement (the worker's `source_calibration` read,
#: current evaluator, enough sample, not stale, within tolerance), and unless
#: the probability lies inside the source's declared support.
R_PRIMARY_SOURCE_NOT_CALIBRATED = (
    "THE_PRIMARY_PROBABILITYS_SOURCE_HAS_NO_CURRENT_PASSING_CALIBRATION")
R_PRIMARY_OUTSIDE_SUPPORT = (
    "THE_PRIMARY_PROBABILITY_IS_OUTSIDE_ITS_SOURCES_DECLARED_SUPPORT")


def primary_gates(*, primary_probability, primary_calibration) -> dict:
    """MODEL_TRUST_DRIFT and OUT_OF_DISTRIBUTION for the primary probability,
    by the entry lane's own rules and constants. Pure."""
    from . import bettor_entry_execution as EX

    cal = dict(primary_calibration or {})
    trusted = cal.get("measured") is True and cal.get("within_tolerance") is True
    try:
        p = (None if primary_probability is None
             or isinstance(primary_probability, bool)
             else float(primary_probability))
    except (TypeError, ValueError):
        p = None
    inside = p is not None and EX.SUPPORT_MIN <= p <= EX.SUPPORT_MAX
    return {
        "MODEL_TRUST_DRIFT": {
            "clear": trusted,
            "why": (cal.get("why") if trusted else
                    "%s: %s" % (EX.R_NO_CALIBRATION if not cal.get("measured")
                                else "CALIBRATION_OUTSIDE_TOLERANCE",
                                cal.get("why") or cal.get("error")
                                or "no calibration measurement was supplied")),
            "source_version": cal.get("source_version")},
        "OUT_OF_DISTRIBUTION": {
            "clear": inside,
            "support": [EX.SUPPORT_MIN, EX.SUPPORT_MAX],
            "probability": p}}


async def predict_distribution(conn, *, structure, primary_cost_cents,
                               hedge_cost_cents, overtime_included,
                               primary_probability, primary_source, at,
                               primary_partial_probability=None,
                               primary_calibration=None,
                               position_value=None,
                               model_key: str = KEY_HEDGE_GIVEN_PRIMARY
                               ) -> dict:
    """THE CLASS PROBABILITIES `decide` NEEDS, AND EVERYTHING THAT MAKES THEM
    FALSIFIABLE.

    Requires an APPROVED KEY_HEDGE_GIVEN_PRIMARY model -- with none it refuses
    R_NO_APPROVED_MODEL and names that key, exactly as `predict_for` refuses for
    KEY_MIDDLE; nothing falls back to an unregistered estimate. With one:

      1 the structure's payout classes (`bettor_payout_states.payout_classes`);
      2 the approved model's P(hedge wins | primary WIN) and | primary LOSE,
        each from its own feature vector;
      3 the void rate measured from recorded outcomes as of `at`
        (`bettor_pair_observations.void_rate`), no look-ahead;
      4 `bettor_payout_states.distribution` with `primary_probability` -- the
        probability HOLD is valued on -- as P(primary wins | not void) --
        and only once that probability has cleared the entry lane's own
        gates on an external source: a current passing calibration
        (`primary_calibration`) and the source's declared support.

    Returns the class probabilities keyed by class label (plus every
    unresolved state at zero), the merged structure and, when a position value
    is supplied, the position value merged the same way -- the table those
    probabilities price exactly. Plus the model id and version, both feature
    vectors and their shas, the void-rate basis and the primary source, which
    is what a decision must record to be scored later.

    `features` / `feature_sha` are the STRUCTURE'S base vector (KEY_MIDDLE's
    schema): the conditional's two vectors are that vector plus the primary
    outcome, and both are recorded under `features_by_primary_outcome`. The
    base vector keeps the decision row readable by every existing consumer of
    the ledger's `features` column.
    """
    from . import bettor_pair_observations as PO
    from . import bettor_payout_states as PS

    gates = primary_gates(primary_probability=primary_probability,
                          primary_calibration=primary_calibration)
    out: dict[str, Any] = {"version": VERSION, "model_key": model_key,
                           "path": "PAYOUT_STATE_DISTRIBUTION",
                           "primary_probability": primary_probability,
                           "primary_source": primary_source,
                           "primary_gates": gates}
    got = await approved(conn, model_key=model_key)
    if not got.get("ok"):
        return dict(out, ok=False, refusal=got["refusal"],
                    refused_for_key=model_key,
                    why=("%s (model key %s)" % (got.get("why") or
                                                got["refusal"], model_key)))
    mdl = got["model"]
    out.update(model_id=mdl["model_id"], model_version=mdl["model_version"])
    if not gates["MODEL_TRUST_DRIFT"]["clear"]:
        return dict(out, ok=False, refusal=R_PRIMARY_SOURCE_NOT_CALIBRATED,
                    why=gates["MODEL_TRUST_DRIFT"]["why"])
    if not gates["OUT_OF_DISTRIBUTION"]["clear"]:
        return dict(out, ok=False, refusal=R_PRIMARY_OUTSIDE_SUPPORT,
                    why=("P(primary) %r is outside the source's declared "
                         "support %s" % (
                             primary_probability,
                             gates["OUT_OF_DISTRIBUTION"]["support"])))
    classes = PS.payout_classes(structure)
    if not classes.get("ok"):
        return dict(out, ok=False, refusal=classes["refusal"],
                    why=classes.get("why"))
    base = features_of(structure, primary_cost_cents=primary_cost_cents,
                       hedge_cost_cents=hedge_cost_cents,
                       overtime_included=overtime_included)
    by_outcome = {
        PS.WIN: conditional_features_of(
            structure, primary_cost_cents=primary_cost_cents,
            hedge_cost_cents=hedge_cost_cents,
            overtime_included=overtime_included, primary_won=True),
        PS.LOSE: conditional_features_of(
            structure, primary_cost_cents=primary_cost_cents,
            hedge_cost_cents=hedge_cost_cents,
            overtime_included=overtime_included, primary_won=False)}
    obj = load(mdl["params"])
    q = {o: float(obj.predict(f)) for o, f in by_outcome.items()}
    # ── ONE EVENT UNDER THE PROBABILITY, THE MODEL AND THE TABLE ─────
    ev = PS.same_event(structure, primary_source=primary_source,
                       overtime_included=overtime_included)
    out["same_event"] = ev
    if not ev.get("ok"):
        return dict(out, ok=False, refusal=ev["refusal"], why=ev.get("why"),
                    mismatched=ev.get("mismatched"))
    # ── EVERY LEARNED CONDITIONAL STANDS ON ENOUGH EVIDENCE ──────────
    prov = mdl.get("training_provenance")
    if isinstance(prov, str):
        try:
            prov = json.loads(prov)
        except ValueError:
            prov = None
    learned = PS.learned_outcomes(classes)
    cohort = PS.cohort_evidence((prov or {}).get("records") or (),
                                learned_outcomes=learned,
                                predictions={o: q.get(o) for o in learned})
    out["conditional_evidence"] = cohort
    if not cohort.get("ok"):
        return dict(out, ok=False, refusal=cohort["refusal"],
                    why=cohort.get("why"),
                    conditional_evidence=cohort)
    void = await PO.void_rate(conn, through=at)
    void_in = None
    if void.get("ok"):
        void_in = {"rate": void["rate"], "n_fixtures": void["n_fixtures"],
                   "n_void_fixtures": void["n_void_fixtures"],
                   "upper_95": void["upper_95"],
                   "source": "bettor_pair_observations.void_rate",
                   "basis": void.get("basis"), "through": void.get("through")}
    primary = {"p_win": primary_probability,
               "p_partial": primary_partial_probability,
               "source": primary_source, "basis": PRIMARY_PROBABILITY_IS}
    predicted = {"target": TARGET_HEDGE_GIVEN_PRIMARY,
                 "p_hedge_wins_given_primary": dict(q),
                 "estimator": mdl["estimator"], "kernel": mdl["kernel"],
                 "model_version": mdl["model_version"],
                 "model_id": mdl["model_id"],
                 "feature_shas_by_primary_outcome": {
                     o: feature_sha(f) for o, f in by_outcome.items()}}
    dist = PS.distribution(classes, primary=primary, conditional=q,
                           void=void_in)
    if not dist.get("ok"):
        return dict(out, ok=False, refusal=dist["refusal"],
                    why=dist.get("why"), predicted=predicted,
                    void_read={k: v for k, v in void.items()
                               if k != "fixtures"},
                    distribution=dist)
    # THE SAME DISTRIBUTION WITH THE VOID MASS AT ITS UPPER 95% BOUND, so the
    # ranking can say whether its choice survives the rate's uncertainty.
    upper = None
    if (dist["basis"]["void"] or {}).get("used") and \
            void_in.get("upper_95") is not None:
        up = PS.distribution(classes, primary=primary, conditional=q,
                             void=dict(void_in, rate=void_in["upper_95"]))
        if up.get("ok"):
            upper = dict(up["probabilities"],
                         **up["unresolved_probabilities"])
    mpv = None
    if position_value is not None:
        mpv = PS.merged_position_value(position_value, classes)
        if not mpv.get("ok"):
            return dict(out, ok=False, refusal=mpv["refusal"],
                        why=mpv.get("why"), predicted=predicted)
    probs = dict(dist["probabilities"], **dist["unresolved_probabilities"])
    basis = {"model_key": model_key, "model_id": mdl["model_id"],
             "model_version": mdl["model_version"],
             "primary": dist["basis"]["primary"],
             "primary_gates": gates,
             "conditional": dist["basis"]["conditional"],
             "void": dist["basis"]["void"],
             "postponed": dist["basis"]["postponed"],
             "identified": dist["identified"],
             "probability_kinds": dist.get("probability_kinds"),
             "same_event": ev.get("agreements"),
             "conditional_evidence": cohort.get("cohorts"),
             "merge_rule": PS.MERGE_RULE}
    predicted.update(class_probabilities=dict(dist["probabilities"]),
                     classes=dist["classes"],
                     implied_primary_marginal=dist["implied_primary_marginal"],
                     primary_probability=primary_probability,
                     primary_source=primary_source,
                     distribution_basis=basis)
    return dict(out, ok=True, refusal=None,
                features=base, feature_sha=feature_sha(base),
                features_by_primary_outcome=by_outcome,
                predicted=predicted,
                region_probabilities=probs,
                region_probabilities_at_void_upper_95=upper,
                merged_structure=PS.merged_structure(structure, classes),
                merged_position_value=mpv,
                distribution={k: dist[k] for k in (
                    "probabilities", "classes", "basis", "identified",
                    "indistinguishable", "implied_primary_marginal",
                    "implied_primary_marginal_is", "sums_to")},
                void_read={k: v for k, v in void.items() if k != "fixtures"},
                distribution_basis=basis,
                probability_basis=(
                    "payout classes priced as (1 - void) x P(primary outcome) "
                    "x P(hedge class | primary outcome): the primary from the "
                    "probability HOLD is valued on, the conditional from the "
                    "table's structure or the approved model %s, the void mass "
                    "measured" % mdl["model_version"]))
