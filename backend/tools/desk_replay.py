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
            "THE_TAPE_IS_NOT_OURS": (
                "every print replayed here is FERRARI's. We were never "
                "in the queue for any of them, so every fill the desk "
                "takes is an ASSUMPTION under queue_share, capped by "
                "the consumption ledger. This is the largest caveat on "
                "every number in this file."),
            "p_fill": DK.NOT_IDENTIFIED,
            "fees": "published PMUS schedule; maker is a REBATE",
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
