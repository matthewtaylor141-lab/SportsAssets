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

NOT A NEW PROBABILITY OVER THE WHOLE FIXTURE. The model answers ONE question --
`p(the structure's both-win region occurs)` -- and the mass outside that region has
to come from somewhere the caller names. `region_probabilities` REFUSES without an
outside split rather than spreading the remainder uniformly, because a uniform
assumption nobody stated is an assertion about the fixture.

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

#: ── THE ONE QUESTION THIS LANE LEARNS ───────────────────────────────
KEY_MIDDLE = "funded_pair_middle_region"
TARGET = "MIDDLE_REGION_OCCURRED"

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

#: ── THE PROMOTION BAR, DECLARED HERE AND NOT PER CALL ───────────────
#:
#: A threshold chosen at promotion time is a threshold chosen to be cleared. These
#: are the lane's, they are readable before any candidate exists, and `promote`
#: takes no argument that can loosen them.
MIN_EVALUATION_ROWS = 40
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

def fit(rows, labels, *, estimator: str = "RIDGE_LOGISTIC", **kw) -> dict:
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
    # `BaseRate` TAKES NO FEATURE LIST, and that is not an inconsistency to
    # paper over: it predicts the training mean for every input, so there is no
    # vector for it to have. Its `features` column is still the lane's full list
    # -- what the model MAY be scored on -- so the registry rows are comparable.
    mdl = (_ESTIMATOR_CLASS[estimator]()
           if estimator == "BASE_RATE"
           else _ESTIMATOR_CLASS[estimator](list(FEATURES), **kw))
    mdl.fit(list(rows), ys)
    return dict(out, ok=True, refusal=None,
                params=mdl.to_dict(), kernel=K.VERSION,
                features=list(FEATURES), train_rows=len(ys),
                train_base_rate=round(sum(ys) / len(ys), 9),
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
    for f in ("params", "evaluation"):
        if isinstance(d.get(f), str):
            try:
                d[f] = json.loads(d[f])
            except Exception:                                   # noqa: BLE001
                pass
    for f in ("train_base_rate",):
        if d.get(f) is not None:
            d[f] = float(d[f])
    return d


async def register(conn, *, model_id: str, model_version: str, fitted: dict,
                   fit_through, model_key: str = KEY_MIDDLE) -> dict:
    """RECORD A CANDIDATE. It has no authority over any decision.

    `fit_through` is the last instant the fit could see, and it is NOT NULLABLE in
    the schema. Everything §4 checks about prospectiveness is checked against this
    column, so a caller cannot promote a model by describing its evaluation as
    held-out.
    """
    out: dict[str, Any] = {"version": VERSION, "model_id": str(model_id)}
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE)
    if not fitted.get("ok"):
        return dict(out, ok=False, refusal=fitted.get("refusal"),
                    why="the fit itself did not succeed")
    await conn.execute(
        "INSERT INTO bettor_funded_models "
        "(model_id, model_key, model_version, state, kernel, estimator, "
        " features, params, fit_through, train_rows, train_base_rate) VALUES "
        "($1,$2,$3,$4,$5,$6,$7::text[],$8::jsonb,$9,$10,$11) "
        "ON CONFLICT (model_id) DO NOTHING",
        str(model_id), str(model_key), str(model_version), STATE_CANDIDATE,
        fitted["kernel"], fitted["estimator"], list(fitted["features"]),
        json.dumps(fitted["params"], default=str), fit_through,
        int(fitted["train_rows"]), fitted.get("train_base_rate"))
    return dict(out, ok=True, state=STATE_CANDIDATE,
                model=_row(await conn.fetchrow(
                    "SELECT * FROM bettor_funded_models WHERE model_id=$1",
                    str(model_id))),
                authority=("NONE. A candidate is fitted and evaluated; the "
                           "decision path reads only the APPROVED row"))


