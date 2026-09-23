"""RESOLVE THE FIVE rn1_complement_1h v1 PREDICTIONS, against real fills.

    python backend/tools/resolve_rn1_v1_outcomes.py \
        --predictions research/beta48/learning/prospective_<stamp>.json \
        --fills       research/beta48/learning/rn1_fills_<stamp>.psv \
        --read-at     <epoch seconds of the render-ops read> \
        --out         research/beta48/learning/outcomes_<stamp>.json

WHAT IT SCORES, AND WHY TWO TARGETS.

v1 recorded `decision_at = entry_at` and claimed the WHOLE following
hour. But the five rows were written at 12:44:16Z, twelve to
twenty-two minutes AFTER their entries, so as an entry-time forecast
they are invalid -- the writer already knew nothing had completed in
that interval. Both targets are therefore scored and reported side by
side:

    UNCONDITIONAL   the window v1 claimed: (entry, entry + 3600]
    CONDITIONAL     the window it actually answers:
                    (entry + elapsed, entry + 3600], among rows that
                    had not already completed by entry + elapsed

`validity` stays INVALID_AS_ENTRY_TIME on all five either way. Scoring
a target does not repair the claim it was recorded under.

THE COMPLEMENT RULE IS THE DATASET'S, NOT A NEW ONE. A complementary
BUY is a BUY of the other outcome of the same condition whose
AVAILABLE_AT = max(ts, detected_at) falls strictly after the decision
and at or before maturity. `learn/dataset.py` is imported for that
function rather than reimplementing it here, because a second rule that
drifts from the first is how a scoring harness starts grading a
different question.

A PASSED DEADLINE IS NOT "NO COMPLEMENT". The caller supplies the
per-lane recency read; if any lane the account trades on is stale at
the read instant, every label is reported PROVISIONAL, because a lane
that stopped delivering is indistinguishable from a quiet book in the
fills alone.

WHAT FIVE OUTCOMES CANNOT DO. They demonstrate that the join runs end
to end. They cannot establish predictive skill, calibration or
profitability, and the report says so in a field rather than leaving it
to the reader.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from sportsassets.learn import dataset as D      # noqa: E402

ELAPSED_S = 1117.0          # v2's conditional offset, frozen
VALIDITY = "INVALID_AS_ENTRY_TIME"

CANNOT_ESTABLISH = (
    "five outcomes demonstrate the join mechanism only. They cannot "
    "establish predictive skill, calibration or profitability, and must "
    "not be described as doing so.")


def read_fills(path: str) -> list:
    """`id|condition_id|outcome_index|side|price|size|source|ts|det|vseen`,
    psql unaligned. A row that cannot be parsed is an ERROR, never a
    skipped row: a silently dropped fill is a manufactured negative."""
    out = []
    for n, line in enumerate(open(path), 1):
        line = line.rstrip("\n")
        if not line or line.startswith("(") or "|" not in line:
            continue
        f = line.split("|")
        if f[0] in ("id",):
            continue
        if len(f) != 10:
            raise ValueError("line %d has %d fields, expected 10: %r"
                             % (n, len(f), line[:120]))
        out.append({
            "id": f[0],
            "condition_id": f[1],
            "outcome_index": int(f[2]),
            "side": f[3].strip().upper(),
            "price": float(f[4]),
            "size": float(f[5]),
            "source": f[6],
            "ts": float(f[7]),
            "detected_at": float(f[8]),
            "venue_seen_at": float(f[9]) if f[9] else None,
        })
    return out


def first_complement(fills, cond, oi, lo, hi):
    """The earliest complementary BUY with available_at in (lo, hi]."""
    best = None
    for f in fills:
        if f["condition_id"] != cond or f["outcome_index"] == oi:
            continue
        if f["side"] != "BUY":
            continue
        a = D.available_at(f)
        if lo < a <= hi and (best is None or a < D.available_at(best)):
            best = f
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--predictions", required=True)
    ap.add_argument("--fills", required=True)
    ap.add_argument("--read-at", type=float, required=True)
    ap.add_argument("--stale-lane", action="append", default=[],
                    help="a lane name that was stale at the read; repeatable")
    ap.add_argument("--exclude", action="append", default=[],
                    help="FROM,TO epoch seconds to test as an ingestion gap")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    book = json.load(open(a.predictions))
    preds = book["predictions"]
    fills = read_fills(a.fills)
    excl = []
    for e in a.exclude:
        lo, hi = e.split(",")
        excl.append((float(lo), float(hi)))

    rows = []
    for p in preds:
        cond, oi = p["condition_id"], int(p["outcome_index"])
        entry = float(p["decision_at"])
        mat = float(p["matures_at"])

        # THE MATURITY MUST HAVE PASSED, AND BY A MARGIN. Ten minutes
        # is the caller's completeness rule; scoring earlier would grade
        # a window that poll arrivals can still change.
        matured_for = a.read_at - mat

        unc = first_complement(fills, cond, oi, entry, mat)
        cond_lo = entry + ELAPSED_S
        # Under the conditional target a row that completed inside the
        # elapsed window LEFT THE RISK SET; it is not a negative.
        left_risk = unc is not None and D.available_at(unc) <= cond_lo
        con = None if left_risk else first_complement(fills, cond, oi,
                                                      cond_lo, mat)

        hit_at = D.available_at(unc) if unc else None
        row = {
            "condition_id": cond, "outcome_index": oi,
            "entry_at": entry, "decision_at": entry, "matures_at": mat,
            "p": p["p"], "baseline_p": p["baseline_p"],
            "input_sha": p.get("input_sha"),
            "matured_for_s": round(matured_for, 1),
            "validity": VALIDITY,
            "unconditional": {
                "window": [entry, mat],
                "outcome": 1 if unc else 0,
                "outcome_at": hit_at,
                "outcome_source": unc["source"] if unc else None,
                "fill_id": unc["id"] if unc else None,
                "time_to_event_s": (hit_at - entry) if hit_at else None,
            },
            "conditional": {
                "elapsed_s": ELAPSED_S,
                "window": [cond_lo, mat],
                "status": ("LEFT_RISK_SET_BEFORE_WINDOW" if left_risk
                           else "IN_RISK_SET"),
                "outcome": (None if left_risk else (1 if con else 0)),
                "outcome_at": (D.available_at(con) if con else None),
                "outcome_source": con["source"] if con else None,
                "fill_id": con["id"] if con else None,
            },
        }

        # SENSITIVITY: would an excluded interval change the label?
        sens = []
        for lo, hi in excl:
            overlaps = not (hi <= entry or lo >= mat)
            hit_in_gap = hit_at is not None and lo <= hit_at <= hi
            sens.append({
                "from": lo, "to": hi,
                "horizon_overlaps_gap": overlaps,
                "the_hit_is_inside_the_gap": hit_in_gap,
                "label_under_EXCLUSION": ("DROPPED" if overlaps else
                                          row["unconditional"]["outcome"]),
                "label_under_INCLUSION": row["unconditional"]["outcome"],
                "changes_the_label": bool(overlaps),
            })
        row["gap_sensitivity"] = sens
        rows.append(row)

    stale = [s for s in a.stale_lane if s]
    provisional = bool(stale) or any(r["matured_for_s"] < 600 for r in rows)
    report = {
        "generated_at": time.time(),
        "read_at": a.read_at,
        "model_key": book["model_key"],
        "model_version": book["model_version"],
        "dataset_version": D.VERSION,
        "n": len(rows),
        "validity_all": VALIDITY,
        "why_validity": (
            "recorded at %.0f, which is after every entry, so as an "
            "entry-time forecast of the whole following hour these are "
            "invalid. Scoring a target does not repair the claim they "
            "were recorded under." % book["recorded_at"]),
        "provisional": provisional,
        "why_provisional": (
            ("lanes stale at the read: %s" % ", ".join(stale)) if stale
            else ("a prediction was scored under ten minutes after "
                  "maturity") if provisional
            else "no lane was stale and every row matured at least ten "
                 "minutes before the read"),
        "fills_read": len(fills),
        "complement_rule":
            "a BUY of the other outcome of the same condition whose "
            "AVAILABLE_AT = max(ts, detected_at) falls in (decision, "
            "maturity]. Imported from learn/dataset.py, not restated.",
        "what_this_cannot_establish": CANNOT_ESTABLISH,
        "rows": rows,
        "summary": {
            "unconditional_positives":
                sum(r["unconditional"]["outcome"] for r in rows),
            "conditional_in_risk_set":
                sum(1 for r in rows
                    if r["conditional"]["status"] == "IN_RISK_SET"),
            "conditional_positives":
                sum(r["conditional"]["outcome"] or 0 for r in rows
                    if r["conditional"]["outcome"] is not None),
        },
    }

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(report, fh, indent=1)
    print(json.dumps({k: report[k] for k in
                      ("n", "fills_read", "provisional", "why_provisional",
                       "validity_all", "summary")}, indent=1))
    for r in rows:
        print("%s oi=%d entry=%.0f  UNC=%d%s  COND=%s(%s)" % (
            r["condition_id"][:10], r["outcome_index"], r["entry_at"],
            r["unconditional"]["outcome"],
            (" via %s" % r["unconditional"]["outcome_source"])
            if r["unconditional"]["outcome"] else "",
            r["conditional"]["outcome"], r["conditional"]["status"]))
    print("written:", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
