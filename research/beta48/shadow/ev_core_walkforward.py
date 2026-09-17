#!/usr/bin/env python3
"""EV CORE -- WALK-FORWARD VALIDATION. The part that decides what is real.

THE ONE RULE. A model is scored only on events it could not have seen. For
time-ordered sports markets that means chronological, rolling-origin folds --
never a random split.

WHY A RANDOM SPLIT IS FATAL HERE, SPECIFICALLY. Sixteen totals lines, a
moneyline, a draw and an exact-score grid all belong to one fixture and share
one outcome. A random row split puts some of them in train and the rest in
test, so the model learns the answer from the same fixture it is then asked
about. The result looks superb and means nothing. Every fold therefore GROUPS
BY EVENT: a fixture is wholly in train or wholly in test, never split.

FOLD LAYOUT. Events are ordered by their own date, then cut into contiguous
blocks. Fold k trains on everything strictly before block k and tests on block
k. Nothing from the future ever reaches a training set -- not a later price,
not a later settlement, not a later whale trade.

THE FINAL HOLDOUT IS NOT A FOLD. The last slice of time is held out entirely
and is not touched while models are being compared. Selecting on it would turn
it into a validation set, and there would then be no holdout at all.

MULTIPLICITY IS TRACKED, NOT WISHED AWAY. With a hundred thousand rows it is
trivial to find a subgroup whose interval excludes zero. Every comparison this
module runs is counted, so the number of looks is visible beside the result and
a Bonferroni-style threshold can be read off honestly.

This module contacts nothing and can place no order.
"""
import json
from collections import Counter, defaultdict

import ev_core_calibration as CAL

NOT_IDENTIFIED = "NOT_IDENTIFIED"

GROUPING_UNIT = "EVENT"
WHY_GROUPED = (
    "every contract on one fixture shares one outcome; splitting a fixture "
    "across train and test lets the model read the answer off its own sibling")
NO_RANDOM_SPLIT = (
    "a random split on time-ordered markets leaks the future; folds here are "
    "chronological and rolling-origin")
HOLDOUT_RULE = (
    "the final holdout is scored once, after the champion is frozen; any "
    "selection on it destroys it")


def _event_date(rows):
    """One date per event, taken as its EARLIEST observation.

    Earliest, not latest: the fold boundary must not be moved later by a late
    observation, which would let an event train on its own near-neighbours.
    """
    d = {}
    for r in rows:
        k = r["EVENT_KEY"]
        ts = r.get("AS_OF") or ""
        if k not in d or ts < d[k]:
            d[k] = ts
    return d