async def approved(conn, *, model_key: str = KEY_MIDDLE) -> dict:
    """THE MODEL THE DECISION PATH MUST USE, or a refusal naming its absence.

    NO APPROVED MODEL IS A REAL ANSWER AND IT IS NOT A FALLBACK. The caller then
    has no model-derived probability, and `bettor_funded_decision` already refuses
    an indirect candidate with no region probabilities -- so the lane declines the
    acquisition rather than deciding from an unregistered estimate.
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
    return dict(out, ok=True, refusal=None, model=row)


# ═════════════════════════════════════════════════════════════════════
# 4 · LABELS AND THE PROSPECTIVE EVALUATION
# ═════════════════════════════════════════════════════════════════════

LABEL_SQL = """
    SELECT d.decision_id, d.group_id, d.features, d.feature_sha,
           d.model_version, d.predicted,
           extract(epoch FROM d.decided_at) AS decided_epoch,
           d.decided_at,
           g.middle_occurred, g.roles, g.legs
      FROM bettor_funded_decisions d
      JOIN LATERAL (
        SELECT count(*) AS legs,
               count(DISTINCT i.leg_role) AS roles,
               -- ── `settlement IS NOT NULL` WAS A VACUOUS CONDITION ──
               --
               -- MEASURED, not reasoned about: the column is NOT NULL with a
               -- `'{}'::jsonb` default, so that test was true for every row
               -- including one whose fixture had never been read. It looked
               -- like a settlement check and constrained nothing.
               --
               -- The condition that means what was intended is that the
               -- settlement record STATES A PAYOUT. `? 'payout_usd'` is that,
               -- and it is also exactly the key the label below reads -- so a
               -- row cannot pass this and then contribute a coalesce'd zero.
               bool_and(i.closed_reason IS NOT NULL
                        AND i.settlement ? 'payout_usd') AS settled,
               -- THE LABEL: did BOTH legs pay? That is the both-win region, and
               -- it is read from each leg's own settlement payout rather than
               -- from a score this lane never sees.
               bool_and(coalesce((i.settlement::jsonb ->> 'payout_usd')
                                 ::numeric, 0) > 0) AS middle_occurred
          FROM bettor_funded_intents i
         WHERE i.portfolio_group_id = d.group_id AND i.kind = 'ENTRY'
      ) g ON TRUE
     WHERE d.group_id IS NOT NULL
       AND d.features IS NOT NULL
       -- ── BOTH ROLES, OR THERE IS NO MIDDLE TO HAVE OCCURRED ──────
       --
       -- A REAL DEFECT, CAUGHT BY RE-READING THIS QUERY. `bool_and(paid)` over
       -- a SINGLE-LEG group is just "did that one leg pay" -- and a moneyline
       -- that won would have been labelled `middle_occurred = true` for a
       -- structure that was never acquired. Those labels would then train the
       -- model to predict the primary leg's win rate while every consumer read
       -- the output as p(both legs pay), and the error is in the direction that
       -- buys hedges: the primary leg wins more often than the middle lands.
       AND g.roles = 2
       -- ── AND EVERY LEG SETTLED, not merely one of them ────────────
       --
       -- `bool_or` here was wrong for the same reason: a group with one leg
       -- settled and one still open would have been labelled from a fixture
       -- that had not finished for the other leg. `bool_and` requires all of
       -- them.
       AND g.settled
