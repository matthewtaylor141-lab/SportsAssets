"""THE POLICY-IMPROVEMENT LOOP. Bounded experiments, one gate, no tuning
against the evaluation set.

    python backend/tools/desk_experiments.py \
        --extract   research/beta48/learning/extract_ferrari_<stamp>.json \
        --fairvalue research/beta48/learning/fairvalue_ferrari_<stamp>.json \
        --out       research/beta48/learning/experiments_<stamp>/

────────────────────────────────────────────────────────────────────
WHERE THE FINAL EVALUATION SET IS, AND WHY IT IS NOT IN HISTORY.

Every row of the replay corpus has been inspected -- I built the loss
decomposition from it, and the candidates below are proposed FROM that
decomposition. A "held-out" slice carved from data I have already read
is not uninspected, and treating it as if it were is the single
easiest way to manufacture a positive result.

So the partitions are:

    TRAIN        earliest 60% of the window. DEVELOPMENT. Inspected.
                 Candidates are proposed against it.
    VALIDATION   next 20%. Used ONCE, to select. Inspected only through
                 the selection statistic.
    FINAL        NOT A SLICE OF HISTORY. The live shadow lane's
                 prospective decisions from the freeze instant onward.
                 That is the only genuinely uninspected data that
                 exists, and it accrues forward rather than being
                 carved out.

The selected candidate is FROZEN here and evaluated there. Nothing in
this file ever reads FINAL, because FINAL has not happened yet.

────────────────────────────────────────────────────────────────────
A CANDIDATE CANNOT WIN ON AN OPTIMISTIC FILL.

`P_FILL` is NOT_IDENTIFIED, so every comparison runs across a declared
scenario grid of `queue_share`. A candidate qualifies only if it beats
the baseline under EVERY scenario. One that wins at 0.50 and loses at
0.10 has found a liquidity assumption, not an edge, and the gate
rejects it by construction.

────────────────────────────────────────────────────────────────────
PROFIT IS NOT THE OBJECTIVE FUNCTION.

Realised P&L alone would promote a policy that parks capital in
unresolved inventory and calls the absence of a loss a gain. So every
candidate reports, and the gate consults:

    terminal value BOUNDS   unresolved legs pay somewhere in [0, 1] per
                            share, so the book's terminal value has a
                            hard lower and upper bound. The LOWER bound
                            is what the gate uses -- a candidate must
                            survive its own worst case.
    max drawdown            on the realised equity path
    committed capital       peak, and time-averaged
    turnover                notional traded per dollar of starting cash
    activity                orders, fills, and NO_TRADE counts

AND INACTIVITY IS REPORTED, NEVER REWARDED. A policy that trades
nothing has no drawdown and no loss; the gate requires a minimum
number of decided opportunities before a candidate is eligible at all,
so "did nothing" cannot pass as "did well".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from decimal import Decimal

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from sportsassets import bettor_desk as DK                  # noqa: E402
from sportsassets import bettor_fee_schedule as FEES        # noqa: E402
from sportsassets.learn import kernel as K                  # noqa: E402

from learn_train_rn1 import decode as decode_fills          # noqa: E402
from learn_fairvalue_ferrari import decode as decode_fv     # noqa: E402

SCENARIOS = (0.10, 0.25, 0.50)          # queue_share, declared
TRAIN_FRAC, VALID_FRAC = 0.60, 0.20
MIN_DECIDED = 50                        # eligibility floor: no inactivity wins
MIN_TURNOVER_FRAC = 0.50                # V2: a candidate may not win by shrinking


def code_version() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True,
                              cwd=os.path.dirname(_HERE)).stdout.strip()
    except Exception:                                       # noqa: BLE001
        return "UNKNOWN"


def fee_fn(qty, price, maker):
    return float(FEES.LATEST.fill_fee(Decimal(str(round(qty, 6))),
                                      Decimal(str(round(price, 6))),
                                      maker=bool(maker)))


# ── THE CANDIDATES, each tied to a line of the loss decomposition ────
#
# The measured attribution was: settlement -$1,358.88, exit-vs-basis
# -$921.16, fees +$381.75 (income), and $24,530 stranded in 396 legs.
# Every hypothesis below names which of those it targets. A change that
# targets nothing measured is not an experiment, it is a guess.

CANDIDATES = [
    {"id": "BASELINE", "hypothesis": "the frozen active policy",
     "targets": "-", "params": {}},

    {"id": "C1_NARROW_BAND",
     "hypothesis": "entries in [0.40,0.65] sit where the fair-value study "
                   "found mean(payout-price) indistinguishable from zero. "
                   "Narrowing toward the centre should not help; if it "
                   "does, the band was not the binding constraint.",
     "targets": "SETTLEMENT", "params": {"entry_lo": 0.45, "entry_hi": 0.55}},

    {"id": "C2_REQUIRE_CLEARANCE",
     "hypothesis": "exit-vs-basis lost $921. Requiring a wider clearing "
                   "margin before completing a pair should cut the "
                   "completions that lock a loss.",
     "targets": "EXIT_VS_BASIS", "params": {"min_clear": 0.03}},

    {"id": "C3_PATIENT_EXIT",
     "hypothesis": "the learned exit fires on a 2c edge. A wider "
                   "threshold holds more legs to settlement, trading "
                   "exit losses for settlement variance.",
     "targets": "EXIT_VS_BASIS", "params": {"exit_edge": 0.06}},

    {"id": "C4_SMALLER_CLIP",
     "hypothesis": "$24,530 stranded in 396 legs is a capital problem "
                   "before it is a P&L one. A smaller clip should cut "
                   "committed capital and turnover at the same win rate.",
     "targets": "STRANDED_CAPITAL", "params": {"order_usd": 120.0}},

    {"id": "C5_SHORTER_REST",
     "hypothesis": "782 orders expired unfilled. A shorter expiry frees "
                   "the intent sooner; if outcomes are unchanged the "
                   "resting time was not buying anything.",
     "targets": "TURNOVER", "params": {"expiry_s": 300.0}},
]


def build_tape(ex):
    fills = decode_fills(ex)
    return sorted(
        ({"kind": "PRINT", "at": f["ts"], "condition_id": f["condition_id"],
          "outcome_index": f["outcome_index"], "price": f["price"],
          "size": f["size"],
          "evidence_id": "%s:%s:%s" % (f["condition_id"],
                                       f["outcome_index"], f["ts"])}
         for f in fills if f["side"] == "BUY"),
        key=lambda e: (e["at"], e["condition_id"], e["outcome_index"]))


def split(tape):
    """CHRONOLOGICAL, and a CONDITION LANDS ENTIRELY IN ONE PART.

    A market's events are one story. Splitting between them would put
    the outcome of a position in one partition and its entry in
    another, which is the overlapping-outcome leak this has to handle
    explicitly rather than hope about.
    """
    first_seen = {}
    for e in tape:
        first_seen.setdefault(e["condition_id"], e["at"])
    order = sorted(first_seen, key=lambda c: first_seen[c])
    n = len(order)
    tr = set(order[:int(n * TRAIN_FRAC)])
    va = set(order[int(n * TRAIN_FRAC):int(n * (TRAIN_FRAC + VALID_FRAC))])
    parts = {"TRAIN": [], "VALIDATION": [], "HELD_BACK_NOT_USED": []}
    for e in tape:
        c = e["condition_id"]
        parts["TRAIN" if c in tr else
              "VALIDATION" if c in va else "HELD_BACK_NOT_USED"].append(e)
    return parts, {"train_conditions": len(tr), "validation_conditions": len(va),
                   "held_back_conditions": n - len(tr) - len(va)}


def run_one(tape, payouts, params, queue_share, curve, curve_sha, cash):
    """One policy, one scenario, one opportunity set."""
    p = DK.Policy(curve=curve, curve_sha=curve_sha)
    for k, v in params.items():
        # A BARE setattr IS A CHECK THAT CANNOT FAIL. A mistyped knob
        # would attach a dead attribute, the policy would run UNCHANGED,
        # and the harness would report "no difference from baseline" --
        # a null result manufactured by a typo. Refuse instead.
        if not hasattr(p, k):
            raise SystemExit("CANDIDATE_PARAM_NOT_A_POLICY_KNOB: %r. The "
                             "policy exposes %s." % (k, sorted(vars(p))))
        setattr(p, k, float(v))
    desk = DK.Desk(policy=p, limits=DK.Limits(starting_cash=cash),
                   fee_fn=fee_fn, queue_share=queue_share, desk_id="exp")

    equity, peak_committed, committed_sum, steps = [], 0.0, 0.0, 0
    for i, e in enumerate(tape):
        desk.step(e)
        if i % 200 == 0:
            c = desk.committed_usd()
            peak_committed = max(peak_committed, c)
            committed_sum += c
            steps += 1
            equity.append(desk.pf.cash + desk.pf.inventory_cost())

    last_at = tape[-1]["at"] if tape else 0.0
    unresolved_qty, unresolved_cost = 0.0, 0.0
    for (cond, oi), leg in list(desk.pf.open_legs()):
        pay = payouts.get((cond, oi))
        if pay is None:
            unresolved_qty += leg["qty"]
            unresolved_cost += leg["cost"]
        else:
            desk.settle(cond, oi, float(pay), last_at + 1.0)

    # TERMINAL VALUE BOUNDS. An unresolved binary leg pays somewhere in
    # [0, 1] per share, so the book's end value is bounded, not unknown.
    lower = desk.pf.cash
    upper = desk.pf.cash + unresolved_qty
    turnover = sum(f["qty"] * f["price"]
                   for o in desk.orders.values() for f in o.fills)
    dd = 0.0
    hi = equity[0] if equity else cash
    for v in equity:
        hi = max(hi, v)
        dd = max(dd, hi - v)

    decided = sum(1 for o in desk.orders.values()
                  if o.state in DK.TERMINAL_STATES)
    acts = {}
    for d in desk.decisions:
        acts[d["action"]] = acts.get(d["action"], 0) + 1

    return {
        "realized_pnl_usd": round(desk.pf.realized, 2),
        "fees_usd": round(desk.pf.fees, 2),
        "terminal_lower_usd": round(lower, 2),
        "terminal_upper_usd": round(upper, 2),
        "terminal_lower_vs_start": round(lower - cash, 2),
        "unresolved_legs": sum(1 for _ in desk.pf.open_legs()),
        "unresolved_cost_usd": round(unresolved_cost, 2),
        "max_drawdown_usd": round(dd, 2),
        "peak_committed_usd": round(peak_committed, 2),
        "mean_committed_usd": round(committed_sum / steps, 2) if steps else 0.0,
        "turnover_usd": round(turnover, 2),
        "turnover_x_capital": round(turnover / cash, 3),
        "orders": len(desk.orders),
        "decided_orders": decided,
        "fills": sum(len(o.fills) for o in desk.orders.values()),
        "decisions": acts,
        "invariant_ok": desk.pf.invariant()["ok"],
    }


def gate_v2(base, cand, n_decided) -> dict:
    """GATE V2, declared in cycle 2's commit BEFORE cycle 2 was run, and
    earned by four things cycle 1 measured about GATE V1 itself.

    V1 ranked on the ZERO-MARKED LOWER TERMINAL BOUND. Cycle 1 showed
    that statistic cannot do the job:

      (a) NO RESOLVING POWER. lower = realized - unresolved_cost, and on
          this corpus the bound spans ~$34k on $100k of cash while the
          realized figures it is meant to separate are +-$1k. The
          instrument is 9-34x wider than the effect.
      (b) IT IS A DEGENERATE OBJECTIVE. Minimising it means holding no
          inventory, so its optimum is "trade nothing". MIN_DECIDED=50
          was supposed to stop that and did not bind -- every candidate
          decided 300-1800 orders. Both of V1's TRAIN accepts, C1 and
          C4, were DOSE REDUCTIONS: narrower band, smaller clip. Less
          of a losing strategy is not an edge.
      (c) IT INVERTED THE SIGN. V1 called baseline VALIDATION "worse" at
          queue_share 0.50 (lower -4435 vs -3315) while its realized
          P&L ROSE (+974 vs +41). Marking open inventory at zero made
          more trading look worse when more trading had earned more.

    So V2 ranks on the COST-MARKED terminal -- which is exactly
    `realized`, since cash + inventory_cost - realized == starting_cash
    -- and keeps the bounds as a REQUIRED DISCLOSURE rather than the
    selector. Then it closes the three holes that opens:

      G1 ACTIVITY AND SIZE. An order-count floor alone lets a candidate
         win by shrinking. Turnover must also stay within 50% of the
         baseline's, so a dose reduction has to beat the baseline at
         comparable size or not at all.
      G3 INVENTORY IS NOT A HIDING PLACE. Cost-marked P&L can be
         flattered by parking capital in unresolved legs, so
         unresolved_cost may not exceed the baseline's by >10%.
      G4 THE MARGIN MUST EXCEED THE UNCERTAINTY. If the improvement in
         realized is smaller than the change in bound WIDTH, the result
         is NOT_RESOLVED, not accepted.
      G7 SIGN STABILITY ACROSS PARTITIONS. Cycle 1's sharpest finding
         was that d(realized)/d(queue_share) is NEGATIVE on TRAIN and
         POSITIVE on VALIDATION for the same policy. A candidate whose
         response to the one execution assumption we cannot identify
         flips sign between partitions has not shown an edge, and the
         caller supplies both partitions so this can be tested.

    V2 IS NOT APPLIED RETROACTIVELY TO CYCLE 1. Cycle 1's verdicts stand
    as V1 produced them. Rescoring a finished cycle under a gate written
    after seeing its results is the tuning this loop exists to prevent.
    """
    reasons, ok = [], True
    if n_decided < MIN_DECIDED:
        ok = False
        reasons.append("INELIGIBLE: %d decided orders < %d floor -- "
                       "inactivity is not performance"
                       % (n_decided, MIN_DECIDED))
    for b, c in zip(base, cand):
        if c["turnover_usd"] < b["turnover_usd"] * MIN_TURNOVER_FRAC:
            ok = False
            reasons.append(
                "DOSE REDUCTION, NOT AN EDGE: turnover %.0f is %.0f%% of "
                "the baseline's %.0f. Trading less of a losing strategy "
                "loses less; that is arithmetic, not improvement."
                % (c["turnover_usd"],
                   100.0 * c["turnover_usd"] / max(b["turnover_usd"], 1e-9),
                   b["turnover_usd"]))
            break
    wins = [c["realized_pnl_usd"] > b["realized_pnl_usd"]
            for b, c in zip(base, cand)]
    if not all(wins):
        ok = False
        reasons.append("NOT ROBUST: improves cost-marked P&L in %d of %d "
                       "scenarios; a candidate that needs a particular "
                       "fill assumption has found a liquidity assumption, "
                       "not an edge" % (sum(wins), len(wins)))
    for b, c in zip(base, cand):
        if c["unresolved_cost_usd"] > b["unresolved_cost_usd"] * 1.10 + 1.0:
            ok = False
            reasons.append("CAPITAL PARKED, NOT EARNED: unresolved cost "
                           "%.0f vs %.0f. Cost-marked P&L can be flattered "
                           "by holding rather than closing."
                           % (c["unresolved_cost_usd"],
                              b["unresolved_cost_usd"]))
            break
    for b, c in zip(base, cand):
        margin = c["realized_pnl_usd"] - b["realized_pnl_usd"]
        dwidth = abs((c["terminal_upper_usd"] - c["terminal_lower_usd"])
                     - (b["terminal_upper_usd"] - b["terminal_lower_usd"]))
        if margin <= dwidth:
            ok = False
            reasons.append("NOT_RESOLVED: the %.0f improvement is smaller "
                           "than the %.0f change in the terminal-value "
                           "bound width. The instrument cannot see an "
                           "effect this size." % (margin, dwidth))
            break
    for b, c in zip(base, cand):
        if c["max_drawdown_usd"] > b["max_drawdown_usd"] * 1.10 + 1.0:
            ok = False
            reasons.append("WORSE DRAWDOWN: %.2f vs %.2f"
                           % (c["max_drawdown_usd"], b["max_drawdown_usd"]))
            break
    for b, c in zip(base, cand):
        if c["peak_committed_usd"] > b["peak_committed_usd"] * 1.10 + 1.0:
            ok = False
            reasons.append("MORE CAPITAL AT RISK: %.2f vs %.2f"
                           % (c["peak_committed_usd"],
                              b["peak_committed_usd"]))
            break
    if not all(c["invariant_ok"] for c in cand):
        ok = False
        reasons.append("LEDGER DOES NOT RECONCILE")
    return {"accepted": ok,
            "reasons": reasons or ["passed every declared V2 condition"]}


def slope_sign(rows) -> int:
    """sign of d(realized)/d(queue_share) across the scenario grid."""
    d = rows[-1]["realized_pnl_usd"] - rows[0]["realized_pnl_usd"]
    return 0 if abs(d) < 1e-9 else (1 if d > 0 else -1)


def sign_stability(train_rows, valid_rows) -> dict:
    """G7. The same policy must respond to queue_share the same way in
    both partitions, or its response is partition noise.

    This is the one criterion cycle 1 could not have had, because it
    tests a property cycle 1 is what discovered.
    """
    st, sv = slope_sign(train_rows), slope_sign(valid_rows)
    return {
        "train_slope_sign": st, "validation_slope_sign": sv,
        "stable": st == sv,
        "detail": ("d(realized)/d(queue_share) is %s on TRAIN and %s on "
                   "VALIDATION" % ({1: "POSITIVE", -1: "NEGATIVE",
                                    0: "FLAT"}[st],
                                   {1: "POSITIVE", -1: "NEGATIVE",
                                    0: "FLAT"}[sv])),
    }


def gate(base, cand, n_decided) -> dict:
    """THE ACCEPTANCE GATE, declared before any candidate was run.

    Four conditions, ALL required:
      1. eligible      at least MIN_DECIDED decided orders, so a policy
                       that traded nothing cannot pass
      2. robust        beats the baseline's LOWER terminal bound under
                       EVERY declared scenario, not on average
      3. no worse risk drawdown and peak committed capital not worse
                       than the baseline by more than 10%
      4. reconciles    the ledger identity holds in every scenario
    """
    reasons, ok = [], True
    if n_decided < MIN_DECIDED:
        ok = False
        reasons.append("INELIGIBLE: %d decided orders < %d floor -- "
                       "inactivity is not performance"
                       % (n_decided, MIN_DECIDED))
    wins = [c["terminal_lower_vs_start"] > b["terminal_lower_vs_start"]
            for b, c in zip(base, cand)]
    if not all(wins):
        ok = False
        reasons.append("NOT ROBUST: improves the lower terminal bound in "
                       "%d of %d scenarios; a candidate that needs a "
                       "particular fill assumption has found a liquidity "
                       "assumption, not an edge" % (sum(wins), len(wins)))
    for b, c in zip(base, cand):
        if c["max_drawdown_usd"] > b["max_drawdown_usd"] * 1.10 + 1.0:
            ok = False
            reasons.append("WORSE DRAWDOWN: %.2f vs %.2f"
                           % (c["max_drawdown_usd"], b["max_drawdown_usd"]))
            break
    for b, c in zip(base, cand):
        if c["peak_committed_usd"] > b["peak_committed_usd"] * 1.10 + 1.0:
            ok = False
            reasons.append("MORE CAPITAL AT RISK: %.2f vs %.2f"
                           % (c["peak_committed_usd"],
                              b["peak_committed_usd"]))
            break
    if not all(c["invariant_ok"] for c in cand):
        ok = False
        reasons.append("LEDGER DOES NOT RECONCILE")
    return {"accepted": ok,
            "reasons": reasons or ["passed every declared condition"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", required=True)
    ap.add_argument("--fairvalue", required=True)
    ap.add_argument("--curve", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cash", type=float, default=100000.0)
    ap.add_argument("--gate", choices=("v1", "v2"), default="v1")
    ap.add_argument("--cycle", type=int, default=1)
    a = ap.parse_args()

    ex = json.load(open(a.extract))
    fv = json.load(open(a.fairvalue))
    payouts = {(r["condition_id"], r["leg"]): r["payout"]
               for r in decode_fv(fv)}

    curve, curve_sha = None, DK.NOT_IDENTIFIED
    if a.curve and os.path.exists(a.curve):
        art = json.load(open(a.curve))
        curve = K.Isotonic.from_dict(art)
        curve_sha = DK._sha(art)

    tape = build_tape(ex)
    parts, counts = split(tape)

    results, t0 = {}, time.time()
    for part in ("TRAIN", "VALIDATION"):
        results[part] = {}
        for cand in CANDIDATES:
            per = []
            for qs in SCENARIOS:
                per.append(run_one(parts[part], payouts, cand["params"],
                                   qs, curve, curve_sha, a.cash))
            results[part][cand["id"]] = per

    scorer = gate_v2 if a.gate == "v2" else gate
    verdicts = {}
    for part in ("TRAIN", "VALIDATION"):
        base = results[part]["BASELINE"]
        verdicts[part] = {}
        for cand in CANDIDATES:
            if cand["id"] == "BASELINE":
                continue
            c = results[part][cand["id"]]
            verdicts[part][cand["id"]] = scorer(
                base, c, min(x["decided_orders"] for x in c))

    # G7 is cross-partition, so it cannot live inside a per-partition
    # gate. It is applied here, to V2 only, and it applies to the
    # BASELINE too -- if the frozen policy itself is unstable that is
    # the finding, and it must not be hidden by only testing challengers.
    stability = {c["id"]: sign_stability(results["TRAIN"][c["id"]],
                                         results["VALIDATION"][c["id"]])
                 for c in CANDIDATES}

    accepted = [c["id"] for c in CANDIDATES if c["id"] != "BASELINE"
                and verdicts["TRAIN"].get(c["id"], {}).get("accepted")
                and verdicts["VALIDATION"].get(c["id"], {}).get("accepted")
                and (a.gate != "v2" or stability[c["id"]]["stable"])]
    for c in CANDIDATES:
        if (a.gate == "v2" and c["id"] != "BASELINE"
                and not stability[c["id"]]["stable"]):
            for part in ("TRAIN", "VALIDATION"):
                verdicts[part][c["id"]]["reasons"].append(
                    "G7 SIGN INSTABILITY: %s" % stability[c["id"]]["detail"])
                verdicts[part][c["id"]]["accepted"] = False

    report = {
        "generated_at": time.time(),
        "elapsed_s": round(time.time() - t0, 1),
        "code_version": code_version(),
        "desk_version": DK.VERSION,
        "variants_tried": len(CANDIDATES) - 1,
        "scenarios_queue_share": list(SCENARIOS),
        "partitions": counts,
        "partition_rule": (
            "chronological by a market's first event; a CONDITION lands "
            "entirely in one part, so a position's entry and its outcome "
            "are never split across partitions"),
        "FINAL_EVALUATION_SET": {
            "where": "the LIVE shadow lane, forward of the freeze instant",
            "why_not_a_history_slice": (
                "every row of this corpus has been inspected -- the loss "
                "decomposition was built from it and these candidates "
                "were proposed from that decomposition. A slice carved "
                "out of data I have already read is not uninspected, and "
                "treating it as if it were is the easiest way to "
                "manufacture a positive result. The held-back 20% is "
                "NOT USED here and is not a substitute."),
        },
        "execution_assumptions": {
            "event_class": DK.EVENT_CLASS,
            "source_venue": DK.SOURCE_VENUE,
            "fee_schedule_venue": DK.FEE_SCHEDULE_VENUE,
            "venue_basis": DK.VENUE_TRANSFER,
            "p_fill": DK.NOT_IDENTIFIED,
            "note": "a candidate must beat the baseline under EVERY "
                    "queue_share scenario. Winning at 0.50 and losing at "
                    "0.10 is a liquidity assumption, not an edge.",
        },
        "cycle": a.cycle,
        "gate": {
            "id": a.gate.upper(),
            "declared_before_this_cycle_ran": True,
            "min_decided_orders": MIN_DECIDED,
            "criteria": (
                ["eligible (activity floor)",
                 "improves the LOWER terminal bound in EVERY scenario",
                 "drawdown not worse by >10%",
                 "peak committed capital not worse by >10%",
                 "ledger reconciles in every scenario"]
                if a.gate == "v1" else
                ["G1 eligible: activity floor AND turnover >= %.0f%% of "
                 "baseline, so a dose reduction cannot win by shrinking"
                 % (100 * MIN_TURNOVER_FRAC),
                 "G2 improves COST-MARKED P&L (= realized) in EVERY "
                 "scenario",
                 "G3 unresolved cost not >10% above baseline, so P&L is "
                 "not flattered by parking capital",
                 "G4 the margin must exceed the change in terminal-bound "
                 "WIDTH, else NOT_RESOLVED",
                 "G5 drawdown and peak committed capital not worse by >10%",
                 "G6 ledger reconciles in every scenario",
                 "G7 d(realized)/d(queue_share) has the SAME SIGN on "
                 "TRAIN and VALIDATION"]),
            "v1_is_not_reapplied_to_cycle_1": (
                "cycle 1's verdicts stand as V1 produced them. Rescoring "
                "a finished cycle under a gate written after seeing its "
                "results is the tuning this loop exists to prevent."),
        },
        "sign_stability": stability,
        "candidates": [{k: v for k, v in c.items()} for c in CANDIDATES],
        "results": results,
        "verdicts": verdicts,
        "accepted": accepted,
        "outcome": ("ACCEPTED: %s" % ", ".join(accepted) if accepted
                    else "NO CANDIDATE QUALIFIED. The active policy stays "
                         "frozen and unchanged."),
    }
    os.makedirs(a.out, exist_ok=True)
    json.dump(report, open(os.path.join(a.out, "report.json"), "w"), indent=1)

    print(json.dumps({k: report[k] for k in
                      ("variants_tried", "partitions", "elapsed_s",
                       "accepted", "outcome")}, indent=1))
    key = ("realized_pnl_usd" if a.gate == "v2"
           else "terminal_lower_vs_start")
    for part in ("TRAIN", "VALIDATION"):
        print("\n== %s == (ranking on %s)" % (part, key))
        for cid in [c["id"] for c in CANDIDATES]:
            r = results[part][cid]
            print("  %-20s %s | bound [%+.0f,%+.0f] | turnover %.0f | "
                  "unres %.0f"
                  % (cid, ["%+.0f" % x[key] for x in r],
                     r[1]["terminal_lower_vs_start"],
                     r[1]["terminal_upper_usd"] - a.cash,
                     r[1]["turnover_usd"], r[1]["unresolved_cost_usd"]))
            if cid != "BASELINE":
                v = verdicts[part][cid]
                print("      %s: %s" % ("ACCEPT" if v["accepted"]
                                        else "REJECT", v["reasons"][0][:96]))
    if a.gate == "v2":
        print("\n== G7 SIGN STABILITY across partitions ==")
        for cid, s in stability.items():
            print("  %-20s %-8s %s" % (cid,
                                       "STABLE" if s["stable"] else "UNSTABLE",
                                       s["detail"]))
    print("\nwritten:", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
