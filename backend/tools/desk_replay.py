"""MODE B: HISTORICAL REPLAY, through the same engine the live loop runs.

    python backend/tools/desk_replay.py \
        --extract  research/beta48/learning/extract_ferrari_<stamp>.json \
        --fairvalue research/beta48/learning/fairvalue_ferrari_<stamp>.json \
        --out      research/beta48/learning/replay_<stamp>/

WHAT MAKES THIS A REPLAY AND NOT A BACKTEST DRESSED UP.

    ONE IMPLEMENTATION. `bettor_desk.Desk` is imported, not
    reimplemented. If the replay disagrees with the live desk, it is
    because the evidence differed, not because a second copy of the
    policy drifted.

    THE TAPE IS REAL. Every event is an actual recorded Ferrari fill --
    venue, condition, leg, price, size and both clocks. Nothing is
    generated.

    THE TAPE IS NOT OURS. These are Ferrari's prints. We were never in
    the queue for any of them, so the fills the desk takes are
    ASSUMPTIONS under a declared `queue_share`, and the consumption
    ledger caps what any one print can give us. This is the single
    largest caveat on every number below and it is repeated in the
    output rather than left here.

    EVERY OUTCOME IS COUNTED. Losses, partial fills, expiries,
    never-paired legs and unsettled positions all appear. A replay that
    reports only completed winners is the failure this directive names.

THE SETTLEMENT SOURCE. Payouts come from the fair-value extract, which
carries each (condition, leg) with its realised settlement. A condition
with no settlement in our records stays OPEN and is reported as
unresolved exposure -- never quietly dropped and never assumed to have
paid zero.
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

from sportsassets import bettor_desk as DK                  # noqa: E402
from sportsassets import bettor_fee_schedule as FEES        # noqa: E402
from sportsassets.learn import kernel as K                  # noqa: E402

from learn_train_rn1 import decode as decode_fills          # noqa: E402
from learn_fairvalue_ferrari import decode as decode_fv     # noqa: E402

MODE = "HISTORICAL_REPLAY"


def fee_fn(qty, price, maker):
    """The published PMUS schedule. Maker is a REBATE (negative theta),
    so a resting fill EARNS -- which is why the sign is taken from the
    schedule rather than assumed to be a cost."""
    return float(FEES.LATEST.fill_fee(Decimal(str(round(qty, 6))),
                                      Decimal(str(round(price, 6))),
                                      maker=bool(maker)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", required=True)
    ap.add_argument("--fairvalue", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--curve", default="", help="frozen isotonic artifact")
    ap.add_argument("--queue-share", type=float, default=0.25)
    ap.add_argument("--cash", type=float, default=100000.0)
    ap.add_argument("--order-usd", type=float, default=250.0)
    ap.add_argument("--max-events", type=int, default=0)
    a = ap.parse_args()

    ex = json.load(open(a.extract))
    fills = decode_fills(ex)

    fv = json.load(open(a.fairvalue))
    payouts = {}
    for r in decode_fv(fv):
        payouts[(r["condition_id"], r["leg"])] = r["payout"]

    curve, curve_sha = None, DK.NOT_IDENTIFIED
    if a.curve and os.path.exists(a.curve):
        art = json.load(open(a.curve))
        curve = K.Isotonic.from_dict(art)
        curve_sha = art.get("sha") or DK._sha(art)

    pol = DK.Policy(curve=curve, curve_sha=curve_sha,
                    order_usd=a.order_usd)
    lim = DK.Limits(starting_cash=a.cash)
    desk = DK.Desk(policy=pol, limits=lim, fee_fn=fee_fn,
                   queue_share=a.queue_share, desk_id="replay")

    tape = sorted(
        ({"kind": "PRINT", "at": f["ts"], "condition_id": f["condition_id"],
          "outcome_index": f["outcome_index"], "price": f["price"],
          "size": f["size"], "evidence_id": "%s:%s:%s" % (
              f["condition_id"], f["outcome_index"], f["ts"])}
         for f in fills if f["side"] == "BUY"),
        key=lambda e: (e["at"], e["condition_id"], e["outcome_index"]))
    if a.max_events:
        tape = tape[:a.max_events]

    t0 = time.time()
    for e in tape:
        desk.step(e)
    last_at = tape[-1]["at"] if tape else 0.0

    # SETTLE WHAT WE CAN, AND SAY WHAT WE CANNOT. A leg with no
    # settlement in our records stays OPEN. Assuming it paid zero would
    # manufacture losses; assuming it paid one would manufacture gains.
    settled, unresolved = 0, []
    for (cond, oi), leg in list(desk.pf.open_legs()):
        p = payouts.get((cond, oi))
        if p is None:
            unresolved.append({"condition_id": cond, "outcome_index": oi,
                               "qty": round(leg["qty"], 4),
                               "cost": round(leg["cost"], 2)})
            continue
        desk.settle(cond, oi, float(p), last_at + 1.0)
        settled += 1

    snap = desk.snapshot()
    acts = {}
    for d in desk.decisions:
        acts[d["action"]] = acts.get(d["action"], 0) + 1

    filled = [o for o in desk.orders.values() if o.state == DK.FILLED]
    partial = [o for o in desk.orders.values()
               if o.state == DK.PARTIALLY_FILLED or
               (o.state == DK.EXPIRED and o.filled_qty > 0)]
    expired0 = [o for o in desk.orders.values()
                if o.state == DK.EXPIRED and o.filled_qty <= 0]

    unresolved_cost = round(sum(u["cost"] for u in unresolved), 2)

    # ── THE LEARNED COMPONENT'S PROVENANCE, MEASURED ────────────────
    #
    # "Learned" is not a quality claim, and a curve fitted on markets
    # that trade INSIDE the window it is then evaluated over is not
    # out-of-sample. So the overlap is computed here rather than
    # asserted, and the verdict follows the number.
    fv_rows = decode_fv(fv)
    fv_rows.sort(key=lambda r: (r["first_ts"], r["condition_id"], r["leg"]))
    seen, order_c = set(), []
    for r in fv_rows:
        if r["condition_id"] not in seen:
            seen.add(r["condition_id"])
            order_c.append(r["condition_id"])
    train_set = set(order_c[:int(len(order_c) * 0.6)])
    trn = [r for r in fv_rows if r["condition_id"] in train_set]
    r0 = tape[0]["at"] if tape else 0.0
    r1 = last_at
    overlap = [r for r in trn if not (r["last_ts"] < r0 or r["first_ts"] > r1)]
    after = [r for r in trn if r["resolved_at"] > r0]
    contaminated = bool(after)
    learned_prov = {
        "component": "residual_exit",
        "estimator": "isotonic (PAV) with a shared-n shrink",
        "feature": "the leg's volume-weighted purchase price",
        "target": "that leg's realised settlement payout in {0, 1}",
        "training_rows": len(trn),
        "training_markets": len(train_set),
        "training_first_fill": (time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(min(r["first_ts"] for r in trn)))
            if trn else None),
        "training_last_resolved": (time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(max(r["resolved_at"] for r in trn))) if trn else None),
        "calibration": "none separate -- the isotonic IS the calibration; "
                       "no held-out calibration split was used for it",
        "evaluation_population": (
            "the fair-value study evaluated it on the LATER 40% of "
            "resolved markets and the pre-registered test was NOT "
            "SUPPORTED: the paired log-loss difference against the "
            "identity was +0.0052 with a 95% interval of [-0.0017, "
            "+0.0120]. It is not established as a better forecast."),
        "training_rows_inside_replay_window": len(overlap),
        "training_rows_inside_replay_window_pct": (
            round(100.0 * len(overlap) / len(trn), 1) if trn else None),
        "training_markets_resolving_after_replay_start": len(after),
        "LOOKAHEAD": contaminated,
        "verdict": ("DEVELOPMENT_EVIDENCE" if contaminated else
                    "OUT_OF_SAMPLE_FOR_THIS_WINDOW"),
        "why": (
            "%d training rows (%s%%) come from markets that traded INSIDE "
            "this replay window, and %d training markets resolved AFTER "
            "the replay's first event. During those decisions the exit "
            "rule consulted a curve fitted using settlements that did not "
            "yet exist at the decision instant. THIS REPLAY'S EXIT "
            "BEHAVIOUR IS DEVELOPMENT EVIDENCE, not a validated result, "
            "and calling the curve 'learned' does not establish that its "
            "exits improve returns." % (
                len(overlap),
                round(100.0 * len(overlap) / len(trn), 1) if trn else 0,
                len(after))) if contaminated else
            "no training market resolved after the replay began",
    }
    report = {
        "mode": MODE,
        "generated_at": time.time(),
        "elapsed_s": round(time.time() - t0, 2),
        "desk_version": DK.VERSION,
        "window": {
            "first_event": tape[0]["at"] if tape else None,
            "last_event": last_at,
            "first_event_iso": (time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(tape[0]["at"]))
                if tape else None),
            "last_event_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                            time.gmtime(last_at)),
            "events": len(tape),
            "conditions": len({e["condition_id"] for e in tape}),
            "source": "recorded ferrariChampions2026 BUY fills, "
                      "%s" % ex.get("sample"),
        },
        "execution_assumptions": {
            "fill_model": DK.FILL_MODEL,
            "queue_share": a.queue_share,
            "event_class": DK.EVENT_CLASS,
            "event_class_note": DK.EVENT_CLASS_NOTE,
            "source_venue": DK.SOURCE_VENUE,
            "fee_schedule_venue": DK.FEE_SCHEDULE_VENUE,
            "venue_basis": DK.VENUE_TRANSFER,
            "venue_note": DK.VENUE_TRANSFER_NOTE,
            "counterfactual_allocation": (
                "our hypothetical order is allocated qty = min(order "
                "remaining, event_size x queue_share - already consumed "
                "from that event). PRICE: we are filled at the price of "
                "the observed execution, never at our own limit. QUEUE: "
                "orders are served oldest-first, which is an assumption "
                "about priority -- no venue told us our position. REUSE: "
                "a single source event CANNOT be spent twice; the "
                "consumption ledger is keyed on the event id so the same "
                "execution re-delivered by a second ingestion lane "
                "allocates nothing further."),
            "THE_TAPE_IS_NOT_OURS": (
                "every event replayed here is FERRARI's OWN EXECUTION, "
                "not a market print. We were never in the queue for any "
                "of them. Ferrari filling at a price is evidence that "
                "FERRARI's order filled, not that ours would have. Every "
                "fill the desk takes is an ASSUMPTION under queue_share, "
                "capped by the consumption ledger. This is the largest "
                "caveat on every number in this file."),
            "p_fill": DK.NOT_IDENTIFIED,
            "fees": "published PMUS schedule applied to GLOBAL-venue "
                    "data; maker is a REBATE",
        },
        "policy": snap["policy"],
        "limits": snap["limits"],
        "decision_census": acts,
        "orders": snap["orders"],
        "order_outcomes": {
            "fully_filled": len(filled),
            "partially_filled_or_expired_partial": len(partial),
            "expired_with_no_fill": len(expired0),
            "fill_rate_of_orders": (round(len(filled) / len(desk.orders), 4)
                                    if desk.orders else None),
        },
        "portfolio": snap["portfolio"],
        "invariant": snap["invariant"],
        "consumption": snap["consumption"],
        "settlement": {
            "legs_settled": settled,
            "legs_unresolved": len(unresolved),
            "unresolved_cost_usd": unresolved_cost,
            "unresolved": unresolved[:50],
            "rule": "a leg with no settlement in our records stays OPEN "
                    "and is reported as unresolved exposure. Assuming it "
                    "paid zero would manufacture losses; assuming one "
                    "would manufacture gains.",
        },
        "net": {
            "realized_pnl_usd": snap["portfolio"]["realized_pnl_usd"],
            "fees_usd": snap["portfolio"]["fees_usd"],
            "residual_cost_usd": snap["portfolio"]["inventory_cost_usd"],
            "cash_usd": snap["portfolio"]["cash"],
            "starting_cash_usd": snap["portfolio"]["starting_cash"],
            "NOT_A_LIVE_RESULT": (
                "HISTORICAL_REPLAY. This must never be combined with a "
                "live shadow total; they are different modes with "
                "different execution assumptions."),
        },
        # THE FULL POSITION. A realised figure standing alone while 396
        # legs are still open is not portfolio performance, and quoting
        # it as though it were is what this block exists to prevent.
        "economic_position": {
            "realized_pnl_usd": snap["portfolio"]["realized_pnl_usd"],
            "cash_usd": snap["portfolio"]["cash"],
            "unvalued_exposure_usd": unresolved_cost,
            "unvalued_legs": len(unresolved),
            "inventory_mark_usd": DK.NOT_IDENTIFIED,
            "executable_liquidation_usd": DK.NOT_IDENTIFIED,
            "total_portfolio_performance": DK.NOT_IDENTIFIED,
            "why_not_identified": (
                "%d legs carry %s of cost with NO settlement and NO "
                "contemporaneous book in our records for the replay's "
                "end instant. Without a mark there is no unrealised "
                "figure, and without depth there is no liquidation "
                "estimate. TOTAL PORTFOLIO PERFORMANCE IS THEREFORE "
                "NOT IDENTIFIED: the realised -%s is one component of "
                "it, not the whole of it." % (
                    len(unresolved), "$%0.2f" % unresolved_cost,
                    "$%0.2f" % abs(snap["portfolio"]["realized_pnl_usd"]))),
            "what_the_ledger_identity_proves": (
                "accounting consistency only -- that cash, cost basis "
                "and realised P&L reconcile to the starting capital. It "
                "does NOT validate any valuation and does NOT validate "
                "the fill assumptions that produced the positions."),
        },
        "learned_component_provenance": learned_prov,
    }

    os.makedirs(a.out, exist_ok=True)
    json.dump(report, open(os.path.join(a.out, "report.json"), "w"), indent=1)
    json.dump([o.to_dict() for o in desk.orders.values()],
              open(os.path.join(a.out, "orders.json"), "w"), indent=1)
    json.dump(desk.decisions[:5000],
              open(os.path.join(a.out, "decisions.json"), "w"), indent=1)

    print(json.dumps({k: report[k] for k in
                      ("mode", "window", "decision_census", "orders",
                       "order_outcomes", "settlement", "net", "invariant")},
                     indent=1, default=str))
    print("written:", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
