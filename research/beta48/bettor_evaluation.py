"""THE PRESPECIFIED EVALUATION. Written before the answer was known.

WHY THIS FILE EXISTS. Every economic number reported so far came from
running a policy over the WHOLE corpus and reading the total. With
four candidates, four queue fractions and several variants, that is
sixteen-plus looks at one sample -- and the best of sixteen looks at
one sample is not an estimate of anything. This file fixes the
protocol first and then runs it.

────────────────────────────────────────────────────────────────────
THE PROTOCOL, IN FULL, FIXED BEFORE ANY RESULT BELOW WAS COMPUTED.

1.  THE SPLIT IS CALENDAR TIME, NOT OUTCOME. The capture runs
    2026-09-13T16:55Z to 2026-09-20T15:52Z. The cut is the UTC day
    boundary nearest the midpoint of that span:

        DEV   [2026-09-13, 2026-09-17)      15,385 tape rows
        EVAL  [2026-09-17, 2026-09-21)      15,205 tape rows

    Chosen from ROW COUNTS and dates alone. No policy was run to pick
    it, and it does not move.

2.  AN EPISODE BELONGS TO THE SIDE ITS DECISION WAS MADE ON -- its
    `t0`, the instant the quote went up. An episode that begins in DEV
    and ends in EVAL is a DEV episode, because that is the information
    the decision had. Nothing is split down the middle and nothing is
    dropped.

3.  DEVELOPMENT HAPPENS ON DEV. Every variant tried is appended to
    REGISTER below -- including the ones that failed, which is the
    point of a register.

4.  EVAL IS TOUCHED ONCE, by ONE variant, chosen on DEV. Each touch is
    appended to `acceptance/eval_touches.json`, so a second touch is
    visible in the artifact rather than invisible in the history. The
    register makes selection VISIBLE; it does not make it free.

5.  DEPENDENCE IS HANDLED BY CLUSTERING ON THE EVENT. The corpus holds
    420 episodes over FIVE events. Episodes within an event share a
    book, a settlement and a direction, so they are not five hundred
    independent observations -- they are five. Every interval below is
    a bootstrap over EVENTS, resampled whole.

6.  THE ACCEPTANCE THRESHOLD, stated before the result: net P&L after
    fees positive on EVAL, with an event-clustered 90% interval that
    excludes zero. Anything short of that is reported as a failure to
    qualify, with the reason.

WHAT THE PROTOCOL CANNOT FIX, and it is the dominant fact: FIVE
CLUSTERS. A bootstrap over five events cannot produce a narrow
interval no matter what the point estimate does, so a positive result
here would be suggestive and could not be significant. That is a
property of the capture, not of the policy, and the honest response is
to say so rather than to report an unclustered interval that looks
tighter.

Run:  python research/beta48/bettor_evaluation.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bettor_episodes as epi                              # noqa: E402
import bettor_policy_final as pf                           # noqa: E402
import bettor_prints as prints_mod                         # noqa: E402

PROTOCOL_VERSION = "BETTOR_EVALUATION_V1"

CUT_ISO = "2026-09-17T00:00:00+00:00"
CUT = dt.datetime.fromisoformat(CUT_ISO).timestamp()

DEV, EVAL = "DEV", "EVAL"

OUT = os.path.join(HERE, "acceptance", "evaluation.json")
TOUCHES = os.path.join(HERE, "acceptance", "eval_touches.json")

# The queue fraction is a SCENARIO, not a knob to be chosen: our
# position inside a price level is not observable. Every result is
# reported across all four, and a variant "wins" only if it wins
# across them.
QFRACS = (0.00, 0.25, 0.50, 1.00)

BASE = dict(name="C3", min_spread_ticks=2, placement="AT_TOUCH",
            hard_flatten=True, cancel_other_on_fill=True,
            max_unmatched_mult=0.5, recovery="COMPLETE_PAIR")

# ── THE VARIANT REGISTER ─────────────────────────────────────────────
#
# Every variant attempted, in the order attempted, with the reason it
# was worth attempting. A variant that failed STAYS HERE. The count of
# this list is the multiplicity that any DEV result has to be read
# against, and it is printed with the results for exactly that reason.

REGISTER = [
    ("R0  C3 baseline", dict(BASE),
     "the previously declared candidate, carried forward unchanged so "
     "every later row has a like-for-like comparison"),

    ("R1  + decision-time sizing", dict(BASE, size_rule="EDGE_SCALED"),
     "allocation: a wider spread pays more for the same capital and "
     "queue risk. Measured previously as moving net BOTH ways"),

    ("R2  + capital release", dict(BASE, release_matched=True),
     "capital recycling without a merge call: sell both legs back and "
     "pay the spread"),

    ("R3  + both", dict(BASE, size_rule="EDGE_SCALED",
                        release_matched=True),
     "the two together, since neither is obviously dominant"),

    # ── BRANCH 1: THE FILL RATE. Attempted, and it FAILED. ───────────
    #
    # The hypothesis was that the binding constraint is the fill rate:
    # 358 of 420 episodes never fill, and the capital behind those
    # quotes is the dominant commitment. R4-R6 gate entry on whether
    # the book's own trailing flow can reach the quote at all.
    #
    # WHAT THE GATE ACTUALLY DID, measured on DEV: it worked as
    # designed and the design was wrong. Fill rate rose 39% -> 78% and
    # committed capital-hours fell eleven-fold. Net got WORSE per unit
    # of capital, and the reason is visible in the per-contract number:
    #
    #     R0 baseline   -0.0032 / contract on filled episodes
    #     R4 cover >= 1 -0.0045
    #     R5 cover >= 3 -0.0048
    #
    # Books that trade enough to reach a resting quote are books where
    # the price is moving, so raising the fill rate raises exposure to
    # the losing subset rather than improving it. KEPT IN THE REGISTER
    # AS A NEGATIVE RESULT. It is not retuned.
    ("R4  flow gate, cover >= 1", dict(BASE, min_flow_cover=1.0),
     "FAILED. Trailing flow over the horizon must cover the depth "
     "ahead plus our clip. Fill rate 39%->78%; per-contract loss got "
     "worse, not better"),

    ("R5  flow gate, cover >= 3", dict(BASE, min_flow_cover=3.0),
     "FAILED. The same gate with headroom. Leaves 4 DEV episodes -- "
     "below the activity floor, so it is not evaluable rather than "
     "good"),

    ("R6  flow gate + sizing", dict(BASE, min_flow_cover=3.0,
                                    size_rule="EDGE_SCALED"),
     "FAILED with R5, for R5's reason"),

    # ── BRANCH 2: THE FEE STRUCTURE. Where the money actually goes. ──
    #
    # DECOMPOSING THE BASELINE'S FILLED EPISODES ON DEV (qfrac 0.00,
    # 81 episodes, 4,459 contracts) ended the guessing:
    #
    #     position P&L    +  4.358     the pair trade itself EARNS
    #     maker rebates   +  6.330     the credit is real
    #     taker fees      - 24.980     <- the entire loss, and more
    #     ------------------------------------------------------------
    #     net             - 14.292
    #
    # Taker fees are 3.9x the rebates earned, and that ratio is not a
    # property of this sample. It is the published schedule:
    # Th_taker / |Th_maker| = 0.06 / 0.0125 = 4.8. A policy that
    # completes every maker fill with a taker order pays back roughly
    # five times what resting earned it, per contract, by construction.
    #
    # So the alternative is not another entry filter. It is to STOP
    # PAYING THE TAKER. R7-R10 leave the second side working so the
    # pair completes passively, and stop crossing out before expiry.
    ("R7  leave both sides working",
     dict(BASE, cancel_other_on_fill=False),
     "the pair completes PASSIVELY -- both legs earn the maker credit "
     "and neither pays the taker fee. Directly aimed at the measured "
     "dominant cost"),

    ("R8  no hard flatten", dict(BASE, hard_flatten=False),
     "hard flatten crosses out on the last open observation before "
     "expiry, which is another taker fee. Isolated so its cost is "
     "separable from R7's"),

    ("R9  both working + no flatten",
     dict(BASE, cancel_other_on_fill=False, hard_flatten=False),
     "the two taker sources removed together"),

    ("R10 R9 + maker-then-taker recovery",
     dict(BASE, cancel_other_on_fill=False, hard_flatten=False,
          recovery="MAKER_THEN_TAKER"),
     "the third taker source: COMPLETE_PAIR buys the complement as a "
     "taker unconditionally. MAKER_THEN_TAKER rests an exit first and "
     "crosses only after the recovery window expires"),

    ("R11 R9 + hold to settlement",
     dict(BASE, cancel_other_on_fill=False, hard_flatten=False,
          recovery="HOLD"),
     "the far end of the same mechanism: pay NO taker fee on recovery "
     "at all and carry the unmatched leg to settlement. The fourth "
     "case-study recovery option, not a threshold move"),
]

# ── BRANCH 3: ADVERSE SELECTION. DEVELOPED AFTER THE EVALUATION ──────
#
# THESE ROWS ARE DEVELOPMENT ONLY AND MUST NOT BE READ AS RESULTS. The
# evaluation split has been spent -- once, by R10, as the protocol
# requires -- so nothing below has been evaluated out of sample and
# nothing below may be reported as validated.
#
# WHY THEY EXIST. R10's evaluation failure was not ambiguous and it was
# not the fee: 197 episodes, net -74.29, of which TWO episodes opened
# during live college football play on 2026-09-19 accounted for -79.82.
# The other 195 netted +5.53. One held 100 YES contracts into a
# settlement of 0.00.
#
#   DEV  loss was taker fees          position +7.36, taker -11.31
#   EVAL loss was the position itself position -63.91, taker -21.81
#
# The two protections R10 removed to save taker fees -- cancelling the
# second side on a fill, and flattening before expiry -- are what had
# been preventing exactly that. Their taker cost is an INSURANCE
# PREMIUM, and R10's improvement on DEV was the premium refunded in a
# period that happened to contain no claim.
#
# WHAT WOULD ACTUALLY FIX IT is a rule that declines to quote into a
# large expected move, since that move is the loss. Time-to-resolution
# would be the natural input and IT IS NOT IN THE CORPUS: the tape
# carries no close or event-start time, only a state transition that
# arrives after the fact. Trailing realised volatility IS in the
# corpus, is strictly backward-looking, and prices the same thing.
REGISTER += [
    ("R12 vol gate, cover >= 1",
     dict(BASE, min_vol_cover=1.0),
     "DEV ONLY. The spread must at least cover the move expected over "
     "the quoting horizon, or being filled is worth less than not "
     "being filled"),

    ("R13 vol gate, cover >= 2",
     dict(BASE, min_vol_cover=2.0),
     "DEV ONLY. The same rule with headroom for the random-walk "
     "scaling, which understates a trending market"),

    ("R14 vol gate + taker avoidance",
     dict(BASE, min_vol_cover=1.0, cancel_other_on_fill=False,
          hard_flatten=False, recovery="MAKER_THEN_TAKER"),
     "DEV ONLY. R10's fee saving is only safe if the adverse move is "
     "refused at entry; this is the pair of them together"),
]

# NO SWEEP OF `recovery_wait_s` APPEARS ABOVE, DELIBERATELY. It is the
# knob that trades taker fees against carry risk, and the mechanism is
# already identified, so sweeping it would be adjusting a threshold
# until the development sample turned positive. The register stops at
# the four STRUCTURALLY different recovery paths the case studies
# actually contain.

# ── THE ACTIVITY FLOOR ───────────────────────────────────────────────
#
# A PROTOCOL AMENDMENT, MADE ON DEV, RECORDED RATHER THAN QUIETLY
# APPLIED. The selection rule as first written -- best worst-case net
# -- is DEGENERATE: a policy that quotes four times and loses a dollar
# beats one that quotes two hundred times and loses fourteen, so the
# rule's optimum is to trade nothing. R5 won it on four episodes.
#
# The amendment is structural, not a response to WHICH variant won: a
# rule whose maximum is inaction cannot rank strategies at all. A
# variant must clear the floor in EVERY queue scenario to be ranked;
# below it, the variant is NOT EVALUABLE, which is a different verdict
# from "worse" and is reported as one.
#
# THE EVALUATION SPLIT HAD NOT BEEN READ WHEN THIS WAS CHANGED, and
# `acceptance/eval_touches.json` is the record of that.
MIN_EPISODES = 30
MIN_EVENTS = 3


def _t(s):
    try:
        return dt.datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return None


def side_of(ep):
    """DEV or EVAL, by the instant the decision was made."""
    t0 = _t(ep.get("t0"))
    if t0 is None:
        return None
    return DEV if t0 < CUT else EVAL


def split(eps):
    out = {DEV: [], EVAL: [], None: []}
    for e in eps:
        out[side_of(e)].append(e)
    return out


def run(kw, qfrac):
    pol = epi.Policy(queue_ahead_fraction=qfrac, **kw)
    return epi.run_all(size=100.0, rebates_on=True, policy=pol,
                       use_tape=True)


def summarise(eps):
    s = pf.summarise(eps)
    never = sum(1 for e in eps if e.get("status") == "NEVER_FILLED")
    s["never_filled"] = never
    s["fill_rate"] = (round(1.0 - never / len(eps), 4) if eps else None)
    s["events"] = len(set(e.get("event") for e in eps))

    # THE DECOMPOSITION IS PART OF EVERY ROW, because the headline net
    # hides which term is carrying it. `taker_fees_paid` is stored
    # NEGATIVE, so position P&L is net minus BOTH of the other terms.
    reb = sum(e.get("rebates_received", 0.0) for e in eps)
    tak = sum(e.get("taker_fees_paid", 0.0) for e in eps)
    ctr = s["contracts"] or 0.0
    s["rebates_usd"] = round(reb, 4)
    s["taker_fees_usd"] = round(tak, 4)
    s["position_usd"] = round(s["net_usd"] - reb - tak, 4)
    s["taker_per_contract"] = round(tak / ctr, 6) if ctr else None
    s["rebate_per_contract"] = round(reb / ctr, 6) if ctr else None
    return s


# ── clustered inference ──────────────────────────────────────────────

def by_event(eps):
    d = {}
    for e in eps:
        d.setdefault(e.get("event"), []).append(e)
    return d


def cluster_bootstrap(eps, *, b=4000, seed=20260922):
    """Resample WHOLE EVENTS with replacement. Nothing else is valid.

    Episodes inside one event share a book, a settlement and a
    direction. Resampling episodes would treat 420 correlated rows as
    420 independent ones and would produce an interval several times
    too narrow -- which is the specific way a backtest lies about its
    own certainty.
    """
    groups = list(by_event(eps).values())
    k = len(groups)
    if k < 2:
        return {"n_clusters": k, "point": None, "lo": None, "hi": None,
                "why": "fewer than two events: no interval is computable"}
    point = sum(e.get("total_if_residual_realises", 0.0) for e in eps)
    sums = [sum(e.get("total_if_residual_realises", 0.0) for e in g)
            for g in groups]
    rng = random.Random(seed)
    draws = []
    for _ in range(b):
        # Resample k clusters with replacement and scale to the same
        # total number of clusters, so the statistic is comparable.
        draws.append(sum(sums[rng.randrange(k)] for _ in range(k)))
    draws.sort()
    return {"n_clusters": k, "point": round(point, 4),
            "lo": round(draws[int(0.05 * b)], 4),
            "hi": round(draws[int(0.95 * b) - 1], 4),
            "excludes_zero": draws[int(0.05 * b)] > 0
                             or draws[int(0.95 * b) - 1] < 0,
            "why": "90%% event-clustered bootstrap over %d events; with "
                   "so few clusters this interval is wide by "
                   "construction and a narrow one would be wrong" % k}


def record_touch(variant, result):
    """Append-only. A second touch of EVAL is VISIBLE, not prevented.

    Preventing it would only move the problem: a lock can be deleted
    and a protocol can be rewritten. What cannot be quietly undone is a
    record, in the artifact, of how many times the evaluation set was
    looked at and by which variant.
    """
    log = []
    if os.path.exists(TOUCHES):
        with open(TOUCHES) as fh:
            log = json.load(fh).get("touches", [])
    log.append({"protocol": PROTOCOL_VERSION, "variant": variant,
                "cut": CUT_ISO, "result": result})
    with open(TOUCHES, "w") as fh:
        json.dump({"note": "APPEND ONLY. Every look at the evaluation "
                           "split, in order. More than one entry for "
                           "more than one variant means the split was "
                           "reused and its result must be read as such.",
                   "touches": log}, fh, indent=2, default=str)
    return len(log)


def main(dev_only=False):
    if not prints_mod.tape_dir():
        print("NO TAPE -- refusing to report a tape-backed result "
              "without the tape.")
        return 1

    print("=" * 92)
    print("PRESPECIFIED EVALUATION -- %s" % PROTOCOL_VERSION)
    print("cut %s   |   DEV before, EVAL on or after, by episode t0"
          % CUT_ISO)
    print("SIMULATED replay. Not account performance, not executed "
          "fills.")
    print("=" * 92)

    # ── DEVELOPMENT. EVAL is not read in this loop. ──────────────────
    dev_rows = []
    print("\nDEVELOPMENT SPLIT ONLY -- %d variants in the register\n"
          % len(REGISTER))
    print("%-32s %5s %4s %4s %9s %9s %8s %9s" % (
        "variant", "qfrac", "eps", "fill", "net$", "position", "rebates",
        "takerfee"))
    print("-" * 92)
    for name, kw, why in REGISTER:
        for q in QFRACS:
            eps = run(kw, q)
            parts = split(eps)
            s = summarise(parts[DEV])
            dev_rows.append({"variant": name, "qfrac": q, "why": why, **s})
            print("%-32s %5.2f %4d %4s %9.2f %9.2f %8.2f %9.2f" % (
                name, q, s["episodes"],
                ("%.0f%%" % (100 * s["fill_rate"]))
                if s["fill_rate"] is not None else "-",
                s["net_usd"], s["position_usd"], s["rebates_usd"],
                s["taker_fees_usd"]))

    # ── SELECTION, on DEV, by the rule stated in the protocol. ───────
    #
    # Best WORST-CASE net across the four queue scenarios. Not the best
    # average and not the best single scenario: the queue fraction is
    # unobservable, so a policy that is only good at one value of it is
    # a policy whose result depends on an assumption nobody can check.
    worst, evaluable = {}, {}
    for r in dev_rows:
        v = r["variant"]
        if v not in worst or r["net_usd"] < worst[v]["net_usd"]:
            worst[v] = r
        ok = (r["episodes"] >= MIN_EPISODES and r["events"] >= MIN_EVENTS)
        evaluable[v] = evaluable.get(v, True) and ok

    ranked = sorted((r for v, r in worst.items() if evaluable[v]),
                    key=lambda r: -r["net_usd"])
    benched = sorted((r for v, r in worst.items() if not evaluable[v]),
                     key=lambda r: r["variant"])

    print("\n" + "=" * 92)
    print("DEV SELECTION -- best WORST-CASE net across the four queue "
          "scenarios,")
    print("among variants clearing the activity floor (>= %d episodes "
          "and >= %d events in EVERY scenario)" % (MIN_EPISODES, MIN_EVENTS))
    print("=" * 92)
    for r in ranked:
        print("  %-32s worst-case net %9.2f at qfrac %.2f  (fill %s)"
              % (r["variant"], r["net_usd"], r["qfrac"],
                 ("%.0f%%" % (100 * r["fill_rate"]))
                 if r["fill_rate"] is not None else "-"))
    for r in benched:
        print("  %-32s NOT EVALUABLE -- %d episodes / %d events at qfrac "
              "%.2f, below the floor"
              % (r["variant"], r["episodes"], r["events"], r["qfrac"]))
    if not ranked:
        print("\n  NO VARIANT CLEARS THE FLOOR. Nothing is selected and "
              "the evaluation split is not read.")
        return 2
    chosen = ranked[0]
    print("\n  SELECTED: %s" % chosen["variant"])
    print("  chosen from %d variants x %d scenarios = %d DEV looks. That "
          "multiplicity is why the DEV number is not the result."
          % (len(REGISTER), len(QFRACS), len(dev_rows)))

    kw = next(k for n, k, _w in REGISTER if n == chosen["variant"])

    if dev_only:
        print("\n--dev-only: the evaluation split was NOT read, and no "
              "touch was recorded.")
        return 0

    # THE SPLIT IS SPENT AFTER ONE TOUCH, and the guard is structural
    # rather than a note in a document. Re-running this file -- to add
    # a variant, to regenerate an artifact, by habit -- would otherwise
    # quietly turn a held-out set into a development set.
    prior = []
    if os.path.exists(TOUCHES):
        with open(TOUCHES) as fh:
            prior = json.load(fh).get("touches", [])
    if prior:
        print("\nREFUSING A SECOND TOUCH. The evaluation split has "
              "already been read %d time(s):" % len(prior))
        for t in prior:
            print("    %s" % t["variant"])
            for r in t["result"]:
                print("       qfrac %.2f  net %9.2f  per_cap_hr %s"
                      % (r["qfrac"], r["net_usd"], r["per_capital_hour"]))
        print("\nThat result stands. A new candidate needs NEW DATA, "
              "not another look at this split -- which is what the "
              "observation release exists to collect.")
        print("To deliberately override, delete %s, and understand that "
              "doing so ends the out-of-sample claim." % TOUCHES)
        return 3

    # ── THE SINGLE EVAL TOUCH ────────────────────────────────────────
    print("\n" + "=" * 92)
    print("EVALUATION SPLIT -- ONE variant, ONE touch")
    print("=" * 92)
    eval_rows = []
    for q in QFRACS:
        eps = run(kw, q)
        parts = split(eps)
        s = summarise(parts[EVAL])
        ci = cluster_bootstrap(parts[EVAL])
        eval_rows.append({"variant": chosen["variant"], "qfrac": q,
                          **s, "bootstrap": ci})
        print("  qfrac %.2f  eps %4d  fill %5s  net %9.2f  "
              "cap_hrs %8.1f  per_cap_hr %11s"
              % (q, s["episodes"],
                 ("%.0f%%" % (100 * s["fill_rate"]))
                 if s["fill_rate"] is not None else "-",
                 s["net_usd"], s["capital_hours"],
                 ("%.6f" % s["per_capital_hour"])
                 if s["per_capital_hour"] is not None else "-"))
        print("            %d events, 90%% clustered interval "
              "[%s, %s]%s"
              % (ci["n_clusters"],
                 ("%.2f" % ci["lo"]) if ci["lo"] is not None else "-",
                 ("%.2f" % ci["hi"]) if ci["hi"] is not None else "-",
                 "  EXCLUDES ZERO" if ci.get("excludes_zero") else ""))

    n_touch = record_touch(chosen["variant"],
                           [{k: r[k] for k in ("qfrac", "net_usd",
                                               "per_capital_hour")}
                            for r in eval_rows])

    # ── THE VERDICT, against the threshold fixed above ───────────────
    qualifies = all(r["net_usd"] > 0 and r["bootstrap"].get("excludes_zero")
                    for r in eval_rows)
    print("\n" + "=" * 92)
    print("VERDICT")
    print("=" * 92)
    if qualifies:
        print("  QUALIFIES on the stated threshold: net positive on EVAL "
              "in every queue scenario with a clustered interval that "
              "excludes zero.")
    else:
        neg = [r for r in eval_rows if r["net_usd"] <= 0]
        wide = [r for r in eval_rows
                if not r["bootstrap"].get("excludes_zero")]
        print("  DOES NOT QUALIFY.")
        if neg:
            print("    net P&L is not positive at qfrac %s"
                  % ", ".join("%.2f" % r["qfrac"] for r in neg))
        if wide:
            print("    the event-clustered interval contains zero at "
                  "qfrac %s -- with %d events it could hardly do "
                  "otherwise"
                  % (", ".join("%.2f" % r["qfrac"] for r in wide),
                     eval_rows[0]["bootstrap"]["n_clusters"]))
    print("\n  EVAL touches recorded to date: %d  (%s)" % (n_touch, TOUCHES))

    with open(OUT, "w") as fh:
        json.dump({"protocol": PROTOCOL_VERSION,
                   "cut": CUT_ISO,
                   "execution": "tape-backed replay; SIMULATED, not "
                                "account performance",
                   "register": [{"variant": n, "policy": k, "why": w}
                                for n, k, w in REGISTER],
                   "dev_looks": len(dev_rows),
                   "dev": dev_rows,
                   "selected": chosen["variant"],
                   "selection_rule": "best worst-case net across the four "
                                     "queue scenarios, on DEV only",
                   "eval": eval_rows,
                   "qualifies": qualifies}, fh, indent=2, default=str)
    print("\nwritten: %s" % OUT)
    return 0


if __name__ == "__main__":
    # `--dev-only` exists so the harness itself can be debugged without
    # spending the one evaluation touch on a run that was only ever
    # testing the plumbing.
    sys.exit(main(dev_only="--dev-only" in sys.argv))
