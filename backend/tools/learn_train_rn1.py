"""TRAIN THE FIRST BASELINE, from a real extract, end to end.

    python backend/tools/learn_train_rn1.py \
        --extract research/beta48/learning/extract_rn1_<stamp>.json \
        --out     research/beta48/learning/run_<stamp>/

WHAT IT DOES, in the order the mandate names:

    1. decode the extract into fills, applying the clock rule
    2. reconstruct entry rows with decision-time features and censoring
    3. split TRAIN / CALIB / EVAL chronologically, one condition to one
       part, and REPORT the overlap drops
    4. fit the baselines and the candidates on TRAIN only
    5. fit the calibrator on CALIB only
    6. score on EVAL only, against the base rate
    7. emit the predictions that would be RECORDED, each with its
       maturity instant, so nothing is scored before its horizon closes

WHAT IT REFUSES. It will not report an EVAL number if EVAL is empty, it
will not calibrate on TRAIN, and it will not score censored rows. Each
refusal is a named status in the output rather than a silent omission.

THE DEV / EVAL DISCLOSURE. Everything before the LAST split boundary
has been inspected while building this pipeline and is DEVELOPMENT
data. Only rows after `--eval-from`, generated after this commit, can
support a prospective claim, and the output labels which is which.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sportsassets.learn import dataset as D       # noqa: E402
from sportsassets.learn import kernel as K        # noqa: E402
from sportsassets.learn import metrics as M       # noqa: E402

SIDE_BUY = 0


def decode(extract: dict) -> list:
    """Extract tuples back into fill dicts. Deltas become absolutes."""
    t0 = float(extract["t0"])
    conds = extract["conditions_list"]
    srcs = extract["sources"]
    out = []
    for row in extract["fills"]:
        ci, oi, is_sell, px, sz, dts, ddet, dven, si = row
        ts = t0 + float(dts)
        out.append({
            "account": extract["account"],
            "condition_id": conds[ci],
            "outcome_index": oi,
            "side": "SELL" if is_sell else "BUY",
            "price": float(px) / 10000.0,
            "size": float(sz) / 100.0,
            "ts": ts,
            "detected_at": ts + float(ddet),
            "venue_seen_at": (ts + float(dven)) if dven is not None else None,
            "source": srcs[si],
        })
    return out


def digest(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--horizon", type=float, default=3600.0)
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

    # THE OBSERVATION END IS THE READ INSTANT, not the last fill. Using
    # the last fill would make the newest rows look decided when their
    # horizons simply had not closed at the time of the read.
    ds = D.build(fills, horizon_s=a.horizon, observation_end=read_at,
                 coverage_exclusions=excl)

    ts_all = sorted(r["decision_at"] for r in ds["rows"])
    if not ts_all:
        print("NO ROWS. skipped=%s" % ds["skipped"])
        return 1
    lo, hi = ts_all[0], ts_all[-1]
    train_end = lo + (hi - lo) * a.train_frac
    calib_end = lo + (hi - lo) * (a.train_frac + a.calib_frac)
    sp = D.split_by_time(ds, train_end=train_end, calib_end=calib_end)

    tr = D.uncensored(sp["parts"]["TRAIN"])
    ca = D.uncensored(sp["parts"]["CALIB"])
    ev = D.uncensored(sp["parts"]["EVAL"])

    report = {
        "generated_at": time.time(),
        "dataset_version": D.VERSION,
        "kernel_version": K.VERSION,
        "metrics_version": M.VERSION,
        "extract": {k: extract[k] for k in
                    ("account", "window_days", "sample", "read_at", "n",
                     "conditions", "by_source")},
        "dataset": {k: ds[k] for k in
                    ("target", "horizon_s", "n_rows", "n_positive",
                     "n_censored", "base_rate_uncensored", "skipped",
                     "coverage_exclusions", "lifecycle_observability",
                     "clock_note", "what_is_not_reconstructed")},
        "split": {k: sp[k] for k in
                  ("counts", "positives", "censored", "conditions",
                   "dropped_for_group_overlap", "rule")},
        "split_boundaries": {"train_end": train_end, "calib_end": calib_end,
                             "first_decision": lo, "last_decision": hi},
        "uncensored_counts": {"TRAIN": len(tr), "CALIB": len(ca),
                              "EVAL": len(ev)},
    }

    if not tr or not ev:
        report["status"] = "INSUFFICIENT_DATA"
        report["why"] = ("TRAIN has %d and EVAL has %d decided rows; a "
                         "model is not reported without both"
                         % (len(tr), len(ev)))
        _write(a.out, report, None)
        print(json.dumps(report["dataset"], indent=1))
        print("STATUS:", report["status"], "-", report["why"])
        return 1

    Xtr, ytr = D.xy(tr)
    Xev, yev = D.xy(ev)
    base_rate = sum(ytr) / len(ytr)

    models = {}
    base = K.BaseRate().fit(Xtr, ytr)
    models["base_rate"] = base

    ridge = K.Ridge(list(D.FEATURES), l2=1.0, max_iter=100).fit(Xtr, ytr)
    models["ridge"] = ridge

    stumps = K.Stumps(list(D.FEATURES), rounds=60, learning_rate=0.1,
                      min_leaf=max(20, len(tr) // 50)).fit(Xtr, ytr)
    models["stumps"] = stumps

    # CALIBRATION ON CALIB ONLY. Fitting it on TRAIN measures memory.
    calib = None
    if ca:
        Xca, yca = D.xy(ca)
        calib = K.Isotonic().fit(ridge.predict_many(Xca), yca)

    scores = {}
    for name, m in models.items():
        p = m.predict_many(Xev)
        scores[name] = M.report(p, yev, baseline_rate=base_rate, label=name)
    if calib is not None:
        pc = [calib.predict(v) for v in ridge.predict_many(Xev)]
        scores["ridge_calibrated"] = M.report(
            pc, yev, baseline_rate=base_rate, label="ridge_calibrated")

    # PER-ACCOUNT / PER-SPORT breakdowns, with a sample floor.
    ev_groups = [r.get("sport") or "unknown" for r in ev]
    by_sport = M.by_group(ridge.predict_many(Xev), yev, ev_groups,
                          baseline_rate=base_rate, min_n=30)

    report["status"] = "TRAINED"
    report["train_base_rate"] = base_rate
    report["eval_observed_rate"] = sum(yev) / len(yev)
    report["scores"] = scores
    report["by_sport"] = by_sport
    report["ridge_weights_raw_scale"] = ridge.weights_on_raw_scale()
    report["stumps_explain"] = stumps.explain()[:10]
    report["calibrated"] = calib is not None
    report["dev_vs_prospective"] = {
        "development_through": calib_end,
        "why": "every row at or before the CALIB boundary was inspected "
               "while this pipeline was built and is DEVELOPMENT data. "
               "The EVAL block below is held-out within this extract but "
               "was generated BEFORE this commit, so it is a held-out "
               "test, NOT a prospective one. A prospective claim needs "
               "predictions recorded before the action, which is what "
               "the prediction ledger is for.",
        "eval_is_prospective": False,
    }

    # THE PREDICTIONS THAT WOULD BE RECORDED. Written out so they can be
    # persisted by the writer without re-deriving anything.
    dsha = digest({"extract": a.extract, "n": ds["n_rows"],
                   "horizon": a.horizon})
    preds = []
    for r in sp["parts"]["EVAL"]:
        f = r["features"]
        p = ridge.predict(f)
        preds.append({
            "subject": r["account"], "condition_id": r["condition_id"],
            "outcome_index": r["outcome_index"],
            "decision_at": r["decision_at"],
            "horizon_s": r["horizon_s"],
            "matures_at": r["decision_at"] + r["horizon_s"],
            "p": p,
            "p_calibrated": calib.predict(p) if calib else None,
            "baseline_p": base_rate,
            "features": f,
            "input_sha": digest(f),
            "retrospective": True,
            "why_retrospective":
                "reconstructed from history; its outcome was already in "
                "the extract when the prediction was computed",
        })
    report["dataset_sha"] = dsha
    report["n_predictions_emitted"] = len(preds)
    _write(a.out, report, preds)

    print(json.dumps({
        "dataset": report["dataset"],
        "split": report["split"],
        "uncensored": report["uncensored_counts"],
        "train_base_rate": base_rate,
        "eval_observed_rate": report["eval_observed_rate"],
        "scores": {k: {"log_loss": v["log_loss"],
                       "baseline_log_loss": v["baseline"]["log_loss"],
                       "skill": v["skill_vs_baseline"].get("skill"),
                       "verdict": v["skill_vs_baseline"].get("verdict"),
                       "auc": v["auc"].get("auc"),
                       "ece": v["calibration"]["ece"]}
                   for k, v in scores.items()},
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
