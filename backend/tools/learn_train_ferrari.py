"""TRAIN THE FERRARI BASELINE: completion, and whether completion clears.

    python backend/tools/learn_train_ferrari.py \
        --extract research/beta48/learning/extract_ferrari_<stamp>.json \
        --out     research/beta48/learning/ferrari_<stamp>/

TWO MODELS ON ONE SET OF ROWS, because the decomposition showed that
the first question is not the one that decides the money:

    complete   will Ferrari make a complementary BUY within H
    clears     will it do so at a pair price BELOW 1.00

Identical features, identical split, identical estimator. The only
difference is the target, so a gap between the two scores is a gap in
predictability and not in what the model was shown.

THE HORIZON IS NOT CHOSEN FROM THIS DATA. H = 3600 s, the same horizon
the RN1 baseline froze, so the two accounts are comparable and no
selection was made by looking at Ferrari's completion times first. The
observed time-to-completion distribution is REPORTED, and the report
says plainly that using it to pick a horizon would be a development
decision that has not been taken.

WHAT IT REFUSES. No EVAL number without a non-empty EVAL. No calibrator
fitted on TRAIN. No censored row scored. No net economics -- every pair
price here is gross, because `trades` has no fees.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from sportsassets.learn import ferrari as F      # noqa: E402
from sportsassets.learn import dataset as D      # noqa: E402
from sportsassets.learn import kernel as K       # noqa: E402
from sportsassets.learn import metrics as M      # noqa: E402

from learn_train_rn1 import decode, digest       # noqa: E402

HORIZON_S = 3600.0
TARGETS = ("complete", "clears")


def _fit_and_score(tr, ca, ev, target):
    """One target, three estimators, one calibrator. Returns the block."""
    Xtr, ytr = F.xy(tr, target=target)
    Xev, yev = F.xy(ev, target=target)
    base_rate = sum(ytr) / len(ytr)

    models = {
        "base_rate": K.BaseRate().fit(Xtr, ytr),
        "ridge": K.Ridge(list(F.FEATURES), l2=1.0, max_iter=100).fit(Xtr, ytr),
        "stumps": K.Stumps(list(F.FEATURES), rounds=60, learning_rate=0.1,
                           min_leaf=max(20, len(tr) // 50)).fit(Xtr, ytr),
    }
    ridge = models["ridge"]

    calib = None
    if ca:
        Xca, yca = F.xy(ca, target=target)
        calib = K.Isotonic().fit(ridge.predict_many(Xca), yca)

    scores = {}
    for name, m in models.items():
        scores[name] = M.report(m.predict_many(Xev), yev,
                                baseline_rate=base_rate, label=name)
    if calib is not None:
        pc = [calib.predict(v) for v in ridge.predict_many(Xev)]
        scores["ridge_calibrated"] = M.report(
            pc, yev, baseline_rate=base_rate, label="ridge_calibrated")

    groups = [r["entry_class"] for r in ev]

    # THE SAMPLE SIZE THAT DECIDES WHETHER ANY OF THIS MEANS ANYTHING.
    # The EVAL rows sit in far fewer markets than there are rows, and a
    # market's outcome drives every row in it, so the uncertainty is
    # over markets. Reported for the ridge and its calibrated form --
    # the two whose numbers anyone would quote.
    markets = [r["group_key"] for r in ev]
    clustered = {}
    p_ridge = ridge.predict_many(Xev)
    for name, pv in (("ridge", p_ridge),
                     ("ridge_calibrated",
                      [calib.predict(v) for v in p_ridge] if calib else None)):
        if pv is None:
            continue
        clustered[name] = {
            "auc": M.clustered_jackknife(pv, yev, markets, M.auc_stat),
            "skill": M.clustered_jackknife(pv, yev, markets,
                                           M.skill_stat(base_rate)),
        }

    return {
        "target": target,
        "target_name": (F.LABEL_COMPLETE if target == "complete"
                        else F.LABEL_CLEARS),
        "train_base_rate": base_rate,
        "eval_observed_rate": sum(yev) / len(yev),
        "scores": scores,
        "clustered_uncertainty": clustered,
        "by_entry_class": M.by_group(ridge.predict_many(Xev), yev, groups,
                                     baseline_rate=base_rate, min_n=30),
        "ridge_weights_raw_scale": ridge.weights_on_raw_scale(),
        "stumps_explain": models["stumps"].explain()[:10],
        "calibrated": calib is not None,
        "_models": models,
        "_calib": calib,
    }


def _time_to_completion(rows) -> dict:
    """Descriptive only. See the module docstring on why it is not used
    to pick the horizon."""
    t = sorted(r["time_to_event_s"] for r in rows
               if r["time_to_event_s"] is not None)
    if not t:
        return {"n": 0}

    def q(p):
        i = p * (len(t) - 1)
        lo = int(i)
        hi = min(lo + 1, len(t) - 1)
        return t[lo] + (t[hi] - t[lo]) * (i - lo)

    return {
        "n": len(t),
        "p05_s": round(q(0.05), 1), "median_s": round(q(0.50), 1),
        "p95_s": round(q(0.95), 1), "max_s": round(t[-1], 1),
        "within_60s": sum(1 for v in t if v <= 60.0),
        "within_300s": sum(1 for v in t if v <= 300.0),
        "note": "DESCRIPTIVE. The horizon was frozen at 3600 s from the "
                "RN1 baseline before these numbers were computed; using "
                "them to re-pick it would be a development decision that "
                "has not been taken.",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--train-frac", type=float, default=0.6)
    ap.add_argument("--calib-frac", type=float, default=0.2)
    ap.add_argument("--exclude", action="append", default=[],
                    help="FROM,TO epoch seconds; repeatable")
    a = ap.parse_args()

    extract = json.load(open(a.extract))
    fills = decode(extract)
    read_at = float(extract["read_at"])

    excl = []
    for e in a.exclude:
        lo, hi = e.split(",")
        excl.append((float(lo), float(hi)))

    ds = F.build(
        fills, horizon_s=HORIZON_S, observation_end=read_at,
        prior_inventory=extract.get("prior_inventory"),
        conditions_list=extract["conditions_list"],
        prior_known=("prior_inventory" in extract),
        coverage_exclusions=excl,
    )

    report = {
        "generated_at": time.time(),
        "ferrari_version": F.VERSION,
        "dataset_version": D.VERSION,
        "kernel_version": K.VERSION,
        "metrics_version": M.VERSION,
        "extract": {k: extract[k] for k in
                    ("account", "address", "window_days", "sample",
                     "read_at", "n", "conditions", "by_source")},
        "dataset": {k: ds[k] for k in
                    ("targets", "parity", "pair_price_definition",
                     "horizon_s", "n_rows", "n_decided", "n_censored",
                     "n_completed", "n_cleared", "base_rate_complete",
                     "base_rate_clears", "clears_given_completed",
                     "prior_inventory_status", "prior_inventory_conditions",
                     "left_censoring_note", "entry_class_census", "skipped",
                     "lifecycle_observability", "coverage_exclusions")},
        "pair_price_summary": F.pair_price_summary(ds["rows"]),
        "time_to_completion": _time_to_completion(ds["rows"]),
    }

    ts_all = sorted(r["decision_at"] for r in ds["rows"])
    if not ts_all:
        report["status"] = "NO_ROWS"
        _write(a.out, report, None)
        print(json.dumps(report["dataset"], indent=1))
        return 1

    lo, hi = ts_all[0], ts_all[-1]
    train_end = lo + (hi - lo) * a.train_frac
    calib_end = lo + (hi - lo) * (a.train_frac + a.calib_frac)
    sp = D.split_by_time(ds, train_end=train_end, calib_end=calib_end)

    tr = D.uncensored(sp["parts"]["TRAIN"])
    ca = D.uncensored(sp["parts"]["CALIB"])
    ev = D.uncensored(sp["parts"]["EVAL"])

    report["split"] = {k: sp[k] for k in
                       ("counts", "positives", "censored", "conditions",
                        "dropped_for_group_overlap", "rule")}
    report["split_boundaries"] = {
        "train_end": train_end, "calib_end": calib_end,
        "first_decision": lo, "last_decision": hi}
    report["uncensored_counts"] = {"TRAIN": len(tr), "CALIB": len(ca),
                                   "EVAL": len(ev)}

    if not tr or not ev:
        report["status"] = "INSUFFICIENT_DATA"
        report["why"] = ("TRAIN has %d and EVAL has %d decided rows; a "
                         "model is not reported without both"
                         % (len(tr), len(ev)))
        _write(a.out, report, None)
        print("STATUS:", report["status"], "-", report["why"])
        return 1

    blocks, keep = {}, {}
    for t in TARGETS:
        b = _fit_and_score(tr, ca, ev, t)
        keep[t] = {"models": b.pop("_models"), "calib": b.pop("_calib")}
        blocks[t] = b

    report["status"] = "TRAINED"
    report["models"] = blocks
    report["dev_vs_prospective"] = {
        "development_through": calib_end,
        "eval_is_prospective": False,
        "why": "every row at or before the CALIB boundary was inspected "
               "while this pipeline was built, and the EVAL block was "
               "generated BEFORE this commit. It is a held-out test, not "
               "a prospective one. A prospective claim needs predictions "
               "recorded before the action, which the prediction ledger "
               "exists to do.",
    }

    preds = []
    for r in sp["parts"]["EVAL"]:
        f = r["features"]
        row = {
            "subject": F.ACCOUNT,
            "condition_id": r["condition_id"],
            "outcome_index": r["outcome_index"],
            "entry_at": r["entry_at"],
            "decision_at": r["decision_at"],
            "horizon_s": r["horizon_s"],
            "matures_at": r["decision_at"] + r["horizon_s"],
            "entry_class": r["entry_class"],
            "baseline_p_complete": blocks["complete"]["train_base_rate"],
            "baseline_p_clears": blocks["clears"]["train_base_rate"],
            "features": f,
            "input_sha": digest(f),
            "retrospective": True,
            "why_retrospective":
                "reconstructed from history; its outcome was already in "
                "the extract when the prediction was computed",
        }
        for t in TARGETS:
            m = keep[t]["models"]["ridge"]
            c = keep[t]["calib"]
            p = m.predict(f)
            row["p_" + t] = p
            row["p_%s_calibrated" % t] = c.predict(p) if c else None
        preds.append(row)

    report["dataset_sha"] = digest(
        {"extract": os.path.basename(a.extract), "n": ds["n_rows"],
         "horizon": HORIZON_S})
    report["n_predictions_emitted"] = len(preds)
    report["model_artifacts"] = {
        t: {n: m.to_dict() for n, m in keep[t]["models"].items()
            if n != "base_rate"}
        for t in TARGETS}
    for t in TARGETS:
        if keep[t]["calib"] is not None:
            report["model_artifacts"][t]["isotonic"] = \
                keep[t]["calib"].to_dict()

    _write(a.out, report, preds)

    print(json.dumps({
        "dataset": {k: report["dataset"][k] for k in
                    ("n_rows", "n_decided", "n_censored", "n_completed",
                     "n_cleared", "base_rate_complete", "base_rate_clears",
                     "clears_given_completed", "entry_class_census",
                     "prior_inventory_status", "skipped")},
        "pair_price_summary": report["pair_price_summary"],
        "time_to_completion": report["time_to_completion"],
        "split": report["split"]["counts"],
        "uncensored": report["uncensored_counts"],
        "scores": {t: {k: {"log_loss": v["log_loss"],
                           "baseline_log_loss": v["baseline"]["log_loss"],
                           "skill": v["skill_vs_baseline"].get("skill"),
                           "verdict": v["skill_vs_baseline"].get("verdict"),
                           "auc": v["auc"].get("auc"),
                           "ece": v["calibration"]["ece"]}
                       for k, v in blocks[t]["scores"].items()}
                   for t in TARGETS},
        "eval_observed_rate": {t: blocks[t]["eval_observed_rate"]
                               for t in TARGETS},
    }, indent=1))
    return 0


def _write(out, report, preds):
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1)
    if preds is not None:
        with open(os.path.join(out, "predictions.json"), "w") as fh:
            json.dump(preds, fh, indent=1)
    print("written:", out)


if __name__ == "__main__":
    raise SystemExit(main())