def make_folds(rows, n_folds=5, holdout_frac=0.2):
    """Chronological, event-grouped folds plus an untouched final holdout."""
    dates = _event_date(rows)
    order = sorted(dates, key=lambda k: (dates[k], k))
    n = len(order)
    if n < n_folds + 2:
        return {"FOLDS": [], "HOLDOUT_EVENTS": [],
                "WHY": "too few events to fold"}

    cut = int(n * (1.0 - holdout_frac))
    train_pool, holdout = order[:cut], order[cut:]

    block = max(1, len(train_pool) // (n_folds + 1))
    folds = []
    for k in range(1, n_folds + 1):
        tr_end = block * k
        te_end = min(len(train_pool), block * (k + 1))
        if tr_end >= te_end:
            continue
        folds.append({
            "FOLD": k,
            "TRAIN_EVENTS": train_pool[:tr_end],
            "TEST_EVENTS": train_pool[tr_end:te_end],
            "TRAIN_UNTIL": dates[train_pool[tr_end - 1]],
            "TEST_FROM": dates[train_pool[tr_end]],
        })
    return {
        "FOLDS": folds,
        "HOLDOUT_EVENTS": holdout,
        "HOLDOUT_FROM": dates[holdout[0]] if holdout else NOT_IDENTIFIED,
        "N_EVENTS": n,
        "N_TRAIN_POOL_EVENTS": len(train_pool),
        "N_HOLDOUT_EVENTS": len(holdout),
        "GROUPING_UNIT": GROUPING_UNIT,
        "WHY_GROUPED": WHY_GROUPED,
        "NO_RANDOM_SPLIT": NO_RANDOM_SPLIT,
        "HOLDOUT_RULE": HOLDOUT_RULE,
    }


def _subset(rows, keys):
    ks = set(keys)
    return [r for r in rows if r["EVENT_KEY"] in ks]


def leakage_check(folds, rows):
    """Prove no event and no timestamp crosses a fold boundary."""
    problems = []
    for f in folds.get("FOLDS", ()):
        tr, te = set(f["TRAIN_EVENTS"]), set(f["TEST_EVENTS"])
        shared = tr & te
        if shared:
            problems.append({"FOLD": f["FOLD"], "SHARED_EVENTS": len(shared)})
        if f["TRAIN_UNTIL"] > f["TEST_FROM"]:
            problems.append({"FOLD": f["FOLD"], "TIME_ORDER_VIOLATION": True,
                             "TRAIN_UNTIL": f["TRAIN_UNTIL"],
                             "TEST_FROM": f["TEST_FROM"]})
    hold = set(folds.get("HOLDOUT_EVENTS", ()))
    for f in folds.get("FOLDS", ()):
        if hold & set(f["TRAIN_EVENTS"]):
            problems.append({"FOLD": f["FOLD"], "HOLDOUT_IN_TRAIN": True})
        if hold & set(f["TEST_EVENTS"]):
            problems.append({"FOLD": f["FOLD"], "HOLDOUT_IN_TEST": True})
    return {
        "PROBLEMS": problems,
        "CLEAN": not problems,
        "CHECKS_RUN": ["EVENT_OVERLAP_TRAIN_TEST", "CHRONOLOGICAL_ORDER",
                       "HOLDOUT_ISOLATION"],
        "WHAT_A_PROBLEM_MEANS": (
            "any entry here invalidates every score produced from these folds"),
    }


def run(rows, models, n_folds=5, holdout_frac=0.2, draws=120):
    """Fit and score every model on every fold. The holdout is NOT scored here.

    `models` maps a name to a factory: fit(train_rows) -> predict(row) -> p.
    """
    folds = make_folds(rows, n_folds, holdout_frac)
    leak = leakage_check(folds, rows)
    results = defaultdict(list)
    comparisons = 0

    for f in folds.get("FOLDS", ()):
        tr = _subset(rows, f["TRAIN_EVENTS"])
        te = _subset(rows, f["TEST_EVENTS"])
        if not tr or not te:
            continue
        for name, factory in models.items():
            predict = factory(tr)
            scored = [{"EVENT_KEY": r["EVENT_KEY"], "P": predict(r),
                       "Y": r["SETTLED_YES"]} for r in te]
            rep = CAL.evaluate(scored, label="%s@fold%d" % (name, f["FOLD"]),
                               draws=draws)
            results[name].append({
                "FOLD": f["FOLD"], "TRAIN_ROWS": len(tr), "TEST_ROWS": len(te),
                "TEST_EVENTS": len(f["TEST_EVENTS"]),
                "LOG_LOSS": rep["LOG_LOSS"], "BRIER": rep["BRIER"],
                "CALIBRATION_SLOPE": rep.get("CALIBRATION_SLOPE"),
                "RELIABILITY": rep.get("RELIABILITY"),
                "RESOLUTION": rep.get("RESOLUTION"),
            })
            comparisons += 1

    summary = {}
    for name, fs in results.items():
        lls = [x["LOG_LOSS"] for x in fs if isinstance(x["LOG_LOSS"], float)]
        brs = [x["BRIER"] for x in fs if isinstance(x["BRIER"], float)]
        summary[name] = {
            "FOLDS_SCORED": len(fs),
            "MEAN_LOG_LOSS": (sum(lls) / len(lls)) if lls else NOT_IDENTIFIED,
            "MEAN_BRIER": (sum(brs) / len(brs)) if brs else NOT_IDENTIFIED,
            "WORST_FOLD_LOG_LOSS": max(lls) if lls else NOT_IDENTIFIED,
            "PER_FOLD": fs,
        }
    return {
        "FOLD_PLAN": {k: v for k, v in folds.items()
                      if k not in ("FOLDS", "HOLDOUT_EVENTS")},
        "FOLD_BOUNDARIES": [{"FOLD": f["FOLD"], "TRAIN_UNTIL": f["TRAIN_UNTIL"],
                             "TEST_FROM": f["TEST_FROM"],
                             "TRAIN_EVENTS": len(f["TRAIN_EVENTS"]),
                             "TEST_EVENTS": len(f["TEST_EVENTS"])}
                            for f in folds.get("FOLDS", ())],
        "LEAKAGE_CHECK": leak,
        "SUMMARY": summary,
        "COMPARISONS_RUN": comparisons,
        "MULTIPLICITY_NOTE": (
            "%d model-fold scores were computed; a subgroup interval that "
            "excludes zero is not surprising at this many looks, and any claim "
            "of an effect should survive a threshold adjusted for them"
            % comparisons),
        "HOLDOUT_NOT_SCORED_HERE": True,
        "HOLDOUT_RULE": HOLDOUT_RULE,
    }


def score_holdout(rows, model_factory, name, n_folds=5, holdout_frac=0.2,
                  draws=200):
    """The one-shot final read. Train on everything before, score the holdout.

    Call this ONCE, after the champion is chosen. It is separated from `run`
    so that scoring the holdout is a deliberate act, not something that happens
    while comparing models.
    """
    folds = make_folds(rows, n_folds, holdout_frac)
    hold = folds.get("HOLDOUT_EVENTS", [])
    if not hold:
        return {"HOLDOUT_STATUS": NOT_IDENTIFIED, "WHY": "no holdout events"}
    tr = _subset(rows, [k for f in folds["FOLDS"]
                        for k in f["TRAIN_EVENTS"] + f["TEST_EVENTS"]])
    te = _subset(rows, hold)
    predict = model_factory(tr)
    scored = [{"EVENT_KEY": r["EVENT_KEY"], "P": predict(r),
               "Y": r["SETTLED_YES"]} for r in te]
    rep = CAL.evaluate(scored, label="%s@HOLDOUT" % name, draws=draws)
    rep["HOLDOUT_FROM"] = folds.get("HOLDOUT_FROM")
    rep["TRAIN_ROWS"] = len(tr)
    rep["HOLDOUT_ROWS"] = len(te)
    rep["HOLDOUT_EVENTS"] = len(hold)
    rep["THIS_IS_THE_ONE_SHOT_READ"] = True
    return rep


def render(rep):
    L = ["=== FOLD PLAN ==="]
    for b in rep.get("FOLD_BOUNDARIES", ()):
        L.append("  fold %d  train<=%s (%d ev)  test>=%s (%d ev)"
                 % (b["FOLD"], b["TRAIN_UNTIL"][:10], b["TRAIN_EVENTS"],
                    b["TEST_FROM"][:10], b["TEST_EVENTS"]))
    L.append("  LEAKAGE_CHECK CLEAN = %s"
             % rep.get("LEAKAGE_CHECK", {}).get("CLEAN"))
    L.append("")
    L.append("=== WALK-FORWARD RESULTS (mean over folds) ===")
    L.append("%-34s %11s %10s %8s" % ("MODEL", "LOG_LOSS", "BRIER", "FOLDS"))
    for name, s in sorted(rep.get("SUMMARY", {}).items(),
                          key=lambda kv: (kv[1]["MEAN_LOG_LOSS"]
                                          if isinstance(kv[1]["MEAN_LOG_LOSS"],
                                                        float) else 9e9)):
        ll, br = s["MEAN_LOG_LOSS"], s["MEAN_BRIER"]
        L.append("%-34s %11s %10s %8d"
                 % (name, ("%.6f" % ll) if isinstance(ll, float) else ll,
                    ("%.6f" % br) if isinstance(br, float) else br,
                    s["FOLDS_SCORED"]))
    return "\n".join(L)


def to_json(rep):
    return json.dumps(rep, indent=1, sort_keys=True, default=str)
