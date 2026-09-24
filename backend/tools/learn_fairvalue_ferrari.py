"""IS THE PRICE THE PROBABILITY? The one number EV_HOLD needs.

    python backend/tools/learn_fairvalue_ferrari.py \
        --extract research/beta48/learning/fairvalue_ferrari_<stamp>.json \
        --out     research/beta48/learning/fairvalue_<stamp>/

WHY THIS EXISTS. `bettor_exit_engine` states its own central refusal:

    "It CANNOT rank the actions, because ranking needs EV_HOLD -- the
    value of doing nothing -- and that requires an independent fair
    value that does not exist."

EV_HOLD for an unpaired leg is `qty x E[payout]`. The whole question is
what E[payout] is, and the cheapest candidate is the price itself. So
that candidate is the BASELINE here, not the conclusion:

    IDENTITY     E[payout] = price.  The market is fair.
    CALIBRATED   E[payout] = an isotonic map fitted on earlier
                 settlements and applied to later ones.

THE HYPOTHESIS IS PRE-REGISTERED, IN ONE LINE, BEFORE THE RUN:
**a calibration fitted on the past beats the identity out of time.**
If it does not, EV_HOLD is the mark, the residual decision collapses to
execution cost, and that is a finding -- not a reason to keep fitting.

WHY ISOTONIC AND NOT A LOGISTIC. Monotonicity is the only shape anyone
is willing to assert: a leg that costs more should not win less often.
Everything else about the curve is left to the data. And §8 of
FERRARI.md is why it is safe to read at the tails now -- before that
fix the curve returned exactly 0.0 on a block of losers, which as a
fair value means "this leg cannot possibly pay" on the strength of
twenty observations.

TWO WEIGHTINGS, BOTH REPORTED, NEITHER SILENT. Weighting by quantity
answers "where are the dollars mispriced"; unweighted answers "where
are the legs mispriced". They are different questions and a single
unlabelled number would be answering one while appearing to answer the
other.

WHAT IT REFUSES. No EVAL number without a non-empty EVAL. No curve
fitted and scored on the same rows. No AUC or skill quoted without its
clustered interval -- the two legs of one market share one outcome, so
the rows are not independent and the market count is what decides
whether a difference is real.

WHAT IT IS NOT. A PRICE-LEVEL calibration, not a decision-time model:
each row's price is a volume-weighted average across the life of a
condition, so no row is a quote at an instant. And it is fitted on the
prices at which FERRARI traded, which is a subset of the prices that
existed -- the curve describes Ferrari's opportunity set, not the
venue's.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from sportsassets.learn import kernel as K       # noqa: E402
from sportsassets.learn import metrics as M      # noqa: E402

from learn_train_rn1 import digest               # noqa: E402

HYPOTHESIS = (
    "A calibration fitted on earlier settlements beats the IDENTITY "
    "(price = probability) on later ones, measured by log loss with a "
    "clustered interval. Registered before the run.")


def decode(extract: dict) -> list:
    """`[ci, leg, vwap_x10000, qty_x100, fills, payout_x10000, t_first,
    t_last, t_resolved]` -> dicts. Deltas become absolutes."""
    t0 = float(extract["t0"])
    conds = extract["conditions_list"]
    out = []
    for r in extract["rows"]:
        ci, leg, vwap, qty, fills, payout, tf, tl, tr = r
        out.append({
            "condition_id": conds[int(ci)],
            "leg": int(leg),
            "price": float(vwap) / 10000.0,
            "qty": float(qty) / 100.0,
            "fills": int(fills),
            "payout": float(payout) / 10000.0,
            "first_ts": t0 + float(tf),
            "last_ts": t0 + float(tl),
            "resolved_at": t0 + float(tr),
        })
    return out


def _score(p, y, groups, *, label, baseline_rate, weights=None) -> dict:
    r = M.report(p, y, weights=weights, baseline_rate=baseline_rate,
                 label=label)
    r["clustered_skill"] = M.clustered_jackknife(
        p, y, groups, M.skill_stat(baseline_rate))
    return r


def _paired_diff(p_a, p_b, y, groups) -> dict:
    """The comparison that actually decides it: per-row log-loss
    difference between two candidates, clustered by market.

    COMPARING TWO SEPARATE INTERVALS IS NOT A COMPARISON. Two overlapping
    confidence intervals can still come from a difference that is
    consistent in every market. The difference is therefore the
    statistic, and it is jackknifed directly.
    """
    def d(pp, yy):
        # pp arrives as interleaved pairs so a deletion removes both
        # candidates' rows together.
        a = [v[0] for v in pp]
        b = [v[1] for v in pp]
        return M.log_loss(b, yy) - M.log_loss(a, yy)

    pairs = list(zip(p_a, p_b))
    r = M.clustered_jackknife(pairs, y, groups, d)
    r["reads_as"] = (
        "positive means the FIRST candidate has the lower log loss. "
        "The interval is over markets.")
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--train-frac", type=float, default=0.6)
    a = ap.parse_args()

    extract = json.load(open(a.extract))
    rows = decode(extract)
    rows.sort(key=lambda r: (r["first_ts"], r["condition_id"], r["leg"]))

    report = {
        "generated_at": time.time(),
        "hypothesis": HYPOTHESIS,
        "kernel_version": K.VERSION,
        "metrics_version": M.VERSION,
        "extract": {k: extract[k] for k in
                    ("account", "address", "sample", "unit", "read_at",
                     "n", "conditions")},
        "n_rows": len(rows),
        "what_this_is_not":
            "a PRICE-LEVEL calibration, not a decision-time model. Each "
            "price is a volume-weighted average across a condition's "
            "life, so no row is a quote at an instant. It is also fitted "
            "on the prices at which FERRARI traded, which is a selected "
            "subset of the prices that existed.",
    }

    if not rows:
        report["status"] = "NO_ROWS"
        _write(a.out, report)
        return 1

    # A MARKET LANDS ENTIRELY IN ONE PART. Its two legs share one
    # outcome, so splitting between them would put the answer in TRAIN.
    order = []
    seen = set()
    for r in rows:
        if r["condition_id"] not in seen:
            seen.add(r["condition_id"])
            order.append(r["condition_id"])
    cut = int(len(order) * a.train_frac)
    train_conds = set(order[:cut])

    tr = [r for r in rows if r["condition_id"] in train_conds]
    ev = [r for r in rows if r["condition_id"] not in train_conds]
    report["split"] = {
        "rule": "chronological by a market's FIRST fill; a market lands "
                "entirely in one part because its two legs share one "
                "outcome",
        "train_markets": len(train_conds),
        "eval_markets": len(order) - len(train_conds),
        "train_rows": len(tr), "eval_rows": len(ev),
    }
    if not tr or not ev:
        report["status"] = "INSUFFICIENT_DATA"
        _write(a.out, report)
        return 1

    ytr = [r["payout"] for r in tr]
    yev = [r["payout"] for r in ev]
    ptr = [r["price"] for r in tr]
    pev = [r["price"] for r in ev]
    qev = [r["qty"] for r in ev]
    gev = [r["condition_id"] for r in ev]
    base_rate = sum(ytr) / len(ytr)

    # THE FRACTIONAL PAYOUT IS REAL AND IS NOT ROUNDED AWAY. A voided or
    # split market can settle at 0.5 per leg, and forcing it to 0 or 1
    # would invent an outcome.
    report["payout_values"] = {
        "distinct_in_eval": sorted({round(v, 4) for v in yev})[:12],
        "fractional_rows": sum(1 for v in yev if 0.0 < v < 1.0),
        "note": "a voided or split market settles fractionally; those "
                "rows are kept as fractional labels rather than rounded "
                "to an outcome that did not happen",
    }

    curve_ct = K.Isotonic().fit(ptr, ytr)
    curve_qty = K.Isotonic().fit(ptr, ytr, weights=[r["qty"] for r in tr])

    cands = {
        "identity_price_is_probability": pev,
        "isotonic_unweighted": [curve_ct.predict(v) for v in pev],
        "isotonic_qty_weighted": [curve_qty.predict(v) for v in pev],
        "base_rate": [base_rate] * len(pev),
    }

    report["scores"] = {
        name: _score(p, yev, gev, label=name, baseline_rate=base_rate)
        for name, p in cands.items()
    }
    report["scores_qty_weighted"] = {
        name: M.report(p, yev, weights=qev, baseline_rate=base_rate,
                       label=name + " (dollar-weighted)")
        for name, p in cands.items()
    }

    # THE PRE-REGISTERED TEST, and the two variants of it.
    report["hypothesis_test"] = {
        "isotonic_unweighted_vs_identity": _paired_diff(
            cands["isotonic_unweighted"],
            cands["identity_price_is_probability"], yev, gev),
        "isotonic_qty_weighted_vs_identity": _paired_diff(
            cands["isotonic_qty_weighted"],
            cands["identity_price_is_probability"], yev, gev),
    }
    for k, v in report["hypothesis_test"].items():
        lo = v.get("ci95", [None, None])[0]
        v["verdict"] = (
            "SUPPORTED" if lo is not None and lo > 0.0
            else "NOT_SUPPORTED: the interval includes zero, so the "
                 "calibration is not shown to beat the price itself")

    # THE DECISION NUMBER, whatever the verdict. How far is E[payout]
    # from the mark, in cents, where the residual actually sits?
    report["ev_hold_vs_mark"] = _ev_hold_table(ev, curve_qty)

    # AND THE SAME QUESTION ASKED SO THAT ONE MARKET CANNOT ANSWER IT
    # TWICE. A market's two legs are perfectly anti-correlated -- their
    # payouts sum to exactly 1.00 -- so a band table built from all rows
    # sees the SAME event from both sides and reports one phenomenon as
    # two. Each slice below takes at most one row per market.
    report["bias_by_slice"] = _bias_by_slice(ev)

    report["curves"] = {
        "isotonic_unweighted": curve_ct.to_dict(),
        "isotonic_qty_weighted": curve_qty.to_dict(),
    }
    report["train_base_rate"] = base_rate
    report["eval_observed_rate"] = sum(yev) / len(yev)
    report["dataset_sha"] = digest({"extract": os.path.basename(a.extract),
                                    "n": len(rows)})
    report["status"] = "TRAINED"
    _write(a.out, report)

    print(json.dumps({
        "split": report["split"],
        "train_base_rate": base_rate,
        "eval_observed_rate": report["eval_observed_rate"],
        "scores": {k: {"log_loss": v["log_loss"], "brier": v["brier"],
                       "ece": v["calibration"]["ece"]}
                   for k, v in report["scores"].items()},
        "hypothesis_test": {k: {"diff": v.get("statistic"),
                                "ci95": v.get("ci95"),
                                "markets": v.get("n_groups"),
                                "verdict": v.get("verdict")}
                            for k, v in report["hypothesis_test"].items()},
        "ev_hold_vs_mark": report["ev_hold_vs_mark"],
        "bias_by_slice": report["bias_by_slice"],
    }, indent=1))
    return 0


def _ev_hold_table(ev, curve) -> dict:
    """E[payout] against the mark, by price band, with the observed rate.

    This is what a residual decision consumes: at the price this leg is
    marked, is holding worth more or less than the mark, and by how many
    cents. Bands, not a formula, because a band is checkable against its
    own occupancy.
    """
    edges = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    out = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        band = [r for r in ev if lo <= r["price"] < hi
                or (i == len(edges) - 2 and r["price"] == hi)]
        if not band:
            out.append({"lo": lo, "hi": hi, "n": 0, "status": "EMPTY"})
            continue
        qty = sum(r["qty"] for r in band)
        mark = sum(r["price"] * r["qty"] for r in band) / qty if qty else None
        obs = sum(r["payout"] * r["qty"] for r in band) / qty if qty else None
        fit = sum(curve.predict(r["price"]) * r["qty"]
                  for r in band) / qty if qty else None
        out.append({
            "lo": lo, "hi": hi, "n": len(band), "markets":
                len({r["condition_id"] for r in band}),
            "qty": round(qty, 1),
            "mean_mark": round(mark, 4),
            "observed_payout": round(obs, 4),
            "fitted_payout": round(fit, 4),
            "observed_minus_mark_cents": round((obs - mark) * 100.0, 2),
            "status": ("OK" if len({r["condition_id"] for r in band}) >= 30
                       else "THIN_MARKET_COUNT"),
        })
    return {
        "bands": out,
        "weighting": "quantity-weighted within each band, because the "
                     "decision is about dollars at risk",
        "how_to_read":
            "observed_minus_mark_cents is the gross cents per share that "
            "HOLDING earned over the mark, in that band, on the "
            "evaluation markets. Positive means holding beat the price. "
            "It is GROSS: no fees, and no spread that an exit would have "
            "had to cross.",
        "not_a_policy":
            "a band with a positive number is not an instruction to hold "
            "and a negative one is not an instruction to exit. An exit "
            "pays a spread this table does not contain, and whether our "
            "order fills at all is P_FILL, which remains NOT_IDENTIFIED.",
    }


BIAS_BANDS = ((0.05, 0.40), (0.40, 0.60), (0.60, 0.95))


def _bias_by_slice(ev) -> dict:
    """mean(payout - price) per price band, one row per market, clustered.

    THREE SLICES, AND THE FIRST IS THE CLEAN ONE. Markets where Ferrari
    only ever bought ONE leg contribute a single row and are disjoint
    from the two-leg markets entirely, so if the effect appears there
    and again in the larger leg of the paired markets, it is not an
    artifact of counting one event twice.

    The extreme bands are trimmed at 0.05 and 0.95: a two-cent error on
    a one-cent leg is arithmetically enormous and economically nothing,
    and including it would let rounding drive the headline.
    """
    groups = {}
    for r in ev:
        groups.setdefault(r["condition_id"], []).append(r)

    slices = {
        "single_leg_markets": [v[0] for v in groups.values() if len(v) == 1],
        "two_leg_markets_larger_leg": [
            max(v, key=lambda r: r["qty"]) for v in groups.values()
            if len(v) == 2],
        "two_leg_markets_smaller_leg": [
            min(v, key=lambda r: r["qty"]) for v in groups.values()
            if len(v) == 2],
    }

    def mean_diff(pp, _y):
        return sum(v[0] for v in pp) / len(pp)

    out = {}
    for name, sel in slices.items():
        bands = []
        for lo, hi in BIAS_BANDS:
            s = [r for r in sel if lo <= r["price"] < hi]
            if len(s) < 30:
                bands.append({"lo": lo, "hi": hi, "n": len(s),
                              "status": "INSUFFICIENT_SAMPLE"})
                continue
            j = M.clustered_jackknife(
                [(r["payout"] - r["price"],) for r in s], [0.0] * len(s),
                [r["condition_id"] for r in s], mean_diff)
            bands.append({
                "lo": lo, "hi": hi, "n": len(s),
                "mean_payout_minus_price_cents":
                    round(j["statistic"] * 100.0, 2),
                "ci95_cents": [round(j["ci95"][0] * 100.0, 2),
                               round(j["ci95"][1] * 100.0, 2)]
                              if "ci95" in j else None,
                "excludes_zero": bool(
                    "ci95" in j and (j["ci95"][0] > 0 or j["ci95"][1] < 0)),
                "status": j["status"],
            })
        out[name] = {"n_markets": len(sel), "bands": bands}

    out["reading"] = (
        "NEGATIVE means the leg paid LESS than it cost: over-priced. "
        "Cents per share, gross, on the evaluation markets only.")
    out["the_caveat_that_decides_whether_this_is_tradeable"] = (
        "these are the prices at which FERRARI CHOSE TO BUY. A longshot "
        "premium measured on one account's purchases is equally "
        "consistent with 'the venue over-prices longshots' and with "
        "'Ferrari overpays for longshots' -- for instance by crossing "
        "the spread, which makes the recorded price the ask rather than "
        "the mid. Nothing here separates those, and only a "
        "contemporaneous book can. The DIRECTION for our own account is "
        "the same under both readings; the SIZE of the opportunity is "
        "not.")
    return out


def _write(out, report):
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1)
    print("written:", out)


if __name__ == "__main__":
    raise SystemExit(main())