"""


async def labelled(conn, *, model_key: str = KEY_MIDDLE, after=None,
                   account_id: str | None = None) -> dict:
    """EVERY DECISION WHOSE FIXTURE HAS RESOLVED, with its prospective vector.

    THE LABEL COMES FROM THE VENUE'S SETTLEMENT, per leg. `middle_occurred` is
    true when BOTH legs were paid -- which is the classifier's both-win region,
    observed rather than modelled. A group with any leg unsettled is excluded
    entirely: a label read before the fixture finished is not a label.

    `after` IS THE PROSPECTIVE FILTER and the caller passes the registry's own
    `fit_through`. Nothing here defaults it, because a default would silently
    make every evaluation retrospective.
    """
    out: dict[str, Any] = {"version": VERSION, "model_key": model_key,
                           "rows": [], "labels": []}
    sql, args = LABEL_SQL, []
    if after is not None:
        args.append(after)
        sql += " AND d.decided_at > $%d" % len(args)
    if account_id is not None:
        args.append(str(account_id))
        sql += " AND d.account_id = $%d" % len(args)
    sql += " ORDER BY d.decided_at"
    try:
        got = await conn.fetch(sql, *args)
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, refusal="THE_LABELS_COULD_NOT_BE_READ",
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]))
    for r in got:
        feats = r["features"]
        if isinstance(feats, str):
            feats = json.loads(feats)
        if r["middle_occurred"] is None:
            continue
        out["rows"].append(feats)
        out["labels"].append(1.0 if r["middle_occurred"] else 0.0)
        out.setdefault("decision_ids", []).append(r["decision_id"])
        out.setdefault("groups", []).append(r["group_id"])
        out.setdefault("decided_at", []).append(float(r["decided_epoch"]))
    return dict(out, ok=True, refusal=None, n=len(out["labels"]),
                label_basis=("BOTH LEGS PAID, from each leg's own venue "
                             "settlement payout. Not a score, not a model"),
                only_two_role_groups=(
                    "a group carrying one role has no both-win region, so "
                    "'did both legs pay' is not a question about it. Labelling "
                    "one would train the model on the primary leg's win rate "
                    "while every consumer reads p(both legs pay)"),
                prospective_filter=("decided_at > %r" % (after,)
                                    if after is not None
                                    else "NONE -- every resolved decision"))


async def evaluate(conn, *, model_id: str, account_id: str | None = None,
                   now: float | None = None) -> dict:
    """SCORE A CANDIDATE ON DECISIONS IT COULD NOT HAVE SEEN, and record it.

    THE PROSPECTIVE WINDOW IS READ FROM THE REGISTRY, not from the caller. That is
    the difference between an evaluation and a claim about one: `fit_through` is
    immutable by trigger, so the set this scores on is exactly the set decided
    after the fit, whatever the caller believes.

    THE BASELINE IS THE TRAINING BASE RATE, carried on the model row.
    `metrics.report` refuses to score without one, and refuses the evaluation
    set's own rate -- which would flatter the model on precisely the split where
    the rate has moved.
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
    out["model_key"] = mdl["model_key"]
    out["fit_through"] = mdl["fit_through"]
    lab = await labelled(conn, model_key=mdl["model_key"],
                         after=mdl["fit_through"], account_id=account_id)
    if not lab.get("ok"):
        return dict(out, ok=False, refusal=lab["refusal"],
                    error=lab.get("error"))
    out["n"] = lab["n"]
    if lab["n"] < MIN_EVALUATION_ROWS:
        return dict(out, ok=False, refusal=R_TOO_FEW_LABELS,
                    n=lab["n"], required=MIN_EVALUATION_ROWS,
                    why=("%d labelled decision(s) after this model's fit "
                         "window. A promotion on fewer is a promotion on noise"
                         % lab["n"]))
    obj = load(mdl["params"])
    preds = [float(obj.predict(r)) for r in lab["rows"]]
    rep = M.report(preds, lab["labels"],
                   baseline_rate=mdl.get("train_base_rate"),
                   label="%s@%s" % (mdl["model_key"], mdl["model_version"]))
    # ── THE PROSPECTIVE CLAIM, CHECKED RATHER THAN ASSERTED ──────────
    #
    # The SQL already filters on `fit_through`, so this re-reads the outcome and
    # states it. A claim in a docstring is not a check, and this field is what a
    # later reader sees beside the score.
    fit_epoch = mdl["fit_through"].timestamp()
    leaked = [d for d in lab.get("decided_at", []) if d <= fit_epoch]
    rep["prospective"] = {
        "fit_through_epoch_s": fit_epoch,
        "rows_the_fit_could_have_seen": len(leaked),
        "verdict": ("PROSPECTIVE" if not leaked else "CONTAMINATED"),
        "why_it_is_checked": ("a model scored on rows it was fitted to measures "
                             "its memory, and that is how a model with no skill "
                             "gets promoted"),
    }
    rep["clustered_by_group"] = M.clustered_jackknife(
        preds, lab["labels"], lab.get("groups", []),
        M.skill_stat(mdl.get("train_base_rate")))
    await conn.execute(
        "UPDATE bettor_funded_models SET evaluation=$2::jsonb "
        " WHERE model_id=$1", str(model_id), json.dumps(rep, default=str))
    return dict(out, ok=bool(not leaked), refusal=(
        None if not leaked else R_EVALUATION_NOT_PROSPECTIVE),
        evaluation=rep, decision_ids=lab.get("decision_ids", []))


# ═════════════════════════════════════════════════════════════════════
# 5 · PROMOTION AND ROLLBACK
# ═════════════════════════════════════════════════════════════════════

