#!/usr/bin/env python3
"""One command, one auditable run of the whole shadow engine.

    python3 scripts/bettor_shadow_demo.py

Deterministic: no clock, no randomness, no network, no credentials. Every
scenario is DECLARED SYNTHETIC and none of it is evidence about
profitability -- it demonstrates that the software does what it says.

Each scenario prints its own expected cash and expected inventory and
ASSERTS them independently of the ledger's own reconciliation, because a
cash total that reconciles to itself proves only that the arithmetic is
self-consistent. It was self-consistent while fees were being dropped.
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "backend")

from sportsassets import bettor_decision_engine as de       # noqa: E402
from sportsassets import bettor_maker_economics as me       # noqa: E402
from sportsassets import bettor_shadow_loop as sl           # noqa: E402
from sportsassets import bettor_venue_contract as vc        # noqa: E402

FEES = de.Fees(taker_per_contract=0.02, maker_per_contract=0.01,
               verified=True, source="DEMO_SCHEDULE_HYPOTHETICAL_VALUES")
ASSUME = ("DECLARED: p_both_legs_fill assumed, to exercise the execution "
          "path the engine correctly refuses to select")
FAILS = []


def check(label, got, want, tol=1e-9):
    ok = abs(got - want) <= tol
    print("      %-38s %12.4f  expected %12.4f  %s"
          % (label, got, want, "OK" if ok else "*** MISMATCH ***"))
    if not ok:
        FAILS.append(label)


def capable():
    vc.CAPABILITIES[("demo", "institutional")] = vc.VenueCapabilities(
        venue="demo", account_class="institutional",
        holds_both_legs_independently=vc.SUPPORTED,
        complement_quote_source=vc.OBSERVED, native_merge=vc.UNSUPPORTED,
        maker_orders=vc.SUPPORTED, cancel_replace=vc.SUPPORTED,
        verified_fee_schedule=True)


def bk(mid="m", **kw):
    d = dict(yes_bid=.45, yes_ask=.47, no_bid=.48, no_ask=.50,
             yes_bid_size=50, yes_ask_size=50, no_bid_size=50,
             no_ask_size=50, age_s=1.0, venue_state="OPEN",
             complement_source=vc.OBSERVED)
    d.update(kw)
    return de.Book(mid, **d)


def loop(cash=100.0, **kw):
    return sl.ShadowLoop(opening_cash=cash, fees=FEES, venue="demo", **kw)


def head(n, t):
    print("\n" + "=" * 72)
    print("%s. %s" % (n, t))
    print("=" * 72)


def main() -> int:
    capable()

    head(1, "NONZERO-FEE ENTRY  (the fee reproduction)")
    lp = loop()
    r = lp.step(bk("m1"), max_contracts=10, assume_unidentified_terms=ASSUME)
    print("   engine said %s / %s; executed under a declared assumption"
          % (r["assumed"]["engine_status"], r["assumed"]["engine_blocker"]))
    for l in r["execution"]["legs"]:
        print("      %-4s %-12s qty %5.4g @ %.4f  fee %.2f"
              % (l["leg"], l["outcome"], l["filled"], l["price"], l["fee"]))
    # 10 x 0.47 + 10 x 0.50 = 9.70 notional, fees 0.20 + 0.20 = 0.40
    check("cash", lp.ledger.cash, 89.90)
    check("fees_paid", lp.ledger.fees_paid, 0.40)
    check("inventory yes", lp.positions["m1"].yes, 10.0)
    check("inventory no", lp.positions["m1"].no, 10.0)
    check("basis", lp.positions["m1"].basis, 10.10)
    check("deployed", lp.deployed, 10.10)

    head(2, "PARTIAL SALE  (basis removed proportionally)")
    p = lp.positions["m1"]
    realized = p.sell("yes", 4, 0.60, FEES.fill_fee(4, maker=True))
    lp.ledger.move("SELL_YES", 4 * 0.60 - 0.04, "partial sale", "m1")
    lp.ledger.fees_paid += 0.04
    lp.ledger.realized_pnl += realized
    # yes basis was 10 x 0.47 + 0.20 = 4.90; 4/10 of it = 1.96
    print("      sold 4 of 10 YES at 0.60; 4/10 of the 4.90 yes basis out")
    check("yes remaining", p.yes, 6.0)
    check("yes basis remaining", p.yes_basis, 2.94)
    check("realized on the sale", realized, 4 * 0.60 - 0.04 - 1.96)
    check("deployed (never negative)", lp.deployed, 2.94 + 5.20)

    head(3, "COMPLETE SALE  (a closed position has zero basis)")
    r2 = p.sell("yes", 6, 0.60, FEES.fill_fee(6, maker=True))
    r3 = p.sell("no", 10, 0.55, FEES.fill_fee(10, maker=True))
    lp.ledger.move("SELL_YES", 6 * 0.60 - 0.06, "close yes", "m1")
    lp.ledger.move("SELL_NO", 10 * 0.55 - 0.10, "close no", "m1")
    lp.ledger.realized_pnl += r2 + r3
    check("yes contracts", p.yes, 0.0)
    check("no contracts", p.no, 0.0)
    check("yes basis", p.yes_basis, 0.0)
    check("no basis", p.no_basis, 0.0)
    check("deployed", lp.deployed, 0.0)
    print("      realized P&L is booked separately and NEVER reduces basis:")
    check("position realized_pnl", p.realized_pnl, realized + r2 + r3)

    head(4, "ONE-LEG FILL -> AUTONOMOUS RECOVERY")
    # The second leg is rejected because the price MOVED, so recovery
    # re-reads the market. The re-read book is what decides the branch.
    for title, mid_, reread, expect in (
            ("complement still executable -> COMPLETE", "m2",
             None, "COMPLETE"),
            ("complement gone, bid present -> EXIT", "m3",
             bk("m3", no_ask=None, no_ask_size=0, yes_bid=.46,
                yes_bid_size=50), "EXIT"),
            ("complement gone, no bid -> HOLD_EXPOSED", "m4",
             bk("m4", no_ask=None, no_ask_size=0, yes_bid=None,
                yes_bid_size=0), "HOLD_EXPOSED")):
        lp2 = loop()
        rr = lp2.step(bk(mid_), max_contracts=10,
                      assume_unidentified_terms=ASSUME,
                      reject_legs=("no",), recovery_book=reread)
        ex = rr["execution"] or {}
        rec = (ex.get("recovery") or {}) if ex else {}
        got = rec.get("action", "NONE")
        ok = got == expect
        if not ok:
            FAILS.append(title)
        print("   %-42s -> %-14s %s (%s)"
              % (title, got, "OK" if ok else "***",
                 ex.get("recovery_book")))
        if rec.get("why"):
            print("        %s" % rec["why"][:66])
        pos = lp2.positions[mid_]
        print("        inventory yes=%.4g no=%.4g directional=%.4g"
              % (pos.yes, pos.no, pos.directional))

    head(5, "QUOTE LIFECYCLE: rest -> touch -> reprice -> fill -> cancel")
    lp3 = loop()
    q = lp3.quote(bk("m5"), side="yes", price=0.45, size=10)
    print("   %s %s at %.4f, quoted exposure %.4f"
          % (q["quote_id"], q["state"], q["price"], lp3.quoted_exposure))
    t = lp3.touch(q["quote_id"])
    print("   TOUCH -> %s (queue %s): %s"
          % (t["outcome"], t["queue_ahead"], t["why"][:46]))
    rp = lp3.reprice_quote(q["quote_id"], new_price=0.455)
    print("   REPRICE %s -> %s at %.4f: %s"
          % (rp["quote_id"], rp["new_quote_id"], rp["to"], rp["why"][:40]))
    f = lp3.fill_quote(rp["new_quote_id"], qty=6, reason="demonstrate fill")
    print("   FILL %s state=%s qty=%.4g fee=%.2f  evidence=%s"
          % (rp["new_quote_id"], f["state"], f["filled"], f["fee"],
             f["evidence_class"]))
    check("cash after maker fill", lp3.ledger.cash, 100.0 - (6 * 0.455 + 0.06))
    check("inventory yes", lp3.positions["m5"].yes, 6.0)
    c = lp3.cancel_quote(rp["new_quote_id"])
    print("   CANCEL %s, released %.4f" % (c["quote_id"], c["released_exposure"]))
    check("quoted exposure after cancel", lp3.quoted_exposure, 0.0)

    head(6, "SETTLEMENT")
    s1 = lp3.settle("m5", yes_wins=True)
    print("      payout %.4f - basis %.4f = realised %+.4f"
          % (s1["payout"], s1["cost_basis"], s1["realised"]))
    check("payout", s1["payout"], 6.0)
    check("basis after settle", lp3.positions["m5"].basis, 0.0)

    head(7, "RESTART: balances, inventory, quotes AND provenance")
    lp4 = loop()
    lp4.step(bk("m6"), max_contracts=10, assume_unidentified_terms=ASSUME)
    lp4.quote(bk("m7"), side="yes", price=0.44, size=8)
    blob = lp4.snapshot()
    back = sl.ShadowLoop.restore(blob, fees=FEES, venue="demo")
    check("cash", back.ledger.cash, lp4.ledger.cash)
    check("inventory yes", back.positions["m6"].yes, lp4.positions["m6"].yes)
    check("basis", back.positions["m6"].basis, lp4.positions["m6"].basis)
    check("quoted exposure", back.quoted_exposure, lp4.quoted_exposure)
    resting = [q for q in back.quotes.values() if q["state"] == "RESTING"]
    print("      resting quotes recovered: %d" % len(resting))
    if len(resting) != 1:
        FAILS.append("restart quotes")

    head(8, "MAKER MATHEMATICS: the review's counterexample")
    q = me.MakerQuote(side="BUY", entry_price=0.485, bid=0.485, ask=0.515,
                      exit_route=me.EXIT_AGGRESSIVE,
                      conditional_reference_move=me.hypothetical(0.0),
                      exit_spread=me.hypothetical(0.030),
                      fee_per_contract=me.hypothetical(0.0),
                      carry_per_contract_per_hour=me.hypothetical(0.0),
                      duration_hours=me.hypothetical(0.0))
    rt = me.round_trip(q)
    print("      passive buy 0.485, sell at the unchanged bid 0.485")
    check("round trip per contract", rt.per_contract, 0.0)
    print("      (the withdrawn model reported -0.0150)")
    st = me.round_trip(me.MakerQuote(
        side="BUY", entry_price=0.485, bid=0.485, ask=0.515,
        exit_route=me.EXIT_SETTLEMENT,
        conditional_reference_move=me.hypothetical(0.0),
        fee_per_contract=me.hypothetical(0.0),
        carry_per_contract_per_hour=me.hypothetical(0.0),
        duration_hours=me.hypothetical(0.0)))
    print("      settlement route -> %s" % st.status)
    print("      missing: %s" % st.missing[0][:64])

    head(9, "LEDGER RECONCILIATION (necessary, NOT sufficient)")
    for name, lp_ in (("scenario 1-3", lp), ("lifecycle", lp3),
                      ("restart", lp4)):
        rc = lp_.ledger.reconciles()
        print("   %-14s %s  residual %s  fees %.2f  realized %+.4f"
              % (name, rc["reconciled"], rc["residual_must_be_0"],
                 lp_.ledger.fees_paid, lp_.ledger.realized_pnl))
        if not rc["reconciled"]:
            FAILS.append("reconcile " + name)

    print("\n" + "=" * 72)
    print("EVIDENCE CLASSES PRODUCED")
    print("=" * 72)
    print("   ", json.dumps(lp3.report()["by_evidence_class"]))
    print("    Every record above is SYNTHETIC_SCENARIO. No PROSPECTIVE_SHADOW")
    print("    record exists, so nothing here supports a profitability claim.")

    print("\n" + "=" * 72)
    if FAILS:
        print("FAILED: %d independent assertion(s): %s" % (len(FAILS), FAILS))
        return 1
    print("ALL INDEPENDENT ASSERTIONS PASSED")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
