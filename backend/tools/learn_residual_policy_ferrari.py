"""COMPONENT 3: what to do with a leg that did not pair.

    python backend/tools/learn_residual_policy_ferrari.py \
        --extract research/beta48/learning/fairvalue_ferrari_<stamp>.json \
        --out     research/beta48/learning/residual_<stamp>/

THE SAME OPPORTUNITIES, THE SAME EXECUTION ASSUMPTIONS, SEVERAL
POLICIES. Every policy below is run over the identical set of
evaluation residuals with the identical spread and fill parameters, so
a difference between two policies is a difference in the policy.

    FERRARI_HOLD_ALL         the reconstructed case study: hold every
                             residual to settlement, sell nothing. This
                             is what Ferrari actually did -- SELL fills
                             in the 60-day window: zero.
    EXIT_ALL                 exit every residual
    EXIT_IF_BELOW_<t>        exit residuals marked under t, hold the
                             rest. Simple baselines, no model.
    EXIT_WHEN_CURVE_SAYS_SO  exit when the TRAIN-fitted calibration
                             says E[payout] is below the achievable
                             proceeds. The learned policy, and the
                             curve is FROZEN on TRAIN markets before
                             any evaluation market is touched.

NO STOP-LOSS IS IMPOSED. Nothing here exits because a position is down
or because Ferrari lost money. A policy exits only where the arithmetic
says the proceeds beat the expected payout, and HOLD is evaluated on
exactly the same footing as every other action.

────────────────────────────────────────────────────────────────────
THE TWO THINGS WE DO NOT KNOW ARE SWEPT, NOT GUESSED.

    SPREAD s    the cents per share given up to leave. We have no
                contemporaneous book for these markets, so s is a
                parameter and every table is reported across a range.
    P_FILL f    whether our exit order fills at all. `bettor_p_fill`
                holds this NOT_IDENTIFIED and this module does not
                quietly identify it.

A leg the policy tries to exit and cannot is simply still held, so

    value = f x (proceeds) + (1 - f) x (payout)

AND THE RANKING DOES NOT DEPEND ON f. Against holding, exiting a leg
gains f x qty x (proceeds - E[payout]). f is positive, so it scales the
benefit and CANNOT flip its sign. **Whether an exit policy beats
holding is decided by the spread alone; only the size of the win
depends on the fill probability.** That is why f is swept for magnitude
and never needed for the verdict.

FEES ARE REAL AND ARE APPLIED. `bettor_fee_schedule.LATEST` -- the
published PMUS schedule, theta x contracts x price x (1 - price) -- is
charged on every simulated exit as a TAKER fill, which is the
conservative side: a resting exit would earn the maker rebate instead,
and assuming the better of the two would be assuming the fill we just
said we cannot assume. Settlement itself is not charged.

WHAT IS STILL COUNTERFACTUAL. Every exit here is a trade that did not
happen. Its price is a parameter, its fill is a parameter, and no
number in this file is a realised cash flow. The realised cash flows
are Ferrari's, and they appear as FERRARI_HOLD_ALL.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from decimal import Decimal

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from sportsassets import bettor_fee_schedule as FEES   # noqa: E402
from sportsassets.learn import kernel as K             # noqa: E402
from sportsassets.learn import metrics as M            # noqa: E402

from learn_fairvalue_ferrari import decode             # noqa: E402
from learn_train_rn1 import digest                     # noqa: E402

SPREADS = (0.0, 0.005, 0.01, 0.02, 0.05)
FILLS = (0.25, 0.5, 1.0)
THRESHOLDS = (0.30, 0.40, 0.50)


def residuals(rows) -> list:
    """One residual per market: the unpaired quantity and its leg.

    A market where both legs were bought contributes |q0 - q1| on the
    heavier leg. A market where only one leg was ever bought contributes
    all of it. This is the same construction the SQL decomposition used,
    so the two are comparable rather than merely similar.
    """
    by = {}
    for r in rows:
        by.setdefault(r["condition_id"], []).append(r)
    out = []
    for cond, legs in by.items():
        if len(legs) == 1:
            r = legs[0]
            out.append({**r, "resid_qty": r["qty"], "paired_qty": 0.0,
                        "kind": "NEVER_PAIRED"})
        elif len(legs) == 2:
            a, b = sorted(legs, key=lambda r: -r["qty"])
            q = a["qty"] - b["qty"]
            if q <= 0.0:
                continue                      # fully paired: no residual
            out.append({**a, "resid_qty": q, "paired_qty": b["qty"],
                        "kind": "PARTLY_PAIRED"})
    return out


def _fee(qty, price) -> float:
    return float(FEES.LATEST.taker_fee(Decimal(str(qty)), Decimal(str(price))))


def value_of(r, exit_now, s, f) -> float:
    """Gross dollars from one residual under one policy decision."""
    hold = r["resid_qty"] * r["payout"]
    if not exit_now:
        return hold
    px = max(0.0, r["price"] - s)
    proceeds = r["resid_qty"] * px - _fee(r["resid_qty"], px)
    return f * proceeds + (1.0 - f) * hold


def policies(curve, s):
    """name -> predicate(residual) -> exit?  Built per spread, because
    the learned rule compares against the achievable proceeds."""
    out = {
        "FERRARI_HOLD_ALL": lambda r: False,
        "EXIT_ALL": lambda r: True,
    }
    for t in THRESHOLDS:
        out["EXIT_IF_BELOW_%.2f" % t] = (lambda t: lambda r: r["price"] < t)(t)
    # THE LEARNED RULE. Exit when the frozen curve says the expected
    # payout is below what leaving would net, per share, fee included.
    def learned(r):
        px = max(0.0, r["price"] - s)
        net_per_share = px - _fee(1.0, px)
        return curve.predict(r["price"]) < net_per_share
    out["EXIT_WHEN_CURVE_SAYS_SO"] = learned
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--train-frac", type=float, default=0.6)
    a = ap.parse_args()

    extract = json.load(open(a.extract))
    rows = decode(extract)
    rows.sort(key=lambda r: (r["first_ts"], r["condition_id"], r["leg"]))

    order, seen = [], set()
    for r in rows:
        if r["condition_id"] not in seen:
            seen.add(r["condition_id"])
            order.append(r["condition_id"])
    cut = int(len(order) * a.train_frac)
    train_conds = set(order[:cut])

    tr = [r for r in rows if r["condition_id"] in train_conds]
    ev_rows = [r for r in rows if r["condition_id"] not in train_conds]

    # FROZEN BEFORE ANY EVALUATION MARKET IS TOUCHED.
    curve = K.Isotonic().fit([r["price"] for r in tr],
                             [r["payout"] for r in tr])
    curve_sha = digest(curve.to_dict())

    ev = residuals(ev_rows)
    report = {
        "generated_at": time.time(),
        "kernel_version": K.VERSION,
        "metrics_version": M.VERSION,
        "fee_schedule": FEES.LATEST.describe(),
        "extract": {k: extract[k] for k in ("account", "address", "sample",
                                            "read_at", "n", "conditions")},
        "frozen_curve_sha": curve_sha,
        "frozen_before": "any evaluation market was scored",
        "eval_residuals": len(ev),
        "eval_markets": len({r["condition_id"] for r in ev}),
        "by_kind": {k: sum(1 for r in ev if r["kind"] == k)
                    for k in ("NEVER_PAIRED", "PARTLY_PAIRED")},
        "residual_cost_gross": round(
            sum(r["resid_qty"] * r["price"] for r in ev), 2),
        "residual_payout_realised": round(
            sum(r["resid_qty"] * r["payout"] for r in ev), 2),
        "spreads_swept": list(SPREADS),
        "fills_swept": list(FILLS),
        "why_swept": "we have no contemporaneous book for these markets "
                     "and P_FILL is NOT_IDENTIFIED. Both are parameters "
                     "rather than assumptions, and the verdict is read "
                     "off the spread alone because a positive fill "
                     "probability scales a difference and cannot flip "
                     "its sign.",
        "no_stop_loss":
            "no policy here exits because a position is down or because "
            "Ferrari lost money. HOLD is evaluated on the same footing "
            "as every other action.",
        "counterfactual_warning":
            "every exit is a trade that did not happen, at a parameter "
            "price with a parameter fill. The only realised cash flows "
            "in this table are FERRARI_HOLD_ALL's.",
    }

    if not ev:
        report["status"] = "NO_RESIDUALS"
        _write(a.out, report)
        return 1

    base_hold = {r["condition_id"]: 0.0 for r in ev}
    for r in ev:
        base_hold[r["condition_id"]] += r["resid_qty"] * r["payout"]

    grid = {}
    for s in SPREADS:
        pol = policies(curve, s)
        for f in FILLS:
            cell = {}
            for name, rule in pol.items():
                per_market = {}
                exits = 0
                for r in ev:
                    e = bool(rule(r))
                    exits += 1 if e else 0
                    per_market[r["condition_id"]] = (
                        per_market.get(r["condition_id"], 0.0)
                        + value_of(r, e, s, f))
                total = sum(per_market.values())
                keys = sorted(per_market)
                diff = [(per_market[k] - base_hold[k],) for k in keys]
                j = M.clustered_jackknife(
                    diff, [0.0] * len(keys), keys,
                    lambda pp, _y: sum(v[0] for v in pp) / len(pp))
                cell[name] = {
                    "total_value": round(total, 2),
                    "exits": exits,
                    "exit_share": round(exits / float(len(ev)), 4),
                    "vs_ferrari_hold_all": round(total - sum(
                        base_hold.values()), 2),
                    "mean_per_market_diff": round(j["statistic"], 4),
                    "ci95_per_market": [round(v, 4) for v in j["ci95"]]
                                       if "ci95" in j else None,
                    "beats_holding":
                        bool("ci95" in j and j["ci95"][0] > 0.0),
                }
            grid["s=%.3f,f=%.2f" % (s, f)] = cell

    report["grid"] = grid
    report["status"] = "EVALUATED"

    # THE BREAK-EVEN SPREAD, read straight off the sweep at f = 1.
    report["break_even_spread"] = _break_even(grid)
    _write(a.out, report)

    print(json.dumps({
        "eval_residuals": report["eval_residuals"],
        "eval_markets": report["eval_markets"],
        "by_kind": report["by_kind"],
        "residual_cost_gross": report["residual_cost_gross"],
        "residual_payout_realised": report["residual_payout_realised"],
        "break_even_spread": report["break_even_spread"],
        "grid_f1": {k.split(",")[0]: {n: {"vs_hold": v["vs_ferrari_hold_all"],
                                          "exit_share": v["exit_share"],
                                          "beats": v["beats_holding"]}
                                      for n, v in c.items()}
                    for k, c in grid.items() if k.endswith("f=1.00")},
    }, indent=1))
    return 0


def _break_even(grid) -> dict:
    """The largest swept spread at which each policy still beats holding.

    READ AT f = 1 BECAUSE f CANNOT CHANGE THE SIGN. At a lower fill
    probability the same policy wins less, not differently.
    """
    out = {}
    for key, cell in grid.items():
        if not key.endswith("f=1.00"):
            continue
        s = float(key.split(",")[0].split("=")[1])
        for name, v in cell.items():
            if name == "FERRARI_HOLD_ALL":
                continue
            if v["beats_holding"]:
                out[name] = max(out.get(name, -1.0), s)
    return {
        "largest_spread_still_beating_hold": out,
        "units": "dollars per share",
        "absent_means":
            "a policy missing from this list did not beat holding at "
            "ANY swept spread, including zero",
        "how_to_use":
            "this is the spread budget an exit has to come in under. "
            "Whether our order fills inside that budget is P_FILL, "
            "which is still NOT_IDENTIFIED, and is what a funded pilot "
            "would measure.",
    }


def _write(out, report):
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1)
    print("written:", out)


if __name__ == "__main__":
    raise SystemExit(main())