async def promote(conn, *, model_id: str, approved_by: str,
                  now: float | None = None) -> dict:
    """APPROVE A CANDIDATE, or refuse and name which condition it failed.

    FOUR CONDITIONS, NONE OF THEM PASSABLE BY ARGUMENT:

      * it is a CANDIDATE (a retired model does not return),
      * it has a recorded evaluation whose own verdict is PROSPECTIVE,
      * that evaluation saw at least `MIN_EVALUATION_ROWS`,
      * and it beats the incumbent on `PROMOTION_METRIC` by `MIN_SKILL_MARGIN`.
        With NO incumbent it must beat its own declared baseline by the same
        margin -- a first model is not admitted merely for being first.

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
    ev = cand.get("evaluation") or None
    if not ev:
        return dict(out, ok=False, refusal=R_NOT_EVALUATED,
                    why="run `evaluate` first; there is nothing to promote on")
    pros = dict(ev.get("prospective") or {})
    if pros.get("verdict") != "PROSPECTIVE":
        return dict(out, ok=False, refusal=R_EVALUATION_NOT_PROSPECTIVE,
                    prospective=pros)
    if int(ev.get("n") or 0) < MIN_EVALUATION_ROWS:
        return dict(out, ok=False, refusal=R_TOO_FEW_LABELS,
                    n=ev.get("n"), required=MIN_EVALUATION_ROWS)
    # ── THE BAR ──────────────────────────────────────────────────────
    inc = _row(await conn.fetchrow(
        "SELECT * FROM bettor_funded_models "
        " WHERE model_key=$1 AND state=$2", cand["model_key"], STATE_APPROVED))
    if ev.get(PROMOTION_METRIC) is None:
        return dict(out, ok=False, refusal=R_NOT_EVALUATED,
                    why=("this candidate's evaluation carries no %r, so there "
                         "is no number to compare. An evaluation missing the "
                         "promotion metric is not an evaluation"
                         % PROMOTION_METRIC))
    cand_metric = float(ev[PROMOTION_METRIC])
    if inc is None:
        # NO INCUMBENT. The bar is the candidate's own declared baseline, which
        # is the TRAINING base rate and was fixed before this evaluation existed.
        against = float((ev.get("baseline") or {}).get(PROMOTION_METRIC))
        against_what = "ITS_OWN_DECLARED_BASELINE"
    else:
        inc_ev = dict(inc.get("evaluation") or {})
        if inc_ev.get(PROMOTION_METRIC) is None:
            return dict(out, ok=False, refusal=R_NOT_EVALUATED,
                        why=("the incumbent carries no evaluation to compare "
                             "against, so no margin can be established"))
        against = float(inc_ev[PROMOTION_METRIC])
        against_what = "THE_INCUMBENT"
    #: LOWER IS BETTER for log loss and Brier. Named rather than assumed, because
    #: the sign of this comparison is the whole promotion decision.
    improvement = round(against - cand_metric, 9)
    out["comparison"] = {"metric": PROMOTION_METRIC,
                         "lower_is_better": True,
                         "candidate": cand_metric, "against": against,
                         "against_what": against_what,
                         "improvement": improvement,
                         "required": MIN_SKILL_MARGIN}
    if improvement < MIN_SKILL_MARGIN:
        return dict(out, ok=False, refusal=R_NO_SKILL,
                    why=("%s of %.6f against %.6f is an improvement of %.6f, "
                         "and the declared bar is %.6f"
                         % (PROMOTION_METRIC, cand_metric, against,
                            improvement, MIN_SKILL_MARGIN)))
    async with conn.transaction():
        if inc is not None:
            await conn.execute(
                "UPDATE bettor_funded_models SET state=$2, retired_at=now(), "
                "  retired_reason=$3, superseded_by=$4 WHERE model_id=$1",
                inc["model_id"], STATE_RETIRED,
                "SUPERSEDED_BY_A_PROMOTED_CANDIDATE", str(model_id))
        await conn.execute(
            "UPDATE bettor_funded_models SET state=$2, approved_at=now(), "
            "  approved_by=$3 WHERE model_id=$1",
            str(model_id), STATE_APPROVED, str(approved_by))
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


# ═════════════════════════════════════════════════════════════════════
# 6 · FROM A PREDICTION TO THE DECISION'S OWN INPUT
# ═════════════════════════════════════════════════════════════════════

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
